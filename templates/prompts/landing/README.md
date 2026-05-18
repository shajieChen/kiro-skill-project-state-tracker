# prompts/landing/

Prompts used to actually land (implement) work in the codebase.

## Rules

- Every LandingPrompt must have one or more `preconditions` in `status.yaml#preconditions`.
- A LandingPrompt **cannot** be marked `ready` while:
  - any precondition is `failed`, or
  - any gate it depends on is `failed`, or
  - its corresponding TestPrompt is not `ready`, or
  - any blocker that lists it under `blocks` is `open` with `severity: high|critical`.
- When a LandingPrompt is edited, the matching TestPrompt automatically becomes a candidate for `needs_update`.
- A LandingPrompt does **not** directly modify a Plan. If a Plan needs changing, that's a Plan-level update.

## Recommended filenames

```
LP-001-scene-implementation.md
LP-002-ui-generation.md
```

## Recommended file structure

```
---
id: LandingPrompt.scene_implementation
type: landing_prompt
depends_on:
  - Plan.rendering_pipeline
  - TestPrompt.scene_acceptance
---

# LandingPrompt: Scene Implementation

## Context for the executor
Briefly: what to implement, where, with what constraints.

## Prompt body
(the actual prompt text to give to the implementation agent)
```

## Relationship to status.yaml

- Registered as an artifact entry with `type: landing_prompt`.
- Its `preconditions` entries are mechanically re-evaluated on every skill run.
- Status flow: `draft → reviewed → approved → (when preconditions pass) ready`. If any dependency changes, the script will mark it `blocked` or `needs_update` for agent review.


## Handoff produce / consume

A LandingPrompt may declare:

- `produces_handoffs: [HC-xxx]` — outputs that downstream prompts will consume.
- `consumes_handoffs: [HC-xxx]` — inputs that this LP requires upstream.

**Before execution**, the agent must check every entry in `consumes_handoffs` and confirm the corresponding precondition is `passing`. The LP cannot enter `ready` while any required handoff is `stale`, `invalidated`, `deprecated`, or `archived`.

**After execution**, if the LP produced a meaningful result, the agent must either register a new HC (`handoff_register`) or bump the existing HC's version (`handoff_version`) and mark the producer's consumers as needing to re-absorb.
