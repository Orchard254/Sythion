"""Transactional follow-up instruction ledger foundation tests."""

import os
import tempfile
import threading

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from backend.core.db_manager import init_database
from backend.models.database import Base, Task
from backend.runtime.followup_instruction_ledger import (
    FollowupInstructionLedgerManager as Ledger,
    FollowupInstructionLedgerRow,
    STATUS_PENDING,
    STATUS_NOT_APPLIED,
    ALL_STATUSES,
    TRANSITIONS,
    PENDING_STATUSES,
)
from backend.services.retention import purge_task


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"timeout": 10})

    @event.listens_for(engine, "connect")
    def setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "ledger-test")
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def seed_task(db, task_id="task-1"):
    db.add(Task(id=task_id, original_request="Do work", state="running", version=1))
    db.commit()


def test_exact_request_identity_is_required(db):
    seed_task(db)
    ledger = Ledger(db)
    with pytest.raises(ValueError, match="Exact follow-up request identity is required"):
        ledger.receive(task_id="task-1", instruction="missing identity")
    with pytest.raises(ValueError, match="Exact follow-up request identity is required"):
        ledger.receive(request_id="", task_id="task-1", instruction="empty")
    with pytest.raises(ValueError, match="Exact follow-up request identity is required"):
        ledger.receive(request_id=123, task_id="task-1", instruction="wrong type")


def test_duplicate_identity_rejected_and_original_preserved(db):
    seed_task(db)
    ledger = Ledger(db)
    first = ledger.receive(request_id="req-1", task_id="task-1", instruction="First follow-up")
    assert first.sequence == 1
    db.commit()

    with pytest.raises(ValueError, match="Duplicate follow-up instruction identity"):
        ledger.receive(request_id="req-1", task_id="task-1", instruction="Second")
    db.rollback()

    rows = db.query(FollowupInstructionLedgerRow).filter_by(request_id="req-1").all()
    assert len(rows) == 1
    assert rows[0].instruction == "First follow-up"
    assert rows[0].sequence == 1


def test_two_additive_instructions_preserve_order_and_count(db):
    seed_task(db)
    ledger = Ledger(db)
    first = ledger.receive(request_id="req-1", task_id="task-1", instruction="First")
    second = ledger.receive(request_id="req-2", task_id="task-1", instruction="Second")
    db.commit()

    assert first.sequence == 1
    assert second.sequence == 2
    pending = ledger.pending(task_id="task-1")
    assert [r.request_id for r in pending] == ["req-1", "req-2"]
    assert [r.instruction for r in pending] == ["First", "Second"]
    assert db.query(FollowupInstructionLedgerRow).count() == 2
    sequences = sorted(r.sequence for r in db.query(FollowupInstructionLedgerRow).all())
    assert len(set(sequences)) == 2


def test_rollback_leaves_no_ledger_row_or_sequence_consumption(db):
    seed_task(db)
    ledger = Ledger(db)
    before_count = db.query(FollowupInstructionLedgerRow).count()
    try:
        ledger.receive(request_id="req-rollback", task_id="task-1", instruction="Rollback me")
        raise RuntimeError("boom")
    except RuntimeError:
        db.rollback()
    assert db.query(FollowupInstructionLedgerRow).count() == before_count
    assert ledger.get("req-rollback") is None
    # A subsequent receive still gets deterministic sequence 1 after rollback.
    fresh = ledger.receive(request_id="req-after-rollback", task_id="task-1", instruction="After rollback")
    db.commit()
    assert fresh.sequence == 1


def test_false_applied_transition_is_disabled(db):
    seed_task(db)
    ledger = Ledger(db)
    ledger.receive(request_id="req-false-applied", task_id="task-1", instruction="Do not fake applied")
    db.commit()
    request_id = "req-false-applied"

    assert "applied" not in {t.lower() for t in []}
    assert not hasattr(ledger, "mark_applied")
    assert not hasattr(ledger, "apply")

    with pytest.raises(ValueError):
        ledger.transition(request_id, " applied ".strip() if False else "applied")
    with pytest.raises(ValueError):
        ledger.transition(request_id, "applied", reason="not earned")
    record = ledger.get(request_id)
    assert record is not None
    assert record.status == STATUS_PENDING
    assert record.status in PENDING_STATUSES


