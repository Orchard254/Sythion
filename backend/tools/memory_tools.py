"""
Persistent real-time memory tools for Sythion (Synth).
Allows Sythion to store, update, and recall user preferences, facts, and profile info across tasks.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime

try:
    from backend.tools.executor import BaseTool
    from backend.api.schemas import (
        ToolOutput,
        MemoryStoreInput,
        MemoryRecallInput,
    )
    from backend.models.database import MemoryEntry, get_db_manager
except ImportError:
    from tools.executor import BaseTool
    from api.schemas import (
        ToolOutput,
        MemoryStoreInput,
        MemoryRecallInput,
    )
    from models.database import MemoryEntry, get_db_manager


class MemoryStoreTool(BaseTool):
    """Save or update a persistent key-value memory entry."""

    name = "memory_store"
    description = "Store or update a persistent user preference, profile fact, or habit for future tasks"
    input_schema = MemoryStoreInput

    async def execute(self, input_data: MemoryStoreInput) -> ToolOutput:
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

                results = []
                q_lower = (input_data.query or "").lower().strip()
                for e in entries:
                    if q_lower:
                        if q_lower not in e.key.lower() and q_lower not in e.value.lower():
                            continue
                    results.append({
                        "key": e.key,
                        "value": e.value,
                        "category": e.category,
                        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
                    })

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

    async def verify(self, input_data: MemoryRecallInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
        }
