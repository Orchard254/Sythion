# Brief: scout - 000 - cycle 3 (fold R-025..R-027 + revisit tractable follow-ups)

- Item: 000 Scout pass (cycle 3)
- Role: scout (no code writes)
- Goal: turn the three new research candidates (R-025, R-026, R-027
  in `.orchestra/reports/000-research-4.md`) into ranked backlog items,
  and re-evaluate the code-tractable follow-ups the researcher flagged as
  "still code-tractable", picking at most TWO of them as new items.
  Do NOT re-open resolved/human items.

## Read

- `C:\Sythion\sythion\.orchestra\goal.md`
- `C:\Sythion\sythion\.orchestra\research.md` (do not edit it)
- `C:\Sythion\sythion\.orchestra\reports\000-research-4.md`
- `C:\Sythion\sythion\.orchestra\backlog.md` (current, stale)
- `C:\Sythion\sythion\.orchestra\board.json`
- `C:\Sythion\sythion\.orchestra\critiques.md` (tail)
- `C:\Sythion\sythion\.orchestra\follow-ups.md`

## Write

- `C:\Sythion\sythion\.orchestra\reports\000-scout-3.md` (this report)
- Overwrite `C:\Sythion\sythion\.orchestra\backlog.md` with a fresh ranked
  backlog containing ONLY: R-025, R-026, R-027, plus at most two of the
  code-tractable follow-ups (your pick, with justification), in value order,
  then the deferred human items referenced instead of re-listed. Each item
  keeps the standard fields (Why, Acceptance, Size, Risk, Likely files,
  Sensitivity, Domain, Status). Mark new items `proposed`.
- Update `C:\Sythion\sythion\.orchestra\board.json` items with new ids
  continuing from 017 (017, 018, ...). Set status `proposed`. Mirror merged
  statuses for already-merged items as needed (only ADD new entries; do not
  rewrite history).

## Constraints

- No code edits, no branches, no commits.
- New item ids start at 017. Sensitivity/domain per classify spirit.
- Do not resurrect resolved items. Do not re-add human/external items as
  code items.
- Keep the backlog compact.

## Budget

The original run budget (6) is long exceeded; treat this as a continuation
run. Do not cap at 6; instead keep the backlog to at most 5 ready items.