#!/usr/bin/env python3
"""Adversarial test for work_state.py - tests verification behavior."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from unittest.mock import patch, MagicMock
from backend.core.db_manager import DatabaseManager, init_database
from backend.models.database import Base, Task, Objective, PlanStep, set_db_manager
from backend.services.work_state import (
    ensure_task_objective, 
    sync_objective_state_for_task,
    build_work_context,
)
from backend.api.schemas import TaskState
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime

# Set up in-memory database with migrations
db_mgr = init_database(":memory:")
set_db_manager(db_mgr)

SessionLocal = sessionmaker(bind=db_mgr.engine)
session = SessionLocal()

print("=== Adversarial Test 1: ensure_task_objective verification failure returns None ===")

# Create a task
task = Task(
    id="task_obj_adv_1",
    original_request="Test objective creation",
    state=TaskState.RUNNING.value,
    normalized_goal="Test objective creation"
)
session.add(task)
session.commit()

# Properly mock the sessionmaker pattern:
# engine = db.bind
# SessionLocal = sessionmaker(bind=engine)
# with SessionLocal() as verify_session:

# The sessionmaker is called with bind=engine, returns a class
# Then that class is called to create an instance
mock_verify_session = MagicMock()
mock_query = MagicMock()
mock_query.filter_by.return_value.first.return_value = None  # Verification fails - objective not found
mock_query.filter_by.return_value.all.return_value = []  # For steps
mock_verify_session.query.return_value = mock_query
mock_verify_session.__enter__ = lambda self: mock_verify_session
mock_verify_session.__exit__ = lambda *args: None

# Mock class that sessionmaker(bind=engine) returns
mock_session_class = MagicMock()
mock_session_class.return_value = mock_verify_session

# Mock the sessionmaker function itself
def mock_sessionmaker_func(*args, **kwargs):
    return mock_session_class

with patch('backend.services.work_state.sessionmaker', mock_sessionmaker_func):
    objective = ensure_task_objective(session, task)
    if objective is None:
        print("  Fix behavior: returns None when verification fails")
    else:
        print(f"  Pre-fix behavior: returned objective (verification didn't fail)")

print("\n=== Adversarial Test 2: sync_objective_state_for_task verification failure ===")

# Create a task with an objective
task2 = Task(
    id="task_sync_adv_1",
    original_request="Test sync",
    state=TaskState.COMPLETED.value,
    normalized_goal="Test sync",
    final_result="Done"
)
session.add(task2)
session.commit()

# Create an objective manually
obj = Objective(
    id="obj_adv_1",
    title="Test Objective",
    purpose="Test",
    status="active",
    priority=3,
    current_summary="Test",
    last_task_id=task2.id,
    created_at=datetime.utcnow(),
    updated_at=datetime.utcnow(),
)
session.add(obj)
session.commit()

task2.objective_id = obj.id
session.commit()

# Need to also add a plan step for the step query to find
step = PlanStep(
    objective_id=obj.id,
    step_order=1,
    description="Test step",
    status="pending",
    linked_task_id=task2.id,
    created_at=datetime.utcnow(),
    updated_at=datetime.utcnow(),
)
session.add(step)
session.commit()

with patch('backend.services.work_state.sessionmaker', mock_sessionmaker_func):
    result = sync_objective_state_for_task(session, task2, event_type="tool.executed")
    if result is None:
        print("  Fix behavior: returns None when verification fails")
    else:
        print(f"  Pre-fix behavior: returned objective (verification didn't fail)")

print("\n=== Check sessionmaker import ===")
with open(REPO_ROOT / "backend" / "services" / "work_state.py", "r") as f:
    content = f.read()
    if "sessionmaker" in content:
        print("  SUCCESS: sessionmaker imported")
    else:
        print("  BUG: sessionmaker NOT imported")

print("\n=== Check verification code present ===")
if "Verification failed: Objective not found" in content:
    print("  SUCCESS: Verification code present in ensure_task_objective")
else:
    print("  BUG: Verification code missing from ensure_task_objective")

if "Verification failed: PlanStep not found" in content:
    print("  SUCCESS: Verification code present in sync_objective_state_for_task")
else:
    print("  BUG: Verification code missing from sync_objective_state_for_task")

session.close()