# tools/

Scripts for status-database maintenance, dependency analysis, and view rendering.

## Scripts

| Script | Purpose |
|--------|---------|
| `scan_changes.py` | Detects changed files since the last skill run (git diff preferred, hash fallback). Writes `status/.cache/changed_files.json`. |
| `validate_status.py` | Checks `status.yaml` against `schema.yaml`. Exits non-zero on structural violations. |
| `propagate.py` | Reads status.yaml + changed_files.json, applies the propagation rules, writes `status/.cache/candidate_transitions.json`. **Mechanical only — does not edit status.yaml.** |
| `apply_changes.py` | Reads `approved_transitions.json` (written by the agent after reviewing candidates), merges into status.yaml, writes `change_events`. **Preserves all user-authored fields.** |
| `render_status.py` | Regenerates every file under `views/` from status.yaml. Safe to re-run any time. |
| `_yaml_compat.py` | Internal helper. Tries to import PyYAML; if missing, falls back to a tiny built-in parser that handles the subset used by this skill. |

## Hard rules

- No script ever silently overwrites a user-authored file under `research/`, `decisions/`, `plan/`, `prompts/`, or `views/X.md` that has been manually edited beyond the auto-generated header.
- `apply_changes.py` writes `status.yaml` atomically (temp file + rename) to avoid corruption on crash.
- `render_status.py` always overwrites `views/*.md` because those are explicitly read-only generated output.

## Running

The skill expects to run scripts from the skill's own `tools/` directory (`<SKILL_DIR>/tools/`). A copy is mirrored into the project's `tools/` for self-containment, but the canonical runners live with the skill.

```
python <SKILL_DIR>/tools/scan_changes.py    --project <project_root>
python <SKILL_DIR>/tools/validate_status.py --project <project_root>
python <SKILL_DIR>/tools/propagate.py       --project <project_root>
# agent reviews .cache/candidate_transitions.json, writes .cache/approved_transitions.json
python <SKILL_DIR>/tools/apply_changes.py   --project <project_root>
python <SKILL_DIR>/tools/render_status.py   --project <project_root>
```


## Handoff tooling

- `render_status.py` regenerates `handoff_view.md` and `prompt_chain_view.md` in addition to the original five views, and augments `landing_prompt_checklist.md` with handoff precondition rows.
- `validate_status.py` enforces handoff invariants: ID uniqueness with `HC-` prefix, producer/consumer references resolve, every fact and constraint has a `source`, every result has a `path` or `summary`, `ready` artifacts may not consume handoffs in `stale|invalidated|deprecated|archived`.
- `handoff.py` is the shared helper module — pure functions only, no I/O.
