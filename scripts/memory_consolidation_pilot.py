"""
Memory consolidation pilot (research-grounded, PREMem-style pre-storage reasoning).

Feeds the REAL current persistent memory plus a scripted onboarding conversation
through a consolidation prompt on a chosen Ollama Cloud model and prints the
JSON consolidation plan. Used to evaluate whether a model can reliably distill
durable understanding (ADD/UPDATE/DELETE/NOOP) before we build the background
consolidation step.

Usage (from repo root):
    PYTHONPATH=.;.\backend python scripts/memory_consolidation_pilot.py [model]

Default model: gpt-oss:120b
"""
import asyncio
import json
import sys

from backend.core.config import settings
from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager, MemoryEntry

DB_PATH = r"C:\Users\Administrator\AppData\Local\Sythion (Synth)\sythion.db"

SYSTEM_PROMPT = """You are the memory consolidation module of a personal desktop assistant.

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
- Output STRICT JSON only, no prose, no markdown fences. Shape:
  {"ops": [{"op": "add"|"update"|"delete"|"noop", "key": "...", "value": "...",
   "category": "profile"|"preference"|"knowledge", "confidence": "high"|"medium",
   "source": "user_statement"|"verified_tool_result"|"inference",
   "reason": "one short sentence"}]}
  - add: a new durable fact not already in memory.
  - update: an existing key whose value should be refined or enriched.
  - delete: an existing entry now known to be wrong or overridden.
  - noop: emit when the conversation added no durable facts (ops list may
    contain a single noop).
  - key: lowercase snake_case, <= 60 chars. value: concise and concrete,
    <= 300 chars."""

SCRIPTED_CONVERSATION = """User: Hi, I'm orchard. I mostly work from my home office in Nairobi.
Synth: Nice to meet you! Would you like me to do a quick system audit?
User: Yes please, do a quick check.
[Synth verified via tools: battery 78%, 3 apps running (Edge, Notepad, Terminal), disk 62% free, local time 09:14 EAT]
User: Please always address me as "Sir".
Synth: Understood.
User: I prefer casual, normal conversation -- no stiff formality.
Synth: Got it.
User: When looking up news for me, only use trusted, reputable sources.
Synth: Understood.
User: Also -- don't reorganize my Downloads folder, I keep it messy on purpose.
Synth: Understood, I won't touch Downloads.
User: Can you remind me Friday 9am to submit the report?
Synth: I've noted you'd like that reminder."""


def load_memory() -> list:
    set_db_manager(DatabaseManager(DB_PATH))
    with DatabaseManager(DB_PATH).get_session() as session:
        rows = (
            session.query(MemoryEntry)
            .order_by(MemoryEntry.updated_at.desc())
            .all()
        )
        return [
            {"category": r.category, "key": r.key, "value": r.value}
            for r in rows
        ]


async def run(model: str) -> None:
    from backend.adapters.ollama_adapter import OllamaCloudAdapter

    memory = load_memory()
    memory_lines = "\n".join(
        f"- [{m['category']}] {m['key']}: {m['value']}" for m in memory
    ) or "(empty)"

    user_msg = (
        "CURRENT PERSISTENT MEMORY:\n"
        + memory_lines
        + "\n\nRECENT CONVERSATION:\n"
        + SCRIPTED_CONVERSATION
    )

    adapter = OllamaCloudAdapter()
    adapter.selected_model = model
    client = await adapter._get_client()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 4000,
        "stream": False,
    }
    print(f"--- model: {model} ---")
    print("--- current memory ---")
    for m in memory:
        print(f"  [{m['category']}] {m['key']}: {m['value']}")
    print("--- response ---")
    resp = await client.post("/api/chat", json=payload)
    resp.raise_for_status()
    data = resp.json()
    content = data.get("message", {}).get("content", "")
    print(content)
    try:
        parsed = json.loads(content.strip().lstrip("```json").rstrip("```").strip())
        print("\n--- parsed ops ---")
        for op in parsed.get("ops", []):
            print(f"  {op['op'].upper():6} [{op.get('category','?')}] {op.get('key','?')}")
            print(f"           -> {op.get('value','')}")
            print(f"           ({op.get('source','?')} / {op.get('confidence','?')}) {op.get('reason','')}")
    except Exception as exc:
        print(f"\n!!! could not parse JSON plan: {exc}")
    await adapter.close()


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "gpt-oss:120b"
    asyncio.run(run(model))
