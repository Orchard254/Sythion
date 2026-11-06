"""
Background memory consolidation (PREMem-style pre-storage reasoning).

After a completed conversation turn, a best-effort background LLM call reads
the recent conversation plus current persistent memory and proposes durable
memory operations (ADD / UPDATE / DELETE / NOOP). The model distills durable
understanding -- who the user is, preferences, habits, domain knowledge --
rather than dumping raw conversation text into the store.

Safety gate (kept deliberately strict; see docs/KNOWLEDGE.md):
- Only `high`-confidence ops are auto-applied.
- DELETE is NEVER auto-applied; it is logged for human review.
- Ops are capped per run so one noisy consolidation cannot flood the store.
- A failure to call or parse the model is silent: consolidation is best-effort
  and must never break the task loop or block a user response.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.models.database import MemoryEdge
from backend.tools.persistent_memory_tools import _RELATION_RE, _rejects_live_state
from backend.services.memory_service import WRITE_AUTHORITY

logger = logging.getLogger(__name__)

CONSOLIDATION_SYSTEM_PROMPT = """You are the memory consolidation module of the trusted local runtime persona of a larger intelligence system.

Your job: read a recent user conversation and the assistant's current persistent
memory, then produce a memory consolidation plan. This is "pre-storage
reasoning": distill the conversation into durable understanding (who the user
is, their preferences, habits, and domain knowledge) -- NOT a raw text dump.

Hard rules:
- GROUNDING: Only produce facts that are explicitly stated by the user or were
  verified with a tool during the conversation. Never invent, generalize, or
  assume. If a fact is not evidenced, omit it.
- DURABILITY: Capture durable understanding (profile, preferences, recurring
  habits, domain knowledge). Ignore transient task chatter (one-off requests,
  reminders, momentary system status) unless it reveals a durable preference.
- SYNTHESIS: Where the user reveals a higher-level pattern or implicit
  constraint across statements (e.g. "casual in chat, formal in work emails"),
  you may synthesize it -- but only from stated evidence, and mark it as an
  inference.
- MERGE: Prefer UPDATE of an existing memory entry over creating a duplicate.
  DELETE an existing entry only when the conversation clearly overrides or
  contradicts it. Keep the store small and clean.
- MEMORY LINKS: the CURRENT MEMORY LINKS section lists directed links between
  two durable facts ("source --relation--> target"). Propose NEW links the
  conversation evidences, and note links now wrong or overridden. relation is
  lowercase snake_case (e.g. works_on, prefers, blocked_by, depends_on).
  Prefer existing memory keys as endpoints; a target that is a stated fact
  with no stored entry may use a short free-text key (<= 60 chars) instead of
  inventing a memory entry.
