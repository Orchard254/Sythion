"""Targeted tests for scoped-memory context propagation and continuity
entry adapters (checklist S011/S013/S014).

Synthetic SQLite only: no network, live provider, profile, or secrets. The
continuity-kernel flag must stay disabled by default; adapters are inert
unless a caller explicitly opts in with ``enabled=True``.
"""

import pytest

from backend.models.database import MemoryEntry, set_db_manager
from backend.core.db_manager import DatabaseManager
from backend.services.memory_service import MemoryAccessContext
from backend.services.continuity_entry_adapters import (
    assistant_entry_context,
    entry_access_context,
    entry_enabled,
    kernel_context_for,
    voice_entry_context,
)


@pytest.fixture
def builder(tmp_path):
    """A ModelContextBuilder backed by a synthetic in-memory temp DB."""
    from backend.services.model_context_builder import ModelContextBuilder

    db_mgr = DatabaseManager(str(tmp_path / "test_scope_wiring.db"))
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        session.add_all(
            [
                MemoryEntry(
                    key="user_pref", value="prefers tea", category="user_profile",
                    scope="USER", status="ACTIVE",
                ),
                MemoryEntry(
                    key="task_a_note", value="alpha task note", category="fact",
                    scope="TASK", scope_ref="task-a", status="ACTIVE",
                ),
                MemoryEntry(
                    key="task_b_note", value="beta task note", category="fact",
                    scope="TASK", scope_ref="task-b", status="ACTIVE",
                ),
                MemoryEntry(
                    key="session_a_note", value="alpha session note", category="fact",
                    scope="SESSION", scope_ref="sess-a", status="ACTIVE",
                ),
                MemoryEntry(
                    key="goal_a_note", value="goal alpha note", category="fact",
                    scope="GOAL", scope_ref="goal-a", status="ACTIVE",
                ),
                MemoryEntry(
                    key="inactive_note", value="forgotten", category="fact",
                    scope="USER", status="FORGOTTEN",
                ),
            ]
        )
        session.commit()
        yield session, ModelContextBuilder(
            db_session=session,
            tool_summary_cap=lambda name: 1000,
            continuation_summary_cap=lambda name: 1000,
            continuation_patterns=(),
        )


def test_default_path_unchanged_task_scope_only(builder):
    """No typed context: historical task/session-only selection (no GOAL)."""
    _, b = builder
    block, _ = b.select_memory_context("alpha", task_id="task-a")
    assert "task_a_note" in block
    assert "task_b_note" not in block
    # GOAL scope was never visible on the legacy path.
    assert "goal_a_note" not in block
    # Inactive entries never appear on any path.
    assert "inactive_note" not in block


def test_typed_context_selects_matching_task_only(builder):
    """A typed access context isolates one task's memory from another."""
    _, b = builder
    ctx = MemoryAccessContext(session_id="sess-a", task_id="task-a")
    block, _ = b.select_memory_context("alpha", access_context=ctx)
    assert "task_a_note" in block
    assert "task_b_note" not in block
    assert "session_a_note" in block


def test_context_and_explicit_scope_intersect(builder):
    """Explicit args and typed context intersect; foreign task stays out."""
    _, b = builder
    ctx = MemoryAccessContext(task_id="task-b")
    # Explicit task-a + context task-b: neither TASK entry is visible.
    block, _ = b.select_memory_context("task note", task_id="task-a", access_context=ctx)
    assert "task_a_note" not in block
    assert "task_b_note" not in block
    # USER scope is still available when both agree globally visible.
    assert "user_pref" in block


def test_goal_scope_via_typed_context(builder):
    """GOAL-scoped memory is selectable through a typed context (S014)."""
    _, b = builder
    ctx = MemoryAccessContext(task_id="task-a", goal_id="goal-a")
    block, _ = b.select_memory_context("goal alpha", access_context=ctx)
    assert "goal_a_note" in block
    other = MemoryAccessContext(task_id="task-b", goal_id="goal-b")
    block2, _ = b.select_memory_context("goal alpha", access_context=other)
    assert "goal_a_note" not in block2


def test_cache_isolation_across_scopes(builder):
    """Same anchor, different scopes: no cross-scope cache leak (S013)."""
    _, b = builder
    ctx_a = MemoryAccessContext(task_id="task-a")
    ctx_b = MemoryAccessContext(task_id="task-b")
    block_a, _ = b.select_memory_context("alpha", access_context=ctx_a)
    block_b, _ = b.select_memory_context("alpha", access_context=ctx_b)
    assert "task_a_note" in block_a
    assert "task_b_note" not in block_a
    assert "task_b_note" in block_b
    assert "task_a_note" not in block_b
    # Distinct cache entries for the two scope fingerprints.
    keys = list(b._memory_cache.keys())
    assert len(keys) == 2
    # Repeat hits stay in-scope.
    assert b.select_memory_context("alpha", access_context=ctx_a)[0] == block_a


