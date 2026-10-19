# Phase 13B Database Migration

## Purpose

Phase 13B introduces a pure conversational runtime that handles CONVERSATION-type interactions without creating Task records. This requires the `conversation_turns` table to support conversations that have no associated `task_id`.

## Migration Required

### Schema Change

**File:** `backend/models/database.py`  
**Table:** `conversation_turns`  
**Column:** `task_id`

**Before:**
```python
task_id = Column(String, nullable=False)
```

**After:**
```python
task_id = Column(String, nullable=True)  # Phase 13B: nullable for pure conversations
```

### Migration Script

**File:** `backend/migrations/migrate_conversation_turns_nullable_task_id.py`

This script handles the SQLite limitation that doesn't support `ALTER COLUMN`. It:

1. Creates a new `conversation_turns_new` table with nullable `task_id`
2. Copies all existing data
3. Drops the old table
4. Renames the new table
5. Recreates indexes

### Running the Migration

```bash
python backend/migrations/migrate_conversation_turns_nullable_task_id.py
```

Or specify a custom database path:

```bash
python backend/migrations/migrate_conversation_turns_nullable_task_id.py "C:\path\to\sythion.db"
```

## Impact

### Before Migration

- CONVERSATION-type requests fail with:
  ```
  NOT NULL constraint failed: conversation_turns.task_id
  ```
- Phase 13B is non-functional

### After Migration

- CONVERSATION-type requests persist successfully with `task_id=NULL`
- Task-based conversations continue to work with valid `task_id` values
- Phase 13B fully operational

## Backward Compatibility

✅ **Fully backward compatible**

- Existing conversation_turns rows retain their `task_id` values
- No data loss
- Task-based conversation persistence unchanged
- Only adds support for task-less conversations

## Verification

After migration, verify with:

```sql
SELECT COUNT(*) FROM conversation_turns WHERE task_id IS NULL;
```

This should show conversation turns from CONVERSATION-type interactions.

## Related Changes

- `backend/services/conversation_runtime.py` — New ConversationRuntime that persists without task_id
- `backend/api/routes.py` — Routes CONVERSATION to ConversationRuntime instead of creating tasks
- `backend/cognitive/intent.py` — Classifies requests as CONVERSATION vs other types
