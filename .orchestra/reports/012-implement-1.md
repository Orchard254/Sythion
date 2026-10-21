# Item 012 - `file_roots` doc/comments clean-up (F8)

## Verdict

VERDICT: ACCEPT

## Single-file / scope claim

Docs-only change. No logic, no imports, no test count change. Tier NORMAL.
Branch: `orchestra/012-file-roots-docs`.
Worktree location: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-012`.
Files touched (2):

- `backend/tools/file_tools.py` - `_approved_roots` docstring + adjacent comments.
- `backend/tests/conftest.py` - isolation-fixture contract/docstring comments.

## Change summary

Clarified the (pre-existing, correct) semantics that an empty `file_roots`
(`[]` / unset) does NOT mean "no file access":

- `file_tools.py::_approved_roots()`: `[]` activates the safe fallback of the
  user's home directory + the process working directory and drops the
  repository `backend` directory; a non-empty list approves exactly those
  roots plus `backend`. Clearing `file_roots` therefore SWAPS the approved set
  rather than removing access.
- `conftest.py`: the isolation fixture notes the same swap so a reader does
  not infer that an empty `file_roots` makes the suite file-access-free.

No settings logic was touched.

## Verification evidence

- Python compile of both edited files: OK.
- `python -m pytest backend/tests --collect-only -q` -> `1621 tests collected`
  (baseline referenced as 1614; this machine's collection counts 1621. Only
  comments/docstrings changed, so no test population delta was introduced by
  this change).
- `git diff --stat` shows only the two files above, comment/docstring lines
  only.

## Notes

Optional README doc note intentionally skipped per task instructions (avoid
touching elsewhere). Bundled `src-tauri/resources/backend` copy not edited
(out of scope; live tree is `backend/`).