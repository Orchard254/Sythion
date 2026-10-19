// Prevents an extra console window in release builds on Windows.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use std::process::Child;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::Manager;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

pub struct AppState {
    pub bridge: Mutex<Option<sidecar::BridgeConfig>>,
    pub child: Mutex<Option<Child>>,
    /// Windows Job Object handle (as a raw pointer-sized integer) used for
    /// sidecar lifetime containment. When the main process dies abruptly the
    /// kernel closes this handle and `KILL_ON_JOB_CLOSE` terminates the
    /// sidecar. `None` when containment is unavailable.
    #[cfg(windows)]
    pub job: Mutex<Option<isize>>,
}

/* Companion click-through.
 * ---------------------------------------------------------------------------
 * `Window::set_ignore_cursor_events(true)` maps to `WS_EX_TRANSPARENT |
 * WS_EX_LAYERED` on Windows (tao window_state.rs). That is whole-window only,
 * which on its own would make the capsule's own controls unusable.
 *
 * So the flag is driven by WHERE THE CURSOR ACTUALLY IS, measured natively:
 *
 *   - The WebView publishes its interactive rectangles (robot + capsule, in CSS
 *     pixels relative to the viewport) via `set_companion_hit_regions`. This
 *     happens only when the layout actually changes, not per frame.
 *   - A polling thread reads the real cursor position with the Win32 cursor API
 *     through Tauri (`cursor_position`, absolute physical screen pixels) and the
 *     window origin (`inner_position`, physical pixels). It converts the cursor
 *     to window-relative CSS pixels and tests it against the published regions.
 *   - `set_ignore_cursor_events` is called only when the answer actually CHANGES,
 *     so the Win32 style churn (and the flicker it can cause) is bounded to real
 *     boundary crossings rather than firing every poll.
 *
 * Polling is mandatory rather than event-driven: while the window ignores cursor
 * events the WebView receives no pointer input at all, so it cannot be the thing
 * that notices the pointer coming back over an interactive region.
 *
 * `dragging` is a safety valve. During a native window drag the window moves with
 * the cursor, and `inner_position()` can lag a frame behind the real origin, so
 * the computed cursor offset can momentarily land outside every region. Ignoring
 * events mid-drag would drop the drag, so a reported drag forces interaction on.
 */
#[derive(Debug, Clone, Copy, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct RegionRect {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

#[derive(Default)]
struct HitTestState {
    regions: Mutex<Vec<RegionRect>>,
    /// CSS px -> physical px for this webview (devicePixelRatio).
    scale: Mutex<f64>,
    dragging: AtomicBool,
    /// Last value actually pushed to the Win32 window, to avoid redundant churn.
    applied_ignore: AtomicBool,
}

/// Diagnostics for native verification. Appended to the profile data dir, never
/// to stdout (a release build is a GUI subsystem process with no console).
fn hit_test_log(line: &str) {
    use std::io::Write;
    const MAX_BYTES: u64 = 512 * 1024;
    let path = sidecar::data_dir().join("logs").join("companion-hit-test.log");
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // Boundary crossings are rate-limited by real cursor movement, but a user
    // sweeping the pointer across the overlay can still produce a lot of lines.
    // Bound the file rather than let it grow without limit.
    if let Ok(meta) = std::fs::metadata(&path) {
        if meta.len() > MAX_BYTES {
            return;
        }
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{line}");
    }
}

#[tauri::command]
fn set_companion_hit_regions(
    state: tauri::State<'_, HitTestState>,
    regions: Vec<RegionRect>,
    scale: f64,
    dragging: bool,
) -> Result<(), String> {
    if !(0.1..=8.0).contains(&scale) {
        // A nonsensical scale would produce a wrong hit test and a window that
        // is either always click-through or never click-through.
        return Err(format!("refusing implausible devicePixelRatio: {scale}"));
    }
    *state.regions.lock().unwrap() = regions;
    *state.scale.lock().unwrap() = scale;
    state.dragging.store(dragging, Ordering::Relaxed);
    Ok(())
}

