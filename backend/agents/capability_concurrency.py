"""Phase 10.5 — Capability Concurrency Truth Table.

Machine-readable classification for every registered capability.
Classification must be evidence-based; UNKNOWN defaults to serialized.

Policy source: backend/services/capabilities.py _specs()
Concurrency stages: backend/agents/coordination.py _REASONING_ONLY_CAPS / _NON_DESTRUCTIVE_CAPS
Stage gates: backend/agents/state.py ParallelismStage

Do not infer safety merely from tool naming. Defaults: SERIAL_ONLY / UNKNOWN serialized.
"""
from __future__ import annotations

from typing import Dict

# Concurrency classes
SERIAL_ONLY = "SERIAL_ONLY"
REASONING_PARALLEL = "REASONING_PARALLEL"
NON_DESTRUCTIVE_PARALLEL = "NON_DESTRUCTIVE_PARALLEL"
FULLY_PARALLEL = "FULLY_PARALLEL"
UNKNOWN = "UNKNOWN"

# Machine-readable truth table — one entry per registered capability id.
# Evidence / rationale is documented; UNTESTED capabilities remain UNKNOWN/SERIAL_ONLY.

CAPABILITY_CONCURRENCY: Dict[str, Dict[str, str]] = {
    # Read-only observation — validated Stage 1 reason-parallel safe (no OS mutation, no file writes).
    "system_observation": {
        "class": REASONING_PARALLEL,
        "stage": "STAGE_1_REASONING_ONLY",
        "evidence": "Read-only OS queries (time, battery, disk, processes, network, windows, clipboard). No mutation; ToolExecutor stateless. Stress tested via parallel reasoning specialists.",
        "notes": "Validated for Stage 1. Stage 2+ also safe but gated by same class.",
    },
    # Read-only ledger recall — same reasoning-parallel envelope.
    "activity_recall": {
        "class": REASONING_PARALLEL,
        "stage": "STAGE_1_REASONING_ONLY",
        "evidence": "Queries immutable task_events ledger; no mutation. Isolated per-task history.",
        "notes": "Stage 1 safe; read-only.",
    },
    # LLM reasoning via provider — per-model keyed lock now validated (Phase 10.75).
    "reasoning_model": {
        "class": REASONING_PARALLEL,
        "stage": "STAGE_1_REASONING_ONLY",
        "evidence": "Phase 10.75 validated per-model keyed async Lock (no global bottleneck, immutable profile replace). Concurrent same-model cold starts serialize narrowly; different models never block; failure does not poison other model; repeated probes bounded. httpx AsyncClient concurrent calls isolated per specialist.",
        "notes": "Stage 1 parallel validated via mocked provider isolation tests (success/failure/mixed/restart). Real provider E2E gated.",
    },
    # Persistent memory (store/recall/link) — non-destructive read/write with typed scopes. Stage 2.
    "persistent_memory": {
        "class": NON_DESTRUCTIVE_PARALLEL,
        "stage": "STAGE_2_NON_DESTRUCTIVE",
        "evidence": "Writes scoped via ScopedMemoryAccess (TASK/GOAL by default); permanent USER/GLOBAL writes blocked. SQLite WAL writer serialization + UNIQUE retry protects DB; Phase 10.75 same-key/different-key stress shows no corruption/no duplicate ACTIVE/no lost unrelated memories, provenance preserved, but semantic merge policy still insufficient for production — Stage 2 remains disabled.",
        "notes": "Stage 2 only; validated for DB safety but NOT enabled for production parallelism. Same-key contention is serialized via retry; semantic merge last-writer-wins, not CRDT.",
    },
    # Goal planning (objective_create etc.) — deterministic validation, no OS mutation.
    "goal_planning": {
        "class": NON_DESTRUCTIVE_PARALLEL,
        "stage": "STAGE_2_NON_DESTRUCTIVE",
        "evidence": "Deterministic schema/dependency validation, versioned plans; no OS tool. SQLite serialized writes.",
        "notes": "Stage 2 only.",
    },
    # Filesystem — writer contention, rename/move races, directory-level races. Must be serialized until proven.
    "file_operations": {
        "class": SERIAL_ONLY,
        "stage": "STAGE_0_SERIALIZED",
        "evidence": "Atomic write uses temp+os.replace per file, but concurrent writes to same file / same directory / conflicting rename/move have not been proven safe. Distinct-file safety requires capability policy permit; now SERIAL_ONLY.",
        "notes": "Do NOT claim general filesystem parallel safety. Serialized regardless of stage; see file concurrency tests.",
    },
    # GUI automation — ALWAYS serialized via MTA worker, secure desktop checks.
    "gui_automation": {
        "class": SERIAL_ONLY,
        "stage": "STAGE_0_SERIALIZED",
        "evidence": "Dedicated UIA MTA worker (single thread), foreground/focus requirements, secure desktop/elevated checks. Cannot be parallelized safely.",
        "notes": "Hard SERIAL_ONLY even at STAGE_3. capability_is_concurrent_safe always False.",
    },
    # Mutating OS control (power, brightness, volume) — serialized.
    "system_control": {
        "class": SERIAL_ONLY,
        "stage": "STAGE_0_SERIALIZED",
        "evidence": "Mutating OS state; HIGH risk, permission-gated. No parallel validation.",
        "notes": "Never FULLY_PARALLEL.",
    },
    # Application control (launch/focus/close) — desktop mutation.
    "app_control": {
        "class": SERIAL_ONLY,
        "stage": "STAGE_0_SERIALIZED",
        "evidence": "Desktop mutation, window identity checks. Not validated for parallelism.",
        "notes": "Serialized.",
    },
    # Package management (winget) — system mutation.
    "package_management": {
        "class": SERIAL_ONLY,
        "stage": "STAGE_0_SERIALIZED",
        "evidence": "Fixed-argument winget; mutates system packages. HIGH risk.",
        "notes": "Serialized.",
    },
    # Browser control — external bridge, tab/page state.
    "browser_control": {
        "class": SERIAL_ONLY,
        "stage": "STAGE_0_SERIALIZED",
        "evidence": "Tab discovery/page interaction via BrowserBridgeService; bridge connectivity + external service. Not validated parallel.",
        "notes": "Serialized.",
    },
    # Device integration — PnP device inventory subprocess, not validated for parallelism.
    "device_integration": {
        "class": SERIAL_ONLY,
        "stage": "STAGE_0_SERIALIZED",
        "evidence": "Installed-device inventory via a PowerShell subprocess (Get-PnpDevice). Integration surface is largely demonstrational; not validated for parallelism.",
        "notes": "Serialized until device/home concurrency is proven.",
    },
    # Voice (STT/TTS) — hardware + model, no parallel validation.
    "voice": {
        "class": SERIAL_ONLY,
        "stage": "STAGE_0_SERIALIZED",
        "evidence": "Local VAD/STT, requires_configuration, hardware bound. Not validated.",
        "notes": "Serialized.",
    },
    # Tool authoring proposals — inert artifacts but writes to proposed_* tables.
    "self_authoring_tools": {
        "class": SERIAL_ONLY,
        "stage": "STAGE_0_SERIALIZED",
        "evidence": "Creates proposed_tools/proposed_skills rows; validation only. DB writes serialized; not proven parallel-safe.",
        "notes": "Serialized until written-concurrency proven.",
    },
}

# Derived helpers

def concurrency_class(capability_id: str) -> str:
    entry = CAPABILITY_CONCURRENCY.get(capability_id)
    if entry is None:
        return UNKNOWN
    return entry["class"]

def default_unsafe_to_serial(capability_id: str) -> bool:
    """Unknown capabilities must default to serialized."""
    return concurrency_class(capability_id) in (SERIAL_ONLY, UNKNOWN)

def to_json_table() -> Dict[str, Dict[str, str]]:
    return dict(CAPABILITY_CONCURRENCY)
