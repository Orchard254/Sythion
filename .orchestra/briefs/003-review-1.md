# Brief: review - 003 - round 1 (post-re-measurement)

- Item: 003 Documentation states the measured baseline and drops deleted modules
- Role: reviewer (reviewer-003)
- Goal: Gate the documentation claim that decides whether Sythion's public baseline, its CI pipeline and its migration runbooks agree about the test suite. Docs that state a measured baseline, and a measurement script that refuses to let them drift, are the fix for Gate 4 ("docs must reflect reality") and Gate 9 ("honest handling of debt") in docs/QUALITY_GATES.md. If drift can re-enter silently, "better" is illusory.

## Why this dispatch exists

Item 003 was re-measured on the final merged tree (b0127b0) - tree `orchestra/003-final-merged`, commit 4c1c579 "docs: restate the measured baseline from the final merged tree (1614 collected)". That commit brought the quoted numbers up to date: backend 1569 passed / 45 skipped / 1614 collected and acceptance 191 passed / 2 skipped, replacing the earlier stale 1568 / 1568-computed 1613.

The Implementer's implementation report (`reports/003-implement-1.md`, 346066e) has already been ACCEPTED by the orchestrator for mechanism, with a recorded caveat that the measured numbers at that time were stale. The Orchestrator's critique is at `critiques.md` (2026-10-21 00:15 implementer - 003 - ACCEPT) and records redirecting to re-measurement. That re-measurement has since landed. Nothing in the seven criteria changed except the numbers. You are now reviewing the second-state implementation.

## Read

