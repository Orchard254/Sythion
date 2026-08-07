# Knowledge Log: Grounded Facts & Open Questions

A living record of verified facts about Sythion (Synth). The goal is to stay
grounded: **every claim is either verified (with the method of verification) or
explicitly marked UNVERIFIED/ASSUMED.** When a doc conflicts with observed
behavior, the observed behavior wins and the doc is noted as stale.

Docs in this repo are stale by default — README says "74 tests" (suite is ~412)
and lists old tool names (`get_time`) that no longer exist (`system_time`).

---

## Verification discipline

1. Facts come from one of: code paths (`file:line`), passing tests, live
   backend logs, or a live repro. Everything else is an assumption.
2. Before changing anything, re-verify the surrounding contract; green tests
   alone are not proof the change is sound at the architectural layer.
3. Fix at the layer where the contract was violated — never patch the symptom.

## Verified contracts & invariants

1. **Ollama Cloud tool-call `arguments` must be JSON objects, never strings.**
   Evidence: live repro — `"{}"` (string) → HTTP 400
   `"Value looks like object, but can't find closing '}' symbol"`; `{}` (object)
   → 200. Fix: `model_engine._build_context` replays stored `params` as dicts;
   `task_execution_loop` persists `params` on the `tool.executed` event.
   Regression tests: `backend/tests/acceptance/test_brain_contract.py`
   (`test_tool_replay_sends_arguments_as_object`).

2. **Config `Settings` singleton captures env at import time.**
   `backend/core/config.py`. `SYTHION_AUTONOMY_LEVEL` is read once; deleting the
   env var after import does not reset `settings.autonomy_level`.

3. **`tests/conftest.py` pins `autonomy_level="guarded"` for the whole suite**
   (autouse fixture). Without it, an ambient `SYTHION_AUTONOMY_LEVEL` from the
   dev shell leaks into every test (mutating tools become model-visible,
   destructive actions skip approval). Autonomous-path tests opt in via
   monkeypatch. Evidence: before the fix, 7 tests failed from env leak.

4. **Dual import convention.** Modules import both `backend.*` and top-level
   (`tools.*`, `api.*`, `core.*`) via `try/except ImportError`. Both
   `C:\Ai\sythion` and `C:\Ai\sythion\backend` must resolve.
   - `python -m pytest tests -q` from `backend/` needs NO PYTHONPATH (pytest
     inserts the repo root via basedir + CWD). Verified locally.
   - Standalone scripts doing `import backend.*` DO need both roots on
     PYTHONPATH. Verified: `python -c "import backend.main"` from `backend/`
     fails with `ModuleNotFoundError: No module named 'backend'`.
   - `backend/main.py:15` self-inserts its dir for uvicorn's `tools.*` imports.

5. **`core/` is the bottom layer; it must not import `api` or `tools`.**
   Verified: no imports of `api.schemas`/`tools` in `backend/core/`.
   Policy shared by schemas + tools lives in `core/` (e.g. `core/hotkeys.py`),
   so the model-facing schema description and runtime enforcement share one
   source. `api/schemas.py` is DTO/validation only.

6. **Tool contract.** `BaseTool` subclasses define `name`, `description`,
   `input_schema` (Pydantic v2, `model_fields`), `execute`, `verify`,
   `get_policy_requirements`, `validate_targets`. `validate_targets` is
   deterministic and side-effect-free.

7. **`app_close` verifies by process liveness, not window title.**
   `backend/tools/app_tools.py`: after WM_CLOSE it captures PIDs and checks
   `_process_alive` (OpenProcess/GetExitCodeProcess; ACCESS_DENIED treated as
   alive). Title re-probe is a second line of defense. Reason: an unsaved-work
   prompt is a dialog whose caption may not contain the app name (ASSUMED, see
   open items).

8. **Model decision retries.** When the model returns text without a control or
   action call, the loop retries up to the repair budget, then fails honestly
   with the exhausted code (no local fallback). Evidence: live log
   "Model decision error (attempt 1/2)" then success on attempt 3;
   `backend/services/task_execution_loop.py` + `core/budgets.py`.

9. **Skill proposals are generated from verified traces** — logs show
   "Proposed skill from 2 traces" then "Generated proposed skill N". Skills are
   declarative workflows over registered tools, never executable code.

10. **The `«loop:24»` token is a model hallucination.** No code in the repo
    produces it (grep over the whole repo). It appeared as a tool parameter
    because the model lacked schema guidance.

11. **The user's Ollama Cloud account has exactly 18 available models.**
    Evidence: live `GET https://ollama.com/api/tags` with the account key
    (2026-08-06). The `/api/tags` `details` objects returned no `families`
    field for this account. Current `SYTHION_SELECTED_MODEL=minimax-m3:cloud`
    (adapter resolves to `minimax-m3`).

