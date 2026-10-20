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
use std::path::{Path, PathBuf};
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

/* Windows console suppression.
 *
 * The packaged shell is a GUI-subsystem process (`windows_subsystem =
 * "windows"`), so it has no console of its own. The sidecar is a
 * console-subsystem program: the bundled `python.exe` is a CUI executable.
 *
 * When a GUI-subsystem parent starts a console-subsystem child without
 * saying otherwise, Windows allocates a NEW console for that child and shows
 * it. That is the empty terminal window that appeared in front of the
 * application on every packaged launch: it is created by this spawn, not by
 * anything external.
 *
 * CREATE_NO_WINDOW runs the child with a console that is never displayed. It
 * changes nothing else: the child's stdout and stderr are already redirected
 * to log files, the handshake still goes through the file contract, and the
 * job-object containment is unaffected.
 */
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
#[cfg(windows)]
const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;

/// The creation flags used for the sidecar process.
#[cfg(windows)]
fn sidecar_creation_flags() -> u32 {
    // The two flags are mutually exclusive by contract: CREATE_NEW_CONSOLE
    // would put a console window back on screen.
    debug_assert_eq!(CREATE_NEW_CONSOLE & CREATE_NO_WINDOW, 0);
    CREATE_NO_WINDOW
}

/// The creation flags used for the sidecar process.
#[cfg(not(windows))]
fn sidecar_creation_flags() -> u32 {
    0
}

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

/* Profile directory contract.
 *
 * The shell and the Python sidecar must agree on ONE profile directory: the
 * database, the profile lock, the handshake file, the token file and the logs
 * all live there. A split profile is not cosmetic - the sidecar would write
 * its handshake into one directory while the shell looked for it in another,
 * the bridge would never resolve, and the application would sit on
 * "Connecting to Sythion" until the handshake timeout expired.
 *
 * The rules:
 *
 *   1. `SYTHION_DATA_DIR` is the canonical explicit override.
 *   2. `Sythion (Synth)_DATA_DIR` is still honoured, but ONLY when the
 *      canonical variable is absent. It exists because the packaging and
 *      launch scripts set it.
 *   3. If BOTH are set and they name different directories, the shell refuses
 *      to start. Silently picking one would hide a misconfiguration that
 *      splits the profile.
 *   4. With neither set, the established default is used:
 *      `%LOCALAPPDATA%\Sythion (Synth)`.
 *
 * The directory is resolved ONCE, in the shell, and the resolved path is
 * passed to the sidecar explicitly through `SYTHION_DATA_DIR`. The sidecar
 * therefore never has to guess, and an inherited environment variable can
 * never point it somewhere else.
 */
pub const DATA_DIR_ENV: &str = "SYTHION_DATA_DIR";
pub const LEGACY_DATA_DIR_ENV: &str = "Sythion (Synth)_DATA_DIR";
pub const DEFAULT_DATA_DIR_NAME: &str = "Sythion (Synth)";

/// The default profile directory when no override is supplied.
///
/// On Windows this is `%LOCALAPPDATA%\Sythion (Synth)`, the location the
/// backend uses for the same default.
pub fn default_data_dir() -> PathBuf {
    match std::env::var_os("LOCALAPPDATA") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir).join(DEFAULT_DATA_DIR_NAME),
        _ => std::env::temp_dir().join("sythion-data"),
    }
}

/// Normalize a profile directory: absolute, with `.` and `..` resolved and no
/// trailing separator.
///
/// Purely lexical - the directory does not have to exist, and nothing is read
/// from or written to the filesystem.
pub fn normalize_data_dir(path: &Path) -> PathBuf {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_default().join(path)
    };

    let mut out = PathBuf::new();
    for component in absolute.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                // `pop` is a no-op on a root, so `C:\..\x` stays `C:\x`.
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// A comparable key for a profile directory.
///
/// Windows paths are case-insensitive, so the comparison folds case there.
pub fn data_dir_key(path: &Path) -> String {
    let text = normalize_data_dir(path).to_string_lossy().replace('/', "\\");
    if cfg!(windows) {
        text.to_lowercase()
    } else {
        text
    }
}

