"""
Persistent memory-entry and memory-edge tools for Sythion.

This module owns durable user memory access over the assistant's own stores:

- `MemoryEntry` key/value persistence for preferences, profile facts, and habits
- `MemoryEdge` graph links between durable memory facts

Runtime activity history recall lives separately in `activity_recall_tool.py` so
persistent user memory does not get conflated with task/event ledger recall.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Generic, TypeVar

try:
    from backend.api.schemas import (
        MemoryForgetInput,
        MemoryLinkInput,
        MemoryRecallInput,
        MemoryRecallRelatedInput,
        MemoryStoreInput,
        MemoryStatsInput,
        MemoryExportInput,
        ToolOutput,
    )
    from backend.models.database import MemoryEdge, MemoryEntry, get_db_manager
    from backend.tools.executor import BaseTool
except ImportError:
    from api.schemas import (
        MemoryForgetInput,
        MemoryLinkInput,
        MemoryRecallInput,
        MemoryRecallRelatedInput,
        MemoryStoreInput,
        MemoryStatsInput,
        MemoryExportInput,
        ToolOutput,
    )
    from models.database import MemoryEdge, MemoryEntry, get_db_manager
    from tools.executor import BaseTool

_RELATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")

_DEVICE_IDENTITY_KEYS = frozenset({
    "approved_roots", "file_roots", "root_path", "system_root_path",
    "user_desktop_path", "desktop_path", "downloads_path", "documents_path",
    "home_dir", "home_directory", "env_home_dir", "hostname",
    "windows_user", "os_user",
})

_EPHEMERAL_STATE_KEYS = frozenset({
    "current_time", "system_time", "time", "date", "today",
    "active_window", "current_window", "foreground_window",
    "disk_usage", "disk_usage_documents", "disk_free", "disk_total",
    "battery", "battery_level", "battery_status",
    "installed_apps", "app_list", "processes", "process_list",
    "running_processes", "system_overview", "system_summary",
    "environment_summary", "env_summary", "summary",
})

_EPHEMERAL_STATE_CATEGORIES = frozenset({"environment"})


def _rejects_live_state(key: str, value: str, category: str | None) -> str | None:
    """Refuse to persist live system state or device identity."""
    k = (key or "").lower().strip()
    cat = (category or "").lower().strip()
    v = value or ""

    if k in _DEVICE_IDENTITY_KEYS:
        return (
            f"device-identity key '{key}' is refused: device paths, approved roots, and "
            "usernames are live facts to query via `system_file_roots`, never stored"
        )
    if k in _EPHEMERAL_STATE_KEYS:
        return (
            f"ephemeral system-state key '{key}' is refused: live state (time, disk, battery, "
            "active window, installed apps, processes) is re-queryable via its system/app tool; "
            "storing it creates stale false facts"
        )
    if cat in _EPHEMERAL_STATE_CATEGORIES:
        return (
            f"category '{category}' is refused: it is reserved for live environment snapshots "
            "that are re-queryable via system/app tools, never persisted"
        )
    if k == "path" or k == "dir" or k.endswith(("_path", "_dir", "_directory", "_root", "_roots")):
        return f"path-like key '{key}' is refused: device paths go stale; query approved roots via `system_file_roots`"
    if re.match(r"^[A-Za-z]:[\\/]", k) or re.search(r"[\\/]Users[\\/]", k):
        return f"path-like key '{key}' is refused: device paths go stale; query approved roots via `system_file_roots`"
    if re.match(r"^[A-Za-z]:[\\/]", v) or re.search(r"[\\/]Users[\\/]", v) or "(user:" in v.lower():
        return "value contains an absolute device path; device-specific paths are never persisted"
    return None


def _query_tokens(query: str) -> list[str]:
    """Split a recall query into lowercase word tokens."""
    return [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t]


def _stem(word: str) -> str:
    """Minimal English plural stemmer (s/es/ies -> singular)."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("es") and len(word) > 2:
        return word[:-2]
    if word.endswith("s") and len(word) > 1:
        return word[:-1]
    return word


