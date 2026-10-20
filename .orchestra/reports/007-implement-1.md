# Report: implement - 007 - attempt 1

Item **007 - The acceptance suite must never write to or delete the product's
real credential** (candidate R-020).
Branch **`orchestra/007-no-real-vault-writes`**, worktree
**`C:\Users\orcha\AppData\Local\Temp\opencode\wt-007`**.

This is the **recovery dispatch** of the round that was interrupted by a network
fault after the work was committed (`a0fa314`, `e1ff9b1`) but before the report
was written. The orchestrator had already read the committed diff and judged it
good. My job was to apply the **one** correction it found (a false claim in a
code comment), gather the proof the plan requires, and write this report.

---

## What I did

### Commits

| sha | message | author of the change |
|---|---|---|
| `a0fa314` | `test(acceptance): never write to or delete the product credential target` | round 1 (committed before the interruption) |
| `e1ff9b1` | `test(acceptance): enumerate the vault unfiltered in the round-trip self-check` | round 1 (committed before the interruption) |
| **`4680e0b`** | `test(acceptance): correct the enumeration-form claim in the round-trip self-check` | **this round - the one correction** |

`4680e0b` is a **comment-only** change: 8 insertions, 6 deletions, all inside a
`#` comment block in
`backend/tests/acceptance/test_secret_storage.py:186-193`. **No behaviour
changed** - the self-check still enumerates unfiltered and filters in Python.

```
$ git -C C:\Users\orcha\AppData\Local\Temp\opencode\wt-007 log --oneline -4
4680e0b test(acceptance): correct the enumeration-form claim in the round-trip self-check
e1ff9b1 test(acceptance): enumerate the vault unfiltered in the round-trip self-check
a0fa314 test(acceptance): never write to or delete the product credential target
ecb6318 chore(orchestra): planner critiques for 006/007, items planned, dispatch implementer-007

$ git -C C:\Users\orcha\AppData\Local\Temp\opencode\wt-007 status --short
(nothing)
```

History was not rewritten, not rebased, not amended. Nothing was pushed.

### Files I touched

