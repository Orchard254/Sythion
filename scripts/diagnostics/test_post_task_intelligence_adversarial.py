#!/usr/bin/env python3
"""Adversarial test for post_task_intelligence.py - tests verification behavior."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import asyncio
from unittest.mock import patch, MagicMock
from backend.core.db_manager import DatabaseManager, init_database
from backend.models.database import Base, Task, ConversationTurn, MemoryEntry, set_db_manager
from backend.services.post_task_intelligence import save_conversation_turn, consolidate_memory_from_trace
from backend.api.schemas import TaskState
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set up in-memory database with migrations
db_mgr = init_database(":memory:")
set_db_manager(db_mgr)

SessionLocal = sessionmaker(bind=db_mgr.engine)
session = SessionLocal()

async def run_tests():
    print("=== Adversarial Test 1: save_conversation_turn verification failure returns early ===")

    # Create a completed task
    task = Task(
        id="task_conv_1",
        original_request="my name is Alice and I prefer VS Code",
        state=TaskState.COMPLETED.value,
        final_result="Switched default text editor to VS Code"
    )
    session.add(task)
    session.commit()

    # Patch the module's sessionmaker to simulate read-back failure for conversation turns
    mock_session_instance = MagicMock()
    mock_query = MagicMock()
    mock_query.filter_by.return_value.all.return_value = []  # Verification fails - no turns found
    mock_session_instance.query.return_value = mock_query
    mock_session_instance.__enter__ = lambda self: mock_session_instance
    mock_session_instance.__exit__ = lambda *args: None

    mock_sessionmaker_class = MagicMock()
    mock_sessionmaker_class.return_value = mock_session_instance

    with patch('backend.services.post_task_intelligence.sessionmaker', mock_sessionmaker_class):
        # Call save_conversation_turn - should return early due to verification failure
        await save_conversation_turn(session, "task_conv_1", task)

    print("  save_conversation_turn completed (returned early due to verification failure)")

    # Check if conversation turns were actually created (they might have been committed before verification)
    turns = session.query(ConversationTurn).filter_by(task_id="task_conv_1").all()
    print(f"  ConversationTurn count after verification failure: {len(turns)}")

    print("\n=== Adversarial Test 2: Check sessionmaker import ===")
    with open(REPO_ROOT / "backend" / "services" / "post_task_intelligence.py", "r") as f:
        content = f.read()
        if "sessionmaker" in content:
            print("  SUCCESS: sessionmaker imported")
        else:
            print("  *** BUG: sessionmaker NOT imported")

    # Check if the fix uses sessionmaker directly (not via skill_proposals chain)
    if "from sqlalchemy.orm import sessionmaker" in content or "from sqlalchemy.orm import Session, sessionmaker" in content:
        print("  SUCCESS: sessionmaker imported directly")
    else:
        print("  WARNING: sessionmaker may be imported via chain")

asyncio.run(run_tests())
session.close()