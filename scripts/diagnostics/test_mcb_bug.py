#!/usr/bin/env python3
"""Reproduction script for model_context_builder.py silent-success bugs."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import asyncio
from backend.core.db_manager import DatabaseManager, init_database
from backend.models.database import Base, MemoryEntry, MemoryContextLog, set_db_manager
from backend.services.model_context_builder import ModelContextBuilder
from sqlalchemy import create_engine, event
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
        MemoryEntry(key="pref_editor", value="vscode", category="user_preference", access_count=0),
    ])
    s.commit()

print("=== Test 1: select_memory_context - access_count/last_accessed_at update ===")
# Check initial state
with db_mgr.get_session() as s:
    entries = s.query(MemoryEntry).all()
    for e in entries:
        print(f"  BEFORE: key={e.key}, access_count={e.access_count}, last_accessed_at={e.last_accessed_at}")

# Call select_memory_context - this should increment access_count and set last_accessed_at
block, stats = builder.select_memory_context("user profile", session_id="test_session")
print(f"  select_memory_context returned: block_len={len(block) if block else 0}, stats={stats}")

# Check if access_count and last_accessed_at were ACTUALLY updated
with db_mgr.get_session() as s:
    entries = s.query(MemoryEntry).all()
    for e in entries:
        print(f"  AFTER:  key={e.key}, access_count={e.access_count}, last_accessed_at={e.last_accessed_at}")

# Check for bug: if access_count is still 0 or last_accessed_at is None, the commit didn't persist
bug_found = False
with db_mgr.get_session() as s:
    entries = s.query(MemoryEntry).all()
    for e in entries:
        if e.access_count == 0 or e.last_accessed_at is None:
            print(f"  *** BUG CONFIRMED: {e.key} not updated! access_count={e.access_count}, last_accessed_at={e.last_accessed_at}")
            bug_found = True

if not bug_found:
    print("  No bug detected (access_count and last_accessed_at updated)")

print("\n=== Test 2: record_memory_context_stats - MemoryContextLog creation ===")
# Call record_memory_context_stats
builder.record_memory_context_stats("test_task_123", {"available": 3, "relevant": 2, "connected": 1, "links": 1})

# Check if MemoryContextLog was ACTUALLY created
with db_mgr.get_session() as s:
    log = s.query(MemoryContextLog).filter_by(task_id="test_task_123").first()
    if log:
        print(f"  Log created: task_id={log.task_id}, available={log.available}, relevant={log.relevant}")
    else:
        print(f"  *** BUG CONFIRMED: MemoryContextLog NOT created for task_id=test_task_123")

print("\n=== Test 3: record_memory_context_stats - log trim verification ===")
# Add more logs to exceed MEMORY_CONTEXT_LOG_RETAIN (500)
from backend.models.database import MemoryContextLog
from sqlalchemy import func

with db_mgr.get_session() as s:
    count = s.query(func.count(MemoryContextLog.id)).scalar()
    print(f"  Current log count: {count}")
    # Add 500 more logs to trigger trim
    for i in range(505):
        s.add(MemoryContextLog(task_id=f"trim_test_{i}", available=1, relevant=1, connected=0, links=0))
    s.commit()

# Trigger trim
builder.record_memory_context_stats("trigger_trim_after_505", {"available": 1, "relevant": 1})

# Verify trim happened
with db_mgr.get_session() as s:
    count = s.query(func.count(MemoryContextLog.id)).scalar()
    print(f"  Log count after adding 505: {count}")
    # Check if old logs were actually deleted
    old_log = s.query(MemoryContextLog).filter_by(task_id="trim_test_0").first()
    if old_log:
        print(f"  *** BUG CONFIRMED: Old log trim_test_0 still exists (trim didn't persist)")
    else:
        print(f"  Old logs trimmed correctly")

print("\n=== Summary ===")
if bug_found:
    print("  BUGS FOUND: Silent commit failures not detected")
else:
    print("  No bugs detected in this run")

session.close()