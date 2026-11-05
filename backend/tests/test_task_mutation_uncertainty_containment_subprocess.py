"""Actual separate-process containment for same-task uncertainty.

These tests spawn fresh interpreters that share only a private SQLite file. They
use a fake tool whose effect counter can be observed from the child, and they
never touch real tools, files, network, profile, vault, or any product DB.

What is intentionally NOT claimed here:

* Cross-profile containment is not proven: separate DB files are trivially
  independent and could not test shared containment.
* OSActionGuard is a process-local guard. Its cross-process behaviour remains
  unsupported; only the durable ownership/runner admission path is exercised.
* There is no parameter deduplication. Identical parameters are repeated across
  attempts to prove that admission is keyed to unresolved evidence, not params.
* No auto-clear is exercised or expected: after both attempts uncertainty is
  still exactly ["old-action"].
* Before/after admission commit ambiguity is a single-process transaction
  property; it is covered by the existing in-process tests and is not
  reliably reproducible through spawned children.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

SYTHION_ROOT = Path(__file__).resolve().parents[2]
TEST_FILE = Path(__file__).resolve()


def _child_env():
    env = os.environ.copy()
    for key in list(env):
        if key.upper().startswith("SYTHION_"):
            env.pop(key, None)
    env["SYTHION_AUTONOMY_LEVEL"] = "autonomous"
    env["PYTHONPATH"] = str(SYTHION_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _spawn_child(mode, db_path, journal_mode):
    return subprocess.Popen(
        [sys.executable, str(TEST_FILE), "child", mode, db_path, journal_mode],
        cwd=str(SYTHION_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_child_env(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _readline(proc, timeout=90):
    import threading

    result = {}

    def _read():
        result["line"] = proc.stdout.readline()

    thread = threading.Thread(target=_read, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        proc.kill()
        proc.wait(timeout=10)
        raise AssertionError("child timed out before producing output")
    line = result.get("line", "")
    if not line:
        try:
            err = proc.stderr.read() or ""
        except Exception:
            err = ""
        raise AssertionError(f"child exited early:\n{str(err)[-3000:]}")
    return line.strip()


def _terminate(proc):
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    for stream in (proc.stdout, proc.stderr):
        try:
            if stream:
                stream.close()
        except Exception:
            pass


PARAMS = {"sandbox_resource": "same deliberate operation"}
OLD_ACTION = "old-action"


CHILD_CODE = r'''
import asyncio
import json
import os
import sys
import time
from pathlib import Path

PROJECT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, PROJECT)
os.environ["SYTHION_AUTONOMY_LEVEL"] = "autonomous"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task, TaskEvent
from backend.runtime.task_ownership import TaskOwnershipManager
from backend.services.task_runner import TaskRunner
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.tools.executor import ToolOutput
from types import SimpleNamespace


def _connect(db_path, journal_mode):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.connect() as connection:
        actual = connection.exec_driver_sql(
            f"PRAGMA journal_mode={journal_mode}"
        ).scalar()
        assert str(actual).upper() == journal_mode.upper()
        connection.commit()
    Base.metadata.create_all(engine)
    return engine


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


def _seed(session):
    session.add(Task(id="original", original_request="Make a study plan", state="running", version=2))
    session.commit()


def _task_count(session):
    return session.query(Task).filter_by(id="original").count()


def _event_count(session, event_type):
    return session.query(TaskEvent).filter_by(task_id="original", event_type=event_type).count()


def main(mode, db_path, journal_mode):
    engine = _connect(db_path, journal_mode)
    session = sessionmaker(bind=engine)()

    if mode == "live-owner":
        _seed(session)
        manager = TaskOwnershipManager(session)
        acquired = manager.acquire("original", "owner", ttl_seconds=60)
        assert acquired.acquired, (acquired.reason,)
        assert manager.begin_action("original", acquired.nonce, OLD_ACTION) is True
        session.commit()
        print("READY old-action", flush=True)
        time.sleep(120)
        session.close()
        engine.dispose()
        return

    if mode == "released-owner":
        _seed(session)
        manager = TaskOwnershipManager(session)
        acquired = manager.acquire("original", "owner", ttl_seconds=60)
        assert acquired.acquired, (acquired.reason,)
        assert manager.begin_action("original", acquired.nonce, OLD_ACTION) is True
        assert manager.release("original", acquired.nonce, outcome="owner_released") is True
        session.commit()
        print("READY released", flush=True)
        time.sleep(120)
        session.close()
        engine.dispose()
        return

    if mode != "probe":
        raise SystemExit(f"unknown child mode: {mode}")

    # Probe mode: inspect, recover, and attempt fresh ids.
    task = session.query(Task).filter_by(id="original").one()
    manager = TaskOwnershipManager(session)
    owner_before = manager.get("original")
    before_uncertainty = list(owner_before.uncertain_action_ids) if owner_before else []
    before_active = owner_before.active_action_id if owner_before else None
    before_status = owner_before.status if owner_before else None

    runner = TaskRunner(session)
    if owner_before is not None and owner_before.status == "ACTIVE":
        # Live owner; forced takeover is the supported recovery route.
        recovered = manager.recover(
            "original", "replacement", "forced recovery of killed owner", force=True, ttl_seconds=60
        )
        assert recovered.acquired, (recovered.reason,)
        session.commit()
        runner._task_ownership_nonce = recovered.nonce
        runner._task_ownership_fencing_token = recovered.fencing_token
        runner._task_ownership_fence = recovered.fencing_token
        runner._current_task_id = "original"
        recovery_status = recovered.ownership.status if recovered.ownership else None
        recovery_uncertainty = list(recovered.uncertain_action_ids)
    else:
        recover_result = asyncio.run(
            runner.acquire_task_ownership("original", worker_id="replacement", ttl_seconds=60)
        )
        assert recover_result.acquired, (getattr(recover_result, "reason", None),)
        after_acquire = manager.get("original")
        recovery_status = after_acquire.status if after_acquire else None
        recovery_uncertainty = list(after_acquire.uncertain_action_ids) if after_acquire else []

    fake = FakeEffect()
    loop = TaskExecutionLoop(session, runner, fake)
    loop.runner = runner
    loop.cancellation._state_store = runner
    loop._ownership_lost = False
    loop.model_engine = SimpleNamespace()

    before_attempt_uncertainty = list(
        TaskOwnershipManager(session).get("original").uncertain_action_ids
    )

    attempt_results = []

    async def attempt(action_id):
        try:
            result = await loop._execute_tool(
                "original",
                {"tool_name": "fake-effect", "params": PARAMS},
                approved_by_user=False,
                action_id=action_id,
            )
            return {"action_id": action_id, "dispatched": bool(result), "error": None}
        except Exception as exc:
            return {
                "action_id": action_id,
                "dispatched": False,
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }

    async def run_attempts():
        # Same parameters, two different fresh action IDs. Neither is old-action.
        for action_id in ("fresh", "different"):
            attempt_results.append(await attempt(action_id))

    asyncio.run(run_attempts())

    try:
        session.rollback()
    except Exception:
        pass
    session.close()

    obs = sessionmaker(bind=engine)()
    after_owner = TaskOwnershipManager(obs).get("original")
    result = {
        "before_owner_status": before_status,
        "before_owner_active_action_id": before_active,
        "before_uncertainty": before_uncertainty,
        "recovery_owner_status": recovery_status,
        "uncertainty_after_recover": recovery_uncertainty,
        "uncertainty_before_attempts": before_attempt_uncertainty,
        "attempt_results": attempt_results,
        "fake_calls": fake.calls,
        "action_started_count": obs.query(TaskEvent).filter_by(
            task_id="original", event_type="action.started"
        ).count(),
        "tool_executed_count": obs.query(TaskEvent).filter_by(
            task_id="original", event_type="tool.executed"
        ).count(),
        "uncertainty_after_attempts": list(after_owner.uncertain_action_ids)
        if after_owner
        else [],
        "owner_status_after_attempts": after_owner.status if after_owner else None,
        "original_task_active_action_id": obs.query(Task).filter_by(
            id="original"
        ).one().active_action_id,
        "journal_mode": str(
            obs.connection().exec_driver_sql("PRAGMA journal_mode").scalar()
        ).upper(),
    }
    obs.close()
    engine.dispose()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "child":
        main(sys.argv[2], sys.argv[3], sys.argv[4])
        raise SystemExit(0)
'''


# Exec the child implementation into this module's globals so the parent-side
# helpers and tests share one source, and so the `__main__` guard below can
# dispatch child mode when this file is executed as a script.
exec(CHILD_CODE)


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_same_task_uncertainty_containment_after_live_owner_crash(tmp_path, journal_mode):
    db_path = str((tmp_path / f"live-{journal_mode}.db")).replace("\\", "/")
    owner = _spawn_child("live-owner", db_path, journal_mode)
    try:
        line = _readline(owner)
        assert "old-action" in line
    finally:
        _terminate(owner)

    probe = _spawn_child("probe", db_path, journal_mode)
    try:
        result = json.loads(_readline(probe))
    finally:
        _terminate(probe)

    assert result["before_owner_status"] == "ACTIVE"
    assert result["before_owner_active_action_id"] == OLD_ACTION
    assert result["before_uncertainty"] == []
    assert result["recovery_owner_status"] == "ACTIVE"
    assert result["uncertainty_after_recover"] == [OLD_ACTION]
    assert result["uncertainty_before_attempts"] == [OLD_ACTION]
    assert result["fake_calls"] == 0
    assert result["tool_executed_count"] == 0
    assert result["action_started_count"] == 0
    assert result["original_task_active_action_id"] is None
    assert result["uncertainty_after_attempts"] == [OLD_ACTION]
    assert result["journal_mode"] == journal_mode
    assert all(not item["dispatched"] for item in result["attempt_results"])
    assert len(result["attempt_results"]) == 2
    assert [item["action_id"] for item in result["attempt_results"]] == ["fresh", "different"]
    # Identical parameters are not what caused or cleared uncertainty.
    assert all("unresolved" in (item["error"] or "").lower() or "uncertain" in (item["error"] or "").lower()
               for item in result["attempt_results"])


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_same_task_uncertainty_containment_after_clean_owner_release(tmp_path, journal_mode):
    db_path = str((tmp_path / f"released-{journal_mode}.db")).replace("\\", "/")
    owner = _spawn_child("released-owner", db_path, journal_mode)
    try:
        line = _readline(owner)
        assert "released" in line
    finally:
        _terminate(owner)

    probe = _spawn_child("probe", db_path, journal_mode)
    try:
        result = json.loads(_readline(probe))
    finally:
        _terminate(probe)

    assert result["before_owner_status"] == "RELEASED"
    assert result["before_uncertainty"] == [OLD_ACTION]
    assert result["uncertainty_after_recover"] == [OLD_ACTION]
    assert result["fake_calls"] == 0
    assert result["tool_executed_count"] == 0
    assert result["action_started_count"] == 0
    assert result["uncertainty_after_attempts"] == [OLD_ACTION]
    assert result["journal_mode"] == journal_mode
    assert all(not item["dispatched"] for item in result["attempt_results"])


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "child":
        main(sys.argv[2], sys.argv[3], sys.argv[4])
        raise SystemExit(0)
    # Avoid pytest collection side effects when invoked as a script without
    # child mode: do nothing.
    raise SystemExit(0)