| file | what |
|---|---|
| `backend/tests/acceptance/test_secret_storage.py` | the comment correction (committed as `4680e0b`) - **the only file changed in the branch by this round** |
| `C:\Sythion\sythion\.orchestra\reports\007-implement-1.md` | this report (the deliverable, written into the orchestrator's tree, **not** into the branch) |

I touched **nothing else**. In particular I did not touch
`backend/core/secrets.py`, `backend/api/routes.py`, the root
`backend/tests/conftest.py`, `docs/ARCHITECTURE.md` or
`.orchestra/follow-ups.md`.

### Scratch trees and harnesses (all OUTSIDE the repository)

```
# a detached scratch worktree of master, for the before/after comparison.
# It is NOT a branch, so no branch can be double-checked-out.
$ git -C C:\Sythion\sythion worktree add --detach C:\Users\orcha\AppData\Local\Temp\opencode\wt007-master master
Preparing worktree (detached HEAD 7cf679e)
HEAD is now at 7cf679e chore(orchestra): reviewer-002 IMPROVE (3 findings confirmed), item 002 back to implementer round 1

# the throwaway harnesses (the plan requires them OUTSIDE the repository)
C:\Users\orcha\AppData\Local\Temp\opencode\wt007-harness\
    vault_probe.py        # the plan's fake-vault recorder + judge
    vault_spy.py          # records writes/deletes, NEVER delegates (real vault unreachable)
    real_vault_tripwire.py# delegates to the real win32cred, then enumerates the REAL vault
    product_blocker.py    # blocks product-prefixed writes/deletes, delegates everything else
    read_only_enumerate.py# read-only CredEnumerate observations
    compare_junit.py      # per-test outcome-set comparison of two --junitxml files
```

### Vault discipline (the constraint that mattered most)

* I **never** wrote to, or deleted from, the product target
  `Sythion (Synth)/ollama_api_key`. Every run that could have touched it was
  executed against a stand-in (`vault_probe.py`'s in-process dictionary,
  `vault_spy.py`'s non-delegating recorders, or `product_blocker.py`, which
  blocks the product prefix before delegating). Where a real-vault run was
  needed (the branch's own test), the target is the **test-scoped**
  `Sythion (Synth) [TEST]/ollama_api_key`.
* I **never** printed or read a credential value. Only target **names** were
  listed; the enumerations below never touch `CredentialBlob`.
* I did **not** deliberately run master's offending test against the real vault.
  Every master-side run of it used a fake vault.
* The only real-vault writes that happened in this round were the branch's
  test-scoped target, written and deleted by the branch's own round-trip test.

### Commands (in the order they were run)

```powershell
# 1. read the brief and the plan; read the committed diff (orchestrator already had)
# 2. measure the enumerator, READ-ONLY, to check the comment's claim
cd C:\Users\orcha\AppData\Local\Temp\opencode\wt007-harness
python read_only_enumerate.py
python probe_prefix_forms.py

# 3. the correction -> commit 4680e0b (comment only)

# 4. the fake-vault probe, both trees
$env:PYTHONPATH="C:\Users\orcha\AppData\Local\Temp\opencode\wt007-harness"
cd ...\wt007-master  ; python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_probe
cd ...\wt-007       ; python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_probe

# 5. the guard's teeth: transient probe (branch and master) with vault_spy
# 6. the guard's teeth: master's own offending test file inside the branch tree
# 7. the real round trip: real_vault_tripwire, and the plain real-vault run
# 8. acceptance suite before/after with --junitxml and a per-test outcome comparison
# 9. full suite: branch (real vault) and master (with product_blocker)
# 10. final read-only enumeration; delete every probe file; git status clean
```

---

## Evidence

### E1 - The diff is exactly two test files; no production behaviour changed

```
$ git -C ...\wt-007 diff --name-only master...HEAD
backend/tests/acceptance/conftest.py
backend/tests/acceptance/test_secret_storage.py

$ git -C ...\wt-007 diff master...HEAD -- backend/core/secrets.py backend/api/routes.py
(empty - exit 0)

$ git -C ...\wt-007 diff --stat master...HEAD
 backend/tests/acceptance/conftest.py            | 72 +++++++++++++++++++++++++
 backend/tests/acceptance/test_secret_storage.py | 67 +++++++++++++++++++++--
 2 files changed, 134 insertions(+), 5 deletions(-)
```

`backend/core/secrets.py` is byte-identical to master: `_CM_TARGET_PREFIX`
(`:29`), `_cm_target` (`:32-33`), `_default_store` (`:112-121`), `set_secret`,
`delete_secret`, `has_secret`, `list_secret_names` and `resolve_ollama_api_key`
are untouched. The test reaches the test-only prefix by pinning the module
attribute **inside the test process** (`monkeypatch`), which works only because
`_cm_target()` reads `_CM_TARGET_PREFIX` at call time.

### E2 - THE FAKE-VAULT PROBE (the plan's proof): master writes the product target, the branch does not

The probe is the plan's harness: `win32cred.CredWrite / CredRead / CredDelete /
CredEnumerate` are replaced by in-process fakes backed by one dictionary, so
**no vault operation is performed at all** while every write/delete target is
recorded. The command is identical on both trees.

**On master (`7cf679e`):**

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt007-master
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_probe -p no:warnings
..........                                                               [100%]
=== VAULT PROBE RECORD (fake vault, no real vault call was made) ===
write Sythion (Synth)/ollama_api_key
delete Sythion (Synth)/ollama_api_key
=== END VAULT PROBE RECORD ===
VAULT PROBE VERDICT: FAIL
  - PRODUCT TARGET WRITE DETECTED: Sythion (Synth)/ollama_api_key, Sythion (Synth)/ollama_api_key
  - NO WRITE TO A NON-PRODUCT TARGET RECORDED

10 passed in 0.93s
MASTER_EXITCODE=1
```

**On the branch (`4680e0b`):**

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-007
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_probe -p no:warnings
..........                                                               [100%]
=== VAULT PROBE RECORD (fake vault, no real vault call was made) ===
write Sythion (Synth) [TEST]/ollama_api_key
delete Sythion (Synth) [TEST]/ollama_api_key
=== END VAULT PROBE RECORD ===
VAULT PROBE VERDICT: PASS

10 passed in 0.86s
BRANCH_EXITCODE=0
```

This is the proof the plan insists on, because a before/after enumeration
alone is **not** a proof: master writes the entry and then deletes it, so the
vault ends in the state it started in and any end-state check passes on master
too. The harness records **which target was written**, and that is what
differs.

The same probe over the **whole acceptance suite** shows the exposure is the
suite, not one test (master's run wrote and deleted the product target from the
suite as a whole):

```
$ cd ...\wt007-master ; python -m pytest backend/tests/acceptance -q -p vault_probe -p no:warnings --junitxml=...junit_master_fake.xml
191 passed, 2 skipped in 18.94s
=== VAULT PROBE RECORD (fake vault, no real vault call was made) ===
write Sythion (Synth)/ollama_api_key
delete Sythion (Synth)/ollama_api_key
=== END VAULT PROBE RECORD ===
VAULT PROBE VERDICT: FAIL
  - PRODUCT TARGET WRITE DETECTED: Sythion (Synth)/ollama_api_key, Sythion (Synth)/ollama_api_key

$ cd ...\wt-007 ; python -m pytest backend/tests/acceptance -q -p vault_probe -p no:warnings --junitxml=...junit_branch_fake.xml
191 passed, 2 skipped in 20.45s
=== VAULT PROBE RECORD (fake vault, no real vault call was made) ===
write Sythion (Synth) [TEST]/ollama_api_key
delete Sythion (Synth) [TEST]/ollama_api_key
=== END VAULT PROBE RECORD ===
VAULT PROBE VERDICT: PASS
```

(The harness's exit status is its own verdict, not pytest's - exactly as the
plan describes. `pytest` reports `10 passed` / `191 passed` in both modes
because the fake vault makes the round trip work in memory; the **harness's
printed record and exit status are the verdict**.)

### E3 - THE GUARD HAS TEETH

**The probe file** (transient, `backend/tests/acceptance/test_zz_product_target_probe_tmp.py`,
created, run, and **deleted** - it is not in the commit and `git status --short`
is empty afterwards) uses the **production store class directly**, so the
attempt really does reach `win32cred`:

```python
def test_probe_write_to_product_target():
    store = secrets_module.CredentialManagerStore()
    store.set("ollama_api_key", "probe-value")

def test_probe_delete_of_product_target():
    store = secrets_module.CredentialManagerStore()
    store.delete("ollama_api_key")

def test_probe_direct_write_to_product_target_with_patched_prefix(monkeypatch):
    """The disarm attempt: patch the prefix, then write to the PRODUCT target."""
    import win32cred
    monkeypatch.setattr(secrets_module, "_CM_TARGET_PREFIX", "Sythion (Synth) [TEST]/")
    cred = {"Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": "Sythion (Synth)/ollama_api_key", ...}
    win32cred.CredWrite(cred, 0)

def test_probe_control_patched_prefix_composes_test_scoped_target(monkeypatch):
    """Control: with the prefix patched, set() composes the TEST-scoped target."""
    monkeypatch.setattr(secrets_module, "_CM_TARGET_PREFIX", "Sythion (Synth) [TEST]/")
    secrets_module.CredentialManagerStore().set("ollama_api_key", "probe-value")
```

It was run with `vault_spy.py` installed: the spy replaces the real
`CredWrite`/`CredDelete` with recorders that **never delegate**, so the real
Windows Credential Manager was unreachable for every target in this run.

**On the branch - the guard blocks all three product-target attempts:**

```
$ cd ...\wt-007
$ python -m pytest backend/tests/acceptance/test_zz_product_target_probe_tmp.py -q -p vault_spy -p no:warnings
FFF.                                                                     [100%]
=== VAULT SPY RECORD (no real vault call was made) ===
write Sythion (Synth) [TEST]/ollama_api_key
=== END VAULT SPY RECORD ===

E           backend.tests.acceptance.conftest.ProductCredentialWriteAttempt: acceptance test backend/tests/acceptance/test_zz_product_target_probe_tmp.py::test_probe_write_to_product_target attempted to write the product credential target 'Sythion (Synth)/ollama_api_key'. Tests must never write to or delete targets under 'Sythion (Synth)/'; use a test-scoped prefix instead.

E           backend.tests.acceptance.conftest.ProductCredentialWriteAttempt: acceptance test backend/tests/acceptance/test_zz_product_target_probe_tmp.py::test_probe_delete_of_product_target attempted to delete the product credential target 'Sythion (Synth)/ollama_api_key'. ...

E           backend.tests.acceptance.conftest.ProductCredentialWriteAttempt: acceptance test backend/tests/acceptance/test_zz_product_target_probe_tmp.py::test_probe_direct_write_to_product_target_with_patched_prefix attempted to write the product credential target 'Sythion (Synth)/ollama_api_key'. ...

FAILED backend/tests/acceptance/test_zz_product_target_probe_tmp.py::test_probe_write_to_product_target
FAILED backend/tests/acceptance/test_zz_product_target_probe_tmp.py::test_probe_delete_of_product_target
FAILED backend/tests/acceptance/test_zz_product_target_probe_tmp.py::test_probe_direct_write_to_product_target_with_patched_prefix
3 failed, 1 passed in 0.56s
BRANCH_PROBE_EXITCODE=1
```

**The proof that the real vault was never reached** is the spy record: the only
entry in it is the **control** test-scoped write. The three product-target
attempts never got past `conftest.py:90` / `conftest.py:94` -
`guarded_write` / `guarded_delete` raise at `_reject(...)` **before**
`original_write(...)` / `original_delete(...)` is reached. The ordering is
visible in the committed source (`backend/tests/acceptance/conftest.py:88-95`):

```python
    def guarded_write(cred, flags=0):
        target = cred.get("TargetName", "") if hasattr(cred, "get") else ""
        _reject(target, "write")          # <-- raises here, BEFORE delegating
        return original_write(cred, flags)

    def guarded_delete(target, *args, **kwargs):
        _reject(target, "delete")         # <-- raises here, BEFORE delegating
        return original_delete(target, *args, **kwargs)
```

The third probe test is the **disarm attempt** the plan warns about (Risk 2):
a guard that read `_CM_TARGET_PREFIX` at *call* time would have compared
against the patched test prefix and let the direct product-target write
through. It did not - the committed guard compares against its **import-time
snapshot** (`conftest.py:44`, `secrets_module._CM_TARGET_PREFIX` read at import
time, not at call time), so the write was blocked.

**On master - the same probe, no guard, the attempt reaches the vault layer and
the suite stays GREEN (this is the defect, and the guard's contrast):**

```
$ cd ...\wt007-master
$ python -m pytest backend/tests/acceptance/test_zz_product_target_probe_tmp.py -q -p vault_spy -p no:warnings
....                                                                     [100%]
=== VAULT SPY RECORD (no real vault call was made) ===
write Sythion (Synth)/ollama_api_key
delete Sythion (Synth)/ollama_api_key
write Sythion (Synth)/ollama_api_key
write Sythion (Synth) [TEST]/ollama_api_key
=== END VAULT SPY RECORD ===

4 passed in 0.11s
MASTER_PROBE_EXITCODE=0
```

### E4 - The guard also fires against MASTER'S OWN offending test file

The plan's secondary reproduction: master's `test_secret_storage.py` dropped
into the branch tree (the branch's `conftest.py` guard stays in place), run with
the spy so the real vault cannot be reached even if the guard were broken.

```
$ cd ...\wt-007
$ python -c "import subprocess;d=subprocess.run(['git','show','master:backend/tests/acceptance/test_secret_storage.py'],capture_output=True).stdout;open(r'backend/tests/acceptance/test_secret_storage.py','wb').write(d)"
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_spy -p no:warnings
........F.                                                               [100%]
=== VAULT SPY RECORD (no real vault call was made) ===
(no write/delete ever reached the vault layer)
=== END VAULT SPY RECORD ===

E           backend.tests.acceptance.conftest.ProductCredentialWriteAttempt: acceptance test backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip attempted to write the product credential target 'Sythion (Synth)/ollama_api_key'. ...

FAILED backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip
1 failed, 9 passed in 1.16s
SECONDARY_REPRO_EXITCODE=1
```

Restoring the branch's copy and confirming it is byte-identical again:

```
$ Copy-Item ...\branch_test_secret_storage.py.bak backend\tests\acceptance\test_secret_storage.py
$ git diff --exit-code --quiet
DIFF_VS_HEAD_EXIT=0            # byte-identical to the committed file again
$ git status --short
?? backend/tests/acceptance/test_zz_product_target_probe_tmp.py    # only the transient probe
```

The guard is therefore not a stub that only reacts to a synthetic probe: it
reacts to master's own offending test.

### E5 - The real-vault round trip is still a REAL round trip

**Plain run against the real Windows Credential Manager** (no harness, no
plugin - the production `win32cred` API is called for real, at the test-scoped
target):

```
$ cd ...\wt-007
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p no:warnings
..........                                                               [100%]
10 passed in 0.89s
REAL_VAULT_EXITCODE=0
```

**With a tripwire that delegates to the real `win32cred` and then enumerates
the REAL, unfiltered vault** - this is the anti-mock proof: the target is
really visible in the operating system's credential store while the test holds
it, and really gone after the delete:

```
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p real_vault_tripwire -p no:warnings -s
........[REAL VAULT TRIPWIRE] CredWrite 'Sythion (Synth) [TEST]/ollama_api_key' -> present in the real Windows Credential Manager enumeration: True
[REAL VAULT TRIPWIRE] CredDelete 'Sythion (Synth) [TEST]/ollama_api_key' -> still present in the real Windows Credential Manager enumeration: False
..

=== REAL VAULT TRIPWIRE RECORD ===
write Sythion (Synth) [TEST]/ollama_api_key -> visible in real vault: True
delete Sythion (Synth) [TEST]/ollama_api_key -> still visible in real vault: False
=== END REAL VAULT TRIPWIRE RECORD ===

10 passed in 0.91s
TRIPWIRE_EXITCODE=0
```

**The test genuinely depends on the real vault** - with the write neutralised
by the non-delegating spy (so nothing reaches the real vault and nothing is
written), the branch's own round-trip test goes red:

```
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_spy -p no:warnings
        assert store.get(SECRET_NAME) == "sk-roundtrip-zz"
E           AssertionError: assert '' == 'sk-roundtrip-zz'
1 failed, 9 passed
```

So the test was not quietly turned into a MemoryStore, a mock or a skip: it
fails the moment the real write does not happen.

The committed assertions are all still there and unchanged in substance
(`backend/tests/acceptance/test_secret_storage.py:144-205`): the production
class `secrets_module.CredentialManagerStore()` is constructed (`:162`), the
prefix is pinned with `monkeypatch` (`:160`), the store is pinned to the real
store (`:166`), the composed target is asserted to be
`Sythion (Synth) [TEST]/ollama_api_key` and to be **not** under the
import-time product prefix (`:171-173`), the value is read back
(`:177`), `has_secret` is `True` (`:178`), the name is listed (`:179`), the
read-only self-check enumerates the real vault (`:194-201`), the `finally`
deletes (`:203`) and the post-delete assertions are `get(...) == ""` and
`has_secret(...) is False` (`:204-205`).

### E6 - The rest of the acceptance suite is unaffected (before / after)

The acceptance suite, **before** (master, `7cf679e`) and **after** (the branch),
under **identical conditions** (the fake-vault harness on both trees, so that
master's offending test could not touch the real vault), with `--junitxml` on
both:

```
$ cd ...\wt007-master ; python -m pytest backend/tests/acceptance -q -p vault_probe -p no:warnings --junitxml=...junit_master_fake.xml
191 passed, 2 skipped in 18.94s
$ cd ...\wt-007      ; python -m pytest backend/tests/acceptance -q -p vault_probe -p no:warnings --junitxml=...junit_branch_fake.xml
191 passed, 2 skipped in 20.45s
```

Per-test outcome-set comparison (`compare_junit.py`, compares `classname::name`
-> PASSED/FAILED/SKIPPED for every `<testcase>`):

```
$ python compare_junit.py junit_master_fake.xml junit_branch_fake.xml
left  : junit_master_fake.xml -> 193 testcases
right : junit_branch_fake.xml -> 193 testcases
only in left : 0
only in right: 0
different outcome: 0
RESULT: the two per-test outcome sets are IDENTICAL
left  outcome histogram: {'PASSED': 191, 'SKIPPED': 2}
right outcome histogram: {'PASSED': 191, 'SKIPPED': 2}
```

The branch was **also** run against the **real** vault with no harness at all,
and its outcome set is identical to master's:

```
$ cd ...\wt-007 ; python -m pytest backend/tests/acceptance -q -p no:warnings --junitxml=...junit_branch_real.xml
191 passed, 2 skipped in 20.38s
BRANCH_ACCEPTANCE_EXITCODE=0

$ python compare_junit.py junit_master_fake.xml junit_branch_real.xml
left  : junit_master_fake.xml -> 193 testcases
right : junit_branch_real.xml -> 193 testcases
only in left : 0
only in right: 0
different outcome: 0
RESULT: the two per-test outcome sets are IDENTICAL
left  outcome histogram: {'PASSED': 191, 'SKIPPED': 2}
right outcome histogram: {'PASSED': 191, 'SKIPPED': 2}
```

The round-trip test is **not** a skip and **not** marked - it is in the
`PASSED` set on both trees.

### E7 - The full suite

**The branch, against the real vault, with no harness:**

```
$ cd ...\wt-007 ; python -m pytest backend/tests -q -p no:warnings
1566 passed, 45 skipped in 374.99s (0:06:14)
BRANCH_FULL_EXITCODE=0
```

**Master, with `product_blocker.py` installed** (every product-prefixed
write/delete blocked *before* the real vault, everything else real) - this is
how master's full suite could be run at all without letting the defect reach the
credential store:

```
$ cd ...\wt007-master ; python -m pytest backend/tests -q -p product_blocker -p no:warnings --junitxml=...junit_master_full_blocked.xml
=== PRODUCT BLOCKER RECORD ===
write Sythion (Synth)/ollama_api_key  -> BLOCKED before the real vault
=== END PRODUCT BLOCKER RECORD ===
FAILED backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip
1 failed, 1565 passed, 45 skipped in 314.36s (0:05:14)
MASTER_FULL_EXITCODE=1
```

Per-test comparison of the two full-suite runs:

```
$ python compare_junit.py junit_master_full_blocked.xml junit_branch_full.xml
left  : junit_master_full_blocked.xml -> 1611 testcases
right : junit_branch_full.xml -> 1611 testcases
only in left : 0
only in right: 0
different outcome: 1
    backend.tests.acceptance.test_secret_storage::test_credential_manager_round_trip: left=FAILED right=PASSED
RESULT: the two per-test outcome sets DIFFER
left  outcome histogram: {'PASSED': 1565, 'SKIPPED': 45, 'FAILED': 1}
right outcome histogram: {'PASSED': 1566, 'SKIPPED': 45}
```

**Exactly one test differs**, and it is the offending one: on master it fails
*only because the blocker turned its product-target write into an error*; on
the branch it passes against the real vault at the test-scoped target.

Two honesty notes on these numbers:

* The plan's baseline is `1564 passed, 45 skipped`, measured on an older
  master. Today's master gives **1566** when the offending test is blocked
  (1565 + 1 failed), and the branch gives **1566 passed**. The +2 is
  **master's movement since the baseline was measured** (item 004 merged), not
  anything item 007 did: the branch adds **zero** test functions, so it cannot
  move the count, and the two runs above differ by exactly the one test the
  item changes.
* The first master full-suite run (without the dummy env var) also reported
  `FAILED backend/tests/test_setup_readiness.py::test_readiness_states`. That
  is **item 006's pre-existing defect**, not item 007's: my master scratch
  worktree is a clean checkout with **no `backend/.env`** (that file is
  gitignored and is not copied into a new worktree), and the readiness test
  depends on the ambient key. The branch worktree *does* have the
  developer's `backend/.env`, which is why the branch's first full-suite run
  was green. Re-running master with `SYTHION_OLLAMA_API_KEY` set to a dummy
  value (the same value the branch run used) removed that unrelated failure -
  see the run above. **I did not change any test to make this go away**; the
  difference is entirely ambient state, and it is item 006's business.

### E8 - Read-only enumeration before and after (CORROBORATING ONLY)

`read_only_enumerate.py` calls only `CredEnumerate`; it never reads a blob.
Run before any test in this round and again after every run in this round -
**identical**:

```
=== unfiltered enumeration (all credentials visible to this user) ===
CredEnumerate(None, 0): 8 match(es)
    gemini:antigravity
    git:https://github.com
    zed:url=https://zed.dev
    zed:url=https://generativelanguage.googleapis.com
    zed:url=https://openrouter.ai/api/v1
    WindowsLive:(token):name=<masked>@<masked>;serviceuri=http://passport.net/purpose
    MicrosoftAccount:user=<masked>@<masked>
    GitHub - https://api.github.com/<masked>
credentials visible: 8
of those, names containing 'Sythion': 0 -> []

=== the targets this item cares about ===
EXACT product target CredEnumerate('Sythion (Synth)/ollama_api_key', 0): EXCEPTION error: (1168, 'CredEnumerate', 'Element not found.')
PRODUCT PREFIX CredEnumerate('Sythion (Synth)/', 0): EXCEPTION error: (1168, 'CredEnumerate', 'Element not found.')
TEST PREFIX CredEnumerate('Sythion (Synth) [TEST]/', 0): EXCEPTION error: (1168, 'CredEnumerate', 'Element not found.')
TEST TARGET CredEnumerate('Sythion (Synth) [TEST]/ollama_api_key', 0): EXCEPTION error: (1168, 'CredEnumerate', 'Element not found.')
=== END (read-only; no blob was read) ===
```

(I masked the account-identifying parts of the third-party credential names
before pasting them into a file that goes into the repository; the raw output
was `WindowsLive:(token):name=<email>;serviceuri=...`,
`MicrosoftAccount:user=<email>`, `GitHub - https://api.github.com/<user>`. No
credential VALUE was read, printed or stored - only target names.)

**This check is corroborating only, and the plan says so explicitly.** It is
trivially satisfied when the product credential does not exist (which is the
case here), **and it is also satisfied on master**, because master writes the
entry and then deletes it. The proof is the harness record (which target was
written) plus the committed guard (which fails the run if a product-prefixed
target is attempted).

Two useful facts do come out of it: the product credential
`Sythion (Synth)/ollama_api_key` is **genuinely absent** (no Sythion target at
all among the 8 visible credentials), and the test-scoped entry
`Sythion (Synth) [TEST]/ollama_api_key` is **also absent** after every run -
the committed test cleans up after itself.

### E9 - The enumerator forms (the measurement the corrected comment rests on)

```
$ python probe_prefix_forms.py
'EXACT gemini:antigravity'                                             -> 1 match(es) ['gemini:antigravity']
'EXACT git:https://github.com'                                         -> 1 match(es) ['git:https://github.com']
'EXACT zed:url=https://zed.dev'                                        -> 1 match(es) ['zed:url=https://zed.dev']
'EXACT zed:url=https://generativelanguage.googleapis.com'              -> 1 match(es) [...]
'EXACT zed:url=https://openrouter.ai/api/v1'                           -> 1 match(es) [...]
'EXACT WindowsLive:(token):name=...;serviceuri=...'                    -> 1 match(es) [...]
'EXACT MicrosoftAccount:user=...'                                      -> 1 match(es) [...]
'EXACT GitHub - https://api.github.com/...'                            -> 1 match(es) [...]
--- partial prefixes of the same credentials ---
'gemini'                                                               -> EXCEPTION (1168, 'CredEnumerate', 'Element not found.')
'gemini:'                                                              -> EXCEPTION (1168, 'CredEnumerate', 'Element not found.')
'GitHub - https://api.github.com/'                                     -> EXCEPTION (1168, 'CredEnumerate', 'Element not found.')
'GitHub - https:'                                                      -> EXCEPTION (1168, 'CredEnumerate', 'Element not found.')
'MicrosoftAccount:'                                                    -> EXCEPTION (1168, 'CredEnumerate', 'Element not found.')
'zed:'                                                                  -> EXCEPTION (1168, 'CredEnumerate', 'Element not found.')
'WindowsLive:'                                                         -> EXCEPTION (1168, 'CredEnumerate', 'Element not found.')
```

So, measured read-only on this machine:

| call | result |
|---|---|
| `CredEnumerate(None, 0)` (unfiltered) | **8** credentials visible to this user; **none** of their target names contains "Sythion" |
| `CredEnumerate(<EXACT target name of an existing credential>, 0)` | **1** - the exact-match filtered form **works** (for all 8 credentials) |
| `CredEnumerate(<PARTIAL / directory-style prefix of that same credential>, 0)` | **1168** - the prefix form **is** broken on this machine |

---

## Acceptance criteria

| # | Criterion (backlog item 007) | Result | Evidence |
|---|---|---|---|
| 1 | No test writes to or deletes the product's credential target. The round-trip test's write and delete target a name that is not under the product prefix `Sythion (Synth)/`. | **MET** | Fake-vault probe: master records `write/delete Sythion (Synth)/ollama_api_key` -> harness **exit 1**; branch records `write/delete Sythion (Synth) [TEST]/ollama_api_key` -> harness **exit 0** (E2). Whole-suite probe on master also records the product target (E2). Static: the branch's diff lists only the two test files and `backend/core/secrets.py` is byte-identical (E1); the test asserts the composed target equals the test-scoped one and does not start with the import-time product snapshot (`test_secret_storage.py:171-173`). Read-only enumeration before/after is identical (E8) - corroborating only, as the plan requires. |
| 2 | The real-vault round trip is still proven, not mocked away: the test still drives the REAL `win32cred` store, writes and then deletes under a test-only target, and the file exits 0 with 0 failures. | **MET** | `python -m pytest backend/tests/acceptance/test_secret_storage.py -q` (real vault, no harness) -> **10 passed, exit 0** (E5). The tripwire shows the target really appears in the real Windows Credential Manager enumeration right after the write and is gone after the delete (E5). With the write neutralised the test goes red - it really depends on the real vault (E5). The production class is constructed at `test_secret_storage.py:162`; the prefix is pinned at `:160`; the composed target is asserted at `:171-173`; the delete is at `:203`; the post-delete assertions at `:204-205` are unchanged. |
| 3 | A committed guard fails the suite if a test writes to a product target; it is demonstrated to have teeth (a deliberate probe write makes it fail, exit 1; removing the probe makes it pass again, exit 0). | **MET** | Guard: `backend/tests/acceptance/conftest.py:44` (import-time snapshot), `:47-54` (exception class, deliberately not a `pywintypes.error`), `:57-99` (autouse fixture), `:76-83` (`_reject` raises **before** delegating), `:88-95` (the wrappers). Teeth: the transient probe -> **3 failed, 1 passed, exit 1** on the branch, spy records **no** product-target call (E3). The probe also fires against **master's own offending test file** -> exit 1 (E4). With the probe deleted, the acceptance suite is **191 passed, 2 skipped, exit 0** (E6) and `git status --short` is empty. |
| 4 | No production behaviour changes: the diff lists only files under `backend/tests/`; `backend/core/secrets.py` is not modified; the test reaches the test-only prefix by pinning the module attribute inside the test process. | **MET** | `git diff --name-only master...HEAD` -> `backend/tests/acceptance/conftest.py`, `backend/tests/acceptance/test_secret_storage.py` (E1). `git diff master...HEAD -- backend/core/secrets.py backend/api/routes.py` -> **empty** (E1). The prefix is pinned with `monkeypatch.setattr(secrets_module, "_CM_TARGET_PREFIX", _TEST_TARGET_PREFIX)` at `test_secret_storage.py:160`, which works because `_cm_target()` reads the global at call time (`backend/core/secrets.py:32-33`). |
| 5 | The operator action is a follow-up, not code: the report states the read-only enumeration result observed before the change; `.orchestra/follow-ups.md` is the orchestrator's to write. | **MET (flagged, not done)** | Before the change: the product target `Sythion (Synth)/ollama_api_key` is **absent** (winerror 1168), the product prefix enumerates empty, and the unfiltered enumeration sees 8 credentials of which **none** is a Sythion target (E8). **The orchestrator should record the follow-up**: the operator should check READ-ONLY whether a credential under `Sythion (Synth)/ollama_api_key` needs re-entering. I did not touch `.orchestra/follow-ups.md`. |
| 6 | The full suite still passes, with the same passed/skipped counts as the pre-change baseline; no test is deleted, skipped or marked to make the suite green. | **MET (with the honest note below)** | Branch: `python -m pytest backend/tests -q` -> **1566 passed, 45 skipped, exit 0** (E7). Master, with the product-target blocker: **1565 passed, 45 skipped, 1 failed** - the one failure is the blocked offending test (E7). The per-test outcome sets differ in exactly that one test (E7). The plan's `1564 passed` baseline was measured on an older master; today's master gives 1566 with the offending test blocked, and the branch adds **zero** test functions, so it cannot move the count. No test was deleted, skipped, xfailed or marked. |

Plan-level criteria that the backlog does not list separately, also verified:
the guard does not intercept `CredRead` (reads stay non-destructive) and does
not touch `secrets_module._store` (`conftest.py:57-99`); the JUnit per-test
outcome comparison is identical for the acceptance suite before/after (E6).

---

## The fake-vault probe

* **Where it lives:** `C:\Users\orcha\AppData\Local\Temp\opencode\wt007-harness\vault_probe.py`
  - **outside** the repository, on `PYTHONPATH` only. It is not committed, not
  referenced by any test, and nothing in the branch depends on it.
* **What it does:** replaces `win32cred.CredWrite / CredRead / CredDelete /
  CredEnumerate` with in-process fakes backed by one dictionary
  (`target -> blob`), so **no vault operation is performed at all**. It records
  every `(op, target)` pair. The fake blob is encoded `utf-16-le`, because
  `CredentialManagerStore.get` decodes with `utf-16-le`
  (`backend/core/secrets.py:60-64`).
* **Its verdict** (printed and used as the process exit status): FAIL if any
  recorded target starts with `Sythion (Synth)/`; FAIL if no vault mutation was
  recorded at all; FAIL unless a write to a non-product target is followed by a
  delete of the same target; otherwise PASS. `VAULT_PROBE_JUDGE=0` prints the
  record but keeps pytest's own exit status (used for the before/after
  acceptance comparison, where the verdict is not the thing being compared).
