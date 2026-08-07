"""
Persistent real-time memory tools for Sythion (Synth).
Allows Sythion to store, update, and recall user preferences, facts, and profile info across tasks.
"""

import re
import json
from typing import Dict, Any, List, Optional
from datetime import datetime

try:
    from backend.tools.executor import BaseTool
    from backend.api.schemas import (
        ToolOutput,
        MemoryStoreInput,
        MemoryRecallInput,
        MemoryForgetInput,
        MemoryLinkInput,
        MemoryRecallRelatedInput,
        ActivityRecallInput,
    )
    from backend.models.database import (
        MemoryEntry, MemoryEdge, TaskEvent, ConversationTurn, get_db_manager,
    )
except ImportError:
    from tools.executor import BaseTool
    from api.schemas import (
        ToolOutput,
        MemoryStoreInput,
        MemoryRecallInput,
        MemoryForgetInput,
        MemoryLinkInput,
        MemoryRecallRelatedInput,
        ActivityRecallInput,
    )
    from models.database import (
        MemoryEntry, MemoryEdge, TaskEvent, ConversationTurn, get_db_manager,
    )


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


def _rejects_live_state(key: str, value: str, category: Optional[str]) -> Optional[str]:
    """Deterministic gate: refuse to persist live system state or device identity.

    The model cannot be trusted to self-police this rule (it has persisted
    exactly these snapshots), so the tool enforces it. Returns the rejection
    reason or None when the write is allowed.
    """
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


def _query_tokens(query: str) -> List[str]:
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


def _entry_match_score(entry, tokens: List[str]) -> int:
    """Score a memory entry against query tokens.

    Exact substring hits on key outrank value/category hits; stemmed
    (plural) forms count at lower weight. Any positive score keeps the
    entry as a candidate so the model (not the tool) decides relevance.
    """
    key = (entry.key or "").lower()
    value = (entry.value or "").lower()
    category = (entry.category or "").lower()
    score = 0
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
    return score


class MemoryStoreTool(BaseTool):
    """Save or update a persistent key-value memory entry."""

    name = "memory_store"
    description = "Store or update a persistent user preference, profile fact, or habit for future tasks"
    input_schema = MemoryStoreInput

    async def execute(self, input_data: MemoryStoreInput) -> ToolOutput:
        rejection = _rejects_live_state(input_data.key, input_data.value, input_data.category)
        if rejection:
            return ToolOutput(
                success=False,
                error={
                    "code": "MEMORY_STATE_STORAGE_REJECTED",
                    "message": rejection,
                },
                duration_ms=0,
            )
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                existing = session.query(MemoryEntry).filter_by(key=input_data.key).first()
                cat = input_data.category or "user_preference"
                if existing:
                    existing.value = input_data.value
                    existing.category = cat
                    existing.updated_at = datetime.utcnow()
                    action = "updated"
                else:
                    entry = MemoryEntry(
                        key=input_data.key,
                        value=input_data.value,
                        category=cat,
                    )
                    session.add(entry)
                    action = "created"
                
                session.commit()

                summary = f"Successfully {action} memory [{cat}] '{input_data.key}': '{input_data.value}'"
                return ToolOutput(
                    success=True,
                    data={"action": action, "key": input_data.key, "value": input_data.value, "category": cat},
                    model_summary=summary,
                    evidence={"source": "database_memory_store"}
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MEMORY_STORE_ERROR", "message": str(e)},
                duration_ms=0
            )

    async def verify(self, input_data: MemoryStoreInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": True,
            # The mutation touches only Sythion's own persistent memory DB, is
            # reversible via the same tool, and never reaches user OS state.
            # Unlike OS mutations it is safe to expose to the model in normal
            # (non-autonomous) mode, where the system prompt instructs its use.
            "app_internal": True,
        }


class MemoryRecallTool(BaseTool):
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

                summary = f"Recalled {len(results)} memory entries matching query '{input_data.query or '*'}'"
                return ToolOutput(
                    success=True,
                    data={"count": len(results), "memories": results},
                    model_summary=summary,
                    evidence={"source": "database_memory_recall"}
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MEMORY_RECALL_ERROR", "message": str(e)},
                duration_ms=0
            )

    @staticmethod
    def _entry_payload(entry, score: int) -> Dict[str, Any]:
        return {
            "key": entry.key,
            "value": entry.value,
            "category": entry.category,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
            "match_score": score,
        }

    async def verify(self, input_data: MemoryRecallInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
        }


