#!/usr/bin/env python3
"""Adversarial test for skill_runtime.py - tests verification behavior."""
import sys
sys.path.insert(0, r"C:\Ai\sythion")

import asyncio
from unittest.mock import patch, MagicMock
from backend.core.db_manager import DatabaseManager, init_database
from backend.models.database import Base, Task, Skill, SkillVersion, set_db_manager
from backend.services.skill_runtime import execute_skill, add_skill_version, create_skill, parse_definition, validate_definition
from backend.tools.executor import ToolExecutor
from backend.api.schemas import TaskState
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set up in-memory database with migrations
db_mgr = init_database(":memory:")
set_db_manager(db_mgr)

SessionLocal = sessionmaker(bind=db_mgr.engine)
session = SessionLocal()
tool_executor = ToolExecutor()

print("=== Adversarial Test: execute_skill pin verification failure returns error ===")

# Create a skill with a version
skill = Skill(id="test_skill", name="Test Skill", description="Test", category="custom", state="enabled")
session.add(skill)

from backend.services.skill_runtime import allowed_tool_names
definition = parse_definition('{"version":"1.0.0","name":"Test Skill","steps":[{"step_id":"s1","tool":"system_battery","inputs":{}}]}')
validation_errors = validate_definition(definition, allowed_tool_names(tool_executor))
skill_version = add_skill_version(session, skill, definition, validation_errors)
session.commit()

print(f"  Skill created: {skill.id}, version: {skill_version.version}")

# Create a task
task = Task(id="task_pin_1", original_request="test pin", state="running", normalized_goal="test")
session.add(task)
session.commit()

# Mock runner
class MockRunner:
    async def get_task(self, task_id):
        return None
    
    async def record_event(self, task_id, event_type, event_data):
        pass

runner = MockRunner()

# Patch the module's sessionmaker to simulate read-back failure for pin verification
mock_session_instance = MagicMock()
mock_query = MagicMock()
mock_query.filter_by.return_value.first.return_value = None  # Verification fails - task not found or pin not set
mock_session_instance.query.return_value = mock_query
mock_session_instance.__enter__ = lambda self: mock_session_instance
mock_session_instance.__exit__ = lambda *args: None

mock_sessionmaker_class = MagicMock()
mock_sessionmaker_class.return_value = mock_session_instance

with patch('backend.services.skill_runtime.sessionmaker', mock_sessionmaker_class):
    # Execute skill - should fail verification and return error
    result = asyncio.run(execute_skill(
        db=session,
        runner=runner,
        task_id="task_pin_1",
        skill_id="test_skill",
        inputs={},
        tool_executor=tool_executor
    ))

print(f"  execute_skill result: success={result.success}, error={result.error}, paused={result.paused}")

# Fix behavior: returns SkillRunOutcome(success=False, error="Failed to pin skill version") when verification fails
# Pre-fix behavior: would raise exception or continue without pin (no verification)
if not result.success and "Failed to pin skill version" in (result.error or ""):
    print("  Fix behavior: returns error when pin verification fails")
else:
    print("  Pre-fix behavior: different error handling")

print("\n=== Check sessionmaker import ===")
with open("C:\\Ai\\sythion\\backend\\services\\skill_runtime.py", "r") as f:
    content = f.read()
    if "sessionmaker" in content:
        print("  SUCCESS: sessionmaker imported")
    else:
        print("  *** BUG: sessionmaker NOT imported")

session.close()