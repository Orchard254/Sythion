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

from backend.adapters.ollama_adapter import get_adapter, format_tools_for_ollama
from backend.tools.executor import ToolExecutor
from backend.core.budgets import TaskBudget, BudgetExceededError
from backend.services.model_context_builder import ModelContextBuilder
from backend.services.model_prompt_contract import build_system_prompt
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional as Opt

logger = logging.getLogger(__name__)


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
    
    def __init__(self, db_session: Session, tool_executor: ToolExecutor):
        self.db = db_session
        self.tool_executor = tool_executor
        self.adapter = get_adapter()
        self.token_count = 0
        self.decision_history: List[Dict[str, Any]] = []
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

        active_autonomy = os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level)
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
        return self.context_builder._is_continuation_request(request)

    def _previous_task_context(self, task_id: str, request: str) -> str:
        """Deterministic continuity: seed a continuation task with the last task's
        goal and recent actions.
        """
        return self.context_builder.previous_task_context(task_id, request)

    def _select_memory_context(self, anchor: str):
        """Selectively inject persistent memory relevant to the current request."""
        return self.context_builder.select_memory_context(anchor)

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
    ) -> List[Dict[str, Any]]:
        """Build conversation context for the model."""
        return self.context_builder.build_context(
            system_prompt=self._build_system_prompt(),
            task_id=task_id,
            original_request=original_request,
            normalized_goal=normalized_goal,
            event_history=event_history,
            current_state=current_state,
            pending_question=pending_question,
            session_id=session_id,
        )

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
            
            # Parse all tool calls into a list for sequential execution
            parsed_tool_calls = []
            for tc in tool_calls:
                fn = tc.get('function', {})
                dt = fn.get('name', '')
                raw = fn.get('arguments', {})
                if isinstance(raw, str):
                    try:
                        parsed_args = json.loads(raw)
                    except json.JSONDecodeError as e:
                        raise ModelDecisionError(
                            code="INVALID_JSON",
                            message=f"Failed to parse arguments: {str(e)}"
                        )
                else:
                    parsed_args = raw or {}
                parsed_tool_calls.append({'decision_type': dt, 'args': parsed_args})

            if len(parsed_tool_calls) > 1:
                logger.info(
                    "Model returned %d tool calls; executing sequentially",
                    len(parsed_tool_calls),
                )

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
            
            # Store extra tool calls for sequential execution by the loop
            self._pending_tool_calls = parsed_tool_calls[1:] if len(parsed_tool_calls) > 1 else []
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
            # Call Ollama Cloud under a hard deadline so a slow/unresponsive
            # cloud call cannot leave the task loop hanging silently.
            from backend.core.config import settings
            decision_timeout = getattr(settings, "decision_timeout_seconds", 120.0)
            response = await asyncio.wait_for(
                self.adapter.execute_tool_call(
                    messages=messages,
                    tools=formatted_tools,
                    max_tokens=self.MAX_TOKENS_PER_REQUEST
                ),
                timeout=decision_timeout,
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
        """Get the decision history for this task."""
        return self.decision_history

    def pop_pending_tool_calls(self) -> List[Dict[str, Any]]:
        """Get and clear pending tool calls for sequential execution."""
        pending = self._pending_tool_calls
        self._pending_tool_calls = []
        return pending.copy()
