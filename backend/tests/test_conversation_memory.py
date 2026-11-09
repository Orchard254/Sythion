"""
Unit and integration tests for Sythion's three-tier conversation memory.
"""

import logging
import pytest
from backend.core.session import get_current_session_id, set_current_session_id, reset_session_id
from backend.models.database import (
    set_db_manager, ConversationTurn, MemoryEntry, MemoryEdge,
    MemoryContextLog, Task, TaskEvent,
)
from backend.core.db_manager import DatabaseManager
from backend.api.schemas import MemoryStoreInput, MemoryRecallInput, MemoryForgetInput, TaskState
from backend.tools.memory_tools import MemoryStoreTool, MemoryRecallTool, MemoryForgetTool
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.task_runner import TaskRunner
from backend.services.model_engine import ModelDecisionEngine
from backend.tools.executor import ToolExecutor
import json


@pytest.fixture
def memory_db_setup(tmp_path):
    db_path = str(tmp_path / "test_conv_memory.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        yield session, db_mgr


@pytest.mark.asyncio
async def test_memory_forget_tool(memory_db_setup):
    session, db_mgr = memory_db_setup
    store_tool = MemoryStoreTool()
    forget_tool = MemoryForgetTool()
    recall_tool = MemoryRecallTool()

    # 1. Direct persistent-memory mutation is fail-closed: nothing is stored.
    store_result = await store_tool.execute(MemoryStoreInput(key="test_key", value="test_val", category="test_cat"))
    assert store_result.success is False
    assert store_result.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"

    # 2. Recall therefore remains empty.
    res1 = await recall_tool.execute(MemoryRecallInput(query="test_key"))
    assert res1.success is True
    assert res1.data["count"] == 0

    # 3. Direct forget is also fail-closed; no delete occurs.
    forget_result = await forget_tool.execute(MemoryForgetInput(key="test_key"))
    assert forget_result.success is False
    assert forget_result.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"

    # 4. Recall remains unchanged.
    res3 = await recall_tool.execute(MemoryRecallInput(query="test_key"))
    assert res3.success is True
    assert res3.data["count"] == 0


@pytest.mark.asyncio
async def test_conversation_turn_save_and_auto_extract(memory_db_setup):
    session, db_mgr = memory_db_setup

    sess_id = reset_session_id()

    # Create dummy completed task
    task = Task(
        id="task_101",
        original_request="my name is Alex and I prefer VS Code",
        state=TaskState.COMPLETED.value,
        final_result="Switched default text editor to VS Code",
    )
    session.add(task)
    session.commit()

    loop = TaskExecutionLoop(db_session=session, task_runner=TaskRunner(session), tool_executor=None)
    await loop._save_conversation_turn("task_101", task)

    # Verify ConversationTurn rows (2 rows: user + assistant)
    turns = session.query(ConversationTurn).filter_by(session_id=sess_id).all()
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert "my name is Alex" in turns[0].summary
    assert turns[1].role == "assistant"
    assert "VS Code" in turns[1].summary

    # Verify regex auto-extracted long-term memories
    name_mem = session.query(MemoryEntry).filter_by(key="user_name").first()
    assert name_mem is not None
    assert name_mem.value == "Alex"

    editor_mem = session.query(MemoryEntry).filter_by(key="preferred_editor").first()
    assert editor_mem is not None
    assert editor_mem.value == "VS Code"


@pytest.mark.asyncio
async def test_previous_task_context_scales_web_summary_for_continuation(memory_db_setup):
    """Web-tool summaries get a scaled-down-but-larger slice on continuation tasks."""
    session, db_mgr = memory_db_setup
    long_summary = "Top 5 results for 'python asyncio':\n" + "\n".join(
        f"{i + 1}. Result headline number {i + 1} with a reasonably long title to exercise the truncation budget ({'pad' * 30})"
        for i in range(5)
    )
    session.add(Task(
        id="prev_web",
        original_request="search the web for python asyncio",
        normalized_goal="find python asyncio info",
        state=TaskState.FAILED.value,
        terminal_reason="ACTION_LIMIT_EXCEEDED",
    ))
    session.flush()
    session.add(TaskEvent(
        task_id="prev_web",
        event_type="tool.executed",
        event_data=json.dumps({
            "tool_name": "file_read",
            "model_summary": long_summary,
            "success": True,
        }),
        sequence_num=1,
    ))
    session.commit()

    engine = ModelDecisionEngine(session, ToolExecutor())
    ctx = engine._previous_task_context(task_id="current_web", request="continue")
    # The continuation slice scales the web cap down (~0.73 of the main cap),
    # so a chunk well past the old 160-char continuation limit survives.
    assert "file_read" in ctx
    assert long_summary[:300] in ctx
    assert long_summary[160:500] in ctx


@pytest.mark.asyncio
async def test_previous_task_context_seeds_continuation(memory_db_setup):
    session, db_mgr = memory_db_setup
    prev = Task(
        id="prev_1",
        original_request="organize my downloads folder",
        normalized_goal="clean up downloads",
        state=TaskState.FAILED.value,
        terminal_reason="ACTION_LIMIT_EXCEEDED",
    )
    session.add(prev)
    session.flush()
    session.add(TaskEvent(
        task_id="prev_1",
        event_type="tool.executed",
        event_data=json.dumps({"tool_name": "file_list", "model_summary": "listed downloads", "success": True}),
        sequence_num=1,
    ))
    session.commit()

    engine = ModelDecisionEngine(session, ToolExecutor())
    ctx = engine._previous_task_context(task_id="current_1", request="continue")
    assert "organize my downloads folder" in ctx
    assert "file_list" in ctx
    assert "ACTION_LIMIT_EXCEEDED" in ctx


@pytest.mark.asyncio
async def test_previous_task_context_ignores_non_continuation(memory_db_setup):
    session, db_mgr = memory_db_setup
    session.add(Task(id="prev_3", original_request="organize my downloads folder", state=TaskState.FAILED.value))
    session.commit()

    engine = ModelDecisionEngine(session, ToolExecutor())
    ctx = engine._previous_task_context(task_id="current_1", request="check my email inbox")
    assert ctx == ""


@pytest.mark.asyncio
async def test_previous_task_context_does_not_treat_greeting_as_continuation(memory_db_setup):
    session, db_mgr = memory_db_setup
    session.add(Task(id="prev_greet", original_request="organize my downloads folder", state=TaskState.FAILED.value))
    session.commit()

    engine = ModelDecisionEngine(session, ToolExecutor())
    ctx = engine._previous_task_context(task_id="current_greet", request="hey")
    assert ctx == ""


@pytest.mark.asyncio
async def test_previous_task_context_excludes_current_task(memory_db_setup):
    session, db_mgr = memory_db_setup
    session.add(Task(id="only_task", original_request="organize my downloads folder", state=TaskState.FAILED.value))
    session.commit()

    engine = ModelDecisionEngine(session, ToolExecutor())
    ctx = engine._previous_task_context(task_id="only_task", request="continue")
    assert ctx == ""


@pytest.mark.asyncio
async def test_previous_task_context_seeds_refinement_phrases(memory_db_setup):
    """Correction/refinement phrasing ("what of ...", "what about ...",
    "... instead ...") must trigger the same PREVIOUS TASK CONTEXT seeding as
    explicit continuation words, so a follow-up like "what of according to
    standard media" resolves against the prior Tuko search instead of being
    answered with "I don't understand"."""
    session, db_mgr = memory_db_setup
    prev = Task(
        id="prev_tuko",
        original_request="search for latest news on tuko website",
        normalized_goal="find latest tuko news",
        state=TaskState.FAILED.value,
        terminal_reason="ACTION_LIMIT_EXCEEDED",
    )
    session.add(prev)
    session.flush()
    session.add(TaskEvent(
        task_id="prev_tuko",
        event_type="tool.executed",
        event_data=json.dumps({"tool_name": "file_read", "model_summary": "Tuko Kenya latest news", "success": True}),
        sequence_num=1,
    ))
    session.commit()

    engine = ModelDecisionEngine(session, ToolExecutor())
    for phrase in ("what of according to standard media", "what about standard media", "try tuko instead"):
        ctx = engine._previous_task_context(task_id="current_ref", request=phrase)
        assert "search for latest news on tuko website" in ctx
        assert "Tuko Kenya latest news" in ctx


@pytest.mark.asyncio
async def test_build_context_injects_guidance_to_resolve_short_messages(memory_db_setup):
    """The system prompt must direct the model to resolve short / elliptical /
    referential messages against the injected RECENT CONVERSATION and PREVIOUS
    TASK CONTEXT blocks before calling request_user_input."""
    session, db_mgr = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())
    msgs = engine._build_context(
        task_id="current_11",
        original_request="what of according to standard media",
        normalized_goal=None,
        event_history=[],
        current_state="running",
    )
    sys_msg = next(m for m in msgs if m["role"] == "system")
    assert "RESOLVE SHORT / ELLIPTICAL / REFERENTIAL REQUESTS" in sys_msg["content"]
    assert "RECENT CONVERSATION (this session)" in sys_msg["content"]