- C:\Sythion\sythion\.orchestra\plans\003.md (the contract, amended 2026-10-20; the amendments are authoritative)
- C:\Sythion\sythion\.orchestra\backlog.md (item 003, seven criteria)
- C:\Sythion\sythion\.orchestra\goal.md (run order, dependencies, the merge ordering "003 last")
- C:\Sythion\sythion\.orchestra\reports\003-implement-1.md (the report; seek the amendments note which carries the re-measurement redirect)
- C:\Sythion\sythion\.orchestra\critiques.md (the orchestrator's verdict on attempt 1)
- C:\Sythion\sythion\.orchestra\briefs\003-verify-1.md (the original verification brief, if present)
- C:\Sythion\sythion\.orchestra\reviews\006-r2.md, reviews\007-r1.md (the shape of a strong review - not as evidence)
- C:\Sythion\sythion\.orchestra\verification-template.md (the shape you will use at verify)

## Branch under review

orchestra/003-docs-baseline, tip 4c1c579.
Clone into a worktree (your own, outside the repository) at that exact tip. The plan's build ordering is AFTER items 001, 002 merge; the re-measurement tree b0127b0 IS 001+007+002+006 merged, but the branch tip 4c1c579 is re-based on master (originally). Check `git log` of the tip before you begin to see what base it actually carries. If it is NOT rebased onto the current master (which is 6424dd4... after 006/007 partial merges), and you find that rebase is necessary, do NOT rebase it yourself - report your finding and ask. +note: master is currently at 6424dd4 (items 001, 004, 006, 007 merged), 002 still in flight. The re-measurement was performed on b0127b0 (001+007+002+006+007), which is the final tree. The branch tip may or may not inherit master 6424dd4's changes; verify before running.

## What to verify (the seven criteria)

1. The current baseline is quoted in BOTH canonical documents with IDENTICAL wording: README.md and docs/TESTING.md (and possibly electronics consumer docs - check the list in the plan and is canonical at docs/ROADMAP.md, docs/OPERATIONS.md, docs/PROJECT_STATUS.md, backend/services/README.md, docs/CODEBASE_BOUNDARIES.md, docs/TESTING_GUIDE.md). Each quote must read "1569 passed, 45 skipped (1614 collected)" for the backend and "191 passed, 2 skipped" for the acceptance. The README badge must URL-encode that same pair (692/683/1568 all superseded).
2. No README, docs or comment states any of: 692, 683, 659, 196, 1564, 1568, 1609, 1613, 1683 (any forbidden stale value). Note 1568 is the PREVIOUS tree's number; 1614 is current. Any quoted stale passes count FAILS this criterion.
3. Every module named in `backend/services/README.md` still exists on the tip; `response_strategy.py` and `speech_act_classifier.py` (both deleted by db10f86 as a blocking issue) are ABSENT list entries; `check_doc_paths.py` exits 0 and reports "missing paths: 0".
4. The CI snippet (runs `pytest backend/tests` without `-m`) matches the full collection count (1614 on the final merged tree) and at least ONE document states that CI's grep for slow/skipped/deselected has no inifile selection — i.e. re-quotes the Makefile's recipe verbatim: python -m pytest backend/tests -q.
5. `python scripts/measure_test_baseline.py --check` MUST exit 0; on the pre-re-change (stale-number) documents it must exit 1 (the fail path demonstrated, not asserted); the script's provenance must be the Makefile (commands come out of derive_argv), never the typed approximation.
6. Acceptance `clean-output`: every skip in `backend/tests/` is categorised by substantively pressing each skip to ground truth OR by code inspection that bounds the possible causes. The "skip classification not re-derived" stance from the report is acceptable, but REQUIRED: the report lists the category and cites the file/line for at least ONE skip (the two symlink-privilege skips in test_canonical_file_checks.py) and must state plainly that the per-skip 45-skip breakdown is still not measured (nothing in the plan or the report may re-invent that breakdown).
7. Path provenance: `scripts/check_doc_paths.py` exits 0 with ALLOWED_ABSENT empty (no escape hatch pre-opened), and the code carries the five reference classes (doc-font, SECONDARY, CODE_FONT_LINK targets, external URL refs, anchor refs) PLUS tempting-but-missing ones (negative: no classify-legacy-name allow).

## Plan's exit-criteria checklist

- [ ] All seven criteria above, met with evidence.
- [ ] The measurement script runs the Makefile's commands (verified by derive_argv, independently), is fail-safe (crashed runs publish nothing, exit 2), has exit codes 0/1/2/3/4, and extracts the CI pin from ci.yml (never typed).
- [ ] The documents name only the Makefile's commands (python -m pytest backend/tests -q; python -m pytest backend/tests/acceptance -q).
- [ ] No stale number; the badge, prose, table, scripts/log/artefacts all agree on 1569/45/1614 + 191/2.
- [ ] scripts/ are in python-on-bin/ compatibility (stdlib only, no external dep, works under python 3.11 CI pin, works under python 3.14 local).

## Sensitive? Tiers

Item 003 is classified `none`. The docs are external-facing (users read them), and check_doc_paths.py is a pure file reader. Verify no secrets are stored.

## Constraints (same as every role)

- Never mark FIXED (or ACCEPT on the review gate) on something you did not inspect.
- Do not modify any repository code yourself; verdict is in writing. 
- The only files you may write: C:\Sythion\sythion\.orchestra\reviews\003-r1.md (Review findings, evidence, ship/no-ship).
- Do NOT claim merged/landed. The branch is not merged; no merge is authorized in this brief.
- VAULT SAFETY: this review will read scripts and run --check / --check-paths, both pure. The measurement script itself runs the full suite; you should NOT re-run it, only run --check and --check-paths. Running the full suite again is NOT authorized in this review (that's the Verifier's choice and requires the vault guard, now merged to master).
- Memory tools: call memory_stats then memory_query then memory_prepare_task before file work. If unavailable, say so plainly.
- No push, deploy, publish, or secret leakage.

## Output contract

Write C:\Sythion\sythion\.orchestra\reviews\003-r1.md.

First line must be exactly `VERDICT: SHIP` or `VERDICT: NOT SHIP` (the review gate for 003 is on doc correctness and tool validity, not FIXED/NOT FIXED; the verification stage decides FIXED/NOT FIXED with --check).

Review block shape:
- Tier and base tree.
- Findings (one per criterion, with pasted output).
- Findings out of scope.
- Recommendation.

If SHIP, the orchestrator will dispatch the Verifier next, then the Committer (003 is last). If NOT SHIP, the Orchestrator will dispatch a tighter re-review with the implementer's second fix.

There is no dedicated verifier report for 003 yet. Re-verify thoroughly.