_LOCATION_SYNONYMS = frozenset({"live", "living", "reside", "residing", "home", "country", "nation", "city", "address", "located", "where"})

def _entry_match_score(entry, tokens: list[str]) -> int:
    """Score a memory entry against query tokens."""
    key = (entry.key or "").lower()
    value = (entry.value or "").lower()
    category = (entry.category or "").lower()
    score = 0
    token_set = set(tokens)

    # Location synonym boost
    if any(k in key for k in ("location", "country", "home", "address")) or category == "profile":
        if token_set & _LOCATION_SYNONYMS:
            score += 4

    for tok in tokens:
        stem = _stem(tok)
        if tok and tok in key:
            score += 3
        elif stem and stem in key:
            score += 1
        if tok and tok in value:
            score += 2
        elif stem and stem in value:
            score += 1
        if tok and tok in category:
            score += 2
        elif stem and stem in category:
            score += 1

    # Importance weighting: only apply when there's a token match
    if score > 0:
        access_count = getattr(entry, "access_count", 0) or 0
        score += min(access_count, 10)  # cap at 10 to prevent runaway scoring

        # Category priority tiebreaker: profile > knowledge > preference
        category_priority = {"profile": 3, "knowledge": 2, "preference": 1}
        score += category_priority.get(category, 0)

    return score


class MemoryStoreTool(BaseTool[MemoryStoreInput]):
    """Save or update a persistent key-value memory entry."""

    name = "memory_store"
    description = "Store or update a persistent user preference, profile fact, or habit for future tasks"
    input_schema = MemoryStoreInput

    async def execute(self, input_data: MemoryStoreInput) -> ToolOutput:
        # Fail closed before any DB/session lookup, validation side effect,
        # write, or other effect: direct persistent-memory mutation has no
        # trusted mutation/provenance boundary available in this context.
        return ToolOutput(
            success=False,
            error={
                "code": "DIRECT_INSTRUCTION_REQUIRED",
                "message": (
                    "A state-changing or sensitive built-in action requires a "
                    "trusted consent boundary, not identity, metadata, "
                    "app_internal status, configuration, or a caller flag"
                ),
            },
            duration_ms=0,
        )

    async def verify(self, input_data: MemoryStoreInput, output: ToolOutput) -> bool:
        if not output.success:
            return False
        try:
            key = output.data.get("key") or (input_data.key or "").strip()
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                row = session.query(MemoryEntry).filter_by(key=key, status="ACTIVE").first()
                return (
                    row is not None
                    and row.value == output.data.get("value")
                    and row.category == output.data.get("category")
                )
        except Exception:
            return False

    def get_policy_requirements(self) -> dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": True,
            "app_internal": True,
        }


class MemoryRecallTool(BaseTool[MemoryRecallInput]):
    """Search or recall stored memories from the database."""

    name = "memory_recall"
    description = "Search and recall stored user preferences, profile facts, and memories"
    input_schema = MemoryRecallInput

    async def execute(self, input_data: MemoryRecallInput) -> ToolOutput:
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                query = session.query(MemoryEntry)
                if input_data.category:
                    query = query.filter_by(category=input_data.category)
                entries = query.all()

                tokens = _query_tokens(input_data.query or "")
                if not tokens:
                    results = []
                    for e in entries:
                        results.append(self._entry_payload(e, 0))
                else:
                    scored = []
                    for e in entries:
                        score = _entry_match_score(e, tokens)
                        if score > 0:
                            scored.append((e, score))
                    scored.sort(key=lambda pair: pair[1], reverse=True)
                    results = [
                        self._entry_payload(e, score) for e, score in scored
                    ]

                if results:
                    mem_previews = [f"{m['key']}='{m['value']}'" for m in results[:5]]
                    summary = f"Recalled {len(results)} memory entries for '{input_data.query or '*'}': {', '.join(mem_previews)}"
                else:
                    summary = f"Recalled 0 memory entries matching query '{input_data.query or '*'}'"
                return ToolOutput(
                    success=True,
                    data={"count": len(results), "memories": results},
                    model_summary=summary,
                    evidence={"source": "database_memory_recall"},
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MEMORY_RECALL_ERROR", "message": str(e)},
                duration_ms=0,
            )

    @staticmethod
    def _entry_payload(entry, score: int) -> dict[str, Any]:
        return {
            "key": entry.key,
            "value": entry.value,
            "category": entry.category,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
            "match_score": score,
        }

    async def verify(self, input_data: MemoryRecallInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
        }


