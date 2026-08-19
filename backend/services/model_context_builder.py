"""Builds model-visible context from durable runtime state."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

MEMORY_CONTEXT_LOG_RETAIN = 500

def _cap_tool_summary(summary: str, cap: int, tool_name: str) -> str:
    """Cap a tool summary, cutting evidence packs at newline boundaries."""
    if len(summary) <= cap:
        return summary
    return summary[:cap - 3] + "..."


class ModelContextBuilder:
    """Build model-visible context from durable runtime state."""

    def __init__(
        self,
        db_session: Session,
        tool_summary_cap: Callable[[str], int],
        continuation_summary_cap: Callable[[str], int],
        continuation_patterns: Sequence[str],
    ) -> None:
        self.db = db_session
        self._tool_summary_cap = tool_summary_cap
        self._continuation_summary_cap = continuation_summary_cap
        self._continuation_patterns = tuple(continuation_patterns)
        # Memory context cache: key -> (timestamp, (block, stats))
        self._memory_cache: Dict[str, Tuple[float, Tuple[str, Dict]]] = {}
        self._memory_cache_ttl = 30.0  # seconds

    def _is_continuation_request(self, request: str) -> bool:
        """Detect a request that means 'continue/resume what you were doing'."""
        try:
            from backend.services.work_state import is_continuation_request
        except ImportError:
            from services.work_state import is_continuation_request
        return is_continuation_request(request)

    def previous_task_context(self, task_id: str, request: str) -> str:
        """Seed a continuation task with the last task's goal and recent actions."""
        if not self._is_continuation_request(request):
            return ""
        try:
            import json
            from datetime import datetime, timedelta
            from backend.models.database import Task, TaskEvent

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
                    cap = self._continuation_summary_cap(tname)
                    if len(str(summary)) > cap:
                        summary = str(summary)[:cap - 3] + "..."
                    lines.append(f"  - {tname}: {summary}")
            if prev.final_result or prev.terminal_reason:
                lines.append(f"- previous outcome: {prev.final_result or prev.terminal_reason}")
            return "\n".join(lines)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Could not build previous-task context: %s", e)
            return ""

    def _synthesize_user_profile(self, entries: List[Any]) -> str:
        """Build a 2-3 sentence user profile from memory entries."""
        try:
            from backend.tools.persistent_memory_tools import _DEVICE_IDENTITY_KEYS, _EPHEMERAL_STATE_KEYS, _EPHEMERAL_STATE_CATEGORIES

            name = None
            location = None
            interests = []
            preferences = {}

            for entry in entries:
                if entry.category in _EPHEMERAL_STATE_CATEGORIES:
                    continue
                key_lower = entry.key.lower()
                if any(dik in key_lower for dik in _DEVICE_IDENTITY_KEYS):
                    continue
                if any(esk in key_lower for esk in _EPHEMERAL_STATE_KEYS):
                    continue

                if "name" in key_lower and name is None:
                    name = entry.value
                elif "location" in key_lower or "city" in key_lower or "country" in key_lower:
                    if location is None:
                        location = entry.value
                elif "interest" in key_lower or "hobby" in key_lower or "like" in key_lower:
                    if entry.value not in interests:
                        interests.append(entry.value)
                elif "prefer" in key_lower:
                    preferences[key_lower] = entry.value

            parts = []
            if name:
                parts.append(f"The user's name is {name}.")
            if location:
                parts.append(f"They are in {location}.")
            if interests:
                parts.append(f"Interests: {', '.join(interests[:3])}.")
            if preferences:
                pref_str = "; ".join(f"{k}: {v}" for k, v in list(preferences.items())[:3])
                parts.append(f"Preferences: {pref_str}.")

            return " ".join(parts) if parts else ""
        except Exception:
            return ""

    def select_memory_context(self, anchor: str, session_id: Optional[str] = None):
        """Inject only the most relevant persistent memories for this request."""
        # Check cache first
        cache_key = f"{session_id}:{hash(anchor)}"
        now = time.monotonic()
        if cache_key in self._memory_cache:
            ts, cached = self._memory_cache[cache_key]
            if now - ts < self._memory_cache_ttl:
                return cached

        block = ""
        stats = {"available": 0, "relevant": 0, "connected": 0, "links": 0}
        try:
            from backend.models.database import MemoryEntry, MemoryEdge
            from backend.tools.persistent_memory_tools import _query_tokens, _entry_match_score
            from sqlalchemy import text as sa_text

            all_entries = self.db.query(MemoryEntry).order_by(MemoryEntry.updated_at.desc()).all()
            stats["available"] = len(all_entries)
            if not all_entries:
                return block, stats

            # Conversation-aware search: combine current request with recent turns
            search_text = anchor or ""
            if session_id:
                from backend.models.database import ConversationTurn
                recent_turns = (
                    self.db.query(ConversationTurn)
                    .filter(ConversationTurn.session_id == session_id)
                    .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
                    .limit(5)
                    .all()
                )
                if recent_turns:
                    turn_text = " ".join(t.summary for t in reversed(recent_turns))
                    search_text = f"{search_text} {turn_text}"

            selected = []
            tokens = _query_tokens(search_text)
            if tokens:
                # Use FTS5 for fast token-based search instead of scanning all entries
                fts_query = " OR ".join(tokens)
                fts_results = self.db.execute(sa_text(
                    "SELECT m.id, m.category, m.key, m.value, m.access_count, m.updated_at "
                    "FROM memory_entries_fts fts "
                    "JOIN memory_entries m ON m.id = fts.rowid "
                    "WHERE memory_entries_fts MATCH :query "
                    "ORDER BY m.access_count DESC, m.updated_at DESC "
                    "LIMIT 25"
                ), {"query": fts_query}).fetchall()
                if fts_results:
                    selected = [type('Entry', (), {
                        'id': r[0], 'category': r[1], 'key': r[2],
                        'value': r[3], 'access_count': r[4], 'updated_at': r[5]
                    })() for r in fts_results]
                else:
                    # Fallback to scoring if FTS returns nothing
                    scored = []
                    for entry in all_entries:
                        score = _entry_match_score(entry, tokens)
                        if score > 0:
                            scored.append((score, entry))
                    scored.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
                    selected = [entry for _, entry in scored[:25]]
            # Fallback: if tokens exist but no matches, include recent high-access entries
            if not selected and tokens:
                fallback = sorted(
                    all_entries,
                    key=lambda e: (-(e.access_count or 0), -(e.updated_at.timestamp() if e.updated_at else 0)),
                )[:10]
                selected = fallback
            if not selected and not tokens:
                # Only fallback to recency when store is empty or query is empty
                selected = list(all_entries[:25])
            stats["relevant"] = len(selected)

            # Increment access_count for selected entries (importance weighting)
            from sqlalchemy import update
            selected_ids = [entry.id for entry in selected]
            if selected_ids:
                self.db.execute(
                    update(MemoryEntry)
                    .where(MemoryEntry.id.in_(selected_ids))
                    .values(
                        access_count=MemoryEntry.access_count + 1,
                        last_accessed_at=datetime.utcnow(),
                    )
                )
                try:
                    self.db.commit()
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Could not commit memory entry access count updates: %s", exc)

            selected_keys = {entry.key for entry in selected}
            from sqlalchemy import or_
            # Only traverse active edges; stale edges are decayed and not useful for context
            edges = (
                self.db.query(MemoryEdge)
                .filter(
                    or_(
                        MemoryEdge.source_key.in_(selected_keys),
                        MemoryEdge.target_key.in_(selected_keys),
                    ),
                    MemoryEdge.status == "active",
                )
                .order_by(MemoryEdge.updated_at.desc())
                .all()
            )
            stats["links"] = len(edges)
            # Traverse both incoming and outgoing edges to find connected entries
            neighbor_keys = set()
            for edge in edges:
                if edge.source_key in selected_keys:
                    neighbor_keys.add(edge.target_key)
                if edge.target_key in selected_keys:
                    neighbor_keys.add(edge.source_key)
            neighbor_keys -= selected_keys
            # Only include neighbors that exist as MemoryEntry records
            neighbors = (
                self.db.query(MemoryEntry)
                .filter(MemoryEntry.key.in_(neighbor_keys))
                .all()
            ) if neighbor_keys else []
            stats["connected"] = len(neighbors)

            lines = [f"- [{entry.category}] {entry.key}: {entry.value}" for entry in selected]
            lines += [f"- [linked] [{entry.category}] {entry.key}: {entry.value}" for entry in neighbors]

            # User profile synthesis: concise summary from profile/preference entries
            profile_summary = self._synthesize_user_profile(selected + neighbors)
            if profile_summary:
                lines.insert(0, f"[USER PROFILE] {profile_summary}")

            if lines:
                block = "\nPERSISTENT MEMORY & USER PREFERENCES:\n" + "\n".join(lines)
            if edges:
                edge_lines = [f"- {edge.source_key} --{edge.relation}--> {edge.target_key}" for edge in edges[:25]]
                block += "\nMEMORY LINKS:\n" + "\n".join(edge_lines)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Could not load selective memory context: %s", e)

        # Cache the result
        result = (block, stats)
        self._memory_cache[cache_key] = (now, result)
        return result

    def record_memory_context_stats(self, task_id: str, mem_stats: Dict[str, int]) -> None:
        """Best-effort persistence of memory-injection stats for validation."""
        try:
            from backend.models.database import MemoryContextLog
            log = MemoryContextLog(
                task_id=task_id,
                available=mem_stats.get("available", 0),
                relevant=mem_stats.get("relevant", 0),
                connected=mem_stats.get("connected", 0),
                links=mem_stats.get("links", 0),
            )
            self.db.add(log)
            self.db.commit()
            # Trim old logs
            from sqlalchemy import func
            count = self.db.query(func.count(MemoryContextLog.id)).scalar()
            if count > MEMORY_CONTEXT_LOG_RETAIN:
                old_ids = [
                    r[0] for r in self.db.query(MemoryContextLog.id)
                    .order_by(MemoryContextLog.id.asc())
                    .limit(count - MEMORY_CONTEXT_LOG_RETAIN)
                    .all()
                ]
                if old_ids:
                    self.db.query(MemoryContextLog).filter(
                        MemoryContextLog.id.in_(old_ids)
                    ).delete(synchronize_session=False)
                    self.db.commit()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Could not record memory context stats: %s", exc)

    def build_context(
        self,
        *,
        system_prompt: str,
        task_id: str,
        original_request: str,
        normalized_goal: Optional[str],
        event_history: List[Dict[str, Any]],
        current_state: str,
        pending_question: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Build the model message list from task/runtime state."""
        import json
        messages: List[Dict[str, Any]] = [{
            "role": "system",
            "content": system_prompt,
        }]

        user_content = f"Task ID: {task_id}\n"
        user_content += f"Original Request: {original_request}\n"

        if normalized_goal:
            user_content += f"Normalized Goal: {normalized_goal}\n"

        user_content += f"Current State: {current_state}\n"

        if pending_question:
            user_content += (
                f"\nPENDING QUESTION (awaiting your next action after user response): "
                f"{pending_question}\n"
            )

        previous_context = self.previous_task_context(task_id, original_request)
        if previous_context:
            user_content += "\n" + previous_context + "\n"

        try:
            from backend.services.work_state import build_work_context
        except ImportError:
            from services.work_state import build_work_context

        work_context = build_work_context(
            self.db,
            task_id=task_id,
            original_request=original_request,
        )
        if work_context:
            user_content += "\n" + work_context + "\n"

        mem_block, mem_stats = self.select_memory_context(
            normalized_goal or original_request,
            session_id=session_id,
        )
        if mem_block:
            user_content += mem_block
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            "Memory injection for task %s: %d relevant + %d linked of %d available, %d links",
            task_id,
            mem_stats["relevant"],
            mem_stats["connected"],
            mem_stats["available"],
            mem_stats["links"],
        )
        self.record_memory_context_stats(task_id, mem_stats)

        try:
            from backend.services.conversation_guidance_adapter import ConversationGuidanceAdapter
        except ImportError:
            from services.conversation_guidance_adapter import ConversationGuidanceAdapter

        guidance = ConversationGuidanceAdapter(self.db).build_blocks(
            original_request=original_request,
            has_previous_context=bool(previous_context),
        )

        if guidance.recent_conversation:
            user_content += "\n" + guidance.recent_conversation + "\n"
        if guidance.conversation_state:
            user_content += "\n" + guidance.conversation_state + "\n"

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
                    summary = _cap_tool_summary(summary, cap, tool_name)
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
            "content": user_content,
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
                    result_summary = _cap_tool_summary(result_summary, cap, tool_name)

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