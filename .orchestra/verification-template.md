VERDICT: FIXED

<!--
Copy this file to .orchestra/verifications/<id>-vN.md and edit it.
The first line MUST be exactly `VERDICT: FIXED`, `VERDICT: NOT FIXED` or
`VERDICT: REGRESSED`.
Delete this comment block in the copy.
-->

# Verification of <id> round <N>

## The problem, and whether it is gone
<!-- The outcome, not the diff. The Reviewer already looked at the diff. -->
- Reproduction on <base> (must fail):
- Reproduction on the branch (must pass):
- Verdict on the original problem:

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
|---|---|---|---|
| 1 | | | |

## Did it break anything else?
- Full suite: <command and result>
- Neighbouring behaviour checked: <what and how>
- Public surface changed: <yes/no, and what>

## Invariant
<!-- If the fix depends on an invariant, name it and verify it by name. -->
- Invariant:
- Verified by:

## Needs a human?
<!-- Only if a human is genuinely the only way to decide: visual behaviour, a
     real device, a third-party integration, performance under real load, a
     business rule. Say what they would need to do. Do not fill this in
     reflexively - a refactor, a test-only change or a doc change does not
     need a human. -->

## Not checked
