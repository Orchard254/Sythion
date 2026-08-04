// Prevents an extra console window in release builds on Windows.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use std::process::Child;
use std::sync::Mutex;

use tauri::Manager;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

pub struct AppState {
    pub bridge: Mutex<Option<sidecar::BridgeConfig>>,
    pub child: Mutex<Option<Child>>,
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

fn main() {
    let state = AppState {
        bridge: Mutex::new(None),
        child: Mutex::new(None),
    };

    let app = tauri::Builder::default()
        .plugin(global_shortcut_plugin())
        .manage(state)
        .invoke_handler(tauri::generate_handler![get_bridge_config])
        .setup(|app| {
            register_stop_shortcut(app);
            let app_handle = app.handle().clone();
            match sidecar::spawn_sidecar() {
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
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Sythion (Synth) application");

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(mut child) = app_handle.state::<AppState>().child.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