/// Resolve the profile directory from an explicit set of overrides.
///
/// `canonical` is `SYTHION_DATA_DIR`, `legacy` is `Sythion (Synth)_DATA_DIR`.
/// Empty values count as absent. Returns an error when both are present and
/// name different directories.
pub fn resolve_data_dir_from(
    canonical: Option<&std::ffi::OsStr>,
    legacy: Option<&std::ffi::OsStr>,
    default: &Path,
) -> Result<PathBuf, String> {
    let canonical = canonical
        .map(|v| v.to_string_lossy().trim().to_string())
        .filter(|v| !v.is_empty());
    let legacy = legacy
        .map(|v| v.to_string_lossy().trim().to_string())
        .filter(|v| !v.is_empty());

    match (canonical, legacy) {
        (Some(c), Some(l)) => {
            if data_dir_key(Path::new(&c)) == data_dir_key(Path::new(&l)) {
                Ok(normalize_data_dir(Path::new(&c)))
            } else {
                Err(format!(
                    "conflicting profile directories: {DATA_DIR_ENV} points at '{}' but {LEGACY_DATA_DIR_ENV} points at '{}'. \
                     Set only {DATA_DIR_ENV}, or make both point at the same directory.",
                    Path::new(&c).display(),
                    Path::new(&l).display(),
                ))
            }
        }
        (Some(c), None) => Ok(normalize_data_dir(Path::new(&c))),
        (None, Some(l)) => Ok(normalize_data_dir(Path::new(&l))),
        (None, None) => Ok(normalize_data_dir(default)),
    }
}

/// Resolve the profile directory from the environment.
///
/// This is the authority. A conflicting configuration is an error, and the
/// caller is expected to refuse to start rather than pick a side.
pub fn resolve_data_dir() -> Result<PathBuf, String> {
    resolve_data_dir_from(
        std::env::var_os(DATA_DIR_ENV).as_deref(),
        std::env::var_os(LEGACY_DATA_DIR_ENV).as_deref(),
        &default_data_dir(),
    )
}

/// The profile directory, for code that only needs somewhere to write.
///
/// A conflicting configuration falls back to the canonical override so that
/// diagnostics still have a destination. The application itself refuses to
/// start in that state - see `resolve_data_dir`.
pub fn data_dir() -> PathBuf {
    match resolve_data_dir() {
        Ok(dir) => dir,
        Err(_) => match std::env::var_os(DATA_DIR_ENV) {
            Some(value) if !value.is_empty() => normalize_data_dir(Path::new(&value)),
            _ => default_data_dir(),
        },
    }
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

pub fn spawn_sidecar(app_handle: &tauri::AppHandle, dir: &Path) -> Result<Child, String> {
    // The profile directory is resolved ONCE, by the caller, and handed to the
    // sidecar explicitly. A conflicting configuration is fatal: starting with a
    // split profile would leave the sidecar waiting for a handshake that lands
    // in a directory nobody is watching.
    let dir = dir.to_path_buf();
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
    let mut command = Command::new(&python);
    command
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
        // The resolved profile directory is handed to the sidecar explicitly.
        // The sidecar then has exactly one source of truth for the database,
        // the profile lock, the handshake file and its own log file.
        .env(DATA_DIR_ENV, &dir)
        .env("SYTHION_LOG_DIR", &log_dir)
        // The legacy variable is removed so the sidecar can never read a
        // different directory than the one the shell resolved.
        .env_remove(LEGACY_DATA_DIR_ENV)
        .stdout(stdout_file.map(Stdio::from).unwrap_or_else(Stdio::null))
        .stderr(stderr_file.map(Stdio::from).unwrap_or_else(Stdio::null));

    // Keep the sidecar's console invisible. See `sidecar_creation_flags`.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(sidecar_creation_flags());
    }

    let child = command
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
            crate::shell_log(
                "[sythion] could not assign sidecar to job object; orphan protection unavailable",
            );
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
                crate::shell_log("[sythion] sidecar exited before writing its handshake");
                return;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        crate::shell_log("[sythion] timed out waiting for the sidecar handshake");
    });
}

#[cfg(test)]
mod tests {
    use super::{
        data_dir_key, development_mode_flag, development_mode_flag_for, normalize_data_dir,
        resolve_data_dir_from, sidecar_creation_flags, DATA_DIR_ENV, DEFAULT_DATA_DIR_NAME,
        LEGACY_DATA_DIR_ENV,
    };
    use std::ffi::OsStr;
    use std::path::{Path, PathBuf};

    fn resolve(canonical: Option<&str>, legacy: Option<&str>) -> Result<PathBuf, String> {
        let default = PathBuf::from("C:\\Users\\test\\AppData\\Local")
            .join(DEFAULT_DATA_DIR_NAME);
        resolve_data_dir_from(
            canonical.map(OsStr::new),
            legacy.map(OsStr::new),
            &default,
        )
    }

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

    /// The sidecar is a console-subsystem program started by a GUI-subsystem
    /// parent. Without CREATE_NO_WINDOW Windows allocates a console for it and
    /// shows it as an empty terminal window in front of the application.
    #[test]
    fn the_sidecar_never_shows_a_console_window() {
        #[cfg(windows)]
        assert_eq!(sidecar_creation_flags() & 0x0800_0000, 0x0800_0000);
        #[cfg(not(windows))]
        assert_eq!(sidecar_creation_flags(), 0);
    }