- Output STRICT JSON only, no prose, no markdown fences. Shape:
  {"ops": [{"op": "add"|"update"|"delete"|"noop", "key": "...", "value": "...",
   "category": "profile"|"preference"|"knowledge", "confidence": "high"|"medium",
   "source": "user_statement"|"verified_tool_result"|"inference",
   "reason": "one short sentence"}],
   "edge_ops": [{"op": "add_edge"|"remove_edge", "source_key": "...",
   "relation": "...", "target_key": "...", "confidence": "high"|"medium",
   "source": "user_statement"|"verified_tool_result"|"inference",
   "reason": "one short sentence"}]}
  - add: a new durable fact not already in memory.
  - update: an existing key whose value should be refined or enriched.
  - delete: an existing entry now known to be wrong or overridden.
  - noop: emit when the conversation added no durable facts (ops list may
    contain a single noop).
  - key: lowercase snake_case, <= 60 chars. value: concise and concrete,
    <= 300 chars.
  - add_edge: a new durable link evidenced by the conversation.
  - remove_edge: an existing link now known to be wrong or overridden.
  - edge_ops may be an empty list. Endpoints match memory keys or are short
    free-text fact labels (<= 60 chars)."""

ALLOWED_OPS = {"add", "update", "delete", "noop"}
ALLOWED_EDGE_OPS = {"add_edge", "remove_edge"}
ALLOWED_CATEGORIES = {"profile", "preference", "knowledge"}
ALLOWED_SOURCES = {"user_statement", "verified_tool_result", "inference"}
MAX_OPS_PER_RUN = 10
MAX_KEY_LEN = 60
MAX_VALUE_LEN = 300

# --- Edge lifecycle (decay + owner review) ----------------------------------
# Mirrors proposed_skills.status: an edge is "active", surfaces as "stale"
# once decayed below the review threshold, and only an explicit owner review
# moves it to "removed". Decay never deletes or removes anything.
EDGE_STATUS_ACTIVE = "active"
EDGE_STATUS_STALE = "stale"
EDGE_STATUS_REMOVED = "removed"
EDGE_DECAY_INTERVAL_DAYS = 7      # one decay step per interval untouched
EDGE_DECAY_FACTOR = 0.5           # each full interval halves the weight
EDGE_STALE_WEIGHT = 0.5           # at/below this the edge is surfaced for review
EDGE_MIN_WEIGHT = 0.1             # floor so a stale edge can always be rescued


def apply_edge_decay(db, now: Optional[datetime] = None) -> Dict[str, int]:
    """Decay untouched edge weights on each consolidation run.

    An edge not reinforced within EDGE_DECAY_INTERVAL_DAYS has its weight
    reduced by EDGE_DECAY_FACTOR per elapsed interval, floored at
    EDGE_MIN_WEIGHT. Edges at/below EDGE_STALE_WEIGHT are marked "stale" so
    the owner review surface can list them. Decay NEVER auto-deletes or moves
    an edge to "removed" -- that transition is owner-only, and removed edges
    are left untouched here.

    Decay deliberately does not touch `updated_at`: that column keeps
    measuring the last real reinforcement (memory_link upsert or an owner
    review), so the decay clock is not reset by the decay pass itself. The
    update is issued via a core statement so the ORM `onupdate` on
    `updated_at` does not fire.

    Returns {"decayed": n, "marked_stale": n} for the run.
    """
    from sqlalchemy import update

    now = now or datetime.utcnow()
    decayed = 0
    marked_stale = 0
    interval_seconds = EDGE_DECAY_INTERVAL_DAYS * 86400.0
    for edge in db.query(MemoryEdge).all():
        if edge.status == EDGE_STATUS_REMOVED:
            continue
        last_touch = edge.updated_at or edge.created_at or now
        days = (now - last_touch).total_seconds() / interval_seconds
        if days < 1.0:
            continue
        steps = min(int(days), 10)  # cap so an ancient edge does not churn
        new_weight = max(EDGE_MIN_WEIGHT, edge.weight * (EDGE_DECAY_FACTOR ** steps))
        new_weight = round(new_weight, 4)
        new_status = edge.status
        if new_weight <= EDGE_STALE_WEIGHT:
            if new_status != EDGE_STATUS_STALE:
                marked_stale += 1
            new_status = EDGE_STATUS_STALE
        db.execute(
            update(MemoryEdge)
            .where(MemoryEdge.id == edge.id)
            .values(weight=new_weight, status=new_status, updated_at=edge.updated_at)
        )
        decayed += 1
    db.commit()
    if decayed:
        logger.info(
            "Edge decay: %d decayed, %d newly stale (never auto-deleted)",
            decayed, marked_stale,
        )
    return {"decayed": decayed, "marked_stale": marked_stale}


# --- Memory pruning (remove old/unused entries) ----------------------------
MEMORY_PRUNE_DAYS = 30  # entries not accessed in this many days are pruned
MEMORY_MIN_KEEP = 10    # always keep at least this many entries


def prune_stale_memories(db, now: Optional[datetime] = None) -> Dict[str, int]:
    """Remove memory entries not accessed in MEMORY_PRUNE_DAYS.

    Safety: always keeps at least MEMORY_MIN_KEEP entries. Profile entries
    (category='profile') are never pruned. Deletion cascades to edges.
    Returns {"pruned": n, "kept": n}.
    """
    from backend.models.database import MemoryEntry, MemoryEdge

    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=MEMORY_PRUNE_DAYS)

    all_entries = db.query(MemoryEntry).order_by(MemoryEntry.updated_at.desc()).all()
    if len(all_entries) <= MEMORY_MIN_KEEP:
        return {"pruned": 0, "kept": len(all_entries)}

    prunable = []
    for entry in all_entries:
        if entry.category == "profile":
            continue  # never prune profile entries
        last_seen = entry.last_accessed_at or entry.updated_at
        if last_seen and last_seen < cutoff:
            prunable.append(entry)

    # Reserve space for minimum keep
    max_prune = max(0, len(all_entries) - MEMORY_MIN_KEEP)
    prunable = prunable[:max_prune]

    pruned = 0
    for entry in prunable:
        # Remove edges first
        db.query(MemoryEdge).filter(
            (MemoryEdge.source_key == entry.key) | (MemoryEdge.target_key == entry.key)
        ).delete(synchronize_session=False)
        db.delete(entry)
        pruned += 1

    if pruned:
        db.commit()
        logger.info("Memory pruning: %d entries pruned, %d kept", pruned, len(all_entries) - pruned)
    return {"pruned": pruned, "kept": len(all_entries) - pruned}


def list_edges(db, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List memory edges, optionally filtered by lifecycle status.

    Mirrors skill_engine.list_proposals: returns plain dicts with ISO
    timestamps, ordered by weight ascending so the weakest (stalest) edges
    surface first for review.
    """
    query = db.query(MemoryEdge).order_by(MemoryEdge.weight.asc(), MemoryEdge.id.asc())
    if status:
        query = query.filter_by(status=status)
    out = []
    for e in query.all():
        out.append({
            "id": e.id,
            "source_key": e.source_key,
            "relation": e.relation,
            "target_key": e.target_key,
            "weight": e.weight,
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        })
    return out


