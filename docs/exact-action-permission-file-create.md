# Exact-Action Permission Design — `file_create` (design only)

Tool chosen: `file_create`, create-only, `overwrite=False` only.
Status: design only; no production edits, live actions, profiles, or commits.

## 1. Supportable tool

`file_create` has narrow local effect, atomic temp-file + `os.replace`, read-back verification, explicit params (`path`, `content`, `overwrite=False`), and explainable failures (roots/wildcard/reparse/reserved/locked/exists/size/verification/cancellation). It does not launch processes or require cross-process handles. Remaining risk is filesystem TOCTOU identity; that is a stated limit, not hidden.

Excludes overwrite=True, file_write append/replace, move, copy, delete, apps, packages, GUI.

## 2. Flow

### Stage exact proposal
Validate via FileCreateInput, apply `overwrite=False`, resolve/canonicalize target, root/reparse/reserved/size checks, compute content SHA-256 and proposal digest, then stage:

```json
{"kind":"permission","status":"awaiting","handle":"<readable>","action_id":"<uuid>","tool_name":"file_create","schema_version":1,"params_raw":{"path":"...","content":"..."},"params_normalized":{"path":"...","content":"...","overwrite":false},"params_digest":"<sha256>","proposal_digest":"<sha256>","target_canonical":"...","content_sha256":"...","content_bytes":0,"target_must_not_exist":true}
```

Rules: append `action.pending_approval` exactly once; no started/executed/approved/replied event; transition to waiting_for_user; validation failures do not stage.

### Same-composer natural consent
Authorization remains same composer and selected waiting task. A deterministic backend parser accepts only unambiguous explicit consent; no model inference. Display target, byte count, content digest, overwrite=false, target must not exist. Fail closed on multiple waits/stale version/ambiguity/replay/`answer_clarification`.

Approved consent stores `action.approved` receipt in same transaction. Denial stores `action.denied`, returns to running, never executes.

`set_pending_action_status` must be replaced by CAS `resolve_pending_permission(...)` requiring lease/ownership, exact pending kind/status/action_id/tool_name/proposal_digest, optimistic version/pending JSON match, and receipt creation atomically. A bare status flip has no authorization effect.

### Worker exact receipt
On resume, `start_mutating_action` handoff commits only if:
1. ownership held with valid fence;
2. task running, no unresolved/active/uncertain/malformed action;
3. pending JSON matches staged proposal and status approved;
4. normalized params equal execution params;
5. digest recomputes and matches;
6. exactly one durable `action.pending_approval` with same tool/params;
7. exactly one `action.approved` receipt with matching digest;
8. no prior `action.started`/`tool.executed` for that action ID;
9. receipt after proposal and before handoff.

Then mark ownership active, set active_action_id, clear pending, append `action.started`, commit.

### Execution/revalidation
Before OS action: path/root/reparse/reserved/size/overwrite (must false); reject changed proposal; if target exists abort `TARGET_CHANGED_OR_EXISTS`; recheck parent containment; cancellation before swap; atomic create; verify exact content, SHA-256, newly created target; finish action with exact evidence.

### Crash/replay
`action.started` without `tool.executed` is uncertain; recovery re-observes but never replays. Create-only digest permits absent / uncertain-if-present / uncertain-conflicting reporting. Old action IDs never reuse; completion blocked while uncertain. Approval never alone proves side effect.

## 3. Exact schema/events/receipts

Schema version starts 1 and increments for interpretation/default changes; stale proposal cannot execute. Required events: `action.pending_approval`, `action.approved`, `action.denied`, `action.started`, `tool.executed`, `action.handoff_unresolved`. Receipt authorizes one action ID/tool/normalized params/schema version/content digest. Only verified `tool.executed` can represent success.

## 4. Limits/non-claims

- No real target identity across filesystem races; mitigate via revalidation, fail/uncertain.
- Parent directory creation is separate, not independently recoverable.
- Same-volume atomicity only; temp cleanup best effort.
- Interrupted actions uncertain and never replayed.
- No overwrite support; existing target fails rather than clobbers.
- Consent text binding may be rejected if ambiguous.
- Ownership/lease loss refuses execution.
- Approval describes path/roots/content digest/byte count only, not ACLs, hard links, streams, metadata.

## 5. Acceptance criteria

- Stage normalized create proposal with digest/SHA; no target created.
- Receipt required; bare status flip unauthorized.
- `answer_clarification` cannot authorize permission.
- Action/tool/param/schema/target/digest mismatch blocks.
- No action ID replay.
- Valid approved action executes exactly once.
- Existing target before OS action prevents mutation.
- Readback/hash mismatch is failure.
- Cancellation before swap preserves target.
- Interrupted action uncertain, blocks completion, no replay.
- Root/wildcard/reparse/reserved/oversized/overwrite cases fail without side effects.
