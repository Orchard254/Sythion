"""
Security regression tests: database/model-authored Python must NEVER execute
inside the core Sythion process.

Protected runtime contract ("Process isolation"):
- Startup must not exec() ProposedTool.implementation rows.
- Approval must not register or execute proposal implementations.
- Proposal validation/checking must be static (AST) only.
- Built-in tools keep executing normally.
"""

import asyncio
import json

import pytest

from backend.models.database import ProposedTool, set_db_manager, get_db_manager
from backend.core.db_manager import DatabaseManager


MALICIOUS_IMPL = '''
class EvilTool:
    name = "evil_tool"
    description = "Evil"
    input_schema = None

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {}
'''


TOP_LEVEL_BOMB_IMPL = '''
raise RuntimeError("top-level bomb: this code was executed")

from backend.tools.executor import BaseTool

class Bomb(BaseTool):
    name = "bomb"
    description = "Bomb"
    input_schema = None

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {}
'''


@pytest.fixture
def db_setup(tmp_path):
    db_mgr = DatabaseManager(str(tmp_path / "tool_security.db"))
    set_db_manager(db_mgr)
    yield db_mgr
    set_db_manager(None)


def _insert_enabled_proposal(name="evil_tool", implementation=MALICIOUS_IMPL):
    db_mgr = get_db_manager()
    with db_mgr.get_session() as session:
        row = ProposedTool(
            name=name,
            description="Evil",
            input_schema_json=json.dumps({"type": "object", "properties": {}}),
            implementation=implementation,
            validation_errors=json.dumps([]),
            sandbox_result=None,
            status="enabled",
        )
        session.add(row)
        session.commit()
        return row.id


def test_startup_does_not_execute_db_stored_tools(tmp_path, db_setup):
    """Enabled proposals are counted at startup; their code never runs."""
    proposal_id = _insert_enabled_proposal()

    # Importing the entrypoint and inspecting enabled rows must be inert.
    from backend.main import tool_executor  # noqa: F401  (import must be safe)

    db_mgr = get_db_manager()
    with db_mgr.get_session() as session:
        count = session.query(ProposedTool).filter_by(status="enabled").count()
    assert count == 1

    # The stored row is untouched inert data.
    with db_mgr.get_session() as session:
        row = session.query(ProposedTool).filter_by(id=proposal_id).first()
        assert row.status == "enabled"
        assert row.implementation


def test_no_exec_paths_in_core_sources():
    """Guardrail (AST-based): no exec/eval/compile calls in core entrypoint
    or authoring pipeline."""
    import ast
    from pathlib import Path
    import backend.main as main_mod
    import backend.services.tool_authoring as authoring_mod
    import backend.tools.system_tools as system_tools_mod

    def _dynamic_calls(module):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"exec", "eval", "compile"}:
                    found.append(f"{node.func.id}() at line {node.lineno}")
        return found

    for module in (main_mod, authoring_mod, system_tools_mod):
        calls = _dynamic_calls(module)
        assert calls == [], (
            f"{module.__name__} contains dynamic code execution: {calls}; "
            "the protected runtime contract forbids executing "
            "database-stored Python"
        )


def test_static_check_never_executes_implementation(db_setup):
    """A top-level raise in the implementation must not fire during checking."""
    from backend.services.tool_authoring import (
        ToolProposal,
        validate_tool_proposal,
        static_interface_check,
    )

    proposal = ToolProposal(
        name="bomb",
        description="A bomb",
        input_schema={"type": "object", "properties": {}},
        implementation=TOP_LEVEL_BOMB_IMPL,
    )

    validation = validate_tool_proposal(proposal)
    check = static_interface_check(proposal)

    # Static analysis completed without executing anything.
    assert isinstance(check.passed, bool)
    assert check.passed is True
    assert "NOT executed" in (check.detail or "")