@pytest.mark.asyncio
async def test_build_context_injects_previous_task_for_continuation(memory_db_setup):
    session, db_mgr = memory_db_setup
    session.add(Task(id="prev_2", original_request="draft a reply to the urgent email", state=TaskState.FAILED.value))
    session.commit()

    engine = ModelDecisionEngine(session, ToolExecutor())
    msgs = engine._build_context(
        task_id="current_9",
        original_request="continue",
        normalized_goal=None,
        event_history=[],
        current_state="running",
    )
    user_msg = next(m for m in msgs if m["role"] == "user")
    assert "PREVIOUS TASK CONTEXT" in user_msg["content"]
    assert "draft a reply to the urgent email" in user_msg["content"]

    msgs2 = engine._build_context(
        task_id="current_10",
        original_request="open the browser",
        normalized_goal=None,
        event_history=[],
        current_state="running",
    )
    user_msg2 = next(m for m in msgs2 if m["role"] == "user")
    assert "PREVIOUS TASK CONTEXT" not in user_msg2["content"]


@pytest.mark.asyncio
async def test_build_context_injects_only_relevant_memory(memory_db_setup):
    """Selective injection replaces the flat top-20 dump: only entries relevant
    to the request are injected, so a large store no longer floods the context."""
    session, db_mgr = memory_db_setup
    for i in range(20):
        session.add(MemoryEntry(key=f"unrelated_topic_{i}", value=f"topic {i}", category="knowledge"))
    session.add(MemoryEntry(key="preferred_editor", value="VS Code", category="preference"))
    session.commit()

    engine = ModelDecisionEngine(session, ToolExecutor())
    msgs = engine._build_context(
        task_id="task_inj_1",
        original_request="please use my preferred editor for the changes",
        normalized_goal="edit code using preferred editor",
        event_history=[],
        current_state="running",
    )
    user_msg = next(m for m in msgs if m["role"] == "user")
    content = user_msg["content"]
    assert "preferred_editor" in content
    assert "VS Code" in content
    # The old top-20 dump would have injected every unrelated entry.
    assert "unrelated_topic_0" not in content
    assert "unrelated_topic_19" not in content
    assert content.count("[knowledge]") < 5
    assert len(content) < 3000