* **Its output:** see E2 - master records the product target and exits 1; the
  branch records the test-scoped target and exits 0.

Two further stand-ins were needed for the other two questions:

* `vault_spy.py` - replaces `CredWrite`/`CredDelete` with recorders that
  **never delegate**, so the real vault is unreachable for any target. Used for
  the guard-teeth runs (E3, E4).
* `product_blocker.py` - delegates to the real API but blocks any
  product-prefixed target **before** delegating. Used to run **master's** full
  suite without letting the defect reach the credential store (E7).
* `real_vault_tripwire.py` - delegates to the real `CredWrite`/`CredDelete` and
  then asks the **real, unfiltered** `CredEnumerate` whether the target is
  visible. Used for the anti-mock proof (E5).

---

## The guard has teeth

* **The demonstration:** the transient probe
  `backend/tests/acceptance/test_zz_product_target_probe_tmp.py` (created, run,
  deleted; **not** in the commit - `git status --short` is empty afterwards)
  calls the production store class directly and tries to write to, delete, and
  (with the prefix patched) write directly to the product target. On the
  branch all three attempts raise
  `backend.tests.acceptance.conftest.ProductCredentialWriteAttempt` and the
  run exits **1** (E3).
* **The proof the real vault was never reached:** the run was made with
  `vault_spy.py`, which never delegates - so the real API was unreachable by
  construction - and the spy record contains **only** the control test-scoped
  write. On top of that, the guard's source checks the target **before**
  delegating (`conftest.py:88-95`), and the master-side contrast run shows the
  same probe with no guard reaching the vault layer with
  `write Sythion (Synth)/ollama_api_key` while the suite stays **green** - the
  defect the guard removes (E3).
