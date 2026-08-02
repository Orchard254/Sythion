"""
Per-task budget tracking and enforcement.

Each task tracks its Cloud request count, input/output tokens, executed
actions, repair attempts, and wall-clock elapsed time against the limits in
`backend/core/config.py`. When a limit is exceeded the task must fail honestly
with the matching code (the execution loop's responsibility) -- never mutate,
never retry, never fall back locally.
"""

from __future__ import annotations

import time
from typing import Dict, Optional


class BudgetExceededError(RuntimeError):
    """Raised when a per-task budget limit is exceeded."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class TaskBudget:
    """Tracks and enforces per-task resource budgets."""

    def __init__(
        self,
        *,
        max_actions: Optional[int] = None,
        max_cloud_requests: Optional[int] = None,
        max_input_tokens: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        max_elapsed_seconds: Optional[int] = None,
        max_repairs: Optional[int] = None,
    ):
        from backend.core.config import settings

        self.max_actions = max_actions if max_actions is not None else settings.max_actions_per_task
        self.max_cloud_requests = (
            max_cloud_requests if max_cloud_requests is not None else settings.max_cloud_requests_per_task
        )
        self.max_input_tokens = (
            max_input_tokens if max_input_tokens is not None else settings.max_input_tokens
        )
        self.max_output_tokens = (
            max_output_tokens if max_output_tokens is not None else settings.max_output_tokens
        )
        self.max_elapsed_seconds = (
            max_elapsed_seconds if max_elapsed_seconds is not None else settings.max_elapsed_time_seconds
        )
        self.max_repairs = max_repairs if max_repairs is not None else settings.max_repair_attempts

        self.cloud_requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.actions = 0
        self.repairs = 0
        self._started_at = time.monotonic()

    # --- recording -----------------------------------------------------------

    def record_cloud_request(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Record one Cloud request and its token usage, checking limits."""
        self.cloud_requests += 1
        self.input_tokens += int(input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)
        self.check_limits()

    def record_action(self) -> None:
        """Record one executed action, checking the action limit."""
        self.actions += 1
        self.check_limits()

    def record_repair(self) -> None:
        """Record one model repair attempt (enforced by the loop)."""
        self.repairs += 1

    # --- checks --------------------------------------------------------------

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started_at

    def check_elapsed(self) -> None:
        """Raise when the wall-clock time budget is exhausted."""
        if self.elapsed_seconds() > self.max_elapsed_seconds:
            raise BudgetExceededError(
                "ELAPSED_TIME_EXCEEDED",
                f"Task exceeded the {self.max_elapsed_seconds}s elapsed-time budget",
            )

    def check_limits(self) -> None:
        """Raise with the specific code for the first exceeded limit."""
        if self.actions > self.max_actions:
            raise BudgetExceededError(
                "ACTION_LIMIT_EXCEEDED",
                f"Exceeded the {self.max_actions}-action per-task budget",
            )
        if self.cloud_requests > self.max_cloud_requests:
            raise BudgetExceededError(
                "CLOUD_REQUEST_LIMIT_EXCEEDED",
                f"Exceeded the {self.max_cloud_requests}-request per-task budget",
            )
        if self.input_tokens > self.max_input_tokens:
            raise BudgetExceededError(
                "INPUT_TOKEN_LIMIT_EXCEEDED",
                f"Exceeded the {self.max_input_tokens}-input-token per-task budget",
            )
        if self.output_tokens > self.max_output_tokens:
            raise BudgetExceededError(
                "OUTPUT_TOKEN_LIMIT_EXCEEDED",
                f"Exceeded the {self.max_output_tokens}-output-token per-task budget",
            )

    # --- reporting -----------------------------------------------------------

    def snapshot(self) -> Dict[str, object]:
        """Return a redacted, evidence-safe summary of current usage."""
        return {
            "cloud_requests": self.cloud_requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "actions": self.actions,
            "repairs": self.repairs,
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
            "limits": {
                "max_actions": self.max_actions,
                "max_cloud_requests": self.max_cloud_requests,
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": self.max_output_tokens,
                "max_elapsed_seconds": self.max_elapsed_seconds,
                "max_repairs": self.max_repairs,
            },
        }