@pytest.mark.asyncio
async def test_build_context_injects_linked_memory_neighborhood(memory_db_setup):
    """Entries reachable only via a memory link (1 hop) are injected too."""
    session, db_mgr = memory_db_setup
    session.add(MemoryEntry(key="work_location", value="Home office", category="preference"))
    session.add(MemoryEntry(key="meeting_tool", value="Zoom", category="preference"))
    session.add(MemoryEntry(key="team_lead", value="Priya", category="profile"))
    session.add(MemoryEntry(key="favorite_food", value="pizza", category="knowledge"))
    session.add(MemoryEdge(source_key="work_location", relation="uses_for", target_key="meeting_tool"))
    session.add(MemoryEdge(source_key="work_location", relation="works_with", target_key="team_lead"))
    session.commit()

    engine = ModelDecisionEngine(session, ToolExecutor())
    msgs = engine._build_context(
        task_id="task_inj_2",
        original_request="set up my work location meeting",
        normalized_goal=None,
        event_history=[],
        current_state="running",
    )
    user_msg = next(m for m in msgs if m["role"] == "user")
    content = user_msg["content"]
    assert "work_location" in content
    assert "meeting_tool" in content
    # team_lead matches no request token and is reached only through the link.
    assert "team_lead" in content
    assert "[linked]" in content
    assert "MEMORY LINKS" in content
    assert "uses_for" in content
    assert "favorite_food" not in content


@pytest.mark.asyncio
async def test_memory_context_log_trim_keeps_most_recent(memory_db_setup):
    """The 500-row trim is FIFO: it keeps the newest rows, discards the oldest."""
    session, db_mgr = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())

    for i in range(505):
        engine._record_memory_context_stats(
            f"task-{i}",
            {"available": 1, "relevant": 0, "connected": 0, "links": 0},
        )

    with db_mgr.get_session() as check:
        rows = check.query(MemoryContextLog).order_by(MemoryContextLog.id.asc()).all()
        assert len(rows) == 500
        # The 5 oldest inserts (task-0..task-4) are dropped; the most recent 500
        # (task-5..task-504) survive, oldest of the survivors first.
        assert rows[0].task_id == "task-5"
        assert rows[-1].task_id == "task-504"


