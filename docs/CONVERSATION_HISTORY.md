# Conversation history and retention

## Complete history

Pure conversations are stored as complete `ConversationExchange` records, not as
the truncated episodic summaries in `conversation_turns`. The two are separate
on purpose: summaries are compression context for model prompting, while
exchanges are the user-visible transcript.

- `GET /api/v1/conversations?limit=50&cursor=<cursor>` returns
  `{ "conversations": [...], "next_cursor": string | null }`.
- Each exchange contains `id`, `session_id`, `user_text`, `assistant_text`, and
  `created_at`.
- Results are newest first. Pass `next_cursor` unchanged to retrieve the next
  page. The cursor uses `created_at` and exchange ID as a stable ordering key.
- `limit` defaults to 50 and accepts 1-200. Invalid cursors return HTTP 400.
- Legacy truncated summaries cannot be reconstructed and are excluded.
- The response ID matches the stored exchange ID, so a newly sent conversation
  can be merged into the UI without waiting for the history refetch.

## Retention

- Retention is manual and owner-triggered through
  `POST /api/v1/retention/purge`.
- There is no automatic startup purge.
- The configured retention window controls what a manual purge removes.
- The purge response reports `purged_turns` and `purged_exchanges` separately,
  so expired complete conversations are visible in the report.
- The frontend must not describe retention as automatic. Settings, Security, and
  System & Integrations wording was corrected to say purging is explicit.
- `purge_turns` and `purge_conversation_history` remain the backend primitives;
  new changes should not add silent deletion of existing user data.