class MemoryForgetTool(BaseTool[MemoryForgetInput]):
    """Delete an outdated or incorrect persistent memory entry."""

    name = "memory_forget"
    description = "Delete an outdated or incorrect persistent memory entry by key"
    input_schema = MemoryForgetInput

    async def execute(self, input_data: MemoryForgetInput) -> ToolOutput:
        # Fail closed before any DB/session lookup, model call, delete, or
        # other effect: direct persistent-memory mutation has no trusted
        # mutation/provenance boundary available in this context.
        return ToolOutput(
            success=False,
            error={
                "code": "DIRECT_INSTRUCTION_REQUIRED",
                "message": (
                    "A state-changing or sensitive built-in action requires a "
                    "trusted consent boundary, not identity, metadata, "
                    "app_internal status, configuration, or a caller flag"
                ),
            },
            duration_ms=0,
        )

    async def verify(self, input_data: MemoryForgetInput, output: ToolOutput) -> bool:
        if not output.success:
            return False
        try:
            key = output.data.get("key") or input_data.key
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                row = session.query(MemoryEntry).filter_by(key=key, status="ACTIVE").first()
                return row is None
        except Exception:
            return False

    def get_policy_requirements(self) -> dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": True,
            "app_internal": True,
        }


class MemoryLinkTool(BaseTool[MemoryLinkInput]):
    """Create or update a directed link between two memory keys or facts."""

    name = "memory_link"
    description = "Create or update a directed link between two memory keys or facts"
    input_schema = MemoryLinkInput

    async def execute(self, input_data: MemoryLinkInput) -> ToolOutput:
        # Fail closed before any input interpretation, DB/session lookup,
        # ORM insert/update, commit, or other effect: direct persistent-memory
        # mutation has no trusted mutation/provenance boundary available in
        # this context.
        return ToolOutput(
            success=False,
            error={
                "code": "DIRECT_INSTRUCTION_REQUIRED",
                "message": (
                    "A state-changing or sensitive built-in action requires a "
                    "trusted consent boundary, not identity, metadata, "
                    "app_internal status, configuration, or a caller flag"
                ),
            },
            duration_ms=0,
        )

    async def verify(self, input_data: MemoryLinkInput, output: ToolOutput) -> bool:
        if not output.success:
            return False
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                edge = session.query(MemoryEdge).filter_by(
                    source_key=output.data["source_key"],
                    relation=output.data["relation"],
                    target_key=output.data["target_key"],
                ).first()
                return edge is not None and edge.status == "active"
        except Exception:
            return False

    def get_policy_requirements(self) -> dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": True,
            "app_internal": True,
        }


