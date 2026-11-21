// Prevents an extra console window in release builds on Windows.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod device_channel;
mod native_attestation;
mod sidecar;

use std::path::PathBuf;
use std::process::Child;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::Manager;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

pub struct AppState {
    pub bridge: Mutex<Option<sidecar::BridgeConfig>>,
    pub child: Mutex<Option<Child>>,
    /// Launcher-generated handshake token for validating sidecar handshake.
    pub launch_token: Mutex<Option<String>>,
    /// Resolved profile directory, shared with device-channel config commands.
    pub profile_dir: Mutex<Option<PathBuf>>,
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
    /// True while a hit-test poller thread is running, so a second one is
    /// never started for the same overlay.
    poller_running: AtomicBool,
}

/// Append one line to a log file in the profile data dir.
///
/// A release build is a GUI-subsystem process: it has no console, so anything
/// written to stderr is discarded. Every diagnostic therefore goes to a file
/// next to the sidecar logs, where it survives the run and can be handed over
/// when a startup problem has to be investigated.
fn append_log(file_name: &str, line: &str) {
    append_log_in(&sidecar::data_dir(), file_name, line);
}

/// Append one line to `<dir>/logs/<file_name>`.
///
/// Split out from `append_log` so the write can be exercised without
/// touching the real profile directory.
fn append_log_in(dir: &std::path::Path, file_name: &str, line: &str) {
    use std::io::Write;
    const MAX_BYTES: u64 = 512 * 1024;
    let path = dir.join("logs").join(file_name);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // Bound the file rather than let it grow without limit.
    if let Ok(meta) = std::fs::metadata(&path) {
        if meta.len() > MAX_BYTES {
            return;
        }
    }
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        let _ = writeln!(f, "{line}");
    }
}

/// Shell diagnostics: the log file plus the console in a debug build.
fn shell_log(line: &str) {
    append_log("sythion-shell.log", line);
    // A debug build has a console; keep the output there too.
    #[cfg(debug_assertions)]
    eprintln!("{line}");
}

/// The path of the shell log, used in user-facing error messages.
fn shell_log_path() -> String {
    sidecar::data_dir()
        .join("logs")
        .join("sythion-shell.log")
        .display()
        .to_string()
}

/// Diagnostics for native verification. Appended to the profile data dir, never
/// to stdout (a release build is a GUI subsystem process with no console).
fn hit_test_log(line: &str) {
    append_log("companion-hit-test.log", line);
}

/// Report a startup failure to the user.
///
/// The packaged shell has no console, so a failure that is only written to
/// stderr is invisible: the application would simply not appear. A native
/// message box gives the user the reason and where the full log is.
#[cfg(windows)]
fn show_startup_error(message: &str) {
    use std::ffi::c_void;
    #[link(name = "user32")]
    extern "system" {
        fn MessageBoxW(
            hwnd: *mut c_void,
            text: *const u16,
            caption: *const u16,
            flags: u32,
        ) -> i32;
    }
    const MB_OK: u32 = 0x0000_0000;
    const MB_ICONERROR: u32 = 0x0000_0010;

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    let body = wide(message);
    let caption = wide("Sythion (Synth) could not start");
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            body.as_ptr(),
            caption.as_ptr(),
            MB_OK | MB_ICONERROR,
        );
    }
}

/// Report a startup failure to the user. Non-Windows builds log it only.
#[cfg(not(windows))]
fn show_startup_error(message: &str) {
    shell_log(&format!("[sythion] startup error: {message}"));
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
    // Claim the poller slot. The overlay can be closed and reopened, and the
    // poller has to be restartable without ending up with two of them racing
    // over the same window style.
    let claimed = app
        .state::<HitTestState>()
        .poller_running
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_ok();
    if !claimed {
        return;
    }
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
        app.state::<HitTestState>()
            .poller_running
            .store(false, Ordering::SeqCst);
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

#[tauri::command]
fn get_device_channel_config(state: tauri::State<AppState>) -> Result<device_channel::DeviceChannelConfig, String> {
    let dir = state
        .profile_dir
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "profile_dir_not_ready".to_string())?;
    device_channel::read_config(&dir)
}