@pytest.mark.asyncio
async def test_conversation_turn_preserves_long_list_options(memory_db_setup):
    """Assistant turn summaries retain long numbered lists up to 2000 chars."""
    session, db_mgr = memory_db_setup
    sess_id = reset_session_id()

    long_list_result = (
        "Here are 4 realistic options for remote work in Kenya:\n"
        "1. Micro-tasking & Data Labeling: Remotasks, Appen (fastest payout for beginners).\n"
        "2. Freelancing: Upwork, Fiverr for writing, graphics, and tech skills.\n"
        "3. Digital Services: Managing social media for local Kenyan businesses.\n"
        "4. Academic/Writing Services: Content writing and academic editing."
    )

    task = Task(
        id="task_kenya_options",
        original_request="since im based in kenya what can i do, because im seeing most of this are for people who are abroad",
        state=TaskState.COMPLETED.value,
        final_result=long_list_result,
    )
    session.add(task)
    session.commit()

    loop = TaskExecutionLoop(db_session=session, task_runner=TaskRunner(session), tool_executor=None)
    await loop._save_conversation_turn("task_kenya_options", task)

    turn_asst = (
        session.query(ConversationTurn)
        .filter_by(session_id=sess_id, role="assistant")
        .first()
    )
    assert "1. Micro-tasking & Data Labeling" in turn_asst.summary
    assert "2. Freelancing" in turn_asst.summary
    assert "3. Digital Services" in turn_asst.summary
    assert "4. Academic/Writing Services" in turn_asst.summary


@pytest.mark.asyncio
async def test_conversation_guidance_adapter_turn_ordering(memory_db_setup):
    """Same-millisecond conversation turns always display User prompt before Synth response."""
    from backend.services.conversation_guidance_adapter import ConversationGuidanceAdapter
    from backend.services.post_task_intelligence import save_conversation_turn

    session, db_mgr = memory_db_setup
    sess_id = reset_session_id()

    task1 = Task(
        id="task_turn_order_1",
        original_request="5mbs into mb",
        state=TaskState.COMPLETED.value,
        final_result="5 Mbps is 0.625 MB/s",
    )
    session.add(task1)
    session.commit()

    await save_conversation_turn(session, "task_turn_order_1", task1)

    adapter = ConversationGuidanceAdapter(session, lambda: sess_id)
    blocks = adapter.build_blocks(original_request="or kb", has_previous_context=False)

    recent = blocks.recent_conversation
    assert recent != ""
    lines = recent.split("\n")
    # Verify header
    assert lines[0] == "RECENT CONVERSATION (this session):"
    # Verify User is before Synth
    user_idx = next(i for i, l in enumerate(lines) if l.startswith("- User:"))
    synth_idx = next(i for i, l in enumerate(lines) if l.startswith("- Synth:"))
    assert user_idx < synth_idx, f"Expected User turn before Synth turn, got:\n{recent}"


def test_extract_topics_preserves_allowed_short_units():
    """_extract_topics preserves allowed 2-letter technical units and acronyms while filtering random short tokens."""
    from backend.services.conversation_state import _extract_topics

    # Units and acronyms preserved
    assert "mb" in _extract_topics("5mbs into mb")
    assert "kb" in _extract_topics("or kb")
    assert "gb" in _extract_topics("convert 10gb to mb")
    assert "os" in _extract_topics("check the os version")
    assert "ip" in _extract_topics("what is my ip")
    assert "ui" in _extract_topics("update the ui layout")
    assert "ai" in _extract_topics("learn about ai")

    # Stop words and non-allowed 2-letter words filtered
    assert _extract_topics("or") == []
    assert _extract_topics("in") == []
    assert _extract_topics("to") == []
    assert _extract_topics("at") == []