class MemoryRecallRelatedTool(BaseTool[MemoryRecallRelatedInput]):
    """Recall a memory key plus its linked neighborhood (graph recall)."""

    name = "memory_recall_related"
    description = "Recall a memory key plus the neighborhood of related memories linked to it"
    input_schema = MemoryRecallRelatedInput

    async def execute(self, input_data: MemoryRecallRelatedInput) -> ToolOutput:
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                entry = session.query(MemoryEntry).filter_by(key=input_data.key, status="ACTIVE").first()
                resolved_key = input_data.key
                if not entry:
                    tokens = _query_tokens(input_data.key)
                    best = None
                    best_score = 0
                    for e in session.query(MemoryEntry).all():
                        s = _entry_match_score(e, tokens)
                        if s > best_score:
                            best, best_score = e, s
                    entry = best
                    if not entry:
                        return ToolOutput(
                            success=False,
                            error={
                                "code": "MEMORY_NOT_FOUND",
                                "message": f"No memory entry matching '{input_data.key}'",
                            },
                            duration_ms=0,
                        )
                    resolved_key = entry.key

                hops = max(1, min(2, int(input_data.hops or 1)))
                visited = {resolved_key}
                frontier = {resolved_key}
                edges_seen = []
                for hop in range(1, hops + 1):
                    if not frontier:
                        break
                    rows = session.query(MemoryEdge).filter(
                        MemoryEdge.source_key.in_(frontier),
                        MemoryEdge.status != "removed",
                    ).all()
                    next_frontier = set()
                    for e in rows:
                        if e.target_key in visited:
                            continue
                        visited.add(e.target_key)
                        next_frontier.add(e.target_key)
                        edges_seen.append((e.source_key, e.relation, e.target_key, hop))
                        if len(edges_seen) >= 25:
                            break
                    frontier = next_frontier
                    if len(edges_seen) >= 25:
                        break

                keys = {resolved_key} | {t for _, _, t, _ in edges_seen}
                entry_map = {
                    e.key: e
                    for e in session.query(MemoryEntry)
                    .filter(MemoryEntry.key.in_(keys - {resolved_key}))
                    .all()
                }
                entry_map[resolved_key] = entry

                relation_by_key: dict[str, list[dict[str, Any]]] = {}
                for src, rel, tgt, hop in edges_seen:
                    relation_by_key.setdefault(tgt, []).append(
                        {"relation": rel, "from": src, "hop": hop}
                    )

                memories = [MemoryRecallTool._entry_payload(entry, 0)]
                for tgt, rel_infos in relation_by_key.items():
                    related = entry_map.get(tgt)
                    if related:
                        memories.append(self._related_payload(related, rel_infos))

                unlinked_facts = [
                    {"target_key": tgt, "relation": rel, "from": src, "hop": hop}
                    for src, rel, tgt, hop in edges_seen
                    if tgt not in entry_map
                ]

                summary = (
                    f"Recalled anchor '{resolved_key}' with "
                    f"{len(memories) - 1} linked entries across {hops} hop(s)"
                )
                return ToolOutput(
                    success=True,
                    data={
                        "anchor_key": resolved_key,
                        "count": len(memories),
                        "memories": memories,
                        "links": [
                            {"source_key": s, "relation": r, "target_key": t, "hop": h}
                            for s, r, t, h in edges_seen
                        ],
                        "unlinked_facts": unlinked_facts,
                    },
                    model_summary=summary,
                    evidence={"source": "database_memory_recall_related"},
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MEMORY_RECALL_RELATED_ERROR", "message": str(e)},
                duration_ms=0,
            )

    @staticmethod
    def _related_payload(entry, rel_infos: list[dict[str, Any]]) -> dict[str, Any]:
        info = rel_infos[0] if rel_infos else {}
        return {
            "key": entry.key,
            "value": entry.value,
            "category": entry.category,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
            "relation": info.get("relation"),
            "via": info.get("from"),
            "hop": info.get("hop"),
        }

    async def verify(self, input_data: MemoryRecallRelatedInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
        }


class MemoryStatsTool(BaseTool[MemoryStatsInput]):
    """
    Get statistics about the memory store.

    Returns counts of entries by category, total entries, total edges,
    and recent activity.
    """

    name = "memory_stats"
    description = "Get statistics about the memory store (entry counts, categories, edges)"
    input_schema = MemoryStatsInput

    async def execute(self, input_data: MemoryStatsInput) -> ToolOutput:
        try:
            db_manager = get_db_manager()
            with db_manager.get_session() as session:
                total_entries = session.query(MemoryEntry).count()
                total_edges = session.query(MemoryEdge).filter(
                    MemoryEdge.status != "removed"
                ).count()

                category_counts = {}
                for entry in session.query(MemoryEntry).all():
                    cat = entry.category or "uncategorized"
                    category_counts[cat] = category_counts.get(cat, 0) + 1

                recent_entries = session.query(MemoryEntry).order_by(
                    MemoryEntry.updated_at.desc()
                ).limit(5).all()

                return ToolOutput(
                    success=True,
                    data={
                        "total_entries": total_entries,
                        "total_edges": total_edges,
                        "category_counts": category_counts,
                        "recent_entries": [
                            {
                                "key": e.key,
                                "category": e.category,
                                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
                            }
                            for e in recent_entries
                        ],
                    },
                    model_summary=f"Memory: {total_entries} entries, {total_edges} edges, {len(category_counts)} categories",
                    evidence={"source": "database_memory_stats"},
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MEMORY_STATS_ERROR", "message": str(e)},
            )

    async def verify(self, input_data: MemoryStatsInput, output: ToolOutput) -> bool:
        return output.success and "total_entries" in (output.data or {})

    def get_policy_requirements(self) -> dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
        }


