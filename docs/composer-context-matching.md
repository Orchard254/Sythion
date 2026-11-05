# Composer context matching: direction and compatibility

## User direction (broader matching/search design remains pending)

- Automatically create and name a context immediately when a discussion starts;
  do not wait for background work or model output to finish.
- Link the human request, discussion, task identities, and later model outputs
  to that context so it can subsequently be found by meaning and discussion.
- Resolve the relevant discussion/context first, then any associated task.
  Multiple plausible contexts or tasks require a natural question in the same
  composer, not a guess based on recency or overlapping words.
- Ordinary conversation and genuinely independent requests remain natural.
  No separate composer or unconditional confirmation step is required.

Semantic naming, search/indexing, retrieval scope, rename/merge behavior, and
the public context API await the broader design. The bounded persistence layer
below now automatically stores an exchange-origin name and exact membership;
it does not infer topics, perform search, or expose a context UI.

## Durable exchange-origin contexts (implemented)

`DiscussionContext` and `DiscussionContextMembership` are additive tables.
`ConversationRuntime._persist_conversation` automatically writes a context and
exact exchange/user-turn/assistant-turn membership in the same transaction as
every newly persisted exchange, including normal chat with no task. The context
name is a deterministic, whitespace-normalized human request excerpt of at most
96 characters (`initial_request_excerpt`), not model-generated topic evidence.
Identical names/text receive distinct opaque IDs; names are not unique keys.
Full original request/response text remains unchanged in `ConversationExchange`.

Creation occurs **at exchange persistence, after response inference and before
background dispatch**, not immediately on inbound message receipt. Durable
pre-inference request capture still needs the `ComposerRequest` ledger below.
Independent exchanges receive independent contexts until a resolver exists;
even an apparent conversational continuation is not silently grouped by text.

A validated cooperative task revision reuses the context of its exact initiating
exchange; neither its name nor prior membership is overwritten. A legacy task
source can be attached during an accepted revision only using its existing
exact task-association turn IDs, atomically with the revision. Unknown legacy
pure-chat topic/membership is not guessed from timestamps or summaries.

Task linkage remains the existing `ConversationTaskAssociation`, reached through
membership's exact exchange ID. No context/name field grants permission,
launches work, changes routing, or removes revision safe-point checks. Missing
or cross-session explicit context IDs fail rather than creating a fallback.
Existing membership cannot be reassigned by this persistence helper.

`discussion_contexts.py` provides exact session-scoped lookup and transaction-
participating membership helpers. They never commit/rollback independently.
Normal `commit=False` persistence, task revisions, and dispatch compensation
include context rows in their existing transaction/cleanup boundary. A failed
membership or exchange persistence cannot leave a committed success context.
Context names do not themselves prove any task ran or completed.

Startup's existing `Base.metadata.create_all` adds the tables without rewriting
old user records or speculatively backfilling topics. Retention and exact cleanup
remove affected memberships before their referenced rows; only affected contexts
with no remaining retained member are deleted. A context's original name remains
while another member survives (no silent rename); it has no separate TTL in
this slice. Unrelated contexts are not swept. Existing independent transcript
versus task-purge scope is unchanged. Retention failures roll back the complete
operation, including names/membership changes, with or without FK enforcement.

Automatic topic matching/search, cross-session lookup, inbound request receipts,
HTTP idempotency, public context listing/UI and duplicate-dispatch prevention
remain unimplemented. Creating a context does not fix model `start_task`
misclassification and must not be presented as resolving that risk.

## Compatibility with the current bounded implementation

The existing initiating exchange/task association retains exact exchange,
task, and turn-row identities. A future context resolver can supply a candidate
association to the current revision boundary; human-readable context names
must never replace stable identity or confer authorization.

The current bounded task descriptor list is reference data, not a complete
context search service. The conversation prompt now explicitly asks for
clarification when multiple discussions/tasks are plausible. Prompt guidance
does not establish reliable semantic matching or create permission.

After resolution, the runtime still checks the exact task, current version,
association/session, ownership, and cooperative decision window. Revisions
append the human message verbatim, not a model rewrite. Context retrieval must
not bypass those checks or the separate consequential-action permission gate.

A task completing during resolution must not become a new-work authorization.
An admitted action, uncertain effect, pending permission, cancellation, stale
version, or unavailable safe point must preserve the existing refusal behavior:
no silent interruption, restart, replay, or replacement task. Future contexts
must retain identity across completion; they must not treat completion or a
changed display name as detachment from the original work.

## Next bounded slice: exact candidate projection (implemented, not routed)

`backend/services/composer_context_candidates.py` reads the existing
`ConversationTaskAssociation -> ConversationExchange/ConversationTurn/Task`
links. It returns frozen records containing exact initiating request/reply,
exchange/turn/task identities, and observed task version/state/result. It does
not rewrite, summarize, name, rank, merge, or select discussions, and owns no
transaction. It uses scalar queries with autoflush disabled so unflushed user
edits are neither exposed as persisted text nor accidentally committed.

Identical wording retains distinct identities. Terminal tasks remain linked.
Missing task/turn references are explicit integrity issues, not dropped matches.
Database errors propagate, rather than becoming an empty/new-topic result.
Readers must use a clean session when they require committed-only data: already
flushed caller writes remain part of that caller's transaction snapshot.

