"""Cross-language conformance of the native attestation canonical message.

The Tauri host (Rust) and the backend verifier (Python) must sign/verify
byte-identical messages. The fixed vector below was emitted by the Rust
`print_canonical_vector_for_cross_language_conformance` test and matched here.
"""

from backend.services.recovery_native_challenge import NativePresenceChallengeBroker

# hex: SYTHION/NATIVE_RECOVERY_PRESENCE/v1\0 + length-prefixed parts for
# challenge-1 / task-1 / sha256:abc / recovery_review_only /
# native_os_verification_succeeded (4-byte big-endian prefixes).
RUST_VECTOR = (
    "53595448494f4e2f4e41544956455f5245434f564552595f50524553454e43452f7631"
    "000000000b6368616c6c656e67652d31000000067461736b2d310000000a7368613235"
    "363a616263000000147265636f766572795f7265766965775f6f6e6c79000000206e61"
    "746976655f6f735f766572696669636174696f6e5f737563636565646564"
)


def test_python_canonical_message_matches_rust_vector():
    message = NativePresenceChallengeBroker._canonical_message(
        "challenge-1", "task-1", "sha256:abc")
    assert message.hex() == RUST_VECTOR


def test_revision_and_task_are_bound_not_free_text():
    a = NativePresenceChallengeBroker._canonical_message(
        "challenge-1", "task-1", "sha256:abc")
    b = NativePresenceChallengeBroker._canonical_message(
        "challenge-1", "task-2", "sha256:abc")
    c = NativePresenceChallengeBroker._canonical_message(
        "challenge-1", "task-1", "sha256:def")
    assert a != b
    assert a != c