@pytest.mark.asyncio
async def test_model_context_builder_verification_access_count_update(memory_db_setup):
    """select_memory_context increments access_count and sets last_accessed_at with read-back verification."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry
    from backend.services.model_engine import ModelDecisionEngine

    session, db_mgr = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())

    # Add memory entries with access_count=0
    session.add_all([
        MemoryEntry(key="test_key_1", value="value1", category="user_profile", access_count=0),
        MemoryEntry(key="test_key_2", value="value2", category="user_profile", access_count=0),
    ])
    session.commit()

    # Call select_memory_context via the engine's context_builder
    block, stats = engine.context_builder.select_memory_context("test profile", session_id="test_session")

    # Verify the block was returned
    assert block is not None
    assert "test_key_1" in block
    assert "test_key_2" in block

    # Verify access_count was incremented and last_accessed_at was set (read-back verification)
    entry1 = session.query(MemoryEntry).filter_by(key="test_key_1").first()
    entry2 = session.query(MemoryEntry).filter_by(key="test_key_2").first()
    assert entry1.access_count == 1, f"access_count not incremented: {entry1.access_count}"
    assert entry2.access_count == 1, f"access_count not incremented: {entry2.access_count}"
    assert entry1.last_accessed_at is not None, "last_accessed_at not set"
    assert entry2.last_accessed_at is not None, "last_accessed_at not set"


@pytest.mark.asyncio
async def test_model_context_builder_verification_memory_context_log_created(memory_db_setup):
    """record_memory_context_stats creates MemoryContextLog with read-back verification."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryContextLog
    from backend.services.model_engine import ModelDecisionEngine

    session, db_mgr = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())

    # Call record_memory_context_stats
    engine._record_memory_context_stats(
        "test_task_verification",
        {"available": 5, "relevant": 3, "connected": 1, "links": 2}
    )

    # Verify MemoryContextLog was created (read-back verification in fix)
    log = session.query(MemoryContextLog).filter_by(task_id="test_task_verification").first()
    assert log is not None, "MemoryContextLog not created - verification would catch this"
    assert log.available == 5
    assert log.relevant == 3
    assert log.connected == 1
    assert log.links == 2