* **Extra teeth evidence:** the guard fires against **master's own offending
  test file** (E4), and against master's **whole acceptance suite** the
  blocker recorded exactly one product-target attempt - the round-trip test's
  (E2, E7). No other test in the suite writes to the product target.

---

## The correction to the enumeration comment

**What the comment claimed (wrongly), at `test_secret_storage.py:186-191`
before my commit:**

> The enumeration is deliberately UNFILTERED and filtered here. The
> prefix-filtered form, `CredEnumerate(prefix, 0)`, returns winerror 1168 on
> this machine even for credentials that demonstrably exist (it fails for every
> prefix tried), **so it cannot tell "absent" from "broken enumerator"**. The
> unfiltered form really does see the entry, which is what makes an "absent"
> observation non-vacuous.

The middle sentence was true of the *prefix* form, but the paragraph's
implication - that an absence check made with the filtered form was therefore
inconclusive - was **false**, because the check that was actually made used the
**exact-match** form, which works.

**What it says now (`test_secret_storage.py:186-193`, commit `4680e0b`):**

> The enumeration is deliberately UNFILTERED and filtered here. Measured on this
> machine: a PARTIAL prefix - a directory-style prefix of a credential that
> demonstrably exists included - makes `CredEnumerate(prefix, 0)` return
> winerror 1168, whereas the EXACT-MATCH form `CredEnumerate(<exact target
> name>, 0)` works for every existing credential. The unfiltered form is used
> because it cannot be defeated by either quirk, and it really does see the
> entry, which is what makes an "absent" observation non-vacuous.

