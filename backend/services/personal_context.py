"""Deterministic Personal Context builder (Phase 7).

Derives a bounded, structured personal-context view from ACTIVE typed
memories + live Goal state. No LLM involvement; no memory writes. The
rendered text is DATA for prompts — the source of truth is the structured
memory rows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.services.memory_service import MemoryService


MAX_LINES_PER_SECTION = 6
MAX_TOTAL_LINES = 24


class PersonalContextBuilder:
    def __init__(self, session):
        self.db = session
        self.memory = MemoryService(session)

    def build(self, query: str = "", scope: str = "USER") -> Dict[str, Any]:
        prefs = self.memory.retrieve(
            query=query, kinds=["PREFERENCE"], scopes=[scope],
            max_results=MAX_LINES_PER_SECTION)
        facts = self.memory.retrieve(
            query=query, kinds=["FACT"], scopes=[scope, "GLOBAL"],
            max_results=MAX_LINES_PER_SECTION,
            require_verified=False)
        instructions = self.memory.retrieve(
            query="", kinds=["INSTRUCTION"], scopes=[scope, "GLOBAL"],
            max_results=MAX_LINES_PER_SECTION)
        assumptions = self.memory.retrieve(
            query=query, kinds=["ASSUMPTION", "BELIEF"], scopes=[scope],
            max_results=3)

        goals = []
        try:
            from backend.models.database import Objective

            goals = [
                {"id": g.id, "title": g.title, "status": g.status}
                for g in db_goals(self.db)
            ]
        except Exception:
            goals = []

        context = {
            "preferences": [{"key": m.key, "value": m.value,
                             "verified": m.verification_status.startswith("VERIFIED")}
                            for m in prefs],
            "facts": [{"key": m.key, "value": m.value} for m in facts],
            "instructions": [{"value": m.value} for m in instructions],
            "assumptions_low_confidence": [
                {"key": m.key, "confidence": m.confidence} for m in assumptions
            ],
            "active_goals": goals[:MAX_LINES_PER_SECTION],
        }
        context["rendered"] = render(context)
        return context


def db_goals(db) -> List[Any]:
    from backend.models.database import Objective

    return (
        db.query(Objective)
        .filter(Objective.status == "active")
        .order_by(Objective.priority.asc(), Objective.updated_at.desc())
        .limit(MAX_LINES_PER_SECTION)
        .all()
    )


def render(context: Dict[str, Any]) -> str:
    """Bounded natural-language rendering of the structured context."""
    lines: List[str] = ["BEGIN PERSONAL CONTEXT (data):"]
    if context["preferences"]:
        lines.append("Preferences:")
        for p in context["preferences"][:MAX_LINES_PER_SECTION]:
            mark = "" if p.get("verified") else " (unverified)"
            lines.append(f"- {p['key'].replace('_', ' ')}: {p['value']}{mark}")
    if context["instructions"]:
        lines.append("Standing instructions:")
        for i in context["instructions"][:MAX_LINES_PER_SECTION]:
            lines.append(f"- {i['value']}")
    if context["facts"]:
        lines.append("Known facts:")
        for f in context["facts"][:MAX_LINES_PER_SECTION]:
            lines.append(f"- {f['key'].replace('_', ' ')}: {f['value']}")
    if context["active_goals"]:
        lines.append("Active goals:")
        for g in context["active_goals"]:
            lines.append(f"- {g['title']} [{g['status']}]")
    if context["assumptions_low_confidence"]:
        lines.append("Low-confidence assumptions (treat skeptically):")
        for a in context["assumptions_low_confidence"]:
            lines.append(f"- {a['key'].replace('_', ' ')}")
    lines.append("END PERSONAL CONTEXT")
    return "\n".join(lines[:MAX_TOTAL_LINES])