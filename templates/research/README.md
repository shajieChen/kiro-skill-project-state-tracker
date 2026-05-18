# research/

Fact-only layer. Findings, evidence, and investigation conclusions live here.

## Rules

- Research records **facts and findings only**. It does not record project state.
- Research does **not** directly decide whether a Plan, LandingPrompt, or TestPrompt is ready.
- Every important finding has an ID, e.g. `R-001`. Use it in filenames and in `status.yaml`.
- Every important piece of evidence has a label, e.g. `E-001`, registered under `status.yaml#evidence`.
- If a finding overturns a prior assumption or decision, declare `invalidates: [A-xxx, D-xxx, ...]` in the file's front matter or body.
- If a finding affects specific artifacts, declare `affects: [Plan.xxx, LandingPrompt.xxx, ...]`.
- Research files are append-only in spirit — replace by adding a new R-file that `invalidates` the old one, rather than rewriting in place.

## Recommended filenames

```
R-001-platform-limit.md
R-002-rendering-pipeline.md
R-003-ai-agent-capability.md
```

## Relationship to status.yaml

- Each research finding has an entry in `status.yaml#research_findings`.
- The `affects` and `invalidates` fields drive the propagation rules in §14 of the skill design.
- **Do not paste the full body of a research file into status.yaml.** Only the ID, title, path, evidence labels, and propagation hints belong there.

## Minimal file front matter (recommended)

```
---
id: R-001
title: Target platform rendering limitation
confidence: high
evidence: [E-001]
affects: [Plan.rendering_pipeline]
invalidates: [A-001]
---

# Body — the actual findings, references, citations, raw data.
```