#[tauri::command]
fn set_device_channel_config(
    state: tauri::State<AppState>,
    config: device_channel::DeviceChannelConfig,
) -> Result<device_channel::DeviceChannelConfig, String> {
    config.validate()?;
    let dir = state
        .profile_dir
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "profile_dir_not_ready".to_string())?;
    device_channel::write_config(&dir, &config)?;
    device_channel::read_config(&dir)
}

/// The global STOP shortcut (Ctrl+Shift+X). Kept exactly as before.
fn stop_shortcut() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyX)
}

/// The global Companion toggle (Ctrl+Shift+S). Verified distinct from STOP and
/// from every other shortcut this shell registers.
fn companion_shortcut() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyS)
}

fn global_shortcut_plugin() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, shortcut, event| {
            if event.state() != ShortcutState::Pressed {
                return;
            }
            // This global handler is invoked for EVERY registered shortcut, so
            // the shortcut must be discriminated here. Without this check the
            // Companion toggle would also fire the STOP POST.
            if *shortcut == companion_shortcut() {
                toggle_companion_from_hotkey(app);
                return;
            }
            if *shortcut == stop_shortcut() {
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
            }
        })
        .build()
}

fn register_shortcuts(app: &tauri::App) {
    for (shortcut, label) in [
        (stop_shortcut(), "stop"),
        (companion_shortcut(), "companion toggle"),
    ] {
        match app.handle().global_shortcut().register(shortcut) {
            Ok(()) => {}
            Err(e) => shell_log(&format!(
                "[sythion] could not register global {label} shortcut: {e}"
            )),
        }
    }
}

/* Main window placement.
 *
 * The Control Center used to be created at a fixed 1200x800 client area. On a
 * 1366x768 display that is taller than the screen, so the window hung below
 * the bottom edge of the work area with the first-run setup screen's
 * scrollbar out of reach. The client area is now clamped to the work area -
 * the desktop minus the taskbar - and the window is centred in it.
 */
const MAIN_WINDOW_PREFERRED_WIDTH: f64 = 1200.0;
const MAIN_WINDOW_PREFERRED_HEIGHT: f64 = 800.0;
const MAIN_WINDOW_MIN_WIDTH: f64 = 800.0;
const MAIN_WINDOW_MIN_HEIGHT: f64 = 600.0;

/// Fit a preferred client area into the available work area.
///
/// Pure arithmetic, kept apart from the Win32 queries so it can be tested
/// without a window. `frame` is the non-client part of the window (the title
/// bar and the sizing border) in the same unit as the other values.
fn fit_client_size(
    preferred: (f64, f64),
    work_area: (f64, f64),
    frame: (f64, f64),
) -> (f64, f64) {
    let available_w = (work_area.0 - frame.0).max(1.0);
    let available_h = (work_area.1 - frame.1).max(1.0);
    (preferred.0.min(available_w), preferred.1.min(available_h))
}

/// The work area of the primary display and the non-client frame of a standard
/// window, both in PHYSICAL pixels.
///
/// `SystemParametersInfo(SPI_GETWORKAREA)` reports the desktop minus the
/// taskbar and any app bars, which is the space a window can actually use.
/// `GetSystemMetrics` reports the frame, so the client area can be sized so
/// that the whole frame fits on the screen.
#[cfg(windows)]
fn screen_layout() -> Option<((i32, i32, u32, u32), (u32, u32))> {
    use std::ffi::c_void;
    #[link(name = "user32")]
    extern "system" {
        fn SystemParametersInfoW(
            action: u32,
            param: u32,
            param_ptr: *mut c_void,
            winini: u32,
        ) -> i32;
        fn GetSystemMetrics(index: i32) -> i32;
    }
    const SPI_GETWORKAREA: u32 = 0x0030;
    const SM_CXSIZEFRAME: i32 = 32;
    const SM_CYSIZEFRAME: i32 = 32;
    const SM_CYCAPTION: i32 = 4;

    #[repr(C)]
    struct Rect {
        left: i32,
        top: i32,
        right: i32,
        bottom: i32,
    }

    let mut rect = Rect {
        left: 0,
        top: 0,
        right: 0,
        bottom: 0,
    };
    let ok = unsafe {
        SystemParametersInfoW(SPI_GETWORKAREA, 0, &mut rect as *mut Rect as *mut c_void, 0)
    };
    if ok == 0 {
        return None;
    }
    let work_area = (
        rect.left,
        rect.top,
        (rect.right - rect.left).max(0) as u32,
        (rect.bottom - rect.top).max(0) as u32,
    );
    let frame_w = unsafe { GetSystemMetrics(SM_CXSIZEFRAME) }.max(0) as u32;
    let frame_h = (unsafe { GetSystemMetrics(SM_CYSIZEFRAME) }
        + unsafe { GetSystemMetrics(SM_CYCAPTION) })
    .max(0) as u32;
    Some((work_area, (frame_w, frame_h)))
}

