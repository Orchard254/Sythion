"""Provider-agnostic, bounded reasoning invocation boundary.

The runtime owns work state, policy, execution and evidence. A reasoning job
only requests computation from a model; its result never authorizes effects.
This small boundary does not schedule autonomous work or persist tasks.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, TypeVar


class ReasoningPurpose(str, Enum):
    CONVERSATION = "conversation"
    TASK_DECISION = "task_decision"
    GOAL_PLANNING = "goal_planning"


@dataclass(frozen=True, slots=True)
class ReasoningJob:
    """Metadata for one bounded provider call, not an execution grant.

    ``work_id`` associates a reasoning call with existing durable work when
    available. ``job_id`` uniquely identifies the invocation, not the goal.
    No prompt, credentials or model response are stored in this metadata.
    """

    purpose: ReasoningPurpose
    timeout_seconds: float
    work_id: str | None = None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Identity of the reasoning processor actually used for this job. Empty
    # until the caller resolves the active provider; never inferred from the
    # model's own output.
    provider_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, ReasoningPurpose):
            raise ValueError("unsupported reasoning purpose")
        timeout = self.timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if self.work_id is not None and (
            not isinstance(self.work_id, str)
            or not self.work_id.strip()
            or self.work_id != self.work_id.strip()
        ):
            raise ValueError("work_id must be a non-empty, trimmed string")
        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        if not isinstance(self.provider_id, str) or self.provider_id != self.provider_id.strip():
            raise ValueError("provider_id must be a trimmed string")


T = TypeVar("T")

REASONING_RESULT_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReasoningResult:
    """One validated reasoning output, shared by all entry paths.

    This is a *proposal*, not execution evidence: it deliberately has no
    fields for tool calls, permissions, approvals, consent, task state or
    success claims. The runtime may accept, reject or ignore it. The caller
    decides what, if anything, to do with ``value``.
    """

    job_id: str
    purpose: ReasoningPurpose
    provider_id: str
    work_id: str | None
    value: Any
    contract_version: int = REASONING_RESULT_VERSION

    def __post_init__(self) -> None:
        if (isinstance(self.contract_version, bool)
                or self.contract_version != REASONING_RESULT_VERSION):
            raise ReasoningResultError("unsupported reasoning result contract version")


class ReasoningResultError(RuntimeError):
    """Model output did not satisfy the result contract; fail closed."""


class ReasoningAdmissionError(RuntimeError):
    """A reasoning invocation was not admitted; no provider call may happen."""


class DispatchVerdict(str, Enum):
    DISPATCH = "dispatch"                  # call a reasoning processor
    DIRECT_PROCEDURE = "direct_procedure"  # runtime handles it without a model
    DEFER = "defer"                        # wait for an actor/resource
    DO_NOTHING = "do_nothing"              # nothing requires reasoning


@dataclass(frozen=True, slots=True)
class AttentionSignals:
    """Runtime-owned facts used to decide whether reasoning is warranted.

    No field is derived from model output. Absence of new input or active work
    is a legitimate reason to call no model at all.
    """

    has_new_input: bool = False
    work_active: bool = False
    pending_mutating_action: bool = False
    budget_available: bool = True
    provider_available: bool = True
    waiting_for_actor: bool = False
    direct_procedure_available: bool = False


@dataclass(frozen=True, slots=True)
class AttentionDecision:
    verdict: DispatchVerdict
    reason: str


def decide_attention(signals: AttentionSignals) -> AttentionDecision:
    """Bounded attention decision shared by every reasoning entry path."""
    if not isinstance(signals, AttentionSignals):
        raise TypeError("signals must be AttentionSignals")
    if signals.pending_mutating_action:
        return AttentionDecision(DispatchVerdict.DEFER, "pending mutating action")
    if not signals.provider_available:
        return AttentionDecision(DispatchVerdict.DEFER, "provider unavailable")
    if not signals.budget_available:
        return AttentionDecision(DispatchVerdict.DEFER, "budget unavailable")
    if signals.waiting_for_actor:
        return AttentionDecision(DispatchVerdict.DEFER, "waiting for an actor")
    if signals.has_new_input:
        return AttentionDecision(DispatchVerdict.DISPATCH, "new input requires reasoning")
    if signals.direct_procedure_available and signals.work_active:
        return AttentionDecision(
            DispatchVerdict.DIRECT_PROCEDURE, "runtime procedure handles this work")
    if signals.work_active:
        return AttentionDecision(DispatchVerdict.DISPATCH, "active work requires reasoning")
    return AttentionDecision(DispatchVerdict.DO_NOTHING, "nothing requires reasoning")


class ReasoningEvent(str, Enum):
    ADMITTED = "admitted"
    DIRECT_PROCEDURE = "direct_procedure"
    DEFERRED = "deferred"
    IDLE = "idle"
    MODEL_STARTED = "model_started"
    MODEL_RESPONSE = "model_response"
    MODEL_FAILED = "model_failed"
    VERIFIED_WORK = "verified_work"


@dataclass(frozen=True, slots=True)
class DispatchEvent:
    event: ReasoningEvent
    job_id: str
    purpose: ReasoningPurpose | None
    provider_id: str
    at: float
    detail: str = ""
    latency_seconds: float | None = None


class ReasoningEventBus:
    """Bounded, read-only event history with synchronous subscribers.

    Events carry identity and coarse outcomes only: no prompts, model output,
    credentials, task text or evidence payloads. Subscribers cannot authorize
    work or change task state; they observe.
    """

    def __init__(self, capacity: int = 1000):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._history: deque[DispatchEvent] = deque(maxlen=capacity)
        self._subscribers: list[Callable[[DispatchEvent], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, handler: Callable[[DispatchEvent], None]) -> None:
        if not callable(handler):
            raise TypeError("event handler must be callable")
        with self._lock:
            self._subscribers.append(handler)

    def emit(self, event: DispatchEvent) -> None:
        if not isinstance(event, DispatchEvent):
            raise TypeError("event must be a DispatchEvent")
        with self._lock:
            self._history.append(event)
            handlers = tuple(self._subscribers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # An observer must never break the reasoning producer. Only
                # bounded history is authoritative; subscribers are advisory.
                pass

    def history(self) -> tuple:
        with self._lock:
            return tuple(self._history)

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


@dataclass(frozen=True, slots=True)
class EffectivenessReport:
    """S183 evaluation: usefulness, not call activity, is the success signal."""

    admitted: int
    model_calls: int
    idle: int
    deferred: int
    direct_procedures: int
    model_responses: int
    failed_calls: int
    verified_useful: int
    total_latency_seconds: float
    total_tokens: int
    context_records: int
    context_items: int
    estimated_context_tokens: int
    avg_context_relevance: float
    activity_only: bool
    effective: bool
    reason: str


class ReasoningMetrics:
    """Bounded counters for reasoning requests, latency and useful work.

    ``record_verified_work`` requires an evidence reference: a bare call count
    can never be promoted to a success measure.
    """

    def __init__(self, event_bus: ReasoningEventBus | None = None):
        self._lock = threading.Lock()
        self._events = event_bus or ReasoningEventBus()
        self._counters: Dict[str, int] = {
            "admitted": 0, "model_calls": 0, "idle": 0, "deferred": 0,
            "direct_procedures": 0, "model_responses": 0, "failed_calls": 0,
            "verified_useful": 0, "total_tokens": 0,
            "context_records": 0, "context_items": 0,
            "estimated_context_tokens": 0,
        }
        self._total_latency = 0.0
        self._relevance_sum = 0.0
        self._started: Dict[str, float] = {}

    @property
    def events(self) -> ReasoningEventBus:
        return self._events

    def _count(self, name: str) -> None:
        self._counters[name] = self._counters.get(name, 0) + 1

    def record_attention(self, job_id: str, decision: AttentionDecision,
                         purpose: ReasoningPurpose | None = None,
                         provider_id: str = "") -> None:
        if not isinstance(decision, AttentionDecision):
            raise TypeError("decision must be an AttentionDecision")
        mapping = {
            DispatchVerdict.DISPATCH: (ReasoningEvent.ADMITTED, "admitted"),
            DispatchVerdict.DIRECT_PROCEDURE: (
                ReasoningEvent.DIRECT_PROCEDURE, "direct_procedures"),
            DispatchVerdict.DEFER: (ReasoningEvent.DEFERRED, "deferred"),
            DispatchVerdict.DO_NOTHING: (ReasoningEvent.IDLE, "idle"),
        }
        event, counter = mapping[decision.verdict]
        with self._lock:
            self._count(counter)
        self._events.emit(DispatchEvent(
            event, job_id, purpose, provider_id, time.monotonic(), decision.reason))

    def record_model_started(self, job: ReasoningJob) -> None:
        now = time.monotonic()
        with self._lock:
            self._count("model_calls")
            self._started[job.job_id] = now
        self._events.emit(DispatchEvent(
            ReasoningEvent.MODEL_STARTED, job.job_id, job.purpose,
            job.provider_id, now))

    def record_model_outcome(self, job: ReasoningJob, *, success: bool,
                             tokens: int = 0) -> None:
        now = time.monotonic()
        with self._lock:
            started = self._started.pop(job.job_id, now)
            latency = max(0.0, now - started)
            self._total_latency += latency
            self._counters["total_tokens"] += max(0, tokens)
            self._count("model_responses" if success else "failed_calls")
        self._events.emit(DispatchEvent(
            ReasoningEvent.MODEL_RESPONSE if success else ReasoningEvent.MODEL_FAILED,
            job.job_id, job.purpose, job.provider_id, now,
            latency_seconds=latency))

    def record_verified_work(self, job: ReasoningJob, evidence_ref: str) -> None:
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise ValueError("verified work requires an evidence reference")
        with self._lock:
            self._count("verified_useful")
        self._events.emit(DispatchEvent(
            ReasoningEvent.VERIFIED_WORK, job.job_id, job.purpose,
            job.provider_id, time.monotonic(), detail="evidence_reference"))

    def record_context(self, working_set: Any) -> None:
        """Record bounded context-relevance and cost-proxy measurements.

        Cost is expressed as estimated context tokens, never as a currency
        claim: no provider price table is assumed here.
        """
        with self._lock:
            self._count("context_records")
            self._counters["context_items"] += len(getattr(working_set, "items", ()))
            self._counters["estimated_context_tokens"] += int(
                getattr(working_set, "token_estimate", lambda: 0)())
            self._relevance_sum += float(getattr(working_set, "relevance", lambda: 0.0)())

    def report(self) -> EffectivenessReport:
        with self._lock:
            values = dict(self._counters)
            latency = self._total_latency
            relevance = (
                self._relevance_sum / values["context_records"]
                if values["context_records"] else 0.0
            )
        activity_only = values["model_calls"] > 0 and values["verified_useful"] == 0
        effective = values["verified_useful"] > 0 and not activity_only
        reason = (
            "verified useful work recorded" if effective
            else "model activity without verified useful work"
            if activity_only else "no verified useful work yet"
        )
        return EffectivenessReport(
            admitted=values["admitted"], model_calls=values["model_calls"],
            idle=values["idle"], deferred=values["deferred"],
            direct_procedures=values["direct_procedures"],
            model_responses=values["model_responses"],
            failed_calls=values["failed_calls"],
            verified_useful=values["verified_useful"],
            total_latency_seconds=round(latency, 6),
            total_tokens=values["total_tokens"],
            context_records=values["context_records"],
            context_items=values["context_items"],
            estimated_context_tokens=values["estimated_context_tokens"],
            avg_context_relevance=round(relevance, 6),
            activity_only=activity_only, effective=effective, reason=reason,
        )

    def reset(self) -> None:
        with self._lock:
            for key in self._counters:
                self._counters[key] = 0
            self._total_latency = 0.0
            self._relevance_sum = 0.0
            self._started.clear()
            self._events.clear()


_DEFAULT_METRICS = ReasoningMetrics()


def get_reasoning_metrics() -> ReasoningMetrics:
    """Process-local measurement sink shared by all reasoning entry paths."""
    return _DEFAULT_METRICS


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """Runtime decision to admit ONE bounded reasoning invocation.

    Admission is structural and provider-neutral: it records which processor
    is being called and under what bound. It is not consent, not task state,
    and never authorizes an action.
    """

    admitted: bool
    reason: str
    job: ReasoningJob
    attention: AttentionDecision | None = None


def admit_reasoning_job(
    purpose: ReasoningPurpose,
    *,
    timeout_seconds: float,
    provider_id: str,
    work_id: str | None = None,
    pending_mutating_action: bool = False,
    signals: AttentionSignals | None = None,
) -> AdmissionDecision:
    """One factory for every entry path's reasoning job.

    Fail closed on an unidentified processor, missing work identity, or an
    unbounded deadline. No provider request may precede a successful admission.
    """
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ReasoningAdmissionError("reasoning processor identity is required")
    if provider_id != provider_id.strip():
        raise ReasoningAdmissionError("reasoning processor identity must be trimmed")
    if purpose is ReasoningPurpose.TASK_DECISION and (
        not isinstance(work_id, str) or not work_id.strip()
    ):
        raise ReasoningAdmissionError("task decisions require a work identity")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < timeout_seconds <= 300
    ):
        raise ReasoningAdmissionError("reasoning deadline is out of bounds")
    metrics = get_reasoning_metrics()
    if not isinstance(pending_mutating_action, bool):
        raise ReasoningAdmissionError("invalid pending-action state")
    if pending_mutating_action:
        metrics.record_attention(
            "admission-denied", AttentionDecision(DispatchVerdict.DEFER,
                                                  "pending mutating action"),
            purpose, provider_id)
        # A mutating action awaiting user decision must be resolved by the
        # runtime first. The model is not a consent authority.
        raise ReasoningAdmissionError("pending mutating action blocks reasoning")
    attention = None
    if signals is not None:
        if not isinstance(signals, AttentionSignals):
            raise ReasoningAdmissionError("invalid attention signals")
        attention = decide_attention(signals)
        if attention.verdict is not DispatchVerdict.DISPATCH:
            # No provider call: the runtime decided to do nothing, defer, or
            # handle the work with a direct procedure.
            metrics.record_attention(
                "admission-denied", attention, purpose, provider_id)
            raise ReasoningAdmissionError(
                f"dispatch {attention.verdict.value}: {attention.reason}")
    job = ReasoningJob(
        purpose=purpose, timeout_seconds=timeout_seconds,
        work_id=work_id, provider_id=provider_id,
    )
    metrics.record_attention(
        job.job_id,
        attention or AttentionDecision(DispatchVerdict.DISPATCH, "admitted"),
        purpose, provider_id)
    return AdmissionDecision(
        admitted=True, reason="admitted", job=job, attention=attention)


def handoff_reasoning_result(
    result: ReasoningResult, job: ReasoningJob
) -> ReasoningResult:
    """Bind a validated result back to the admitted job before use.

    Results from a different invocation, processor or purpose are rejected:
    a model output must never be replayed into another job's handoff.
    """
    if not isinstance(result, ReasoningResult):
        raise ReasoningResultError("result must be a ReasoningResult")
    if not isinstance(job, ReasoningJob):
        raise TypeError("job must be a ReasoningJob")
    if (result.job_id != job.job_id or result.purpose is not job.purpose
            or result.provider_id != job.provider_id or result.work_id != job.work_id
            or result.contract_version != REASONING_RESULT_VERSION):
        raise ReasoningResultError("reasoning result does not belong to this job")
    return result


class ReasoningJournalError(RuntimeError):
    """A reasoning invocation could not be durably recorded as required."""


def as_mapping_result(
    job: ReasoningJob,
    raw: Any,
    *,
    provider_id: str | None = None,
    max_keys: int = 10_000,
) -> ReasoningResult:
    """Validate an untrusted structured response into the shared contract."""
    if not isinstance(job, ReasoningJob):
        raise TypeError("job must be a ReasoningJob")
    if isinstance(raw, bool) or not isinstance(raw, dict):
        raise ReasoningResultError("model response must be a mapping")
    if isinstance(max_keys, bool) or not isinstance(max_keys, int) or max_keys <= 0:
        raise ValueError("max_keys must be a positive integer")
    if len(raw) > max_keys or any(not isinstance(k, str) for k in raw):
        raise ReasoningResultError("model response has an invalid mapping shape")
    resolved_provider = job.provider_id if provider_id is None else provider_id
    if not isinstance(resolved_provider, str) or resolved_provider != resolved_provider.strip():
        raise ValueError("provider_id must be a trimmed string")
    return ReasoningResult(
        job_id=job.job_id,
        purpose=job.purpose,
        provider_id=resolved_provider,
        work_id=job.work_id,
        value=dict(raw),
    )


def as_text_result(
    job: ReasoningJob,
    raw: Any,
    *,
    max_chars: int = 100_000,
    provider_id: str | None = None,
) -> ReasoningResult:
    """Validate untrusted text output into the shared result contract."""
    if not isinstance(job, ReasoningJob):
        raise TypeError("job must be a ReasoningJob")
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise ReasoningResultError("model response must be text")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    if len(raw) > max_chars:
        raise ReasoningResultError("model response exceeds the result limit")
    resolved_provider = job.provider_id if provider_id is None else provider_id
    if not isinstance(resolved_provider, str) or resolved_provider != resolved_provider.strip():
        raise ValueError("provider_id must be a trimmed string")
    return ReasoningResult(
        job_id=job.job_id,
        purpose=job.purpose,
        provider_id=resolved_provider,
        work_id=job.work_id,
        value=raw,
    )


async def _record_phase(on_event, job, phase):
    try:
        await on_event(job, phase)
    except Exception as exc:
        # Avoid leaking provider output, task content, or backend DB details.
        raise ReasoningJournalError("reasoning event persistence failed") from exc


# Runtime-owned dispatch bound: at most one in-flight reasoning call per
# work item (or per standalone conversation job). This is a structural gate,
# not a scheduler; it prevents overlapping processor calls being replayed as
# independent evidence for the same work.
_DISPATCH_SLOTS: dict[str, str] = {}
_DISPATCH_LOCK = threading.Lock()


def _dispatch_key(job: ReasoningJob) -> str:
    return f"{job.purpose.value}:{job.work_id or job.job_id}"


def acquire_dispatch_slot(job: ReasoningJob) -> None:
    key = _dispatch_key(job)
    with _DISPATCH_LOCK:
        if key in _DISPATCH_SLOTS:
            raise ReasoningAdmissionError("reasoning already in flight for this work")
        _DISPATCH_SLOTS[key] = job.job_id


def release_dispatch_slot(job: ReasoningJob) -> None:
    key = _dispatch_key(job)
    with _DISPATCH_LOCK:
        if _DISPATCH_SLOTS.get(key) == job.job_id:
            del _DISPATCH_SLOTS[key]


async def dispatch_reasoning(
    job: ReasoningJob,
    call: Callable[[], Awaitable[T]],
    *,
    on_event: Callable[[ReasoningJob, str], Awaitable[None]] | None = None,
) -> T:
    """Invoke one model call under a deadline; return its *untrusted* output.

    No retries, tool execution, state writes, permission approvals, or success
    claims happen here. Cancellation and provider failures propagate to the
    existing caller's error handling. The caller validates the model output.
    """
    if not isinstance(job, ReasoningJob):
        raise TypeError("job must be a ReasoningJob")
    if not callable(call):
        raise TypeError("call must be callable")
    if on_event is not None and not callable(on_event):
        raise TypeError("on_event must be callable")

    # The dispatch slot is a runtime-owned bound: no second processor call may
    # overlap for the same work item. It is always released, including on
    # cancellation, timeout and failure.
    metrics = get_reasoning_metrics()
    acquire_dispatch_slot(job)
    try:
        # Persistence must succeed *before* invoking a provider. A failed
        # start receipt is an admission failure, not a reason to make an
        # untracked call. Record the model as started only AFTER the receipt
        # is durably persisted: otherwise a failed start leaves a phantom
        # model_calls increment, a leaked _started entry and a MODEL_STARTED
        # event for a call that never happened, skewing effectiveness reports.
        if on_event is not None:
            await _record_phase(on_event, job, "started")
        metrics.record_model_started(job)
        try:
            awaitable = call()
            if not inspect.isawaitable(awaitable):
                raise TypeError("call must return an awaitable")
            result = await asyncio.wait_for(awaitable, timeout=job.timeout_seconds)
        except asyncio.CancelledError:
            metrics.record_model_outcome(job, success=False)
            # Never turn cancellation into a retry or a successful result.
            if on_event is not None:
                try:
                    await _record_phase(on_event, job, "cancelled")
                except Exception:
                    pass
            raise
        except TimeoutError:
            metrics.record_model_outcome(job, success=False)
            if on_event is not None:
                await _record_phase(on_event, job, "timed_out")
            raise
        except Exception:
            metrics.record_model_outcome(job, success=False)
            if on_event is not None:
                await _record_phase(on_event, job, "failed")
            raise

        # Persist the fact that the provider returned before its untrusted
        # response can be parsed or used by the caller. A failed completion
        # receipt is outside the provider-call try/except, so it must balance
        # the in-flight metrics here: otherwise _started leaks forever, no
        # terminal outcome is recorded, and the effectiveness report shows a
        # call with no result. The durable ledger honestly stays unresolved
        # for review — we do not fabricate a response_received receipt.
        try:
            if on_event is not None:
                await _record_phase(on_event, job, "response_received")
        except Exception:
            metrics.record_model_outcome(job, success=False)
            if on_event is not None:
                try:
                    await _record_phase(on_event, job, "failed")
                except Exception:
                    pass
            raise
        tokens = 0
        if isinstance(result, dict):
            usage = result.get("usage")
            if isinstance(usage, dict):
                for key in ("total_tokens", "prompt_eval_count", "eval_count",
                            "prompt_tokens", "completion_tokens"):
                    value = usage.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        tokens += value
        metrics.record_model_outcome(job, success=True, tokens=tokens)
        return result
    finally:
        release_dispatch_slot(job)
