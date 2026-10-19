//! Sidecar launch + startup handshake (Phase A contract), implemented in Rust.
//!
//! Replicates `scripts/launch_sidecar.py` for the packaged shell:
//!   1. Write a fresh per-launch token to `%LOCALAPPDATA%\Sythion (Synth)\token.json`.
//!   2. Spawn exactly one Python sidecar with `Sythion (Synth)_HANDSHAKE_TOKEN_FILE`.
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

// Windows process-lifetime containment.
//
// A Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` makes the kernel
// terminate every process assigned to it when the last handle to the job is
// closed. Because the main Sythion process holds that handle, an abrupt
// termination (crash, `taskkill`, session end) closes the handle and the
// sidecar dies with it instead of surviving as an orphan that keeps the
// profile lock forever. This is the only mechanism that works when the parent
// cannot run any cleanup code.
#[cfg(windows)]
mod win_job {
    use std::ffi::c_void;

    const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: u32 = 0x0000_2000;
    const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: i32 = 9;

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct JobObjectBasicLimitInformation {
        per_process_user_time_limit: i64,
        per_job_user_time_limit: i64,
        limit_flags: u32,
        minimum_working_set_size: usize,
        maximum_working_set_size: usize,
        active_process_limit: u32,
        affinity: usize,
        priority_class: u32,
        scheduling_class: u32,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct IoCounters {
        read_operation_count: u64,
        write_operation_count: u64,
        other_operation_count: u64,
        read_transfer_count: u64,
        write_transfer_count: u64,
        other_transfer_count: u64,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct JobObjectExtendedLimitInformation {
        basic_limit_information: JobObjectBasicLimitInformation,
        io_info: IoCounters,
        process_memory_limit: usize,
        job_memory_limit: usize,
        peak_process_memory_used: usize,
        peak_job_memory_used: usize,
    }

    type Handle = *mut c_void;

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(lp_job_attributes: *mut c_void, lp_name: *const u16) -> Handle;
        fn SetInformationJobObject(
            h_job: Handle,
            job_object_info_class: i32,
            lp_job_object_info: *const c_void,
            cb_job_object_info_length: u32,
        ) -> i32;
        fn AssignProcessToJobObject(h_job: Handle, h_process: Handle) -> i32;
        fn CloseHandle(h_object: Handle) -> i32;
    }

    /// Create a job object configured to kill its members when closed.
    /// Returns `None` on failure (containment is then unavailable).
    pub fn create() -> Option<Handle> {
        unsafe {
            let job = CreateJobObjectW(std::ptr::null_mut(), std::ptr::null());
            if job.is_null() {
                return None;
            }
            let mut info: JobObjectExtendedLimitInformation = std::mem::zeroed();
            info.basic_limit_information.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                &info as *const _ as *const c_void,
                std::mem::size_of::<JobObjectExtendedLimitInformation>() as u32,
            );
            if ok == 0 {
                CloseHandle(job);
                return None;
            }
            Some(job)
        }
    }

    /// Assign a process (by raw handle) to the job. Returns false on failure.
    pub fn assign(job: Handle, process: Handle) -> bool {
        unsafe { AssignProcessToJobObject(job, process) != 0 }
    }

    /// Close the job handle. Any surviving members are killed.
    pub fn close(job: Handle) {
        if !job.is_null() {
            unsafe {
                CloseHandle(job);
            }
        }
    }

    pub fn open_process(access: u32, inherit: i32, pid: u32) -> Handle {
        #[link(name = "kernel32")]
        extern "system" {
            fn OpenProcess(dw_desired_access: u32, b_inherit_handle: i32, dw_process_id: u32) -> Handle;
        }
        unsafe { OpenProcess(access, inherit, pid) }
    }
}

/// Close a job handle stored as a raw isize. Exposed for the exit handler.
#[cfg(windows)]
pub fn close_job_handle(handle: isize) {
    win_job::close(handle as *mut std::ffi::c_void);
}

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
    std::env::var_os("Sythion (Synth)_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| local_app_data_dir().join("Sythion (Synth)"))
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

fn resolve_python(app_handle: &tauri::AppHandle) -> Result<(PathBuf, PathBuf), String> {
    if let Ok(exe) = std::env::var("Sythion (Synth)_PYTHON") {
        if !exe.is_empty() {
            return Ok((PathBuf::from(exe), std::env::current_dir().unwrap_or_default()));
        }
    }
    if let Ok(dir) = std::env::var("Sythion (Synth)_SIDECAR_DIR") {
        if !dir.is_empty() {
            let dir = PathBuf::from(dir);
            if dir.join("backend").is_dir() {
                return Ok((PathBuf::from("python"), dir));
            }
            return Err(format!("configured sidecar directory missing backend: {}", dir.display()));
        }
    }
    // Packaged mode takes priority over development checkouts: a packaged install
    // also contains a backend/ directory next to the executable.
    if let Ok(resource_dir) = app_handle.path().resource_dir() {
        let python = resource_dir.join("python-runtime").join("python.exe");
        let backend = resource_dir.join("backend").join("main.py");
        if python.is_file() && backend.is_file() {
            return Ok((python, resource_dir));
        }
        return Err(format!(
            "packaged Python runtime missing: expected {} and {}",
            python.display(),
            backend.display()
        ));
    }
    let cwd = std::env::current_dir().unwrap_or_default();
    if cwd.join("backend").is_dir() {
        return Ok((PathBuf::from("python"), cwd));
    }
    if cwd.file_name().and_then(|n| n.to_str()) == Some("src-tauri") {
        if let Some(parent) = cwd.parent() {
            if parent.join("backend").is_dir() {
                return Ok((PathBuf::from("python"), parent.to_path_buf()));
            }
        }
    }
    Err("unable to resolve a Python runtime: no development checkout and no Tauri resource directory".to_string())
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

/// Translate the shell's build mode into the sidecar's development flag.
///
/// The two must agree, because they decide *which origins the sidecar trusts*
/// (`backend/core/auth.py` -> `control_origins`):
///
///   - A packaged build (the Tauri CLI enables `custom-protocol`) serves the
///     bundled frontend from `tauri://localhost`. Only the packaged origins are
///     trusted, and development mode stays off.
///   - A development build serves `devUrl` instead, i.e. the Vite dashboard on
///     `http://localhost:<port>`. That origin is trusted only in development
///     mode; otherwise every request from the UI is rejected by the control
///     boundary with 403 and no CORS allow-origin, which surfaces as a healthy
///     sidecar plus a "backend unreachable" UI.
///
/// `tauri::is_dev()` is exactly `!cfg!(feature = "custom-protocol")`, so it is
/// the same condition that decides which URL the webview loads. Deriving the
/// flag from it keeps the two from drifting apart again.
fn development_mode_flag_for(is_dev: bool) -> &'static str {
    if is_dev {
        "true"
    } else {
        "false"
    }
}

fn development_mode_flag() -> &'static str {
    development_mode_flag_for(tauri::is_dev())
}