12. **`deepseek-v4-flash:0731` returns HTTP 403 on this account**
    ("this model requires a subscription, upgrade for access").
    Evidence: live `/api/chat` with the account key (2026-08-06). Listing in
    `/api/tags` does NOT mean the model is usable on the account's plan —
    subscription-gated models still appear in the list. This validates the
    open item about discarded provider error bodies: without reading the body,
    a 403 would surface as a bare failure.

13. **Full availability sweep (all 18 models, one-word prompt, 2026-08-06).**
    - Reply (200): `gemma4:31b`, `gpt-oss:120b`, `gpt-oss:20b`, `minimax-m3`,
      `nemotron-3-nano:30b`, `nemotron-3-super`, `nemotron-3-ultra`.
    - Subscription-gated (403): `deepseek-v4-flash:0731`,
      `deepseek-v4-flash:preview`, `deepseek-v4-pro`, `glm-5.1`, `glm-5.2`,
      `kimi-k2.6`, `kimi-k2.7-code`, `kimi-k3`, `minimax-m2.7`,
      `mistral-large-3:675b`, `qwen3.5:397b`.
    - Caveat: this proves plain chat access only; native tool-call support
      (required by the task loop) is NOT yet verified for the 7 usable models.

14. **Multi-turn tool-call flow verified on a switched model (no 400s).**
    A full "system audit" task ran 5 sequential tool calls
    (`system_time` → `system_battery` → `system_disk` → `system_processes` →
    `app_list`) then `complete_task`, cleanly. `.env` had been switched to
    `gemma4:31b` at that point; the replay-arguments-as-object fix holds
    across providers. Evidence: live backend log 2026-08-06.

15. **`SYTHION_FILE_ROOTS` is unset → all filesystem access is blocked.**
    Config default `file_roots: List[str] = []` (`backend/core/config.py:33`).
    Every `file_list`/`read_file`/etc. fails with `TARGET_OUTSIDE_ROOTS`.
    Evidence: live log — `file_list C:\Users\orchard\Documents` →
    `TARGET_OUTSIDE_ROOTS` (2026-08-06).
    **Status: FIXED 2026-08-06** — `.env` now sets
    `SYTHION_FILE_ROOTS` (JSON) to `C:/Users/Administrator/{Documents,
    Downloads, Desktop}`; verified `settings.file_roots` loads and a live
    `file_list` on Documents succeeds.

16. **`SYTHION_FILE_ROOTS` must be JSON, not comma-separated.**
    Verified: `Settings(_env_file=None)` raises `SettingsError` on
    `'a,b'`; a JSON list parses. The README documents the comma format —
    which would crash startup, since `settings = Settings()` runs at module
    import. The README's documented value is a boot-breaking trap.

17. **The model hallucinated `C:\Users\orchard\Documents`.** No such profile
    exists on this machine; the real profile is `C:\Users\Administrator`
    (Documents/Downloads/Desktop exist there). The system prompt
    (`model_engine.py`) only says "never access files outside approved
    roots" — it never lists the roots, so the model has no grounded source
    for paths and guesses from the user's name.

18. **Session ID is process-lifetime; conversation turns are session-scoped.**
    `backend/core/session.py` generates a fresh uuid at module import and
    `model_engine._build_context` queries `ConversationTurn` by
    `session_id` (last 20). A backend restart therefore strands prior
    sessions' turns (the 02:35:30 restart lost the 02:27–02:31 onboarding
    conversation). Cross-restart memory is ONLY via `memory_store`/
    `memory_recall`, which were never called during onboarding → recalls
    return empty → the model correctly asked for clarification instead of
    fabricating. The system prompt (model_engine.py:139–142) already
    instructs `memory_store` on preferences/"remember this", and the loop
    has a "remember that"/"note that" intent pattern
    (task_execution_loop.py:842).

19. **Persistent memories are auto-injected every task** (not just via recall).
    `model_engine._build_context` (model_engine.py:274-283) prepends the top-20
    `MemoryEntry` rows (by `updated_at` desc) to the user message as
    `PERSISTENT MEMORY & USER PREFERENCES`. Verified by code reading. So the
    model always SEES stored preferences even when `memory_recall` returns 0.

20. **`memory_recall` previously used strict substring matching and hid real
    memories from natural-language queries.** Verified against the real DB:
    `query="user profile preferences"` returned 0 even though 5 entries
    (`user_name`, `address_preference`, `conversation_style_preference`, ...)
    existed, because no single key/value contained that exact phrase. The model
    concluded "no record" — data existed but was invisible. Fixed in
    `backend/tools/memory_tools.py`: queries are tokenized; each entry is scored
    (exact key > value/category, light plural stemming); ALL positive-score
    entries return ranked (the model, not the tool, decides relevance). Real-DB
    probe now: `"user profile preferences"` → 5, `"preference"` → 4,
    `"onboarding"` → 0 (correct: no such memory exists).