def test_empty_context_returns_no_memory(builder):
    """A typed context allowing no scopes surfaces nothing."""
    _, b = builder
    ctx = MemoryAccessContext(include_user=False, include_global=False)
    block, stats = b.select_memory_context("user_pref", access_context=ctx)
    assert block == ""
    assert stats["available"] == 0


def test_select_adapter_method_matches_direct_call(builder):
    """access_context_for_scope + select_memory_context_with_access agree."""
    _, b = builder
    ctx = b.access_context_for_scope(task_id="task-a", session_id="sess-a")
    assert ctx.resolved_task_id == "task-a"
    assert ctx.resolved_session_id == "sess-a"
    block_direct, _ = b.select_memory_context("alpha", access_context=ctx)
    block_adapter, _ = b.select_memory_context_with_access("alpha", ctx)
    assert "task_a_note" in block_direct
    assert "task_a_note" in block_adapter


def test_dict_and_duck_context_coercion(builder):
    """Dict/duck-typed contexts coerce like the MemoryService contract."""
    _, b = builder

    class Duck:
        session_id = "sess-a"
        task_id = "task-a"

    for value in ({"task_id": "task-a", "session_id": "sess-a"}, Duck()):
        block, _ = b.select_memory_context("alpha", access_context=value)
        assert "task_a_note" in block
        assert "task_b_note" not in block


def test_engine_threads_task_session_and_context(builder):
    """ModelDecisionEngine forwards scope into memory selection."""
    from unittest.mock import MagicMock

    from backend.services.model_engine import ModelDecisionEngine

    engine = ModelDecisionEngine.__new__(ModelDecisionEngine)
    engine.context_builder = MagicMock()
    engine.context_builder.select_memory_context.return_value = ("block", {})
    ctx = MemoryAccessContext(task_id="task-a")
    engine._select_memory_context("alpha", task_id="task-a", session_id="sess-a", access_context=ctx)
    kwargs = engine.context_builder.select_memory_context.call_args.kwargs
    assert kwargs["task_id"] == "task-a"
    assert kwargs["session_id"] == "sess-a"
    assert kwargs["access_context"] is ctx
    # Legacy single-arg call still works with None scope.
    engine._select_memory_context("alpha")
    kwargs2 = engine.context_builder.select_memory_context.call_args.kwargs
    assert kwargs2["task_id"] is None
    assert kwargs2["session_id"] is None
    assert kwargs2["access_context"] is None


def test_kernel_flag_stays_disabled_and_adapters_inert():
    """Flag remains off at import; adapters return None unless opted in."""
    from backend.services.continuity_kernel_contract import CONTINUITY_KERNEL_ENABLED

    assert CONTINUITY_KERNEL_ENABLED is False
    assert entry_enabled() is False
    assert entry_access_context(session_id="sess-a", task_id="task-a") is None
    assert assistant_entry_context(session_id="sess-a", task_id="task-a") is None
    assert voice_entry_context(voice_session_id="v-1", task_id="task-a") is None
    assert kernel_context_for(session_id="sess-a", task_id="task-a") is None


def test_adapters_build_scoped_contexts_when_enabled():
    """With enabled=True, each entry maps its own identity onto the context."""
    ctx = entry_access_context(session_id="sess-a", task_id="task-a", goal_id="goal-a", enabled=True)
    assert isinstance(ctx, MemoryAccessContext)
    assert ctx.resolved_session_id == "sess-a"
    assert ctx.resolved_task_id == "task-a"
    assert ctx.resolved_goal_id == "goal-a"

    a_ctx = assistant_entry_context(session_id="sess-a", task_id="task-a", enabled=True)
    assert a_ctx.resolved_session_id == "sess-a"

    v_ctx = voice_entry_context(voice_session_id="voice-9", task_id="task-a", enabled=True)
    assert v_ctx.resolved_session_id == "voice-9"
    assert v_ctx.resolved_task_id == "task-a"

    kernel_ctx = kernel_context_for(
        session_id="sess-a", task_id="task-a", enabled=True
    )
    assert kernel_ctx is not None
    assert kernel_ctx.session_id == "sess-a"
    assert kernel_ctx.enabled is True
