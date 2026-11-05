"""Isolated real-subprocess characterization for composer creation/scheduling crashes.

These tests spawn fresh interpreters that share only a private SQLite file.  They
use a fake conversation engine and a fake worker and never touch real tools,
files, network, profiles, vault, or any product database.  Crashes are produced
by genuine parent-controlled process termination at named barriers.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from fastapi import BackgroundTasks

SYTHION_ROOT = Path(__file__).resolve().parents[2]
TEST_FILE = Path(__file__).resolve()

REQUEST_ID = "composer-crash-req-1"
SESSION_ID = "composer-crash-session"


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
        raise AssertionError(f"child exited early:\n{str(err)[-4000:]}")
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

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import (
    Base, Task, ConversationExchange, ConversationTurn,
    ConversationTaskAssociation,
)
from backend.api.schemas import AssistantTurnRequest
from backend.api.routes import assistant_turn

REQUEST_ID = "composer-crash-req-1"
SESSION_ID = "composer-crash-session"


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


def _setup_session():
    import backend.core.session as session_mod
    session_mod._EXPLICIT_SESSION_ID = SESSION_ID
    try:
        from backend.services import conversation_state
        conversation_state.update_state = lambda *a, **k: None
    except Exception:
        pass


class _FakeEngine:
    def __init__(self, *a, **k):
        pass

    async def run_turn(self, *a, **k):
        return {
            "assistant_text": "Requested background work.",
            "commands": [{"type": "start_task", "request": "same request"}],
        }


def _make_noop_worker():
    async def _worker(db, task_id, tool_executor=None):
        return None

    return _worker


def _make_blocking_worker():
    async def _worker(db, task_id, tool_executor=None):
        print("READY worker-execution", flush=True)
        await asyncio.sleep(120)

    return _worker


def _state_snapshot(db, task_ids_out=None):
    tasks = db.query(Task).all()
    ex_count = db.query(ConversationExchange).count()
    turn_count = db.query(ConversationTurn).count()
    assoc_count = db.query(ConversationTaskAssociation).count()
    receipt_status = None
    try:
        from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

        manager = ComposerRequestReceiptManager(db)
        receipt = manager.get(request_id=REQUEST_ID, session_id=SESSION_ID)
        receipt_status = getattr(receipt, "status", None) if receipt is not None else None
    except Exception as exc:
        receipt_status = "ERROR:%s:%s" % (type(exc).__name__, str(exc)[:160])
    snap = {
        "task_count": len(tasks),
        "task_ids": [t.id for t in tasks],
        "task_states": [t.state for t in tasks],
        "task_versions": [t.version for t in tasks],
        "task_active_action_ids": [t.active_action_id for t in tasks],
        "exchange_count": ex_count,
        "turn_count": turn_count,
        "association_count": assoc_count,
        "receipt_status": receipt_status,
    }
    if task_ids_out is not None:
        task_ids_out.extend(snap["task_ids"])
    return snap


def _reopen_snapshot(db_path, journal_mode):
    engine = _connect(db_path, journal_mode)
    session = sessionmaker(bind=engine)()
    _setup_session()
    snap = _state_snapshot(session)
    session.close()
    engine.dispose()
    return snap


def main(mode, db_path, journal_mode):
    engine = _connect(db_path, journal_mode)
    db = sessionmaker(bind=engine)()
    _setup_session()

    import backend.services.conversation_turn_engine as cte
    cte.ConversationTurnEngine = _FakeEngine
    import backend.services.conversation_runtime as cr
    cr.ModelClient = lambda *a, **k: object()

    import backend.api.routes as routes

    if mode == "crash-worker":
        routes.run_task = _make_blocking_worker()
    else:
        routes.run_task = _make_noop_worker()

    req = AssistantTurnRequest(text="do work", client_request_id=REQUEST_ID)

    if mode in ("crash-before-add", "crash-after-add"):
        bg = BackgroundTasks()
        orig_add = bg.add_task

        if mode == "crash-before-add":
            def hooked(*args, **kwargs):
                print("READY before-add-task", flush=True)
                time.sleep(120)
                orig_add(*args, **kwargs)

        else:
            def hooked(*args, **kwargs):
                orig_add(*args, **kwargs)
                print("READY after-add-task", flush=True)
                time.sleep(120)

        bg.add_task = hooked
        try:
            asyncio.run(assistant_turn(req, bg, db, None))
        except Exception as exc:
            print("UNEXPECTED %s: %s" % (type(exc).__name__, str(exc)[:300]), flush=True)
        time.sleep(120)
        db.close()
        engine.dispose()
        return

    if mode == "crash-worker":
        bg = BackgroundTasks()
        # Register and run the fake worker. The worker itself emits the named
        # barrier that the parent kills at. Commit here mirrors the request-scoped
        # get_db completion that happens before a background task runs.
        asyncio.run(assistant_turn(req, bg, db, None))
        try:
            db.commit()
        except Exception:
            db.rollback()
        try:
            asyncio.run(bg())
        except Exception as exc:
            print("WORKER-ERROR %s: %s" % (type(exc).__name__, str(exc)[:300]), flush=True)
        time.sleep(120)
        db.close()
        engine.dispose()
        return

    if mode == "probe":
        import backend.core.session as session_mod
        session_mod._EXPLICIT_SESSION_ID = SESSION_ID

        before = _state_snapshot(db)

        bg = BackgroundTasks()
        retry_result = None
        try:
            response = asyncio.run(assistant_turn(req, bg, db, None))
            retry_result = {
                "ok": True,
                "status": getattr(response, "status", None),
                "background_task_ids": getattr(response, "background_task_ids", None),
                "receipt_id": getattr(response, "composer_receipt_id", None),
                "bg_registered_count": len(bg.tasks),
            }
        except HTTPException as exc:
            retry_result = {
                "ok": False,
                "http_status": exc.status_code,
                "detail": str(exc.detail)[:240],
                "bg_registered_count": len(bg.tasks),
            }
        except Exception as exc:
            retry_result = {
                "ok": False,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:240]),
                "bg_registered_count": len(bg.tasks),
            }

        after = _state_snapshot(db)
        reopened = _reopen_snapshot(db_path, journal_mode)

        result = {
            "before_state": before,
            "retry": retry_result,
            "after_state": after,
            "reopened_state": reopened,
        }
        db.close()
        engine.dispose()
        print(json.dumps(result), flush=True)
        return

    raise SystemExit("unknown mode: %s" % mode)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "child":
        main(sys.argv[2], sys.argv[3], sys.argv[4])
        raise SystemExit(0)
    raise SystemExit(0)
'''

exec(CHILD_CODE)


def _parse_mode_result(line):
    return line


import pytest


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_composer_crash_before_add_task_then_retry(tmp_path, journal_mode):
    db_path = str((tmp_path / ("before-add-" + journal_mode + ".db")).resolve()).replace("\\", "/")
    child = _spawn_child("crash-before-add", db_path, journal_mode)
    try:
        line = _readline(child)
        assert "before-add-task" in line, line
    finally:
        _terminate(child)

    probe = _spawn_child("probe", db_path, journal_mode)
    try:
        result = json.loads(_readline(probe))
    finally:
        _terminate(probe)

    before = result["before_state"]
    retry = result["retry"]
    reopened = result["reopened_state"]

    # A task row and completed conversation/association were durably committed
    # before the registration barrier.
    assert before["task_count"] == 1, before
    assert before["exchange_count"] == 1, before
    assert before["turn_count"] == 2, before
    assert before["association_count"] == 1, before
    assert before["task_states"] == ["running"], before
    assert before["receipt_status"] == "resolving", before

    # Retry is an honest durable replay, not a fresh launch.
    assert retry["ok"] is True, retry
    assert retry["status"] == "completed", retry
    assert retry["background_task_ids"] == before["task_ids"], (retry, before)
    assert retry["bg_registered_count"] == 0, retry

    # Durable state is unchanged by the retry and survives reopen.
    assert reopened["task_count"] == 1, reopened
    assert reopened["exchange_count"] == 1, reopened
    assert reopened["association_count"] == 1, reopened
    assert reopened["task_states"] == ["running"], reopened
    # Scheduling was not durably established by the crashed attempt.
    assert reopened["receipt_status"] == "resolving", reopened


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_composer_crash_after_add_task_then_retry(tmp_path, journal_mode):
    db_path = str((tmp_path / ("after-add-" + journal_mode + ".db")).resolve()).replace("\\", "/")
    child = _spawn_child("crash-after-add", db_path, journal_mode)
    try:
        line = _readline(child)
        assert "after-add-task" in line, line
    finally:
        _terminate(child)

    probe = _spawn_child("probe", db_path, journal_mode)
    try:
        result = json.loads(_readline(probe))
    finally:
        _terminate(probe)

    before = result["before_state"]
    retry = result["retry"]
    reopened = result["reopened_state"]

    assert before["task_count"] == 1, before
    assert before["exchange_count"] == 1, before
    assert before["turn_count"] == 2, before
    assert before["association_count"] == 1, before
    assert before["task_states"] == ["running"], before
    assert before["receipt_status"] == "resolving", before

    assert retry["ok"] is True, retry
    assert retry["status"] == "completed", retry
    assert retry["background_task_ids"] == before["task_ids"], (retry, before)
    assert retry["bg_registered_count"] == 0, retry

    assert reopened["task_count"] == 1, reopened
    assert reopened["exchange_count"] == 1, reopened
    assert reopened["association_count"] == 1, reopened
    assert reopened["task_states"] == ["running"], reopened
    assert reopened["receipt_status"] == "resolving", reopened


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_composer_crash_during_fake_worker_execution_then_retry(tmp_path, journal_mode):
    db_path = str((tmp_path / ("worker-" + journal_mode + ".db")).resolve()).replace("\\", "/")
    child = _spawn_child("crash-worker", db_path, journal_mode)
    try:
        line = _readline(child)
        assert "worker-execution" in line, line
    finally:
        _terminate(child)

    probe = _spawn_child("probe", db_path, journal_mode)
    try:
        result = json.loads(_readline(probe))
    finally:
        _terminate(probe)

    before = result["before_state"]
    retry = result["retry"]
    reopened = result["reopened_state"]

    # The composer request completed registration before the worker crash; the
    # receipt is resolved and no second copy is created on retry.
    assert before["task_count"] == 1, before
    assert before["exchange_count"] == 1, before
    assert before["turn_count"] == 2, before
    assert before["association_count"] == 1, before
    assert before["task_states"] == ["running"], before
    assert before["receipt_status"] == "resolved", before

    assert retry["ok"] is True, retry
    assert retry["status"] == "completed", retry
    assert retry["background_task_ids"] == before["task_ids"], (retry, before)

    assert reopened["task_count"] == 1, reopened
    assert reopened["exchange_count"] == 1, reopened
    assert reopened["association_count"] == 1, reopened
    assert reopened["task_states"] == ["running"], reopened
    assert reopened["receipt_status"] == "resolved", reopened


if __name__ == "__main__":
    # Avoid pytest collection of script-only execution.
    raise SystemExit(0)
