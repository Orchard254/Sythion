//! Trusted native host half of the recovery-presence attestation boundary.
//!
//! The per-launch key is generated here, never in JavaScript, model context,
//! the control token, or a request header. The host provisions it to the
//! sidecar through a private file in the profile data dir (user-only ACL)
//! created by the same atomic/ACL helper used by the handshake token.
//! A challenge is signed ONLY after the window-bound OS verification returns
//! `Verified`. Every other outcome denies. This module never clears recovery,
//! approves actions, or mutates task state.

use std::collections::HashSet;
use std::path::Path;
use std::sync::Mutex;

use hmac::{Hmac, Mac};
use sha2::Sha256;

use crate::sidecar::write_private_text_file;

pub const KEY_FILE_NAME: &str = "native_attestation.key";
const DOMAIN: &[u8] = b"SYTHION/NATIVE_RECOVERY_PRESENCE/v1\0";
const PURPOSE: &str = "recovery_review_only";
const OUTCOME: &str = "native_os_verification_succeeded";

type HmacSha256 = Hmac<Sha256>;

pub struct NativeAttestation {
    key: [u8; 32],
    signed: Mutex<HashSet<String>>,
}

impl NativeAttestation {
    pub fn new() -> Result<Self, String> {
        let mut key = [0u8; 32];
        getrandom::getrandom(&mut key).map_err(|e| format!("native key RNG: {e}"))?;
        Ok(Self { key, signed: Mutex::new(HashSet::new()) })
    }

    /// Provision the verifier key to the sidecar via a user-only-ACL file.
    pub fn provision(&self, dir: &Path) -> Result<std::path::PathBuf, String> {
        let content = hex::encode(self.key);
        write_private_text_file(dir, KEY_FILE_NAME, content.as_bytes())
    }

    fn canonical_message(
        challenge_id: &str, task_id: &str, evidence_revision: &str,
    ) -> Vec<u8> {
        let mut message = DOMAIN.to_vec();
        for part in [challenge_id, task_id, evidence_revision, PURPOSE, OUTCOME] {
            let bytes = part.as_bytes();
            let len = u32::try_from(bytes.len()).unwrap_or(u32::MAX);
            message.extend_from_slice(&len.to_be_bytes());
            message.extend_from_slice(bytes);
        }
        message
    }

    /// Sign a challenge exactly once. Denied if the challenge was already used.
    pub fn sign(
        &self, challenge_id: &str, task_id: &str, evidence_revision: &str,
    ) -> Result<String, String> {
        {
            let mut signed = self.signed.lock().map_err(|_| "native signer lock poisoned")?;
            if challenge_id.is_empty() || challenge_id.len() > 128 || task_id.is_empty()
                || task_id.len() > 128 || evidence_revision.len() > 160
            {
                return Err("invalid native challenge scope".to_string());
            }
            if !signed.insert(challenge_id.to_string()) {
                return Err("native challenge already signed".to_string());
            }
        }
        let message = Self::canonical_message(challenge_id, task_id, evidence_revision);
        let mut mac = HmacSha256::new_from_slice(&self.key)
            .map_err(|_| "native HMAC key invalid".to_string())?;
        mac.update(&message);
        Ok(hex::encode(mac.finalize().into_bytes()))
    }
}

