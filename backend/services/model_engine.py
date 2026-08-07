"""
Model Decision Engine for Sythion (Synth).
Handles LLM orchestration, context building, and decision parsing.
"""

import json
import logging
import os
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session

from backend.adapters.ollama_adapter import get_adapter, format_tools_for_ollama
from backend.tools.executor import ToolExecutor
from backend.api.schemas import TaskState
from backend.core.budgets import TaskBudget, BudgetExceededError
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional as Opt

logger = logging.getLogger(__name__)

# How many memory-context injection rows the validation log retains.
MEMORY_CONTEXT_LOG_RETAIN = 500


class CompleteTaskInput(BaseModel):
    """Input for complete_task decision."""
    result: str = Field(..., description="The user-facing response message or result text. MUST be written directly to the user (e.g., 'Hello! I am doing well, how can I help you?'), NEVER a meta-summary of what you did.")


class FailTaskInput(BaseModel):
    """Input for fail_task decision."""
    reason: str = Field(..., description="Reason the task could not be completed")


class RequestUserInput(BaseModel):
    """Input for request_user_input decision."""
    question: str = Field(..., description="Question to ask the user")


class RunSkillInput(BaseModel):
    """Input for run_skill decision."""
    skill_id: str = Field(..., description="ID of the skill to run")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Skill input parameters")


class ExecuteToolInput(BaseModel):
    """Input for execute_tool decision."""
    tool_name: str = Field(..., description="Name of the tool to execute")
    params: Dict[str, Any] = Field(default_factory=dict, description="Tool parameters")


class ModelDecisionError(Exception):
    """Raised when model decision is invalid or cannot be parsed."""
    def __init__(self, code: str, message: str, attempt: int = 0):
        self.code = code
        self.message = message
        self.attempt = attempt
        super().__init__(message)


class DecisionType:
    """Valid model decision types."""
    COMPLETE_TASK = "complete_task"
    FAIL_TASK = "fail_task"
    REQUEST_USER_INPUT = "request_user_input"
    RUN_SKILL = "run_skill"
    EXECUTE_TOOL = "execute_tool"


