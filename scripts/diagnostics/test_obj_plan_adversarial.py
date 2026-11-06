#!/usr/bin/env python3
"""Adversarial test for objective_planning_tool.py - tests verification behavior."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import asyncio
from unittest.mock import patch, MagicMock
from backend.core.db_manager import DatabaseManager, init_database
from backend.models.database import Base, Objective, PlanStep, Task, set_db_manager, get_db_manager
from backend.tools.objective_planning_tool import (
    ObjectiveCreateTool, ObjectiveCreateInput,
    ObjectiveAddStepTool, ObjectiveAddStepInput,
    ObjectiveCompleteStepTool, ObjectiveCompleteStepInput,
    ObjectiveGetPlanTool, ObjectiveGetPlanInput,
    ObjectiveUpdateSummaryTool, ObjectiveUpdateSummaryInput,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime

# Set up in-memory database with migrations
db_mgr = init_database(":memory:")
set_db_manager(db_mgr)

print("=== Adversarial Test 1: ObjectiveCreateTool verification failure ===")

tool = ObjectiveCreateTool()
inp = ObjectiveCreateInput(
    title="Test Objective",
    purpose="Test",
    steps=["Step 1", "Step 2"],
    priority=2,
)

# Mock the sessionmaker to simulate read-back failure
mock_verify_session = MagicMock()
mock_query = MagicMock()
mock_query.filter_by.return_value.first.return_value = None  # Verification fails - objective not found
mock_query.filter_by.return_value.all.return_value = []  # For steps
mock_verify_session.query.return_value = mock_query
mock_verify_session.__enter__ = lambda self: mock_verify_session
mock_verify_session.__exit__ = lambda *args: None

mock_session_class = MagicMock()
mock_session_class.return_value = mock_verify_session

def mock_sessionmaker_func(*args, **kwargs):
    return mock_session_class

with patch('backend.tools.objective_planning_tool.sessionmaker', mock_sessionmaker_func):
    output = asyncio.run(tool.execute(inp))
    if not output.success and output.error and output.error.get("code") == "VERIFICATION_FAILED":
        print("  Fix behavior: returns VERIFICATION_FAILED error when verification fails")
    else:
        print(f"  Pre-fix behavior: success={output.success}, error={output.error}")

print("\n=== Adversarial Test 2: ObjectiveAddStepTool verification failure ===")

# First create an objective normally
create_tool = ObjectiveCreateTool()
create_inp = ObjectiveCreateInput(title="Test", steps=["Step 1"])
create_output = asyncio.run(create_tool.execute(create_inp))
obj_id = create_output.data["objective_id"] if create_output.success else None

if obj_id:
    add_tool = ObjectiveAddStepTool()
    add_inp = ObjectiveAddStepInput(description="New step", objective_id=obj_id)

    # Reset mock for this test
    mock_verify_session_2 = MagicMock()
    mock_query_2 = MagicMock()
    mock_query_2.filter_by.return_value.first.return_value = None
    mock_verify_session_2.query.return_value = mock_query_2
    mock_verify_session_2.__enter__ = lambda self: mock_verify_session_2
    mock_verify_session_2.__exit__ = lambda *args: None

    mock_session_class_2 = MagicMock()
    mock_session_class_2.return_value = mock_verify_session_2

    def mock_sessionmaker_func_2(*args, **kwargs):
        return mock_session_class_2

    with patch('backend.tools.objective_planning_tool.sessionmaker', mock_sessionmaker_func_2):
        output = asyncio.run(add_tool.execute(add_inp))
        if not output.success and output.error and output.error.get("code") == "VERIFICATION_FAILED":
            print("  Fix behavior: returns VERIFICATION_FAILED error when verification fails")
        else:
            print(f"  Pre-fix behavior: success={output.success}, error={output.error}")

print("\n=== Adversarial Test 3: ObjectiveCompleteStepTool verification failure ===")

if obj_id:
    complete_tool = ObjectiveCompleteStepTool()
    comp_inp = ObjectiveCompleteStepInput(result_summary="Done", objective_id=obj_id, step_order=1)

    mock_verify_session_3 = MagicMock()
    mock_query_3 = MagicMock()
    mock_query_3.filter_by.return_value.first.return_value = None
    mock_verify_session_3.query.return_value = mock_query_3
    mock_verify_session_3.__enter__ = lambda self: mock_verify_session_3
    mock_verify_session_3.__exit__ = lambda *args: None

    mock_session_class_3 = MagicMock()
    mock_session_class_3.return_value = mock_verify_session_3

    def mock_sessionmaker_func_3(*args, **kwargs):
        return mock_session_class_3

    with patch('backend.tools.objective_planning_tool.sessionmaker', mock_sessionmaker_func_3):
        output = asyncio.run(complete_tool.execute(comp_inp))
        if not output.success and output.error and output.error.get("code") == "VERIFICATION_FAILED":
            print("  Fix behavior: returns VERIFICATION_FAILED error when verification fails")
        else:
            print(f"  Pre-fix behavior: success={output.success}, error={output.error}")

print("\n=== Adversarial Test 4: ObjectiveUpdateSummaryTool verification failure ===")

if obj_id:
    update_tool = ObjectiveUpdateSummaryTool()
    upd_inp = ObjectiveUpdateSummaryInput(summary="Updated", objective_id=obj_id)

    mock_verify_session_4 = MagicMock()
    mock_query_4 = MagicMock()
    mock_query_4.filter_by.return_value.first.return_value = None
    mock_verify_session_4.query.return_value = mock_query_4
    mock_verify_session_4.__enter__ = lambda self: mock_verify_session_4
    mock_verify_session_4.__exit__ = lambda *args: None

    mock_session_class_4 = MagicMock()
    mock_session_class_4.return_value = mock_verify_session_4

    def mock_sessionmaker_func_4(*args, **kwargs):
        return mock_session_class_4

    with patch('backend.tools.objective_planning_tool.sessionmaker', mock_sessionmaker_func_4):
        output = asyncio.run(update_tool.execute(upd_inp))
        if not output.success and output.error and output.error.get("code") == "VERIFICATION_FAILED":
            print("  Fix behavior: returns VERIFICATION_FAILED error when verification fails")
        else:
            print(f"  Pre-fix behavior: success={output.success}, error={output.error}")

print("\n=== Check sessionmaker import ===")
with open(REPO_ROOT / "backend" / "tools" / "objective_planning_tool.py", "r") as f:
    content = f.read()
    if "sessionmaker" in content:
        print("  SUCCESS: sessionmaker imported")
    else:
        print("  BUG: sessionmaker NOT imported")

print("\n=== Check verification code present ===")
checks = [
    ("ObjectiveCreateTool", "VERIFICATION_FAILED"),
    ("ObjectiveAddStepTool", "VERIFICATION_FAILED"),
    ("ObjectiveCompleteStepTool", "VERIFICATION_FAILED"),
    ("ObjectiveUpdateSummaryTool", "VERIFICATION_FAILED"),
]
for tool_name, code in checks:
    if code in content:
        print(f"  SUCCESS: {tool_name} has verification code")
    else:
        print(f"  BUG: {tool_name} missing verification code")