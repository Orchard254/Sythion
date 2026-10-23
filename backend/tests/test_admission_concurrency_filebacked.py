"""File-backed admission concurrency regression tests (item 022).

These tests exercise the admission boundary on a REAL file-backed SQLite
database using per-thread sessions (QueuePool), i.e. the honest analogue
of DatabaseManager's production pool - NOT the shared StaticPool connection
used by the in-memory harness. The cross-process counterpart is
backend/tests/test_admission_concurrency_process.py.

No production logic/SQL change is required for these to pass; they exist to
commit the harness-only classification of the intermittent in-memory failure.
"""

import threading
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.agents.delegation import (
    DelegationService,
    active_delegations_for_task,
    DelegationRequest,
)
from backend.agents.service import AgentService
from backend.agents.state import SpecialistRole
from backend.models.database import Base, Delegation
from backend.core.config import settings

MAX = 2


def _make_db(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=5000"))
        conn.commit()
    Base.metadata.create_all(engine)
    return engine


def _seed(engine, task_id="t-boundary"):
    session = sessionmaker(bind=engine)()
    svc = AgentService(session)
    svc.ensure_definitions()
    main = svc.get_or_create_main()
    ds = DelegationService(session)
    first = ds.propose(DelegationRequest(
        parent_agent_id=main.id, specialist_role="ANALYSIS",
        objective="first job", task_id=task_id))
    assert first["error"] is None
    assert ds.validate(first["delegation"].id)["ok"]
    second = ds.propose(DelegationRequest(
        parent_agent_id=main.id, specialist_role="ANALYSIS",
        objective="second job", task_id=task_id))
    third = ds.propose(DelegationRequest(
        parent_agent_id=main.id, specialist_role="ANALYSIS",
        objective="third job", task_id=task_id))
    assert second["delegation"] is not None
    assert third["delegation"] is not None
    session.commit()
    second_id = second["delegation"].id
    third_id = third["delegation"].id
    session.close()
    return second_id, third_id


class TestAdmissionFileBacked:
    def test_concurrent_admission_boundary_shared_writer_connection(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.core.config.settings.max_specialists_per_task", MAX)
        db_path = str(tmp_path / "shared.db")
        engine = _make_db(db_path)
        try:
            second_id, third_id = _seed(engine)
            session = sessionmaker(bind=engine)()
            lock = threading.Lock()
            results = []

            def worker(deleg_id, idx):
                with lock:
                    try:
                        r = DelegationService(session).validate(deleg_id)
                        session.commit()
                        results.append((idx, r))
                    except Exception as e:
                        try:
                            session.rollback()
                        except Exception:
                            pass
                        results.append((idx, {"ok": False, "exc": str(e)}))

            threads = [
                threading.Thread(target=worker,
                                 args=(second_id, 0)),
                threading.Thread(target=worker,
                                 args=(third_id, 1)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            oks = [r for _, r in results if isinstance(r, dict) and r.get("ok")]
            assert len(oks) <= 1, f"fan-out exceeded: {results}"
            total = active_delegations_for_task(session, "t-boundary", None)
            assert total <= MAX, f"persisted count exceeded: {total}"
            session.close()
        finally:
            engine.dispose()

    def test_concurrent_admission_per_thread_sessions_at_boundary(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.core.config.settings.max_specialists_per_task", MAX)
        db_path = str(tmp_path / "perthread.db")
        engine = _make_db(db_path)
        second_id, third_id = _seed(engine)
        engine.dispose()

        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        try:
            results = []
            barrier = threading.Barrier(2)
            joined_flags = {"t0": False, "t1": False}

            def worker(deleg_id, idx, flag_key):
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    results.append((idx, {"ok": False, "exc": "barrier broken"}))
                    return
                sess = sessionmaker(bind=engine)()
                try:
                    r = DelegationService(sess).validate(deleg_id)
                    sess.commit()
                    results.append((idx, r))
                except Exception as e:
                    try:
                        sess.rollback()
                    except Exception:
                        pass
                    results.append((idx, {"ok": False, "exc": str(e)}))
                finally:
                    sess.close()
                    joined_flags[flag_key] = True

            t0 = threading.Thread(target=worker,
                                  args=(second_id, 0, "t0"))
            t1 = threading.Thread(target=worker,
                                  args=(third_id, 1, "t1"))
            t0.start()
            t1.start()
            t0.join(timeout=10)
            t1.join(timeout=10)

            assert not t0.is_alive() and not t1.is_alive(), "thread hung"
            oks = [r for _, r in results
                   if isinstance(r, dict) and r.get("ok")]
            assert len(oks) <= 1, (
                f"fan-out exceeded across per-thread sessions: {results}")

            read_engine = create_engine(
                f"sqlite:///{db_path}",
                connect_args={"check_same_thread": False, "timeout": 30},
            )
            try:
                read_sess = sessionmaker(bind=read_engine)()
                try:
                    total = active_delegations_for_task(
                        read_sess, "t-boundary", None)
                    assert total <= MAX, (
                        f"persisted count exceeded: {total}")
                    proposed = read_sess.query(Delegation).filter(
                        Delegation.task_id == "t-boundary",
                        Delegation.status == "PROPOSED",
                    ).count()
                    # No uncommitted/partial state: every processed proposal
                    # resolved to VALIDATED or FAILED.
                    assert proposed == 0, (
                        f"leftover PROPOSED rows: {proposed}")
                finally:
                    read_sess.close()
            finally:
                read_engine.dispose()
        finally:
            engine.dispose()