@pytest.mark.asyncio
async def test_model_context_builder_verification_trim_persists(memory_db_setup):
    """record_memory_context_stats trim deletes old logs with read-back verification."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryContextLog
    from sqlalchemy import func
    from backend.services.model_engine import ModelDecisionEngine

    session, db_mgr = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())

    # Add 500 existing logs
    for i in range(500):
        session.add(MemoryContextLog(task_id=f"existing_{i}", available=1, relevant=0, connected=0, links=0))
    session.commit()

    # Call record_memory_context_stats - should add 1 (501) and trim 1
    engine._record_memory_context_stats(
        "trigger_trim",
        {"available": 1, "relevant": 0, "connected": 0, "links": 0}
    )

    # Verify trim happened (read-back verification in fix)
    with db_mgr.get_session() as check:
        count = check.query(func.count(MemoryContextLog.id)).scalar()
        assert count == 500, f"Expected 500 after trim, got {count}"
        
        # Oldest log (existing_0) should be deleted
        old_log = check.query(MemoryContextLog).filter_by(task_id="existing_0").first()
        assert old_log is None, "Old log existing_0 not deleted - verification would catch this"
        
        # New log should exist
        new_log = check.query(MemoryContextLog).filter_by(task_id="trigger_trim").first()
        assert new_log is not None, "New log not created"


@pytest.mark.asyncio
async def test_model_context_builder_select_memory_context_verification_failure_returns_error(memory_db_setup):
    """select_memory_context returns error tuple when verification fails (fix behavior).

    This test FAILS on pre-fix code because:
    1. Pre-fix code lacks the sessionmaker import (AttributeError when patching)
    2. Pre-fix code has no verification error return path - it always returns (block, stats)
    """
    from unittest.mock import patch, MagicMock
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry
    from backend.services.model_engine import ModelDecisionEngine

    session, db_mgr = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())

    # Add memory entry
    session.add(MemoryEntry(key="verify_fail_key", value="value", category="user_profile", access_count=0))
    session.commit()

    # Patch the verification session to simulate read-back failure (returns None)
    # This tests the NEW verification code path added by the fix
    def mock_verify_session(*args, **kwargs):
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter_by.return_value.first.return_value = None  # Verification fails - entry not found
        mock_session.query.return_value = mock_query
        return mock_session
    
    with patch('backend.services.model_context_builder.sessionmaker') as mock_sessionmaker:
        mock_sessionmaker.return_value.__enter__ = mock_verify_session
        mock_sessionmaker.return_value.__exit__ = lambda *args: None
        
        # Call select_memory_context - should return error tuple due to verification failure
        result = engine.context_builder.select_memory_context("verify fail", session_id="test_session")
        
        # Fix behavior: returns (None, {"error": ...}) when verification fails
        # Pre-fix behavior: would return (block, stats) normally (no verification)
        assert result is not None
        assert isinstance(result, tuple)
        assert len(result) == 2
        # The fix returns (None, {"error": ...}) on verification failure
        # Pre-fix code doesn't have this error return path at all
        if result[0] is None and isinstance(result[1], dict) and "error" in result[1]:
            # Fix behavior - verification caught the failure
            pass
        else:
            # Pre-fix behavior - no verification, returns normal result
            pytest.fail("Pre-fix code does not have verification error return path")


@pytest.mark.asyncio
async def test_post_task_intelligence_save_conversation_turn_verification(memory_db_setup, caplog):
    """save_conversation_turn creates ConversationTurn rows with read-back verification.

    This test FAILS on pre-fix code because:
    1. Pre-fix code lacks the sessionmaker import (AttributeError when patching)
    2. Pre-fix code has no verification - it commits turns but doesn't verify they persisted
    """
    from unittest.mock import patch, MagicMock
    from backend.tools.executor import ToolExecutor
    from backend.models.database import Task, ConversationTurn
    from backend.api.schemas import TaskState
    from backend.services.post_task_intelligence import save_conversation_turn
    from backend.core.session import set_current_session_id

    session, db_mgr = memory_db_setup
    sess_id = set_current_session_id("test_verification_session")

    # Create a completed task
    task = Task(
        id="task_conv_verify",
        original_request="test verification",
        state=TaskState.COMPLETED.value,
        final_result="Task completed successfully"
    )
    session.add(task)
    session.commit()

    # Patch the module's sessionmaker to simulate read-back failure for conversation turns
    mock_session_instance = MagicMock()
    mock_query = MagicMock()
    mock_query.filter_by.return_value.all.return_value = []  # Verification fails - no turns found
    mock_session_instance.query.return_value = mock_query
    mock_session_instance.__enter__ = lambda self: mock_session_instance
    mock_session_instance.__exit__ = lambda *args: None

    mock_sessionmaker_class = MagicMock()
    mock_sessionmaker_class.return_value = mock_session_instance

    caplog.set_level(logging.WARNING, logger="backend.services.post_task_intelligence")
    with patch('backend.services.post_task_intelligence.sessionmaker', mock_sessionmaker_class):
        # Call save_conversation_turn - should return early due to verification failure
        await save_conversation_turn(session, "task_conv_verify", task)

    # Fix behavior: returns early (verification caught the failure)
    # The turns may have been committed before verification, so we check that the
    # verification log message is emitted (asserted below).
    turns = session.query(ConversationTurn).filter_by(task_id="task_conv_verify").all()
    # The fix returns early when verification fails, but the commit already happened.
    # The count below is the ConversationTurn rows the production code committed for this task: the read-back
    # verification is what fails, not the commit.  The session has autoflush=False (backend/core/db_manager.py:88-92),
    # so only committed rows are visible - an unflushed row could not satisfy it, so this count is a persistence check.
    # The key test is that the verification code path exists and
    # runs: the production code logs this warning when the read-back finds no turns.
    assert len(turns) == 2
    # caplog.text is the root handler's text, not the module logger's.
    assert "Verification failed" in caplog.text


@pytest.mark.asyncio
async def test_model_context_builder_scope_filters_other_task_memory(memory_db_setup):
    """Task-scoped memory from another task must not enter current model context."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry
    from backend.services.model_engine import ModelDecisionEngine

    session, _ = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())
    session.add_all([
        MemoryEntry(
            key="other_task_secret",
            value="other-task-only value",
            category="knowledge",
            status="ACTIVE",
            scope="TASK",
            scope_ref="task-a",
        ),
        MemoryEntry(
            key="current_task_memory",
            value="current task memory value",
            category="knowledge",
            status="ACTIVE",
            scope="TASK",
            scope_ref="task-b",
        ),
        MemoryEntry(
            key="global_memory",
            value="global memory value",
            category="knowledge",
            status="ACTIVE",
            scope="GLOBAL",
        ),
    ])
    session.commit()

    block, _ = engine.context_builder.select_memory_context(
        "task b memory",
        session_id="session-b",
        task_id="task-b",
    )

    assert "other_task_secret" not in block
    assert "other-task-only value" not in block
    assert "current_task_memory" in block
    assert "global_memory" in block