**Why this is the right claim:**

* The **directory/partial-prefix** form really is broken on this machine -
  measured, not assumed (E9): every partial prefix tried returns 1168 even
  for credentials that demonstrably exist.
* The **exact-match** form really works - measured for all 8 visible
  credentials (E9). So an exact-match absence check **is** conclusive, and
  the misleading implication is gone.
* The **unfiltered** form is kept because it cannot be defeated by either
  quirk - that is the stated reason now, instead of "the check was
  inconclusive".
* **The behaviour did not change**: the self-check still calls
  `win32cred.CredEnumerate(None, 0)` (`:195`) and filters the target names in
  Python (`:201`). The commit is a comment-only change: 8 insertions, 6
  deletions, all `#` lines.

---

## Findings

1. **The plan's line-number baseline has moved.** The plan quotes master
   `702ffc5`; master is now `7cf679e` and the full-suite baseline is
   `1566 passed, 45 skipped` (not `1564`). Verified against master directly:
   master with the product-target blocker gives 1565 passed + 1 failed, i.e.
   the same 1566 with the offending test turned red. **The +2 is master's
   movement, not item 007** - the branch adds zero test functions.
2. **A pre-existing, unrelated failure surfaced in a clean checkout.**
   `backend/tests/test_setup_readiness.py::test_readiness_states` fails in a
   clean tree without `backend/.env` (that file is gitignored and is not
   copied into a new worktree). This is exactly item 006's subject
   (`backlog.md` item 006, criterion 1: "in a clean checkout ... FAILS"). I did
   not touch it and did not paper over it; the comparison runs above pin
   `SYTHION_OLLAMA_API_KEY` to a dummy value so the unrelated ambient
   dependency cannot mask the comparison.