class MemoryForgetTool(BaseTool):
    """Delete an outdated or incorrect persistent memory entry."""

    name = "memory_forget"
    description = "Delete an outdated or incorrect persistent memory entry by key"
    input_schema = MemoryForgetInput

    async def execute(self, input_data: MemoryForgetInput) -> ToolOutput:
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                entry = session.query(MemoryEntry).filter_by(key=input_data.key).first()
                if not entry:
                    return ToolOutput(
                        success=False,
                        error={"code": "MEMORY_NOT_FOUND", "message": f"No memory entry with key '{input_data.key}'"},
                        duration_ms=0
                    )
                session.delete(entry)
                session.commit()
                summary = f"Deleted memory entry '{input_data.key}'"
                return ToolOutput(
                    success=True,
                    data={"action": "deleted", "key": input_data.key},
                    model_summary=summary,
                    evidence={"source": "database_memory_forget"}
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MEMORY_FORGET_ERROR", "message": str(e)},
                duration_ms=0
            )

    async def verify(self, input_data: MemoryForgetInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": True,
            "app_internal": True,
        }


class MemoryLinkTool(BaseTool):
    """Create or update a directed link between two memory keys or facts."""

    name = "memory_link"
    description = "Create or update a directed link between two memory keys or facts"
    input_schema = MemoryLinkInput

    async def execute(self, input_data: MemoryLinkInput) -> ToolOutput:
        source_key = (input_data.source_key or "").strip()
        target_key = (input_data.target_key or "").strip()
        relation = (input_data.relation or "").strip()

        for label, key in (("source_key", source_key), ("target_key", target_key)):
            rejection = _rejects_live_state(key, "", None)
            if rejection:
                return ToolOutput(
                    success=False,
                    error={
                        "code": "MEMORY_LINK_STATE_REJECTED",
                        "message": f"{label}: {rejection}",
                    },
                    duration_ms=0,
                )
        if not relation or not _RELATION_RE.match(relation):
            return ToolOutput(
                success=False,
                error={
                    "code": "MEMORY_LINK_INVALID_RELATION",
                    "message": (
                        f"relation '{relation}' must be lowercase snake_case "
                        "starting with a letter (max 50 chars)"
                    ),
                },
                duration_ms=0,
            )
        if source_key == target_key:
            return ToolOutput(
                success=False,
                error={
                    "code": "MEMORY_LINK_SELF_LOOP",
                    "message": "a memory link cannot connect a key to itself",
                },
                duration_ms=0,
            )
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                existing = session.query(MemoryEdge).filter_by(
                    source_key=source_key,
                    relation=relation,
                    target_key=target_key,
                ).first()
                if existing:
                    existing.weight = input_data.weight
                    existing.status = "active"  # re-linking is a reinforcement
                    existing.updated_at = datetime.utcnow()
                    action = "updated"
                else:
                    session.add(MemoryEdge(
                        source_key=source_key,
                        relation=relation,
                        target_key=target_key,
                        weight=input_data.weight,
                    ))
                    action = "created"

                session.commit()

                summary = (
                    f"Successfully {action} memory link "
                    f"'{source_key} --{relation}--> {target_key}'"
                )
                return ToolOutput(
                    success=True,
                    data={
                        "action": action,
                        "source_key": source_key,
                        "relation": relation,
                        "target_key": target_key,
                        "weight": input_data.weight,
                    },
                    model_summary=summary,
                    evidence={"source": "database_memory_link"}
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MEMORY_LINK_ERROR", "message": str(e)},
                duration_ms=0
            )

    async def verify(self, input_data: MemoryLinkInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": True,
            # Like memory_store, the mutation touches only Sythion's own
            # memory DB, never user OS state.
            "app_internal": True,
        }


class MemoryRecallRelatedTool(BaseTool):
    """Recall a memory key plus its linked neighborhood (graph recall)."""

    name = "memory_recall_related"
    description = "Recall a memory key plus the neighborhood of related memories linked to it"
    input_schema = MemoryRecallRelatedInput

    async def execute(self, input_data: MemoryRecallRelatedInput) -> ToolOutput:
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                entry = session.query(MemoryEntry).filter_by(key=input_data.key).first()
                resolved_key = input_data.key
                if not entry:
                    # Fall back to the best-scored match so a fuzzy key still
                    # yields its linked neighborhood.
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
                edges_seen = []  # (source_key, relation, target_key, hop)
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

                relation_by_key: Dict[str, List[Dict[str, Any]]] = {}
                for src, rel, tgt, hop in edges_seen:
                    relation_by_key.setdefault(tgt, []).append(
                        {"relation": rel, "from": src, "hop": hop}
                    )

                memories = [MemoryRecallTool._entry_payload(entry, 0)]
                for tgt in relation_by_key:
                    related = entry_map.get(tgt)
                    if related:
                        memories.append(self._related_payload(related, relation_by_key[tgt]))

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
                    evidence={"source": "database_memory_recall_related"}
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MEMORY_RECALL_RELATED_ERROR", "message": str(e)},
                duration_ms=0
            )

    @staticmethod
    def _related_payload(entry, rel_infos: List[Dict[str, Any]]) -> Dict[str, Any]:
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

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
        }


