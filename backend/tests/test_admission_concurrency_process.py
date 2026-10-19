"""Cross-process admission concurrency regression tests."""

import multiprocessing
import time
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.agents.delegation import DelegationService, active_delegations_for_task
from backend.agents.service import AgentService
from backend.agents.state import AgentType, SpecialistRole
from backend.models.database import Base
from backend.core.config import settings


def _make_db(db_path: str):
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


def _child_validate(db_path: str, deleg_id: str, barrier, out_queue):
    try:
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        session = sessionmaker(bind=engine)()
        try:
            barrier.wait(timeout=30)
            srv = DelegationService(session)
            result = srv.validate(deleg_id)
            session.commit()
            out_queue.put(("ok", bool(result.get("ok"))))
        except Exception as exc:
            try:
                session.rollback()
            except Exception:
                pass
            out_queue.put(("exc", str(exc)[:200]))
        finally:
            session.close()
            engine.dispose()
    except Exception as exc:
        out_queue.put(("exc", str(exc)[:200]))


class TestAdmissionCrossProcess:
    def test_concurrent_admission_across_processes(self, tmp_path):
        db_path = str(tmp_path / "admission.db")
        engine = _make_db(db_path)
        session = sessionmaker(bind=engine)()
        svc = AgentService(session)
        svc.ensure_definitions()
        main = svc.get_or_create_main()

        from backend.agents.delegation import DelegationRequest

        ds = DelegationService(session)
        first = ds.propose(DelegationRequest(
            parent_agent_id=main.id, specialist_role="ANALYSIS",
            objective="first job", task_id="t-proc"))
        assert first["error"] is None
        assert ds.validate(first["delegation"].id)["ok"]

        second = ds.propose(DelegationRequest(
            parent_agent_id=main.id, specialist_role="ANALYSIS",
            objective="second job", task_id="t-proc"))
        third = ds.propose(DelegationRequest(
            parent_agent_id=main.id, specialist_role="ANALYSIS",
            objective="third job", task_id="t-proc"))
        assert second["delegation"] is not None
        assert third["delegation"] is not None
        second_id = second["delegation"].id
        third_id = third["delegation"].id
        session.commit()
        session.close()
        engine.dispose()

        barrier = multiprocessing.Barrier(2)
        queue = multiprocessing.Queue()
        p1 = multiprocessing.Process(
            target=_child_validate,
            args=(db_path, second_id, barrier, queue),
        )
        p2 = multiprocessing.Process(
            target=_child_validate,
            args=(db_path, third_id, barrier, queue),
        )
        p1.start()
        p2.start()
        p1.join(30)
        p2.join(30)
        assert not p1.is_alive() and not p2.is_alive(), "child hung"

        results = []
        while not queue.empty():
            results.append(queue.get())
        oks = [r for r in results if r[0] == "ok" and r[1] is True]
        assert len(oks) <= 1, f"fan-out exceeded across processes: {results}"

        engine = _make_db(db_path)
        # reopen
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30})
        sess = sessionmaker(bind=engine)()
        assert active_delegations_for_task(sess, "t-proc", None) <= 2
        sess.close()
        engine.dispose()
