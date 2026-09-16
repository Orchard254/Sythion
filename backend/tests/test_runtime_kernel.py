"""
Phase 1 kernel interface tests.

Proves the runtime interfaces exist, are implemented by the protected
runtime, and that the reasoning layer goes through them (no direct adapter
coupling).
"""

import asyncio

import pytest

from backend.runtime import (
    DbEventLedger,
    EventLedger,
    LeaseManager as LeaseManagerProtocol,
    ModelClient,
    TaskStateStore,
)
from backend.runtime.events import DbEventLedger as DbEventLedgerImpl
from backend.runtime.model_client import OllamaCloudModelClient
from backend.services.task_runner import TaskRunner


# --- Protocol conformance -------------------------------------------------------


def test_db_event_ledger_implements_event_ledger_protocol(test_db):
    assert isinstance(DbEventLedgerImpl(test_db), EventLedger)


def test_task_runner_implements_state_store_and_lease_protocols(test_db):
    runner = TaskRunner(test_db)
    assert isinstance(runner, TaskStateStore)
    assert isinstance(runner._leases, LeaseManagerProtocol)


def test_ollama_client_implements_model_client_protocol():
    from backend.adapters.ollama_adapter import get_adapter

    client = OllamaCloudModelClient()
    assert isinstance(client, ModelClient)
    # Backed by the existing adapter singleton; no second provider exists.
    assert client.provider_name() == "ollama-cloud"
    assert client._adapter is get_adapter()


# --- Model boundary --------------------------------------------------------------


class _FakeModelClient:
    """Records the decision request; returns a canned complete_task call."""

    def __init__(self):
        self.calls = []

    async def decide(self, messages, tools, max_tokens=4000):
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        return {
            "message": {
                "tool_calls": [
                    {"function": {"name": "complete_task", "arguments": {"result": "hi"}}}
                ]
            }
        }

    def provider_name(self):
        return "fake"


@pytest.mark.asyncio
async def test_decision_engine_uses_injected_model_client(test_db):
    from backend.services.model_engine import DecisionType, ModelDecisionEngine
    from backend.tools.executor import ToolExecutor

    fake = _FakeModelClient()
    engine = ModelDecisionEngine(test_db, ToolExecutor(), model_client=fake)

    # Bypass the real context builder; the boundary under test is decide().
    engine._build_context = lambda **kwargs: []

    decision_type, params = await engine.make_decision(
        task_id="t",
        original_request="hello",
        normalized_goal=None,
        event_history=[],
        current_state="running",
    )

    assert decision_type == DecisionType.COMPLETE_TASK
    assert params["result"] == "hi"
    assert len(fake.calls) == 1
    assert fake.calls[0]["max_tokens"] == ModelDecisionEngine.MAX_TOKENS_PER_REQUEST


# --- Ledger behavior ---------------------------------------------------------------


def test_event_ledger_append_read_and_sequence(test_db):
    ledger = DbEventLedger(test_db)

    seq1 = ledger.append_event("t1", "task.created", {"ok": 1})
    seq2 = ledger.append_event("t1", "tool.executed", {"ok": 2})
    test_db.commit()

    assert (seq1, seq2) == (1, 2)
    assert ledger.latest_sequence("t1") == 2
    assert ledger.latest_sequence("t-unknown") == 0

    history = ledger.event_history("t1")
    assert [e["event_type"] for e in history] == ["task.created", "tool.executed"]
    assert [e["sequence_num"] for e in ledger.events_after("t1", 1)] == [2]


def test_event_ledger_decodes_malformed_payloads(test_db):
    from backend.models.database import TaskEvent
    import json as _json

    ledger = DbEventLedger(test_db)
    ledger.append_event("t1", "evt.ok", {"fine": True})
    test_db.flush()
    # Simulate legacy malformed stored JSON (Windows backslash paths).
    row = test_db.query(TaskEvent).filter_by(task_id="t1").first()
    row.event_data = "C:\\Users\\someone\\file"
    test_db.commit()

    history = ledger.event_history("t1")
    decoded_ok = [e for e in history if e["event_type"] == "evt.ok"]
    assert decoded_ok[0]["event_data"] == {"raw": "C:\\Users\\someone\\file"}