@pytest.mark.asyncio
async def test_model_context_builder_prefers_verified_relevant_memory(memory_db_setup):
    """Verified/high-confidence memory should outrank a merely frequent weak entry."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry
    from backend.services.model_engine import ModelDecisionEngine

    session, _ = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())
    session.add_all([
        MemoryEntry(
            key="weak_memory",
            value="project alpha is an external preference",
            category="knowledge",
            status="ACTIVE",
            scope="GLOBAL",
            access_count=99,
            confidence=0.1,
            verification_status="UNVERIFIED",
        ),
        MemoryEntry(
            key="verified_memory",
            value="project alpha is the verified priority",
            category="knowledge",
            status="ACTIVE",
            scope="GLOBAL",
            access_count=1,
            confidence=0.98,
            verification_status="VERIFIED",
        ),
    ])
    session.commit()

    block, _ = engine.context_builder.select_memory_context(
        "project alpha",
        session_id="session-ranking",
        task_id="task-ranking",
    )

    assert "verified_memory" in block
    assert "weak_memory" in block
    assert block.index("verified_memory") < block.index("weak_memory")


@pytest.mark.asyncio
async def test_model_context_builder_scope_filter_without_task_session_ids(memory_db_setup):
    """With no task/session ids, only USER/GLOBAL memory is injected."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry
    from backend.services.model_engine import ModelDecisionEngine

    session, _ = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())
    session.add_all([
        MemoryEntry(
            key="scoped_task_memory",
            value="scoped task value",
            category="knowledge",
            status="ACTIVE",
            scope="TASK",
            scope_ref="task-x",
        ),
        MemoryEntry(
            key="scoped_session_memory",
            value="scoped session value",
            category="knowledge",
            status="ACTIVE",
            scope="SESSION",
            scope_ref="session-x",
        ),
        MemoryEntry(
            key="global_memory",
            value="global memory value",
            category="knowledge",
            status="ACTIVE",
            scope="GLOBAL",
        ),
    ])
    session.commit()

    block, _ = engine.context_builder.select_memory_context("scoped global")
    assert "scoped_task_memory" not in block
    assert "scoped_session_memory" not in block
    assert "global_memory" in block

    # With a session id but no task id, the matching SESSION entry is
    # visible while the TASK entry is still excluded.
    block, _ = engine.context_builder.select_memory_context(
        "scoped session", session_id="session-x"
    )
    assert "scoped_session_memory" in block
    assert "scoped_task_memory" not in block


@pytest.mark.asyncio
async def test_model_context_builder_excludes_inactive_entries(memory_db_setup):
    """FORGOTTEN/SUPERSEDED/ARCHIVED entries never enter model context."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry
    from backend.services.model_engine import ModelDecisionEngine

    session, _ = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())
    session.add_all([
        MemoryEntry(
            key="forgotten_memory",
            value="forgotten secret value",
            category="knowledge",
            status="FORGOTTEN",
            scope="GLOBAL",
        ),
        MemoryEntry(
            key="superseded_memory",
            value="superseded secret value",
            category="knowledge",
            status="SUPERSEDED",
            scope="GLOBAL",
        ),
        MemoryEntry(
            key="active_memory",
            value="active memory value",
            category="knowledge",
            status="ACTIVE",
            scope="GLOBAL",
        ),
    ])
    session.commit()

    block, _ = engine.context_builder.select_memory_context(
        "secret active memory", session_id="session-inact", task_id="task-inact"
    )
    assert "forgotten_memory" not in block
    assert "forgotten secret value" not in block
    assert "superseded_memory" not in block
    assert "superseded secret value" not in block
    assert "active_memory" in block


@pytest.mark.asyncio
async def test_model_context_builder_no_match_fallback_ranks_quality(memory_db_setup):
    """No-token-match fallback ranks verified/high-confidence above frequent weak."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry
    from backend.services.model_engine import ModelDecisionEngine

    session, _ = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())
    session.add_all([
        MemoryEntry(
            key="fallback_weak",
            value="zzqf alpha value",
            category="knowledge",
            status="ACTIVE",
            scope="GLOBAL",
            access_count=999,
            confidence=0.1,
            verification_status="UNVERIFIED",
        ),
        MemoryEntry(
            key="fallback_verified",
            value="zzqf beta value",
            category="knowledge",
            status="ACTIVE",
            scope="GLOBAL",
            access_count=1,
            confidence=0.98,
            verification_status="VERIFIED",
        ),
    ])
    session.commit()

    # Query tokens match no stored value textually: fallback ordering decides.
    block, _ = engine.context_builder.select_memory_context(
        "nomatchbeam quortex", session_id="session-fb", task_id="task-fb"
    )
    assert "fallback_verified" in block
    assert "fallback_weak" in block
    assert block.index("fallback_verified") < block.index("fallback_weak")