21. **`SYTHION_FILE_ROOTS` enforcement broke 8 legacy tests that used temp dirs**
    (7 `TestFileTools` + `test_executor_cancellation_check_blocks_os_action`):
    targets under `%TEMP%` were rejected `TARGET_OUTSIDE_ROOTS`. The repo's
    established pattern is `monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])`
    (see test_file_mutations.py / test_action_approval.py / test_canonical_file_checks.py
    / test_target_validation.py). `test_tools.py`'s `temp_dir` fixture now does the
    same via `"tools.file_tools._approved_roots"` — note the module-path split:
    acceptance tests import `backend.*`, unit tests import top-level `tools.*`,
    and each needs its own module object patched. Also: the root in the lambda
    MUST be `Path(dirpath).resolve()` — `tempfile.mkdtemp()` returns the 8.3
    short form (`C:\Users\ADMINI~1\...`) and the string containment check in
    `_contained` (file_tools.py:120) compares against the target's LONG form,
    so an unresolved short-form root never matches.

22. **`tests/acceptance/test_gui_fixture.py` runs real UIA against a live window
    and is sensitive to user input.** The fixture edit accepts live keystrokes;
    a user typing during the run corrupts the assertion (observed
    `'chello-fixture'` vs `'hello-fixture'` when the user typed `c` mid-run).
    Passes in isolation. Not a code bug; rerun without using the PC.

23. **INTELLECTUAL HONESTY contract added to the system prompt**
    (`model_engine.py`, `_build_system_prompt`): never claim memory/recall you
    cannot ground; distinguish "I remember X" / "I don't have that stored" /
    "let me check"; never guess paths or personal details; a 0-result
    `memory_recall` does NOT prove absence — check broader query + the
    auto-injected PERSISTENT MEMORY section first. Pinned by
    `test_system_prompt_enforces_intellectual_honesty`.

24. **Memory consolidation pilot (live, both models passed).** Research-grounded
    (PREMem pre-storage reasoning; HINDSIGHT; Letta core-memory blocks; Mem0
    ADD/UPDATE/DELETE/NOOP). Scripted onboarding conversation through the
    consolidation prompt on `gpt-oss:120b` AND `gemma4:31b`: both produced only
    grounded `user_statement` ops, correctly IGNORED the transient Friday
    reminder and audit chatter, recognized already-captured entries (noop), and
    emitted strict parseable JSON. So consolidation quality is OK on the
    current selected model; no model whitelist needed. Evidence: live runs of
    `scripts/memory_consolidation_pilot.py`.

25. **Background consolidation built with a strict gate.**
    `backend/services/memory_consolidation.py` + new `adapter.chat()` (plain
    chat, no tools/`ensure_capabilities`). Wired into the loop at
    `task_execution_loop._consolidate_memory_from_trace` (runs right after
    `_save_conversation_turn` on COMPLETED). Gates: only `confidence=="high"`
    ops auto-applied; DELETE is NEVER auto-applied (logged for review); capped
    at 10 ops/run; unparseable/adapter-failure returns `[]` (best-effort, never
    breaks the loop). Gated by `settings.memory_consolidation_enabled`
    (`SYTHION_MEMORY_CONSOLIDATION_ENABLED`), which conftest pins OFF so tests
    never hit the model API. Live E2E verified against the real model on a
    throwaway DB: `user_location`, `work_setup`, `address_preference` applied.

