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
            connect_args={"check_same_thread": False},
            echo=False  # Set to True for SQL debugging
        )
        
        # Enable WAL mode for concurrent reads
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
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

            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_idempotency_key "
                "ON tasks (idempotency_key) WHERE idempotency_key IS NOT NULL"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_events_task_sequence "
                "ON task_events (task_id, sequence_num)"
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