fn create_main_window(app: &mut tauri::App) -> tauri::Result<()> {
    let app_handle = app.handle().clone();
    let scale = app_handle
        .primary_monitor()
        .ok()
        .flatten()
        .map(|m| m.scale_factor())
        .unwrap_or(1.0);

    let mut width = MAIN_WINDOW_PREFERRED_WIDTH;
    let mut height = MAIN_WINDOW_PREFERRED_HEIGHT;
    let mut position: Option<(f64, f64)> = None;

    #[cfg(windows)]
    if let Some(((wa_left, wa_top, wa_w, wa_h), (frame_w, frame_h))) = screen_layout() {
        let work_area = (wa_w as f64 / scale, wa_h as f64 / scale);
        let frame = (frame_w as f64 / scale, frame_h as f64 / scale);
        let (fitted_w, fitted_h) = fit_client_size((width, height), work_area, frame);
        width = fitted_w.max(MAIN_WINDOW_MIN_WIDTH.min(work_area.0));
        height = fitted_h.max(MAIN_WINDOW_MIN_HEIGHT.min(work_area.1));
        // Centre the whole frame horizontally, and start it at the top of the
        // work area so nothing of it hides behind the taskbar.
        let outer_w = width + frame.0;
        position = Some((
            wa_left as f64 / scale + (work_area.0 - outer_w).max(0.0) / 2.0,
            wa_top as f64 / scale,
        ));
    }

    let mut builder = tauri::WebviewWindowBuilder::new(
        app,
        "main",
        tauri::WebviewUrl::App("index.html".into()),
    )
    .title("Sythion (Synth)")
    .inner_size(width, height)
    .min_inner_size(MAIN_WINDOW_MIN_WIDTH, MAIN_WINDOW_MIN_HEIGHT);

    if let Some((x, y)) = position {
        builder = builder.position(x, y);
    }

    let window = builder.build()?;
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
 *
 * The overlay is created HIDDEN and revealed later.
 *
 * It is an always-on-top window with no taskbar button, so during the
 * first-run setup it floated above the setup form and hid the screen the user
 * still had to complete. Creating it hidden and showing it once the setup gate
 * has been cleared fixes that without changing the overlay's design.
 *
 * The window is still created during `setup()` rather than on demand, because
 * a webview window cannot be created from a command: the builder blocks until
 * the event loop answers, and the event loop is the thread that would have to
 * answer. Creating it up front and toggling its visibility is the only way to
 * add a window to a running Tauri app on this platform.
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
    .focused(false)
    // Created hidden: the overlay is revealed by `set_companion_visible` once
    // the first-run setup gate has been cleared.
    .visible(false);

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
    // The window stays hidden until the Control Center asks for it.
    Ok(())
}

/* Companion visibility persistence.
 * ---------------------------------------------------------------------------
 * The frontend's `localStorage['sythion.companionEnabled']` cannot be read from
 * Rust. The desktop command that reveals/hides the overlay already runs on
 * every change to that preference, so it mirrors the value into a tiny flag in
 * the profile directory. The shell reads that flag during setup and restores
 * visibility without depending on the main UI's own coordination effect. A
 * missing/unreadable flag means OFF, which keeps the first-run setup form
 * unobstructed.
 */