def get_edge(db, edge_id: int) -> Optional[MemoryEdge]:
    return db.query(MemoryEdge).filter_by(id=edge_id).first()


def reinforce_edge(db, edge_id: int) -> Dict[str, Any]:
    """Owner review decision: reinforce a stale edge back to active (weight 1.0).

    This is the only automatic weight reset besides memory_link; it counts as
    a real touch (bumps `updated_at`). Raising on non-stale edges mirrors
    approve_proposal refusing non-proposed proposals.
    """
    edge = get_edge(db, edge_id)
    if not edge:
        raise ValueError(f"Edge {edge_id} not found")
    if edge.status != EDGE_STATUS_STALE:
        raise ValueError(
            f"Edge {edge_id} is {edge.status}, not {EDGE_STATUS_STALE}; only stale edges are reviewed"
        )
    edge.weight = 1.0
    edge.status = EDGE_STATUS_ACTIVE
    edge.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(edge)
    return {
        "id": edge.id,
        "source_key": edge.source_key,
        "relation": edge.relation,
        "target_key": edge.target_key,
        "weight": edge.weight,
        "status": edge.status,
    }


def remove_edge(db, edge_id: int) -> Dict[str, Any]:
    """Owner review decision: mark a stale edge removed.

    Mirrors reject_proposal: the row is retained (no hard delete) but leaves
    the live graph -- it is excluded from recall walks and context injection.
    Only an explicit owner decision reaches this state; never auto-applied.
    """
    edge = get_edge(db, edge_id)
    if not edge:
        raise ValueError(f"Edge {edge_id} not found")
    if edge.status != EDGE_STATUS_STALE:
        raise ValueError(
            f"Edge {edge_id} is {edge.status}, not {EDGE_STATUS_STALE}; only stale edges are reviewed"
        )
    edge.status = EDGE_STATUS_REMOVED
    db.commit()
    db.refresh(edge)
    return {
        "id": edge.id,
        "source_key": edge.source_key,
        "relation": edge.relation,
        "target_key": edge.target_key,
        "weight": edge.weight,
        "status": edge.status,
    }


def _get(obj, name: str, default: str = ""):
    """Read an attribute or dict key (ORM row or plain dict)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _build_user_message(memory_entries, turns, memory_edges=None) -> str:
    """Compose the model input from current memory, links, and recent conversation."""
    memory_lines = "\n".join(
        f"- [{_get(m, 'category')}] {_get(m, 'key')}: {_get(m, 'value')}"
        for m in memory_entries
    ) or "(empty)"
    edge_lines = "\n".join(
        f"- {_get(e, 'source_key')} --{_get(e, 'relation')}--> {_get(e, 'target_key')}"
        for e in (memory_edges or [])
    ) or "(none)"
    turn_lines = "\n".join(
        f"{'User' if _get(t, 'role') == 'user' else 'Synth'}: {_get(t, 'summary')}"
        for t in turns
    ) or "(no recent conversation)"
    return (
        "CURRENT PERSISTENT MEMORY:\n"
        + memory_lines
        + "\n\nCURRENT MEMORY LINKS:\n"
        + edge_lines
        + "\n\nRECENT CONVERSATION:\n"
        + turn_lines
    )


def parse_ops(content: str) -> List[Dict[str, Any]]:
    """Extract the ops list from a model response, tolerating markdown fences."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in consolidation response")
        data = json.loads(text[start:end + 1])
    ops = data.get("ops") if isinstance(data, dict) else None
    if not isinstance(ops, list):
        raise ValueError("Consolidation response has no ops list")
    return ops


