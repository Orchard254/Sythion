import hashlib, json
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.models.database import Base, Task
from backend.runtime.permission_proposal import (
    PermissionProposal, PermissionProposalManager, PermissionProposalRow,
    MAX_WRITE_BYTES, SUPPORTED_ACTION,
)

def _make_db(tmp_path):
    db_path = tmp_path / "proposal.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30})
    with engine.connect() as conn:
        conn.execute(text("PRAGMA busy_timeout=5000"))
        conn.commit()
    Base.metadata.create_all(engine)
    return engine, db_path

@pytest.fixture
def seeded(tmp_path):
    engine, _ = _make_db(tmp_path)
    with sessionmaker(bind=engine)() as s:
        s.add(Task(id="task-1", original_request="x", state="running", version=3))
        s.commit()
        yield s, engine

def _params(**kw):
    p = {"path": "C:/tmp/demo.txt", "content": "hello world", "overwrite": False}
    p.update(kw)
    return p

def test_happy(seeded):
    s, engine = seeded
    mgr = PermissionProposalManager(s)
    prop = mgr.stage(task_id="task-1", action_id="a1", tool_name=SUPPORTED_ACTION, task_version=3, params=_params())
    assert isinstance(prop, PermissionProposal)
    assert len(prop.params_digest) == 64 and len(prop.proposal_digest) == 64
    assert prop.content_sha256 == hashlib.sha256(b"hello world").hexdigest()
    assert s.query(PermissionProposalRow).count() == 1

