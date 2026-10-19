"""
Migration helper: Make task_id nullable in conversation_turns for Phase 13B.

NOTE: The authoritative schema migration mechanism is DatabaseManager startup
in backend/core/db_manager.py (_apply_migrations). This standalone script is an
operational CLI helper that delegates directly to DatabaseManager to avoid
duplicate migration authorities.
"""

import os
import sys
from backend.core.db_manager import DatabaseManager


def migrate_database(db_path: str):
    """Apply canonical DatabaseManager migrations to the given database."""
    print(f"Applying canonical migrations to database: {db_path}")
    if not os.path.exists(db_path):
        print(f"Database does not exist at {db_path}")
        return

    manager = DatabaseManager(db_path)
    manager.engine.dispose()
    print("Canonical migrations completed successfully!")

if __name__ == "__main__":
    # Default database path
    db_path = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Sythion (Synth)",
        "sythion.db"
    )
    
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    
    migrate_database(db_path)