fn spawn_hit_test_poller(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        // ~25 Hz. GetCursorPos is a cheap call; the expensive path
        // (set_ignore_cursor_events -> Win32 style change) only runs on an
        // actual transition.
        const POLL: Duration = Duration::from_millis(40);
        loop {
            let Some(window) = app.get_webview_window("companion") else {
                break; // the overlay was closed
            };

            let dragging = app.state::<HitTestState>().dragging.load(Ordering::Relaxed);
            let regions = app.state::<HitTestState>().regions.lock().unwrap().clone();
            let scale = *app.state::<HitTestState>().scale.lock().unwrap();

            let mut local = (f64::NAN, f64::NAN);
            let ignore = if dragging || regions.is_empty() || scale <= 0.0 {
                // Never click-through while dragging, and never before the UI has
                // told us where its interactive parts are. An unknown layout must
                // fail safe and stay interactive.
                false
            } else {
                match (window.cursor_position(), window.inner_position()) {
                    (Ok(cursor), Ok(origin)) => {
                        let lx = (cursor.x - origin.x as f64) / scale;
                        let ly = (cursor.y - origin.y as f64) / scale;
                        local = (lx, ly);
                        !regions.iter().any(|r| {
                            lx >= r.x && lx <= r.x + r.w && ly >= r.y && ly <= r.y + r.h
                        })
                    }
                    _ => false,
                }
            };

            let state = app.state::<HitTestState>();
            if state.applied_ignore.load(Ordering::Relaxed) != ignore {
                match window.set_ignore_cursor_events(ignore) {
                    Ok(()) => {
                        state.applied_ignore.store(ignore, Ordering::Relaxed);
                        hit_test_log(&format!(
                            "ignore={ignore} regions={} scale={scale:.3} local=({:.1},{:.1}) dragging={dragging}",
                            regions.len(),
                            local.0,
                            local.1
                        ));
                    }
                    Err(e) => hit_test_log(&format!("set_ignore_cursor_events({ignore}) failed: {e}")),
                }
            }

            std::thread::sleep(POLL);
        }
        hit_test_log("poller stopped");
    });
}

#[tauri::command]
fn get_bridge_config(state: tauri::State<AppState>) -> Result<sidecar::BridgeConfig, String> {
    state
        .bridge
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "sidecar_not_ready".to_string())
}

fn global_shortcut_plugin() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, _shortcut, event| {
            if event.state() != ShortcutState::Pressed {
                return;
            }
            let handle = app.clone();
            std::thread::spawn(move || {
                let config = handle.state::<AppState>().bridge.lock().unwrap().clone();
                if let Some(cfg) = config {
                    let url = format!("{}/api/v1/stop", cfg.base_url);
                    let _ = ureq::post(&url)
                        .set("X-Control-Token", &cfg.control_token)
                        .timeout(std::time::Duration::from_secs(5))
                        .call();
                }
            });
        })
        .build()
}

fn register_stop_shortcut(app: &tauri::App) {
    let stop = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyX);
    match app.handle().global_shortcut().register(stop) {
        Ok(()) => {}
        Err(e) => eprintln!("[sythion] could not register global stop shortcut: {e}"),
    }
}

fn create_main_window(app: &mut tauri::App) -> tauri::Result<()> {
    let window = tauri::WebviewWindowBuilder::new(
        app,
        "main",
        tauri::WebviewUrl::App("index.html".into()),
    )
    .title("Sythion (Synth)")
    .inner_size(1200.0, 800.0)
    .min_inner_size(800.0, 600.0)
    .build()?;
    window.show()?;
    Ok(())
}

/* Companion overlay window.
 *
 * Deliberately built with no new Tauri commands, no new plugins and no new
 * Cargo dependencies. Everything it needs is already part of the core window
 * plugin:
 *
 *   - `transparent(true)` + a transparent page background gives a genuinely
 *     transparent window rather than an opaque rectangle.
 *   - `decorations(false)` removes the title bar.
 *   - `always_on_top(true)` + `skip_taskbar(true)` keep it floating without
 *     adding a taskbar button.
 *   - `shadow(false)` matters on Windows: the native drop shadow is drawn
 *     around the whole window rect and would otherwise show as a dark halo
 *     around the transparent regions.
 *   - `focused(false)` so opening the app does not pull focus away from
 *     whatever the user was doing.
 *
 * The window is positioned against the primary monitor in *logical* pixels so
 * it stays in the bottom-right corner under fractional DPI scaling.
 *
 * The `?surface=companion` marker is how the frontend knows to render only the
 * Companion. It is read from `location.search`, so identifying the surface
 * requires no capability and no IPC.
 */
