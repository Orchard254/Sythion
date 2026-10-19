"""
Compatibility facade for memory-related tools.

Historically this module mixed two concerns:
- durable user memory entry/edge tools
- runtime activity-history recall

The implementation now lives in focused modules:
- `persistent_memory_tools.py` for `MemoryEntry` / `MemoryEdge` access
- `activity_recall_tool.py` for task/event/conversation history recall

This file remains the stable import surface for existing callers and tests.
"""

from __future__ import annotations

try:
    from backend.tools.activity_recall_tool import (
        _ACTIVITY_SUMMARY_CAP,
        _as_naive_utc,
        _bound_activity_summary,
        _event_summary,
        ActivityRecallTool,
    )
    from backend.tools.persistent_memory_tools import (
        _DEVICE_IDENTITY_KEYS,
        _EPHEMERAL_STATE_CATEGORIES,
        _EPHEMERAL_STATE_KEYS,
        _RELATION_RE,
        _entry_match_score,
        _query_tokens,
        _rejects_live_state,
        _stem,
        MemoryForgetTool,
        MemoryLinkTool,
        MemoryRecallRelatedTool,
        MemoryRecallTool,
        MemoryStoreTool,
    )
except ImportError:
    from tools.activity_recall_tool import (
        _ACTIVITY_SUMMARY_CAP,
        _as_naive_utc,
        _bound_activity_summary,
        _event_summary,
        ActivityRecallTool,
    )
    from tools.persistent_memory_tools import (
        _DEVICE_IDENTITY_KEYS,
        _EPHEMERAL_STATE_CATEGORIES,
        _EPHEMERAL_STATE_KEYS,
        _RELATION_RE,
        _entry_match_score,
        _query_tokens,
        _rejects_live_state,
        _stem,
        MemoryForgetTool,
        MemoryLinkTool,
        MemoryRecallRelatedTool,
        MemoryRecallTool,
        MemoryStoreTool,
    )

__all__ = [
    "ActivityRecallTool",
    "MemoryForgetTool",
    "MemoryLinkTool",
    "MemoryRecallRelatedTool",
    "MemoryRecallTool",
    "MemoryStoreTool",
    "_ACTIVITY_SUMMARY_CAP",
    "_DEVICE_IDENTITY_KEYS",
    "_EPHEMERAL_STATE_CATEGORIES",
    "_EPHEMERAL_STATE_KEYS",
    "_RELATION_RE",
    "_as_naive_utc",
    "_bound_activity_summary",
    "_entry_match_score",
    "_event_summary",
    "_query_tokens",
    "_rejects_live_state",
    "_stem",
]