3. **My first "disarm" probe was misconceived, and the run says so.** I wrote
   a probe that patched `_CM_TARGET_PREFIX` to the test prefix and then called
   the production store's `set()`. It **passed** - correctly - because the
   patched prefix changes the *composed* target, so the write went to
   `Sythion (Synth) [TEST]/ollama_api_key` (the spy recorded exactly that).
   The real disarm scenario is a *direct* `win32cred.CredWrite` to the product
   target while the prefix is patched; that is the third probe test, and it is
   **blocked**. Both facts are in the evidence above.
4. **The guard is acceptance-scoped only** (as the plan states). A test outside
   `backend/tests/acceptance/` that wrote to the product target would not be
   caught by the committed guard. My master full-suite run with
   `product_blocker.py` is evidence about *today's* suite: exactly one test
   attempts a product-target write, and it is the round-trip test. Moving the
   guard to the root `backend/tests/conftest.py` remains the right follow-up,
   but that file is contended by items 002 and 006.
5. **The residual side effect the plan discloses is real but harmless.** The
   acceptance suite now creates and deletes `Sythion (Synth) [TEST]/ollama_api_key`
   in the **real** Windows Credential Manager during the round-trip test. If a
   run dies between the write and the `finally` delete, a stray entry survives
   (`CRED_PERSIST_LOCAL_MACHINE`, inherited from the production store). It is
   visibly test-scoped in the Credential Manager UI and it is not the product's
   key. **What the operator should look for:** a stray
   `Sythion (Synth) [TEST]/ollama_api_key` entry. After every run in this round
   the enumeration shows that entry **absent** - the cleanup works (E8).