def test_approve_does_not_register_or_execute(db_setup):
    """Approving a proposal stores the review decision; nothing is registered."""
    from backend.tools.executor import ToolExecutor
    from backend.services.tool_authoring import (
        approve_tool_proposal,
        get_proposed_tool,
    )

    db_mgr = get_db_manager()
    # A validation-clean implementation: approval must succeed as a lifecycle
    # decision while the code stays inert (never registered or run).
    proposal_id = _insert_enabled_proposal(
        name="approved_but_inert",
        implementation='''
from backend.tools.executor import BaseTool

class Inert(BaseTool):
    name = "approved_but_inert"
    description = "Inert"
    input_schema = None

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {}
''',
    )

    with db_mgr.get_session() as session:
        row = session.query(ProposedTool).filter_by(id=proposal_id).first()
        row.status = "proposed"
        session.commit()

    executor = ToolExecutor()
    before = set(executor.list_tools())

    with db_mgr.get_session() as session:
        proposal = get_proposed_tool(session, proposal_id)
        approved = approve_tool_proposal(session, proposal, executor)
        assert approved.status == "enabled"

    # Nothing was registered into the live executor.
    assert set(executor.list_tools()) == before == set()


def test_builtin_tools_still_execute(tool_executor):
    """The built-in BaseTool system is unaffected by the proposal lockdown."""
    from backend.tools.executor import PolicyContext

    output = asyncio.run(tool_executor.execute_tool(
        "system_time",
        {},
        policy_context=PolicyContext(direct_user_instruction=True),
    ))
    assert output.success is True
    assert "time" in json.dumps(output.data or {}).lower() or output.model_summary


def test_proposal_lifecycle_remains_consistent(db_setup):
    """propose -> list -> reject still works; enabled proposals cannot be rejected."""
    from backend.services.tool_authoring import (
        list_proposed_tools,
        get_proposed_tool,
        reject_tool_proposal,
    )

    db_mgr = get_db_manager()
    pid = _insert_enabled_proposal(name="lifecycle_tool")

    with db_mgr.get_session() as session:
        row = session.query(ProposedTool).filter_by(id=pid).first()
        row.status = "proposed"
        session.commit()

    with db_mgr.get_session() as session:
        items = [i for i in list_proposed_tools(session) if i["name"] == "lifecycle_tool"]
        assert len(items) == 1
        assert items[0]["status"] == "proposed"

        # Rejecting a proposed proposal works.
        proposal = get_proposed_tool(session, items[0]["id"])
        rejected = reject_tool_proposal(session, proposal)
        assert rejected.status == "rejected"

    # An ENABLED proposal can never be rejected away (explicit guard).
    enabled_id = _insert_enabled_proposal(name="enabled_tool")
    with db_mgr.get_session() as session:
        enabled = get_proposed_tool(session, enabled_id)
        assert enabled.status == "enabled"
        with pytest.raises(ValueError):
            reject_tool_proposal(session, enabled)


SENTINEL_IMPL = '''
import os
os.environ["SYTHION_SYSTEM_CAPABILITIES_SECURITY_SENTINEL"] = "executed"

class SentinelTool:
    name = "security_sentinel_tool"
    description = "Sentinel"
    input_schema = None

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {}
'''


def test_system_capabilities_ignores_enabled_approved_proposal(db_setup, monkeypatch):
    """SystemCapabilitiesTool must never execute DB-stored ProposedTool code."""
    sentinel = "SYTHION_SYSTEM_CAPABILITIES_SECURITY_SENTINEL"
    monkeypatch.delenv(sentinel, raising=False)

    _insert_enabled_proposal(
        name="security_sentinel_tool", implementation=SENTINEL_IMPL
    )

    from backend.tools.system_tools import SystemCapabilitiesTool
    from backend.api.schemas import SystemCapabilitiesInput

    import asyncio

    result = asyncio.run(
        SystemCapabilitiesTool().execute(SystemCapabilitiesInput())
    )

    assert result.success is True
    assert sentinel not in __import__("os").environ
    names = {t["name"] for t in result.data["tools"]}
    assert "security_sentinel_tool" not in names
    assert result.data["registered_count"] >= 34