fn create_companion_window(app: &mut tauri::App) -> tauri::Result<()> {
    const WIDTH: f64 = 360.0;
    // HEIGHT must fit the capsule's TALLEST state, not its shortest. With the
    // STT-unavailable notice plus a wrapped error line, the capsule's own bottom
    // edge sits at roughly 370px from the top of the window. At the previous
    // 300 the text field was clipped at the window edge and the microphone and
    // send buttons were pushed outside the window entirely, so after a failed
    // send the user could not reach the field to correct and retry the message.
    //
    // The extra transparent area costs nothing because click-through is now
    // per-region: empty space passes input to whatever is underneath.
    const HEIGHT: f64 = 380.0;
    const MARGIN: f64 = 24.0;

    // Default to the bottom-left of the virtual desktop, then correct against
    // the primary monitor below.
    let mut position: Option<(f64, f64)> = None;
    if let Some(monitor) = app.primary_monitor()? {
        let scale = monitor.scale_factor();
        let size = monitor.size().to_logical::<f64>(scale);
        let origin = monitor.position().to_logical::<f64>(scale);
        position = Some((
            origin.x + size.width - WIDTH - MARGIN,
            origin.y + size.height - HEIGHT - MARGIN,
        ));
    }

    let mut builder = tauri::WebviewWindowBuilder::new(
        app,
        "companion",
        tauri::WebviewUrl::App("index.html?surface=companion#/companion".into()),
    )
    .title("Sythion Companion")
    .inner_size(WIDTH, HEIGHT)
    .min_inner_size(300.0, 220.0)
    .resizable(false)
    .decorations(false)
    .transparent(true)
    .always_on_top(true)
    .skip_taskbar(true)
    .shadow(false)
    .focusable(true)
    .focused(false);

    if let Some((x, y)) = position {
        builder = builder.position(x, y);
    }

    let window = builder.build()?;
    // At high DPI, Tauri may create the window at the logical size in physical
    // pixels (no scaling). Detect this and resize to the correct physical size.
    let sf = window.scale_factor().unwrap_or(1.0);
    let sz = window.inner_size().unwrap_or(tauri::PhysicalSize::new(0, 0));
    let target_w = (WIDTH * sf) as u32;
    let target_h = (HEIGHT * sf) as u32;
    if sz.width != target_w || sz.height != target_h {
        let _ = window.set_size(tauri::PhysicalSize::new(target_w, target_h));
    }
    window.show()?;
    Ok(())
}

fn main() {
    // Set per-monitor DPI awareness BEFORE any window is created. Tauri/Tao
    // calls become_dpi_aware() in the event loop (after setup()), so windows
    // created in setup() see 96 DPI instead of the real monitor DPI. Setting
    // it here ensures the monitor's true scale factor is known during window
    // creation.
    #[cfg(windows)]
    {
        use std::ffi::c_void;
        #[link(name = "user32")]
        extern "system" {
            fn SetProcessDpiAwarenessContext(value: *mut c_void) -> i32;
        }
        #[link(name = "shcore")]
        extern "system" {
            fn SetProcessDpiAwareness(value: i32) -> i32;
        }
        const DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: *mut c_void =
            -4isize as *mut c_void;
        const DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE: *mut c_void =
            -3isize as *mut c_void;
        const PROCESS_PER_MONITOR_DPI_AWARE: i32 = 2;
        unsafe {
            let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        }
    }

    let state = AppState {
        bridge: Mutex::new(None),
        child: Mutex::new(None),
        #[cfg(windows)]
        job: Mutex::new(None),
    };

    let app = tauri::Builder::default()
        .plugin(global_shortcut_plugin())
        .manage(state)
        .manage(HitTestState::default())
        .invoke_handler(tauri::generate_handler![
            get_bridge_config,
            set_companion_hit_regions
        ])
        .setup(|app| {
            register_stop_shortcut(app);
            let app_handle = app.handle().clone();
            match sidecar::spawn_sidecar(&app_handle) {
                Ok(child) => {
                    *app.state::<AppState>().child.lock().unwrap() = Some(child);
                    sidecar::spawn_watcher(app_handle);
                }
                Err(e) => {
                    eprintln!("[sythion] failed to launch the sidecar: {e}");
                    return Err(e.into());
                }
            }
            create_main_window(app)?;
            create_companion_window(app)?;
            // Must come AFTER create_companion_window: the poller exits as soon
            // as the overlay window is missing, so starting it earlier would make
            // it terminate immediately and click-through would never engage.
            spawn_hit_test_poller(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Sythion (Synth) application");

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(mut child) = app_handle.state::<AppState>().child.lock().unwrap().take() {
                let _ = child.kill();
            }
            #[cfg(windows)]
            if let Some(job) = app_handle.state::<AppState>().job.lock().unwrap().take() {
                sidecar::close_job_handle(job);
            }
        }
    });
}
