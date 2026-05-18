# outputs/

Concrete artifacts produced by LandingPrompt execution that downstream consumers
reference through HandoffContext entries (see `status/README.md` →
HandoffContext, and `prompts/landing/README.md`).

## What lives here

- Design documents written by upstream LandingPrompts (e.g. rendering pipeline
  design).
- Generated code directories that downstream Prompts must respect as a frozen
  interface.
- Result summaries that downstream TestPrompts validate against.

## What does NOT live here

- Free-form working notes (use the LandingPrompt file itself).
- Raw Research findings (those live under `research/`).
- Decision records (those live under `decisions/`).

## Relationship to status.yaml

Each file or subdirectory under `outputs/` that another artifact must reference
should be listed in `status.yaml` under a HandoffContext's `results:` array, for
example:

```yaml
handoff_contexts:
  - id: HC-001
    title: Rendering pipeline implementation handoff
    producer: LandingPrompt.rendering_pipeline_design
    status: available
    version: 1
    results:
      - id: HR-001
        type: design_doc
        path: outputs/rendering_pipeline_design.md
        summary: Selected rendering path and component boundaries.
```

`validate_status.py` warns if a `results[].path` does not exist on disk; it does
not delete the entry — that decision is the agent's, via an explicit handoff
status transition (e.g. `invalidated`).

## Naming

No enforced naming convention here — let the producer pick clear paths. Prefer
referencing them by path through HandoffContext rather than by encoding meaning
in filenames.
