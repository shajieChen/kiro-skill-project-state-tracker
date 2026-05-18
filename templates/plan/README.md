# plan/

Execution plans built on top of Decisions (and, by extension, Research).

## Rules

- A Plan records **execution steps and structure**, not investigation.
- A Plan must be grounded in at least one Decision or one explicit Research finding.
- Do **not** copy long Research excerpts into a Plan file. Reference by ID.
- A Plan can be `invalidated` by a new Research finding or by a Decision change.
- Every Plan artifact must be registered in `status.yaml#artifacts` with `type: plan`.

## Recommended filenames

```
plan.md                          # top-level plan, single-file projects
P-001-rendering-pipeline.md
P-002-implementation-milestone.md
```

## Recommended file structure

```
---
id: Plan.rendering_pipeline
type: plan
based_on_decisions: [D-001]
based_on_research: [R-001]
---

# Plan: Rendering Pipeline

## Goal
One paragraph.

## Steps
1. ...
2. ...

## Definition of Done
- ...
```

## Relationship to status.yaml

- Registered as an artifact entry with `type: plan`.
- Status transitions: `draft → reviewed → approved → (possibly) needs_update | invalidated`.
- When `status: approved`, this Plan unblocks LandingPrompts that depend on it (subject to their other preconditions).
