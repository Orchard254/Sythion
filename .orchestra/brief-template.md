# Brief: <stage> - <id> - attempt <n>

<!-- What the orchestrator dispatched. Written BEFORE the sub-agent is
     dispatched, so the delegation is auditable and a retry can be compared
     against the attempt that failed. -->

- Item: <id> <title>
- Role: <researcher | scout | planner | implementer | reviewer | verifier | committer | librarian>
- Goal (the slice that matters): <from goal.md>

## Read
- <exact files to read>

## Write
- <exact files it may write>

## Context
- Current status: <status in board.json>
- Branch: <branch, or n/a>
- Plan: <path, or n/a>
- Previous findings: <the last review or verification this is a redo of>

## Constraints
- Safety rules that apply to this stage
- Out of scope for this stage
- Do not push, deploy, publish, or touch real data.
- Do not run destructive commands.
- Do not ask the user anything. If something needs a decision you cannot
  make, say so in the report and stop.

## Output contract
- Write <file> with this exact shape: <sections>
- Every claim needs evidence: a command and its output, a file:line, a test
  name, a URL with a date.
- End with a "Not checked" section listing what you did not verify.

## Previous attempt (if any)
- <what the last attempt did wrong, so this one does not repeat it>
