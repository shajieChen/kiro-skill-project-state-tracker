# prompts/test/

Prompts used to verify, regress, and accept implementation work.

## Rules

- A TestPrompt should cover the key observable behaviors of its corresponding LandingPrompt.
- When a Plan or LandingPrompt changes, the matching TestPrompt becomes a candidate for `needs_update`.
- A TestPrompt being `ready` is a **prerequisite** for its LandingPrompt being `ready`.
- A TestPrompt does not depend on the LandingPrompt being approved before it can itself be `ready`.

## Recommended filenames

```
TP-001-scene-acceptance.md
TP-002-regression-check.md
```

## Recommended file structure

```
---
id: TestPrompt.scene_acceptance
type: test_prompt
covers:
  - LandingPrompt.scene_implementation
---

# TestPrompt: Scene Acceptance

## What to verify
- Behavior 1: ...
- Behavior 2: ...

## Prompt body
(the actual prompt text used to run verification)
```

## Relationship to status.yaml

- Registered as an artifact entry with `type: test_prompt`.
- Status flow: `draft → reviewed → ready → (possibly) needs_update`.
- When a TestPrompt transitions to `ready`, the matching LandingPrompt's preconditions are recomputed and may become eligible for `ready`.


## Handoff verification

A TestPrompt may declare `consumes_handoffs: [HC-xxx]` to indicate that it verifies behavior described in that handoff. When the LP under test ships a new HC version, the TP enters `needs_update` until it has been rewritten to cover the new facts and constraints.

TestPrompts should explicitly check that:

- the LP under test honored every `constraint` in the consumed handoff;
- the LP under test produced outputs at the paths declared in `results`;
- the LP under test did not silently drop any `fact` the downstream chain depends on.