const COMPANION_PREF_FILE: &str = "companion_visible";

fn companion_pref_path(profile_dir: &std::path::Path) -> PathBuf {
    profile_dir.join(COMPANION_PREF_FILE)
}

fn read_companion_visible(profile_dir: &std::path::Path) -> bool {
    std::fs::read_to_string(companion_pref_path(profile_dir))
        .map(|s| s.trim() == "1")
        .unwrap_or(false)
}

fn write_companion_visible(app: &tauri::AppHandle, visible: bool) {
    let Some(dir) = app.state::<AppState>().profile_dir.lock().unwrap().clone() else {
        return;
    };
    let path = companion_pref_path(&dir);
    if let Err(e) = std::fs::write(&path, if visible { "1\n" } else { "0\n" }) {
        shell_log(&format!("[sythion] could not persist Companion visibility: {e}"));
    }
}

/// Toggle the Companion overlay from the global hotkey.
///
/// This is a session-level show/hide: it deliberately does NOT write the
/// persisted flag, so the Settings preference remains the single source of
/// truth for startup restore. The plugin's global handler may run on the
/// hotkey thread, so this only sends messages to the Tauri event loop.
fn toggle_companion_from_hotkey(app: &tauri::AppHandle) {
    let Some(window) = app.get_webview_window("companion") else {
        shell_log("[sythion] Companion hotkey ignored: overlay not available");
        return;
    };
    let next = !window.is_visible().unwrap_or(false);
    let result = if next { window.show() } else { window.hide() };
    match result {
        Ok(()) => shell_log(&format!("[sythion] Companion toggled to {next} by hotkey")),
        Err(e) => shell_log(&format!("[sythion] could not toggle Companion by hotkey: {e}")),
    }
}

