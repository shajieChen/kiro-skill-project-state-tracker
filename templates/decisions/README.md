# decisions/

Engineering choices made on top of Research. The bridge between Research and Plan.

## Rules

- A Decision records: which approach was chosen, why, what was rejected, and which Research it depends on.
- A Decision does **not** include the full investigation — that belongs in `research/`.
- Each Decision has an ID, e.g. `D-001`.
- Decisions are YAML so they can be diffed precisely.

## Recommended filenames

```
D-001-rendering-strategy.yaml
D-002-prompt-generation-policy.yaml
```

## Recommended structure (this is what goes inside `D-001-rendering-strategy.yaml`)

```yaml
id: D-001
title: Use simplified rendering pipeline
status: draft
based_on:
  - R-001
  - E-001
rejects:
  - alternative: full_rendering_pipeline
    reason: Exceeds target platform memory budget per R-001.
affects:
  - Plan.rendering_pipeline
rationale: |
  Brief, decision-focused. One short paragraph. Do not duplicate research notes here.
```

## Relationship to status.yaml

- Each Decision has an entry in `status.yaml#decisions`.
- A Decision's `status` follows the same 9-state enum as artifacts (typically `draft → reviewed → approved`).
- When a Decision changes, every Plan that lists it in `depends_on` becomes a candidate for `needs_update`.