class ModelDecisionEngine:
    """
    Core LLM orchestration engine.
    
    Responsibilities:
    - Build context from task state, history, and available tools
    - Send requests to Ollama Cloud with tool definitions
    - Parse and validate model decisions
    - Handle repair attempts (max 2)
    - Track token usage
    """
    
    MAX_REPAIR_ATTEMPTS = 2
    MAX_TOKENS_PER_REQUEST = 4000
    MAX_TOKENS_PER_TASK = 50000

    # Tool-summary truncation budget when tool results are replayed into the
    # model context. Low-content tools (battery status, process list, etc.)
    # keep the historical 220-char cap; web tools get a larger budget because
    # their own tool-level caps (web_search 2000 / web_fetch 3000 chars)
    # already bound the output sensibly, and a 220-char cut would destroy the
    # multiple-result / page-text payload the tool was built to deliver.
    #
    # Budget check (worst case): up to 10 tool events are replayed per task,
    # each summary appears at two sites ("Recent Activity" + native tool turn).
    # web_fetch worst case = 10 * 2200 * 2 = 44,000 chars ~= 11k tokens; web_search
    # = 10 * 1600 * 2 = 32,000 chars ~= 8k tokens. Both sit well inside the
    # 50,000-token input cap (~200k chars @ 4 chars/token), leaving ample
    # headroom for the system prompt, memory injection, and conversation.
    _DEFAULT_SUMMARY_CAP = 220
    _WEB_TOOL_SUMMARY_CAPS = {
        "web_search": 1600,
        "web_fetch": 2200,
    }
    # The continuation-task slice scales down from the normal cap by the same
    # ratio the previous 160-char slice used against the 220-char default
    # (160/220 ~= 0.73), so web content still gets proportionally more room.
    _CONTINUATION_SUMMARY_CAP = 160
    _CONTINUATION_SCALE = _CONTINUATION_SUMMARY_CAP / _DEFAULT_SUMMARY_CAP

    def _tool_summary_cap(self, tool_name: str, *, continuation: bool = False) -> int:
        """Per-tool char cap for a tool-result summary in the model context."""
        cap = self._WEB_TOOL_SUMMARY_CAPS.get(tool_name, self._DEFAULT_SUMMARY_CAP)
        if continuation:
            cap = round(cap * self._CONTINUATION_SCALE)
        return cap

    _CONTINUATION_PATTERNS = (
        r"\bcontinue\b",
        r"\bresume\b",
        r"\bproceed\b",
        r"\bgo on\b",
        r"\bkeep going\b",
        r"\bcarry on\b",
        r"\bpick up\b",
        r"\bwhat you were\b",
        r"\bwhere (?:you|we) were\b",
        r"\bfinish (?:the|your) (?:task|work|job)\b",
        r"\byou were\b",
        r"\bas we discussed\b",
        r"\bprevious task\b",
        r"\blast task\b",
        r"\bcontinue where\b",
        r"\bwhat of\b",
        r"\bwhat about\b",
        r"\binstead\b",
    )
    
    def __init__(self, db_session: Session, tool_executor: ToolExecutor):
        self.db = db_session
        self.tool_executor = tool_executor
        self.adapter = get_adapter()
        self.token_count = 0
        self.decision_history: List[Dict[str, Any]] = []
        
    def _build_system_prompt(self) -> str:
        """Build the system prompt for Sythion (Synth) behavior."""
        available_tools = ", ".join(t["name"] for t in self._model_visible_tools()) or "(none registered)"
        from backend.core.config import settings
        active_autonomy = os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level)
        if active_autonomy == "autonomous":
            risk_guidance = (
                "- AUTONOMOUS MODE: Execute mutating, destructive, and GUI actions directly without asking for confirmation; "
                "call the appropriate tool directly and let policy, target checks, and verification run in the executor. "
                "Do not call `request_user_input` for risk confirmation. "
                "Use `request_user_input` only when a target is genuinely ambiguous or a parameter cannot be determined. "
                "If a close/overwrite/delete returns an unsaved-work or overwrite prompt, do not silently discard data; "
                "report it and ask the user how to proceed."
            )
            confirmation_tool_usage = (
                "request_user_input - When you need clarification or additional parameters"
            )
        else:
            risk_guidance = (
                "- DESTRUCTIVE / IRREVERSIBLE ACTIONS (e.g. deleting existing files/folders, overwriting non-empty files, "
                "uninstalling software, closing windows with potential unsaved work): You MUST call `request_user_input` first "
                "to state the specific risk, target, and impact, and ask for explicit user confirmation before touching the target."
            )
            confirmation_tool_usage = (
                "request_user_input - When you need clarification, user confirmation for destructive actions, or additional parameters"
            )
        return f"""You are Sythion (Synth), an intelligent Windows 10/11 personal desktop assistant built for autonomous daily planning, device interaction, and task execution.

Your role is to help users accomplish tasks on their Windows computer through safe, verified actions.

IMPORTANT RULES:
1. You can only make ONE decision at a time - never parallel calls. If you need multiple actions, do them one after another.
2. Always observe state before taking actions
3. Verify after every mutating operation
4. Respect user privacy - never access files outside approved roots
5. Do not execute arbitrary shell commands or automate browsers through the shell; however, controlling the currently focused desktop window with the gui_* tools (gui_hotkey, gui_type, gui_click, gui_paste) is allowed when the user asked you to, e.g. sending Ctrl+L or Ctrl+T and typing a URL into the focused browser window
6. Handle errors gracefully with honest failure messages
7. Ask for clarification when targets are ambiguous
8. Stop immediately if the user cancels
9. Never repeat a tool call that already failed with the same arguments; change your approach or fail the task honestly
10. Never repeat a read-only query tool call (e.g. system_battery, system_time, system_disk, web_search, memory_recall) once it returns data — immediately call complete_task to present the final answer to the user

INTELLECTUAL HONESTY:
- Never claim to know or remember something you cannot ground in stored memory, verified tool output, or the user's explicit words. If unsure, say so and verify with a tool.
- Always distinguish clearly: "I remember X" (grounded in stored memory or verified data), "I don't have that stored" (only after checking memory), or "Let me check" (then actually check before answering).
- Never guess file paths, root directories, or personal details. Query a tool or state honestly that you don't know rather than inventing them.
- A memory_recall result of 0 entries does not prove nothing is stored: first try a broader query (or no query) and check the PERSISTENT MEMORY & USER PREFERENCES section already injected into your context before reporting a memory as absent.
- Stored memories can be stale or wrong. If a memory conflicts with live tool output, the tool output wins: trust the tool, and never assert a fact that a tool just contradicted.
- Never fabricate tool results, memory contents, or verification outcomes.

FILE ACCESS & GRANTS:
- You operate within approved file roots. The user controls these: boot defaults come from environment configuration, and the user can grant additional roots at any time in the app's Settings (Approved file roots) — a grant takes effect immediately without a restart.
- To learn the exact approved roots, the current user, and the home directory, call `system_file_roots`. Never guess a root path or a username — query the tool.
- When a file operation is rejected with TARGET_OUTSIDE_ROOTS, do NOT claim you have no way to gain access. State the exact path you need, ask the user to add it under Settings -> Approved file roots, then retry once they confirm.
- Never attempt to bypass the root check, guess a root path, or operate on a path you have not been granted.

REAL-TIME RISK EVALUATION & AUTONOMY GUIDANCE:
- Perform a real-time risk assessment before executing any mutating tool.
- SAFE / REVERSIBLE ACTIONS (e.g. launching registered applications, creating new scratch files, focusing windows, reading system status, daily planning, organizing tasks): Execute immediately and autonomously without unnecessary interruptions.
- `app_launch` reuses an existing open window of the target app by default (safe default: no duplicate process). Pass `new_instance=True` when reusing the current window could disrupt something the user is actively doing (e.g. the user is actively working in a browser window) and a genuinely separate window is safer. Reuse-by-default remains acceptable; this is advisory, not a hard rule.
{risk_guidance}

REAL-TIME MEMORY MANAGEMENT:
- You can store user preferences, habits, profile facts, or app mappings to persistent memory using `memory_store`.
- Call `memory_store(key="...", value="...", category="...")` ONLY for durable facts about the user: preferences, habits, profile details, or explicit "remember this" instructions.
- `memory_store` REFUSES live system state and device identity: current time/date, disk or battery levels, active window, installed apps, process lists, environment summary dumps, and device paths/roots/usernames are re-queryable live facts — query their tools (`system_file_roots`, `system_time`, `system_disk`, `system_battery`, `system_active_window`, `system_processes`, `app_list`) instead. Stored snapshots go stale and become false facts; the tool rejects them.
- Call `memory_forget(key="...")` if the user asks you to forget a preference or if a memory is outdated.
- Relate durable facts with `memory_link(source_key="...", relation="...", target_key="...")` (relation is lowercase snake_case, e.g. user_name --prefers--> preferred_editor), and use `memory_recall_related(key="...")` to recall a key plus its linked neighborhood.
- Persistent memories, memory links, and recent conversation turns are automatically retrieved (relevance-selected) and injected into your prompt for every task.
- RESOLVE SHORT / ELLIPTICAL / REFERENTIAL REQUESTS FROM PRIOR CONTEXT BEFORE ASKING TO CLARIFY. A message that lacks a clear standalone subject, or reads as a follow-up or correction of the previous turn (e.g. "what about ...", "what of ...", "instead of X", "try Y instead", "the other one", "any news ...", a bare noun or a name), normally refers to the immediately preceding turn. Map it against the RECENT CONVERSATION (this session) block and the PREVIOUS TASK CONTEXT block already in your context, and act on that referent — do NOT call request_user_input asking the user to restate the message. Only ask for clarification when you genuinely cannot map the message onto prior context or stored facts.
- When a PREVIOUS TASK CONTEXT block is present and the user asks to continue or resume, continue that task's goal from its last actions instead of asking the user what to continue.
- To answer questions about Sythion's OWN past actions and activity (e.g. "what were you doing at 8am?", "what did you search for this morning?", "which files did you touch yesterday?"), use `activity_recall(start_time=..., end_time=..., exclude_task_ids=[...], tool_name=...)`. This is distinct from `memory_recall` (stored facts/preferences) and `web_search` (live internet). Resolve natural-language timing ("this morning", "8am", "yesterday") into an ISO-8601 `start_time` yourself using the current time. Always pass your current task_id in `exclude_task_ids` so the result only shows PRIOR activity — not events from the task you are currently running.

REAL-TIME WEB SEARCH & INTERNET ACCESS:
- You can search the live internet for real-time news, weather, documentation, or facts using `web_search`.
- You can fetch and read web page content using `web_fetch`.
- Use `web_search` whenever the user asks for online information, current events, or external web lookups.

CAPABILITIES & SELF-AWARENESS:
- Call `system_capabilities` when you need to know exactly what you can do: the full tool inventory, which tools are model-visible in the current autonomy mode, and the known capability gaps.
- If a user request needs a capability that does not exist (see known gaps in `system_capabilities` output), do not fake it — call `tool_propose` with a name, description, input schema, and Python implementation. The proposal will be validated, sandbox-tested, and require owner approval before becoming live. This is how you grow your own capabilities.
- Tool proposals must: define a class inheriting from BaseTool, implement execute() and get_policy_requirements(), use no forbidden imports (subprocess, os, socket, etc.), contain no secrets or coordinates in schema/description, and pass sandbox testing.
- NEVER say "I don't have a tool for that" without first checking `system_capabilities` and considering whether `tool_propose` could create one.

AVAILABLE TOOLS (call one directly by its exact name):
{available_tools}

DECISION TYPES:
1. complete_task - MANDATORY for all completion and conversational turns. You MUST call complete_task for EVERY response, conversational remark, or task result (e.g. call complete_task(result="Understood! What would you like to work on next?")). NEVER output raw text without a tool call.
2. fail_task - When you cannot complete the task (after max 2 repair attempts)
3. {confirmation_tool_usage}
4. run_skill - To execute a registered skill workflow

Call any listed tool directly with its parameters. To finish, call complete_task with the final result message. To ask a question or request confirmation, call request_user_input.

EFFICIENCY & ACTION BUDGET:
- Each task has a bounded per-task action budget (default 50 executed actions) and FAILS when it is exhausted. Conserve actions.
- Do not re-run the same read-only observation (`system_time`, `system_disk`, `app_list`, `file_list` on the same path) unless its result may have changed and you genuinely need it.
- Gather the minimum evidence the task needs, then call `complete_task` with the result. Repeating identical observations wastes the budget and is a signal you should conclude.

You MUST call a tool to get real information (like the current time). Do NOT answer from memory - use a tool to query the system first.

Always think step-by-step and prefer observation before action."""

    def _model_visible_tools(self) -> List[Dict[str, Any]]:
        """Return the policy-filtered subset of tools safe to expose to the model.

        Phase B exposes only read-only, confirmation-free tools with no screen,
        UI-tree, or privacy-sensitive output. Mutating and GUI tools remain
        registered and policy-gated at the executor but are never model-visible
        until their milestone gate passes (Phase C+). The single exception is
        app-internal mutations (`app_internal`): they change only Sythion's own
        persistent memory, are reversible, and are instructed by the system
        prompt, so they stay callable in normal mode.
        """
        visible = []
        from backend.core.config import settings
        active_autonomy = os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level)
        for name in self.tool_executor.list_tools():
            tool = self.tool_executor.get_tool(name)
            if not tool:
                continue
            reqs = tool.get_policy_requirements()
            if reqs.get("requires_admin"):
                continue
            if active_autonomy != "autonomous":
                if reqs.get("mutating") or reqs.get("destructive"):
                    if not reqs.get("app_internal"):
                        continue
                if reqs.get("requires_confirmation"):
                    continue
                if reqs.get("risk_level") in {"medium", "high"}:
                    continue
                if (
                    reqs.get("privacy_sensitive")
                    or reqs.get("requires_screen_access")
                    or reqs.get("requires_ui_automation")
                ):
                    continue
            visible.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            })
        return visible

    def _decision_tools(self) -> List[Dict[str, Any]]:
        """Build tool metadata for the decision functions."""
        return [
            {
                "name": DecisionType.COMPLETE_TASK,
                "description": "Complete the task with the final result",
                "input_schema": CompleteTaskInput,
            },
            {
                "name": DecisionType.FAIL_TASK,
                "description": "Fail the task with a reason",
                "input_schema": FailTaskInput,
            },
            {
                "name": DecisionType.REQUEST_USER_INPUT,
                "description": "Ask the user a clarifying question",
                "input_schema": RequestUserInput,
            },
            {
                "name": DecisionType.RUN_SKILL,
                "description": "Execute a registered skill workflow",
                "input_schema": RunSkillInput,
            },
        ]

    def _is_continuation_request(self, request: str) -> bool:
        """Detect a request that means "continue/resume what you were doing"."""
        if not request or not request.strip():
            return True
        text = request.lower()
        for pat in self._CONTINUATION_PATTERNS:
            if re.search(pat, text):
                return True
        return len(text.strip()) <= 12

    def _previous_task_context(self, task_id: str, request: str) -> str:
        """Deterministic continuity: seed a continuation task with the last task's
        goal and recent actions.

        The model never persists its own task state reliably, so continuity must
        come from durable task rows + events, not from model-written memory.
        Explicit continuation phrases attach to any prior executed task; short or
        empty requests attach only to a recent non-terminal task (the
        continue-after-failure case) to avoid greeting noise.
        """
        if not self._is_continuation_request(request):
            return ""
        try:
            from backend.models.database import Task, TaskEvent
            from datetime import timedelta

            explicit = bool(request and request.strip()) and len(request.strip()) > 12
            query = self.db.query(Task).filter(Task.id != task_id)
            if explicit:
                query = query.filter(Task.state.notin_(("created", "running")))
            else:
                cutoff = datetime.utcnow() - timedelta(hours=12)
                query = (
                    query.filter(Task.state.in_(("failed", "interrupted", "paused")))
                    .filter(Task.created_at >= cutoff)
                )
            prev = query.order_by(Task.created_at.desc()).first()
            if not prev:
                return ""

            lines = [
                "PREVIOUS TASK CONTEXT (a prior task exists; if the user asks you to "
                "continue or resume, continue THIS work rather than asking what to do):",
                f"- previous task id: {prev.id}",
                f"- previous task state: {prev.state}",
                f"- previous request: {prev.original_request}",
                f"- previous goal: {prev.normalized_goal or '(none recorded)'}",
            ]
            evts = (
                self.db.query(TaskEvent)
                .filter(TaskEvent.task_id == prev.id)
                .filter(TaskEvent.event_type == "tool.executed")
                .order_by(TaskEvent.created_at.desc())
                .limit(5)
                .all()
            )
            if evts:
                lines.append("- last actions before it stopped:")
                for ev in reversed(evts):
                    try:
                        data = json.loads(ev.event_data) if isinstance(ev.event_data, str) else (ev.event_data or {})
                    except (ValueError, TypeError):
                        data = {}
                    tname = data.get("tool_name", "?")
                    summary = data.get("model_summary")
                    if summary is None:
                        summary = data.get("output", "")
                    if isinstance(summary, (dict, list)):
                        summary = json.dumps(summary, ensure_ascii=False, default=str)
                    cap = self._tool_summary_cap(tname, continuation=True)
                    if len(str(summary)) > cap:
                        summary = str(summary)[:cap - 3] + "..."
                    lines.append(f"  - {tname}: {summary}")
            if prev.final_result or prev.terminal_reason:
                lines.append(f"- previous outcome: {prev.final_result or prev.terminal_reason}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Could not build previous-task context: {e}")
            return ""

    def _select_memory_context(self, anchor: str):
        """Selectively inject persistent memory relevant to the current request.

        Anchors on the request text, scores entries with the same scorer as
        `memory_recall`, keeps the top relevant entries, then walks memory
        links one hop outward to pull in the immediate neighborhood. Returns
        (context_block, stats) where stats records injected-vs-available counts
        for the rollout log.
        """
        block = ""
        stats = {"available": 0, "relevant": 0, "connected": 0, "links": 0}
        try:
            from backend.models.database import MemoryEntry, MemoryEdge
            from backend.tools.memory_tools import _query_tokens, _entry_match_score

            all_entries = self.db.query(MemoryEntry).order_by(MemoryEntry.updated_at.desc()).all()
            stats["available"] = len(all_entries)
            if not all_entries:
                return block, stats

            selected = []
            tokens = _query_tokens(anchor or "")
            if tokens:
                scored = []
                for e in all_entries:
                    s = _entry_match_score(e, tokens)
                    if s > 0:
                        scored.append((s, e))
                scored.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
                selected = [e for _, e in scored[:5]]
            if not selected:
                selected = list(all_entries[:3])
            stats["relevant"] = len(selected)

            selected_keys = {e.key for e in selected}
            edges = (
                self.db.query(MemoryEdge)
                .filter(
                    MemoryEdge.source_key.in_(selected_keys),
                    MemoryEdge.status != "removed",
                )
                .order_by(MemoryEdge.updated_at.desc())
                .all()
            )
            stats["links"] = len(edges)
            neighbor_keys = {e.target_key for e in edges} - selected_keys
            neighbors = (
                self.db.query(MemoryEntry)
                .filter(MemoryEntry.key.in_(neighbor_keys))
                .all()
            ) if neighbor_keys else []
            stats["connected"] = len(neighbors)

            lines = [f"- [{e.category}] {e.key}: {e.value}" for e in selected]
            lines += [f"- [linked] [{e.category}] {e.key}: {e.value}" for e in neighbors]
            if lines:
                block = "\nPERSISTENT MEMORY & USER PREFERENCES:\n" + "\n".join(lines)
            if edges:
                edge_lines = [f"- {e.source_key} --{e.relation}--> {e.target_key}" for e in edges[:12]]
                block += "\nMEMORY LINKS:\n" + "\n".join(edge_lines)
        except Exception as e:
            logger.warning(f"Could not load selective memory context: {e}")
        return block, stats

    def _record_memory_context_stats(self, task_id: str, mem_stats: Dict[str, int]) -> None:
        """Best-effort persistence of injection stats for the validation dump.

        Written via its own session so the caller's transaction (the task-loop
        session) is never committed here. Rows older than the retention cap
        are trimmed so the table cannot grow unbounded. Never raises.
        """
        try:
            from sqlalchemy import func
            from backend.models.database import MemoryContextLog, get_db_manager

            db_mgr = get_db_manager()
            with db_mgr.get_session() as sess:
                max_id = sess.query(func.max(MemoryContextLog.id)).scalar()
                sess.add(MemoryContextLog(
                    task_id=task_id,
                    available=mem_stats.get("available", 0),
                    relevant=mem_stats.get("relevant", 0),
                    connected=mem_stats.get("connected", 0),
                    links=mem_stats.get("links", 0),
                ))
                if max_id is not None:
                    sess.query(MemoryContextLog).filter(
                        MemoryContextLog.id <= max_id - MEMORY_CONTEXT_LOG_RETAIN + 1
                    ).delete(synchronize_session=False)
        except Exception as exc:  # pragma: no cover - best-effort
            logger.warning("Could not record memory context stats: %s", exc)

    def _build_context(
        self,
        task_id: str,
        original_request: str,
        normalized_goal: Optional[str],
        event_history: List[Dict[str, Any]],
        current_state: str,
        pending_question: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Build conversation context for the model.
        
        Args:
            task_id: Current task ID
            original_request: User's original request
            normalized_goal: Normalized task goal if available
            event_history: List of past events
            current_state: Current task state
            pending_question: Question awaiting user response
            
        Returns:
            List of message dicts for the API
        """
        messages = []
        
        # System prompt
        messages.append({
            "role": "system",
            "content": self._build_system_prompt()
        })
        
        # User request
        user_content = f"Task ID: {task_id}\n"
        user_content += f"Original Request: {original_request}\n"
        
        if normalized_goal:
            user_content += f"Normalized Goal: {normalized_goal}\n"
        
        user_content += f"Current State: {current_state}\n"
            
        if pending_question:
            user_content += f"\nPENDING QUESTION (awaiting your next action after user response): {pending_question}\n"

        previous_context = self._previous_task_context(task_id, original_request)
        if previous_context:
            user_content += "\n" + previous_context + "\n"

        # Fetch selectively-relevant persistent memories (anchored on the request)
        mem_block, mem_stats = self._select_memory_context(normalized_goal or original_request)
        if mem_block:
            user_content += mem_block
        logger.info(
            "Memory injection for task %s: %d relevant + %d linked of %d available, %d links",
            task_id, mem_stats["relevant"], mem_stats["connected"],
            mem_stats["available"], mem_stats["links"],
        )
        self._record_memory_context_stats(task_id, mem_stats)

        # Fetch episodic conversation turns from this session
        try:
            from backend.core.session import get_current_session_id
            from backend.models.database import ConversationTurn
            sess_id = get_current_session_id()
            turns = self.db.query(ConversationTurn).filter_by(session_id=sess_id).order_by(ConversationTurn.created_at.desc()).limit(5).all()
            if turns:
                user_content += "\nRECENT CONVERSATION (this session):\n"
                for t in reversed(turns):
                    prefix = "User" if t.role == "user" else "Synth"
                    user_content += f"- {prefix}: {t.summary}\n"
        except Exception as e:
            logger.warning(f"Could not load conversation turns: {e}")

        if event_history:
            recent_events = event_history[-10:]
            user_content += "\nRecent Activity:\n"
            for event in recent_events:
                event_type = event.get('event_type', 'unknown')
                event_data = event.get('event_data', {})

                if event_type == 'tool.executed':
                    tool_name = event_data.get('tool_name', 'unknown')
                    success = event_data.get('success', False)
                    summary = event_data.get('model_summary')
                    if summary is None:
                        summary = event_data.get('output')
                    if isinstance(summary, (dict, list)):
                        summary = json.dumps(summary, ensure_ascii=False, default=str)
                    elif summary is None:
                        summary = ''
                    summary = str(summary)
                    cap = self._tool_summary_cap(tool_name)
                    if len(summary) > cap:
                        summary = summary[:cap - 3] + '...'
                    status_str = "SUCCESS" if success else "FAILED"
                    user_content += f"- {tool_name}: {status_str} -> {summary}\n"
                elif event_type == 'skill.executed':
                    skill_name = event_data.get('skill_name', 'unknown')
                    success = event_data.get('success', False)
                    status_str = "SUCCESS" if success else "FAILED"
                    user_content += f"- Skill '{skill_name}': {status_str}\n"
                elif event_type == 'user.replied':
                    answer = event_data.get('answer', '')
                    user_content += f"- User replied: {answer}\n"
                else:
                    user_content += f"- {event_type}: {json.dumps(event_data, ensure_ascii=False, default=str)[:200]}\n"
        
        messages.append({
            "role": "user",
            "content": user_content
        })

        # Add recent event history (last 10 events) as native multi-turn messages
        if event_history:
            recent_events = event_history[-10:]
            for idx, event in enumerate(recent_events):
                event_type = event.get('event_type', 'unknown')
                event_data = event.get('event_data', {})
                evt_id = event.get('id', idx)
                call_id = f"call_{evt_id}"

                if event_type == 'tool.executed':
                    tool_name = event_data.get('tool_name', 'unknown')
                    success = event_data.get('success', False)
                    result_summary = event_data.get('model_summary')
                    if result_summary is None:
                        result_summary = event_data.get('output')
                    if isinstance(result_summary, (dict, list)):
                        result_summary = json.dumps(result_summary, ensure_ascii=False, default=str)
                    elif result_summary is None:
                        result_summary = ''
                    result_summary = str(result_summary)
                    cap = self._tool_summary_cap(tool_name)
                    if len(result_summary) > cap:
                        result_summary = result_summary[:cap - 3] + '...'

                    status_str = "SUCCESS" if success else "FAILED"
                    # Assistant tool call turn. Ollama Cloud rejects replay
                    # arguments sent as a JSON string ("{}"), so always send
                    # them as an object (the real stored params when present).
                    replay_args = event_data.get('params')
                    if isinstance(replay_args, str):
                        try:
                            replay_args = json.loads(replay_args)
                        except (json.JSONDecodeError, TypeError):
                            replay_args = {}
                    if not isinstance(replay_args, dict):
                        replay_args = {}
                    messages.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": replay_args,
                            }
                        }]
                    })
                    # Tool response turn
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": f"[{status_str}] {result_summary}"
                    })
                elif event_type == 'skill.executed':
                    skill_name = event_data.get('skill_name', 'unknown')
                    success = event_data.get('success', False)
                    status_str = "SUCCESS" if success else "FAILED"
                    messages.append({
                        "role": "user",
                        "content": f"[Skill Execution] Skill '{skill_name}' status: {status_str}"
                    })
                elif event_type == 'user.replied':
                    answer = event_data.get('answer', '')
                    messages.append({
                        "role": "user",
                        "content": f"[User Response] User replied: {answer}"
                    })
        
        return messages

    _REASONING_FIELDS = frozenset({
        "reasoning", "reasoning_content", "thinking", "thought", "chain_of_thought",
        "rationale", "internal_thoughts",
    })

    def _redact_reasoning_fields(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Discard model reasoning/thinking fields before any further handling.

        Reasoning traces are private and unbounded; they must never reach
        task events, decision history, logs, or a later model request.
        """
        if not isinstance(message, dict):
            return message
        return {k: v for k, v in message.items() if k.lower() not in self._REASONING_FIELDS}

    def _parse_decision(self, response: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Parse model response into decision type and parameters.
        
        Args:
            response: Raw API response from Ollama Cloud
            
        Returns:
            Tuple of (decision_type, decision_params)
            
        Raises:
            ModelDecisionError: If response is invalid
        """
        try:
            # Check for tool calls in response - supports both OpenAI format
            # (choices[0].message.tool_calls) and Ollama native format
            # (message.tool_calls at top level).
            message = None
            choices = response.get('choices', [])
            if choices:
                message = choices[0].get('message', {})
            else:
                message = response.get('message', {})

            message = self._redact_reasoning_fields(message)

            if not message:
                raise ModelDecisionError(
                    code="EMPTY_RESPONSE",
                    message="Model returned no message"
                )
            
            tool_calls = message.get('tool_calls', [])
            
            if not tool_calls:
                content = message.get('content', '')
                if content:
                    raise ModelDecisionError(
                        code="MISSING_CONTROL_CALL",
                        message="Model returned text without a required control or action call"
                    )
                raise ModelDecisionError(
                    code="EMPTY_MESSAGE",
                    message="Model returned empty response"
                )
            
            # We expect exactly ONE tool call; some models emit multiple.
            # Take only the first and discard the rest (instead of rejecting
            # outright) so the task can continue.
            if len(tool_calls) > 1:
                logger.warning(
                    "Model returned %d parallel tool calls; using only the first",
                    len(tool_calls),
                )
            
            tool_call = tool_calls[0]
            function = tool_call.get('function', {})
            decision_type = function.get('name', '')
            
            # Validate decision type
            valid_types = [
                DecisionType.COMPLETE_TASK,
                DecisionType.FAIL_TASK,
                DecisionType.REQUEST_USER_INPUT,
                DecisionType.RUN_SKILL,
                DecisionType.EXECUTE_TOOL
            ]
            
            # Parse arguments (Ollama may return an object or a JSON string)
            raw_args = function.get('arguments', {})
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError as e:
                    raise ModelDecisionError(
                        code="INVALID_JSON",
                        message=f"Failed to parse arguments: {str(e)}"
                    )
            else:
                args = raw_args or {}
            
            if decision_type not in valid_types:
                # The model may call a registered tool directly instead of
                # wrapping it in execute_tool - map that to EXECUTE_TOOL. Only
                # tools in the policy-filtered model-visible subset may be
                # called: a guessed mutating/GUI tool name must be rejected,
                # not mapped, even if it is registered.
                if decision_type in {t["name"] for t in self._model_visible_tools()}:
                    params = args or {}
                    if not isinstance(params, dict):
                        params = {}
                    return DecisionType.EXECUTE_TOOL, {
                        "tool_name": decision_type,
                        "params": params
                    }
                raise ModelDecisionError(
                    code="INVALID_DECISION_TYPE",
                    message=f"Unknown decision type: {decision_type}"
                )
            
            return decision_type, args
            
        except ModelDecisionError:
            raise
        except Exception as e:
            raise ModelDecisionError(
                code="PARSE_ERROR",
                message=f"Failed to parse response: {str(e)}"
            )
    
    async def make_decision(
        self,
        task_id: str,
        original_request: str,
        normalized_goal: Optional[str],
        event_history: List[Dict[str, Any]],
        current_state: str,
        pending_question: Optional[str] = None,
        repair_context: Optional[str] = None,
        budget: Optional[TaskBudget] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Make a decision for the current task state.
        
        Args:
            task_id: Task ID
            original_request: User's original request
            normalized_goal: Normalized goal
            event_history: Event history
            current_state: Current state
            pending_question: Pending question if any
            repair_context: Error context for repair attempts
            
        Returns:
            Tuple of (decision_type, decision_params)
            
        Raises:
            ModelDecisionError: If decision cannot be made
        """
        # Build context
        messages = self._build_context(
            task_id=task_id,
            original_request=original_request,
            normalized_goal=normalized_goal,
            event_history=event_history,
            current_state=current_state,
            pending_question=pending_question
        )
        
        # Add repair context if this is a repair attempt
        if repair_context:
            messages.append({
                "role": "user",
                "content": f"REPAIR REQUIRED: {repair_context}\n\nPlease provide a valid decision."
            })
        
        # Get tools for the model: only the policy-filtered read-only subset,
        # plus the control functions. Mutating/GUI tools are never exposed.
        tool_metadata = list(self._model_visible_tools())
        tool_metadata.extend(self._decision_tools())
        formatted_tools = format_tools_for_ollama(tool_metadata)
        
        try:
            # Call Ollama Cloud
            response = await self.adapter.execute_tool_call(
                messages=messages,
                tools=formatted_tools,
                max_tokens=self.MAX_TOKENS_PER_REQUEST
            )
            
            # Track tokens (estimate)
            usage = response.get('usage', {})
            tokens_used = usage.get('total_tokens', 0)
            self.token_count += tokens_used
            
            # Record the Cloud request + token usage against the per-task budget.
            if budget is not None:
                budget.record_cloud_request(
                    input_tokens=usage.get('prompt_tokens') or usage.get('prompt_eval_count') or 0,
                    output_tokens=usage.get('completion_tokens') or usage.get('eval_count') or 0,
                )
            
            if self.token_count > self.MAX_TOKENS_PER_TASK:
                logger.warning(f"Token budget exceeded for task: {self.token_count}")
            
            # Parse decision
            decision_type, params = self._parse_decision(response)
            
            # Store in history
            self.decision_history.append({
                'type': decision_type,
                'params': params,
                'timestamp': datetime.utcnow().isoformat(),
                'tokens': tokens_used
            })
            
            logger.info(f"Model decision: {decision_type}")
            return decision_type, params
            
        except BudgetExceededError:
            raise
        except ModelDecisionError:
            raise
        except Exception as e:
            logger.error(f"Model request failed: {e}")
            raise ModelDecisionError(
                code="CLOUD_ERROR",
                message=f"Ollama Cloud request failed: {str(e)}"
            )
    
    def get_token_usage(self) -> int:
        """Get total tokens used for this task."""
        return self.token_count
    
    def get_decision_history(self) -> List[Dict[str, Any]]:
        """Get decision history for this task."""
        return self.decision_history.copy()
