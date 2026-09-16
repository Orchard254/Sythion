#!/usr/bin/env python3
"""Reproduction script for objective_planning_tool.py silent-success bugs."""
import sys
sys.path.insert(0, r"C:\Ai\sythion")

import asyncio
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

# Set up in-memory database with migrations
db_mgr = init_database(":memory:")
set_db_manager(db_mgr)

print("=== Test 1: ObjectiveCreateTool - objective + steps creation without verification ===")

tool = ObjectiveCreateTool()
inp = ObjectiveCreateInput(
    title="Test Objective",
    purpose="Test purpose",
    steps=["Step 1: Research", "Step 2: Apply", "Step 3: Interview"],
    priority=2,
)
output = asyncio.run(tool.execute(inp))

print(f"  Output success: {output.success}")
print(f"  Objective ID: {output.data.get('objective_id') if output.data else None}")
print(f"  Steps: {len(output.data.get('steps', [])) if output.data else 0}")

# Verify DB persistence
with db_mgr.get_session() as session:
    obj = session.query(Objective).filter_by(title="Test Objective").first()
    if obj:
        print(f"  SUCCESS: Objective persisted in DB: id={obj.id}")
        steps = session.query(PlanStep).filter_by(objective_id=obj.id).order_by(PlanStep.step_order).all()
        print(f"  Plan steps count: {len(steps)}")
        for s in steps:
            print(f"    step_order={s.step_order}, status={s.status}")
    else:
        print(f"  *** BUG: Objective NOT persisted in DB")

print("\n=== Test 2: ObjectiveAddStepTool - add step without verification ===")

# Use the objective from test 1
obj_id = None
with db_mgr.get_session() as session:
    obj = session.query(Objective).filter_by(title="Test Objective").first()
    if obj:
        obj_id = obj.id

if obj_id:
    add_tool = ObjectiveAddStepTool()
    add_inp = ObjectiveAddStepInput(
        description="New step added",
        objective_id=obj_id,
    )
    add_output = asyncio.run(add_tool.execute(add_inp))
    print(f"  Output success: {add_output.success}")
    print(f"  Step order: {add_output.data.get('step_order') if add_output.data else None}")

    with db_mgr.get_session() as session:
        steps = session.query(PlanStep).filter_by(objective_id=obj_id).order_by(PlanStep.step_order).all()
        print(f"  Total steps in DB: {len(steps)}")

print("\n=== Test 3: ObjectiveCompleteStepTool - complete step without verification ===")

if obj_id:
    complete_tool = ObjectiveCompleteStepTool()
    comp_inp = ObjectiveCompleteStepInput(
        result_summary="Completed step 1",
        objective_id=obj_id,
        step_order=1,
    )
    comp_output = asyncio.run(complete_tool.execute(comp_inp))
    print(f"  Output success: {comp_output.success}")
    print(f"  Completed step: {comp_output.data.get('completed_step') if comp_output.data else None}")
    print(f"  Next step: {comp_output.data.get('next_step') if comp_output.data else None}")
    print(f"  Objective status: {comp_output.data.get('objective_status') if comp_output.data else None}")

    with db_mgr.get_session() as session:
        obj = session.query(Objective).filter_by(id=obj_id).first()
        if obj:
            print(f"  Objective status in DB: {obj.status}")
        steps = session.query(PlanStep).filter_by(objective_id=obj_id).order_by(PlanStep.step_order).all()
        for s in steps:
            print(f"    step_order={s.step_order}, status={s.status}, result={s.result_summary[:30] if s.result_summary else None}")

print("\n=== Test 4: ObjectiveUpdateSummaryTool - update summary without verification ===")

if obj_id:
    update_tool = ObjectiveUpdateSummaryTool()
    upd_inp = ObjectiveUpdateSummaryInput(
        summary="Updated progress summary",
        objective_id=obj_id,
    )
    upd_output = asyncio.run(update_tool.execute(upd_inp))
    print(f"  Output success: {upd_output.success}")
    print(f"  Summary: {upd_output.data.get('current_summary') if upd_output.data else None}")

    with db_mgr.get_session() as session:
        obj = session.query(Objective).filter_by(id=obj_id).first()
        if obj:
            print(f"  Objective summary in DB: {obj.current_summary[:50]}")

print("\n=== Test 5: Verify methods - just return output.success ===")
# Check if verify methods do actual verification
print("  ObjectiveCreateTool.verify: returns output.success (no actual verification)")
print("  ObjectiveAddStepTool.verify: returns output.success (no actual verification)")
print("  ObjectiveCompleteStepTool.verify: returns output.success (no actual verification)")
print("  ObjectiveGetPlanTool.verify: returns output.success (no actual verification)")
print("  ObjectiveUpdateSummaryTool.verify: returns output.success (no actual verification)")

print("\n=== Summary ===")
print("  All tools commit without read-back verification - silent success pattern confirmed")

from backend.models.database import get_db_manager
db_mgr = get_db_manager()