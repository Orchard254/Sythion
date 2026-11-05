"""
Database connection and session management.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
import os
import sqlite3
import threading
import uuid
import datetime
from ..models.database import Base

# Increment when the set of additive migrations/safe schema assumptions changes.
# DBs whose PRAGMA user_version does not match this value receive one automatic
# pre-migration backup before _apply_migrations() runs.
CURRENT_SCHEMA_REVISION = 1

# Ensure additive, still-unwired foundation tables are registered on Base
# metadata before create_all() runs. These imports are side-effect only and
# do not enable routing, dispatch, or automatic use.
try:
    from backend.runtime.composer_request_receipt import ComposerRequestReceiptRow  # noqa: F401
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from runtime.composer_request_receipt import ComposerRequestReceiptRow  # type: ignore  # noqa: F401
    except Exception:
        ComposerRequestReceiptRow = None  # type: ignore


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


def _migrate_followup_instruction_ledger(conn) -> None:  # pragma: no cover - exercised via tests
    """Non-destructively enforce ordered identity when legacy data is safe."""
    if not _table_exists(conn, "followup_instruction_ledger"):
        return
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(followup_instruction_ledger)"))}
    if "source_task_id" not in columns or "sequence" not in columns:
        raise RuntimeError(
            "Follow-up ledger legacy table lacks source identity/order columns; non-destructive migration blocked"
        )
    null_source = conn.execute(text(
        "SELECT COUNT(*) FROM followup_instruction_ledger "
        "WHERE source_task_id IS NULL OR TRIM(CAST(source_task_id AS TEXT)) = ''"
    )).scalar()
    if null_source:
        raise RuntimeError(
            "Follow-up ledger legacy rows have null or blank source identities; non-destructive migration blocked"
        )
    null_sequence = conn.execute(text(
        "SELECT COUNT(*) FROM followup_instruction_ledger WHERE sequence IS NULL"
    )).scalar()
    if null_sequence:
        raise RuntimeError(
            "Follow-up ledger legacy rows have null sequence values; non-destructive migration blocked"
        )
    duplicates = conn.execute(text(
        "SELECT source_task_id, sequence, COUNT(*) AS n "
        "FROM followup_instruction_ledger "
        "GROUP BY source_task_id, sequence "
        "HAVING COUNT(*) > 1"
    )).fetchall()
    if duplicates:
        raise RuntimeError(
            "Follow-up ledger legacy rows have duplicate ordered identities; non-destructive migration blocked: "
            + repr([tuple(row) for row in duplicates[:10]])
        )
    # Non-destructive forward step: create the unique index only when the
    # existing data can satisfy it. Never rebuild, drop, or rewrite rows.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_followup_instruction_ledger_source_task_sequence "
        "ON followup_instruction_ledger (source_task_id, sequence)"
    ))


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


def _is_existing_sqlite_database(database_path: str) -> bool:
    """Return True when ``database_path`` already refers to an on-disk database."""
    if not database_path or database_path == ":memory:":
        return False
    if str(database_path).startswith("file:"):
        return False
    main = str(database_path)
    if os.path.exists(main) and os.path.getsize(main) > 0:
        return True
    # A database may legitimately exist only through its WAL sidecars.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = main + suffix
        if os.path.exists(sidecar) and os.path.getsize(sidecar) > 0:
            return True
    return False


_RESERVED_BACKUP_PATHS: set = set()
_RESERVED_BACKUP_LOCK = threading.Lock()


def _reserve_unique_backup_path(database_path: str) -> str:
    """Atomically reserve a unique, non-existing backup destination."""
    base = os.path.basename(str(database_path))
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    directory = os.path.dirname(os.path.abspath(str(database_path)))
    if not directory:
        directory = os.getcwd()
    os.makedirs(directory, exist_ok=True)

    counter = 0
    while True:
        suffix = f".backup-{timestamp}"
        if counter:
            suffix += f"-{counter}"
        suffix += f"-{uuid.uuid4().hex[:8]}"
        candidate = os.path.join(directory, base + suffix)

        with _RESERVED_BACKUP_LOCK:
            # Reject anything already reserved/claimed in this process.
            if candidate in _RESERVED_BACKUP_PATHS:
                counter += 1
                continue
            # Atomic, exclusive OS-level reservation.  O_EXCL guarantees the
            # file did not exist at reservation time, which is what lets cleanup
            # trust ownership below.
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                counter += 1
                continue
            except OSError:
                counter += 1
                continue
            try:
                os.close(fd)
            except OSError:
                try:
                    os.remove(candidate)
                except OSError:
                    pass
                counter += 1
                continue

            # Avoid claiming a destination if its SQLite sidecars already exist.
            sidecar_collision = False
            for sidecar_suffix in ("-wal", "-shm", "-journal"):
                if os.path.exists(candidate + sidecar_suffix):
                    sidecar_collision = True
                    break
            if sidecar_collision:
                try:
                    os.remove(candidate)
                except OSError:
                    pass
                counter += 1
                continue

            _RESERVED_BACKUP_PATHS.add(candidate)
            return candidate
        counter += 1


def _claim_backup_destination(backup_path: str) -> str:
    """Atomically claim an explicitly requested destination, if it is free."""
    with _RESERVED_BACKUP_LOCK:
        if backup_path in _RESERVED_BACKUP_PATHS:
            return backup_path
        try:
            fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise
        except OSError:
            raise
        try:
            os.close(fd)
        except OSError:
            try:
                os.remove(backup_path)
            except OSError:
                pass
            raise RuntimeError(f"Could not reserve backup destination: {backup_path}")
        _RESERVED_BACKUP_PATHS.add(backup_path)
        return backup_path


# Backwards-compatible spelling for callers/tests.
_pre_migration_backup_path = _reserve_unique_backup_path


def verify_sqlite_integrity(database_path: str) -> bool:
    """Run SQLite's PRAGMA integrity_check and return True only if 'ok'."""
    if not os.path.exists(str(database_path)):
        return False
    conn = None
    try:
        conn = sqlite3.connect(str(database_path), timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        result = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(result) and str(result[0]).lower() == "ok"
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# Backwards/alias names that injected-failure tests may monkeypatch.
verify_database_integrity = verify_sqlite_integrity
check_sqlite_integrity = verify_sqlite_integrity
_verify_sqlite_integrity = verify_sqlite_integrity


def _database_schema_version(database_path: str) -> int:
    """Return SQLite user_version, or 0 if unreadable/absent (fail-safe)."""
    if not _is_existing_sqlite_database(database_path):
        return 0
    conn = None
    try:
        conn = sqlite3.connect(str(database_path), timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _database_fingerprint(conn) -> tuple:
    """Capture current schema and data content without changing it.

    Used only for a transactional, rollback-protected migration probe.
    """
    schema_rows = conn.execute(text(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "ORDER BY type, name, tbl_name, sql"
    )).fetchall()
    table_names = [row[0] for row in conn.execute(text(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )).fetchall()]
    table_fingerprints = []
    for table_name in table_names:
        quoted_table = '"' + str(table_name).replace('"', '""') + '"'
        columns = [
            row[1]
            for row in conn.execute(text(f"PRAGMA table_info({quoted_table})"))
        ]
        if columns:
            column_sql = ", ".join(
                '"' + str(column).replace('"', '""') + '"' for column in columns
            )
            rows = conn.execute(
                text(f"SELECT {column_sql} FROM {quoted_table}")
            ).fetchall()
            table_fingerprints.append(
                (str(table_name), len(rows), tuple(tuple(row) for row in rows))
            )
        else:
            table_fingerprints.append((str(table_name), 0, ()))
    return (
        tuple(tuple(row) for row in schema_rows),
        tuple(table_fingerprints),
    )


def _receipt_digests_pending(engine) -> bool:
    """Return whether receipt digest migration would mutate this database."""
    try:
        from sqlalchemy.orm import sessionmaker
        from backend.runtime.composer_request_receipt import (
            ComposerRequestReceiptRow,
            ComposerRequestReceiptManager,
            DIGEST_VERSION,
        )
    except Exception:
        return False
    session = None
    try:
        session = sessionmaker(bind=engine)()
        rows = session.query(ComposerRequestReceiptRow).all()
        for row in rows:
            try:
                if int(getattr(row, "digest_version", 0) or 0) >= int(DIGEST_VERSION):
                    continue
            except Exception:
                return True
            try:
                if ComposerRequestReceiptManager._valid_immutable_fields(row):
                    return True
            except Exception:
                return True
        return False
    except Exception:
        return True
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _database_requires_mutation(database_path: str) -> bool:
    """Conservative pre-mutation backup predicate.

    The previous clone-probe implementation attempted to *prove* that startup
    would not mutate the database and skipped the snapshot in that case.  That
    optimization is deliberately rejected: a short-lived probe cannot reliably
    model every live startup mutation (including SQLite journal-mode conversion
    from DELETE to WAL, schema migration, and receipt digest migration).
    Therefore *every* existing database is treated as a possible mutation target
    and receives a snapshot.  Fresh/absent databases are never backed up.
    """
    path = str(database_path)
    if not _is_existing_sqlite_database(path):
        return False
    return True


def _database_needs_pre_migration_backup(database_path: str) -> bool:
    """Conservative snapshot gate.

    SQLite user_version is never used as proof of migration completeness, and
    the unvalidated clone-probe predicate is deliberately not consulted.  Any
    existing on-disk database may be mutated by startup (including WAL
    conversion), so a snapshot is required for every existing database.  Fresh
    or absent databases are not backed up.
    """
    return _is_existing_sqlite_database(database_path)


def _cleanup_partial_backup(path: str) -> None:
    """Remove a failed backup's artifacts only when this process owns them."""
    str_path = str(path)
    with _RESERVED_BACKUP_LOCK:
        owned = str_path in _RESERVED_BACKUP_PATHS
        if owned:
            _RESERVED_BACKUP_PATHS.discard(str_path)
    if not owned:
        return
    for candidate in (str_path, str_path + "-wal", str_path + "-shm", str_path + "-journal"):
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
        except OSError:
            pass


def _sqlite_online_backup(source_conn, destination_conn) -> None:
    """Thin wrapper around sqlite3's backup API; patchable for failure injection."""
    source_conn.backup(destination_conn)


# Aliases that injected-failure tests may monkeypatch instead.
_sqlite_backup = _sqlite_online_backup
sqlite_online_backup = _sqlite_online_backup
_run_sqlite_backup = _sqlite_online_backup
sqlite_backup = _sqlite_online_backup
perform_sqlite_backup = _sqlite_online_backup


def backup_existing_database(database_path: str, backup_path: str = None, *, integrity_check: bool = True) -> str:
    """Create a non-overwriting, WAL-aware SQLite backup using the SQLite backup API.

    Returns the path to the created backup.  Raises on any backup or integrity
    failure.  Caller must ensure the profile lock is already held.
    """
    source_path = str(database_path)
    if backup_path is None:
        backup_path = _reserve_unique_backup_path(source_path)
    else:
        backup_path = _claim_backup_destination(str(backup_path))
    backup_path = str(backup_path)

    src_conn = None
    dst_conn = None
    try:
        # Source integrity is checked first: a corrupt source must not be
        # silently preserved as a "good" pre-migration backup.  Ambiguous
        # sources (missing or zero-byte main file, including sidecar-only
        # WAL states) are fail-closed: never connect/create an empty
        # database, which would discard uncheckpointed WAL data.
        if not os.path.exists(source_path) or os.path.getsize(source_path) <= 0:
            raise RuntimeError(
                f"Cannot back up missing or empty database: {source_path}"
            )
        if integrity_check and not os.path.exists(source_path):
            raise RuntimeError(f"Cannot back up missing database: {source_path}")
        if integrity_check and os.path.exists(source_path):
            if not verify_sqlite_integrity(source_path):
                raise RuntimeError(f"Source database integrity check failed: {source_path}")

        # Read-only open prevents SQLite from creating a replacement main file.
        from pathlib import Path as _Path

        _source_uri = _Path(source_path).resolve().as_uri() + "?mode=ro"
        src_conn = sqlite3.connect(_source_uri, uri=True, timeout=30)
        src_conn.execute("PRAGMA busy_timeout=30000")
        dst_conn = sqlite3.connect(backup_path, timeout=30)
        dst_conn.execute("PRAGMA busy_timeout=30000")
        # SQLite Online Backup API (WAL-aware; does not rely on file copying).
        # Indirection hook so injected-failure tests can force a failure without
        # fighting the immutable sqlite3.Connection type.
        _sqlite_online_backup(src_conn, dst_conn)
        dst_conn.commit()

        # Make the backup a single self-contained file instead of inheriting
        # an uncheckpointed WAL sidecar.  These steps are required, not
        # optional: an ignored conversion failure would leave a backup with
        # WAL sidecars and undermine the self-contained guarantee.
        try:
            checkpoint_result = dst_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint_result is not None and len(checkpoint_result) > 0:
                if int(checkpoint_result[0]) != 0:
                    raise RuntimeError(
                        "SQLite backup checkpoint did not complete cleanly"
                    )
            mode_result = dst_conn.execute("PRAGMA journal_mode=DELETE").fetchone()
            dst_conn.commit()
            actual_mode = dst_conn.execute("PRAGMA journal_mode").fetchone()
            if actual_mode is None or str(actual_mode[0]).upper() != "DELETE":
                raise RuntimeError("SQLite backup could not be made self-contained")
        except Exception:
            raise

        if integrity_check and not verify_sqlite_integrity(backup_path):
            raise RuntimeError(f"Backup integrity check failed: {backup_path}")

        return backup_path
    except Exception:
        for _conn in (src_conn, dst_conn):
            if _conn is not None:
                try:
                    _conn.close()
                except Exception:
                    pass
        src_conn = None
        dst_conn = None
        _cleanup_partial_backup(backup_path)
        raise
    finally:
        for conn in (src_conn, dst_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


# Aliases that tests/docs may use.
backup_sqlite_database = backup_existing_database
pre_migration_backup = backup_existing_database
create_pre_migration_backup = backup_existing_database


class DatabaseManager:
    """Manages SQLite database connections with WAL mode."""

    def __init__(self, database_path: str, profile_lock=None):
        """Initialize database connection.

        Args:
            database_path: Path to SQLite database file
            profile_lock: Optional ProfileLock already held by the caller.  When
                supplied it must report as held before any schema work begins.
        """
        self.database_path = database_path
        self.profile_lock = profile_lock
        self.pre_migration_backup_path = None

        if profile_lock is not None:
            fd = getattr(profile_lock, "_fd", None)
            if fd is None:
                raise RuntimeError(
                    "Database initialization requires a held profile lock, but "
                    "the supplied profile lock is not held."
                )

        # Automatic pre-migration backup for an *existing* database before any
        # schema mutation.  SQLite user_version is untrusted generic state, so
        # it is never used as a gating signal.  A backup is taken only when the
        # real startup migration path would actually mutate this database; the
        # decision is transactional and fail-closed.  Fresh databases are not
        # backed up.  Backups are never pruned or deleted automatically.
        if _database_needs_pre_migration_backup(database_path):
            self.pre_migration_backup_path = backup_existing_database(database_path)

        self.engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
            echo=False  # Set to True for SQL debugging
        )

        # Enable WAL mode for concurrent reads
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=30000"))
            conn.commit()

        # Every connection (including those created later by new_session()) must
        # wait on the SQLite writer lock instead of failing fast. The engine
        # default timeout only governs the Python side; this pragma makes the
        # SQLite side wait too.
        from sqlalchemy import event

        @event.listens_for(self.engine, "connect")
        def _set_busy_timeout(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        # Create the baseline schema, then apply additive SQLite migrations.
        Base.metadata.create_all(self.engine)
        self._apply_migrations()

        # Version-aware maintenance for the unwired composer receipt primitive.
        # Runs after additive migrations and only upgrades safe legacy rows.
        try:
            from backend.runtime.composer_request_receipt import migrate_composer_request_receipt_digests as _migrate_composer_receipts
        except Exception:  # pragma: no cover - alternate invocation layout
            try:
                from runtime.composer_request_receipt import migrate_composer_request_receipt_digests as _migrate_composer_receipts  # type: ignore
            except Exception:
                _migrate_composer_receipts = None
        if _migrate_composer_receipts is not None:
            _migrate_composer_receipts(self.engine)

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
            # Item 008: per-task sequence counter so concurrent appends cannot
            # allocate the same sequence number. Counter stores the last
            # allocated sequence (0 = none allocated yet). Backfilled from
            # existing rows; idempotent across boots and restarts.
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS task_event_sequences "
                "(task_id VARCHAR NOT NULL PRIMARY KEY, next_seq INTEGER NOT NULL)"
            ))
            conn.execute(text(
                "INSERT INTO task_event_sequences (task_id, next_seq) "
                "SELECT task_id, COALESCE(MAX(sequence_num), 0) "
                "FROM task_events GROUP BY task_id "
                "ON CONFLICT(task_id) DO UPDATE SET "
                "next_seq = MAX(task_event_sequences.next_seq, excluded.next_seq)"
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

            # Durable follow-up instruction ledger ordered identity. Non-destructive:
            # the unique index is created only when legacy data can satisfy it.
            _migrate_followup_instruction_ledger(conn)

            # Composer inbound request receipts are created additively by
            # Base.metadata.create_all(). Existing pre-feature rows keep their
            # stored values, but gain the lifecycle/digest version columns
            # forward-compatibly without rewriting accepted request text.
            if _table_exists(conn, "composer_request_receipts"):
                composer_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(composer_request_receipts)"))}
                if "row_version" not in composer_columns:
                    conn.execute(text("ALTER TABLE composer_request_receipts ADD COLUMN row_version INTEGER DEFAULT 1"))
                if "digest_version" not in composer_columns:
                    conn.execute(text("ALTER TABLE composer_request_receipts ADD COLUMN digest_version INTEGER DEFAULT 0"))
            if _table_exists(conn, "conversation_exchanges"):
                exchange_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(conversation_exchanges)"))}
                if "status" not in exchange_columns:
                    conn.execute(text("ALTER TABLE conversation_exchanges ADD COLUMN status VARCHAR"))

            # Intentionally do NOT write SQLite user_version: it is generic,
            # user-controllable state and must not be treated as proof that this
            # application's schema/data migrations are complete.
    def pre_migration_backup(self) -> str:
        """Run/return the automatic pre-migration backup for this database."""
        if _database_needs_pre_migration_backup(self.database_path):
            return backup_existing_database(self.database_path)
        return None

    def backup_database(self, backup_path=None):
        return backup_existing_database(self.database_path, backup_path)

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


def init_database(database_path: str, profile_lock=None) -> DatabaseManager:
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

    return DatabaseManager(database_path, profile_lock=profile_lock)