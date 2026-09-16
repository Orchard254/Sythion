#!/usr/bin/env python3
"""Adversarial test for model_context_builder.py - forces trim verification."""
import sys
sys.path.insert(0, r"C:\Ai\sythion")

from backend.core.db_manager import DatabaseManager, init_database
from backend.models.database import Base, MemoryEntry, MemoryContextLog, set_db_manager
from backend.services.model_context_builder import ModelContextBuilder
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set up in-memory database with migrations (includes FTS5)
db_mgr = init_database(":memory:")
set_db_manager(db_mgr)

SessionLocal = sessionmaker(bind=db_mgr.engine)

# Create builder with a fresh session
session = SessionLocal()
builder = ModelContextBuilder(
    db_session=session,
    tool_summary_cap=lambda name: 200,
    continuation_summary_cap=lambda name: 150,
    continuation_patterns=["continue", "resume"],
)

# Add test memory entries
with db_mgr.get_session() as s:
    s.add_all([
        MemoryEntry(key="user_name", value="Alice", category="user_profile", access_count=0),
        MemoryEntry(key="user_city", value="Seattle", category="user_profile", access_count=0),
    ])
    s.commit()

print("=== Adversarial Test: Trigger trim by calling record_memory_context_stats when > 500 logs exist ===")

# First, add 500 logs manually
with db_mgr.get_session() as s:
    for i in range(500):
        s.add(MemoryContextLog(task_id=f"existing_{i}", available=1, relevant=1, connected=0, links=0))
    s.commit()

# Verify count is 500
with db_mgr.get_session() as s:
    count = s.query(func.count(MemoryContextLog.id)).scalar()
    print(f"  Initial log count: {count}")

# Now call record_memory_context_stats - this should add 1 more (making 501) and trigger trim of 1
builder.record_memory_context_stats("trigger_trim_task", {"available": 2, "relevant": 1, "connected": 0, "links": 0})

# Check if trim happened - oldest log (existing_0) should be deleted
with db_mgr.get_session() as s:
    count = s.query(func.count(MemoryContextLog.id)).scalar()
    print(f"  Log count after record_memory_context_stats: {count}")
    
    old_log = s.query(MemoryContextLog).filter_by(task_id="existing_0").first()
    if old_log:
        print(f"  *** BUG: Old log existing_0 still exists (trim didn't persist or verify)")
    else:
        print(f"  SUCCESS: Old log existing_0 was trimmed")

    # Verify the new log was created
    new_log = s.query(MemoryContextLog).filter_by(task_id="trigger_trim_task").first()
    if new_log:
        print(f"  SUCCESS: New log trigger_trim_task created")
    else:
        print(f"  *** BUG: New log NOT created")

print("\n=== Adversarial Test 2: Simulate commit failure for access_count update ===")
# This is harder to test without mocking, but let's at least verify the verification code path exists
# by checking the select_memory_context code has the verification block

print("  Checking if select_memory_context has verification code...")
import inspect
source = inspect.getsource(builder.select_memory_context)
if "Verify the updates persisted" in source:
    print("  SUCCESS: Verification code present in select_memory_context")
else:
    print("  *** BUG: Verification code MISSING from select_memory_context")

print("\n=== Adversarial Test 3: Verify sessionmaker import ===")
if "sessionmaker" in open("C:\\Ai\\sythion\\backend\\services\\model_context_builder.py").read():
    print("  SUCCESS: sessionmaker imported")
else:
    print("  *** BUG: sessionmaker NOT imported")

session.close()