6. **The enumerator quirk is machine-specific and worth knowing.**
   On this machine, `CredEnumerate(prefix, 0)` returns 1168 for every
   *partial* prefix and works only for an exact target name (E9). Any other
   tooling in this repository that filters credentials by prefix would be
   silently broken here. I did not change any other tool - this is an
   observation, not a change.
7. **Doc debt is unchanged and still false (deliberately, criterion 4).**
   `docs/ARCHITECTURE.md:358` ("A non-persistent in-memory backend is used for
   tests so the real vault is never touched") is still inaccurate: after this
   fix the suite performs a real `win32cred` write/delete - at a *test-scoped*
   target. `docs/ACCEPTANCE_TESTS.md:199` ("Secrets in Credential Manager
   mock") is also inaccurate - the test uses the **real** vault, before and
   after. Both wordings belong to item 003 / the documentation follow-up.
8. **The guard's exception type is load-bearing.**
   `ProductCredentialWriteAttempt` derives from `AssertionError`, **not**
   `pywintypes.error`, because `CredentialManagerStore.delete` swallows
   `pywintypes.error` (`backend/core/secrets.py:83-86`). The evidence in E3/E4
   shows the delete attempt failing loudly, which it would not have if the
   exception had been swallowed.

---

## Not checked

* **Whether the product credential ever existed, and whether an earlier
  full-suite run in an earlier session deleted it.** That cannot be determined
  from the repository, and the fix deliberately does not depend on it. The
  operator's READ-ONLY check (criterion 5) is still outstanding and belongs in
  the orchestrator's follow-up entry.
* **Any credential VALUE.** No blob was ever read, printed or stored. Only
  target names were listed.
* **A real-vault reproduction of master's defect.** Deliberately not done. The
  fake-vault probe is sufficient: it records which target was written, it
  performs no vault operation at all, and it is the plan's specified proof. A
  real-vault reproduction would have meant writing to (and deleting) the
  product target - the exact harm this item exists to prevent - and would have
  proved nothing that the probe does not prove more safely.
* **The root `backend/tests/conftest.py` guard** (deferred: items 002 and 006
  contend for that file).
* **Non-Windows behaviour.** The guard's fixture is a no-op when `win32cred`
  cannot be imported (`conftest.py:69-74`), by design; only the Windows path
  was exercised here.
* **The CI runner (Python 3.11, per `backlog.md` item 003) and the enumerator
  quirk.** The partial-prefix quirk was measured on CPython 3.14.7 / Windows
  only; I did not check whether a 3.11 runner behaves differently. The
  committed self-check uses the unfiltered form precisely because it does not
  depend on either quirk.
* **`.orchestra/follow-ups.md`** - not touched; the orchestrator writes it.
* **`docs/ARCHITECTURE.md` and `docs/ACCEPTANCE_TESTS.md`** - not touched
  (criterion 4); the wording debt is recorded in Finding 7.
* **The other items in flight** (002, 006) - their worktrees and branches were
  not touched.
* **The repository's CogniGraph memory protocol** (`AGENTS.md`). The memory
  MCP server's tools were not connected to this subagent session - the tool
  catalogue exposed to this session contains no `memory_*` tool - so
  `memory_stats` / `memory_query` / `memory_prepare_task` could not be called.
  The brief forbids asking the user anything and names the report file as the
  deliverable, so I proceeded and read the plan, the brief and the backlog
  directly instead. Nothing was written to the memory store.

---

## Reproducing this report

```powershell
# harness (outside the repo)
$env:PYTHONPATH="C:\Users\orcha\AppData\Local\Temp\opencode\wt007-harness"

# master (detached scratch worktree at 7cf679e) - fake vault, no real vault call
cd C:\Users\orcha\AppData\Local\Temp\opencode\wt007-master
python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_probe   # -> exit 1, product target recorded

# branch - fake vault
cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-007
python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_probe   # -> exit 0, test-scoped target recorded

# branch - real vault, real round trip
python -m pytest backend/tests/acceptance/test_secret_storage.py -q                    # -> 10 passed
python -m pytest backend/tests/acceptance -q                                          # -> 191 passed, 2 skipped
python -m pytest backend/tests -q                                                      # -> 1566 passed, 45 skipped
```

Nothing was pushed. No production module was changed. The product credential
was never written, deleted or read.
