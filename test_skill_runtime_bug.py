#!/usr/bin/env python3
"""Reproduction script for skill_runtime.py silent-success bug."""
import sys
sys.path.insert(0, r"C:\Ai\sythion")

import asyncio
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

print("=== Test: execute_skill - task.selected_skill_version pin without verification ===")

# Create a skill with a version
skill = Skill(id="test_skill", name="Test Skill", description="Test", category="custom", state="enabled")
session.add(skill)

definition = parse_definition('{"version":"1.0.0","name":"Test Skill","steps":[{"step_id":"s1","tool":"file_read","inputs":{"path":"test.txt"}}]}')
validation_errors = validate_definition(definition, {})
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

# Execute skill - this should pin the task.selected_skill_version
result = asyncio.run(execute_skill(
    db=session,
    runner=runner,
    task_id="task_pin_1",
    skill_id="test_skill",
    inputs={},
    tool_executor=tool_executor
))

print(f"  execute_skill result: success={result.success}, error={result.error}")

# Check if task.selected_skill_version was ACTUALLY persisted
session.expire_all()
task_refreshed = session.query(Task).filter_by(id="task_pin_1").first()
print(f"  Task selected_skill_version: {task_refreshed.selected_skill_version}")

if task_refreshed.selected_skill_version:
    print("  SUCCESS: task.selected_skill_version persisted")
else:
    print("  *** BUG: task.selected_skill_version NOT persisted (silent commit failure not detected)")

session.close()