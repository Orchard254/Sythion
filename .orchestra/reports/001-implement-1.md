# Report: implement - 001 - attempt 1

## What I did

### Read

- `.orchestra/briefs/001-implement-1.md`, `.orchestra/plans/001.md` (in full, including
  "Corrections from attempt 1" and the **Rule P** definition), `.orchestra/backlog.md`
  (item 001's six acceptance criteria), `.orchestra/goal.md`,
  `.orchestra/reports/scout-001.md`.
- `.github/workflows/ci.yml` (whole file, all four jobs),
  `frontend/package.json` (scripts block), `backend/requirements.txt` (read only),
  `backend/core/config.py`, `backend/api/routes.py` (the readiness endpoint),
  `backend/tests/test_setup_readiness.py`.

### Working tree

Dedicated worktree, as the brief required. I did **not** check out a branch, commit, or
modify anything in `C:\Sythion\sythion` (the orchestrator's tree).

```
PS> git worktree add C:\Users\orcha\AppData\Local\Temp\opencode\wt-001 -b orchestra/001-ci-gates-repo master
Preparing worktree (new branch 'orchestra/001-ci-gates-repo')
Updating files: 100% (4859/4859), done.
HEAD is now at e224319 chore(orchestra): scout backlog (5 ready items), researcher+scout critiques, planner briefs
```

All work below is inside `C:\Users\orcha\AppData\Local\Temp\opencode\wt-001`.

### The change

Exactly two repository files, one commit.

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | (a) trigger `on:` block, (b) `backend-tests` working directories, (c) `backend-tests` install step, (d) new pin-guard step, (e) `provider-e2e` install step, (f) `frontend` job vitest step |
| `frontend/package.json` | `scripts` block only: `"test": "vitest run"`, new `"test:watch": "vitest"` |

```
PS> git log --oneline master..HEAD
9ef7153 ci: make the pipeline actually gate this repository

PS> git show --stat --oneline HEAD
9ef7153 ci: make the pipeline actually gate this repository
 .github/workflows/ci.yml | 23 +++++++++++++++++------
 frontend/package.json    |  3 ++-
 2 files changed, 19 insertions(+), 7 deletions(-)
```

Full diff (`git diff master...HEAD`):

```diff
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 608b7cf..de40dc3 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -2,16 +2,14 @@ name: CI
 
 on:
   push:
-    branches: [main]
+    branches: [master, main]
   pull_request:
+  workflow_dispatch:
 
 jobs:
   backend-tests:
     name: Backend tests
     runs-on: windows-latest
-    defaults:
-      run:
-        working-directory: backend
     steps:
       - uses: actions/checkout@v4
 
@@ -21,18 +19,29 @@ jobs:
           python-version: "3.11"
           cache: pip
 
+      - name: Guard (test tooling install is pinned)
+        shell: pwsh
+        working-directory: ${{ github.workspace }}
+        run: |
+          $bad = Get-Content .github/workflows/ci.yml | Where-Object { $_.Trim() -cmatch '^(pip install|python -m pip install)' -and $_ -cmatch 'pytest' -and $_ -cnotmatch '==' }
+          if ($bad) { Write-Error ("unpinned pytest install: " + ($bad -join ' | ')); exit 1 }
+
       - name: Install dependencies
+        working-directory: backend
         run: |
           python -m pip install --upgrade pip
           python -m pip install -r requirements.txt
-          python -m pip install pytest pytest-asyncio
 
       - name: Run backend test suite
+        working-directory: backend
         # Provider-backed E2E (provider_e2e marker) is SKIPPED here: normal
         # CI never incurs provider cost and never needs credentials.
         run: python -m pytest tests -q
 
       - name: Import gate (packaged entrypoint compiles)
+        # Must run from the repository root: backend/api/routes.py imports
+        # backend.* absolutely, so the repo root must be on sys.path.
+        working-directory: ${{ github.workspace }}
         run: python -c "import backend.main; import backend.api.routes"
 
   provider-e2e:
@@ -59,7 +68,6 @@ jobs:
         run: |
           python -m pip install --upgrade pip
           python -m pip install -r requirements.txt
-          python -m pip install pytest pytest-asyncio
 
       - name: Run provider-backed specialist E2E
         env:
@@ -92,6 +100,9 @@ jobs:
       - name: Build
         run: npm run build
 
+      - name: Test (vitest)
+        run: npm test
+
   tauri-build:
     name: Tauri shell build (Windows)
     runs-on: windows-latest
diff --git a/frontend/package.json b/frontend/package.json
index 86b6dba..52a4787 100644
--- a/frontend/package.json
+++ b/frontend/package.json
@@ -8,7 +8,8 @@
     "build": "tsc && vite build",
     "preview": "vite preview",
     "lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0",
-    "test": "vitest",
+    "test": "vitest run",
+    "test:watch": "vitest",
     "tauri": "tauri"
   },
   "dependencies": {
```

### Commands run (all read-only except the two edits and the commit)

- `git worktree add ... -b orchestra/001-ci-gates-repo master` (from `C:\Sythion\sythion`).
- `Get-Content` / `Select-String` checks on `ci.yml` and `package.json`.
- `python -c "import backend.main; import backend.api.routes"` from the repo root and from `backend/`.
- `npm ci` and `npm test` / `npm run lint` / `npm run build` in `frontend/`.
- A guard probe (`C:\Users\orcha\AppData\Local\Temp\opencode\guard_probe.py`, outside the
  repository) that extracts the guard step's `run:` block from the YAML and executes it.
- Full backend suite runs under the pinned toolchain and under the local toolchain.

### Files I touched outside the two named ones

**None inside the repository.** The commit contains exactly the two files.

The verification created these **gitignored, worktree-local** artefacts (none is tracked,
none is in the diff, `git status --short --untracked-files=all` is empty):

```
!! frontend/node_modules/     (npm ci)
!! frontend/dist/             (npm run build)
!! __pycache__/  !! backend/**/__pycache__/  !! .pytest_cache/   (pytest)
```

The throwaway venv and all helper scripts live outside the repository, in
`C:\Users\orcha\AppData\Local\Temp\opencode\`.

I did **not** touch `docs/TESTING.md`, `Makefile`, `backend/requirements.txt`,
`backend/**`, `follow-ups.md`, or any file under `src-tauri/`, `build/`, `scripts/`.

## Evidence

### 0. The branch and the two-file diff (criterion 5)

```
PS> git diff --name-only master...HEAD
.github/workflows/ci.yml
frontend/package.json

PS> git status --short --untracked-files=all
(empty)

PS> git log --oneline master..HEAD
9ef7153 ci: make the pipeline actually gate this repository
```

`master...HEAD` is a three-dot diff, so the orchestrator's own later commit on `master`
(`163adab chore(orchestra): items 001/002/004 to building`) does not enter the diff.

### 1. The plan's five-part reproduction, master vs branch

Script: `C:\Users\orcha\AppData\Local\Temp\opencode\repro.ps1` (outside the repository).

```
############ REPRODUCTION: MASTER (d2a24e5 + orchestrator commits) ############
--- file: C:\Sythion\sythion\.github\workflows\ci.yml ---

[R1] trigger
  line 5: branches: [main]
  workflow_dispatch trigger key matches: 0

[R2] import gate working directory
  line 12: defaults:
  line 14: working-directory: backend
  line 46: defaults:
  line 48: working-directory: backend
  line 75: defaults:
  line 77: working-directory: frontend
  line 115: working-directory: frontend

[R3] vitest
  (no match - the vitest suite is never executed)

[R4] Rule P (the guard's rule and criterion 4's check, verbatim)
  OFFENDER: python -m pip install pytest pytest-asyncio
  OFFENDER: python -m pip install pytest pytest-asyncio
  line 28
  line 62

############ REPRODUCTION: BRANCH orchestra/001-ci-gates-repo (9ef7153) ############
--- file: C:\Users\orcha\AppData\Local\Temp\opencode\wt-001\.github\workflows\ci.yml ---

[R1] trigger
  line 5: branches: [master, main]
  workflow_dispatch trigger key matches: 1
  line 7: workflow_dispatch:

[R2] import gate working directory
  line 24: working-directory: ${{ github.workspace }}
  line 30: working-directory: backend
  line 36: working-directory: backend
  line 44: working-directory: ${{ github.workspace }}
  line 55: defaults:
  line 57: working-directory: backend
  line 83: defaults:
  line 85: working-directory: frontend
  line 126: working-directory: frontend

[R3] vitest
  line 103: - name: Test (vitest)
  line 104: run: npm test

[R4] Rule P (the guard's rule and criterion 4's check, verbatim)
  (no output - zero offenders)
```

On master, Rule P finds exactly the two offenders the plan named: `ci.yml:28` and
`ci.yml:62`. On the branch: zero.

### 2. The trigger (criterion 1)

```
PS> git branch -a
+ master
* orchestra/001-ci-gates-repo
+ orchestra/004-real-assertions
  remotes/origin/HEAD -> origin/master
  remotes/origin/master
```

There is still **no `main` branch** (local or remote) - `orchestra/004-real-assertions` is the
concurrent implementer's branch, created after the plan was written; it is not a branch the
workflow would need to trigger on.

```
PS> Select-String -Path .github/workflows/ci.yml -Pattern 'branches: \['
line 5:     branches: [master, main]

PS> Select-String -Path .github/workflows/ci.yml -Pattern '^\s*workflow_dispatch:\s*$'
line 7:   workflow_dispatch:
MATCH_COUNT=1

PS> Select-String -Path .github/workflows/ci.yml -Pattern 'workflow_dispatch'
line 7:   workflow_dispatch:
line 52:     if: github.event_name == 'workflow_dispatch' &&
```

The `workflow_dispatch` **trigger key** appears exactly once. The other occurrence is at
`ci.yml:52`, inside the `provider-e2e` job's `if:` condition, exactly where the plan says it
must stay.

The workflow file is valid YAML and declares the three intended triggers:

```
PS> python -c "import yaml,pprint; d=yaml.safe_load(open(r'.github/workflows/ci.yml',encoding='utf-8')); print('top-level keys:', list(d.keys())); pprint.pprint(d[True]); print('jobs:', list(d['jobs'].keys()))"
top-level keys: ['name', True, 'jobs']
{'pull_request': None,
 'push': {'branches': ['master', 'main']},
 'workflow_dispatch': None}
jobs: ['backend-tests', 'provider-e2e', 'frontend', 'tauri-build']

PS> python -c "import yaml; d=yaml.safe_load(open(r'.github/workflows/ci.yml',encoding='utf-8')); [print(j, '->', [(s.get('name') or s.get('uses'), s.get('working-directory')) for s in v['steps']]) for j,v in d['jobs'].items()]"
backend-tests -> [('actions/checkout@v4', None), ('Set up Python', None), ('Guard (test tooling install is pinned)', '${{ github.workspace }}'), ('Install dependencies', 'backend'), ('Run backend test suite', 'backend'), ('Import gate (packaged entrypoint compiles)', '${{ github.workspace }}')]
provider-e2e -> [('actions/checkout@v4', None), ('Set up Python', None), ('Install dependencies', None), ('Run provider-backed specialist E2E', None)]
frontend -> [('actions/checkout@v4', None), ('Set up Node', None), ('Install dependencies', None), ('Lint', None), ('Build', None), ('Test (vitest)', None)]
tauri-build -> [('actions/checkout@v4', None), ('Set up Node', None), ('Install Rust toolchain', None), ('Cache Cargo', None), ('Install frontend dependencies', 'frontend'), ('Build the desktop shell (compile only, no bundle)', None), ('Assert the shell binary was produced', None)]
```

(The `True` key is PyYAML's YAML-1.1 reading of the key `on`; GitHub Actions reads it as
the string `on`. The parsed value is the three-trigger mapping shown above.)

### 3. The import gate (criterion 2)

```
PS> # positive control - from the repo root (the directory the step declares)
PS> python -c "import backend.main; import backend.api.routes"; "EXIT=$LASTEXITCODE"
EXIT=0

PS> # negative control - from backend/ (today's ci.yml:12-14 + :35-36 behaviour)
PS> Push-Location backend
PS> python -c "import backend.main; import backend.api.routes"; "EXIT=$LASTEXITCODE"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    import backend.main; import backend.api.routes
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'backend'
EXIT=1
PS> Pop-Location
```

The gate is directory-sensitive, not accidentally green: the identical command is exit 0 from
the repository root and exit 1 from `backend/`.

The step declares the repository root explicitly and the job no longer has a job-level
`defaults.run.working-directory` that could silently apply to it:

```
PS> Select-String -Path .github/workflows/ci.yml -Pattern 'github.workspace'
line 24:         working-directory: ${{ github.workspace }}
line 44:         working-directory: ${{ github.workspace }}

PS> Select-String -Path .github/workflows/ci.yml -Pattern 'defaults:'
line 55:     defaults:
line 83:     defaults:
```

The two remaining `defaults:` blocks are the `provider-e2e` job (line 55, byte-identical to
master) and the `frontend` job (line 83, byte-identical to master, and correct: `npm test`
must run in `frontend/`). The `backend-tests` job has none.

The command on `ci.yml:45` is byte-identical to master's `ci.yml:36`. It appears in the
diff as an **unchanged context line** (leading space), next to the `working-directory:` line
that was added above it:

```
PS> git diff master...HEAD -- .github/workflows/ci.yml | Select-String -Pattern 'import gate|import backend|working-directory' | ForEach-Object { $_.Line }
-        working-directory: backend
+        working-directory: ${{ github.workspace }}
+        working-directory: backend
+        working-directory: backend
       - name: Import gate (packaged entrypoint compiles)
+        working-directory: ${{ github.workspace }}
         run: python -c "import backend.main; import backend.api.routes"

PS> # byte-for-byte comparison of the two command lines
master: [        run: python -c "import backend.main; import backend.api.routes"]
branch: [        run: python -c "import backend.main; import backend.api.routes"]
IDENTICAL=True
```

### 4. The pin guard (criterion 4, and the plan's risk 5)

The guard's `run:` block is **byte-identical** to the plan's snippet
(`plans/001.md:497-498`):

```
PS> python -c "<compare plan snippet with the YAML-parsed run block>"
plan snippet line 1 == "$bad = Get-Content .github/workflows/ci.yml | Where-Object { $_.Trim() -cmatch '^(pip install|python -m pip install)' -and $_ -cmatch 'pytest' -and $_ -cnotmatch '==' }"
plan snippet line 2 == 'if ($bad) { Write-Error ("unpinned pytest install: " + ($bad -join \' | \')); exit 1 }'
file run block        == '$bad = Get-Content .github/workflows/ci.yml | Where-Object { $_.Trim() -cmatch \'^(pip install|python -m pip install)\' -and $_ -cmatch \'pytest\' -and $_ -cnotmatch \'==\' }\nif ($bad) { Write-Error ("unpinned pytest install: " + ($bad -join \' | \')); exit 1 }\n'
BYTE-IDENTICAL       = True
sha256 plan  = 4e28f0609b33c9baed3085dc17a4840b7ceebdd24d1923d5655b6ce2f7eed986
sha256 file  = 4e28f0609b33c9baed3085dc17a4840b7ceebdd24d1923d5655b6ce2f7eed986
```

**The guard cannot flag itself.** Neither of its own source lines, after stripping leading
whitespace, begins with a pip-install prefix:

```
PS> foreach ($l in $guard) { "pip-prefix match = " + ($l.Trim() -cmatch '^(pip install|python -m pip install)') }
pip-prefix match = False
pip-prefix match = False
```

**The guard is a no-op on the merged file (exit 0) and exits 1 against master**, naming the
two offending lines. The `run:` block was extracted verbatim from the YAML
(`guard_probe.py`, outside the repository) and executed as a PowerShell script, which is how
a `run:` block is executed on `windows-latest`:

```
=== RUN 1: guard against the BRANCH file ===
=== extracted guard run: block (verbatim, 254 chars) ===
$bad = Get-Content .github/workflows/ci.yml | Where-Object { $_.Trim() -cmatch '^(pip install|python -m pip install)' -and $_ -cmatch 'pytest' -and $_ -cnotmatch '==' }
if ($bad) { Write-Error ("unpinned pytest install: " + ($bad -join ' | ')); exit 1 }
=== end extracted block ===
guard step keys: ['name', 'run', 'shell', 'working-directory']
guard shell: pwsh
guard working-directory: ${{ github.workspace }}
=== executing guard with cwd=C:\Users\orcha\AppData\Local\Temp\opencode\wt-001 (shell=powershell) ===
--- stdout ---
--- stderr ---
GUARD_EXIT=0
```

```
=== RUN 2: guard against MASTER's checkout ===
=== executing guard with cwd=C:\Users\orcha\AppData\Local\Temp\opencode\masterrepo (shell=powershell) ===
--- stdout ---
--- stderr ---
C:\Users\orcha\AppData\Local\Temp\opencode\_guard_probe.ps1 : unpinned pytest install:           python -m pip install pytest pytest-asyncio |           python -m pip install pytest pytest-asyncio
    + CategoryInfo          : NotSpecified: (:) [Write-Error], WriteErrorException
    + FullyQualifiedErrorId : Microsoft.PowerShell.Commands.WriteErrorException,_guard_probe.ps1

GUARD_EXIT=1
```

and the two lines it names are master's `ci.yml:28` and `ci.yml:62`:

```
PS> Select-String -Path <master ci.yml> -Pattern '^\s*(pip install|python -m pip install)' | Where-Object { $_.Line -cmatch 'pytest' -and $_.Line -cnotmatch '==' } | ForEach-Object { "line $($_.LineNumber): $($_.Line)" }
line 28:           python -m pip install pytest pytest-asyncio
line 62:           python -m pip install pytest pytest-asyncio
```

**Why the loose pattern from the redone attempt 1 is unusable** (the plan's "Corrections
from attempt 1", Defect 1) - it matches the guard's own line:

```
PS> Select-String -Path .github/workflows/ci.yml -Pattern 'pip install.*pytest'
line 26: $bad = Get-Content .github/workflows/ci.yml | Where-Object { $_.Trim() -cmatch '^(pip install|python -m pip install)' -and $_ -cmatch 'pytest' -and $_ -cnotmatch '==' }

PS> Get-Content .github/workflows/ci.yml | Where-Object { $_.Trim() -cmatch '^(pip install|python -m pip install)' -and $_ -cmatch 'pytest' -and $_ -cnotmatch '==' }
(no output - zero offenders)
```

I used **Rule P** verbatim for both the guard and criterion 4's check, and changed neither.
The plan's self-consistency checks therefore still hold: the guard is a no-op on the merged
file (exit 0) and exits 1 against master.

### 5. The pinned pytest (criterion 4, checks A/B/C)

**Check A - the file, by Rule P:**

```
PS> Get-Content .github/workflows/ci.yml | Where-Object { $_.Trim() -cmatch '^(pip install|python -m pip install)' -and $_ -cmatch 'pytest' -and $_ -cnotmatch '==' }
(no output - zero offenders)
```

**Check B - the declaration** (`backend/requirements.txt` read only, not edited):

```
PS> Select-String -Path backend\requirements.txt -Pattern '^\s*pytest' | ForEach-Object { "line $($_.LineNumber): $($_.Line)" }
line 43: pytest==7.4.4
line 44: pytest-asyncio==0.23.3
line 45: pytest-cov==4.1.0

PS> Select-String -Path .github/workflows/ci.yml -Pattern 'pip install' | ForEach-Object { "line $($_.LineNumber): $($_.Line.Trim())" }
line 26: $bad = Get-Content .github/workflows/ci.yml | Where-Object { $_.Trim() -cmatch '^(pip install|python -m pip install)' -and $_ -cmatch 'pytest' -and $_ -cnotmatch '==' }
line 32: python -m pip install --upgrade pip
line 33: python -m pip install -r requirements.txt
line 69: python -m pip install --upgrade pip
line 70: python -m pip install -r requirements.txt

PS> Select-String -Path .github/workflows/ci.yml -Pattern 'requirements\.txt' | ForEach-Object { "line $($_.LineNumber): $($_.Line.Trim())" }
line 33: python -m pip install -r requirements.txt
line 70: python -m pip install -r requirements.txt
```

`backend/requirements.txt` is now the single source of truth for the test tooling in both
jobs. (Line 26 is the guard's own source; it is not a pip-install line under Rule P.)

**Check C - the version that lands** (throwaway `--system-site-packages` venv, only the
pinned test tooling installed - the environment the CI job's
`python -m pip install -r requirements.txt` produces for the test tooling):

```
=== installing pinned test tooling ===
Using cached pytest-7.4.4-py3-none-any.whl (325 kB)
Using cached pytest_asyncio-0.23.3-py3-none-any.whl (17 kB)
Successfully installed pytest-7.4.4 pytest-asyncio-0.23.3
=== versions ===
pytest 7.4.4
pytest 7.4.4
pytest-asyncio 0.23.3
```

**The version that CI installs is the declared one: `pytest==7.4.4` /
`pytest-asyncio==0.23.3`.** Three pytest versions exist across this repository
(`8.4.2` on the local machine, `7.4.4` declared in `backend/requirements.txt`, and
whatever "latest" the unpinned install used to pull onto the runner); this change removes
the third from the pipeline.

### 6. The vitest step (criterion 3)

`npm ci` in the worktree succeeds with the changed `package.json` (the lockfile records no
`scripts`, so it stays in sync):

```
PS> npm ci
added 289 packages, and audited 290 packages in 6m
EXIT=0
```

```
PS> Select-String -Path frontend/package.json -Pattern '"test:watch"' | ForEach-Object { "line $($_.LineNumber): $($_.Line)" }
line 12:     "test:watch": "vitest",
```

```
PS> npm test
> sythion-frontend@0.1.0 test
> vitest run

 RUN  v1.6.1 C:/Users/orcha/AppData/Local/Temp/opencode/wt-001/frontend

 ✓ src/lib/companionActivation.test.ts  (6 tests) 14ms
 ✓ src/lib/setupReadiness.test.ts  (8 tests) 13ms
 ✓ src/lib/shellLayout.test.ts  (5 tests) 14ms
 ✓ src/lib/observation.test.ts  (21 tests) 42ms
 ✓ src/lib/runtimeHealth.test.ts  (9 tests) 22ms

 Test Files  5 passed (5)
      Tests  49 passed (49)
   Duration  2.31s
EXIT=0
```

**Negative control - the old script is a watcher that never returns** (this is why the
script is explicitly non-watch rather than relying on `CI=true`):

```
PS> # npx vitest  ==  master's "test": "vitest"
STILL RUNNING after 25s -> watch mode confirmed (it never exits on its own)
(terminated the watcher I started)
--- tail of the watcher's stdout ---
 Test Files  5 passed (5)
      Tests  49 passed (49)

 PASS  Waiting for file changes...
       press h to show help, press q to quit
```

The rest of the `frontend` job is also green with the changed `package.json` (the CI job
runs `npm ci`, `npm run lint`, `npm run build`, then `npm test`):

```
PS> npm run lint
> eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0
LINT_EXIT=0

PS> npm run build
> tsc && vite build
✓ 193 modules transformed.
✓ built in 9.60s
BUILD_EXIT=0
```

The lockfile is untouched:

```
PS> git diff --name-only master...HEAD -- frontend/package-lock.json
(empty)

PS> python -c "import json; d=json.load(open('frontend/package-lock.json',encoding='utf-8')); print('lockfileVersion', d.get('lockfileVersion')); print('root keys:', sorted(d['packages'][''].keys())); print('has scripts:', 'scripts' in d['packages'][''])"
lockfileVersion 3
root keys: ['dependencies', 'devDependencies', 'name', 'version']
has scripts: False
```

### 7. The `provider-e2e` job did not change behaviour

`Compare-Object` over the whole `provider-e2e` job block, master vs branch - the **only**
difference is the deleted unpinned install line that criterion 4 requires of the whole file:

```
=== provider-e2e job: master vs branch (Compare-Object) ===

InputObject                                           SideIndicator
-----------                                           -------------
          python -m pip install pytest pytest-asyncio <=
```

(`<=` = present in master only.) Every protected line is byte-identical:

```
=== protected lines: identical in master and branch? ===
IDENTICAL=True  [if: github.event_name == 'workflow_dispatch' &&]
IDENTICAL=True  [vars.RUN_PROVIDER_E2E == '1']
IDENTICAL=True  [environment: provider-e2e]
IDENTICAL=True  [SYTHION_RUN_PROVIDER_E2E: "1"]
IDENTICAL=True  [SYTHION_OLLAMA_API_KEY: ${{ secrets.OLLAMA_API_KEY }}]
IDENTICAL=True  [SYTHION_SELECTED_MODEL: ${{ vars.SYTHION_SELECTED_MODEL }}]
IDENTICAL=True  [run: python -m pytest tests/test_phase975_provider_e2e.py -q -m provider_e2e]
```

The job is still unreachable by a push or a pull request: its condition requires
`github.event_name == 'workflow_dispatch'` (a push has `event_name == 'push'`) **and**
`vars.RUN_PROVIDER_E2E == '1'`. The only consequence of adding the `workflow_dispatch:`
trigger is that the job becomes reachable by a *deliberate manual dispatch* - which is what
the job was written for. No secret was moved into the repository, and the repository
variable / environment / secret were not touched.

### 8. The full backend suite

**Pre-change baseline** (the orchestrator's figure, recorded in `goal.md` "Baseline notes"
and `reports/research-001.md`, measured in the orchestrator's tree on CPython 3.14.7 with
pytest 8.4.2): **1564 passed, 45 skipped, 1609 collected** (533 s).

All runs below are in the branch worktree, whose Python content is byte-identical to
master's (this change touches no Python file). The local interpreter is CPython 3.14.7;
CI pins 3.11.

**Run 1 - pinned toolchain (`pytest==7.4.4` / `pytest-asyncio==0.23.3`), worktree as
checked out:**

```
$ <tmp>/venv_py74/Scripts/python.exe -m pytest backend/tests -q
...
=========================== short test summary info ===========================
FAILED backend/tests/test_setup_readiness.py::test_readiness_states - assert ...
1 failed, 1563 passed, 45 skipped, 428302 warnings in 655.14s (0:10:55)
=== SUITE_EXIT=1 elapsed=669.4556872s ===
```

```
____________________________ test_readiness_states ____________________________
    def test_readiness_states(monkeypatch):
        ...
        res = client.get("/api/v1/setup/readiness")
        assert res.status_code == 200
        body = res.json()
>       assert body["full_ready"] is True
E       assert False is True
backend\tests\test_setup_readiness.py:160: AssertionError
```

**This failure is an artefact of the worktree, not of the change or of the pin** - see
Finding 3 for the full proof (the gitignored `backend/.env` is absent in a fresh
worktree, so the readiness endpoint's credential check is False).

**Run 2 - the same pinned toolchain, with the environment the baseline was measured in
restored** (a **dummy** `SYTHION_OLLAMA_API_KEY` in the process env, standing in for the
gitignored `backend/.env`; no real credential was moved anywhere):

```
########## RUN A: pinned toolchain (pytest 7.4.4 / pytest-asyncio 0.23.3) ##########
pytest 7.4.4
1 failed, 1563 passed, 45 skipped, 428298 warnings in 520.48s (0:08:40)
=== RUN A SUITE_EXIT=1 elapsed=529.8679642s ===
```

The readiness test now passes; the single failure is a **different** test:

```
_________________ test_focus_brings_fixture_window_foreground _________________
    @pytest.mark.asyncio
    async def test_focus_brings_fixture_window_foreground(fixture_window):
        tool = GUIFocusTool()
        out = await tool.execute(GUIFocusInput(window_title=FIXTURE_TITLE))
>       assert out.success is True
E       assert False is True
E        +  where False = ToolOutput(success=False, data=None, model_summary=None, error={'code': 'TARGET_NOT_VISIBLE', 'message': "Window 'Sythion (Synth) GUI Fixture' is not visible"}, evidence=None, duration_ms=0).success
backend\tests\acceptance\test_gui_fixture.py:133: AssertionError
```

**Run 3 - the identical tree under the LOCAL toolchain (system pytest 8.4.2), same
environment:**

```
########## RUN B: local toolchain (system pytest 8.4.2) ##########
pytest 8.4.2
1564 passed, 45 skipped, 407754 warnings in 552.06s (0:09:12)
=== RUN B SUITE_EXIT=0 elapsed=564.1287732s ===
```

**1564 passed / 45 skipped / 1609 collected - identical to the pre-change baseline, exit 0.**

**Isolation control for the GUI test** (worktree, `backend/tests/acceptance/test_gui_fixture.py`):

```
=== PINNED 7.4.4  attempt 1 ===  4 passed, 297 warnings in 9.14s   EXIT=0
=== PINNED 7.4.4  attempt 2 ===  4 passed, 297 warnings in 8.97s   EXIT=0
=== PINNED 7.4.4  attempt 3 ===  4 passed, 297 warnings in 9.03s   EXIT=0
=== LOCAL  8.4.2  attempt 1 ===  4 passed, 305 warnings in 9.25s   EXIT=0
=== LOCAL  8.4.2  attempt 2 ===  4 passed, 305 warnings in 9.40s   EXIT=0
=== LOCAL  8.4.2  attempt 3 ===  4 passed, 305 warnings in 9.16s   EXIT=0
```

**Run 4 - the pinned toolchain again, to see whether the GUI failure repeats:**

```
########## RUN A2: pinned toolchain (pytest 7.4.4), re-run of the full suite ##########
pytest 7.4.4
1564 passed, 45 skipped, 428304 warnings in 549.22s (0:09:09)
=== RUN A2 SUITE_EXIT=0 elapsed=557.6611084s ===
=== DONE A2=0 ===
```

**The GUI failure did not repeat: 1564 passed / 45 skipped / 1609 collected, exit 0 -
identical to the pre-change baseline, under the pinned toolchain.**

**Collected count under the pinned toolchain (independent check):**

```
$ <tmp>/venv_py74/Scripts/python.exe -m pytest backend/tests --collect-only -q
1609 tests collected in 113.05s (0:01:53)
COLLECT_EXIT=0
```

**Summary of the differences from the pre-change baseline - reported, not smoothed over:**

| Run | Toolchain | Env | Result | vs baseline |
|---|---|---|---|---|
| baseline (orchestrator, orchestrator's tree) | pytest 8.4.2 | `backend/.env` present | 1564 passed / 45 skipped / 1609 collected, exit 0 | - |
| 1 | pytest 7.4.4 | worktree, no `.env` | 1 failed / 1563 passed / 45 skipped, exit 1 | 1 failure: the readiness test, caused by the missing gitignored `.env` (Finding 3) |
| 2 | pytest 7.4.4 | worktree + dummy key | 1 failed / 1563 passed / 45 skipped, exit 1 | 1 failure: a **different** test, the GUI-visibility one |
| 3 | pytest 8.4.2 | worktree + dummy key | 1564 passed / 45 skipped / 1609 collected, exit 0 | **identical** |
| 4 | pytest 7.4.4 | worktree + dummy key | 1564 passed / 45 skipped / 1609 collected, exit 0 | **identical** |

**The collected count is 1609 in every run** (passed + failed + skipped, and confirmed
directly by `--collect-only` under the pinned toolchain) - the pin changes *what* the suite
exercises, not *how much* it collects, which is what the plan predicted.

**The known COM hazard did not materialise.** Neither the orchestrator nor I observed
`Windows fatal exception: code 0x80040155` in any of these runs; all four completed and
printed their summary line. The GUI-visibility failure in run 2 is a different, milder
flakiness (a window the UIA tree reports as not visible), it did not reproduce in run 4,
and it did not reproduce when the test file was run in isolation under either toolchain
(3/3 each).

**No difference is attributable to this change**: the change adds no Python code, edits no
Python file, and the only Python-visible effect it can have is the pytest version CI
installs - and the pinned toolchain runs the same 1609 tests and passes 1564 of them
(exactly the baseline) once the environment artefact is removed.

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | `on.push.branches` contains `master`; the workflow can also be started by hand (`workflow_dispatch` as a trigger key) | **PASS** (file level) | `ci.yml:5` = `branches: [master, main]`; `ci.yml:7` = `workflow_dispatch:`; the anchored trigger-key pattern matches exactly 1 time (0 on master). See the caveat in Findings about when the trigger becomes usable. |
| 2 | The import gate's exact command, run from the directory it declares, exits 0 (and exits 1 from `backend/`) | **PASS** | `python -c "import backend.main; import backend.api.routes"` from the repo root -> `EXIT=0`; from `backend/` -> `EXIT=1`, `ModuleNotFoundError: No module named 'backend'`. `ci.yml:44` declares `working-directory: ${{ github.workspace }}`; the `backend-tests` job has no job-level `defaults:`. The command line is byte-identical to master's (`IDENTICAL=True`) and appears in the diff only as an unchanged context line. |
| 3 | The `frontend` job runs the vitest suite in non-watch mode; `package.json` maps a non-interactive script to it | **PASS** | `ci.yml:103-104` = `- name: Test (vitest)` / `run: npm test`; `frontend/package.json:11` = `"test": "vitest run"`, `:12` = `"test:watch": "vitest"`. `npm test` -> `Test Files 5 passed (5)` / `Tests 49 passed (49)`, `EXIT=0`. Negative control: bare `vitest` hangs at "Waiting for file changes...". `npm ci` -> exit 0, lockfile untouched. |
| 4 | No `pip install` line in `ci.yml` installs `pytest`/`pytest-asyncio` without a version constraint (Rule P); the version CI installs is the one pinned in `backend/requirements.txt` | **PASS** | Check A: Rule P -> zero offenders (master: two, at `ci.yml:28` and `ci.yml:62`). Check B: `backend/requirements.txt:43-44` = `pytest==7.4.4` / `pytest-asyncio==0.23.3`; the workflow's only test-tooling install is `python -m pip install -r requirements.txt` (lines 33 and 70). Check C: pinned venv -> `pytest 7.4.4` / `pytest-asyncio 0.23.3`; the full suite under the pinned toolchain -> `1564 passed, 45 skipped, 1609 collected`, exit 0 - **identical to the pre-change baseline**; `--collect-only` under the pinned toolchain -> `1609 tests collected`. |
| 5 | `git diff --name-only master...<branch>` lists only `.github/workflows/ci.yml` and `frontend/package.json` | **PASS** | Exactly those two lines; `git status --short --untracked-files=all` is empty; the commit's stat is `2 files changed, 19 insertions(+), 7 deletions(-)`. No new file, no `requirements.txt` edit, no `Makefile` edit, no `docs/TESTING.md` edit, no runtime file. |
| 6 | The outside observation is recorded as a follow-up, not performed here | **NOT MINE TO DO - the criterion assigns it to the orchestrator** | Criterion 5 forbids me from touching `follow-ups.md`, and the plan says the entry is written by the orchestrator at merge time. I did **not** run or dispatch the workflow. The exact line the orchestrator should add to `.orchestra/follow-ups.md` is in Findings below. |

## Findings

1. **The plan's Defect 1 was real, and the corrected rule is what I implemented.** The
   loose `Select-String 'pip install.*pytest'` pattern that attempt 1 used for criterion 4
   *does* match the guard's own source line in the merged file (evidence above, line 26).
   I used Rule P verbatim for both the guard and criterion 4's check and changed neither;
   the guard's `run:` block is byte-identical to the plan's snippet (sha256 above), so the
   plan's self-consistency checks still hold: the guard is a no-op on the merged file
   (exit 0) and exits 1 against master, naming `ci.yml:28` and `ci.yml:62`.

2. **`workflow_dispatch` only becomes *usable* after the merge.** The trigger key is
   present in the file (criterion 1 is met at the file level), but GitHub only offers the
   manual dispatch button for a workflow that exists on the **default branch**. So the
   "can be started by hand" half of criterion 1 cannot be exercised until this branch is
   merged to `master`. This is the plan's own stated position (plan "Risk" 1 and criterion
   6) and is why the observation is a follow-up, not an in-repo check.

3. **A full-suite run in a fresh worktree shows one failure - and it is NOT caused by
   this change.** `backend/tests/test_setup_readiness.py::test_readiness_states` asserts
   `body["full_ready"] is True`; `full_ready = all(checks.values())` and
   `key_ok = has_secret(OLLAMA_API_KEY) or bool(settings.ollama_api_key)`
   (`backend/api/routes.py:2261,2290`). `settings.ollama_api_key` is loaded from
   `backend/.env` (`backend/core/config.py:173-176`, `env_prefix = "SYTHION_"`), and
   **`backend/.env` is gitignored** (`.gitignore:16`) - it exists in the orchestrator's
   tree but not in a fresh worktree checkout. Proof:
   - worktree, no key: `1 failed, 7 passed`;
   - worktree, a **dummy** `SYTHION_OLLAMA_API_KEY` in the process env: `8 passed`, exit 0;
   - the orchestrator's tree (which has the real `backend/.env`): `8 passed`, exit 0.
   The failing check is therefore the *credential* check, not a code or toolchain
   regression. My change touches no Python file at all, so it cannot cause this. With the
   environment restored, the pinned toolchain reproduces the baseline exactly
   (`1564 passed, 45 skipped, 1609 collected`, exit 0) - see section 8.

4. **The `provider-e2e` job's gate is unchanged and it is still unreachable by push or
   pull request** (evidence in section 7). Adding `workflow_dispatch` is what makes that
   job reachable at all - by hand only, and only if a human has set
   `vars.RUN_PROVIDER_E2E == '1'`. **The Reviewer/Verifier should confirm that the
   repository variable `RUN_PROVIDER_E2E` is not already set to `'1'`** before anyone
   dispatches the workflow by hand (the plan's Open question 1; not readable from the
   checkout). I did not dispatch the workflow and did not touch the variable, the
   environment, or the secret.

5. **The `provider-e2e` job's `python -m pip install -r requirements.txt` will install the
   declared `pytest==7.4.4`** now that the unpinned override is gone. The `provider_e2e`
   tests are skipped unless `SYTHION_RUN_PROVIDER_E2E=1` (the job sets it), so the
   credentialed path is the only one that exercises the provider - and it now runs the
   declared pytest, like every other job.

6. **Pre-existing conditions of a pipeline that has never run** (the plan's risk 6, all
   unchanged by this item and all outside its scope):
   - `backend/requirements.txt` pins `whispercpp==0.0.17`, which has **no wheel** for
     cp311 (or cp314) and must be source-built on the runner. The `Install dependencies`
     step may therefore fail on the first real run, on a package this item does not touch
     (criterion 5 forbids touching `requirements.txt`).
   - The `tauri-build` job may need a toolchain the runner lacks.
   - `actions/setup-python@v5` with `cache: pip` and no `cache-dependency-path` globs
     `**/requirements.txt`; this checkout contains several such files
     (`backend/requirements.txt`, `src-tauri/resources/backend/requirements.txt`,
     `build/staging/whisper-src/.../requirements.txt`). I did **not** exercise the
     action, so I cannot say from here whether the cache step behaves as expected. The
     `Set up Python` step (including `cache: pip`) is byte-identical to master - it
     appears only as context in the diff - so this is not a regression.

7. **Two `requirements.txt` files exist in the checkout** and neither `python -m pip
   install -r requirements.txt` nor the `cache: pip` glob was changed by me; the job's
   working directory is `backend/`, so `-r requirements.txt` resolves to
   `backend/requirements.txt` exactly as on master.

8. **The plan's "one or two commits" became one commit, deliberately.** A two-commit split
   (ci.yml first, package.json second) would have left an intermediate commit whose
   `frontend` job runs `npm test` while `package.json` still maps `test` to the watcher -
   i.e. a commit where the pipeline hangs. One commit keeps every reachable state of the
   branch correct.

9. **Four full-suite runs, all recorded; the differences are reported, not smoothed over.**
   Run 1 (pinned toolchain, worktree as checked out) -> `1 failed, 1563 passed, 45 skipped`
   - the failure is the `backend/.env` artefact of finding 3, not a code or toolchain
   regression. Run 2 (pinned, environment restored) -> `1 failed, 1563 passed, 45 skipped`,
   a **different** failure: the GUI-visibility test. Run 3 (local toolchain 8.4.2, same
   environment) -> `1564 passed, 45 skipped`, exit 0, exactly the baseline. Run 4 (pinned
   again) -> `1564 passed, 45 skipped`, exit 0, exactly the baseline. The GUI test passes
   3/3 in isolation under each toolchain, so run 2's failure is environment flakiness in
   the full-suite context, not a pin incompatibility. **The suite did not die with the
   known `Windows fatal exception: code 0x80040155` COM fault in any of the four runs**,
   so that hazard did not materialise here; had it, I would have re-run and recorded both
   attempts, as the brief asks.

10. **The exact follow-up line the orchestrator should add** to
    `.orchestra/follow-ups.md` at merge time (format: `- [ ] (item-id) [deferred|required] what to do`),
    per criterion 6:

    ```
    - [ ] (001) [deferred] Observe the repaired CI workflow on GitHub after the merge: a push to `master` must start the `backend-tests`, `frontend` and `tauri-build` jobs, and a manual dispatch must be able to start the `provider-e2e` job (only if `vars.RUN_PROVIDER_E2E` is set to `1` - confirm it is unset before dispatching). Expect the first `Install dependencies` run to source-build `whispercpp==0.0.17`, which has no cp311 wheel.
    ```

    I did not write that file: criterion 5 and the plan both put `follow-ups.md` in the
    orchestrator's hands at merge time, so that the branch diff stays limited to the two
    files the acceptance criteria name.

11. **Nothing was pushed, deployed, published, dispatched or deleted.** No `rm -rf`,
    `git reset --hard`, `git clean -fd`, `git checkout -- .`, `git stash drop`, no
    `make clean`, no `scripts/stage_python_runtime.py`, no migrations, no
    `npx tauri build`. The only processes I terminated were the vitest watcher I started
    myself, in my own worktree.

## Not checked

- **I did not run the workflow on GitHub, and did not dispatch it.** `.github/workflows/**`
  is an external path; running the pipeline is outside the repository and is forbidden by
  the brief. Consequently the following are **not** established by this report: that a
  push to `master` actually starts the workflow; that `workflow_dispatch` appears in the
  UI; that the runner-side `python -m pip install -r requirements.txt` succeeds (it must
  source-build `whispercpp==0.0.17`); that the suite is green on Python 3.11 (all local
  runs are on CPython 3.14.7); that the `tauri-build` job is green; and that the
  `setup-python` pip cache step behaves with the several `requirements.txt` files in the
  checkout. All of these belong to the follow-up that criterion 6 requires.
- **I did not verify the GitHub repository settings** - whether `vars.RUN_PROVIDER_E2E` is
  already set to `'1'`, whether the `provider-e2e` environment exists, or whether the
  `OLLAMA_API_KEY` secret exists. They are not readable from the checkout. See finding 4.
- **I did not check `actionlint` / a GitHub Actions schema validator** - the workflow was
  validated by parsing it with PyYAML and by reading back every step's keys, but no
  GitHub-side schema validation was performed, and `shell: pwsh` could not be exercised
  locally (PowerShell 7 is not installed on this machine; the guard was executed under
  Windows PowerShell 5.1, which implements the same `Get-Content` / `Where-Object` /
  `-cmatch` / `-cnotmatch` / `Write-Error` / `exit` semantics).
- **I did not build the Tauri shell, the release payload or the installer**, and did not
  touch `backend/sythion.db` or `%LOCALAPPDATA%\Sythion (Synth)`.
- **I did not run the acceptance suite separately** (`backend/tests/acceptance`); it is
  part of `backend/tests` and is included in the full-suite counts.
- **I did not re-run the R5 two-file check at the exact moment the branch was created.**
  The orchestrator committed `163adab chore(orchestra): items 001/002/004 to building` to
  `master` while I was working, so the `master` ref my worktree sees moved during the run.
  The check is a three-dot diff (`master...HEAD`), so it is unaffected by commits that land
  on `master`; the output quoted in the Evidence section was taken after that commit
  existed, and it lists exactly the two files.
- **I did not verify the documentation** that item 003 owns (`docs/TESTING.md` still
  describes CI as running "on push/PR to `main`" and does not list the new vitest step or
  the guard). That is item 003's file by design, but a Reviewer should know the
  documentation is currently stale with respect to this change.