/// Show or hide the Companion overlay.
///
/// The overlay is created hidden during startup so it cannot float above the
/// first-run setup form. This command is how the Control Center reveals it
/// once the setup gate has been cleared.
///
/// Showing and hiding an existing window is a message to the event loop, not a
/// window creation, so it is safe to call from a command.
#[tauri::command]
fn set_companion_visible(app: tauri::AppHandle, visible: bool) -> Result<(), String> {
    let window = app
        .get_webview_window("companion")
        .ok_or_else(|| "the Companion overlay is not available".to_string())?;
    if visible {
        window.show().map_err(|e| format!("could not show the Companion overlay: {e}"))?;
        shell_log("[sythion] Companion overlay shown");
    } else {
        window
            .hide()
            .map_err(|e| format!("could not hide the Companion overlay: {e}"))?;
        shell_log("[sythion] Companion overlay hidden");
    }
    // Mirror the preference into the profile dir so the shell can restore
    // this state at the next startup (Rust cannot read localStorage).
    write_companion_visible(&app, visible);
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

    // Build the Tauri context once so the shell can identify whether this is a
    // separately generated prototype build. A prototype uses an isolated default
    // profile, so it can never merge with or overwrite the production profile.
    let app_context = tauri::generate_context!();
    let product_name = app_context.config().product_name.clone();
    let identifier = Some(app_context.config().identifier.clone());

    // Resolve the profile directory ONCE, before anything is built.
    //
    // Tauri turns a failing setup hook into a panic, and a release build is a
    // GUI-subsystem process with no console: the panic would be invisible and
    // the application would simply never appear. A conflicting override
    // configuration is therefore rejected here, where the reason can still be
    // shown to the user.
    let profile_dir = match sidecar::resolve_data_dir_for_build(product_name.as_deref(), identifier.as_deref()) {
        Ok(dir) => dir,
        Err(e) => {
            // Persist the reason next to the profile the operator asked for.
            let diag_dir = std::env::var_os(sidecar::DATA_DIR_ENV)
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| sidecar::default_data_dir_for(product_name.as_deref(), identifier.as_deref()));
            append_log_in(
                &diag_dir,
                "sythion-shell.log",
                &format!("[sythion] the application failed to start: {e}"),
            );
            show_startup_error(&format!(
                "Sythion cannot use the requested profile directory.\n\n{e}\n\n\
                 Set {canonical} to the profile directory and leave {legacy} unset.",
                canonical = sidecar::DATA_DIR_ENV,
                legacy = sidecar::LEGACY_DATA_DIR_ENV,
            ));
            return;
        }
    };

    let state = AppState {
        bridge: Mutex::new(None),
        child: Mutex::new(None),
        launch_token: Mutex::new(None),
        profile_dir: Mutex::new(Some(profile_dir.clone())),
        #[cfg(windows)]
        job: Mutex::new(None),
    };

    // Ensure the private device-channel config exists before the sidecar starts.
    if let Err(e) = device_channel::ensure_config(&profile_dir) {
        eprintln!("device channel config unavailable: {e}");
        return;
    }

    // Native attestation key is generated and provisioned BEFORE the sidecar
    // starts. The sidecar reads it once from the user-only-ACL file and deletes
    // it; no key ever passes through the webview, control token, env or HTTP.
    let native_attestation = match native_attestation::NativeAttestation::new() {
        Ok(boundary) => boundary,
        Err(e) => {
            eprintln!("native attestation unavailable: {e}");
            return;
        }
    };
    if let Err(e) = native_attestation.provision(&profile_dir) {
        eprintln!("native attestation key provisioning failed: {e}");
        return;
    }

    let app = tauri::Builder::default()
        .plugin(global_shortcut_plugin())
        .manage(state)
        .manage(HitTestState::default())
        .manage(native_attestation)
        .invoke_handler(tauri::generate_handler![
            get_bridge_config,
            set_companion_hit_regions,
            set_companion_visible,
            get_device_channel_config,
            set_device_channel_config,
            native_attestation::verify_native_presence
        ])
        .setup(move |app| {
            let started: Result<(), String> = (|| {
                register_shortcuts(app);
                let app_handle = app.handle().clone();
                let child = sidecar::spawn_sidecar_for_build(
                    &app_handle,
                    &profile_dir,
                    product_name.as_deref(),
                    identifier.as_deref(),
                )
                .map_err(|e| {
                    format!("the local runtime could not be started: {e}")
                })?;
                *app.state::<AppState>().child.lock().unwrap() = Some(child);
                sidecar::spawn_watcher(app_handle, profile_dir.clone());
                create_main_window(app)
                    .map_err(|e| format!("the main window could not be created: {e}"))?;
                // The Companion overlay is created hidden. It is an always-on-top
                // window with no taskbar button, and during the first-run setup it
                // would float above the setup form and hide it. The Control Center
                // reveals it through `set_companion_visible` once the setup gate
                // has been cleared.
                create_companion_window(app)
                    .map_err(|e| format!("the Companion overlay could not be created: {e}"))?;
                // Restore the persisted Settings preference at the shell level
                // so the overlay is available without waiting for the main UI's
                // own coordination effect. A missing flag means OFF, which keeps
                // the first-run setup form unobstructed.
                if read_companion_visible(&profile_dir) {
                    if let Some(window) = app.get_webview_window("companion") {
                        if let Err(e) = window.show() {
                            shell_log(&format!(
                                "[sythion] could not restore Companion visibility: {e}"
                            ));
                        } else {
                            shell_log("[sythion] Companion visibility restored at startup");
                        }
                    }
                }
                // Must come AFTER create_companion_window: the poller exits as soon
                // as the overlay window is missing, so starting it earlier would make
                // it terminate immediately and click-through would never engage.
                spawn_hit_test_poller(app.handle().clone());
                Ok(())
            })();

            if let Err(e) = started {
                // A failing setup hook becomes a panic, which a GUI build cannot
                // show. Report the reason and stop deliberately instead. The
                // sidecar, if it was started, is reaped by the job object when
                // this process exits.
                shell_log(&format!("[sythion] the application failed to start: {e}"));
                show_startup_error(&format!(
                    "Sythion could not start.\n\n{e}\n\nA diagnostic log was written to:\n{}",
                    shell_log_path()
                ));
                std::process::exit(1);
            }
            Ok(())
        })
        .build(app_context);

    let app = match app {
        Ok(app) => app,
        Err(e) => {
            // A failed start used to panic silently: the packaged shell has no
            // console, so the application simply never appeared. Report it.
            shell_log(&format!("[sythion] the application failed to start: {e}"));
            show_startup_error(&format!(
                "Sythion could not start.\n\n{e}\n\nA diagnostic log was written to:\n{}",
                shell_log_path()
            ));
            return;
        }
    };

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

