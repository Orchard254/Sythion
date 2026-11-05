"""Same-task containment only: fake effects, private DELETE/WAL SQLite, no I/O tools."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from backend.models.database import Task, TaskEvent
from backend.api.schemas import TaskState
from backend.runtime.task_ownership import TaskOwnershipManager, TaskOwnershipRow
from backend.services.task_runner import TaskRunner
from backend.services.model_engine import DecisionType
from backend.tools.executor import ToolOutput
from backend.tests.test_followup_worker_race_probe import sessions, seed, worker


PARAMS = {"sandbox_resource": "same deliberate operation"}


class FakeEffect:
    def __init__(self):
        self.calls = 0

    def list_tools(self):
        return []

    def get_tool(self, name):
        return SimpleNamespace(get_policy_requirements=lambda: {"mutating": True})

    async def execute_tool(self, **kwargs):
        self.calls += 1
        return ToolOutput(success=True, data={"fake_effect": self.calls}, model_summary="fake only")


async def _owned(db):
    await seed(db)
    runner = TaskRunner(db)
    assert (await runner.acquire_task_ownership("original", worker_id="owner")).acquired
    return runner


def _pending(db, runner, action_id="fresh"):
    payload = {"kind": "permission", "status": "approved", "action_id": action_id,
               "tool_name": "fake-effect", "params": PARAMS}
    db.get(Task, "original").pending_action = json.dumps(payload)
    runner.ledger.append_event("original", "action.pending_approval", payload)
    runner.ledger.append_event("original", "action.approved", {"action_id": action_id})
    db.commit()  # isolated staging evidence, NOT a claimed production consent path
    return json.dumps(payload)


def _loop(db, runner, fake):
    loop = worker(db, SimpleNamespace(), fake)
    loop.runner = runner
    loop.cancellation._state_store = runner
    loop._ownership_lost = False
    return loop


def _snapshot(sessions):
    with sessions() as db:
        task = db.get(Task, "original")
        row = db.get(TaskOwnershipRow, "original")
        return (
            task.state, task.version, task.pending_action, task.active_action_id,
            {c.name: getattr(row, c.name) for c in row.__table__.columns},
            [(e.sequence_num, e.event_type, e.event_data) for e in
             db.query(TaskEvent).filter_by(task_id="original").order_by(TaskEvent.sequence_num)],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", ["release", "force"])
@pytest.mark.parametrize("approved", [False, True])
async def test_recovered_uncertainty_refuses_fresh_ids_without_effect_or_permission_consumption(
    sessions, monkeypatch, recovery, approved
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    with sessions() as old_db:
        old = await _owned(old_db)
        await old.start_mutating_action("original", "old-action", "fake-effect", PARAMS)
        if recovery == "release":
            assert await old.release_task_ownership("original") is True
        else:
            with sessions() as db:
                manager = TaskOwnershipManager(db)
                recovered = manager.recover("original", "recovered", "fake-only recovery", force=True)
                assert recovered.acquired and recovered.uncertain_action_ids == ["old-action"]
                assert manager.release("original", recovered.nonce)
                db.commit()
        with sessions() as db:
            replacement = TaskRunner(db)
            assert (await replacement.acquire_task_ownership("original", worker_id="replacement")).acquired
            if approved:
                _pending(db, replacement)
            before = _snapshot(sessions)
            fake = FakeEffect()
            loop = _loop(db, replacement, fake)
            for _ in range(2):
                with pytest.raises(RuntimeError, match="unresolved|uncertain|already"):
                    await loop._execute_tool("original", {"tool_name": "fake-effect", "params": PARAMS},
                                             approved_by_user=approved, action_id="fresh")
                assert fake.calls == 0
                assert _snapshot(sessions) == before
                assert not db.connection().connection.dbapi_connection.in_transaction
        # A late result from the original producer cannot clear the old evidence.
        with pytest.raises(RuntimeError):
            await old.finish_mutating_action("original", "old-action", {"success": True})
        assert _snapshot(sessions) == before


def _evidence(db, runner, source):
    row = db.get(TaskOwnershipRow, "original")
    if source == "task_active":
        db.get(Task, "original").active_action_id = "old"
    elif source == "task_empty":
        db.get(Task, "original").active_action_id = ""
    elif source == "owner_active":
        row.active_action_id = "old"
    elif source == "owner_empty":
        row.active_action_id = ""
    elif source.startswith("json:"):
        row.uncertain_action_ids = source[5:]
    elif source == "started":
        runner.ledger.append_event("original", "action.started", {"action_id": "old", "tool_name": "fake-effect"})
    elif source == "approved_legacy":
        runner.ledger.append_event("original", "action.approved", {"decision": "approved"})
    elif source in {"handoff", "uncertain", "interrupted"}:
        kind = {"handoff": "action.handoff_unresolved", "uncertain": "action.uncertain",
                "interrupted": "task.interrupted"}[source]
        runner.ledger.append_event("original", kind, {})
    elif source == "empty_result":
        runner.ledger.append_event("original", "tool.executed", {})
    elif source == "null_result":
        runner.ledger.append_event("original", "tool.executed", None)
    elif source.startswith("malformed:"):
        runner.ledger.append_event("original", source[10:], ["malformed action evidence"])
    elif source == "failed_result":
        runner.ledger.append_event("original", "tool.executed", {
            "action_id": "old", "tool_name": "fake-effect", "mutating": True,
            "success": False, "verification": "failed", "params": PARAMS})
    elif source in {"duplicate_starts", "duplicate_results"}:
        for _ in range(2 if source == "duplicate_starts" else 1):
            runner.ledger.append_event("original", "action.started", {"action_id": "old", "tool_name": "fake-effect"})
        for _ in range(2 if source == "duplicate_results" else 1):
            runner.ledger.append_event("original", "tool.executed", {
                "action_id": "old", "tool_name": "fake-effect", "mutating": True,
                "success": True, "verification": "passed", "params": PARAMS})
    else:
        assert source == "unrelated_success"
        runner.ledger.append_event("original", "action.started", {"action_id": "old", "tool_name": "fake-effect"})
        runner.ledger.append_event("original", "tool.executed", {
            "action_id": "unrelated", "tool_name": "fake-effect", "mutating": True,
            "success": True, "verification": "passed", "params": PARAMS})
    db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [
    "task_active", "task_empty", "owner_active", "owner_empty", 'json:["old"]', "json:", "json:{broken", "json:{}", "json:null",
    'json:""', "json:[null]", "started", "approved_legacy", "handoff", "uncertain", "interrupted",
    "malformed:action.started", "malformed:tool.executed", "empty_result", "null_result",
    "failed_result", "unrelated_success", "duplicate_starts", "duplicate_results",
])
async def test_every_durable_evidence_source_blocks_before_consuming_current_permission(sessions, source):
    with sessions() as db:
        runner = await _owned(db)
        _evidence(db, runner, source)
        pending = _pending(db, runner)
        before = _snapshot(sessions)
        with pytest.raises(RuntimeError):
            await runner.start_mutating_action("original", "fresh", "fake-effect", PARAMS, approved=True)
        assert _snapshot(sessions) == before
        assert db.get(Task, "original").pending_action == pending
        assert not db.connection().connection.dbapi_connection.in_transaction


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["task_active", 'json:["old"]', "handoff", "approved_legacy"])
async def test_worker_refuses_to_stage_permission_over_unresolved_evidence(sessions, monkeypatch, source):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    with sessions() as db:
        runner = await _owned(db)
        _evidence(db, runner, source)
        before = _snapshot(sessions)
        fake = FakeEffect()
        fake.get_tool = lambda name: SimpleNamespace(get_policy_requirements=lambda: {
            "mutating": True, "requires_confirmation": True})
        with pytest.raises(RuntimeError):
            await _loop(db, runner, fake)._execute_tool("original", {"tool_name": "fake-effect", "params": PARAMS})
        assert fake.calls == 0 and _snapshot(sessions) == before
        assert not db.connection().connection.dbapi_connection.in_transaction


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ['json:["other"]', "handoff"])
async def test_worker_rechecks_uncertainty_after_initial_handoff_before_effect(sessions, monkeypatch, source):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    with sessions() as db:
        runner = await _owned(db)
        fake = FakeEffect()
        real_begin = runner.begin_task_action
        calls = 0

        async def add_evidence_at_dispatch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                with sessions() as other:
                    _evidence(other, TaskRunner(other), source)
            return await real_begin(*args, **kwargs)

        monkeypatch.setattr(runner, "begin_task_action", add_evidence_at_dispatch)
        assert await _loop(db, runner, fake)._execute_tool(
            "original", {"tool_name": "fake-effect", "params": PARAMS}, action_id="current") is False
        assert fake.calls == 0 and calls == 2
        with sessions() as observer:
            assert observer.get(Task, "original").active_action_id == "current"
            assert observer.query(TaskEvent).filter_by(event_type="action.started").count() == 1
            assert observer.query(TaskEvent).filter_by(event_type="tool.executed").count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "[]", '["old"]', "", "not-json", "{}", "null", '""', "[null]"])
async def test_ownership_primitive_checks_all_uncertainty_and_preserves_caller_transaction(sessions, raw):
    with sessions() as db:
        runner = await _owned(db)
        db.get(TaskOwnershipRow, "original").uncertain_action_ids = raw
        db.commit()
        db.execute(text("BEGIN IMMEDIATE"))
        db.execute(text("INSERT INTO tasks (id, original_request, state, version) VALUES ('caller','kept','created',1)"))
        outer = db.get_transaction()
        before = _snapshot(sessions)
        started = runner.task_ownership.begin_action("original", runner.task_ownership_nonce, "fresh")
        assert started is (raw in (None, "[]"))
        assert db.get_transaction() is outer and outer.is_active
        assert db.connection().connection.dbapi_connection.in_transaction
        assert db.get(Task, "caller") is not None
        assert _snapshot(sessions) == before  # primitive never commits, including success
        db.rollback()
        assert db.get(Task, "caller") is None


@pytest.mark.asyncio
async def test_normal_actions_allow_distinct_ids_same_params_but_never_dispatch_one_id_twice(sessions, monkeypatch):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    with sessions() as db:
        runner = await _owned(db)
        # A non-action audit payload is not unknown external-action evidence.
        runner.ledger.append_event("original", "task.note", ["unrelated audit payload"])
        db.commit()
        fake = FakeEffect()
        loop = _loop(db, runner, fake)
        for action in ["one", "two"]:
            assert await loop._execute_tool("original", {"tool_name": "fake-effect", "params": PARAMS}, action_id=action)
        assert fake.calls == 2
        before = _snapshot(sessions)
        with pytest.raises(RuntimeError, match="already|admitted|replay"):
            await loop._execute_tool("original", {"tool_name": "fake-effect", "params": PARAMS}, action_id="one")
        assert fake.calls == 2 and _snapshot(sessions) == before
        assert db.query(TaskEvent).filter_by(event_type="action.started").count() == 2
        assert db.query(TaskEvent).filter_by(event_type="tool.executed").count() == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "fence", "expiry", "uncertain", "history", "stop"])
async def test_exact_internal_handoff_is_fenced_and_cannot_restart_dispatch(sessions, monkeypatch, change):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    with sessions() as db:
        runner = await _owned(db)
        await runner.start_mutating_action("original", "current", "fake-effect", PARAMS)
        if change == "fence":
            runner._task_ownership_fence += 1
        elif change == "expiry":
            db.get(TaskOwnershipRow, "original").expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        elif change == "uncertain":
            db.get(TaskOwnershipRow, "original").uncertain_action_ids = '["other"]'
            db.commit()
        elif change == "history":
            runner.ledger.append_event("original", "action.handoff_unresolved", {"action_id": "other"})
            db.commit()
        elif change == "stop":
            with sessions() as stop_db:
                await TaskRunner(stop_db).force_cancel("original")
        before = _snapshot(sessions)
        assert await runner.begin_task_action("original", "current") is (change == "none")
        assert _snapshot(sessions) == before
        assert await runner.begin_task_action("original", "different") is False
        assert _snapshot(sessions) == before
        if change == "none":
            fake = FakeEffect()
            with pytest.raises(RuntimeError):
                await _loop(db, runner, fake)._execute_tool(
                    "original", {"tool_name": "fake-effect", "params": PARAMS}, action_id="current")
            assert fake.calls == 0 and _snapshot(sessions) == before


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Legacy synthetic approval: approved_by_user dispatch is disabled by the "
        "fail-closed independent human-consent policy, so forged approvals do not execute."
    ),
)
@pytest.mark.asyncio
async def test_clean_permission_staging_and_exact_approved_handoff_still_execute_once(sessions, monkeypatch):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    with sessions() as db:
        runner = await _owned(db)
        fake = FakeEffect()
        fake.get_tool = lambda name: SimpleNamespace(get_policy_requirements=lambda: {
            "mutating": True, "requires_confirmation": True})
        loop = _loop(db, runner, fake)
        assert await loop._execute_tool("original", {"tool_name": "fake-effect", "params": PARAMS}) is False
        task = db.get(Task, "original")
        pending = json.loads(task.pending_action)
        assert task.state == "waiting_for_user" and pending["status"] == "awaiting"
        assert fake.calls == 0 and db.query(TaskEvent).filter_by(event_type="action.started").count() == 0
        # Isolated approval evidence under the retained current owner. This
        # tests handoff preservation, not a production UI/consent integration.
        await runner.set_pending_action_status("original", "approved")
        await runner.transition_state("original", TaskState.RUNNING, "action.approved",
                                      {"action_id": pending["action_id"]})
        assert await loop._execute_tool("original", {"tool_name": "fake-effect", "params": PARAMS},
                                       approved_by_user=True, action_id=pending["action_id"]) is True
        assert fake.calls == 1
        assert db.get(Task, "original").pending_action is None
        assert db.get(Task, "original").active_action_id is None
        assert db.query(TaskEvent).filter_by(event_type="action.started").count() == 1
        assert db.query(TaskEvent).filter_by(event_type="tool.executed").count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["legacy", "current_owner", "old_owner"])
async def test_task_only_start_marker_needs_exact_durable_owner_binding_for_internal_begin(sessions, binding):
    with sessions() as db:
        runner = await _owned(db)
        if binding == "current_owner":
            await runner.record_action_started("original", "current", "fake-effect", [])
        else:
            db.get(Task, "original").active_action_id = "current"
            data = {"action_id": "current", "tool_name": "fake-effect"}
            if binding == "old_owner":
                data.update(owner_nonce="not-this-owner", fencing_token=runner._task_ownership_fence)
            runner.ledger.append_event("original", "action.started", data)
            db.commit()
        before = _snapshot(sessions)
        assert await runner.begin_task_action("original", "current") is (binding == "current_owner")
        if binding != "current_owner":
            assert _snapshot(sessions) == before
        else:
            assert db.get(TaskOwnershipRow, "original").active_action_id == "current"
            assert db.query(TaskEvent).filter_by(event_type="action.started").count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["uncertainty", "stop"])
async def test_admission_reloads_cached_state_after_actual_writer_boundary(sessions, monkeypatch, winner):
    with sessions() as db:
        runner = await _owned(db)
        pending = _pending(db, runner)
        cached_task = db.get(Task, "original")
        cached_owner = db.get(TaskOwnershipRow, "original")
        assert cached_task.state == "running" and cached_owner.uncertain_action_ids is None
        real_ensure = runner._ensure_transaction
        paused = False

        def winner_commits():
            async def run():
                with sessions() as other:
                    if winner == "stop":
                        await TaskRunner(other).force_cancel("original")
                    else:
                        other.execute(text("BEGIN IMMEDIATE"))
                        other.get(TaskOwnershipRow, "original").uncertain_action_ids = '["committed-first"]'
                        other.commit()
            asyncio.run(run())

        def before_lock():
            nonlocal paused
            if not paused:
                paused = True
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(winner_commits).result(timeout=10)
            return real_ensure()

        monkeypatch.setattr(runner, "_ensure_transaction", before_lock)
        with pytest.raises(RuntimeError):
            await runner.start_mutating_action("original", "fresh", "fake-effect", PARAMS, approved=True)
        with sessions() as observer:
            task = observer.get(Task, "original")
            assert task.pending_action == pending
            assert task.state == ("cancelled" if winner == "stop" else "running")
            assert observer.query(TaskEvent).filter_by(event_type="action.started").count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_full_recovered_worker_cannot_dispatch_fresh_mutation_or_replay(sessions, monkeypatch, approved):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    decisions = []

    class Model:
        async def make_decision(self, **kwargs):
            decisions.append(True)
            if len(decisions) == 1:
                return DecisionType.EXECUTE_TOOL, {"tool_name": "fake-effect", "params": PARAMS}
            assert len(decisions) == 2
            return DecisionType.FAIL_TASK, {"code": "FAKE_REVIEW_REQUIRED", "reason": "retained uncertainty"}

        def pop_pending_tool_calls(self):
            return []

    with sessions() as db:
        old = await _owned(db)
        await old.start_mutating_action("original", "old", "fake-effect", PARAMS)
        assert await old.release_task_ownership("original")
        pending = _pending(db, old) if approved else None
        fake = FakeEffect()
        loop = worker(db, Model(), fake)
        if approved:
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(loop.execute_task("original"), 10)
            assert decisions == []
        else:
            await asyncio.wait_for(loop.execute_task("original"), 10)
            assert len(decisions) == 2
        assert fake.calls == 0
        with pytest.raises(RuntimeError, match="Cannot execute terminal task"):
            await asyncio.wait_for(worker(db, Model(), fake).execute_task("original"), 10)
        assert fake.calls == 0
    with sessions() as db:
        task = db.get(Task, "original")
        assert task.state == "failed" and task.active_action_id == "old"
        assert task.pending_action == pending
        assert json.loads(db.get(TaskOwnershipRow, "original").uncertain_action_ids) == ["old"]
        assert db.query(TaskEvent).filter_by(event_type="action.started").count() == 1
        assert db.query(TaskEvent).filter_by(event_type="tool.executed").count() == 0
        assert db.query(TaskEvent).filter_by(event_type="task.completed").count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("read", ["events", "ownership"])
async def test_unavailable_evidence_refuses_without_consuming_permission(sessions, monkeypatch, read):
    with sessions() as db:
        runner = await _owned(db)
        _pending(db, runner)
        before = _snapshot(sessions)

        def fail_read(*args, **kwargs):
            raise RuntimeError("injected admission evidence unavailable")

        with monkeypatch.context() as scoped:
            scoped.setattr(runner.ledger if read == "events" else TaskOwnershipManager,
                           "event_history" if read == "events" else "get", fail_read)
            with pytest.raises(RuntimeError, match="evidence unavailable"):
                await runner.start_mutating_action("original", "fresh", "fake-effect", PARAMS, approved=True)
        assert _snapshot(sessions) == before
        assert not db.connection().connection.dbapi_connection.in_transaction


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Legacy synthetic approval: approved_by_user dispatch is disabled by the "
        "fail-closed independent human-consent policy, so commit-error admission "
        "windows for forged approvals are unreachable."
    ),
)
@pytest.mark.asyncio
@pytest.mark.parametrize("commit_phase", ["before", "after"])
async def test_start_commit_exception_never_dispatches_and_durable_start_cannot_replay(sessions, monkeypatch, commit_phase):
    with sessions() as db:
        runner = await _owned(db)
        pending = _pending(db, runner)
        fake = FakeEffect()
        loop = _loop(db, runner, fake)
        real_commit = db.commit

        def ambiguous_commit():
            if commit_phase == "after":
                real_commit()
            raise RuntimeError("injected admission commit exception")

        with monkeypatch.context() as scoped:
            scoped.setattr(db, "commit", ambiguous_commit)
            with pytest.raises(RuntimeError, match="injected admission"):
                await loop._execute_tool("original", {"tool_name": "fake-effect", "params": PARAMS},
                                         approved_by_user=True, action_id="fresh")
        assert fake.calls == 0
        before_retry = _snapshot(sessions)
        with sessions() as observer:
            task = observer.get(Task, "original")
            assert task.pending_action == (pending if commit_phase == "before" else None)
            assert observer.query(TaskEvent).filter_by(event_type="action.started").count() == (commit_phase == "after")
            assert observer.query(TaskEvent).filter_by(event_type="tool.executed").count() == 0
        if commit_phase == "after":
            for action in ["fresh", "different"]:
                with pytest.raises(RuntimeError):
                    await loop._execute_tool("original", {"tool_name": "fake-effect", "params": PARAMS}, action_id=action)
                assert fake.calls == 0 and _snapshot(sessions) == before_retry


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["raw", "autoflush", "nested"])
@pytest.mark.parametrize("api", ["start", "begin", "stage"])
@pytest.mark.parametrize("finish", ["commit", "rollback"])
async def test_admission_refusal_preserves_caller_work_and_pending_permission(sessions, monkeypatch, kind, api, finish):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    with sessions() as db:
        runner = await _owned(db)
        pending = _pending(db, runner)
        before = _snapshot(sessions)
        if kind == "autoflush":
            staged = Task(id="caller", original_request="kept", state="created", version=1)
            db.add(staged)
            assert db.execute(select(Task).where(Task.id == "caller")).scalar_one() is staged
            assert not db.new and not db.dirty
        else:
            db.execute(text("BEGIN IMMEDIATE"))
            db.execute(text("INSERT INTO tasks (id, original_request, state, version) VALUES ('caller','kept','created',1)"))
        outer = db.get_transaction()
        nested = db.begin_nested() if kind == "nested" else None
        if nested:
            db.execute(text("INSERT INTO tasks (id, original_request, state, version) VALUES ('savepoint','kept','created',1)"))
        connection = db.connection().connection.dbapi_connection
        with monkeypatch.context() as scoped:
            scoped.setattr(db, "commit", lambda: pytest.fail("admission committed caller work"))
            scoped.setattr(db, "rollback", lambda: pytest.fail("admission rolled back caller work"))
            with pytest.raises(RuntimeError, match="caller-owned"):
                if api == "start":
                    await runner.start_mutating_action("original", "fresh", "fake-effect", PARAMS, approved=True)
                elif api == "begin":
                    await runner.begin_task_action("original", "fresh")
                else:
                    fake = FakeEffect()
                    fake.get_tool = lambda name: SimpleNamespace(get_policy_requirements=lambda: {
                        "mutating": True, "requires_confirmation": True})
                    await _loop(db, runner, fake)._execute_tool(
                        "original", {"tool_name": "fake-effect", "params": PARAMS})
        assert db.get_transaction() is outer and outer.is_active and connection.in_transaction
        assert db.get_nested_transaction() is nested
        assert db.get(Task, "caller") is not None
        assert db.get(Task, "original").pending_action == pending
        if nested:
            assert nested.is_active and db.get(Task, "savepoint") is not None
            nested.rollback()
            assert db.get(Task, "savepoint") is None and db.get(Task, "caller") is not None
        assert _snapshot(sessions) == before
        getattr(db, finish)()
        assert not connection.in_transaction
    with sessions() as observer:
        assert (observer.get(Task, "caller") is not None) is (finish == "commit")
        assert observer.get(Task, "savepoint") is None
        assert _snapshot(sessions) == before


@pytest.mark.asyncio
async def test_forged_approved_by_user_never_dispatches_and_uncertainty_remains(sessions):
    """A forged approved_by_user dispatch must not run and cannot clear uncertainty."""
    with sessions() as db:
        runner = await _owned(db)
        pending_raw = _pending(db, runner)
        pending = json.loads(pending_raw)
        before = _snapshot(sessions)
        fake = FakeEffect()
        loop = _loop(db, runner, fake)

        result = await loop._execute_tool(
            "original",
            {"tool_name": "fake-effect", "params": PARAMS},
            approved_by_user=True,
            action_id=pending["action_id"],
        )

        assert result is False
        assert fake.calls == 0
        assert _snapshot(sessions) == before

        task = db.get(Task, "original")
        assert task.pending_action is not None
        assert json.loads(task.pending_action)["status"] == "approved"
        assert db.query(TaskEvent).filter_by(event_type="tool.executed").count() == 0

        from backend.services.execution_components import CompletionVerifier

        assert CompletionVerifier(db, runner.ledger.event_history).precondition_error(
            "original"
        ) is not None
