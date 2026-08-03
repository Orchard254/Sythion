//! Sidecar launch + startup handshake (Phase A contract), implemented in Rust.
//!
//! Replicates `scripts/launch_sidecar.py` for the packaged shell:
//!   1. Write a fresh per-launch token to `%LOCALAPPDATA%\JARVIS\token.json`.
//!   2. Spawn exactly one Python sidecar with `JARVIS_HANDSHAKE_TOKEN_FILE`.
//!   3. Poll `sidecar.json` for the bound port + token.
//!   4. Publish the resolved base URL + token to the frontend bridge.
//!
//! The token is never placed on a command line (visible in process listings).

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::Manager;

use crate::AppState;

pub const HANDSHAKE_TIMEOUT_SECS: u64 = 90;
const TOKEN_FILE_NAME: &str = "token.json";
const HANDSHAKE_FILE_NAME: &str = "sidecar.json";

#[derive(Clone, Debug, Serialize)]
pub struct BridgeConfig {
    pub base_url: String,
    pub control_token: String,
}

#[derive(Deserialize)]
struct HandshakePayload {
    port: u16,
    token: String,
}

fn local_app_data_dir() -> PathBuf {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir)
}

pub fn data_dir() -> PathBuf {
    std::env::var_os("JARVIS_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| local_app_data_dir().join("JARVIS"))
}

fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("crypto RNG unavailable");
    let mut token = String::with_capacity(64);
    for byte in bytes {
        use std::fmt::Write;
        let _ = write!(token, "{byte:02x}");
    }
    token
}

fn pick_free_port() -> u16 {
    TcpListener::bind(("127.0.0.1", 0))
        .expect("failed to probe a loopback port")
        .local_addr()
        .expect("probed loopback address")
        .port()
}

fn write_token_file(dir: &PathBuf, token: &str) -> PathBuf {
    let path = dir.join(TOKEN_FILE_NAME);
    let payload = serde_json::json!({ "token": token, "version": 1 });
    let content = serde_json::to_vec_pretty(&payload).expect("serialize token file");
    std::fs::write(&path, content).expect("write token file");
    path
}

fn resolve_python() -> (PathBuf, PathBuf) {
    if let Ok(exe) = std::env::var("JARVIS_PYTHON") {
        if !exe.is_empty() {
            return (PathBuf::from(exe), std::env::current_dir().unwrap_or_default());
        }
    }
    if let Ok(dir) = std::env::var("JARVIS_SIDECAR_DIR") {
        if !dir.is_empty() {
            return (PathBuf::from("python"), PathBuf::from(dir));
        }
    }
    let cwd = std::env::current_dir().unwrap_or_default();
    if cwd.join("backend").is_dir() {
        return (PathBuf::from("python"), cwd);
    }
    if cwd.file_name().and_then(|n| n.to_str()) == Some("src-tauri") {
        if let Some(parent) = cwd.parent() {
            if parent.join("backend").is_dir() {
                return (PathBuf::from("python"), parent.to_path_buf());
            }
        }
    }
    (PathBuf::from("python"), cwd)
}

fn read_handshake() -> Option<HandshakePayload> {
    let path = data_dir().join(HANDSHAKE_FILE_NAME);
    let content = std::fs::read_to_string(path).ok()?;
    let payload: HandshakePayload = serde_json::from_str(&content).ok()?;
    if payload.port == 0 || payload.token.is_empty() {
        return None;
    }
    Some(payload)
}

pub fn spawn_sidecar() -> Result<Child, String> {
    let dir = data_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("create data dir: {e}"))?;

    // Remove a stale handshake so a previous launch's file is never mistaken
    // for this launch (mirrors scripts/launch_sidecar.py).
    let _ = std::fs::remove_file(dir.join(HANDSHAKE_FILE_NAME));

    let token = generate_token();
    let token_file = write_token_file(&dir, &token);

    let port = pick_free_port();
    let (python, workdir) = resolve_python();

    let child = Command::new(&python)
        .current_dir(&workdir)
        .arg("-m")
        .arg("uvicorn")
        .arg("backend.main:app")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .env("JARVIS_HANDSHAKE_TOKEN_FILE", &token_file)
        .env("JARVIS_PORT", port.to_string())
        .env("JARVIS_DEVELOPMENT_MODE", "false")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("spawn sidecar ({python:?}): {e}"))?;

    Ok(child)
}

pub fn spawn_watcher(app_handle: tauri::AppHandle) {
    std::thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(HANDSHAKE_TIMEOUT_SECS);
        while Instant::now() < deadline {
            if let Some(payload) = read_handshake() {
                let config = BridgeConfig {
                    base_url: format!("http://127.0.0.1:{}", payload.port),
                    control_token: payload.token,
                };
                let state = app_handle.state::<AppState>();
                *state.bridge.lock().unwrap() = Some(config);
                return;
            }
            let child_exited = app_handle
                .state::<AppState>()
                .child
                .lock()
                .unwrap()
                .as_mut()
                .map(|c| c.try_wait().ok().flatten().is_some())
                .unwrap_or(false);
            if child_exited {
                eprintln!("[jarvis] sidecar exited before writing its handshake");
                return;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        eprintln!("[jarvis] timed out waiting for the sidecar handshake");
    });
}