def test_caller_digest_rejected(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    with pytest.raises(ValueError):
        mgr.stage(task_id="task-1", action_id="d1", tool_name=SUPPORTED_ACTION, task_version=3, params=_params(proposal_digest="x"))
    with pytest.raises(TypeError):
        mgr.stage(task_id="task-1", action_id="d2", tool_name=SUPPORTED_ACTION, task_version=3, params=_params(), proposal_digest="x")
    with pytest.raises(TypeError):
        mgr.stage(task_id="task-1", action_id="d3", tool_name=SUPPORTED_ACTION, task_version=3, params=_params(), digest="x")

def test_malformed(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    cases = [None, {}, {"path":"x.txt"}, {"content":"x"}, {"path":"","content":"x"},
             {"path":123,"content":"x"}, {"path":"x.txt","content":5},
             {"path":"x.txt","content":"x","overwrite":True},
             {"path":"x.txt","content":"x","overwrite":"false"},
             {"path":"x.txt","content":"x","extra":1},
             {"path":"C:/tmp/*.txt","content":"x"}]
    for i,p in enumerate(cases):
        with pytest.raises((ValueError, TypeError)):
            mgr.stage(task_id="task-1", action_id=f"bad{i}", tool_name=SUPPORTED_ACTION, task_version=3, params=p)

def test_oversized(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    with pytest.raises(ValueError):
        mgr.stage(task_id="task-1", action_id="big", tool_name=SUPPORTED_ACTION, task_version=3,
                  params={"path":"C:/tmp/b.txt","content":"a"*(MAX_WRITE_BYTES+1)})

def test_unsupported_tool(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    with pytest.raises(ValueError):
        mgr.stage(task_id="task-1", action_id="ut", tool_name="file_write", task_version=3,
                  params={"path":"C:/tmp/x.txt","content":"x"})

def test_task_mismatch(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    with pytest.raises(ValueError):
        mgr.stage(task_id="task-1", action_id="tm", tool_name=SUPPORTED_ACTION, task_version=2,
                  params={"path":"C:/tmp/x.txt","content":"x"})
    with pytest.raises(ValueError):
        mgr.stage(task_id="nope", action_id="tm2", tool_name=SUPPORTED_ACTION, task_version=3,
                  params={"path":"C:/tmp/x.txt","content":"x"})

def test_terminal_states(tmp_path):
    engine, _ = _make_db(tmp_path)
    for st in ["cancelled","interrupted","completed","failed","paused"]:
        with sessionmaker(bind=engine)() as s:
            s.query(Task).delete()
            s.commit()
            s.add(Task(id="t", original_request="x", state=st, version=1))
            s.commit()
            mgr = PermissionProposalManager(s)
            with pytest.raises(ValueError):
                mgr.stage(task_id="t", action_id=f"a{st}", tool_name=SUPPORTED_ACTION, task_version=1,
                          params={"path":"C:/tmp/x.txt","content":"x"})

def test_active_action(tmp_path):
    engine, _ = _make_db(tmp_path)
    with sessionmaker(bind=engine)() as s:
        s.add(Task(id="t", original_request="x", state="running", version=1, active_action_id="o"))
        s.commit()
        mgr = PermissionProposalManager(s)
        with pytest.raises(ValueError):
            mgr.stage(task_id="t", action_id="a", tool_name=SUPPORTED_ACTION, task_version=1,
                      params={"path":"C:/tmp/x.txt","content":"x"})

def test_conflicting_pending(tmp_path):
    engine, _ = _make_db(tmp_path)
    with sessionmaker(bind=engine)() as s:
        s.add(Task(id="t", original_request="x", state="running", version=1,
                   pending_action=json.dumps({"action_id":"other"})))
        s.commit()
        mgr = PermissionProposalManager(s)
        with pytest.raises(ValueError):
            mgr.stage(task_id="t", action_id="a", tool_name=SUPPORTED_ACTION, task_version=1,
                      params={"path":"C:/tmp/x.txt","content":"x"})

def test_stale_duplicate(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    mgr.stage(task_id="task-1", action_id="dup", tool_name=SUPPORTED_ACTION, task_version=3,
              params={"path":"C:/tmp/a.txt","content":"one"})
    s.commit()
    with pytest.raises(ValueError):
        mgr.stage(task_id="task-1", action_id="dup", tool_name=SUPPORTED_ACTION, task_version=3,
                  params={"path":"C:/tmp/a.txt","content":"two"})
    with pytest.raises(ValueError):
        mgr.stage(task_id="task-1", action_id="dup", tool_name=SUPPORTED_ACTION, task_version=4,
                  params={"path":"C:/tmp/a.txt","content":"one"})

def test_rollback_privacy(tmp_path):
    engine, _ = _make_db(tmp_path)
    secret = "SUPER_SECRET_CONTENT_12345"
    with sessionmaker(bind=engine)() as s:
        s.add(Task(id="t", original_request="x", state="running", version=1))
        s.commit()
        mgr = PermissionProposalManager(s)
        prop = mgr.stage(task_id="t", action_id="a", tool_name=SUPPORTED_ACTION, task_version=1,
                         params={"path":"C:/tmp/never.txt","content":secret})
        s.rollback()
        with sessionmaker(bind=engine)() as s2:
            assert s2.query(PermissionProposalRow).count() == 0
        payload = prop.to_dict()
        assert secret not in __import__("json").dumps(payload)
        assert secret not in repr(prop)
        assert "content" not in payload
        assert "params" not in payload

def test_digest_tamper(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    p1 = mgr.stage(task_id="task-1", action_id="t1", tool_name=SUPPORTED_ACTION, task_version=3,
                   params={"path":"C:/tmp/x.txt","content":"alpha"})
    s.commit()
    p2 = PermissionProposalManager(s).stage(task_id="task-1", action_id="t2", tool_name=SUPPORTED_ACTION, task_version=3,
                                           params={"path":"C:/tmp/x.txt","content":"beta"})
    s.commit()
    assert p1.params_digest != p2.params_digest
    assert p1.proposal_digest != p2.proposal_digest
    t = s.query(Task).first()
    t.version = 4
    s.commit()
    p3 = PermissionProposalManager(s).stage(task_id="task-1", action_id="t3", tool_name=SUPPORTED_ACTION, task_version=4,
                                           params={"path":"C:/tmp/x.txt","content":"alpha"})
    s.commit()
    assert p3.params_digest == p1.params_digest
    assert p3.proposal_digest != p1.proposal_digest

def test_isolated_sqlite(tmp_path):
    engine, db_path = _make_db(tmp_path)
    assert db_path.exists()
    with sessionmaker(bind=engine)() as s:
        s.add(Task(id="t", original_request="x", state="running", version=1))
        s.commit()
        mgr = PermissionProposalManager(s)
        with pytest.raises(ValueError):
            mgr.stage(task_id="", action_id="a", tool_name=SUPPORTED_ACTION, task_version=1,
                      params={"path":"C:/tmp/x.txt","content":"x"})
        prop = mgr.stage(task_id="t", action_id="a", tool_name=SUPPORTED_ACTION, task_version=1,
                         params={"path":"C:/tmp/x.txt","content":"x"})
        assert prop.proposal_id is not None
        s.commit()
    engine.dispose()

def test_no_execution(tmp_path):
    target = tmp_path / "should_not_exist.txt"
    engine, _ = _make_db(tmp_path)
    with sessionmaker(bind=engine)() as s:
        s.add(Task(id="t", original_request="x", state="running", version=1))
        s.commit()
        mgr = PermissionProposalManager(s)
        mgr.stage(task_id="t", action_id="a", tool_name=SUPPORTED_ACTION, task_version=1,
                  params={"path":str(target),"content":"data"})
        s.commit()
    assert not target.exists()

def test_hex(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    p = mgr.stage(task_id="task-1", action_id="hex", tool_name=SUPPORTED_ACTION, task_version=3,
                  params={"path":"C:/tmp/x.txt","content":"x"})
    s.commit()
    assert len(p.params_digest)==64 and len(p.proposal_digest)==64
    int(p.params_digest,16); int(p.proposal_digest,16)

def test_overwrite_rejected(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    with pytest.raises(ValueError):
        mgr.stage(task_id="task-1", action_id="ow", tool_name=SUPPORTED_ACTION, task_version=3,
                  params={"path":"C:/tmp/x.txt","content":"x","overwrite":True})


def test_task_unavailable_fail_closed(monkeypatch, tmp_path):
    import backend.runtime.permission_proposal as pp
    monkeypatch.setattr(pp, "Task", None)
    engine, _ = _make_db(tmp_path)
    with sessionmaker(bind=engine)() as s:
        mgr = PermissionProposalManager(s)
        with pytest.raises(ValueError):
            mgr.stage(task_id="t", action_id="a", tool_name=SUPPORTED_ACTION, task_version=1,
                      params={"path": "C:/tmp/x.txt", "content": "x"})


def test_task_hint_fail_closed(tmp_path):
    engine, _ = _make_db(tmp_path)
    with sessionmaker(bind=engine)() as s:
        with pytest.raises(ValueError):
            PermissionProposalManager(s, task=object())


def test_schema_version_exact(tmp_path):
    engine, _ = _make_db(tmp_path)
    with sessionmaker(bind=engine)() as s:
        s.add(Task(id="t", original_request="x", state="running", version=1))
        s.commit()
        mgr = PermissionProposalManager(s)
        for bad in (0, 2, -1, "2"):
            with pytest.raises(ValueError):
                mgr.stage(task_id="t", action_id="sv{}".format(bad), tool_name=SUPPORTED_ACTION,
                          task_version=1, schema_version=bad,
                          params={"path": "C:/tmp/x.txt", "content": "x"})


def test_relative_and_traversal_rejected(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    for bad_path in ["relative.txt", "C:/tmp/../x.txt", "CON"]:
        with pytest.raises(ValueError):
            mgr.stage(task_id="task-1", action_id="badpath", tool_name=SUPPORTED_ACTION,
                      task_version=3, params={"path": bad_path, "content": "x"})


def test_schema_registration():
    import backend.runtime.permission_proposal as pp
    assert "permission_proposals" in pp.Base.metadata.tables


def test_windows_alias_paths_rejected(seeded):
    s, _ = seeded
    mgr = PermissionProposalManager(s)
    bad_paths = [
        "C:\\",
        "C:/tmp/file.txt:stream",
        "\\\\.\\COM1",
        "\\server\\share\\x.txt",
        "C:/tmp/COM1/x.txt",
        " C:/tmp/x.txt",
        "C:/tmp/x.txt ",
        "relative.txt",
        "C:/tmp/../x.txt",
    ]
    for i, bad in enumerate(bad_paths):
        with pytest.raises(ValueError):
            mgr.stage(task_id="task-1", action_id="win{}".format(i), tool_name=SUPPORTED_ACTION,
                      task_version=3, params={"path": bad, "content": "x"})


def test_real_init_database_registration(tmp_path):
    from backend.core.db_manager import init_database
    from sqlalchemy import inspect
    dbpath = str(tmp_path / "real_init.db")
    mgr = init_database(dbpath)
    try:
        assert "permission_proposals" in inspect(mgr.engine).get_table_names()
    finally:
        mgr.engine.dispose()
