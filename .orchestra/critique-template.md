# Critiques

<!-- The orchestrator's judgement of every sub-agent report. Append-only.
     One block per report. A report you have not critiqued does not count as
     done: this file is where the run's quality is actually decided, and
     where a human can audit it. -->

## <timestamp> - <role> - <id> - <ACCEPT|REDO|REASSIGN|ESCALATE>
- brief: briefs/<id>-<stage>-<n>.md
- report: reports/<id>-<stage>-<n>.md
- evidence: <does every claim carry evidence? which do not?>
- coverage: <did it do everything the brief asked? what is missing?>
- consistency: <does it agree with the repo state and the other roles' reports?>
- not checked: <did the report say what it did not check?>
- verdict: <ACCEPT | REDO | REASSIGN | ESCALATE>
- reason: <one or two sentences, and what happens next>
