# Report: implement - 007 - attempt 2

Item **007 - The acceptance suite must never write to or delete the product's
real credential**. Branch **`orchestra/007-no-real-vault-writes`**, worktree
**`C:\Users\orcha\AppData\Local\Temp\opencode\wt-007`**.

This is a **short hardening round** on top of the accepted round 1
(`a0fa314`, `e1ff9b1`, `4680e0b`). The Reviewer returned `VERDICT: ACCEPT`
with eight findings, **none of them a criterion failure**. The orchestrator
picked two of them as real holes in the guard and asked for them to be closed.
Nothing else was touched.

* **HOLE 1** - the prefix comparison was case-SENSITIVE, but Windows
  credential target names are NOT. A write to `sythion (synth)/ollama_api_key`
  would land in the product's own credential slot and would not have tripped
  the guard.
* **HOLE 2** - the guard FAILED OPEN when it could not read the target: a
  credential that is not dict-like produced an empty target, which passed the
  check and was **delegated to the real vault**.

**Both holes are closed.** Proof is below, with the BEFORE behaviour measured
on the pre-change guard using a **non-delegating spy** - the product target was
never written to or deleted from, in any spelling, at any point in this round.

---

## What I did

### The commit

| sha | message | files |
|---|---|---|
| **`468a17a`** | `test(acceptance): reject product-credential writes case-insensitively and fail closed` | `backend/tests/acceptance/conftest.py` only |

```
$ git -C ...\wt-007 log --oneline -3
468a17a test(acceptance): reject product-credential writes case-insensitively and fail closed
4680e0b test(acceptance): correct the enumeration-form claim in the round-trip self-check
e1ff9b1 test(acceptance): enumerate the vault unfiltered in the round-trip self-check

$ git -C ...\wt-007 show --stat HEAD
 backend/tests/acceptance/conftest.py | 62 +++++++++++++++++++++++++++++++++---
 1 file changed, 57 insertions(+), 5 deletions(-)
```

History was not rewritten, not rebased, not amended. **Nothing was pushed.**

### Files I touched