    /// CREATE_NEW_CONSOLE would undo CREATE_NO_WINDOW and put the console
    /// back on screen, so the two must never be combined.
    #[test]
    fn the_sidecar_never_requests_a_new_console() {
        #[cfg(windows)]
        assert_eq!(sidecar_creation_flags() & 0x0000_0010, 0);
        #[cfg(not(windows))]
        assert_eq!(sidecar_creation_flags(), 0);
    }

    /* Profile directory contract.
     *
     * A split profile is not cosmetic: the sidecar would write its handshake
     * into one directory while the shell looked for it in another, the bridge
     * would never resolve and the application would hang on "Connecting to
     * Sythion" until the handshake timeout expired.
     */

    /// The canonical variable alone decides the profile.
    #[test]
    fn the_canonical_variable_selects_the_profile() {
        let dir = resolve(Some("C:\\profiles\\sythion"), None).expect("resolved");
        assert_eq!(dir, PathBuf::from("C:\\profiles\\sythion"));
    }

    /// The legacy variable is still honoured when the canonical one is absent.
    #[test]
    fn the_legacy_variable_is_still_honoured() {
        let dir = resolve(None, Some("C:\\profiles\\legacy")).expect("resolved");
        assert_eq!(dir, PathBuf::from("C:\\profiles\\legacy"));
    }

    /// Both variables naming the same directory are not a conflict.
    #[test]
    fn matching_overrides_are_accepted() {
        let dir = resolve(Some("C:\\profiles\\sythion"), Some("C:\\profiles\\sythion"))
            .expect("resolved");
        assert_eq!(dir, PathBuf::from("C:\\profiles\\sythion"));
    }

    /// The same directory written differently is still the same directory.
    #[test]
    fn equivalent_overrides_are_accepted() {
        let dir = resolve(
            Some("C:\\profiles\\sythion"),
            Some("C:\\profiles\\sythion\\.\\"),
        )
        .expect("resolved");
        assert_eq!(dir, PathBuf::from("C:\\profiles\\sythion"));
    }

    /// Two different directories are a misconfiguration that must not be
    /// resolved silently.
    #[test]
    fn conflicting_overrides_are_rejected() {
        let err = resolve(Some("C:\\profiles\\one"), Some("C:\\profiles\\two"))
            .expect_err("conflicting overrides must fail");
        assert!(err.contains(DATA_DIR_ENV), "diagnostic names the canonical variable: {err}");
        assert!(err.contains(LEGACY_DATA_DIR_ENV), "diagnostic names the legacy variable: {err}");
        assert!(err.contains("C:\\profiles\\one"));
        assert!(err.contains("C:\\profiles\\two"));
    }

    /// With neither override the established default is used.
    #[test]
    fn the_default_is_used_without_overrides() {
        let dir = resolve(None, None).expect("resolved");
        assert!(dir.ends_with(DEFAULT_DATA_DIR_NAME));
    }

    /// An empty override counts as absent, not as "the current directory".
    #[test]
    fn empty_overrides_count_as_absent() {
        let dir = resolve(Some(""), None).expect("resolved");
        assert!(dir.ends_with(DEFAULT_DATA_DIR_NAME));
        let dir = resolve(Some("   "), Some("")).expect("resolved");
        assert!(dir.ends_with(DEFAULT_DATA_DIR_NAME));
    }

    /// The resolved directory is absolute, so the sidecar cannot interpret it
    /// relative to its own working directory.
    #[test]
    fn the_resolved_directory_is_absolute() {
        let dir = resolve(Some("relative\\profile"), None).expect("resolved");
        assert!(dir.is_absolute(), "{dir:?} must be absolute");
    }

    /// Comparison folds case on Windows, where paths are case-insensitive.
    #[test]
    fn comparison_is_case_insensitive_on_windows() {
        let a = Path::new("C:\\Profiles\\Sythion");
        let b = Path::new("c:\\profiles\\sythion");
        if cfg!(windows) {
            assert_eq!(data_dir_key(a), data_dir_key(b));
        } else {
            assert_ne!(data_dir_key(a), data_dir_key(b));
        }
    }

    /// Normalization collapses `.` and `..` without touching the filesystem.
    #[test]
    fn normalization_is_lexical() {
        assert_eq!(
            normalize_data_dir(Path::new("C:\\a\\b\\..\\c")),
            PathBuf::from("C:\\a\\c")
        );
        assert_eq!(
            normalize_data_dir(Path::new("C:\\a\\.\\b")),
            PathBuf::from("C:\\a\\b")
        );
    }
}