def parse_edge_ops(content: str) -> List[Dict[str, Any]]:
    """Extract the edge_ops list from a model response, tolerating markdown fences.

    A response without edge_ops is valid and yields an empty list.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in consolidation response")
        data = json.loads(text[start:end + 1])
    ops = data.get("edge_ops") if isinstance(data, dict) else None
    if ops is None:
        return []
    if not isinstance(ops, list):
        raise ValueError("Consolidation response edge_ops is not a list")
    return ops


def sanitize_ops(ops: List[Any]) -> List[Dict[str, Any]]:
    """Validate and normalize raw ops; drop malformed and noop entries."""
    cleaned: List[Dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        op_name = str(op.get("op", "")).lower()
        if op_name not in ALLOWED_OPS or op_name == "noop":
            continue
        key = str(op.get("key", "")).strip()
        value = str(op.get("value", "")).strip()
        if not key or len(key) > MAX_KEY_LEN or len(value) > MAX_VALUE_LEN:
            continue
        category = str(op.get("category", "")).lower()
        if category not in ALLOWED_CATEGORIES:
            category = "knowledge"
        source = str(op.get("source", "")).lower()
        if source not in ALLOWED_SOURCES:
            source = "inference"
        cleaned.append({
            "op": op_name,
            "key": key,
            "value": value,
            "category": category,
            "confidence": str(op.get("confidence", "")).lower(),
            "source": source,
            "reason": str(op.get("reason", ""))[:200],
        })
    return cleaned


def sanitize_edge_ops(ops: List[Any]) -> List[Dict[str, Any]]:
    """Validate and normalize raw edge ops; drop malformed entries."""
    cleaned: List[Dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        op_name = str(op.get("op", "")).lower()
        if op_name not in ALLOWED_EDGE_OPS:
            continue
        source_key = str(op.get("source_key", "")).strip()
        relation = str(op.get("relation", "")).strip()
        target_key = str(op.get("target_key", "")).strip()
        if not source_key or not relation or not target_key:
            continue
        if len(source_key) > MAX_KEY_LEN or len(target_key) > MAX_KEY_LEN:
            continue
        if not _RELATION_RE.match(relation):
            continue
        source = str(op.get("source", "")).lower()
        if source not in ALLOWED_SOURCES:
            source = "inference"
        cleaned.append({
            "op": op_name,
            "source_key": source_key,
            "relation": relation,
            "target_key": target_key,
            "confidence": str(op.get("confidence", "")).lower(),
            "source": source,
            "reason": str(op.get("reason", ""))[:200],
        })
    return cleaned


async def consolidate_memory(
    db,
    turns,
    memory_entries,
    adapter: Optional[Any] = None,
    memory_edges: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """Run one consolidation pass and apply gated ops.

    Returns the list of applied ops (memory + edge), empty on any failure.
    Never raises.
    """
    if not turns:
        return []
    from backend.runtime.model_provider import get_default_provider

    provider = adapter or get_default_provider()
    messages = [
        {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(memory_entries, turns, memory_edges)},
    ]
    try:
        resp = await provider.chat(messages, max_tokens=4000, timeout=120.0)
    except Exception as exc:  # best-effort: never break the task loop
        logger.warning("Memory consolidation chat failed: %s", exc)
        return []

    content = resp.get("message", {}).get("content", "")
    try:
        ops = sanitize_ops(parse_ops(content))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Memory consolidation response unparseable: %s", exc)
        return []

    try:
        edge_ops = sanitize_edge_ops(parse_edge_ops(content))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Memory consolidation edge_ops unparseable: %s", exc)
        edge_ops = []

    to_apply: List[Dict[str, Any]] = []
    for op in ops[:MAX_OPS_PER_RUN]:
        if op["confidence"] != "high":
            logger.info("Skipping consolidation op (low/unknown confidence): %s", op)
            continue
        if op["op"] == "delete":
            logger.warning(
                "Consolidation requested DELETE of '%s' -- not auto-applied; review needed",
                op["key"],
            )
            continue
        to_apply.append(op)

    to_apply_edges: List[Dict[str, Any]] = []
    for op in edge_ops[:MAX_OPS_PER_RUN]:
        if op["confidence"] != "high":
            logger.info("Skipping consolidation edge op (low/unknown confidence): %s", op)
            continue
        if op["op"] == "remove_edge":
            logger.warning(
                "Consolidation requested REMOVE of '%s --%s--> %s' -- not auto-applied; review needed",
                op["source_key"], op["relation"], op["target_key"],
            )
            continue
        to_apply_edges.append(op)

    from backend.services.memory_service import MemoryService

    EVAL_VERSION_REF = "consolidation-pass"
    service = MemoryService(db)
    applied: List[Dict[str, Any]] = []
    for op in to_apply:
        try:
            # Phase 7: consolidation writes flow through the Memory Service.
            # LLM-proposed candidates are typed conservatively: user-stated
            # facts become PREFERENCE/FACT via USER_CONVERSATION (unverified);
            # everything else lands as ASSUMPTION/BELIEF via MODEL_INFERENCE —
            # never auto-verified.
            source = op.get("source", "model_inference").lower()
            # Consolidation can never mint TOOL_EVIDENCE/VERIFIED_TOOL from a
            # model label: genuine tool evidence must arrive through the service
            # with independently validated provenance. Any tool-flavored claim
            # coming out of consolidation is treated as an UNVERIFIED
            # consolidation candidate, never verified tool evidence.
            src_type = {
                "user_statement": "USER_CONVERSATION",
                "verified_tool_result": "CONSOLIDATION",
                "inference": "MODEL_INFERENCE",
            }.get(source, "CONSOLIDATION")
            kind = {
                "profile": "FACT", "preference": "PREFERENCE",
                "knowledge": "ASSUMPTION",
            }.get(op.get("category", ""), "ASSUMPTION")
            if src_type == "USER_CONVERSATION" and kind == "ASSUMPTION":
                kind = "PREFERENCE"
            # Minimal authority coercion: model-inferred consolidations cannot
            # mint kinds the source is not allowed to create (notably FACT from
            # a "profile" label). Coerce any disallowed kind to a safe
            # UNVERIFIED kind (ASSUMPTION) instead of rejecting the op — but
            # never grant a verified kind/badge to a model label.
            allowed_kinds = WRITE_AUTHORITY.get(src_type, {}).get("kinds", set())
            if allowed_kinds and kind not in allowed_kinds:
                if "ASSUMPTION" in allowed_kinds:
                    kind = "ASSUMPTION"
                elif "BELIEF" in allowed_kinds:
                    kind = "BELIEF"
                else:
                    kind = sorted(allowed_kinds)[0] if allowed_kinds else "ASSUMPTION"
            confidence = 0.9 if op.get("confidence") == "high" else 0.6
            if _rejects_live_state(op.get("key"), str(op.get("value", "")).strip(), op.get("category") or None):
                continue
            service.create(
                key=op["key"], value=op["value"], kind=kind,
                source_type=src_type,
                source_ref=f"consolidation:{EVAL_VERSION_REF}",
                confidence=confidence,
                category=op.get("category") or None,
            )
            applied.append(op)
        except Exception as exc:
            logger.warning("Memory store failed for %s: %s", op.get("key"), exc)
    for op in to_apply_edges:
        source_key = (op.get("source_key") or "").strip()
        target_key = (op.get("target_key") or "").strip()
        relation = (op.get("relation") or "").strip()
        if not source_key or not target_key:
            continue
        if _rejects_live_state(source_key, "", None) or _rejects_live_state(target_key, "", None):
            continue
        if not relation or not _RELATION_RE.match(relation):
            continue
        if source_key == target_key:
            continue
        # Weight is intentionally fixed for consolidation-created edges: model
        # proposals cannot influence edge weight magnitude, and out-of-range
        # values are not accepted or normalized into storage.
        weight = 1.0
        try:
            existing = db.query(MemoryEdge).filter_by(
                source_key=source_key,
                relation=relation,
                target_key=target_key,
            ).first()
            if existing:
                existing.weight = weight
                existing.status = "active"
                existing.updated_at = datetime.utcnow()
            else:
                db.add(MemoryEdge(
                    source_key=source_key,
                    relation=relation,
                    target_key=target_key,
                    weight=weight,
                ))
            db.commit()
            applied.append(op)
        except Exception as exc:
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning("Memory link failed for %s: %s", source_key, exc)
    if applied:
        logger.info("Memory consolidation applied %d ops", len(applied))
    return applied