#[cfg(test)]
mod tests {
    use super::{append_log_in, fit_client_size, read_companion_visible};

    /// A 1366x768 display with a 48px taskbar leaves a 1366x720 work area.
    /// The preferred 1200x800 client area is taller than that, so it has to be
    /// reduced or the window hangs below the bottom edge of the screen with the
    /// setup screen's scrollbar out of reach.
    #[test]
    fn the_client_area_is_clamped_to_the_work_area() {
        let (w, h) = fit_client_size((1200.0, 800.0), (1366.0, 720.0), (16.0, 31.0));
        assert_eq!(w, 1200.0);
        assert_eq!(h, 689.0);
    }

    /// On a roomy display the preferred size is kept as it is.
    #[test]
    fn a_large_display_keeps_the_preferred_size() {
        let (w, h) = fit_client_size((1200.0, 800.0), (1920.0, 1040.0), (16.0, 31.0));
        assert_eq!(w, 1200.0);
        assert_eq!(h, 800.0);
    }

    /// A small display shrinks the client area in both directions.
    #[test]
    fn a_small_display_shrinks_both_axes() {
        let (w, h) = fit_client_size((1200.0, 800.0), (1024.0, 600.0), (16.0, 31.0));
        assert_eq!(w, 1008.0);
        assert_eq!(h, 569.0);
    }

    /// The result must never be zero or negative, whatever the inputs are.
    #[test]
    fn the_client_area_never_collapses() {
        let (w, h) = fit_client_size((1200.0, 800.0), (10.0, 10.0), (16.0, 31.0));
        assert!(w >= 1.0);
        assert!(h >= 1.0);
    }

    /// A release build has no console, so the shell's diagnostics have to
    /// reach a file or they are lost the moment the process starts.
    #[test]
    fn shell_diagnostics_are_written_to_a_file() {
        let dir = std::env::temp_dir().join(format!(
            "sythion-log-test-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        append_log_in(&dir, "sythion-shell.log", "[sythion] diagnostic line");
        let path = dir.join("logs").join("sythion-shell.log");
        let content = std::fs::read_to_string(&path).expect("diagnostics file");
        assert!(content.contains("[sythion] diagnostic line"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// An absent preference means OFF (first-run must not be obstructed), and
    /// a persisted "1"/"0" round-trips through the tiny flag file.
    #[test]
    fn companion_visibility_pref_defaults_off_and_roundtrips() {
        let dir = std::env::temp_dir().join(format!(
            "sythion-companion-pref-test-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("temp profile dir");
        assert!(!read_companion_visible(&dir));
        std::fs::write(dir.join("companion_visible"), "1\n").expect("write pref");
        assert!(read_companion_visible(&dir));
        std::fs::write(dir.join("companion_visible"), "0\n").expect("write pref");
        assert!(!read_companion_visible(&dir));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A second line is appended, not overwritten: the log has to keep the
    /// earlier entries.
    #[test]
    fn shell_diagnostics_are_appended() {
        let dir = std::env::temp_dir().join(format!(
            "sythion-log-append-test-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        append_log_in(&dir, "sythion-shell.log", "first");
        append_log_in(&dir, "sythion-shell.log", "second");
        let content = std::fs::read_to_string(dir.join("logs").join("sythion-shell.log"))
            .expect("diagnostics file");
        assert!(content.contains("first"));
        assert!(content.contains("second"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