@pytest.mark.asyncio
async def test_model_context_builder_selection_is_bounded(memory_db_setup):
    """Selection stays capped well below a large store (no full dump)."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry
    from backend.services.model_engine import ModelDecisionEngine

    session, _ = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())
    session.add_all([
        MemoryEntry(
            key=f"bounded_memory_{i}",
            value=f"bounded project item {i}",
            category="knowledge",
            status="ACTIVE",
            scope="GLOBAL",
            confidence=0.9,
            verification_status="VERIFIED",
        )
        for i in range(60)
    ])
    session.commit()

    block, stats = engine.context_builder.select_memory_context(
        "bounded project", session_id="session-bnd", task_id="task-bnd"
    )
    assert stats["available"] == 60
    assert 0 < stats["relevant"] <= 25
    # A full dump would name all 60 keys; the block names at most 25.
    entry_lines = [ln for ln in block.splitlines() if "bounded_memory_" in ln]
    assert len(entry_lines) <= 25


def test_entry_match_score_verified_beats_frequent_weak():
    """Scorer: verified/high-confidence relevance outranks raw frequency."""
    from backend.tools.persistent_memory_tools import _entry_match_score

    class FakeEntry:
        def __init__(self, **kw):
            self.key = kw["key"]
            self.value = kw["value"]
            self.category = kw["category"]
            self.access_count = kw["access_count"]
            self.confidence = kw["confidence"]
            self.verification_status = kw["verification_status"]

    weak = FakeEntry(
        key="weak", value="project alpha note", category="knowledge",
        access_count=999, confidence=0.1, verification_status="UNVERIFIED",
    )
    verified = FakeEntry(
        key="verified", value="project alpha note", category="knowledge",
        access_count=1, confidence=0.98, verification_status="VERIFIED",
    )
    tokens = ["project", "alpha"]
    assert _entry_match_score(verified, tokens) > _entry_match_score(weak, tokens)


@pytest.mark.asyncio
async def test_model_context_builder_linked_neighbors_exclude_foreign_scope(memory_db_setup):
    """Linked neighbors that are foreign-scoped or inactive never enter model context."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry, MemoryEdge
    from backend.services.model_engine import ModelDecisionEngine

    session, _ = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())
    session.add_all([
        MemoryEntry(key="anchor_mem", value="anchor project value", category="knowledge", status="ACTIVE", scope="GLOBAL"),
        MemoryEntry(key="foreign_neighbor", value="foreign scoped value", category="knowledge", status="ACTIVE", scope="TASK", scope_ref="other-task"),
        MemoryEdge(source_key="anchor_mem", target_key="foreign_neighbor", relation="links_to", status="active"),
    ])
    session.commit()

    block, stats = engine.context_builder.select_memory_context(
        "project anchor", session_id="session-scope", task_id="current-task"
    )
    assert "anchor_mem" in block
    assert "foreign_neighbor" not in block
    assert "foreign scoped value" not in block


@pytest.mark.asyncio
async def test_model_context_builder_neighbor_expansion_bounded(memory_db_setup):
    """One-hop neighbor expansion stays bounded even with many linked entries."""
    from backend.tools.executor import ToolExecutor
    from backend.models.database import MemoryEntry, MemoryEdge
    from backend.services.model_engine import ModelDecisionEngine

    session, _ = memory_db_setup
    engine = ModelDecisionEngine(session, ToolExecutor())
    session.add(MemoryEntry(key="anchor_hub", value="hub project value", category="knowledge", status="ACTIVE", scope="GLOBAL"))
    for i in range(40):
        key = f"neighbor_{i}"
        session.add(MemoryEntry(key=key, value=f"neighbor value {i}", category="knowledge", status="ACTIVE", scope="GLOBAL"))
        session.add(MemoryEdge(source_key="anchor_hub", target_key=key, relation="relates", status="active"))
    session.commit()

    block, stats = engine.context_builder.select_memory_context(
        "hub project", session_id="session-bnd2", task_id="task-bnd2"
    )
    assert stats["connected"] <= 25
    linked_lines = [ln for ln in (block or "").splitlines() if "[linked]" in ln]
    assert len(linked_lines) <= 25