| file | what |
|---|---|
| `backend/tests/acceptance/conftest.py` | the two hole fixes + the comments/docstring that explain them |
| `C:\Sythion\sythion\.orchestra\reports\007-implement-2.md` | this report (the deliverable, written into the orchestrator's tree, **not** into the branch) |

**Nothing else.** In particular I did not touch `backend/core/secrets.py`,
`backend/api/routes.py`, `backend/main.py`, the root `backend/tests/conftest.py`,
any `docs/` file, or `.orchestra/follow-ups.md`.

```
$ git -C ...\wt-007 diff --name-only master...HEAD
backend/tests/acceptance/conftest.py
backend/tests/acceptance/test_secret_storage.py

$ git -C ...\wt-007 diff master...HEAD -- backend/core/secrets.py backend/api/routes.py backend/main.py backend/tests/conftest.py docs/
(empty)

$ git -C ...\wt-007 diff 4680e0b..HEAD --stat
 backend/tests/acceptance/conftest.py | 62 +++++++++++++++++++++++++++++++++---
 1 file changed, 57 insertions(+), 5 deletions(-)
```

The round-2 diff is **one file**: `backend/tests/acceptance/conftest.py`.
`backend/tests/acceptance/test_secret_storage.py` is byte-identical to
`4680e0b` (I reverted an experimental one-line change to it - see
*Findings*, #6 - so that the line numbers the orchestrator cited for the
round-trip RED proofs, `:177` and `:197`, stay valid).

### The change (the whole diff)

```diff
--- a/backend/tests/acceptance/conftest.py
+++ b/backend/tests/acceptance/conftest.py
@@ -43,6 +43,20 @@ ACCEPTANCE_CONTROL_TOKEN = "acceptance-control-token"
 
 _PRODUCT_TARGET_PREFIX = secrets_module._CM_TARGET_PREFIX
 
+# The prefix comparison is CASE-INSENSITIVE, and it has to be: Windows
+# Credential Manager target names are case-insensitive, so
+# "sythion (synth)/ollama_api_key" and "Sythion (Synth)/ollama_api_key"
+# address the SAME credential slot (measured: writing the second spelling
+# does not produce a second, separately visible credential). A case-sensitive
+# comparison would let the lower-cased spelling through and land the write
+# in the product's own credential.
+#
+# DO NOT "fix" this back to a case-sensitive comparison. The test-scoped
+# prefix ("Sythion (Synth) [TEST]/") is unaffected: it differs from the
+# product prefix at the character after "Sythion (Synth)" - a space, not a
+# "/" - so it is still allowed in either spelling.
+_PRODUCT_TARGET_PREFIX_LOWER = _PRODUCT_TARGET_PREFIX.lower()
+
 
 class ProductCredentialWriteAttempt(AssertionError):
@@ -65,6 +79,21 @@ def _forbid_product_credential_writes(request, monkeypatch):
 
     The guard never touches ``secrets_module._store`` - no other acceptance
     test sees a different secret store than before.
+
+    The guard's rule is "reject unless you can PROVE the target is not a
+    product target", which has two consequences:
+
+    * The prefix comparison is CASE-INSENSITIVE. Windows credential target
+      names are case-insensitive, so "sythion (synth)/ollama_api_key" is the
+      same slot as "Sythion (Synth)/ollama_api_key" and is rejected too.
+    * The guard FAILS CLOSED. A credential whose target name cannot be read
+      is REJECTED, never delegated to the real vault.
+
+    Known constraint: ``backend/main.py`` installs a global
+    ``@app.exception_handler(Exception)``. A future acceptance test that drove
+    a vault write through ``backend.main.app`` would get a 500 response
+    instead of this exception - the write would still be blocked, but the
+    test would not go red. No acceptance test uses ``main.app`` today.
     """
     try:
         import win32cred
@@ -74,20 +103,43 @@ def _forbid_product_credential_writes(request, monkeypatch):
         return
 
     def _reject(target, action):
-        if isinstance(target, str) and target.startswith(_PRODUCT_TARGET_PREFIX):
+        """Raise unless the target is PROVABLY not a product target.
+
+        Fails CLOSED: an unreadable target is rejected, not delegated.
+        """
+        if not isinstance(target, str):
+            raise ProductCredentialWriteAttempt(
+                f"acceptance test {request.node.nodeid} attempted to {action} a "
+                f"credential whose target name could not be read. The guard "
+                f"fails closed: the target must be a readable string that is "
+                f"not under {_PRODUCT_TARGET_PREFIX!r}."
+            )
+        if target.lower().startswith(_PRODUCT_TARGET_PREFIX_LOWER):
             raise ProductCredentialWriteAttempt(
                 f"acceptance test {request.node.nodeid} attempted to {action} the "
                 f"product credential target {target!r}. Tests must never write to "
-                f"or delete targets under {_PRODUCT_TARGET_PREFIX!r}; use a "
-                f"test-scoped prefix instead."
+                f"or delete targets under {_PRODUCT_TARGET_PREFIX!r} (the "
+                f"comparison is case-insensitive, because Windows credential "
+                f"target names are); use a test-scoped prefix instead."
             )
 
+    def _target_of(cred):
+        """The target name of a credential, or None if it cannot be read."""
+        getter = getattr(cred, "get", None)
+        if getter is None:
+            return None
+        try:
+            target = getter("TargetName", None)
+        except Exception:
+            # Anything unreadable is treated as "unknown target" -> rejected.
+            return None
+        return target if isinstance(target, str) else None
+
     original_write = win32cred.CredWrite
     original_delete = win32cred.CredDelete
 
     def guarded_write(cred, flags=0):
-        target = cred.get("TargetName", "") if hasattr(cred, "get") else ""
-        _reject(target, "write")
+        _reject(_target_of(cred), "write")
         return original_write(cred, flags)
 
     def guarded_delete(target, *args, **kwargs):
```

### The commands I ran (in order)

```powershell
# 1. read the brief, the review, the plan, the backlog, round 1's report,
#    the committed guard and the round-trip test

# 2. BEFORE proof of both holes, with a NON-DELEGATING spy (the real vault
#    is unreachable for every target), on the pre-change guard
cd ...\wt-007
$env:PYTHONPATH="C:\Users\orcha\AppData\Local\Temp\opencode\wt007-harness"
python -m pytest backend/tests/acceptance/test_zz_hole_probe_tmp.py -q -p vault_spy2 -p no:warnings -rA --tb=short

# 3. the change -> 468a17a

# 4. AFTER proof of both holes, same probe, same spy
python -m pytest backend/tests/acceptance/test_zz_hole_probe_tmp.py -q -p vault_spy2 -p no:warnings -rA --tb=short

# 5. the guard's teeth (4/4 product-target attempts) + the non-delegating spy
python -m pytest backend/tests/acceptance/test_zz_teeth_probe_tmp.py -q -p vault_spy2 -p no:warnings -rA --tb=short

# 6. the real vault, both spellings of the TEST-SCOPED target
python -m pytest backend/tests/acceptance/test_zz_lower_real_probe_tmp.py -q -p no:warnings -rA --tb=short
python -m pytest backend/tests/acceptance/test_zz_case_real_probe_tmp.py  -q -p no:warnings -rA --tb=short -s

# 7. the round trip is still real
python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p no:warnings          # real vault
python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_spy2 -p no:warnings   # write neutralised
python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p blind_enum -p no:warnings    # enumerator blinded

# 8. the suites
python -m pytest backend/tests/acceptance -q -p no:warnings --junitxml=...junit_r2_final_acceptance.xml
python -m pytest backend/tests -q -p no:warnings --junitxml=...junit_r2_final_full_retry2.xml

# 9. the per-test outcome-set comparison
python compare_junit.py junit_branch_real.xml junit_r2_final_acceptance.xml
python compare_junit.py junit_master_fake.xml junit_r2_final_acceptance.xml

# 10. read-only enumeration, cleanup of my own test-scoped probe credential, git status
```

### The harnesses (all OUTSIDE the repository)

```
C:\Users\orcha\AppData\Local\Temp\opencode\wt007-harness\
    vault_spy2.py         # NEW this round: non-delegating spy; records an
                          # unreadable credential as "<unreadable target: ...>"
    blind_enum.py         # NEW this round: CredEnumerate always raises 1168
    probe_hole.py         # NEW this round: the 8 spelling / unreadable-target probes
    probe_teeth.py        # NEW this round: the 4 product-target attempts + the control
    cleanup_test_scoped.py# NEW this round: deletes ONLY test-scoped targets, never
                          # the product namespace; prints target NAMES only
    compare_junit.py      # round 1: per-test outcome-set comparison
    read_only_enumerate.py# round 1: read-only enumeration (names only)
```

The two probe files were **copied into** `backend/tests/acceptance/` for their
runs and **deleted afterwards**; they are not in the commit and
`git status --short` is empty (E8).

### Vault discipline (the constraint that mattered most)

* I **never** wrote to, or deleted from, the product target
  `Sythion (Synth)/...` **in any spelling** - not `Sythion (Synth)/...`, not
  `sythion (synth)/...`, not any other casing. Every product-target attempt in
  this round was made with the **non-delegating spy** installed, so the real
  Windows Credential Manager was unreachable by construction, and every one of
  them was **blocked before delegation**.
* I **never printed or read a credential VALUE**. Only target **names** were
  listed, and the read-only enumeration never touches `CredentialBlob`.
* The only real-vault writes I caused were **test-scoped**:
  `Sythion (Synth) [TEST]/ollama_api_key` (the branch's own round-trip test),
  plus my own probe targets `Sythion (Synth) [TEST]/caseprobe`,
  `sythion (synth) [test]/caseprobe` and `sythion (synth) [test]/caseprobe-lower`.
  **All of them are deleted** - the read-only enumeration after the round shows
  **8** credentials and **no** target name containing "Sythion" (E7).
* I did **not** run the offending test on `master` against the real vault, and
  I did not reproduce the BEFORE behaviour against the real product target: the
  BEFORE proof uses the spy.

---

## Evidence

### E1 - The BEFORE behaviour of both holes (pre-change guard, non-delegating spy)

The probe (`probe_hole.py`, copied in as `test_zz_hole_probe_tmp.py`) makes
eight attempts. The spy replaces `win32cred.CredWrite` / `CredDelete` with
recorders that **never delegate**, so the real vault was unreachable for every
target in this run.

```
$ python -m pytest backend/tests/acceptance/test_zz_hole_probe_tmp.py -q -p vault_spy2 -p no:warnings -rA --tb=short
F.......                                                                 [100%]
=== VAULT SPY RECORD (no real vault call was made) ===
write sythion (synth)/ollama_api_key
write Sythion (Synth) [TEST]/caseprobe
write sythion (synth) [test]/caseprobe
write <unreadable target: no .get on object>
write <unreadable target: dict has no TargetName str>
delete sythion (synth)/ollama_api_key
delete sythion (synth) [test]/caseprobe
=== END VAULT SPY RECORD ===
...
E   backend.tests.acceptance.conftest.ProductCredentialWriteAttempt: acceptance test backend/tests/acceptance/test_zz_hole_probe_tmp.py::test_probe_a_write_product_original_spelling attempted to write the product credential target 'Sythion (Synth)/ollama_api_key'. ...
PASSED backend/tests/acceptance/test_zz_hole_probe_tmp.py::test_probe_b_write_product_lower_spelling
PASSED backend/tests/acceptance/test_zz_hole_probe_tmp.py::test_probe_c_write_test_scoped_original_spelling
PASSED backend/tests/acceptance/test_zz_hole_probe_tmp.py::test_probe_d_write_test_scoped_lower_spelling
PASSED backend/tests/acceptance/test_zz_hole_probe_tmp.py::test_probe_e_write_non_dict_credential
PASSED backend/tests/acceptance/test_zz_hole_probe_tmp.py::test_probe_f_write_dict_without_target_name
PASSED backend/tests/acceptance/test_zz_hole_probe_tmp.py::test_probe_g_delete_product_lower_spelling
PASSED backend/tests/acceptance/test_zz_hole_probe_tmp.py::test_probe_h_delete_test_scoped_lower_spelling
FAILED backend/tests/acceptance/test_zz_hole_probe_tmp.py::test_probe_a_write_product_original_spelling
1 failed, 7 passed in 0.46s
```

Reading the BEFORE record:

| attempt | BEFORE outcome | reached the vault layer? |
|---|---|---|
| write `Sythion (Synth)/ollama_api_key` (product, original) | **BLOCKED** | no |
| write `sythion (synth)/ollama_api_key` (product, lower-cased) | **NOT blocked** | **YES** - the spy recorded it |
| write `Sythion (Synth) [TEST]/caseprobe` (test-scoped) | allowed | yes (spy) |
| write `sythion (synth) [test]/caseprobe` (test-scoped, lower-cased) | allowed | yes (spy) |
| write a **non-dict** credential | **NOT blocked** | **YES** - `write <unreadable target: no .get on object>` |
| write a **dict with no TargetName** | **NOT blocked** | **YES** - `write <unreadable target: dict has no TargetName str>` |
| delete `sythion (synth)/ollama_api_key` (product, lower-cased) | **NOT blocked** | **YES** - `delete sythion (synth)/ollama_api_key` |
| delete `sythion (synth) [test]/caseprobe` (test-scoped, lower-cased) | allowed | yes (spy) |

**That is hole 1 and hole 2, measured, on the pre-change guard.** The
lower-cased product target and the unreadable credential both reached the
vault layer. (The real vault was not involved: the spy never delegates.)

### E2 - The AFTER behaviour of both holes (the committed guard, same probe, same spy)

```
$ python -m pytest backend/tests/acceptance/test_zz_hole_probe_tmp.py backend/tests/acceptance/test_zz_teeth_probe_tmp.py -q -p vault_spy2 -p no:warnings -rA --tb=line
FF..FFF.FFFF.                                                            [100%]
=== VAULT SPY RECORD (no real vault call was made) ===
write Sythion (Synth) [TEST]/caseprobe
write sythion (synth) [test]/caseprobe
delete sythion (synth) [test]/caseprobe
write Sythion (Synth) [TEST]/ollama_api_key
=== END VAULT SPY RECORD ===

...conftest.py:118: ...ProductCredentialWriteAttempt: acceptance test ...test_probe_a_write_product_original_spelling attempted to write the product credential target 'Sythion (Synth)/ollama_api_key'. Tests must never write to or delete targets under 'Sythion (Synth)/' (the comparison is case-insensitive, because Windows credential target names are); use a test-scoped prefix instead.
...conftest.py:118: ...ProductCredentialWriteAttempt: acceptance test ...test_probe_b_write_product_lower_spelling attempted to write the product credential target 'sythion (synth)/ollama_api_key'. Tests must never write to or delete targets under 'Sythion (Synth)/' (the comparison is case-insensitive, because Windows credential target names are); use a test-scoped prefix instead.
...conftest.py:111: ...ProductCredentialWriteAttempt: acceptance test ...test_probe_e_write_non_dict_credential attempted to write a credential whose target name could not be read. The guard fails closed: the target must be a readable string that is not under 'Sythion (Synth)/'.
...conftest.py:111: ...ProductCredentialWriteAttempt: acceptance test ...test_probe_f_write_dict_without_target_name attempted to write a credential whose target name could not be read. The guard fails closed: the target must be a readable string that is not under 'Sythion (Synth)/'.
...conftest.py:118: ...ProductCredentialWriteAttempt: acceptance test ...test_probe_g_delete_product_lower_spelling attempted to delete the product credential target 'sythion (synth)/ollama_api_key'. ...
...conftest.py:118: ...ProductCredentialWriteAttempt: acceptance test ...test_probe_teeth_probe_tmp.py::test_probe_write_to_product_target attempted to write the product credential target 'Sythion (Synth)/ollama_api_key'. ...
...conftest.py:118: ...ProductCredentialWriteAttempt: acceptance test ...test_probe_teeth_probe_tmp.py::test_probe_delete_of_product_target attempted to delete the product credential target 'Sythion (Synth)/ollama_api_key'. ...
...conftest.py:118: ...ProductCredentialWriteAttempt: acceptance test ...test_probe_teeth_probe_tmp.py::test_probe_direct_write_to_product_target_with_patched_prefix attempted to write the product credential target 'Sythion (Synth)/ollama_api_key'. ...
...conftest.py:118: ...ProductCredentialWriteAttempt: acceptance test ...test_probe_teeth_probe_tmp.py::test_probe_api_driven_write_to_product_target attempted to write the product credential target 'Sythion (Synth)/ollama_api_key'. ...
PASSED ...test_probe_c_write_test_scoped_original_spelling
PASSED ...test_probe_d_write_test_scoped_lower_spelling
PASSED ...test_probe_h_delete_test_scoped_lower_spelling
PASSED ...test_probe_control_patched_prefix_composes_test_scoped_target
FAILED ...test_probe_a_write_product_original_spelling
FAILED ...test_probe_b_write_product_lower_spelling
FAILED ...test_probe_e_write_non_dict_credential
FAILED ...test_probe_f_write_dict_without_target_name
FAILED ...test_probe_g_delete_product_lower_spelling
FAILED ...test_probe_write_to_product_target
FAILED ...test_probe_delete_of_product_target
FAILED ...test_probe_direct_write_to_product_target_with_patched_prefix
FAILED ...test_probe_api_driven_write_to_product_target
9 failed, 4 passed in 0.95s
```

**The spy record contains only test-scoped targets.** No product-prefixed
target, in any spelling, reached the vault layer - the interception happens
**before** delegation, in `conftest.py:141-147`:

```python
    def guarded_write(cred, flags=0):
        _reject(_target_of(cred), "write")     # <-- raises here, BEFORE delegating
        return original_write(cred, flags)

    def guarded_delete(target, *args, **kwargs):
        _reject(target, "delete")              # <-- raises here, BEFORE delegating
        return original_delete(target, *args, **kwargs)
```

### E3 - The guard still has teeth: 4/4 product-target attempts blocked

The same teeth probe the Reviewer used (a store `set`, a store `delete`, the
"disarm attempt" - a direct `CredWrite` to the product target with the prefix
patched - plus an API-driven write), run against the **committed** guard with
the **non-delegating spy** installed:

| # | attempt | result |
|---|---|---|
| 1 | `CredentialManagerStore().set("ollama_api_key", ...)` | **BLOCKED** (`conftest.py:118`) |
| 2 | `CredentialManagerStore().delete("ollama_api_key")` | **BLOCKED** (`conftest.py:118`) |
| 3 | direct `win32cred.CredWrite` to `Sythion (Synth)/ollama_api_key` with `_CM_TARGET_PREFIX` patched | **BLOCKED** (`conftest.py:118`) |
| 4 | API-driven: `PUT /api/v1/secrets/ollama_api_key` through the real store | **BLOCKED** (`conftest.py:118`) |
| - | control: with the prefix patched, `set()` composes the test-scoped target | **allowed**, the spy recorded `write Sythion (Synth) [TEST]/ollama_api_key` |

The API attempt raised straight out of the `TestClient` (the acceptance app
has no global exception handler), through
`backend/api/routes.py:1948 -> backend/core/secrets.py:147 -> :77 ->
win32cred.CredWrite`:

```
E   backend.tests.acceptance.conftest.ProductCredentialWriteAttempt: acceptance test backend/tests/acceptance/test_zz_teeth_probe_tmp.py::test_probe_api_driven_write_to_product_target attempted to write the product credential target 'Sythion (Synth)/ollama_api_key'. ...
```

### E4 - The round trip is still real (three independent mutations)

**Positive control - the real vault, no harness:**

```
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p no:warnings
..........                                                               [100%]
10 passed in 1.44s
REAL_VAULT_EXITCODE=0
```

**Mutation 1 - the write neutralised (non-delegating spy, nothing reaches the
vault). The test goes RED at `test_secret_storage.py:177`:**

```
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_spy2 -p no:warnings --tb=short
........F.                                                               [100%]
=== VAULT SPY RECORD (no real vault call was made) ===
write Sythion (Synth) [TEST]/ollama_api_key
delete Sythion (Synth) [TEST]/ollama_api_key
=== END VAULT SPY RECORD ===

=================================== FAILURES ===================================
_____________________ test_credential_manager_round_trip ______________________
backend\tests\acceptance\test_secret_storage.py:177: in test_credential_manager_round_trip
    assert store.get(SECRET_NAME) == "sk-roundtrip-zz"
E   AssertionError: assert '' == 'sk-roundtrip-zz'
1 failed, 9 passed in 1.47s
```

**Mutation 2 - the enumerator blinded (`CredEnumerate` always raises 1168).
The test goes RED at `test_secret_storage.py:197`:**

```
$ python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p blind_enum -p no:warnings --tb=short
........F.                                                               [100%]
=================================== FAILURES ===================================
_____________________ test_credential_manager_round_trip ______________________
backend\tests\acceptance\test_secret_storage.py:195: in test_credential_manager_round_trip
    enumerated = win32cred.CredEnumerate(None, 0)
..\wt007-harness\blind_enum.py:24: in blind_enumerate
    raise pywintypes.error(1168, "CredEnumerate", "Element not found.')
E   pywintypes.error: (1168, 'CredEnumerate', 'Element not found.')

During handling of the above exception, another exception occurred:
backend\tests\acceptance\test_secret_storage.py:197: in test_credential_manager_round_trip
    pytest.fail(
E   Failed: the test-scoped credential was not found in the real Windows Credential Manager: error(1168, 'CredEnumerate', 'Element not found.')
1 failed, 9 passed in 1.54s
```

**Both RED points are the exact lines the orchestrator cited** (`:177` and
`:197`) - `test_secret_storage.py` is byte-identical to `4680e0b`, so the
citations still hold. The test is genuinely dependent on a real vault write
and on a real vault enumeration; it was not quietly mocked away.

### E5 - The acceptance suite: 191 passed, 2 skipped, identical per-test outcomes

```
$ python -m pytest backend/tests/acceptance -q -p no:warnings --junitxml=...junit_r2_final_acceptance.xml
.......................................................ss............... [ 37%]
........................................................................ [ 74%]
.................................................                        [100%]
191 passed, 2 skipped in 29.56s
ACCEPTANCE_EXITCODE=0
```

Per-test outcome-set comparison against **round 1's** run of the same suite
(identical tree, real vault):

```
$ python compare_junit.py junit_branch_real.xml junit_r2_final_acceptance.xml
left  : junit_branch_real.xml -> 193 testcases
right : junit_r2_final_acceptance.xml -> 193 testcases
only in left : 0
only in right: 0
different outcome: 0
RESULT: the two per-test outcome sets are IDENTICAL
left  outcome histogram: {'PASSED': 191, 'SKIPPED': 2}
right outcome histogram: {'PASSED': 191, 'SKIPPED': 2}
```

And against **master's** run (round 1's master run, under the fake vault):

```
$ python compare_junit.py junit_master_fake.xml junit_r2_final_acceptance.xml
left  : junit_master_fake.xml -> 193 testcases
right : junit_r2_final_acceptance.xml -> 193 testcases
only in left : 0
only in right: 0
different outcome: 0
RESULT: the two per-test outcome sets are IDENTICAL
left  outcome histogram: {'PASSED': 191, 'SKIPPED': 2}
right outcome histogram: {'PASSED': 191, 'SKIPPED': 2}
```

The round-trip test is in the `PASSED` set, not in the `SKIPPED` set.

### E6 - The full suite (four runs; the known native-crash hazard appeared once)

**Run 1 (green, on this round's tree before a comment-only edit):**

```
$ python -m pytest backend/tests -q -p no:warnings
1566 passed, 45 skipped in 338.34s (0:05:38)
FULL_SUITE_EXITCODE=0
```

**Run 2 (on the exact committed tree `468a17a`) - DIED with a native crash:**

```
$ python -m pytest backend/tests -q -p no:warnings
...
.......................................................ss............... [  4%]
...
........................................................................ [ 62%]
python : Windows fatal exception: access violation
Current thread 0x00006c7c [Thread-137 (validate_in_thread)] (most recent call first):
  File "...\backend\agents\delegation.py", line 1358 in get_delegation
  File "...\backend\tests\test_phase95_hardening.py", line 68 in validate_in_thread
  File "...\threading.py", line 1024 in run
FULL_SUITE_EXITCODE=-1073741819
```

This is the **known hazard** the brief describes ("2 native crashes in 6
observed full-suite runs, two kinds, neither reproducing in isolation"). It is
in `backend/tests/test_phase95_hardening.py` - a threading/atomicity test
about delegation admission control. That file contains **no** reference to
`win32cred`, `secrets` or `credential` (grep below), and the guard is
acceptance-scoped, so it cannot reach this test.

**Run 3 (re-run of run 2) - one FAILURE, in the same unrelated file:**

```
$ python -m pytest backend/tests -q -p no:warnings
...
E       assert 3 <= 2
backend\tests\test_phase95_hardening.py:92: AssertionError
FAILED backend/tests/test_phase95_hardening.py::TestAdmissionAtomicity::test_two_callers_at_boundary_only_one_admits
1 failed, 1565 passed, 45 skipped in 300.30s (0:05:00)
```

That is the **second kind** of the known non-determinism: a thread race in
`test_two_callers_at_boundary_only_one_admits` ("With 1 existing + limit 2,
only one of the two should succeed" -> the count came out 3). It reproduces
**nowhere in isolation**:

```
$ python -m pytest backend/tests/test_phase95_hardening.py -q -p no:warnings
.................................                                        [100%]
33 passed in 104.18s (0:01:44)
PHASE95_ISOLATED_EXITCODE=0

$ Select-String -Path backend\tests\test_phase95_hardening.py -Pattern "win32cred|secrets|credential|CredWrite|CredDelete" -CaseSensitive:$false
(no matches)
```

**Run 4 (the final run of this round, on the exact committed tree
`468a17a`):**

```
$ git -C ...\wt-007 log --oneline -1
468a17a test(acceptance): reject product-credential writes case-insensitively and fail closed

$ python -m pytest backend/tests -q -p no:warnings --junitxml=...junit_r2_final_full_retry2.xml
.......................................................ss............... [  4%]
........................................................................ [  8%]
........................................................................ [ 13%]
........................................................................ [ 17%]
........................................................................ [ 22%]
........................................................................ [ 26%]
........................................................................ [ 31%]
........................................................................ [ 35%]
.......................................................................s [ 40%]
s................s........ss........................s................... [ 44%]
........................................................................ [ 49%]
........................................................................ [ 53%]
........................................................................ [ 58%]
........................................................................ [ 62%]
........................................................................ [ 67%]
....................s...........ss...................................... [ 71%]
...................sssssssssssssssssssssssssssssss...................... [ 75%]
........................................................................ [ 80%]
........................................................................ [ 84%]
........................................................................ [ 89%]
...........sss.......................................................... [ 93%]
........................................................................ [ 98%]
...........................                                              [100%]
1566 passed, 45 skipped in 280.07s (0:04:40)
FULL_SUITE_RETRY2_EXITCODE=0
```

**The branch's full suite is `1566 passed, 45 skipped`, exit 0, on the exact
committed tree** - the same figure the Reviewer measured independently
(`1566 passed, 45 skipped`, exit 0). Runs 2 and 3 are recorded in full above:
they are the known native-crash / thread-race hazard, in a file unrelated to
the vault.

### E7 - The read-only enumeration, before and after the round

Read-only (`CredEnumerate` only; no blob is read; target NAMES only):

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

Identical to the state before this round: **8** credentials, **no** "Sythion"
target, the product target absent, the test-scoped target absent (the
round-trip test cleans up after itself).

One stray entry of my own appeared mid-round and was removed - it was
**test-scoped** ("sythion (synth) [test]/caseprobe"), never the product
namespace:

```
$ python cleanup_test_scoped.py
=== before cleanup ===
    ...
    sythion (synth) [test]/caseprobe
    ...
deleted test-scoped target 'sythion (synth) [test]/caseprobe'
=== after cleanup ===
    gemini:antigravity
    git:https://github.com
    zed:url=https://zed.dev
    ...
```

(The cleanup script REFUSES any target under the product prefix - see the
source in the harness directory.)

### E8 - The worktree is clean: no test deleted, skipped, xfailed or marked

```
$ git -C ...\wt-007 status --short
(empty)

$ git -C ...\wt-007 diff --name-only 4680e0b..HEAD
backend/tests/acceptance/conftest.py
```

The branch adds **zero** test functions and modifies **zero** test
functions in this round; the two probe files were copied in for their runs
and deleted (`git status --short` is empty, and they are not in the commit).

---

## The two holes

| # | Hole | Status | Evidence that it is now closed |
|---|---|---|---|
| 1 | case-sensitive prefix comparison | **CLOSED** | BEFORE: a write to `sythion (synth)/ollama_api_key` and a delete of the same target were **NOT blocked** and reached the vault layer (E1). AFTER: both are **BLOCKED** with `ProductCredentialWriteAttempt` at `conftest.py:118` (E2). The comparison is `target.lower().startswith(_PRODUCT_TARGET_PREFIX_LOWER)` (`conftest.py:117`). |
| 2 | fails open on an unreadable credential | **CLOSED** | BEFORE: `win32cred.CredWrite(object(), 0)` and `win32cred.CredWrite({"Type": ...}, 0)` (no `TargetName`) were **NOT blocked** and reached the vault layer (E1). AFTER: both are **BLOCKED** with "the target name could not be read ... The guard fails closed" at `conftest.py:111` (E2). The rule is now "reject unless you can PROVE the target is not a product target". |

---

## Both spellings, both directions

`backend/tests/acceptance/conftest.py:117`
`if target.lower().startswith(_PRODUCT_TARGET_PREFIX_LOWER):`
with `_PRODUCT_TARGET_PREFIX_LOWER == "sythion (synth)/"`.

The test-scoped prefix lower-cases to `"sythion (synth) [test]/"`, which does
**not** start with `"sythion (synth)/"` - the two differ at the character
after `Sythion (Synth)`: a space, not a `/`. So the test-scoped prefix is
still allowed in either spelling.

| target | spelling | BEFORE | AFTER | reached the vault layer? |
|---|---|---|---|---|
| `Sythion (Synth)/ollama_api_key` | product, original | BLOCKED | **BLOCKED** | no (never) |
| `sythion (synth)/ollama_api_key` | product, lower-cased | *not blocked* | **BLOCKED** | no (never) |
| `Sythion (Synth) [TEST]/caseprobe` | test-scoped, original | allowed | **allowed** | yes (spy) |
| `sythion (synth) [test]/caseprobe` | test-scoped, lower-cased | allowed | **allowed** | yes (spy) |
| `Sythion (Synth) [TEST]/ollama_api_key` | test-scoped, original, via the production store | allowed | **allowed** | yes - the branch's own round trip |
| `sythion (synth) [test]/ollama_api_key` | test-scoped, lower-cased, via the production store | - | **allowed** | yes - see below |
| `sythion (synth) [test]/caseprobe` (delete) | test-scoped, lower-cased | allowed | **allowed** | yes (spy) |
| `sythion (synth)/ollama_api_key` (delete) | product, lower-cased | *not blocked* | **BLOCKED** | no (never) |

**The lower-cased TEST-SCOPED spelling against the REAL vault** (not the spy)
- the guard allows it, the write really lands, the value really reads back,
and the delete really removes it:

```
$ python -m pytest backend/tests/acceptance/test_zz_lower_real_probe_tmp.py -q -p no:warnings -rA --tb=short
.                                                                        [100%]
PASSED backend/tests/acceptance/test_zz_lower_real_probe_tmp.py::test_probe_lower_cased_test_scoped_target_round_trip
1 passed in 0.34s
```

(The probe asserted `target == "sythion (synth) [test]/caseprobe-lower"`,
`not target.lower().startswith("sythion (synth)/")`, that the value was
non-empty **without printing it**, that the target name was visible in the
real unfiltered enumeration, and that after the delete the value was gone and
the name was no longer enumerated.)

**Why the case-insensitive comparison is the right rule - measured at a
TEST-SCOPED target only** (never the product target):

```
$ python -m pytest backend/tests/acceptance/test_zz_case_real_probe_tmp.py -q -p no:warnings -rA --tb=short -s
[CASE PROBE] after writing 'Sythion (Synth) [TEST]/caseprobe': ['Sythion (Synth) [TEST]/caseprobe']
[CASE PROBE] after writing 'sythion (synth) [test]/caseprobe': ['Sythion (Synth) [TEST]/caseprobe']
[CASE PROBE] deleted 'Sythion (Synth) [TEST]/caseprobe'
[CASE PROBE] deleted 'sythion (synth) [test]/caseprobe'
[CASE PROBE] after the deletes: []
.
PASSED backend/tests/acceptance/test_zz_case_real_probe_tmp.py::test_probe_two_spellings_are_one_slot
1 passed in 0.26s
```

Writing the lower-cased spelling did **not** create a second, separately
visible credential: the two spellings address the same slot. A
case-sensitive comparison would therefore have let a lower-cased product
target straight through to the product's own credential.

---

## What did not regress

| the Reviewer's confirmed item | round 2 result |
|---|---|
| the guard blocks 3/3 product-target attempts (store `set`, store `delete`, direct `CredWrite` with the prefix patched) | **still blocked** - plus the API-driven write: 4/4 (E3) |
| a non-delegating spy records **no** product-prefixed target | **confirmed** - the spy record holds only `Sythion (Synth) [TEST]/...` targets (E2, E3) |
| the round trip is real: 10/10 against the real vault | **confirmed** - `10 passed`, exit 0 (E4) |
| with the write neutralised the test goes RED at `test_secret_storage.py:177` | **confirmed** - `AssertionError: assert '' == 'sk-roundtrip-zz'` at `:177` (E4) |
| with the enumerator blinded the test goes RED at `:197` | **confirmed** - `Failed: the test-scoped credential was not found ...` at `:197` (E4) |
| the acceptance suite is `191 passed, 2 skipped` with identical per-test outcome sets | **confirmed** - `191 passed, 2 skipped`; the per-test outcome set is IDENTICAL to round 1's run and to master's (E5) |
| the branch's full suite is `1566 passed, 45 skipped` | **confirmed** - see E6 (run 1 green on this round's tree; run 4 green on the exact committed tree; runs 2 and 3 hit the known native-crash / thread-race hazard in an unrelated file, and are recorded in full) |
| `git status --short` in the worktree is empty - no test deleted, skipped, xfailed or marked | **confirmed** (E8) |
| the guard is complete for the namespace the product uses (only route is `backend/core/secrets.py`) | **unchanged** - I re-grepped: `CredWrite`/`CredDelete`/`CredRead` appear only in `backend/core/secrets.py` and in the guard itself |

---

## The other six findings - what I decided about each

I did **not** fix them; they are queued. My decisions and reasons:

| # | the Reviewer's finding | my decision |
|---|---|---|
| 3 | the guard is acceptance-scoped; a test outside `backend/tests/acceptance/` is unguarded | **NOT fixed.** Moving the guard to the root `backend/tests/conftest.py` is the right follow-up, but that file is contended by items 002 and 006. Untouched. |
| 4 | `backend/main.py:274`'s global `@app.exception_handler(Exception)` would turn the guard's exception into a 500 | **NOT "fixed"** (it is a production file and out of scope), but **documented in the guard's own docstring** (`conftest.py:92-96`), as the brief permits: the write would still be blocked, but the test would not go red, and no acceptance test uses `main.app` today. This is the only thing I added beyond the two fixes, and it is a comment. |
| 5 | `test_secret_storage.py:196`'s `except Exception` around the enumeration is broader than the 1168 it is about | **NOT narrowed.** It is queued, it still fails loudly (E4 shows it), and narrowing it would have meant editing `test_secret_storage.py` - which would shift the very line numbers the orchestrator cited for the RED proofs (`:177`, `:197`) and widen this round's diff beyond the two holes. I kept the file byte-identical to `4680e0b` instead. |
| 6 | the `CredWrite` wrapper signature matches the real one | **no action.** The signature is unchanged: `guarded_write(cred, flags=0)`. |
| 7 | the product prefix is snapshotted twice, in two modules | **NOT de-duplicated.** `test_secret_storage.py:35` still holds its own import-time snapshot, and `conftest.py:44` still holds the guard's. The new `_PRODUCT_TARGET_PREFIX_LOWER` (`conftest.py:58`) is **derived from the guard's own snapshot** (`.lower()` of it), not a second capture of the production constant - the test's assertion is still not a tautology. |
| 8 | doc debt in `docs/ARCHITECTURE.md:358` and `docs/ACCEPTANCE_TESTS.md:199` | **NOT touched** - `docs/` is out of scope for this item. |

---

## Findings

1. **The vault's case-insensitivity is stronger than a plain "the second
   write replaces the first".** In a first run of my case probe I saw
   something the Reviewer's transcript does not show: after writing
   `Sythion (Synth) [TEST]/caseprobe` and then
   `sythion (synth) [test]/caseprobe`, the enumeration showed ONE entry
   (`Sythion (Synth) [TEST]/caseprobe`), but a **delete with the original
   spelling then revealed the lower-cased entry as a separately stored
   credential** (`[CASE PROBE] after the delete:
   ['sythion (synth) [test]/caseprobe']`). I cleaned it up
   (`cleanup_test_scoped.py`, E7) and re-ran the probe in the safer form
   above, which deletes both spellings. The lesson for the guard is the
   same or stronger: the vault does not guarantee one entry per
   case-insensitive name, so the guard must reject **every** spelling.
   The code comment at `conftest.py:46-52` states the measured fact
   without over-claiming.
2. **The fail-closed rule has a deliberate edge: a credential dict with no
   `TargetName` is now rejected.** Nothing in the repository writes such a
   credential (`CredentialManagerStore.set` always sets `TargetName` -
   `backend/core/secrets.py:69-77`), and the full suite confirms it
   (E6). But a future test that writes a credential without a target name
   will now fail loudly instead of writing an unnamed credential. That is
   the intended trade: "reject unless you can PROVE the target is not a
   product target".
3. **`guarded_delete` is unaffected by the fail-closed change**, as the
   brief required: its target is always a plain string
   (`_cm_target(name)`), so it is always determinable. The delete path is
   still the one the Reviewer proved has teeth - `store.delete(...)` on the
   product target raises (E3, #2).
4. **The guard's exception type is still load-bearing.** It remains an
   `AssertionError` subclass, not a `pywintypes.error`, because
   `CredentialManagerStore.delete` swallows `pywintypes.error`
   (`backend/core/secrets.py:83-86`). The delete attempt in E3 fails
   loudly, which it would not have done with a `pywintypes.error`.
5. **The full suite is not deterministic on this machine, and this round
   hit both known kinds** (E6): a native access violation in
   `test_phase95_hardening.py` (run 2) and a thread-race failure in the
   same file (run 3). Both are in a file with no reference to the vault,
   and the file passes 33/33 in isolation. This is pre-existing and
   unrelated to item 007; it is the hazard the brief warned about.
6. **I reverted an experimental one-line change to
   `test_secret_storage.py`.** I had made its "not under the product
   prefix" assertion case-insensitive
   (`assert not target.lower().startswith(_PRODUCT_TARGET_PREFIX.lower())`)
   to match the guard's rule. I reverted it so the file stays
   byte-identical to `4680e0b` and the orchestrator's line citations
   (`:177`, `:197`) stay valid. The assertion is not a safety hole: the
   composed target is a constant, and the guard is the backstop - a
   mis-cased test prefix would be blocked by the guard and the test would
   go red. **If the reviewer prefers the stronger assertion, it is a
   one-line follow-up in the same file.**
7. **The `except Exception` in `_target_of` (`conftest.py:132-135`) is
   intentional and is a fail-closed catch-all**, not a swallowed error: any
   exception while reading the target means "unknown target", which is
   rejected.

---

## Not checked

* **Whether the product credential ever existed**, and whether an earlier
  full-suite run in an earlier session deleted it. Not determinable from
  the repository, and this fix deliberately does not depend on it. The
  operator's READ-ONLY check remains outstanding and belongs to the
  orchestrator.
* **Any credential VALUE.** No blob was ever read, printed or stored. Only
  target names were listed.
* **A real-vault reproduction of the BEFORE behaviour.** Deliberately not
  done: it would have meant writing to (and deleting) the product target -
  the exact harm this item exists to prevent. The BEFORE proof uses a
  non-delegating spy, which records the target that *would* have been
  written and makes the real vault unreachable by construction.
* **The non-Windows path of the guard** (the fixture's `ImportError`
  no-op) - only the Windows path was exercised.
* **The CI runner (Python 3.11) and pywin32 311.** Every measurement here is
  CPython 3.14.7 / Windows with pywin32 312 (the repo pins 311).
* **`win32cred.CredRename` / `CredWriteDomainCredentials`** - the
  Reviewer's measurement stands; I did not re-measure them.
* **The root `backend/tests/conftest.py` guard** (deferred: items 002 and
  006 contend for that file).
* **`docs/ARCHITECTURE.md` and `docs/ACCEPTANCE_TESTS.md`** - not touched
  (finding 8).
* **`.orchestra/follow-ups.md`** - not touched; the orchestrator owns it.
* **The other items in flight** (002, 006) - their worktrees and branches
  were not touched.
* **The repository's CogniGraph memory protocol** (`AGENTS.md`). The memory
  MCP server's tools are not exposed to this session's tool catalogue (I
  searched it: `search({query: "memory"})` returned an empty list), so
  `memory_stats` / `memory_query` / `memory_prepare_task` could not be
  called and nothing was written to the memory store. I read the brief,
  the review, the plan, the backlog and round 1's report directly instead.
* **The master-side run of the offending test** - deliberately not
  repeated in this round; round 1 already established it with a fake
  vault, and the guard's behaviour on master is unchanged by this diff
  (the guard is acceptance-scoped and this diff only changes the guard).

---

## Reproducing this report

```powershell
# harness (outside the repository)
$env:PYTHONPATH="C:\Users\orcha\AppData\Local\Temp\opencode\wt007-harness"

cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-007

# the two holes, before and after (the spy makes the real vault unreachable)
Copy-Item ...\wt007-harness\probe_hole.py  backend\tests\acceptance\test_zz_hole_probe_tmp.py
Copy-Item ...\wt007-harness\probe_teeth.py backend\tests\acceptance\test_zz_teeth_probe_tmp.py
python -m pytest backend/tests/acceptance/test_zz_hole_probe_tmp.py backend/tests/acceptance/test_zz_teeth_probe_tmp.py -q -p vault_spy2
Remove-Item backend\tests\acceptance\test_zz_hole_probe_tmp.py,backend\tests\acceptance\test_zz_teeth_probe_tmp.py

# the round trip is still real
python -m pytest backend/tests/acceptance/test_secret_storage.py -q                    # 10 passed (real vault)
python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p vault_spy2      # RED at :177
python -m pytest backend/tests/acceptance/test_secret_storage.py -q -p blind_enum      # RED at :197

# the suites
python -m pytest backend/tests/acceptance -q                                          # 191 passed, 2 skipped
python -m pytest backend/tests -q                                                    # 1566 passed, 45 skipped
```

Nothing was pushed. No production module was changed. The product credential
was never written, deleted or read.
