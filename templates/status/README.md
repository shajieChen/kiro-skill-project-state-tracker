# status/

The state database for this project. **`status.yaml` is the single source of truth.**

## Rules

- `status.yaml` records: state, dependencies, gates, blockers, evidence references, and impact relationships.
- It does **not** duplicate the body of research or plan files. Only IDs, paths, and propagation hints.
- Every artifact (Plan, LandingPrompt, TestPrompt, Decision, Research) is registered here.
- The skill's propagation rules read from this file. If state isn't in this file, the skill can't reason about it.

## Files

- `status.yaml` — the database.
- `schema.yaml` — structural constraints used by `tools/validate_status.py`.
- `.cache/` — intermediate JSON files produced by `scan_changes.py`, `propagate.py`, and the agent. **Add `status/.cache/` to your `.gitignore`.**

## Editing rules

- You may hand-edit `status.yaml` for fields that the skill doesn't compute (titles, descriptions, severity, manual annotations). Always do this in a separate commit from skill-driven runs to keep diffs reviewable.
- Do **not** hand-edit `change_events` or `snapshots.file_hashes` — these are managed by `apply_changes.py`.
- Do **not** silently bump an artifact to `ready` — pass through the propagation flow so a `change_event` is recorded.

## Suggested `.gitignore` entry for this folder

```
status/.cache/
```


## HandoffContext index

`status.yaml` now serves three roles:

1. **State database** — artifact / blocker / gate / precondition statuses.
2. **Dependency database** — depends_on / blocks / affected_by edges.
3. **Handoff index center** — `handoff_contexts[]` records the cross-Prompt facts, results, and constraints plus which consumers have absorbed which version.

`handoff_contexts` records **summaries and references only** — never copy the full text of a Research file, a producer's design doc, or a generated artifact into status.yaml. Each handoff fact, constraint, and result must include a `source` field pointing back to the canonical file.
