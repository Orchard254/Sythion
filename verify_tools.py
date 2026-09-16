import os
import sys
sys.path.insert(0, r'C:\Ai\sythion')

from backend.tools.executor import ToolExecutor
from backend.tools.system_tools import create_system_tools
from backend.tools.file_tools import create_file_tools
from backend.tools.app_tools import create_app_tools
from backend.tools.package_tools import create_package_tools
from backend.tools.gui_tools import create_gui_tools
from backend.tools.memory_tools import MemoryStoreTool, MemoryRecallTool
from backend.tools.persistent_memory_tools import (
    MemoryStoreTool as PMStore,
    MemoryRecallTool as PMRecall,
    MemoryForgetTool,
    MemoryLinkTool,
    MemoryRecallRelatedTool,
    MemoryStatsTool,
    MemoryExportTool
)
from backend.tools.activity_recall_tool import ActivityRecallTool
from backend.tools.objective_planning_tool import (
    ObjectiveCreateTool, ObjectiveAddStepTool, ObjectiveCompleteStepTool,
    ObjectiveGetPlanTool, ObjectiveUpdateSummaryTool
)

# Build full registry
ex = ToolExecutor()
all_tools = (
    list(create_system_tools()) +
    list(create_file_tools()) +
    list(create_app_tools()) +
    list(create_package_tools()) +
    list(create_gui_tools()) +
    [MemoryStoreTool(), MemoryRecallTool(),
     PMStore(), PMRecall(), MemoryForgetTool(), MemoryLinkTool(),
     MemoryRecallRelatedTool(), MemoryStatsTool(), MemoryExportTool(),
     ActivityRecallTool(),
     ObjectiveCreateTool(), ObjectiveAddStepTool(), ObjectiveCompleteStepTool(),
     ObjectiveGetPlanTool(), ObjectiveUpdateSummaryTool()]
)
for tool in all_tools:
    ex.register_tool(tool)

print(f"=== TOTAL REGISTERED TOOLS: {len(ex.list_tools())} ===")
for name in sorted(ex.list_tools()):
    tool = ex.get_tool(name)
    reqs = tool.get_policy_requirements()
    print(f"  {name}: {reqs}")

print("\n=== GUARDED MODE FILTER ===")
os.environ['SYTHION_AUTONOMY_LEVEL'] = 'guarded'
from backend.services.model_engine import ModelDecisionEngine
from backend.core.config import settings

class MockDB:
    pass

engine = ModelDecisionEngine(MockDB(), ex)
active_autonomy = os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level)
print(f"active_autonomy = {active_autonomy}")

visible_guarded = []
filtered_guarded = []

for name in sorted(ex.list_tools()):
    tool = ex.get_tool(name)
    reqs = tool.get_policy_requirements()
    if reqs.get("requires_admin"):
        filtered_guarded.append((name, "requires_admin"))
        continue
    if active_autonomy != "autonomous":
        if reqs.get("mutating") or reqs.get("destructive"):
            if not reqs.get("app_internal"):
                filtered_guarded.append((name, "mutating/destructive (not app_internal)"))
                continue
        if reqs.get("requires_confirmation"):
            filtered_guarded.append((name, "requires_confirmation"))
            continue
        if reqs.get("risk_level") in {"medium", "high"}:
            filtered_guarded.append((name, f"risk_level={reqs.get('risk_level')}"))
            continue
        if (
            reqs.get("privacy_sensitive")
            or reqs.get("requires_screen_access")
            or reqs.get("requires_ui_automation")
        ):
            filtered_guarded.append((name, "privacy_sensitive/requires_screen_access/requires_ui_automation"))
            continue
    visible_guarded.append(name)

print(f"\nVISIBLE IN GUARDED MODE: {len(visible_guarded)}")
for name in visible_guarded:
    print(f"  {name}")

print(f"\nFILTERED OUT IN GUARDED MODE: {len(filtered_guarded)}")
for name, reason in filtered_guarded:
    print(f"  {name}: {reason}")

print("\n=== AUTONOMOUS MODE FILTER ===")
os.environ['SYTHION_AUTONOMY_LEVEL'] = 'autonomous'
engine2 = ModelDecisionEngine(MockDB(), ex)
active_autonomy2 = os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level)
print(f"active_autonomy = {active_autonomy2}")

visible_auto = []
filtered_auto = []

for name in sorted(ex.list_tools()):
    tool = ex.get_tool(name)
    reqs = tool.get_policy_requirements()
    if reqs.get("requires_admin"):
        filtered_auto.append((name, "requires_admin"))
        continue
    if active_autonomy2 != "autonomous":
        # this branch won't execute in autonomous mode
        pass
    visible_auto.append(name)

print(f"\nVISIBLE IN AUTONOMOUS MODE: {len(visible_auto)}")
for name in visible_auto:
    print(f"  {name}")

print(f"\nFILTERED OUT IN AUTONOMOUS MODE: {len(filtered_auto)}")
for name, reason in filtered_auto:
    print(f"  {name}: {reason}")

print(f"\n=== DECISION TOOLS ===")
decision = engine._decision_tools()
print(f"Count: {len(decision)}")
for t in decision:
    print(f"  {t['name']}")

print(f"\n=== TOTALS WITH DECISION TOOLS ===")
print(f"Guarded total: {len(visible_guarded) + len(decision)}")
print(f"Autonomous total: {len(visible_auto) + len(decision)}")