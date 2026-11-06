#!/usr/bin/env python3
"""Reproduction script for skill_proposals.py silent-success bugs."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.core.db_manager import DatabaseManager, init_database
from backend.models.database import Base, Task, TaskEvent, Skill, SkillVersion, ProposedSkill, set_db_manager
from backend.services.skill_proposals import maybe_generate_proposal, _auto_enable_if_ready, approve_proposal, reject_proposal
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

print("=== Test 1: _auto_enable_if_ready - validation_errors commit without verification ===")
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

print(f"  Before: validation_errors={proposal.validation_errors}")

# Call _auto_enable_if_ready - this should set validation_errors and commit
# We need to mock settings to have a threshold
from unittest.mock import patch
with patch('backend.core.config.settings') as mock_settings:
    mock_settings.skill_auto_enable_threshold = 2
    result = _auto_enable_if_ready(session, proposal, tool_executor)

print(f"  After _auto_enable_if_ready: validation_errors={proposal.validation_errors}")
print(f"  Result: {result}")

# Check if validation_errors was actually persisted
session.expire_all()
proposal_refreshed = session.query(ProposedSkill).filter_by(id=proposal.id).first()
print(f"  Refreshed from DB: validation_errors={proposal_refreshed.validation_errors}")

if proposal_refreshed.validation_errors and proposal_refreshed.validation_errors != "[]":
    print("  SUCCESS: validation_errors persisted")
else:
    print("  *** BUG: validation_errors NOT persisted (silent commit failure not detected)")

print("\n=== Test 2: maybe_generate_proposal - trace_ids update without verification ===")
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

print(f"  Before: trace_ids={prop.trace_ids}")

# Call maybe_generate_proposal with a similar task - should update trace_ids
task2 = Task(id="task_trace_2", original_request="test2", state="completed", normalized_goal="test2")
session.add(task2)
session.add(TaskEvent(task_id="task_trace_2", event_type="tool.executed", event_data=json.dumps({"tool_name": "file_read", "success": True, "params": {}}), sequence_num=1))
session.commit()

result = maybe_generate_proposal(session, "task_trace_2", tool_executor)

session.expire_all()
prop_refreshed = session.query(ProposedSkill).filter_by(id=prop.id).first()
print(f"  After: trace_ids={prop_refreshed.trace_ids}")

if "task_trace_2" in (prop_refreshed.trace_ids or ""):
    print("  SUCCESS: trace_ids updated")
else:
    print("  *** BUG: trace_ids NOT updated (silent commit failure not detected)")

print("\n=== Test 3: maybe_generate_proposal - new proposal creation without verification ===")
# Create two similar completed tasks
task3 = Task(id="task_new_1", original_request="test new", state="completed", normalized_goal="test new")
session.add(task3)
session.add(TaskEvent(task_id="task_new_1", event_type="tool.executed", event_data=json.dumps({"tool_name": "file_write", "success": True, "params": {"path": "a.txt", "content": "hello"}}), sequence_num=1))
session.commit()

task4 = Task(id="task_new_2", original_request="test new2", state="completed", normalized_goal="test new2")
session.add(task4)
session.add(TaskEvent(task_id="task_new_2", event_type="tool.executed", event_data=json.dumps({"tool_name": "file_write", "success": True, "params": {"path": "b.txt", "content": "world"}}), sequence_num=1))
session.commit()

# Call maybe_generate_proposal - should create new proposal
result = maybe_generate_proposal(session, "task_new_2", tool_executor)

if result:
    print(f"  Proposal created: id={result.id}")
    session.expire_all()
    prop_refreshed = session.query(ProposedSkill).filter_by(id=result.id).first()
    if prop_refreshed:
        print(f"  SUCCESS: Proposal persisted in DB")
    else:
        print(f"  *** BUG: Proposal NOT persisted in DB (silent commit failure)")
else:
    print(f"  *** No proposal generated (may be expected if traces not similar enough)")

session.close()