pub fn spawn_sidecar(app_handle: &tauri::AppHandle) -> Result<Child, String> {
    let dir = data_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("create data dir: {e}"))?;

    // Remove a stale handshake so a previous launch's file is never mistaken
    // for this launch (mirrors scripts/launch_sidecar.py).
    let _ = std::fs::remove_file(dir.join(HANDSHAKE_FILE_NAME));

    let token = generate_token();
    let token_file = write_token_file(&dir, &token);

    let port = pick_free_port();
    let (python, workdir) = match resolve_python(app_handle) {
        Ok(pair) => pair,
        Err(e) => return Err(e),
    };

    // Create the containment job BEFORE spawning, so the child can be assigned
    // immediately. The handle is stored in AppState and lives for the whole app
    // lifetime; when the main process dies (even abruptly) the kernel closes
    // it and KILL_ON_JOB_CLOSE terminates the sidecar.
    #[cfg(windows)]
    let job_handle = win_job::create();

    let log_dir = dir.join("logs");
    let _ = std::fs::create_dir_all(&log_dir);
    let stdout_file = std::fs::File::create(log_dir.join("sidecar.out.log")).ok();
    let stderr_file = std::fs::File::create(log_dir.join("sidecar.err.log")).ok();
    let child = Command::new(&python)
        .current_dir(&workdir)
        .arg("-m")
        .arg("uvicorn")
        .arg("backend.main:app")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .env("SYTHION_HANDSHAKE_TOKEN_FILE", &token_file)
        .env("SYTHION_PORT", port.to_string())
        .env("SYTHION_DEVELOPMENT_MODE", development_mode_flag())
        .stdout(stdout_file.map(Stdio::from).unwrap_or_else(Stdio::null))
        .stderr(stderr_file.map(Stdio::from).unwrap_or_else(Stdio::null))
        .spawn()
        .map_err(|e| format!("spawn sidecar ({python:?}): {e}"))?;

    // Assign the child to the job. If assignment fails (e.g. the main process
    // is already in a job that forbids nested jobs) we degrade gracefully:
    // the sidecar still runs, just without abrupt-termination containment.
    #[cfg(windows)]
    if let Some(job) = job_handle {
        // Obtain a handle to the child via OpenProcess rather than
        // Child::raw_handle(), which is not stable on this toolchain.
        const PROCESS_SET_QUOTA: u32 = 0x0000_0100;
        const PROCESS_TERMINATE: u32 = 0x0000_0001;
        let process_handle = unsafe {
            win_job::open_process(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, child.id())
        };
        let assigned = if process_handle.is_null() {
            false
        } else {
            let ok = win_job::assign(job, process_handle);
            // AssignProcessToJobObject does not take ownership of the handle;
            // close ours to avoid leaking it.
            win_job::close(process_handle);
            ok
        };
        if assigned {
            *app_handle.state::<crate::AppState>().job.lock().unwrap() = Some(job as isize);
        } else {
            eprintln!("[sythion] could not assign sidecar to job object; orphan protection unavailable");
            win_job::close(job);
        }
    }

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
                eprintln!("[sythion] sidecar exited before writing its handshake");
                return;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        eprintln!("[sythion] timed out waiting for the sidecar handshake");
    });
}

#[cfg(test)]
mod tests {
    use super::{development_mode_flag, development_mode_flag_for};

    /// A packaged shell serves `tauri://localhost`, which is trusted without
    /// development mode, so the flag must stay "false". Widening it here would
    /// silently trust the Vite origins in a shipped build.
    #[test]
    fn packaged_builds_never_enable_development_mode() {
        assert_eq!(development_mode_flag_for(false), "false");
    }

    /// A development shell serves `devUrl` (the Vite dashboard). Without the
    /// flag the sidecar answers 403 with no CORS allow-origin and the UI reports
    /// "backend unreachable" despite a healthy sidecar.
    #[test]
    fn dev_builds_enable_development_mode() {
        assert_eq!(development_mode_flag_for(true), "true");
    }

    /// The flag handed to the sidecar must agree with the condition that decides
    /// which URL the webview loads, otherwise the two drift and the bridge breaks.
    #[test]
    fn the_flag_follows_the_url_the_webview_loads() {
        assert_eq!(development_mode_flag(), development_mode_flag_for(tauri::is_dev()));
    }
}