#[cfg(windows)]
fn verify_windows_presence(hwnd: isize, message: &str) -> Result<bool, String> {
    use std::time::Duration;

    use windows::core::HSTRING;
    use windows::Security::Credentials::UI::UserConsentVerificationResult;
    use windows_future::{AsyncStatus, IAsyncOperation};
    use windows::Win32::Foundation::HWND;
    use windows::Win32::System::WinRT::{
        RoGetActivationFactory, RoInitialize, IUserConsentVerifierInterop,
        RO_INIT_MULTITHREADED,
    };

    // Multiple threads may initialize COM; RPC_E_CHANGED_MODE is not a failure.
    unsafe {
        let _ = RoInitialize(RO_INIT_MULTITHREADED);
    }
    let class = HSTRING::from("Windows.Security.Credentials.UI.UserConsentVerifier");
    let interop: IUserConsentVerifierInterop = unsafe { RoGetActivationFactory(&class) }
        .map_err(|e| format!("windows hello interop unavailable: {e:?}"))?;
    let handle = HWND(hwnd as *mut std::ffi::c_void);
    let prompt = HSTRING::from(message);
    let operation: IAsyncOperation<UserConsentVerificationResult> = unsafe {
        interop.RequestVerificationForWindowAsync(handle, &prompt)
    }
    .map_err(|e| format!("windows hello request failed: {e:?}"))?;

    // Block on the WinRT operation here so no !Send COM pointer crosses an
    // async boundary. Tauri runs this synchronous command on a worker thread.
    for _ in 0..900 {
        let status = operation.Status()
            .map_err(|e| format!("windows hello status unavailable: {e:?}"))?;
        match status {
            AsyncStatus::Completed => {
                let outcome = operation.GetResults()
                    .map_err(|e| format!("windows hello result unavailable: {e:?}"))?;
                return Ok(outcome == UserConsentVerificationResult::Verified);
            }
            AsyncStatus::Canceled => return Ok(false),
            AsyncStatus::Error => return Ok(false),
            AsyncStatus::Started => std::thread::sleep(Duration::from_millis(100)),
            _ => return Ok(false),
        }
    }
    Ok(false)
}

#[cfg(not(windows))]
fn verify_windows_presence(_hwnd: isize, _message: &str) -> Result<bool, String> {
    Err("native presence verification requires Windows".to_string())
}

/// Tauri command: verify OS presence for the owning window, then sign.
/// Any cancel / unavailable / busy / failure outcome denies and signs nothing.
#[tauri::command]
pub fn verify_native_presence(
    state: tauri::State<'_, NativeAttestation>,
    window: tauri::WebviewWindow,
    challenge_id: String,
    task_id: String,
    evidence_revision: String,
    message: String,
) -> Result<String, String> {
    let hwnd = window.hwnd().map_err(|e| format!("window handle unavailable: {e}"))?;
    if !verify_windows_presence(hwnd.0 as isize, &message)? {
        return Err("native presence not verified".to_string());
    }
    state.sign(&challenge_id, &task_id, &evidence_revision)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn attestation() -> NativeAttestation {
        // Deterministic key for offline tests only; production uses getrandom.
        NativeAttestation { key: [7u8; 32], signed: Mutex::new(HashSet::new()) }
    }

    #[test]
    fn canonical_message_is_length_prefixed_and_domain_separated() {
        let message = NativeAttestation::canonical_message("c1", "t1", "sha256:abc");
        assert!(message.starts_with(DOMAIN));
        // Backend uses 4-byte BIG-endian length prefixes.
        let expected_tail = [
            0u8, 0, 0, 2, b'c', b'1',
            0, 0, 0, 2, b't', b'1',
            0, 0, 0, 10, b's', b'h', b'a', b'2', b'5', b'6', b':', b'a', b'b', b'c',
        ];
        assert!(message.windows(expected_tail.len())
            .any(|window| window == expected_tail));
        for part in [PURPOSE, OUTCOME] {
            let prefix = (part.len() as u32).to_be_bytes();
            let mut expected = prefix.to_vec();
            expected.extend_from_slice(part.as_bytes());
            assert!(message.windows(expected.len()).any(|w| w == expected.as_slice()));
        }
    }

    #[test]
    fn print_canonical_vector_for_cross_language_conformance() {
        let message = NativeAttestation::canonical_message(
            "challenge-1", "task-1", "sha256:abc");
        println!("CROSS_LANG_VECTOR={}", hex::encode(message));
    }

    #[test]
    fn signing_is_one_shot_per_challenge() {
        let state = attestation();
        let first = state.sign("challenge-1", "task-1", "sha256:abc").unwrap();
        assert_eq!(first.len(), 64);
        assert!(state.sign("challenge-1", "task-1", "sha256:abc").is_err());
    }

    #[test]
    fn signature_binds_task_and_revision() {
        let state = attestation();
        let a = state.sign("c1", "task-a", "sha256:aaa").unwrap();
        let b = state.sign("c2", "task-b", "sha256:bbb").unwrap();
        assert_ne!(a, b);
    }

    #[test]
    fn invalid_scope_is_rejected_without_signing() {
        let state = attestation();
        assert!(state.sign("", "task", "sha256:abc").is_err());
        assert!(state.sign("c1", "", "sha256:abc").is_err());
        assert!(state.sign("c1", "task", &"x".repeat(200)).is_err());
    }
}