26. **Research notes (2026 agent-memory state of the art):** memory = context
    engineering (what tokens enter the window, how they're rewritten). Tiers:
    core blocks (always in-context, agent-editable, ~50k chars) / episodic
    (per-session summaries, retrieved on demand) / semantic (vector or graph).
    Practitioner consensus: async writes + sync reads; start vector, add graph
    only when needed; define scope first; set a memory budget (rot control).
    Reflective-memory papers (PREMem EMNLP-25, HINDSIGHT arXiv 2512.12818,
    RMM ACL-25) shift reasoning to WRITE time (consolidation), distinguishing
    factual/experiential/subjective + evolution patterns. Caveats: memory
    generation is model-dependent (some strong coders are weak at it); naive
    consolidation causes memory rot, duplicates, contradictions, and confident
    wrong beliefs (Bi-Mem, Mem0, Letta). This is why the gate is confidence+
    source based and deletes require review.

27. **File roots are now a live grant system (no restart needed).** Approved
    roots = union of `SYTHION_FILE_ROOTS` (.env boot defaults for this device)
    + `file_roots` granted at runtime via `PUT /api/v1/settings` (persisted in
    `settings_metadata`, applied immediately). `core/settings_store.py`
    `resolved_file_roots(db)` is the single merge/dedup source; `file_tools`
    `_approved_roots()` consults it on every containment check (fresh DB read
    per op, falls back to env-only if the DB is unavailable so a failure can
    never silently widen access, then home+cwd when nothing is configured);
    `GET /api/v1/settings` reports the same union so reported == enforced. The
    frontend dashboard already has an "Approved file roots" field. The system
    prompt now teaches the model the mechanism (FILE ACCESS & GRANTS) so a
    `TARGET_OUTSIDE_ROOTS` rejection no longer dead-ends with "I have no way to
    gain access" — it names the path and asks the user to grant it in Settings.
    Live E2E (throwaway DB): grant via settings_store -> root appears in
    `_approved_roots()` immediately, containment honored, outside still
    rejected. Root cause of the dead-end: file tools read only the config
    singleton, the API reported `settings or persisted`, and the model didn't
    know grants existed. Suite: 443 passed / 2 skipped.

28. **Wrong path memories poisoned every answer — fixed by introspection +
    memory hygiene.** Live repro: "check my root path" -> the model claimed
    "your approved root path is C:\Ai\sythion" (false) and "check files on my
    desktop" -> it used `C:\Users\orchard\Desktop` (no such user; real user is
    Administrator and that Desktop IS an approved root). Root cause: two
    confidently-wrong memories (`system_root_path`=C:\Ai\sythion,
    `user_desktop_path`=C:\Users\orchard\Desktop) were injected into context on
    every task and the model had no grounded way to know its real roots. Fix
    (three layers): (a) memory hygiene - deleted the two provably-wrong rows
    from the live DB (backup at
    `%TEMP%\opencode\sythion_memory_backup_20260806_033257.json`); (b) new
    read-only model-visible tool `system_file_roots` (returns enforced
    approved roots + OS user + home, grounded in `_approved_roots()`, so the
    model queries instead of guessing); (c) prompt - INTELLECTUAL HONESTY now
    says stored memories can be stale, live tool output wins and must never be
    contradicted; FILE ACCESS & GRANTS says call `system_file_roots` and never
    guess a root/user; MEMORY MANAGEMENT forbids storing device paths/roots/
    usernames as memory (they go stale). Verified: live tool output returns
    the three Administrator roots + user + home; suite 444 passed / 2 skipped.

