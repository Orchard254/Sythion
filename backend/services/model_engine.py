"""
Model Decision Engine for Sythion (Synth).
Handles LLM orchestration, context building, and decision parsing.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.runtime.interfaces import ModelClient
from backend.runtime.model_client import get_default_model_client
from backend.adapters.ollama_adapter import format_tools_for_ollama
from backend.tools.executor import ToolExecutor
from backend.core.budgets import TaskBudget, BudgetExceededError
from backend.services.model_context_builder import ModelContextBuilder
from backend.services.model_prompt_contract import build_system_prompt
from backend.services.reasoning_dispatch import ReasoningJob, ReasoningPurpose, ReasoningJournalError, dispatch_reasoning
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class CompleteTaskInput(BaseModel):
    """Input for complete_task decision."""
    model_config = ConfigDict(extra="forbid")
    result: str = Field(..., description="The user-facing response message or result text. MUST be written directly to the user. complete_task finalizes the task; use request_user_input instead when waiting for an answer.")

class FailTaskInput(BaseModel):
    """Input for fail_task decision."""
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., description="Reason the task could not be completed")

class RequestUserInput(BaseModel):
    """Input for request_user_input decision."""
    model_config = ConfigDict(extra="forbid")
    question: str = Field(..., description="Question to ask the user. This pauses the task as WAITING_FOR_USER; do not also call complete_task.")

class RunSkillInput(BaseModel):
    """Input for run_skill decision."""
    model_config = ConfigDict(extra="forbid")
    skill_id: str = Field(..., description="ID of the skill to run")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Skill input parameters")

class ExecuteToolInput(BaseModel):
    """Input for execute_tool decision."""
    model_config = ConfigDict(extra="forbid")
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

    # gemma4:31b supports 256K tokens — output and task caps raised accordingly
    MAX_TOKENS_PER_REQUEST = 8192    # was 4000
    MAX_TOKENS_PER_TASK = 200000     # was 50000 (soft warning threshold only)


    # Default char caps for tool summaries injected into model context.
    # gemma4:31b has 256K token window — these defaults are now generous.
    _DEFAULT_SUMMARY_CAP = 1500      # was 220
    _TOOL_SUMMARY_CAPS = {
        "browser_tabs": 8000,        # was 2000
        "browser_read_page": 12000,  # was 3000
        "browser_screenshot": 1000,  # was 400
    }
    _CONTINUATION_SUMMARY_CAP = 1000  # was 160 — continuation tasks see rich context
    _CONTINUATION_SCALE = _CONTINUATION_SUMMARY_CAP / _DEFAULT_SUMMARY_CAP

    def _tool_summary_cap(self, tool_name: str, *, continuation: bool = False) -> int:
        """Per-tool char cap for a tool-result summary in the model context."""
        cap = self._TOOL_SUMMARY_CAPS.get(tool_name, self._DEFAULT_SUMMARY_CAP)
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
    
    def __init__(
        self,
        db_session: Session,
        tool_executor: ToolExecutor,
        model_client: Optional[ModelClient] = None,
    ):
        self.db = db_session
        self.tool_executor = tool_executor
        # The reasoning engine sees one explicit model client. When injected,
        # never instantiate the default Ollama client as an accidental second
        # provider: this would consult its credentials and bind task state
        # across provider identities. The old adapter attribute stays available
        # for compatibility, pointing to the SAME selected client only.
        self.model_client: ModelClient = model_client if model_client is not None else get_default_model_client()
        self.adapter = getattr(self.model_client, "_adapter", None)
        # Phase 6: bounded evidence-based fallback state.
        from backend.core.config import settings

        self._model_override: Optional[str] = None
        self._fallback_attempts = 0
        self._max_fallback_attempts = max(1, settings.model_max_attempts - 1)
        self.token_count = 0
        self.decision_history: List[Dict[str, Any]] = []
        # Explicitly wired by TaskExecutionLoop; standalone/model-only callers
        # do not implicitly gain write authority over a task event ledger.
        self.reasoning_journal = None
        self._pending_tool_calls: List[Dict[str, Any]] = []
        
        from backend.core.config import settings
        self._DEFAULT_SUMMARY_CAP = settings.tool_summary_cap_default
        self._TOOL_SUMMARY_CAPS = {
            # Browser tools — now fully configurable via settings
            "browser_tabs": getattr(settings, "tool_summary_cap_browser_tabs", 8000),
            "browser_read_page": getattr(settings, "tool_summary_cap_browser_read_page", 12000),
            "browser_screenshot": getattr(settings, "tool_summary_cap_browser_screenshot", 1000),
        }
        self._CONTINUATION_SUMMARY_CAP = round(self._DEFAULT_SUMMARY_CAP * 0.73)
        self._CONTINUATION_SCALE = self._CONTINUATION_SUMMARY_CAP / self._DEFAULT_SUMMARY_CAP
        
        self.context_builder = ModelContextBuilder(
            db_session=db_session,
            tool_summary_cap=lambda tool_name: self._tool_summary_cap(tool_name),
            continuation_summary_cap=lambda tool_name: self._tool_summary_cap(tool_name, continuation=True),
            continuation_patterns=self._CONTINUATION_PATTERNS,
        )
        
    def _build_system_prompt(self) -> str:
        """Build the system prompt for Sythion (Synth) behavior."""
        available_tools = ", ".join(t["name"] for t in self._model_visible_tools()) or "(none registered)"
        from backend.core.config import settings

        active_autonomy = str(os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level) or "").strip()
        return build_system_prompt(
            available_tools=available_tools,
            active_autonomy=active_autonomy,
        )

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
        active_autonomy = str(os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level) or "").strip()
        for name in self.tool_executor.list_tools():
            tool = self.tool_executor.get_tool(name)
            if not tool:
                continue
            reqs = tool.get_policy_requirements()
            if reqs.get("requires_admin"):
                continue
            if (
                reqs.get("privacy_sensitive")
                or reqs.get("requires_screen_access")
                or reqs.get("requires_ui_automation")
            ):
                continue
            if active_autonomy != "autonomous":
                if reqs.get("mutating") or reqs.get("destructive"):
                    if not reqs.get("app_internal"):
                        continue
                if reqs.get("requires_confirmation"):
                    continue
                if reqs.get("risk_level") in {"medium", "high"}:
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
                "description": "Finalize the task with the final user-facing result; this ends the task and must not ask a question or request input",
                "input_schema": CompleteTaskInput,
            },
            {
                "name": DecisionType.FAIL_TASK,
                "description": "Fail the task with a reason",
                "input_schema": FailTaskInput,
            },
            {
                "name": DecisionType.REQUEST_USER_INPUT,
                "description": "Ask the user a question or request clarification/confirmation and pause the task (WAITING_FOR_USER); use this alone, never together with complete_task",
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
        return self.context_builder._is_continuation_request(request)

    def _previous_task_context(self, task_id: str, request: str) -> str:
        """Deterministic continuity: seed a continuation task with the last task's
        goal and recent actions.
        """
        return self.context_builder.previous_task_context(task_id, request)

    def _select_memory_context(
        self,
        anchor: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        access_context: Optional[Any] = None,
        context: Optional[Any] = None,
    ):
        """Selectively inject persistent memory relevant to the current request.

        Scope propagation (S014): the task/session identity and the optional
        typed access context are threaded into selection so scoped memory
        cannot leak across tasks/sessions. All scope arguments default to
        ``None``, preserving the historical unscoped call exactly.
        """
        return self.context_builder.select_memory_context(
            anchor,
            session_id=session_id,
            task_id=task_id,
            access_context=access_context,
            context=context,
        )

    def _record_memory_context_stats(self, task_id: str, mem_stats: Dict[str, int]) -> None:
        """Best-effort persistence of injection stats for the validation dump."""
        self.context_builder.record_memory_context_stats(task_id, mem_stats)

    def _build_context(
        self,
        task_id: str,
        original_request: str,
        normalized_goal: Optional[str],
        event_history: List[Dict[str, Any]],
        current_state: str,
        pending_question: Optional[str] = None,
        session_id: Optional[str] = None,
        access_context: Optional[Any] = None,
        context: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Build conversation context for the model.

        The optional typed access context is forwarded to memory selection;
        it defaults to ``None`` so existing callers keep the legacy
        task/session-only behavior.
        """
        return self.context_builder.build_context(
            system_prompt=self._build_system_prompt(),
            task_id=task_id,
            original_request=original_request,
            normalized_goal=normalized_goal,
            event_history=event_history,
            current_state=current_state,
            pending_question=pending_question,
            session_id=session_id,
            access_context=access_context,
            context=context,
        )

    _REASONING_FIELDS = frozenset({
        "reasoning", "reasoning_content", "thinking", "thought", "chain_of_thought",
        "rationale", "internal_thoughts",
    })

    # Decisions that end the task or park it waiting for the user. Nothing
    # may execute after the first such decision in the same response.
    _TERMINAL_DECISIONS = (
        DecisionType.COMPLETE_TASK,
        DecisionType.FAIL_TASK,
        DecisionType.REQUEST_USER_INPUT,
    )

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
        Parse a model response into a decision type and its parameters.

        Accepts both the OpenAI shape (``choices[0].message.tool_calls``)
        and the Ollama native shape (``message.tool_calls``); reasoning
        fields are redacted before parsing. An explicit
        ``request_user_input`` call wins over a contradictory terminal
        decision in the same response. A direct call to a model-visible
        tool is mapped to ``execute_tool``. Remaining calls after the
        primary decision are stored on ``self._pending_tool_calls``,
        truncated at the first terminal decision.

        Args:
            response: Raw API response from the model provider.

        Returns:
            Tuple of (decision_type, decision_params).

        Raises:
            ModelDecisionError: If the response has no valid control or
                action call, or cannot be parsed.
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
            
            # Parse all tool calls into a list for sequential execution
            parsed_tool_calls = []
            for tool_call in tool_calls:
                function_call = tool_call.get('function', {})
                decision_type_name = function_call.get('name', '')
                raw_arguments = function_call.get('arguments', {})
                if isinstance(raw_arguments, str):
                    try:
                        parsed_args = json.loads(raw_arguments)
                    except json.JSONDecodeError as e:
                        raise ModelDecisionError(
                            code="INVALID_JSON",
                            message=f"Failed to parse arguments: {str(e)}"
                        )
                else:
                    parsed_args = raw_arguments or {}
                parsed_tool_calls.append({'decision_type': decision_type_name, 'args': parsed_args})

            if len(parsed_tool_calls) > 1:
                logger.info(
                    "Model returned %d tool calls; executing sequentially",
                    len(parsed_tool_calls),
                )

            # An explicit clarification/confirmation request wins over a
            # contradictory task-final decision. A single response cannot both
            # finish a task and wait for the user; prioritize asking and drop all
            # terminal decisions except the chosen request.
            request_index = next(
                (i for i, parsed_call in enumerate(parsed_tool_calls)
                 if parsed_call["decision_type"] == DecisionType.REQUEST_USER_INPUT),
                None,
            )
            if request_index is not None:
                # Keep earlier non-terminal calls in their original order.
                preserved = [
                    parsed_call for parsed_call in parsed_tool_calls[:request_index]
                    if parsed_call["decision_type"] not in self._TERMINAL_DECISIONS
                ]
                chosen = parsed_tool_calls[request_index]
                request_args = chosen.get("args", {})
                if isinstance(request_args, str):
                    try:
                        request_args = json.loads(request_args)
                    except json.JSONDecodeError:
                        request_args = {}
                if not isinstance(request_args, dict):
                    request_args = {}
                self._pending_tool_calls = preserved
                return DecisionType.REQUEST_USER_INPUT, request_args

            # First tool call is the primary decision
            primary = parsed_tool_calls[0]
            decision_type = primary['decision_type']
            args = primary['args']

            # Validate decision type
            valid_types = [
                DecisionType.COMPLETE_TASK,
                DecisionType.FAIL_TASK,
                DecisionType.REQUEST_USER_INPUT,
                DecisionType.RUN_SKILL,
                DecisionType.EXECUTE_TOOL
            ]

            if decision_type not in valid_types:
                # The model may call a registered tool directly instead of
                # wrapping it in execute_tool - map that to EXECUTE_TOOL. Only
                # tools in the policy-filtered model-visible subset may be
                # called: a guessed mutating/GUI tool name must be rejected,
                # not mapped, even if it is registered.
                if decision_type in tuple(t["name"] for t in self._model_visible_tools()):
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
            
            # Asking the user is a terminal wait: never execute later calls from the
            # same response, especially a trailing complete_task that would falsely
            # finish work the task is still waiting to clarify or resume.
            if decision_type in self._TERMINAL_DECISIONS:
                pending_tool_calls = []
            else:
                # A terminal decision (complete/fail/ask) ends or waits the task.
                # Nothing may execute after the first such decision in the same
                # response, so truncate the pending queue at the first terminal.
                pending_tool_calls = []
                seen_terminal = False
                for parsed_call in parsed_tool_calls[1:]:
                    if seen_terminal:
                        break
                    pending_tool_calls.append(parsed_call)
                    if parsed_call["decision_type"] in self._TERMINAL_DECISIONS:
                        seen_terminal = True
            self._pending_tool_calls = pending_tool_calls
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
        budget: Optional[TaskBudget] = None,
        session_id: Optional[str] = None,
        access_context: Optional[Any] = None,
        context: Optional[Any] = None,
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
            pending_question=pending_question,
            session_id=session_id,
            access_context=access_context,
            context=context,
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
            # Call the model under a hard deadline so a slow/unresponsive
            # provider cannot leave the task loop hanging silently.
            from backend.core.config import settings
            decision_timeout = getattr(settings, "decision_timeout_seconds", 120.0)
            target_model = self._model_override

            async def _call():
                if hasattr(self.model_client, "decide"):
                    import inspect

                    if "model_id" in inspect.signature(
                        self.model_client.decide
                    ).parameters:
                        return await self.model_client.decide(
                            messages=messages,
                            tools=formatted_tools,
                            max_tokens=self.MAX_TOKENS_PER_REQUEST,
                            model_id=target_model,
                        )
                return await self.model_client.decide(
                    messages=messages,
                    tools=formatted_tools,
                    max_tokens=self.MAX_TOKENS_PER_REQUEST,
                )

            response = await dispatch_reasoning(
                ReasoningJob(
                    purpose=ReasoningPurpose.TASK_DECISION,
                    work_id=task_id,
                    timeout_seconds=decision_timeout,
                ),
                _call,
                on_event=self.reasoning_journal,
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
        except ReasoningJournalError as exc:
            # Missing/failed receipts are an admission failure, not a cloud
            # error. Never fall back to another provider or replay this result.
            raise ModelDecisionError(
                code="REASONING_JOURNAL_ERROR",
                message="Reasoning invocation could not be durably recorded",
            ) from exc
        except ModelDecisionError:
            raise
        except asyncio.TimeoutError as e:
            # Phase 6: classify timeout; failed attempts consume budget too
            # (the provider did process the request until the deadline).
            if budget is not None:
                try:
                    budget.record_cloud_request()
                except BudgetExceededError as bexc:
                    logger.warning(f"Fallback blocked: {bexc.message}")
                    raise ModelDecisionError(code=bexc.code, message=bexc.message)
            fallback = self._maybe_fallback("timeout", str(e)[:200])
            if fallback:
                return await self.make_decision(
                    task_id=task_id,
                    original_request=original_request,
                    normalized_goal=normalized_goal,
                    event_history=event_history,
                    current_state=current_state,
                    pending_question=pending_question,
                    repair_context=repair_context,
                    budget=budget,
                    session_id=session_id,
                    access_context=access_context,
                    context=context,
                )
            raise ModelDecisionError(code="TIMEOUT", message=f"model request timed out: {e}")
        except Exception as e:
            logger.error(f"Model request failed: {e}")
            if budget is not None:
                try:
                    budget.record_cloud_request()
                except BudgetExceededError as bexc:
                    logger.warning(f"Fallback blocked: {bexc.message}")
                    raise ModelDecisionError(code=bexc.code, message=bexc.message)
            fallback = self._maybe_fallback("provider_error", str(e)[:200])
            if fallback:
                return await self.make_decision(
                    task_id=task_id,
                    original_request=original_request,
                    normalized_goal=normalized_goal,
                    event_history=event_history,
                    current_state=current_state,
                    pending_question=pending_question,
                    repair_context=repair_context,
                    budget=budget,
                    session_id=session_id,
                    access_context=access_context,
                    context=context,
                )
            raise ModelDecisionError(
                code="CLOUD_ERROR",
                message=f"Ollama Cloud request failed: {str(e)}"
            )

    def _maybe_fallback(self, category: str, detail: str) -> bool:
        """Bounded, audited model fallback (Phase 6).

        Attempts at most ``model_max_attempts - 1`` fallbacks per engine
        instance; each attempt consumes normal cloud-request budget because
        the retry goes through the same decision call. Returns True when a
        fallback candidate was selected (caller retries the decision).
        """
        if self._fallback_attempts >= self._max_fallback_attempts:
            return False
        try:
            from backend.models.database import ModelSelection
            from backend.services.model_selection import (
                ModelRequirements,
                ModelSelector,
                record_failure,
            )

            failed_model = self._model_override
            if not failed_model:
                try:
                    from backend.runtime.model_provider import (
                        get_default_provider,
                    )

                    failed_model = get_default_provider().selected_model_id()
                except Exception:
                    failed_model = None
            selector = ModelSelector(self.db)
            requirements = ModelRequirements(
                tool_calling=True, structured_output=True, task_class="general")
            record_failure(self.db, failed_model or "unknown", category, detail)

            result = selector.next_fallback(requirements, failed_model or "")
            if not result.selected:
                return False

            previous = failed_model
            self._model_override = result.selected
            self._fallback_attempts += 1
            self.db.add(ModelSelection(
                requirements_json=json.dumps(requirements.to_dict()),
                selected_model=result.selected,
                rejected_json=json.dumps(result.rejected),
                reason=result.reason[:300],
                fallback_of=previous,
                created_at=datetime.utcnow(),
            ))
            self.db.commit()
            logger.warning(
                "Model %s failed (%s); falling back to %s (attempt %d/%d)",
                previous, category, result.selected,
                self._fallback_attempts, self._max_fallback_attempts,
            )
            return True
        except Exception as exc:  # pragma: no cover - never mask the original
            logger.warning(f"Fallback selection failed: {exc}")
            return False
    
    def get_token_usage(self) -> int:
        """Get total tokens used for this task."""
        return self.token_count

    def get_decision_history(self) -> List[Dict[str, Any]]:
        """Get the decision history for this task."""
        return self.decision_history

    def pop_pending_tool_calls(self) -> List[Dict[str, Any]]:
        """Get and clear pending tool calls for sequential execution."""
        pending = self._pending_tool_calls
        self._pending_tool_calls = []
        return pending.copy()
