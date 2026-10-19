"""Migration persistence tests for Phase 11.5 §18."""

import tempfile
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.core.db_manager import DatabaseManager
from backend.models.database import Base

def test_fresh_database_has_strategy_outcomes():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "fresh.db")
        m = DatabaseManager(p)
        with m.engine.connect() as conn:
            rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_outcomes'")).fetchall()
            assert rows, "strategy_outcomes missing on fresh DB"
        m.engine.dispose()

def test_legacy_db_upgrade_creates_strategy_outcomes():
    # Simulate pre-Phase11 DB: create base tables minus strategy_outcomes
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "legacy.db")
        engine = create_engine(f"sqlite:///{p}", connect_args={"check_same_thread": False})
        # Create only old tables: drop strategy tables if exist then create without them
        # Use Base but remove strategy tables temporarily
        Base.metadata.create_all(engine)
        # Simulate legacy by dropping strategy tables
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS strategy_outcomes"))
            conn.execute(text("DROP TABLE IF EXISTS strategy_experiments"))
            conn.commit()
        engine.dispose()
        # Now run DatabaseManager upgrade — should recreate
        m = DatabaseManager(p)
        with m.engine.connect() as conn:
            rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_outcomes'")).fetchall()
            assert rows, "upgrade did not recreate strategy_outcomes"
            rows2 = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_experiments'")).fetchall()
            assert rows2, "upgrade did not recreate strategy_experiments"
        m.engine.dispose()

def test_restart_idempotent():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "restart.db")
        m1 = DatabaseManager(p)
        m1.engine.dispose()
        m2 = DatabaseManager(p)
        m2.engine.dispose()
        m3 = DatabaseManager(p)
        with m3.engine.connect() as conn:
            rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_outcomes'")).fetchall()
            assert rows
        m3.engine.dispose()

def test_backup_restore_preserves_schema():
    import shutil
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "orig.db")
        m = DatabaseManager(p)
        # insert a row
        from backend.learning.store import StrategyOutcomeStore
        from backend.learning.taxonomy import StrategyType, OutcomeClass
        s = m.new_session()
        store = StrategyOutcomeStore(s)
        store.record(task_class="command", strategy_type=StrategyType.DIRECT_EXECUTION.value, outcome=OutcomeClass.SUCCESS_VERIFIED.value, task_id="bak1", evidence_refs=["task:bak1"])
        s.close()
        m.engine.dispose()
        # backup copy
        bak = os.path.join(td, "backup.db")
        shutil.copy(p, bak)
        m2 = DatabaseManager(bak)
        s2 = m2.new_session()
        from backend.models.database import StrategyOutcome
        assert s2.query(StrategyOutcome).count() == 1
        s2.close()
        m2.engine.dispose()


def test_fresh_database_has_nullable_conversation_turns_task_id():
    """Fresh database must create conversation_turns.task_id as NULLABLE without any script."""
    from backend.models.database import ConversationTurn
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "fresh_conv.db")
        m = DatabaseManager(p)
        with m.engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(conversation_turns)")).fetchall()
            task_id_col = next((c for c in cols if c[1] == "task_id"), None)
            assert task_id_col is not None, "conversation_turns missing task_id column"
            assert task_id_col[3] == 0, f"task_id should be nullable (notnull=0), got {task_id_col[3]}"

        # Verify we can insert a conversation turn with task_id=None
        s = m.new_session()
        turn = ConversationTurn(
            session_id="fresh-session-1",
            task_id=None,
            role="user",
            summary="Hello from fresh db test",
        )
        s.add(turn)
        s.commit()

        retrieved = s.query(ConversationTurn).filter_by(session_id="fresh-session-1").first()
        assert retrieved is not None
        assert retrieved.task_id is None
        s.close()
        m.engine.dispose()


def test_legacy_database_migration_makes_conversation_turns_task_id_nullable():
    """Legacy database with task_id NOT NULL must be upgraded on DatabaseManager startup."""
    from backend.models.database import ConversationTurn
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "legacy_conv.db")
        
        # 1. Create a legacy database where conversation_turns.task_id is NOT NULL
        engine = create_engine(f"sqlite:///{p}", connect_args={"check_same_thread": False})
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE conversation_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id VARCHAR NOT NULL,
                    task_id VARCHAR NOT NULL,
                    role VARCHAR DEFAULT 'user',
                    summary TEXT NOT NULL,
                    created_at DATETIME
                )
            """))
            conn.execute(text("""
                INSERT INTO conversation_turns (session_id, task_id, role, summary)
                VALUES ('legacy-sess-1', 'task-legacy-001', 'user', 'legacy question')
            """))
        
        # Verify it was created with NOT NULL (notnull=1)
        with engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(conversation_turns)")).fetchall()
            task_id_col = next((c for c in cols if c[1] == "task_id"), None)
            assert task_id_col[3] == 1, "Expected legacy table to have notnull=1"
        engine.dispose()

        # 2. Open with DatabaseManager -> canonical migrations run
        m = DatabaseManager(p)
        with m.engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(conversation_turns)")).fetchall()
            task_id_col = next((c for c in cols if c[1] == "task_id"), None)
            assert task_id_col[3] == 0, f"Expected migrated table to have notnull=0, got {task_id_col[3]}"

        # 3. Verify existing row is preserved
        s = m.new_session()
        legacy_row = s.query(ConversationTurn).filter_by(session_id="legacy-sess-1").first()
        assert legacy_row is not None
        assert legacy_row.task_id == "task-legacy-001"
        assert legacy_row.summary == "legacy question"

        # 4. Verify new row with task_id=None succeeds
        new_turn = ConversationTurn(
            session_id="new-sess-1",
            task_id=None,
            role="user",
            summary="new question without task",
        )
        s.add(new_turn)
        s.commit()

        new_row = s.query(ConversationTurn).filter_by(session_id="new-sess-1").first()
        assert new_row is not None
        assert new_row.task_id is None

        s.close()
        m.engine.dispose()


def test_conversation_turns_migration_repeated_startup_idempotent():
    """Repeated DatabaseManager startup must be idempotent for conversation_turns."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "repeated_startup.db")
        m1 = DatabaseManager(p)
        m1.engine.dispose()
        m2 = DatabaseManager(p)
        m2.engine.dispose()
        m3 = DatabaseManager(p)
        with m3.engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(conversation_turns)")).fetchall()
            task_id_col = next((c for c in cols if c[1] == "task_id"), None)
            assert task_id_col[3] == 0
        m3.engine.dispose()


def test_backup_restore_preserves_conversation_turns_nullable():
    """Backup/restore must preserve conversation_turns schema and rows."""
    import shutil
    from backend.models.database import ConversationTurn
    with tempfile.TemporaryDirectory() as td:
        orig_p = os.path.join(td, "orig_conv.db")
        m = DatabaseManager(orig_p)
        s = m.new_session()
        s.add(ConversationTurn(session_id="sess-orig", task_id=None, role="user", summary="conv turn"))
        s.commit()
        s.close()
        m.engine.dispose()

        # Backup
        bak_p = os.path.join(td, "bak_conv.db")
        shutil.copy(orig_p, bak_p)

        # Restore / reopen
        m_bak = DatabaseManager(bak_p)
        s_bak = m_bak.new_session()
        row = s_bak.query(ConversationTurn).filter_by(session_id="sess-orig").first()
        assert row is not None
        assert row.task_id is None
        s_bak.close()
        m_bak.engine.dispose()
