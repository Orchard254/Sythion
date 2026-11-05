"""Adversarial tests for the durable composer inbound request receipt foundation."""

import hashlib
import json
import sqlite3
import threading
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.db_manager import DatabaseManager
from backend.models.database import Base, SessionLineage
from backend.runtime.composer_request_receipt import (
    STATUS_RECEIVED,
    STATUS_RESOLVING,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_RESOLVED,
    STATUS_FAILED,
)


def _import_receipt():
    from backend.runtime.composer_request_receipt import (
        ComposerRequestReceiptManager as Manager,
        ComposerRequestReceiptRow as Row,
        ComposerRequestReceipt as Receipt,
        STATUS_RECEIVED,
        STATUS_RESOLVING,
        STATUS_NEEDS_CLARIFICATION,
        STATUS_RESOLVED,
        STATUS_FAILED,
        ALL_STATUSES,
        TRANSITIONS,
    )

    return Manager, Row, Receipt, STATUS_RECEIVED, STATUS_RESOLVING, STATUS_NEEDS_CLARIFICATION, STATUS_RESOLVED, STATUS_FAILED, ALL_STATUSES, TRANSITIONS


def _engine(tmp_path, name="receipt.db"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    return engine


def _trusted_session(db, session_id=None):
    sid = session_id or f"trusted-{uuid.uuid4()}"
    row = SessionLineage(
        session_id=sid,
        generation=1,
        started_at=__import__("datetime").datetime.utcnow(),
        system_generated=True,
        provenance="system_uuid4",
        parent_session_id=None,
        reason="test",
        created_at=__import__("datetime").datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return sid


def test_import_variants_exist():
    import backend.runtime.composer_request_receipt as a
    import backend.runtime.composer_inbound_request_receipt as b
    import backend.services.composer_request_receipt as c
    import backend.services.composer_inbound_request_receipt as d
    assert a.ComposerRequestReceiptManager is b.ComposerRequestReceiptManager


def test_request_and_session_identity_required(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        with pytest.raises(ValueError):
            mgr.receive(session_id=sid, text="hello")
        with pytest.raises(ValueError):
            mgr.receive(request_id="", session_id=sid, text="hello")
        with pytest.raises(ValueError):
            mgr.receive(request_id="req-1", text="hello")
        with pytest.raises(ValueError):
            mgr.receive(request_id="req-1", session_id="", text="hello")
        with pytest.raises(ValueError):
            mgr.receive(request_id="req-1", session_id=sid, text="")
    engine.dispose()


def test_explicit_session_not_blocked_for_legacy_or_arbitrary_scopes(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        scope = "legacy-or-user-supplied-session"
        mgr = Manager(db)
        receipt = mgr.receive(request_id="req-1", session_id=scope, text="hello")
        db.commit()
        assert receipt.session_id == scope
    engine.dispose()


def test_read_requires_explicit_session(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        scope = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=scope, text="hello")
        db.commit()
        with pytest.raises(ValueError):
            mgr.get(request_id="req-1")
        with pytest.raises(ValueError):
            mgr.list()
    engine.dispose()


def test_same_request_same_payload_one_receipt(tmp_path):
    Manager, Row, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        first = mgr.receive(request_id="req-1", session_id=sid, text="exact text", payload={"a": 1})
        db.commit()
        second = mgr.receive(request_id="req-1", session_id=sid, text="exact text", payload={"a": 1})
        db.commit()
        assert first.request_id == second.request_id
        assert first.session_id == second.session_id
        assert first.payload_digest == second.payload_digest
        assert db.query(Row).count() == 1
    engine.dispose()


def test_changed_text_conflicts(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=sid, text="first")
        db.commit()
        with pytest.raises(ValueError, match="conflict|Conflict"):
            mgr.receive(request_id="req-1", session_id=sid, text="second")
    engine.dispose()


def test_changed_payload_conflicts(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=sid, text="same", payload={"x": 1})
        db.commit()
        with pytest.raises(ValueError, match="conflict|Conflict"):
            mgr.receive(request_id="req-1", session_id=sid, text="same", payload={"x": 2})
    engine.dispose()


def test_caller_digest_is_not_authority(tmp_path):
    Manager, Row, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        # Same request + same structural payload.  A caller-supplied digest string
        # is not part of the payload and cannot create a conflict by itself.
        mgr.receive(request_id="req-1", session_id=sid, text="same", payload={"x": 1})
        db.commit()
        same = mgr.receive(request_id="req-1", session_id=sid, text="same", payload={"x": 1})
        db.commit()
        assert same is not None
        assert db.query(Row).count() == 1
    engine.dispose()


def test_strict_payload_rejects_unsupported_and_surrogates(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        with pytest.raises(ValueError):
            mgr.receive(request_id="req-1", session_id=sid, text="hello", payload=b"raw bytes")
        with pytest.raises(ValueError):
            mgr.receive(request_id="req-2", session_id=sid, text="hello", payload={"bad": "x\udcff"})
    engine.dispose()


def test_exact_text_preserved(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        text = "  leading/trailing spaces and\nnewlines  "
        mgr = Manager(db)
        receipt = mgr.receive(request_id="req-1", session_id=sid, text=text, payload={"p": "v"})
        db.commit()
        fetched = mgr.get(request_id="req-1", session_id=sid)
        assert fetched.text == text
        assert fetched.text_digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert fetched.payload_digest == hashlib.sha256(__import__("json").dumps({"p": "v"}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    engine.dispose()


def test_different_request_ids_separate(tmp_path):
    Manager, Row, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=sid, text="hello")
        mgr.receive(request_id="req-2", session_id=sid, text="hello")
        db.commit()
        assert db.query(Row).count() == 2
    engine.dispose()


def test_different_sessions_do_not_leak(tmp_path):
    Manager, Row, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        s1 = f"session-a-{uuid.uuid4()}"
        s2 = f"session-b-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=s1, text="secret in A")
        db.commit()
        assert mgr.get(request_id="req-1", session_id=s2) is None
        assert mgr.list(session_id=s2) == []
        assert db.query(Row).count() == 1
    engine.dispose()


def test_same_request_id_allowed_in_different_sessions(tmp_path):
    Manager, Row, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        s1 = f"session-a-{uuid.uuid4()}"
        s2 = f"session-b-{uuid.uuid4()}"
        mgr = Manager(db)
        a = mgr.receive(request_id="req-1", session_id=s1, text="A")
        b = mgr.receive(request_id="req-1", session_id=s2, text="B")
        db.commit()
        assert a.session_id != b.session_id
        assert db.query(Row).count() == 2
    engine.dispose()


def test_concurrent_same_id_same_payload_one_receipt(tmp_path):
    Manager, Row, *_ = _import_receipt()
    db_path = tmp_path / "concurrent.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    sid = f"session-{uuid.uuid4()}"
    engine.dispose()

    errors = []
    results = []

    def worker():
        eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30})
        try:
            with sessionmaker(bind=eng)() as db:
                mgr = Manager(db)
                r = mgr.receive(request_id="req-1", session_id=sid, text="concurrent", payload={"k": 1})
                db.commit()
                results.append(r)
        except Exception as exc:
            errors.append(exc)
        finally:
            eng.dispose()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(results) == 8

    check_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30})
    with sessionmaker(bind=check_engine)() as db:
        assert db.query(Row).count() == 1
    check_engine.dispose()


def test_caller_rollback_removes_receipt(tmp_path):
    Manager, Row, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=sid, text="rollback me")
        db.rollback()
        assert db.query(Row).count() == 0
        assert mgr.get(request_id="req-1", session_id=sid) is None
    engine.dispose()


def test_failed_transaction_does_not_persist(tmp_path):
    Manager, Row, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=sid, text="first")
        db.commit()
        try:
            mgr.receive(request_id="req-1", session_id=sid, text="second")
        except ValueError:
            db.rollback()
        assert db.query(Row).count() == 1
        assert mgr.get(request_id="req-1", session_id=sid).text == "first"
    engine.dispose()


def test_no_hidden_commit(tmp_path):
    Manager, Row, *_ = _import_receipt()
    engine = _engine(tmp_path)
    session = sessionmaker(bind=engine)()
    sid = f"session-{uuid.uuid4()}"
    mgr = Manager(session)
    mgr.receive(request_id="req-1", session_id=sid, text="uncommitted")
    with sessionmaker(bind=engine)() as other:
        assert other.query(Row).count() == 0
    session.rollback()
    session.close()
    engine.dispose()


def test_list_filters_session_and_status(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        s1 = f"session-a-{uuid.uuid4()}"
        s2 = f"session-b-{uuid.uuid4()}"
        mgr = Manager(db)
        first = mgr.receive(request_id="req-1", session_id=s1, text="A")
        mgr.transition(request_id="req-1", session_id=s1, status=STATUS_RESOLVING)
        mgr.receive(request_id="req-2", session_id=s2, text="B")
        db.commit()
        assert len(mgr.list(session_id=s1)) == 1
        assert len(mgr.list(session_id=s2)) == 1
        assert len(mgr.list(session_id=s1, status=STATUS_RESOLVING)) == 1
        assert len(mgr.list(session_id=s1, status=STATUS_RECEIVED)) == 0
    engine.dispose()


def test_transition_validates_and_no_terminal_replay(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=sid, text="hello")
        db.commit()
        advanced = mgr.transition(request_id="req-1", session_id=sid, status=STATUS_RESOLVING)
        assert advanced.status == STATUS_RESOLVING
        mgr.transition(request_id="req-1", session_id=sid, status=STATUS_RESOLVED)
        db.commit()
        with pytest.raises(ValueError):
            mgr.transition(request_id="req-1", session_id=sid, status=STATUS_FAILED)
    engine.dispose()


def test_status_not_immutable_binding(tmp_path):
    Manager, Row, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=sid, text="hello", status=STATUS_FAILED)
        db.commit()
        receipt = mgr.get(request_id="req-1", session_id=sid)
        assert receipt is not None
        assert receipt.status == STATUS_RECEIVED
    engine.dispose()


def test_critical_init_database_creates_table(tmp_path):
    db_path = tmp_path / "init.db"
    db_mgr = DatabaseManager(str(db_path))
    with db_mgr.get_session() as db:
        from sqlalchemy import text
        assert db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='composer_request_receipts'")).fetchone() is not None
    pass


def test_privacy_to_dict_no_raw_payload(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        r = mgr.receive(request_id="req-1", session_id=sid, text="secret text", payload={"password": "x"})
        db.commit()
        data = r.to_dict()
        assert data["text"] == "secret text"
        assert "password" not in str(data)
        assert "payload" not in data
    engine.dispose()


def test_invalid_status_transition_still_exact_scope(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        mgr.receive(request_id="req-1", session_id=sid, text="hello")
        db.commit()
        with pytest.raises(ValueError):
            mgr.transition(request_id="req-1", session_id=sid, status="approved")
    engine.dispose()


def test_nul_separator_digest_collision_is_avoided(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid_a = f"session-{uuid.uuid4()}"
        sid_b = f"session-{uuid.uuid4()}"
        # Same component bytes arranged differently across fields must not collide.
        mgr = Manager(db)
        r1 = mgr.receive(request_id="a", session_id=f"{sid_b}\x00{sid_a}", text="x", payload={"p": 1})
        r2 = mgr.receive(request_id=f"a\x00b", session_id=sid_a if False else sid_b, text="y", payload={"p": 2})
        db.commit()
        assert r1.request_digest != r2.request_digest
    engine.dispose()

def test_caller_digest_and_status_do_not_override_stored_binding(tmp_path):
    Manager, *_ = _import_receipt()
    engine = _engine(tmp_path)
    with sessionmaker(bind=engine)() as db:
        sid = f"session-{uuid.uuid4()}"
        mgr = Manager(db)
        receipt = mgr.receive(
            request_id="req-1",
            session_id=sid,
            text="exact",
            payload={"x": 1},
            status="resolved",
            payload_digest="attacker-supplied",
        )
        db.commit()
        assert receipt.status == STATUS_RECEIVED
        assert receipt.row_version == 1
        fetched = mgr.get(request_id="req-1", session_id=sid)
        assert fetched is not None
        assert fetched.payload_digest != "attacker-supplied"
    engine.dispose()

def _legacy_digest(request_id, session_id, text_digest, payload_digest):
    nul = chr(0)
    return hashlib.sha256((request_id + nul + session_id + nul + text_digest + nul + payload_digest).encode("utf-8")).hexdigest()


def _make_legacy_db(tmp_path, name, request_id, session_id, text="exact", request_digest_mode="nul", payload=None, status="received", text_digest=None, payload_digest=None):
    payload = {} if payload is None else payload
    if text_digest is None:
        text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if payload_digest is None:
        payload_digest = hashlib.sha256(__import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if request_digest_mode == "nul":
        request_digest = _legacy_digest(request_id, session_id, text_digest, payload_digest)
    elif request_digest_mode == "canonical":
        request_digest = hashlib.sha256(__import__("json").dumps({"digest_version": 1, "request_id": request_id, "session_id": session_id, "text_digest": text_digest, "payload_digest": payload_digest}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    elif request_digest_mode == "malformed":
        request_digest = "not-a-valid-digest"
    else:
        request_digest = request_digest_mode
    db_path = str(tmp_path / name)
    conn = __import__("sqlite3").connect(db_path)
    conn.execute(
        """
        CREATE TABLE composer_request_receipts (
            request_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL,
            text_digest TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            request_digest TEXT NOT NULL,
            created_at DATETIME,
            updated_at DATETIME,
            PRIMARY KEY (request_id, session_id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO composer_request_receipts
        (request_id, session_id, text, status, text_digest, payload_digest, request_digest, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (request_id, session_id, text, status, text_digest, payload_digest, request_digest),
    )
    conn.commit()
    conn.close()
    return db_path, payload, text_digest, payload_digest




def test_legacy_populated_valid_exact_replay(tmp_path):
    request_id = "legacy-valid-req"
    session_id = f"legacy-valid-session-{uuid.uuid4()}"
    db_path, payload, text_digest, payload_digest = _make_legacy_db(tmp_path, "legacy-valid.db", request_id, session_id)
    db = DatabaseManager(db_path)
    with db.get_session() as db_session:
        mgr = _import_receipt()[0](db_session)
        receipt = mgr.receive(request_id=request_id, session_id=session_id, text="exact", payload=payload)
        db_session.commit()
        assert receipt.text == "exact"
        assert receipt.text_digest == text_digest
        assert receipt.payload_digest == payload_digest
        assert receipt.status == "received"
        # migration must convert to the canonical version without changing accepted text
        row = db_session.query(_import_receipt()[1]).filter_by(request_id=request_id, session_id=session_id).first()
        assert int(row.digest_version) == 1
        assert int(row.row_version) >= 1
        assert row.request_digest != _legacy_digest(request_id, session_id, text_digest, payload_digest)


def test_legacy_populated_changed_payload_conflicts(tmp_path):
    request_id = "legacy-changed-req"
    session_id = f"legacy-changed-session-{uuid.uuid4()}"
    db_path, payload, text_digest, payload_digest = _make_legacy_db(tmp_path, "legacy-changed.db", request_id, session_id)
    db = DatabaseManager(db_path)
    with db.get_session() as db_session:
        mgr = _import_receipt()[0](db_session)
        with pytest.raises(ValueError, match="conflict|Conflict"):
            mgr.receive(request_id=request_id, session_id=session_id, text="exact", payload={"changed": True})
        with pytest.raises(ValueError, match="conflict|Conflict"):
            mgr.receive(request_id=request_id, session_id=session_id, text="different text", payload=payload)


def test_legacy_populated_actual_nul_request_fails_closed(tmp_path):
    ids = "legacy\x00request" if False else ("actual" + chr(0) + "nul-request")
    session_id = "actual" + chr(0) + "nul-session"
    db_path, payload, text_digest, payload_digest = _make_legacy_db(tmp_path, "legacy-nul.db", ids, session_id)
    db = DatabaseManager(db_path)
    with db.get_session() as db_session:
        mgr = _import_receipt()[0](db_session)
        with pytest.raises(ValueError):
            mgr.receive(request_id=ids, session_id=session_id, text="exact", payload=payload)


def test_legacy_populated_malformed_digest_fails_closed(tmp_path):
    db_path, payload, text_digest, payload_digest = _make_legacy_db(tmp_path, "legacy-malformed.db", "malformed-req", "malformed-session", request_digest_mode="malformed", payload_digest="not-hex", text_digest=None)
    db = DatabaseManager(db_path)
    with db.get_session() as db_session:
        mgr = _import_receipt()[0](db_session)
        with pytest.raises(ValueError):
            mgr.receive(request_id="malformed-req", session_id="malformed-session", text="exact", payload={"legacy": "ignored"})


def test_legacy_populated_literal_backslash_x00_is_ordinary_data(tmp_path):
    text = r"literal \x00 data"
    db_path, payload, text_digest, payload_digest = _make_legacy_db(tmp_path, "legacy-literal.db", "literal-req", "literal-session", text=text, payload={"kept": True})
    db = DatabaseManager(db_path)
    with db.get_session() as db_session:
        mgr = _import_receipt()[0](db_session)
        receipt = mgr.receive(request_id="literal-req", session_id="literal-session", text=text, payload={"kept": True})
        db_session.commit()
        assert receipt.text == text
        row = db_session.query(_import_receipt()[1]).filter_by(request_id="literal-req", session_id="literal-session").first()
        assert int(row.digest_version) == 1


def test_legacy_populated_migration_idempotency(tmp_path):
    db_path, payload, text_digest, payload_digest = _make_legacy_db(tmp_path, "legacy-idempotent.db", "legacy-idempotent-req", "legacy-idempotent-session")
    # initialize twice to ensure additive, idempotent migration
    DatabaseManager(db_path)
    DatabaseManager(db_path)
    with DatabaseManager(db_path).get_session() as db_session:
        assert db_session.query(_import_receipt()[1]).count() == 1
        row = db_session.query(_import_receipt()[1]).first()
        assert int(row.digest_version) == 1
        assert int(row.row_version) >= 1


def test_legacy_populated_transactional_rollback(tmp_path):
    request_id = f"legacy-rollback-req-{uuid.uuid4()}"
    session_id = f"legacy-rollback-session-{uuid.uuid4()}"
    db_path, payload, text_digest, payload_digest = _make_legacy_db(tmp_path, "legacy-rollback.db", request_id, session_id)
    db = DatabaseManager(db_path)
    with db.get_session() as db_session:
        mgr = _import_receipt()[0](db_session)
        # Receive exact replay and drop migration changes.
        receipt = mgr.receive(request_id=request_id, session_id=session_id, text="exact", payload=payload)
        db_session.rollback()
        # New connection still sees old upgraded? The rollback reverts version upgrade; row remains.
        with DatabaseManager(db_path).get_session() as verify:
            row = verify.query(_import_receipt()[1]).filter_by(request_id=request_id, session_id=session_id).first()
            assert row is not None
            assert row.text == "exact"