class MemoryExportTool(BaseTool[MemoryExportInput]):
    """
    Export memory entries for backup or analysis.

    Exports entries as JSON or Markdown format, optionally filtered by category.
    """

    name = "memory_export"
    description = "Export memory entries as JSON or Markdown for backup or analysis"
    input_schema = MemoryExportInput

    async def execute(self, input_data: MemoryExportInput) -> ToolOutput:
        try:
            db_manager = get_db_manager()
            with db_manager.get_session() as session:
                query = session.query(MemoryEntry)
                if input_data.category:
                    query = query.filter(MemoryEntry.category == input_data.category)

                entries = query.order_by(MemoryEntry.key).all()

                edges = []
                if input_data.include_edges:
                    edge_query = session.query(MemoryEdge).filter(
                        MemoryEdge.status != "removed"
                    )
                    if input_data.category:
                        entry_keys = [e.key for e in entries]
                        edge_query = edge_query.filter(
                            MemoryEdge.source_key.in_(entry_keys) |
                            MemoryEdge.target_key.in_(entry_keys)
                        )
                    edges = edge_query.all()

                if input_data.format == "markdown":
                    lines = ["# Memory Export\n"]
                    lines.append(f"**Total entries:** {len(entries)}")
                    lines.append(f"**Total edges:** {len(edges)}\n")

                    by_category = {}
                    for entry in entries:
                        cat = entry.category or "uncategorized"
                        by_category.setdefault(cat, []).append(entry)

                    for cat, cat_entries in sorted(by_category.items()):
                        lines.append(f"## {cat.title()}\n")
                        for entry in cat_entries:
                            lines.append(f"- **{entry.key}**: {entry.value[:200]}")
                            if entry.updated_at:
                                lines.append(f"  *Updated:* {entry.updated_at.isoformat()}")
                        lines.append("")

                    if edges:
                        lines.append("## Links\n")
                        for edge in edges:
                            lines.append(f"- {edge.source_key} --[{edge.relation}]--> {edge.target_key}")
                        lines.append("")

                    content = "\n".join(lines)
                else:
                    import json
                    data = {
                        "entries": [
                            {
                                "key": e.key,
                                "value": e.value,
                                "category": e.category,
                                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
                            }
                            for e in entries
                        ],
                        "edges": [
                            {
                                "source_key": e.source_key,
                                "relation": e.relation,
                                "target_key": e.target_key,
                            }
                            for e in edges
                        ],
                        "metadata": {
                            "total_entries": len(entries),
                            "total_edges": len(edges),
                            "exported_at": datetime.now().isoformat(),
                        },
                    }
                    content = json.dumps(data, indent=2)

                return ToolOutput(
                    success=True,
                    data={
                        "format": input_data.format,
                        "total_entries": len(entries),
                        "total_edges": len(edges),
                        "content": content,
                    },
                    model_summary=f"Exported {len(entries)} entries, {len(edges)} edges as {input_data.format}",
                    evidence={"source": "database_memory_export"},
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MEMORY_EXPORT_ERROR", "message": str(e)},
            )

    async def verify(self, input_data: MemoryExportInput, output: ToolOutput) -> bool:
        return output.success and "content" in (output.data or {})

    def get_policy_requirements(self) -> dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
        }