Coverage is explicitly **retained initiating task associations in one session**,
not all conversations. The bounded list reports `has_more_associations`; exact
lookup never falls back to a newer task. Empty results, an exhausted limit,
excluded pure chat, another session, or expired history cannot prove that the
human request is independent. No existing route is wired to this new reader.

## Proposed persistence boundary (design only)

The schema still cannot represent an unanswered inbound request:
`ConversationExchange` requires both texts and `ConversationState` is a mutable
session summary. The new context layer covers retained exchange membership,
not request capture or semantic resolution. Do not overload summaries or
initiating task associations with inferred memberships.

| Proposed record | Required identity/invariant |
| --- | --- |
| `ComposerRequest` | Client request ID + session, exact human text, observed context/binding revision, durable received/resolving/needs-clarification/resolved/failed status. Bind retry ID to payload. Persist before model calls; never erase it because inference or launch fails. |
| `DiscussionContext` (base implemented) | Opaque ID, session, initial name/provenance and creation time exist. Revisioned semantic naming and pre-inference creation remain future work; labels are not matching evidence or permission. |
| `DiscussionContextMembership` (exchange linkage implemented) | Exact exchange/turn references exist; task association is retained separately. Inbound-request and typed semantic relationships remain future work. Never deduplicate, overwrite, or merge by name/text; audit later reassociation/rename. |
| `ContextBinding` | The context/task actually observed by the composer, binding revision, and any pending disambiguation request. Separate stable identity from mutable display names and current task status. |
| `DispatchReceipt` | Exact request/resolution/binding identity and one committed task launch or revision outcome. A retry reads the receipt, not a new model call or worker launch. Model fields cannot mint this receipt. |

Except for the marked persistence base, this is a logical design, not a
migration/API promise. Retain full existing exchange/task/turn identities and
user text. Backfill only exact known associations, leaving unclassified
historical topics unclassified rather than fabricating or merging them.

## Admission sequence and remaining duplicate-risk boundary

1. Capture and persist the inbound request ID, exact text, and user-observed
   binding. If a retry ID has a different payload, report a conflict; do not
   overwrite either user's context. Reading the server's newest exchange is
   not equivalent to knowing what a concurrent composer displayed.
2. Retrieve candidate discussions with explicit coverage, then resolve any
   associated task. Semantic evidence may propose matches; lexical overlap,
   model confidence, names, and model `new_work` flags are not authority.
3. If multiple discussions or tasks are plausible, persist the pending
   clarification against this request and ask naturally in the same composer.
   A later answer resolves that exact pending question; it must not silently
   answer a permission hold or whichever question is newest at that instant.
4. A new-topic proposal may create/name a context, **not start work or detach
   the observed task automatically**. A task-launch proposal conflicting with
   an existing binding cannot bypass the conflict by inventing a new context
   ID or claiming that the old task just completed. Require a committed
   resolution against the observed binding before the dispatch transaction.
5. Atomically recheck request/binding revisions and record membership plus the
   dispatch outcome. For a follow-up, pass exact human text, source exchange,
   task identity/version into `TaskInstructionRevision.append`; do not weaken
   its owner, permission, admitted-action, or uncertain-effect checks. A stale
   candidate is information for re-resolution, never a fallback to `start_task`.
6. Keep launch-slot uniqueness and receipt consumption in the same database
   transaction as task creation. Existing `TaskRunner.create_task` commits
   internally, so this needs a deliberate transaction-composable primitive;
   adding a context row before the current call is not sufficient atomicity.
   Uncertain handoff must not cause automatic launch/action replay.

**Not solved by contexts alone:** creating another named context for a
misclassified follow-up merely moves the duplicate-task bug. A text-only
semantic classifier cannot prove that no relevant discussion exists. The
dispatch/binding protocol must make that conflict visible and fail closed
where independence is unresolved, rather than accepting a model-created
context as authorization. Clearly independent work should remain natural;
there is no claim here that retrieval scores or a model label make that
distinction infallible. Do not deploy this as duplicate-proof on prompt tests.

## Adversarial acceptance matrix for later routing work

| Case | Required behavior |
| --- | --- |
| Two chess discussions with identical words/names | Preserve both IDs and ask which; no task mutation or launch. |
| Indirect follow-up with no shared words | Resolve by discussion meaning/history; do not infer new work from zero overlap. |
| Weather request while chess runs | Allow genuinely independent work only through a fresh committed resolution; do not append it to chess or grant action permission. |
| Model invents target/context or says `new_work=true` | Reject identifiers outside the retrieved/bound set; no new-context execution bypass. |
| Candidate limit hit, database failure, or historical pure chat omitted | Report incomplete/unavailable evidence; not a no-match authorization. |
| Task completes/cancels after retrieval | Preserve binding and fail/re-resolve; no replacement task or terminal-state reopen. |
| Action/skill already admitted, permission pending, or effect uncertain | Preserve evidence and refuse append; do not interrupt, approve, or replay. |
| Two tabs send against the same binding revision | One conditional resolution wins; loser remains a preserved unresolved/conflict request. |
| Same request retried after response loss or completion | Return original receipt without model/worker replay; changed payload conflicts. |
| Context naming/matching contains injected instructions | Treat as data; exact identities and deterministic permission gates remain authoritative. |
| Persistence fails before/after resolution or launch handoff is uncertain | Preserve inbound request and honest outcome, with no success claim or automatic replay. |
| Context rename, midnight/session rollover, retention, or unavailable old source | Do not silently retarget to newest task; preserve provenance and expose scope/unavailability. |
