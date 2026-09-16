#!/usr/bin/env python3
"""Reproduction script for work_state.py silent-success bugs."""
import sys
sys.path.insert(0, r"C:\Ai\sythion")

from backend.core.db_manager import DatabaseManager, init_database
from backend.models.database import Base, Task, Objective, PlanStep, set_db_manager
from backend.services.work_state import (
    ensure_task_objective, 
    sync_objective_state_for_task,
    build_work_context,
    is_continuation_request,
    decompose_request
)
from backend.api.schemas import TaskState
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set up in-memory database with migrations
db_mgr = init_database(":memory:")
set_db_manager(db_mgr)

SessionLocal = sessionmaker(bind=db_mgr.engine)
session = SessionLocal()

print("=== Test 1: ensure_task_objective - objective + plan steps creation without verification ===")

# Create a task
task = Task(
    id="task_obj_1",
    original_request="Find remote work opportunities in Kenya",
    state=TaskState.RUNNING.value,
    normalized_goal="Find remote work opportunities in Kenya"
)
session.add(task)
session.commit()

# Call ensure_task_objective - this should create an Objective and PlanSteps
objective = ensure_task_objective(session, task)

print(f"  Returned objective: id={objective.id}, title={objective.title}, status={objective.status}")

# Check if objective was ACTUALLY persisted
session.expire_all()
obj_from_db = session.query(Objective).filter_by(id=objective.id).first()
if obj_from_db:
    print(f"  SUCCESS: Objective persisted in DB: id={obj_from_db.id}, title={obj_from_db.title}")
else:
    print(f"  *** BUG: Objective NOT persisted in DB (silent commit failure)")

# Check if plan steps were created
steps = session.query(PlanStep).filter_by(objective_id=objective.id).all()
print(f"  Plan steps count: {len(steps)}")
for s in steps:
    print(f"    step_order={s.step_order}, description={s.description[:50]}, status={s.status}")

if len(steps) == 0:
    print(f"  *** BUG: Plan steps NOT created (silent commit failure)")

print("\n=== Test 2: sync_objective_state_for_task - objective state sync without verification ===")

# Create another task with an existing objective
task2 = Task(
    id="task_sync_1",
    original_request="Continue the job search",
    state=TaskState.COMPLETED.value,
    normalized_goal="Find remote work opportunities in Kenya",
    final_result="Found 5 opportunities"
)
task2.objective_id = objective.id
session.add(task2)
session.commit()

# Call sync_objective_state_for_task
synced_obj = sync_objective_state_for_task(session, task2, event_type="tool.executed", event_data={"note": "Step 1 done"})

print(f"  Synced objective: id={synced_obj.id if synced_obj else None}, status={synced_obj.status if synced_obj else None}")

# Check if sync was ACTUALLY persisted
session.expire_all()
obj_from_db = session.query(Objective).filter_by(id=objective.id).first()
if obj_from_db:
    print(f"  SUCCESS: Objective synced in DB: status={obj_from_db.status}, summary={obj_from_db.current_summary[:50]}")
else:
    print(f"  *** BUG: Objective sync NOT persisted in DB")

print("\n=== Test 3: is_continuation_request - detection logic ===")
test_cases = [
    ("continue", True),
    ("resume", True),
    ("what of standard media", True),
    ("hi", False),
    ("hello", False),
    ("", True),
    ("open the browser", False),
]
for req, expected in test_cases:
    result = is_continuation_request(req)
    status = "OK" if result == expected else "BUG"
    print(f"  {status}: is_continuation_request('{req}') = {result} (expected {expected})")

print("\n=== Test 4: decompose_request - decomposition logic ===")
test_cases = [
    ("First, research platforms. Then, apply to jobs.", ["research platforms", "apply to jobs"]),
    ("Do X. And then do Y.", ["Do X", "do Y"]),
    ("Single action request.", []),
    ("", []),
]
for req, expected_parts in test_cases:
    result = decompose_request(req)
    # Just check if it decomposes when expected
    has_decomp = len(result) >= 2
    expected_decomp = len(expected_parts) >= 2
    status = "OK" if has_decomp == expected_decomp else "BUG"
    print(f"  {status}: decompose_request('{req[:40]}...') = {len(result)} parts")

print("\n=== Test 5: build_work_context - read-only context building ===")
context = build_work_context(session, task_id="task_obj_1", original_request="continue")
if context:
    print(f"  Context built successfully (len={len(context)})")
    print(f"  Contains: {context[:100]}...")
else:
    print(f"  Context empty (no objective found)")

session.close()