_ACTIVITY_SUMMARY_CAP = 220


def _as_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize a possibly tz-aware datetime to naive UTC.

    The DB stores naive UTC timestamps (datetime.utcnow()); range queries must
    compare like for like. tz-aware inputs are converted to UTC then stripped.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _bound_activity_summary(text: Any) -> str:
    """Bound a single activity summary line to the default tool cap."""
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False, default=str)
    text = str(text or "")
    if len(text) > _ACTIVITY_SUMMARY_CAP:
        text = text[:_ACTIVITY_SUMMARY_CAP - 3] + "..."
    return text


def _event_summary(event_data: Any) -> str:
    """Extract only the bounded model_summary (fall back to `output` text).

    Mirrors ModelDecisionEngine._previous_task_context exactly: never surface
    raw `params`, `data`, or the full `output` payload to the model.
    """
    try:
        data = json.loads(event_data) if isinstance(event_data, str) else (event_data or {})
    except (ValueError, TypeError):
        data = {}
    summary = data.get("model_summary")
    if summary is None:
        summary = data.get("output", "")
    return _bound_activity_summary(summary)


class ActivityRecallTool(BaseTool):
    """Recall Sythion's own past activity across tasks within a time range."""

    name = "activity_recall"
    description = "Recall Sythion's own past actions and activity (tool calls and what you asked) within a time range; for questions like 'what were you doing at 8am'"
    input_schema = ActivityRecallInput

    async def execute(self, input_data: ActivityRecallInput) -> ToolOutput:
        try:
            start = _as_naive_utc(input_data.start_time)
            end = _as_naive_utc(input_data.end_time) or datetime.utcnow()
            max_results = max(1, min(25, int(input_data.max_results or 10)))
            exclude_ids = set(input_data.exclude_task_ids or [])
            if start is None or end < start:
                return ToolOutput(
                    success=False,
                    error={
                        "code": "INVALID_TIME_RANGE",
                        "message": "start_time is required and end_time must not be before start_time",
                    },
                    duration_ms=0,
                )

            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                events = []
                q = (
                    session.query(TaskEvent)
                    .filter(TaskEvent.created_at >= start)
                    .filter(TaskEvent.created_at <= end)
                    .order_by(TaskEvent.created_at.desc())
                )
                tool_name = (input_data.tool_name or "").strip().lower()
                for ev in q:
                    if ev.task_id in exclude_ids:
                        continue
                    summary = _event_summary(ev.event_data)
                    entry = {
                        "kind": "tool",
                        "task_id": ev.task_id,
                        "event_type": ev.event_type,
                        "created_at": ev.created_at.isoformat() if ev.created_at else None,
                        "summary": summary,
                    }
                    if tool_name:
                        if ev.event_type != "tool.executed":
                            continue
                        try:
                            data = json.loads(ev.event_data) if isinstance(ev.event_data, str) else (ev.event_data or {})
                        except (ValueError, TypeError):
                            data = {}
                        if str(data.get("tool_name", "")).lower() != tool_name:
                            continue
                        entry["tool_name"] = str(data.get("tool_name", ""))
                    else:
                        try:
                            data = json.loads(ev.event_data) if isinstance(ev.event_data, str) else (ev.event_data or {})
                        except (ValueError, TypeError):
                            data = {}
                        entry["tool_name"] = str(data.get("tool_name", "")) or None
                    events.append(entry)
                    if len(events) >= max_results:
                        break

                if not tool_name:
                    turn_q = (
                        session.query(ConversationTurn)
                        .filter(ConversationTurn.created_at >= start)
                        .filter(ConversationTurn.created_at <= end)
                        .order_by(ConversationTurn.created_at.desc())
                        .all()
                    )
                    for t in turn_q:
                        if t.task_id in exclude_ids:
                            continue
                        events.append({
                            "kind": "turn",
                            "task_id": t.task_id,
                            "role": t.role,
                            "created_at": t.created_at.isoformat() if t.created_at else None,
                            "summary": _bound_activity_summary(t.summary),
                        })
                    events.sort(key=lambda e: e.get("created_at") or "", reverse=True)
                    events = events[:max_results]

                summary = (
                    f"Found {len(events)} activities between "
                    f"{start.isoformat()} and {end.isoformat()}"
                )
                return ToolOutput(
                    success=True,
                    data={"count": len(events), "events": events},
                    model_summary=summary,
                    evidence={"source": "database_activity_recall"},
                )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "ACTIVITY_RECALL_ERROR", "message": str(e)},
                duration_ms=0,
            )

    async def verify(self, input_data: ActivityRecallInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
        }
