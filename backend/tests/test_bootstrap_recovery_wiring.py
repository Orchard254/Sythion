"""Startup bootstrap must invoke the three production recovery functions.

``TaskOrchestrator.recover_all`` scans plan steps only. Delegation, plan and
specialist-TTL recovery live in separate services and must be wired into the
``task_recovery`` stage by bootstrap itself — best-effort, non-fatal, before
agent init / wake recovery. These tests lock that wiring in.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from backend.core.db_manager import init_database


EXPECTED_ORDER = [
    "task_recovery",
    "goal_migration",
    "agent_init",
    "wake_recovery",
]


def _db(tmp_path):
    return init_database(str(tmp_path / "bootstrap-recovery-wiring.db"))


def _recording_service(method, result=None, error=None, events=None,
                       event_name=None):
    """Build a fake service whose ``method`` records the session it got."""
    calls = []

    class _Fake:
        def __init__(self, session):
            self.session = session

        def _run(self, *args, **kwargs):
            calls.append((self.session, args, kwargs))
            if events is not None:
                events.append(event_name)
            if error is not None:
                raise error
            return result

    setattr(_Fake, method, _Fake._run)
    return _Fake, calls


def _patch_recoveries(monkeypatch, events, *, delegation=None, plan=None,
                      agents=None):
    """Patch the three service classes; each arg is ``(result, error)``."""
    def _spec(pair, name):
        result, error = pair if pair is not None else (None, None)
        return _recording_service(
            {"delegation": "recover_delegations",
             "plan": "recover_plans",
             "agents": "expire_due"}[name],
            result=result, error=error, events=events, event_name=name,
        )

    FakeDelegation, delegation_calls = _spec(delegation, "delegation")
    FakePlan, plan_calls = _spec(plan, "plan")
    FakeAgents, agent_calls = _spec(agents, "agents")

    monkeypatch.setattr("backend.agents.delegation.DelegationService",
                        FakeDelegation)
    monkeypatch.setattr("backend.agents.coordination.DelegationPlanService",
                        FakePlan)
    monkeypatch.setattr("backend.agents.service.AgentService", FakeAgents)
    return delegation_calls, plan_calls, agent_calls


def test_task_recovery_stage_invokes_all_three_recoveries(monkeypatch, tmp_path):
    events = []
    delegation_calls, plan_calls, agent_calls = _patch_recoveries(
        monkeypatch, events,
        delegation=([{"delegation_id": "d1", "resolved": True}], None),
        plan=({"recovered": 2}, None),
        agents=(3, None),
    )

    # Track every session handed out by the app's db_manager.
    db_manager = _db(tmp_path)
    handed_out = []
    real_new_session = db_manager.new_session

    def _tracking_new_session():
        session = real_new_session()
        handed_out.append(session)
        return session

    monkeypatch.setattr(db_manager, "new_session", _tracking_new_session)

    from backend.services.bootstrap import run_startup_sequence

    try:
        boot = run_startup_sequence(db_manager)
    finally:
        db_manager.engine.dispose()

    # All three were called exactly once, with an app DB session.
    for calls in (delegation_calls, plan_calls, agent_calls):
        assert len(calls) == 1
        session, args, kwargs = calls[0]
        assert isinstance(session, Session)
        assert any(session is handed for handed in handed_out)
        assert args == () and kwargs == {}

    # Their outputs are surfaced on the boot payload for observability.
    assert boot["delegation_recovery"] == [
        {"delegation_id": "d1", "resolved": True}
    ]
    assert boot["plan_recovery"] == {"recovered": 2}
    assert boot["specialist_expiry"] == 3

    # Stage set and ordering are unchanged; startup fully succeeded.
    assert boot["order"] == EXPECTED_ORDER
    assert set(boot["stage_statuses"]) == set(EXPECTED_ORDER)
    assert boot["stage_statuses"]["task_recovery"] == "completed"
    assert boot["startup_sequence_status"] == "completed"
    assert len(boot["stages"]) == 4


@pytest.mark.parametrize("failing", ["delegation", "plan", "agents"])
def test_one_recovery_failure_is_non_fatal(monkeypatch, tmp_path, failing):
    events = []
    failure = RuntimeError(f"injected {failing} recovery failure")
    pairs = {
        "delegation": ([{"delegation_id": "ok"}], None),
        "plan": ({"recovered": 1}, None),
        "agents": (1, None),
    }
    pairs[failing] = (None, failure)
    delegation_calls, plan_calls, agent_calls = _patch_recoveries(
        monkeypatch, events,
        delegation=pairs["delegation"], plan=pairs["plan"], agents=pairs["agents"],
    )

    db_manager = _db(tmp_path)
    from backend.services.bootstrap import run_startup_sequence

    try:
        # Must return normally — no recovery failure may raise out of startup.
        boot = run_startup_sequence(db_manager)
    finally:
        db_manager.engine.dispose()

    # The failure is truthfully recorded on the task_recovery stage.
    assert boot["stage_statuses"]["task_recovery"] == "failed"
    stage = next(s for s in boot["stages"] if s["stage"] == "task_recovery")
    assert str(failure) in stage["error"]

    # Best-effort: the other two recoveries still ran despite the failure.
    all_calls = {
        "delegation": delegation_calls,
        "plan": plan_calls,
        "agents": agent_calls,
    }
    for name, calls in all_calls.items():
        assert len(calls) == 1, f"{name} recovery was skipped"

    # Failed recovery falls back to a safe empty/default value.
    defaults = {"delegation": [], "plan": {"recovered": 0}, "agents": 0}
    assert boot[{"delegation": "delegation_recovery",
                 "plan": "plan_recovery",
                 "agents": "specialist_expiry"}[failing]] == defaults[failing]

    # The ordered sequence continued: every later stage still ran.
    assert boot["order"] == EXPECTED_ORDER
    assert set(boot["stage_statuses"]) == set(EXPECTED_ORDER)
    for stage_name in ("goal_migration", "agent_init", "wake_recovery"):
        assert boot["stage_statuses"][stage_name] == "completed"
    assert boot["startup_sequence_status"] == "failed"


def test_recoveries_run_before_agent_init_and_wake_recovery(
        monkeypatch, tmp_path):
    events = []
    _patch_recoveries(monkeypatch, events)

    class _FakeMainAgentService:
        def __init__(self, session):
            self.session = session

        def get_or_create(self):
            events.append("agent_init")
            return SimpleNamespace(agent_id="main-agent-1", state="READY")

        def recover_pending_wakes(self, boot_time):
            events.append("wake_recovery")
            return {"reclaimed": 0, "failed_reclaims": 0, "pending": 0}

    monkeypatch.setattr("backend.services.main_agent.MainAgentService",
                        _FakeMainAgentService)

    db_manager = _db(tmp_path)
    from backend.services.bootstrap import run_startup_sequence

    try:
        boot = run_startup_sequence(db_manager)
    finally:
        db_manager.engine.dispose()

    assert events == [
        "delegation", "plan", "agents", "agent_init", "wake_recovery",
    ]
    assert boot["order"] == EXPECTED_ORDER
