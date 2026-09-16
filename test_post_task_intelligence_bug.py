#!/usr/bin/env python3
"""Reproduction script for post_task_intelligence.py silent-success bugs."""
import sys
sys.path.insert(0, r"C:\Ai\sythion")

import asyncio
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

print("=== Test 1: save_conversation_turn - ConversationTurn commit without verification ===")

# Create a completed task
task = Task(
    id="task_conv_1",
    original_request="my name is Alice and I prefer VS Code",
    state=TaskState.COMPLETED.value,
    final_result="Switched default text editor to VS Code"
)
session.add(task)
session.commit()

# Call save_conversation_turn
asyncio.run(save_conversation_turn(session, "task_conv_1", task))

# Check if ConversationTurn rows were ACTUALLY created
turns = session.query(ConversationTurn).filter_by(task_id="task_conv_1").all()
print(f"  ConversationTurn count: {len(turns)}")
for t in turns:
    print(f"    role={t.role}, summary={t.summary[:50]}")

if len(turns) >= 2:
    print("  SUCCESS: ConversationTurn rows created")
else:
    print("  *** BUG: ConversationTurn rows NOT created (silent commit failure)")

# Check if memory entries were extracted
name_mem = session.query(MemoryEntry).filter_by(key="user_name").first()
editor_mem = session.query(MemoryEntry).filter_by(key="preferred_editor").first()
print(f"  user_name memory: {name_mem.value if name_mem else 'NOT FOUND'}")
print(f"  preferred_editor memory: {editor_mem.value if editor_mem else 'NOT FOUND'}")

print("\n=== Test 2: consolidate_memory_from_trace - best-effort consolidation ===")
# This is best-effort, so we just verify it runs without error
# Need at least 2 conversation turns in the session
from backend.core.session import get_current_session_id, set_current_session_id
sess_id = set_current_session_id("test_session_consolidate")

task2 = Task(
    id="task_conv_2",
    original_request="remember that I like pizza",
    state=TaskState.COMPLETED.value,
    final_result="Noted your preference"
)
session.add(task2)
session.commit()

asyncio.run(save_conversation_turn(session, "task_conv_2", task2))

# Now try consolidation
asyncio.run(consolidate_memory_from_trace(session, "task_conv_2"))

print("  Consolidation completed (best-effort)")

session.close()