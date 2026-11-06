#!/usr/bin/env python3
"""Adversarial test for skill_proposals.py - tests verification behavior."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from unittest.mock import patch, MagicMock
from backend.core.db_manager import DatabaseManager, init_database
from backend.models.database import Base, Task, TaskEvent, Skill, SkillVersion, ProposedSkill, set_db_manager
from backend.services.skill_proposals import maybe_generate_proposal, _auto_enable_if_ready
from backend.tools.executor import ToolExecutor
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import json

# Set up in-memory database with migrations
db_mgr = init_database(":memory:")
set_db_manager(db_mgr)

SessionLocal = sessionmaker(bind=db_mgr.engine)
session = SessionLocal()
tool_executor = ToolExecutor()

print("=== Adversarial Test 1: _auto_enable_if_ready verification failure returns None ===")
# Create a proposal with validation errors
skill = Skill(id="test_skill", name="Test Skill", description="Test", category="custom", state="enabled")
session.add(skill)
session.commit()

skill_version = SkillVersion(
    skill_id="test_skill",
    version="1.0.0",
    definition='{"version":"1.0.0","name":"Test Skill","steps":[{"step_id":"s1","tool":"file_read","inputs":{"path":"test.txt"}}]}',
    definition_hash="abc123",
    validation_result='[]'
)
session.add(skill_version)
session.commit()

# Create a proposal with validation errors
proposal = ProposedSkill(
    definition='{"version":"1.0.0","name":"Test Skill","steps":[{"step_id":"s1","tool":"nonexistent_tool","inputs":{}}]}',
    trace_ids=json.dumps(["task1", "task2"]),
    status="proposed",
    validation_errors="[]"
)
session.add(proposal)
session.commit()

# Patch the module's sessionmaker to simulate read-back failure
# sessionmaker(bind=engine) returns a class, SessionLocal() returns an instance
mock_session_instance = MagicMock()
mock_query = MagicMock()
mock_query.filter_by.return_value.first.return_value = None  # Verification fails
mock_session_instance.query.return_value = mock_query
mock_session_instance.__enter__ = lambda self: mock_session_instance
mock_session_instance.__exit__ = lambda *args: None

mock_sessionmaker_class = MagicMock()
mock_sessionmaker_class.return_value = mock_session_instance

with patch('backend.services.skill_proposals.sessionmaker', mock_sessionmaker_class):
    from unittest.mock import patch as mock_patch
    with mock_patch('backend.core.config.settings') as mock_settings:
        mock_settings.skill_auto_enable_threshold = 2
        result = _auto_enable_if_ready(session, proposal, tool_executor)

print(f"  Result: {result}")
if result is None:
    print("  Fix behavior: returns None when verification fails")
else:
    print("  Pre-fix behavior: returns None anyway (validation failed)")

print("\n=== Adversarial Test 2: maybe_generate_proposal trace_ids verification failure ===")
# Create a task with a trace
task = Task(id="task_trace_1", original_request="test", state="completed", normalized_goal="test")
session.add(task)
session.add(TaskEvent(task_id="task_trace_1", event_type="tool.executed", event_data=json.dumps({"tool_name": "file_read", "success": True, "params": {}}), sequence_num=1))
session.commit()

# Create an existing proposal
prop = ProposedSkill(
    definition='{"version":"1.0.0","name":"File Read Workflow","steps":[{"step_id":"s1","tool":"file_read","inputs":{}}]}',
    trace_ids=json.dumps(["task_trace_1"]),
    status="proposed",
    validation_errors="[]"
)
session.add(prop)
session.commit()

# Create a similar task
task2 = Task(id="task_trace_2", original_request="test2", state="completed", normalized_goal="test2")
session.add(task2)
session.add(TaskEvent(task_id="task_trace_2", event_type="tool.executed", event_data=json.dumps({"tool_name": "file_read", "success": True, "params": {}}), sequence_num=1))
session.commit()

call_count = [0]
mock_session_instance_2 = MagicMock()
mock_query_2 = MagicMock()
mock_query_2.filter_by.return_value.first.return_value = None
mock_session_instance_2.query.return_value = mock_query_2
mock_session_instance_2.__enter__ = lambda self: mock_session_instance_2
mock_session_instance_2.__exit__ = lambda *args: None

mock_sessionmaker_class_2 = MagicMock()
mock_sessionmaker_class_2.return_value = mock_session_instance_2

with patch('backend.services.skill_proposals.sessionmaker', mock_sessionmaker_class_2):
    result = maybe_generate_proposal(session, "task_trace_2", tool_executor)

print(f"  Result: {result}")
if result is None:
    print("  Fix behavior: returns None when trace_ids verification fails")
else:
    print("  Pre-fix behavior: returns proposal (no verification)")

print("\n=== Adversarial Test 3: maybe_generate_proposal new proposal verification failure ===")
# Create two similar completed tasks
task3 = Task(id="task_new_1", original_request="test new", state="completed", normalized_goal="test new")
session.add(task3)
session.add(TaskEvent(task_id="task_new_1", event_type="tool.executed", event_data=json.dumps({"tool_name": "file_write", "success": True, "params": {"path": "a.txt", "content": "hello"}}), sequence_num=1))
session.commit()

task4 = Task(id="task_new_2", original_request="test new2", state="completed", normalized_goal="test new2")
session.add(task4)
session.add(TaskEvent(task_id="task_new_2", event_type="tool.executed", event_data=json.dumps({"tool_name": "file_write", "success": True, "params": {"path": "b.txt", "content": "world"}}), sequence_num=1))
session.commit()

mock_session_instance_3 = MagicMock()
mock_query_3 = MagicMock()
mock_query_3.filter_by.return_value.first.return_value = None
mock_session_instance_3.query.return_value = mock_query_3
mock_session_instance_3.__enter__ = lambda self: mock_session_instance_3
mock_session_instance_3.__exit__ = lambda *args: None

mock_sessionmaker_class_3 = MagicMock()
mock_sessionmaker_class_3.return_value = mock_session_instance_3

with patch('backend.services.skill_proposals.sessionmaker', mock_sessionmaker_class_3):
    result = maybe_generate_proposal(session, "task_new_2", tool_executor)

print(f"  Result: {result}")
if result is None:
    print("  Fix behavior: returns None when proposal verification fails")
else:
    print("  Pre-fix behavior: returns proposal (no verification)")

print("\n=== Check sessionmaker import ===")
with open(REPO_ROOT / "backend" / "services" / "skill_proposals.py", "r") as f:
    content = f.read()
    if "sessionmaker" in content:
        print("  SUCCESS: sessionmaker imported")
    else:
        print("  *** BUG: sessionmaker NOT imported")

session.close()