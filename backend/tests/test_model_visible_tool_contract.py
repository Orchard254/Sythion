"""Focused tests for model-visible tool availability and mode-aware prompts."""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import SystemCapabilitiesInput
from backend.models.database import Base
from backend.services.model_engine import ModelDecisionEngine, ModelDecisionError
from backend.services.model_prompt_contract import build_system_prompt
from backend.tools.executor import ToolExecutor
from backend.tools.registry import create_all_tools
from backend.tools.system_tools import SystemCapabilitiesTool


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def tool_executor():
    executor = ToolExecutor()
    for tool in create_all_tools():
        executor.register_tool(tool)
    return executor


@pytest.mark.asyncio
async def test_engine_model_visible_never_includes_gui_tools(db_session, tool_executor, monkeypatch):
    for autonomy in ("guarded", "autonomous"):
        monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
        engine = ModelDecisionEngine(db_session, tool_executor)
        visible = {tool["name"] for tool in engine._model_visible_tools()}

        assert not any(name.startswith("gui_") for name in visible)
        # Tools the executor can never auto-dispatch are not advertised in
        # either autonomy mode; visibility does not widen with autonomy.
        assert "app_launch" not in visible
        assert "app_focus" not in visible
        assert "file_write" not in visible
        # Reviewed-safe read-only tools remain visible in both modes.
        assert "system_time" in visible


@pytest.mark.asyncio
async def test_system_capabilities_mirror_engine_visibility(db_session, tool_executor, monkeypatch):
    caps_tool = SystemCapabilitiesTool()
    result = await caps_tool.execute(SystemCapabilitiesInput())
    assert result.success is True

    for autonomy in ("guarded", "autonomous"):
        monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
        engine = ModelDecisionEngine(db_session, tool_executor)
        engine_visible = {tool["name"] for tool in engine._model_visible_tools()}
        reported_visible = set(result.data[f"model_visible_{autonomy}"])

        # The capability report is cited as ground truth in the prompt, so it
        # must be EXACTLY the set the engine advertises in every autonomy mode:
        # no over-reporting tools the executor rejects (e.g. objective_create,
        # system_volume) and no under-reporting tools the engine does expose.
        # Set equality enforces both directions; a one-way subset is not enough.
        assert engine_visible == reported_visible
        assert not any(name.startswith("gui_") for name in engine_visible)
        assert "app_launch" not in engine_visible
        assert "file_write" not in engine_visible


@pytest.mark.asyncio
async def test_engine_rejects_hidden_gui_tool_as_invalid_decision(db_session, tool_executor, monkeypatch):
    for autonomy in ("guarded", "autonomous"):
        monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
        engine = ModelDecisionEngine(db_session, tool_executor)
        response = {
            "message": {
                "tool_calls": [{"function": {"name": "gui_hotkey", "arguments": {}}}]
            }
        }

        with pytest.raises(ModelDecisionError) as exc:
            engine._parse_decision(response)
        assert exc.value.code == "INVALID_DECISION_TYPE"


def test_prompt_guarded_states_gui_not_model_callable():
    prompt = build_system_prompt(available_tools="system_time", active_autonomy="guarded")

    assert "not model-callable in either autonomy mode" in prompt
    assert "no approval, consent, or configuration can make these tools available" in prompt
    assert "Launching, focusing, or driving applications and windows are gated" in prompt


def test_prompt_autonomous_does_not_claim_gui_or_unconditional_focus():
    prompt = build_system_prompt(available_tools="app_launch, app_focus", active_autonomy="autonomous")

    assert "not model-callable in either autonomy mode" in prompt
    assert "app_focus when it is listed in AVAILABLE TOOLS" in prompt
    assert "controlling and observing desktop windows with the gui_* tools" not in prompt
    assert "is allowed when requested" not in prompt


def test_invalid_decision_repair_guidance_is_not_blanket():
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent.joinpath("services/task_execution_loop.py").read_text(encoding="utf-8")
    assert "Do NOT retry gui_* or any other tool not " in source
    assert "listed in AVAILABLE TOOLS (app_launch only when listed)." in source
    assert "Do NOT retry app_launch, " not in source
