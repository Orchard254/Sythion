"""Offline, default-deny native recovery presence challenge contract.

This is NOT a Windows Hello implementation, human identity proof, approval,
recovery clearance, or execution grant. A *future* trusted Tauri host must
verify device-user presence itself before signing a particular challenge with
an independently provisioned, per-launch secret NEVER exposed to the webview,
HTTP caller, model or control-token channel. This contract verifies only the
integrity of that host assertion. It is deliberately NOT wired to resume.

Challenges are process-local, short lived, single use and fail closed after
restart. A local API token, ordinary user click or caller-provided boolean
cannot satisfy a challenge. Do not enable signing in a backend or renderer.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List

_REVISION = re.compile(r"^sha256:[a-f0-9]{64}$")
_TOKEN = re.compile(r"^[a-f0-9]{64}$")
_SIGNATURE = re.compile(r"^[a-f0-9]{64}$")
_DOMAIN = b"SYTHION/NATIVE_RECOVERY_PRESENCE/v1\x00"
_MAX_ATTEMPTS = 3
_MAX_PENDING = 64
_DEFAULT_TTL = 90.0


@dataclass(frozen=True, slots=True)
class NativePresenceChallenge:
    challenge_id: str
    task_id: str
    evidence_revision: str
    expires_in_seconds: int
    purpose: str = "recovery_review_only"


@dataclass(frozen=True, slots=True)
class NativePresenceObservation:
    """Host-message integrity only; the OS verification is NOT implemented here."""

    host_message_authenticated: bool = False
    authenticated_human: bool = False
    independent_effect_verified: bool = False
    recovery_cleared: bool = False
    authorization_issued: bool = False
    automatic_replay_allowed: bool = False
    purpose: str = "recovery_review_only"


@dataclass(slots=True)
class _Pending:
    task_id: str
    revision: str
    expires: float
    attempts: int = 0


class NativePresenceChallengeBroker:
    """Process-local one-shot handshake verifier; disabled until host key wired.

    The key must be generated exclusively by a trusted native process for the
    current launch and transported through a protected host/backend channel.
    The API and webview must never choose or read it. The construction contract
    cannot prove such origin, so it is not used as a production authenticator.
    """

    def __init__(self, host_key: bytes | None = None, *, monotonic: Callable[[], float] = time.monotonic):
        if host_key is not None and (type(host_key) is not bytes or len(host_key) < 32):
            raise ValueError("native host key must be at least 32 bytes")
        if not callable(monotonic):
            raise ValueError("monotonic clock is required")
        self._key = host_key
        self._clock = monotonic
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}

    @property
    def configured(self) -> bool:
        return self._key is not None

    def issue(self, task_id: str, evidence_revision: str, *, ttl_seconds: int = 90) -> NativePresenceChallenge:
        if not self.configured:
            raise PermissionError("trusted native verifier is unavailable")
        if (type(task_id) is not str or not task_id or len(task_id) > 128
                or task_id != task_id.strip()):
            raise ValueError("invalid task scope")
        if type(evidence_revision) is not str or not _REVISION.fullmatch(evidence_revision):
            raise ValueError("invalid evidence revision")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 120:
            raise ValueError("invalid challenge expiry")
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._pending) >= _MAX_PENDING:
                raise RuntimeError("native recovery challenge capacity reached")
            identifier = secrets.token_hex(32)
            self._pending[identifier] = _Pending(task_id, evidence_revision, now + ttl_seconds)
        return NativePresenceChallenge(identifier, task_id, evidence_revision, ttl_seconds)

    def _prune(self, now: float) -> None:
        for token, entry in list(self._pending.items()):
            if now >= entry.expires:
                del self._pending[token]

    @staticmethod
    def _canonical_message(challenge_id: str, task_id: str, evidence_revision: str) -> bytes:
        # Fixed order + unambiguous length prefixes; neither caller free-text
        # nor provider output is accepted in this signing contract.
        parts = (challenge_id, task_id, evidence_revision, "recovery_review_only", "native_os_verification_succeeded")
        return _DOMAIN + b"".join(len(p.encode("utf-8")).to_bytes(4, "big") + p.encode("utf-8") for p in parts)

    def verify_host_assertion(
        self, *, challenge_id: str, task_id: str, evidence_revision: str,
        signature_hex: str, current_evidence_revision: str,
    ) -> NativePresenceObservation:
        """Verify a trusted *host claim*, not independently prove OS user presence.

        The caller must freshly compute ``current_evidence_revision`` from
        authoritative task state. A webview-provided revision is NEVER enough
        and an eventual grant must recheck it under the writer transaction.
        The native host must sign only after a successful window-bound OS
        challenge. The signed message binds the fixed verifier outcome label;
        the backend never takes that label from an HTTP caller. Until that host is implemented and its key channel secured,
        do not wire this method to any HTTP route or allow it to clear blockers.
        """
        if not self.configured:
            raise PermissionError("trusted native verifier is unavailable")
        if type(challenge_id) is not str or not _TOKEN.fullmatch(challenge_id):
            raise PermissionError("invalid native challenge")
        if type(signature_hex) is not str or not _SIGNATURE.fullmatch(signature_hex):
            raise PermissionError("invalid native assertion")
        with self._lock:
            now = self._clock()
            self._prune(now)
            entry = self._pending.get(challenge_id)
            if entry is None:
                raise PermissionError("native challenge unavailable or consumed")
            entry.attempts += 1
            if entry.attempts >= _MAX_ATTEMPTS:
                del self._pending[challenge_id]
            if current_evidence_revision != entry.revision:
                self._pending.pop(challenge_id, None)
                raise PermissionError("recovery evidence revision changed")
            if task_id != entry.task_id or evidence_revision != entry.revision:
                raise PermissionError("native assertion scope mismatch")
            msg = self._canonical_message(challenge_id, task_id, evidence_revision)
            expected = hmac.new(self._key, msg, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature_hex):
                raise PermissionError("native assertion invalid")
            self._pending.pop(challenge_id, None)
        return NativePresenceObservation(host_message_authenticated=True)


@dataclass(frozen=True, slots=True)
class NativeAttestationReceipt:
    """Auditable record of ONE verified trusted-host assertion.

    It records host-message integrity only. Every authority flag is fixed
    false: a host signature does not prove an authenticated human, an external
    effect outcome, recovery clearance, consent, or replay permission.
    """

    receipt_id: str
    challenge_id: str
    task_id: str
    evidence_revision: str
    observed_at: float
    host_message_authenticated: bool = True
    authenticated_human: bool = False
    independent_effect_verified: bool = False
    recovery_cleared: bool = False
    authorization_issued: bool = False
    automatic_replay_allowed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.receipt_id, str) or not self.receipt_id:
            raise ValueError("receipt identity is required")
        for name in (
            "host_message_authenticated", "authenticated_human",
            "independent_effect_verified", "recovery_cleared",
            "authorization_issued", "automatic_replay_allowed",
        ):
            value = getattr(self, name)
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
        if (self.host_message_authenticated is not True
                or self.authenticated_human is not False
                or self.independent_effect_verified is not False
                or self.recovery_cleared is not False
                or self.authorization_issued is not False
                or self.automatic_replay_allowed is not False):
            raise ValueError("attestation receipt may not carry authority flags")


class TrustedHostAttestationBoundary:
    """Backend half of the trusted-host boundary.

    The verifier key can only be supplied by a ``key_provider`` callable wired
    by the trusted launcher at process start. No HTTP/webview/model input can
    set it, and there is deliberately no key setter. The provider is consumed
    exactly once. This does NOT implement Windows Hello or prove human
    presence; the native host must sign only after its own OS verification.
    """

    def __init__(self, key_provider: Callable[[], bytes], *, monotonic: Callable[[], float] = time.monotonic):
        if not callable(key_provider):
            raise ValueError("a trusted host key provider is required")
        key = key_provider()
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("native host key must be at least 32 bytes")
        self._broker = NativePresenceChallengeBroker(host_key=key, monotonic=monotonic)
        self._clock = monotonic
        self._receipts: List[NativeAttestationReceipt] = []
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self._broker.configured

    def issue(self, task_id: str, evidence_revision: str, *, ttl_seconds: int = 90) -> NativePresenceChallenge:
        return self._broker.issue(task_id, evidence_revision, ttl_seconds=ttl_seconds)

    def verify(
        self, *, challenge_id: str, task_id: str, evidence_revision: str,
        signature_hex: str, current_evidence_revision: str,
    ) -> NativeAttestationReceipt:
        observation = self._broker.verify_host_assertion(
            challenge_id=challenge_id, task_id=task_id,
            evidence_revision=evidence_revision, signature_hex=signature_hex,
            current_evidence_revision=current_evidence_revision,
        )
        if observation != NativePresenceObservation(host_message_authenticated=True):
            raise PermissionError("unexpected native attestation observation")
        with self._lock:
            receipt = NativeAttestationReceipt(
                receipt_id=secrets.token_hex(16),
                challenge_id=challenge_id,
                task_id=task_id,
                evidence_revision=evidence_revision,
                observed_at=self._clock(),
            )
            self._receipts.append(receipt)
            return receipt

    def receipts(self) -> tuple:
        """Read-only audit copies; never authority or consent evidence."""
        with self._lock:
            return tuple(self._receipts)