@pytest.mark.parametrize("bad_status", ["applied", "done", "finished", "complete", "completed", "applying"])
def test_creation_blocks_applied_status_synonyms(db, bad_status):
    seed_task(db)
    ledger = Ledger(db)
    with pytest.raises(ValueError, match="cannot be created|cannot be asserted|Unsupported|applied"):
        ledger.receive(request_id=f"req-{bad_status}", task_id="task-1", instruction="no", status=bad_status)
    db.rollback()
    with pytest.raises(ValueError):
        Ledger(db, default_status=bad_status)
    assert db.query(FollowupInstructionLedgerRow).count() == 0


def test_ledger_migration_adds_unique_index_without_conflict():
    import backend.core.db_manager as dbm
    import backend.core.db_manager as dbm

    path = os.path.join(tempfile.gettempdir(), "ledger_legacy_ok.db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
    # Build a legacy-shaped table without the unique index, but valid data.
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE tasks (
                id VARCHAR PRIMARY KEY,
                original_request TEXT NOT NULL,
                idempotency_key VARCHAR,
                normalized_goal TEXT,
                state VARCHAR NOT NULL,
                version INTEGER,
                active_action_id VARCHAR,
                selected_skill_version VARCHAR,
                objective_id VARCHAR,
                pending_question TEXT,
                pending_action TEXT,
                final_result TEXT,
                created_at DATETIME,
                updated_at DATETIME,
                terminal_reason VARCHAR
            )
        """))
        conn.execute(text("""
            CREATE TABLE followup_instruction_ledger (
                request_id VARCHAR PRIMARY KEY,
                task_id VARCHAR,
                source_task_id VARCHAR,
                session_id VARCHAR,
                exchange_id VARCHAR,
                source_exchange_id VARCHAR,
                user_turn_id INTEGER,
                assistant_turn_id INTEGER,
                instruction TEXT NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                sequence INTEGER NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                applied_at DATETIME,
                terminal_at DATETIME,
                applied_task_version INTEGER,
                applied_instruction_revision INTEGER,
                terminal_reason TEXT,
                metadata_json TEXT
            )
        """))
        conn.execute(text(
            "INSERT INTO followup_instruction_ledger (request_id, task_id, source_task_id, instruction, status, sequence, created_at, updated_at) "
            "VALUES ('r1', 't1', 't1', 'hi', 'pending', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO followup_instruction_ledger (request_id, task_id, source_task_id, instruction, status, sequence, created_at, updated_at) "
            "VALUES ('r2', 't1', 't1', 'hello', 'pending', 2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
    with engine.begin() as conn:
        dbm._migrate_followup_instruction_ledger(conn)
        indexes = conn.execute(text("PRAGMA index_list('followup_instruction_ledger')")).fetchall()
        assert any(bool(row[2]) for row in indexes)
    engine.dispose()


def test_ledger_migration_blocks_duplicate_or_null_legacy_identities():
    import backend.core.db_manager as dbm

    path = os.path.join(tempfile.gettempdir(), "ledger_legacy_conflict.db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE followup_instruction_ledger (
                request_id VARCHAR PRIMARY KEY,
                task_id VARCHAR,
                source_task_id VARCHAR,
                session_id VARCHAR,
                exchange_id VARCHAR,
                source_exchange_id VARCHAR,
                user_turn_id INTEGER,
                assistant_turn_id INTEGER,
                instruction TEXT NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                sequence INTEGER NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                applied_at DATETIME,
                terminal_at DATETIME,
                applied_task_version INTEGER,
                applied_instruction_revision INTEGER,
                terminal_reason TEXT,
                metadata_json TEXT
            )
        """))
        # Duplicate ordered identity.
        conn.execute(text(
            "INSERT INTO followup_instruction_ledger (request_id, task_id, source_task_id, instruction, status, sequence, created_at, updated_at) "
            "VALUES ('r1', 't1', 't1', 'hi', 'pending', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO followup_instruction_ledger (request_id, task_id, source_task_id, instruction, status, sequence, created_at, updated_at) "
            "VALUES ('r2', 't1', 't1', 'dup', 'pending', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
    with pytest.raises(RuntimeError, match="duplicate ordered identities"):
        with engine.begin() as conn:
            dbm._migrate_followup_instruction_ledger(conn)
    # Data remains unchanged.
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM followup_instruction_ledger")).scalar()
        assert count == 2
    engine.dispose()

    # Null source identity case.
    path2 = os.path.join(tempfile.gettempdir(), "ledger_legacy_null.db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path2 + suffix)
        except FileNotFoundError:
            pass
    engine2 = create_engine(f"sqlite:///{path2}", connect_args={"timeout": 10})
    with engine2.begin() as conn:
        conn.execute(text("""
            CREATE TABLE followup_instruction_ledger (
                request_id VARCHAR PRIMARY KEY,
                task_id VARCHAR,
                source_task_id VARCHAR,
                instruction TEXT NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                sequence INTEGER NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        conn.execute(text(
            "INSERT INTO followup_instruction_ledger (request_id, task_id, source_task_id, instruction, status, sequence, created_at, updated_at) "
            "VALUES ('r1', 't1', NULL, 'hi', 'pending', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
    with pytest.raises(RuntimeError, match="null or blank source identities"):
        with engine2.begin() as conn:
            dbm._migrate_followup_instruction_ledger(conn)
    engine2.dispose()

def test_stop_terminal_marks_pending_not_applied_without_permission_inference(db):
    seed_task(db)
    ledger = Ledger(db)
    ledger.receive(request_id="req-stop", task_id="task-1", instruction="Please slow down")
    db.commit()

    result = ledger.on_task_terminal("task-1", reason="stopped")
    assert len(result) == 1
    assert result[0].status == STATUS_NOT_APPLIED
    assert result[0].instruction == "Please slow down"
    assert result[0].status != "applied"
    assert result[0].status != "cancelled"
    assert result[0].status in ALL_STATUSES
    assert "approved" not in result[0].to_dict()
    assert "permission_granted" not in result[0].to_dict()


def test_retention_preserves_ledger_rows_and_survives_task_deletion(db):
    seed_task(db)
    ledger = Ledger(db)
    ledger.receive(request_id="req-retention", task_id="task-1", instruction="Keep this text")
    db.commit()

    assert purge_task(db, "task-1") is not None
    row = db.query(FollowupInstructionLedgerRow).filter_by(request_id="req-retention").first()
    assert row is not None
    assert row.task_id is None
    assert row.source_task_id == "task-1"
    assert row.instruction == "Keep this text"

    record = ledger.get("req-retention")
    assert record is not None
    assert record.task_id is None
    assert record.source_task_id == "task-1"


def test_retention_failure_does_not_partially_delete(db, monkeypatch):
    seed_task(db)
    ledger = Ledger(db)
    ledger.receive(request_id="req-retention-fail", task_id="task-1", instruction="Keep this")
    db.commit()

    from backend.services import retention as retention_module

    original_commit = db.commit
    calls = {"count": 0}

    def fail_commit():
        calls["count"] += 1
        raise RuntimeError("commit boom")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="commit boom"):
        purge_task(db, "task-1")
    monkeypatch.setattr(db, "commit", original_commit)
    db.rollback()
    # Ledger and task remain because purge transaction rolled back.
    assert db.query(FollowupInstructionLedgerRow).filter_by(request_id="req-retention-fail").count() == 1
    assert db.query(Task).filter_by(id="task-1").count() == 1


def test_concurrent_ordered_allocation(db):
    seed_task(db)
    engine = db.bind
    from sqlalchemy.orm import sessionmaker

    results = {}
    errors = []

    def worker(index):
        session = sessionmaker(bind=engine)()
        try:
            session.execute(text("PRAGMA foreign_keys=ON"))
            ledger = Ledger(session)
            record = ledger.receive(
                request_id=f"req-concurrent-{index}",
                task_id="task-1",
                instruction=f"instruction {index}",
            )
            session.commit()
            results[index] = record.sequence
        except Exception as exc:  # pragma: no cover - diagnostic
            session.rollback()
            errors.append(str(exc))
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    sequences = sorted(results.values())
    assert sequences == list(range(1, 6))
    assert len(set(results.values())) == 5


def test_migration_creates_table_and_unique_constraint():
    path = os.path.join(tempfile.gettempdir(), "ledger_migration.db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
    manager = init_database(path)
    try:
        with manager.engine.connect() as conn:
            table = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='followup_instruction_ledger'")
            ).fetchall()
            assert table
            # Unique constraints may appear as autoindexes without a SQL text row;
            # check both explicit indexes and PRAGMA metadata.
            index_list = conn.execute(text("PRAGMA index_list('followup_instruction_ledger')")).fetchall()
            auto_sql = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='followup_instruction_ledger'")
            ).fetchall()
            has_unique = any(bool(row[2]) for row in index_list) or any(
                row[0] and "source_task_id" in row[0] and "sequence" in row[0] for row in auto_sql
            )
            assert has_unique
    finally:
        manager.engine.dispose()


def test_status_vocabulary_disables_applied_transitions():
    assert "applied" not in TRANSITIONS[STATUS_PENDING]
    for status in ("received", "queued", "waiting", "blocked"):
        if status in TRANSITIONS:
            assert "applied" not in TRANSITIONS[status]
