# views/

**Read-only generated views** derived from `status/status.yaml`.

## Rules

- Do not edit files in this directory. Every run of `tools/render_status.py` overwrites them.
- If a view looks stale, regenerate it: `python tools/render_status.py --project .` (or re-invoke the skill).
- `views/` is **not** a state source. The state source is `status/status.yaml`.

## Files

| File | Content |
|------|---------|
| `status_report.md` | Top-level dashboard. Counts per status, open blockers, failed gates, ready/blocked landing prompts, needs_update test prompts, recent change_events. |
| `blocker_view.md` | All open blockers with severity, source, blocked artifacts, gate dependencies, suggested resolution order. |
| `landing_prompt_checklist.md` | Per-LandingPrompt: status, dependent Plan, dependent TestPrompt, precondition check results, executable yes/no with reasons. |
| `dependency_graph.md` | Mermaid graph of the full dependency tree. |
| `change_impact_report.md` | Most recent change_event: files changed → directly affected artifacts → indirectly affected artifacts → before/after state → suggested next actions. |


## Handoff views

- `handoff_view.md` — every HC with producer, consumers, status, version, facts/results/constraints, and per-consumer absorption state. Highlights stale/invalidated handoffs and version drift.
- `prompt_chain_view.md` — Mermaid graph of LP → HC → LP/TP edges, plus a list of chain breakpoints and the suggested next executable prompt.

Both views are fully derived from `status.yaml`. Re-run `tools/render_status.py` after any apply step.
