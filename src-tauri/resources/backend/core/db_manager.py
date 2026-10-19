"""
Database connection and session management.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
import os

from ..models.database import Base


def _table_exists(conn, table_name: str) -> bool:
    rows = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
    ), {"t": table_name}).fetchall()
    return bool(rows)


_CONVERSATION_TURNS_DDL = """
CREATE TABLE conversation_turns_new (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    session_id VARCHAR NOT NULL,
    task_id VARCHAR,
    role VARCHAR DEFAULT 'user',
    summary TEXT NOT NULL,
    created_at DATETIME
)
"""


def _rebuild_conversation_turns_table(conn) -> None:
    """Rebuild ``conversation_turns`` so task_id is nullable (Phase 13B)."""
    conn.execute(text(_CONVERSATION_TURNS_DDL))
    conn.execute(text("""
        INSERT INTO conversation_turns_new (id, session_id, task_id, role, summary, created_at)
        SELECT id, session_id, task_id, role, summary, created_at FROM conversation_turns
    """))
    conn.execute(text("DROP TABLE conversation_turns"))
    conn.execute(text("ALTER TABLE conversation_turns_new RENAME TO conversation_turns"))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_conversation_turns_session_id ON conversation_turns (session_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_conversation_turns_created_at ON conversation_turns (created_at)"
    ))


class DatabaseManager:
    """Manages SQLite database connections with WAL mode."""

    def __init__(self, database_path: str):
        """Initialize database connection.

        Args:
            database_path: Path to SQLite database file
        """
        self.database_path = database_path
        self.engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
            echo=False  # Set to True for SQL debugging
        )

        # Enable WAL mode for concurrent reads
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=5000"))
            conn.commit()

        # Create the baseline schema, then apply additive SQLite migrations.
        Base.metadata.create_all(self.engine)
        self._apply_migrations()

        # Session factory
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

    def _apply_migrations(self) -> None:
        """Apply small, transactional, forward-only SQLite schema updates."""
        with self.engine.begin() as conn:
            task_columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(tasks)"))
            }
            if "idempotency_key" not in task_columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN idempotency_key VARCHAR"))
            if "objective_id" not in task_columns:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN objective_id VARCHAR"))

            # Phase F: skills gain a category column (existing DBs).
            skill_columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(skills)"))
            }
            if "category" not in skill_columns:
                conn.execute(text("ALTER TABLE skills ADD COLUMN category VARCHAR DEFAULT 'custom'"))

            # Phase F: skill versions gain a content hash; proposals carry the
            # requested live skill id. New tables are created by create_all.
            sv_columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(skill_versions)"))
            }
            if "definition_hash" not in sv_columns:
                conn.execute(text("ALTER TABLE skill_versions ADD COLUMN definition_hash VARCHAR"))
            prop_columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(proposed_skills)"))
            } if _table_exists(conn, "proposed_skills") else set()
            if "skill_id" not in prop_columns:
                conn.execute(text("ALTER TABLE proposed_skills ADD COLUMN skill_id VARCHAR"))

            # Phase F: objectives gain completion_criteria, criteria_verified, parent_objective_id
            obj_columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(objectives)"))
            } if _table_exists(conn, "objectives") else set()
            if "completion_criteria" not in obj_columns:
                conn.execute(text("ALTER TABLE objectives ADD COLUMN completion_criteria TEXT"))
            if "criteria_verified" not in obj_columns:
                conn.execute(text("ALTER TABLE objectives ADD COLUMN criteria_verified BOOLEAN DEFAULT 0"))
            if "parent_objective_id" not in obj_columns:
                conn.execute(text("ALTER TABLE objectives ADD COLUMN parent_objective_id VARCHAR"))

            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_idempotency_key "
                "ON tasks (idempotency_key) WHERE idempotency_key IS NOT NULL"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_events_task_sequence "
                "ON task_events (task_id, sequence_num)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_tasks_objective_id "
                "ON tasks (objective_id)"
            ))

            # Activity-history queries filter task_events / conversation_turns
            # by created_at time range (activity_recall tool); index the range
            # key so cross-task time queries are not unindexed table scans.
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_task_events_created_at "
                "ON task_events (created_at)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_conversation_turns_created_at "
                "ON conversation_turns (created_at)"
            ))

            # Phase 13B: conversation_turns.task_id must be nullable for pure conversations.
            # SQLite does not support ALTER COLUMN, so if task_id has NOT NULL (notnull == 1),
            # rebuild the table in-place preserving all existing rows and indexes.
            if _table_exists(conn, "conversation_turns"):
                turn_cols = conn.execute(text("PRAGMA table_info(conversation_turns)")).fetchall()
                task_id_col = next((c for c in turn_cols if c[1] == "task_id"), None)
                if task_id_col and task_id_col[3] == 1:  # notnull == 1
                    _rebuild_conversation_turns_table(conn)

            # Edge decay review: memory edges gain a lifecycle status column
            # ("active"/"stale"/"removed"), mirroring proposed_skills.status.
            edge_columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(memory_edges)"))
            } if _table_exists(conn, "memory_edges") else set()
            if "status" not in edge_columns:
                conn.execute(text(
                    "ALTER TABLE memory_edges ADD COLUMN status VARCHAR DEFAULT 'active'"
                ))

            # Memory entries gain access_count for importance weighting
            me_columns = {
                row[1] for row in conn.execute(text("PRAGMA table_info(memory_entries)"))
            } if _table_exists(conn, "memory_entries") else set()
            if "access_count" not in me_columns:
                conn.execute(text(
                    "ALTER TABLE memory_entries ADD COLUMN access_count INTEGER DEFAULT 0"
                ))
            if "last_accessed_at" not in me_columns:
                conn.execute(text(
                    "ALTER TABLE memory_entries ADD COLUMN last_accessed_at TIMESTAMP"
                ))

            # FTS5 virtual table for fast memory entry search (content, key, category)
            fts_exists = conn.execute(text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_entries_fts'"
            )).fetchone()
            if not fts_exists:
                conn.execute(text(
                    "CREATE VIRTUAL TABLE memory_entries_fts USING fts5("
                    "  key, category, value, content='memory_entries', content_rowid='id'"
                    ")"
                ))
                # Populate FTS table with existing data
                conn.execute(text(
                    "INSERT INTO memory_entries_fts(rowid, key, category, value) "
                    "SELECT id, key, category, value FROM memory_entries"
                ))
                # Triggers to keep FTS in sync
                conn.execute(text(
                    "CREATE TRIGGER memory_entries_ai AFTER INSERT ON memory_entries BEGIN"
                    "  INSERT INTO memory_entries_fts(rowid, key, category, value)"
                    "  VALUES (new.id, new.key, new.category, new.value);"
                    " END;"
                ))
                conn.execute(text(
                    "CREATE TRIGGER memory_entries_ad AFTER DELETE ON memory_entries BEGIN"
                    "  INSERT INTO memory_entries_fts(memory_entries_fts, rowid, key, category, value)"
                    "  VALUES ('delete', old.id, old.key, old.category, old.value);"
                    " END;"
                ))
                conn.execute(text(
                    "CREATE TRIGGER memory_entries_au AFTER UPDATE ON memory_entries BEGIN"
                    "  INSERT INTO memory_entries_fts(memory_entries_fts, rowid, key, category, value)"
                    "  VALUES ('delete', old.id, old.key, old.category, old.value);"
                    "  INSERT INTO memory_entries_fts(rowid, key, category, value)"
                    "  VALUES (new.id, new.key, new.category, new.value);"
                    " END;"
                ))

            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_objectives_status "
                "ON objectives (status)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plan_steps_objective_order "
                "ON plan_steps (objective_id, step_order)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_plan_steps_status "
                "ON plan_steps (status)"
            ))

    @contextmanager
    def get_session(self) -> Session:
        """Get a database session context manager.

        Yields:
            SQLAlchemy Session object
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def new_session(self) -> Session:
        """Create an independent session not tied to a request scope.

        Caller owns the session and must call .close() when done. This is
        used for background task execution that outlives the HTTP request.
        """
        return self.SessionLocal()


def init_database(database_path: str) -> DatabaseManager:
    """Initialize the database and return manager instance.

    Args:
        database_path: Path to SQLite database file

    Returns:
        DatabaseManager instance
    """
    # Ensure directory exists
    db_dir = os.path.dirname(database_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, mode=0o700)  # User-only permissions

    return DatabaseManager(database_path)