# Follow-ups for the human

Things the team cannot or must not do itself: deploys, publishes, migrations
against real data, secret rotation, live config, manual checks.

Format: `- [ ] (item-id) [deferred|required] what to do`

- `[deferred]` - safe to do later, the run did not wait on it and nothing is
  broken until it happens.
- `[required]` - must happen before the change is live. The in-repo work is
  still merged; this only says when the outside action has to land.

Nothing here blocks the run. The orchestrator merges the in-repo part and
keeps going.
