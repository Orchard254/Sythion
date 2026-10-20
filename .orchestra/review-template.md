VERDICT: ACCEPT

<!--
Copy this file to .orchestra/reviews/<id>-rN.md and edit it.
The first line MUST be exactly `VERDICT: ACCEPT` or `VERDICT: IMPROVE`.
Delete this comment block in the copy.
-->

# Review of <id> round <N>

## Acceptance criteria
| # | Criterion | Result (met / not met / not verified) | Evidence |
|---|---|---|---|
| 1 | | | |

## Goal fit
<!-- Does this serve goal.md? Simpler way? Scope creep? -->

## Sensitivity
<!--
Run: python3 <skill-dir>/scripts/classify.py <base>...<branch>
Tier: NORMAL | EXTERNAL | SENSITIVE | SENSITIVE+EXTERNAL
If SENSITIVE, name the pinned invariant and the check that pins it:
  invariant:
  pinned by: <test name / exhaustive domain / provably behaviour-preserving diff>
  verified: yes | no
If EXTERNAL, list the outside action queued in follow-ups.md.
-->

## Findings (most important first)
1. <file:line> - problem - what to change

## Deferred
<!-- Anything left for the human, and why it was not done here. -->

## Needs human?
<!-- The default is no. Only if the change cannot be made safely in-repo. -->

## Not checked
<!-- REQUIRED. What you did not inspect, and why. An empty section is a
     finding, not a sign of thoroughness. -->