29. **No task continuity + environment-memory pollution — fixed deterministically.**
    Live repro (2026-08-06 03:52-03:57 logs, tasks d930b4d9/e3aa75c4/
    51b36de4): the model looped redundant read-only observations
    (`system_time`/`system_disk`/`app_list`/`file_list` on the same paths)
    until the 50-action budget failed the task with `ACTION_LIMIT_EXCEEDED`,
    then stored 7 environment snapshots as memory (`approved_roots`,
    `disk_usage_documents`, `summary`, `installed_apps`, `active_window`,
    `current_time`, `env_home_dir`) — the exact stale-path hazard of entry 28.
    The user's "continue" task called `memory_recall(category='task')`, found
    nothing, and asked "which previous task?". Root cause (3 layers): (a) the
    memory prompt only framed memory as user preferences, so the model stored
    machine snapshots instead of task state; (b) `_save_conversation_turn`
    stores only request + final status — the goal lives only in the terminal
    task row, which a new task never reads; (c) the model cannot be trusted to
    self-police (it ignored the existing memory rule). Fixes:
    - **Deterministic guard** in `MemoryStoreTool` (`_rejects_live_state`,
      `backend/tools/memory_tools.py`): refuses device-identity keys
      (`approved_roots`, `env_home_dir`, ...), ephemeral-state keys
      (`current_time`, `active_window`, `installed_apps`, `summary`, ...),
      `category='environment'` wholesale, path-like keys, and absolute-path
      values → `MEMORY_STATE_STORAGE_REJECTED`. `user_name`/`username` are
      deliberately allowed (the owner's name is a legit profile fact).
    - **Continuation seeding** in `ModelDecisionEngine._previous_task_context`
      (`backend/services/model_engine.py`): explicit continuation phrases
      ("continue", "resume", "proceed", "go on", ...) attach to any prior
      executed task; short/empty requests attach only to a recent (<12h)
      non-terminal (failed/interrupted/paused) task. Injects PREVIOUS TASK
      CONTEXT (request, goal, last 5 tool actions, outcome) into
      `_build_context` so a continuation never starts cold.
    - **Prompt**: memory rules now forbid live-state snapshots (tool enforces),
      continuation guidance, and a new EFFICIENCY & ACTION BUDGET section
      (budget = 50 actions, do not re-run identical read-only observations).
    - **Hygiene**: removed the 7 polluted rows from the live DB (backup
      `%TEMP%\opencode\sythion_memory_cleanup_20260806_041008.json`); kept all
      genuine user preferences. Tests: guard tests in `test_memory_tools.py`
      (18) + continuation tests in `test_conversation_memory.py` (4).
      Suite: 467 passed / 2 skipped.

30. **Capability discovery (`system_capabilities`) — the self-reflection surface.**
    Added `SystemCapabilitiesTool` (`backend/tools/system_tools.py`) as the
    first move of the self-growth plan (user: "i dont even know where to
    start"). Empty-input, read-only; introspects `create_all_tools()` and
    reports registered count, per-tool domain/risk/flags, the model-visible
    subsets per autonomy mode (mirrors `_model_visible_tools` via
    `_model_visible(reqs, autonomy)` — visibility is cheap-policy only:
    `read_only` + no confirmation gate + not on the kill-list), and
    `_KNOWN_GAPS` (8): file_search, system_volume, power control, window
    enumeration, local network status, brightness/display, per-process
    CPU/mem, OCR. Prompt gained a CAPABILITIES & SELF-AWARENESS section
    (call it to know what you can do; name missing capabilities honestly
    instead of faking). Verified: live run reports 35 tools / 18 guarded
    visible / 33 autonomous / 8 gaps; suite 468 passed / 2 skipped.
    Invariant: `system_capabilities` must stay read-only and policy-cheap so
    it can never grant itself anything. Next self-growth step (L2): auto-enable
    a skill proposal after N successful verified runs, replacing manual approval.

31. **L2 shipped: skill proposals auto-enable after N verified traces.**
    `skill_engine.maybe_generate_proposal` now accumulates every task whose
    verified trace is materially similar onto the existing proposal (was:
    created at 2 traces then ignored later runs) and promotes it once the
    accumulated count reaches `SYTHION_SKILL_AUTO_ENABLE_THRESHOLD` (default 5;
    0/negative restores manual approval). Promotion = the SAME
    `approve_proposal` path the owner uses, preceded by re-validation against
    the CURRENT read-only tool set (`_auto_enable_if_ready`). Owner-submitted
    proposals never auto-enable. Safety invariants, all tested:
    - rejected signatures are never re-proposed or auto-enabled (owner intent
      is permanent until they re-submit via POST /skills);
    - an enabled proposal's signature returns None (a live skill already
      exists), no duplicate proposals;
    - owner-submitted proposals (skill_id set, trace_ids `[]`) never
      auto-enable — the guard is explicit, not implicit in empty trace_ids;
    - a proposal whose tool disappeared from the allowed set stays `proposed`
      with stored validation errors;
    - zero threshold keeps the old always-manual contract.
    Tests: 7 new in `TestProposals` (test_skill_engine.py). Suite 475/2.

32. **L3 shipped: tool-authoring pipeline with strict gates.**
    `tool_authoring.py` implements the model-written tool proposal pipeline:
    - **Validation**: name (lowercase, no collision with builtins), description
      (no secrets), input schema (no coordinates/secrets), implementation
      (AST-checked: no forbidden imports like subprocess/os/socket, no eval/exec,
      must define a class inheriting BaseTool with execute() and
      get_policy_requirements()).
    - **Sandbox**: executes implementation in a restricted namespace with
      limited builtins, verifies class instantiation and interface compliance.
    - **Staged enablement**: approved tools start in guarded mode; after 5
      successful verified uses, promote to autonomous.
    - **Owner approval**: explicit review required (POST /tools/proposals/{id}/approve).
    API: POST /tools/propose, GET /tools/proposals, POST approve/reject.
    Tests: 23 new in test_tool_authoring.py. Suite 614/2.

31. **Relational memory layer (memory edges + selective injection).** Added
    2026-08-06. New `memory_edges` table
    (`backend/models/database.py`, `MemoryEdge`: source_key/relation/target_key,
    weight, source_task_id, timestamps; both key columns indexed; created by
    `Base.metadata.create_all` like the other tables — no db_manager migration
    needed). New tools `memory_link` (upsert directed link; applies the same
    `_rejects_live_state()` gate to BOTH endpoints, validates relation
    `^[a-z][a-z0-9_]{0,49}$`, rejects self-loops, free-text targets allowed)
    and `memory_recall_related` (anchor key or best-scored fuzzy match, outward
    BFS capped at 2 hops / 25 edges, returns linked entries + unlinked_facts).
    Registered in `create_all_tools` → 37 registered / 20 guarded-visible / 35
    autonomous (both new tools are model-visible; `memory_link` is
    `app_internal` like `memory_store`). Consolidation
    (`memory_consolidation.py`) gained `edge_ops` (`add_edge`/`remove_edge`)
    parsed by `parse_edge_ops`/`sanitize_edge_ops` and applied via `MemoryLinkTool`
    under the SAME gates as memory ops: high-confidence only, remove_edge never
    auto-applied, capped per run, silent on parse failure. `consolidate_memory`
    signature is `(db, turns, memory_entries, adapter=None, memory_edges=None)`
    — the 4th positional arg MUST stay `adapter` (existing tests call it
    positionally); the trace caller passes `memory_edges=` by keyword. Phase 4:
    `_build_context` no longer dumps top-20 entries; it anchors on
    `normalized_goal or original_request`, scores entries with the same scorer
    as `memory_recall`, keeps top 5 relevant + 1-hop linked neighbors, injects a
    MEMORY LINKS block, and trims conversation turns to last 5 (was 20). Logs
    injected-vs-available counts per task. `_rejects_live_state` now also
    rejects path-shaped KEYS (not just path values). Full suite: 505 passed /
    2 skipped.

32. **Persistent session id (survives restarts).** Added 2026-08-06. `MemoryEdge`
    note above ("no migration needed") is now WRONG: it gained a `status`
    column (entry 33). `backend/core/session.py` rewritten: `get_session_id()`
    lazily reuses the persisted `current_session_id` setting unless
    `session_started_at` is a previous LOCAL calendar day → mints + persists a
    fresh id; `reset_session_id()` mints + persists + clears the in-process
    override; `get_current_session_id()`/`set_current_session_id()` keep the old
    names (backward compatible). Settings keys `current_session_id` /
    `session_started_at` via `settings_store.set_setting` (new internal helper;
    bypasses the ALLOWED_SETTINGS update whitelist). Graceful degradation: if
    the settings store raises, fall back to a stable in-memory uuid + warning,
    never crash. Verified: ZERO direct imports of `_CURRENT_SESSION_ID` anywhere
    — all call sites use accessors (`model_engine.py`, `task_execution_loop.py`),
    so no call-site churn was needed. Freezegun NOT installed; tests monkeypatch
    `session.datetime`.

33. **Memory-edge lifecycle (decay + owner review surface).** Added 2026-08-06.
    `MemoryEdge` gained `status` (`active`|`stale`|`removed`, index, default
    `active`) mirroring `proposed_skills.status`. Existing DBs get it via a new
    ALTER TABLE migration in `db_manager._apply_migrations` (PRAGMA table_info
    guard), so entry 31's "no migration needed" is superseded. Decay
    (`apply_edge_decay`, run at the top of the loop's consolidation cadence)
    halves weight per 7-day interval untouched (factor 0.5, interval 7d, floor
    0.1) and marks edges at/below 0.5 `stale`; decay NEVER deletes or removes —
    only the owner review endpoints transition `stale`→`active` (reinforce:
    weight 1.0, real touch bumping updated_at) or `stale`→`removed` (reviewed
    removal, row retained like rejected proposals). Decay issues a core `update`
    that explicitly preserves `updated_at` — ORM `onupdate` fires even for
    core UPDATEs executed via `Session.execute`, so it must be overridden in
    `values()`. `memory_link` re-link clears `stale`→`active` (reinforcement);
    recall walks and `_select_memory_context` filter out `status == "removed"`.
    New API under `/api/v1/memory/edges` (GET list w/ `status` filter,
    POST `/{id}/reinforce`, POST `/{id}/remove`; 404/422 semantics mirror
    skills proposals).

34. **`conversation_turns` retention (transcript leak fix).** Added 2026-08-06.
    `retention.py` now bounds the episodic transcript by the SAME window as
    tasks: `purge_turns(db, days, now)` sweeps any turn older than the window,
    including orphans whose task row was already purged (the leak: turns
    reference a purged task and were never swept); `_delete_complete_record`
    deletes the task's turns as part of a complete-record purge; the windowed
    API run reports `purged_turns` (new field, default 0 on `RetentionRunResponse`).
    Durable memory (`memory_entries`/`memory_edges`) is exempt — not a transcript.
    Verified: `_previous_task_context()` reads `Task`/`TaskEvent` only, never
    `conversation_turns`, so turns retention cannot break continuation seeding.

35. **Validation dump report (data only).** Added 2026-08-06. New
    `memory_context_log` table (one row per model context build: task_id +
    available/relevant/connected/links) written best-effort by
    `model_engine._record_memory_context_stats` via its OWN session (never
    commits the task-loop transaction) and trimmed to the newest 500 rows.
    `backend/services/validation_report.py::build_validation_report` returns
    per-store counts (memory_entries, memory_edges, conversation_turns, tasks,
    task_events, skills, proposed_skills, proposed_skills_pending), injection
    totals + per-run rows over the last N builds (default 50, cap 1000), and the
    distinct relation vocabulary in use. Endpoint `GET /api/v1/memory/report`.
    Route prefix note: ALL api routes are mounted under `/api/v1` in
    `backend/main.py:117` — API tests must use the prefix (404 otherwise).

    Full suite after the 4 fixes: **554 passed / 2 skipped**.

36. **Verification checks (2026-08-06).**
    - `memory_context_log` trim is FIFO: `_record_memory_context_stats` deletes
      `id <= max_id - 500 + 1` (newest id seen before the insert), so it always
      keeps the most recent 500 by insertion order and discards the oldest.
      Confirmed by `test_memory_context_log_trim_keeps_most_recent`
      (test_conversation_memory.py): 505 inserts → 500 survivors = task-5..task-504.
    - `num_ctx` is now explicitly set to `NUM_CTX = 65536` (64K) on all three
      payloads in `ollama_adapter.py` (ensure_capabilities probe,
      execute_tool_call, chat). Verified by `_payload_transport` assertions in
      `test_ollama_adapter.py` (`test_execute_tool_call_payload_sets_num_ctx`,
      `test_chat_payload_sets_num_ctx`) and the probe assertion in
      `test_cloud_capability.py`.
    - Why 65536: selected model `gemma4:31b` reports
      `model_info["gemma4.context_length"] = 262144` (256K) via live
      authenticated `GET /api/show`; Sythion's own input-cap is 50K
      (`max_input_tokens`), so 64K is comfortable headroom above it while
      requesting far less than the full 256K to bound GPU-time cost/latency.
      `parameters` is empty on Ollama Cloud, so the server default when unset
      is unknown — an explicit `num_ctx` also removes that unknown (Ollama can
      silently truncate the oldest messages once cumulative tokens exceed
      `num_ctx`, and historical defaults can be as low as 2048).
    - Ollama Cloud pricing finding (2026-08-06 web search): billing is by GPU
      time, not per-token (ollamatps.com, devtoolhub: "Usage reflects actual
      utilization of Ollama's cloud infrastructure — primarily GPU time").
      A larger window costs more GPU time per request, so 64K is a justified
      middle ground; no authoritative Ollama Cloud page recommends a different
      value. Revisit if real pricing/latency data suggests otherwise.
    - No other request fields, token caps, or budget/memory-injection logic
      were changed.

    Full suite: **557 passed / 2 skipped**.

## Known traps

- **Stale docs**: README test count (74) and tool names (`get_time`,
  `get_battery_status`, `observe_gui`) are wrong. Current names: `system_time`,
  `system_battery`, `system_active_window`, `system_disk`, `gui_observe`.
  Suite is 614 passed / 2 skipped (README's 74 is way stale).
- **CI import gate likely fails**: `.github/workflows/ci.yml` runs
  `python -c "import backend.main; import backend.api.routes"` from
  `backend/`. The same command fails locally without PYTHONPATH (verified). Not
  yet confirmed whether CI is currently red.
- **`uvicorn --reload` mid-task**: reload spawns a new process; the old process
  keeps draining its background task ("Waiting for background tasks to
  complete") while a second server starts. Don't reload during an active task
  when validating live behavior.
- **Config/env traps**: deleting `SYTHION_AUTONOMY_LEVEL` after import does not
  restore guarded behavior (see invariant 2).
- **Win11 Notepad**: unsaved content shows in the title as `*name - Notepad`.
  Its save prompt dialog is a separate window.

## PC-control audit (2026-08-06)

Verified by introspection of `create_all_tools()` (script at
`%TEMP%\opencode\tool_audit.py`) + `executor.py` policy + handoff brief
(`C:\Ai\SYTHION_FINAL_HANDOFF_BRIEF.md` §5 tool table). 34 registered tools;
17 model-visible in guarded mode, 32 in autonomous, 2 never callable
(`package_install`/`package_uninstall`, `requires_admin` →
`ELEVATION_NOT_ALLOWED` hard block in ALL modes, executor.py:248). Now 35
registered / 18 guarded-visible / 33 autonomous since `system_capabilities`
shipped (entry 30) — the tool itself self-reports the current counts. Now 37
registered / 20 guarded-visible / 35 autonomous since `memory_link` +
`memory_recall_related` shipped (entry 31).

**Verdict: not "full PC control" — by design.** The brief (line 13) explicitly
disclaims shell, service, browser-agent, remote-control, self-modifying. What
exists fully matches the brief's V1 tool table except two gaps (see below).

- **Covered (native, verified)**: files (list/read/create/write/move/copy/delete
  in roots), apps (list/launch/focus/close), system read (time, battery, disk,
  processes, active window), package search/list, web_search/web_fetch, GUI
  observe/invoke/set_value/click/type/hotkey/paste/focus on medium-integrity
  visible windows, memory, live file-root grants, cancel.
- **Hard-blocked / non-scope (deliberate)**: elevation (install/uninstall,
  services, UAC/secure desktop, elevated windows), shell (winget is the only
  subprocess tool; its mutators are blocked), browser-page automation, clipboard
  read, ambient monitoring/scheduling, connectors (email/calendar), remote
  access + cross-device (brief line 31 — directly conflicts with the owner's
  server/multi-device directive; needs an approved scope expansion).
- **Fixable gaps (not by design)**: volume level get/set (only
  `volumeup`/`volumedown`/`mute` media keys via gui_hotkey whitelist), power
  (lock/sleep/restart/shutdown) — no native tool and `win+l`/`ctrl+alt+del` are
  `DANGEROUS_HOTKEY_REJECTED`, brightness, local network status, file search by
  name/content, window enumeration (only `system_active_window`), per-process
  CPU/mem (list only), `gui_observe(use_ocr=True)` → `available: false` (OCR
  engine not bundled), scroll tool absent despite brief listing it, per-monitor
  DPI mapping (known v1 limitation, ARCHITECTURE.md:245).
- **Build order for these gaps** (autonomous direction): `file_search` →
  `system_volume` (Core Audio COM, comtypes already a dependency; pycaw not
  installed) → `window_list` → power/network/system-control family with
  explicit confirmations for irreversible ops.

## Open / not yet verified

- Does MiniMax m3 actually respect the improved `gui_hotkey` schema
  (description + `items`)? The fix is plausible but not live-confirmed.
- Ollama Cloud failure bodies: adapter discards the response body on
  `CLOUD_ERROR` (per earlier finding) — provider error detail may never reach
  logs. Verify at `backend/adapters/ollama_adapter.py`.
- Whether the Win11 Notepad save-prompt dialog caption contains "notepad"
  (drives how often the title-based re-probe catches it vs. the PID check).

## Target architecture (owner directive)

The owner's stated goal: if the project succeeds, Sythion runs on a server and
operates other devices, handling everything autonomously and seeking approval
ONLY for genuinely important things. All future design decisions are checked
against this direction.

- **Autonomy model**: proceed autonomously and carefully; approval is the
  exception, reserved for consequential/irreversible/expensive actions. The
  deciding gate must be a policy layer (importance classifier), not the model's
  in-context judgment, because the model cannot be trusted to self-judge
  importance consistently.
- **Phased roadmap (sketch, in dependency order)**:
  1. Trust layer first (largely built): verified-outcome-only tools,
     bounded model summaries, honest-failure loop, live file-root grants,
     guarded-by-default tests. This is the safe base for turning autonomy up.
  2. Importance/approval gate: classify each planned action as routine vs.
     consequential (cost, irreversibility, blast radius, targets, context).
     Only consequential actions ask; routine ones execute. Needs its own
     deterministic contract + tests, exactly like the honesty contract.
  3. Server readiness: real auth (control-token + loopback-only boundary is a
     local-shell trust model and cannot run unattended), remote-safe transport,
     long-horizon scheduling (currently one running task + process-lifetime
     session — does not survive restart), durable memory (the consolidation
     layer is the seed of this).
  4. Multi-device: device registry, per-device file roots/grants, remote action
     transport for "operate other devices".
- **Current status**: layers 1 is in progress; 2-4 not started. The build order
  is deliberate: you can only give a system autonomy it can audit.

## Lease-hold after terminal state (2026-08-07, entry 33 — VERIFIED)

**Symptom**: a just-completed task still holds the RunnerLease (next request
gets spurious `409 Another task runner is currently active`).

**Root cause**: `TaskExecutionLoop.execute_task` cancelled the lease heartbeat
only in the function-level `finally` (task_execution_loop.py), i.e. AFTER the
best-effort post-completion steps (`_propose_skill_from_trace`,
`_save_conversation_turn`, `_consolidate_memory_from_trace`). Memory
consolidation can run for tens of seconds (its timeout was 300s, already
lowered to 120s), so the heartbeat kept renewing the expired lease throughout
that window despite the task being terminal. Defect, verified by DB: a
`completed` task still had a renewing heartbeat (`heartbeat`/`expires_at` in the
future long after the task turned `completed`) and `_consolidate_memory_from_trace`
only `continue`s/`return`s for <2 turns, so the window is real for normal chats.

Layer: `services/task_execution_loop.py` (task-loop timing), not the runner or
schema.

**Fix applied**: on terminal-state detection (`COMPLETED/FAILED/…`) inside the
decision loop, cancel+await the heartbeat *before* running the post-completion
steps, so the lease is released as soon as the task is confirmed terminal and
the next request is not blocked on best-effort cleanup. The function-level
`finally` still cancels the (now already-done) heartbeat harmlessly. `run_task`
still `release_lease()`s on exit. Verified: full suite 614 passed / 2 skipped;
import + AST parse clean.

**Related (by design, not a bug)**: orphan tasks left in `running` by a crashed
process/`--reload` restart without a lease are marked `interrupted` on the next
stale-lease takeover (`_mark_task_interrupted`, task_runner.py) and pruned by
`retention.py`; no code change needed.
