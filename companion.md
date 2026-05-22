# Project State Tracker — Companion Reference

Loaded on-demand by PST SKILL.md when executing §4 pipeline steps. Discard from working memory after the matching step completes.

# Companion Reference

> Load sections on-demand per §6 pointer table. Discard after the step completes.
> Step 4 confidence thresholds: high ≥90% auto-approve; medium 60–89% approve+flag; low <60% hold (report in §5 "Recommended Next Actions").
> Step 2 failure: never skip a changed file — produce a partial record with `requires_agent_review: true`.

## Part C: §6 Reference Rules (Full Text)

### §6A ID & Title Extraction

**ID** (first match wins):
1. Filename pattern: `R-001-*.md` → `R-001`, `D-001-*.yaml` → `D-001`, `LP-001-*.md` → `LP-001`, `TP-001-*.md` → `TP-001`
2. YAML front-matter `id:` field
3. H1 with prefix: `# R-001: Title` → `R-001`; `# Plan.<topic>: Title` → `Plan.<topic>` (project-state-spec convention)
4. Fallback: slugify → `Plan.<stem>`, `LP.<stem>`, `TP.<stem>`

**Title** (first match): YAML `title:` → H1 text → humanized filename

**Type** from directory: research/ → research_finding, decisions/ → decision, plan/ → plan, prompts/landing/ → landing_prompt, prompts/test/ → test_prompt

**External artifacts** (path starts with `external:`): These are managed by Execute-LandingPrompt. Their IDs follow the same resolution order above. During AUDIT, skip file-based checks for external artifacts — trust the last ELP-authored change_event as ground truth.

### §6B Dependency Inference (4-Step Process)

**Step A — Reference Scan:** Find artifact IDs in body text.
- Patterns: `R-\d{3}`, `D-\d{3}`, `Plan\.\w+`, `LP-\d{3}`, `TP-\d{3}`, `HC-\d{3}`

**Step B — Semantic Markers:** Keywords near IDs:
- "based on R-001" → depends_on
- "implements D-002" → depends_on
- "invalidates A-001" → invalidates
- "affects Plan.*" → affects
- "requires LP-001 output" → consumes_handoffs

**Step C — Structural Inference** (no explicit markers):
- Decision→R-* = based_on; Plan→D-* = depends_on; LP→Plan.* = depends_on; TP→LP-* = depends_on

**Step D — Confidence Scoring:**
- Explicit semantic marker = high → auto-register
- ID in body, no marker = medium → `requires_agent_review: true`
- Structural only = low → suggest in report only

### §6C Precondition Generation

Per LP:
- Each upstream Plan P → PC: `P.status ∈ [approved, ready]`
- Each downstream TP → PC: `TP.status ∈ [ready]`
- Each consumed HC → PC: `HC.status ∈ [available, consumed], version ≥ 1`

Generate Gate G-001 if any LP has PCs. Sequential IDs: PC-001, PC-002...

**Incremental PC generation (closes gap: PSS-added LPs after INIT):**
- INIT (§3A) generates PCs for every LP found.
- AUDIT (§4) MUST scan for LPs whose artifact exists but have **zero** matching `preconditions[*] WHERE target == <LP id>` and ADD missing PCs using the rules above. This covers LPs added by project-state-spec or by manual user edits post-INIT.
- Existing PCs are NEVER mutated by AUDIT (only their `status` field is recomputed). The "add missing only" rule preserves user-edited PC overrides.
- New PCs append to `preconditions[]` with sequential ids; if Gate G-001 doesn't exist yet but at least one PC now exists, create G-001 in the same audit cycle.
- This MUST run before Step 3 (`propagate.py`) so propagation sees the new PCs.

### §6F Handoff Context Management

When LP referenced downstream and no HC exists:
- Suggest: `{id: HC-<next>, producer: LP.id, status: draft}`
- Extract facts: bullets under Summary/Output/Results (max 3)
- Extract constraints: sentences with "must not"/"required"/"constraint" (max 3)
- Set consumed_by from downstream depends_on
- Mark `requires_agent_review: true`

Never auto-bump HC versions — EXCEPT when Execute-LandingPrompt writes a HC update with actually-changed facts/constraints content (verified by content diff, not just re-execution).

**ELP-authored HCs:** When `source: Execute-LandingPrompt` in the change_event that created/updated an HC, treat the HC as validated (ELP extracted facts from actual execution). Do not mark `requires_agent_review`.

**Pending-consumers backfill (closes dangling-HC gap, executed as AUDIT §4 Step 2.6):**

ELP may write `handoff_contexts[*].pending_consumers: ["<token>"]` when the next-LP token in `## LP 序列` has not yet been registered as an artifact (typical when PSS scaffolded only the current LP but the downstream LP arrives in a later stage, or when the token is a free-form filename stem). The backfill rule:

1. For every HC `H` in `status.yaml`, for every token `t` in `H.pending_consumers[]`:
2. Resolve `t` against `artifacts[*].id` using the §6A ID-resolution order (filename pattern → fm `id:` → H1 prefix → fallback slug). The match MUST be against an artifact whose `id` is the canonical id (not the raw filename token).
3. If resolved → append `t` (the resolved id) to `H.consumed_by[]` (skip if already present), append `{consumer: <resolved id>, status: "pending", consumed_version: null, consumed_at: null}` to `H.consumed_status[]` (skip if a row for the same consumer already exists), and remove `t` from `H.pending_consumers[]`.
4. If unresolved → leave `t` in `H.pending_consumers[]`. The next AUDIT will retry.
5. This MUST run AFTER §6C Step 2.5 (so PSS-added LPs are registered and discoverable) and BEFORE Step 3 (`propagate.py`), so propagation sees the corrected `consumed_by[]`.

Existing `consumed_by[]` and `consumed_status[]` entries are never mutated by backfill (it is an append-only operation). This preserves any user-edited consumer overrides.

### §6G status.yaml Schema

Full structural constraints (required fields, valid statuses, ID prefixes) live in the machine-readable schema file:

```
tools/_schema_default.yaml
```

When tools and this schema disagree, **tools are the truth**.

**Top-level structure (quick reference):**

```yaml
meta: {project_name, schema_version, created, last_updated, last_run, source_root?, scope?[], pst_root?, coding_standards?, summary:{...}, pointers:{...}}
project: {name, phase, version}
artifacts: [{id, type, path, status, depends_on[], group?, produces_handoffs?[], consumes_handoffs?[], last_checked?}]
research_findings: [{id, title, path, status, evidence?[], affects?[]}]
decisions: [{id, title, path, status, based_on[], rejects?[], affects?[]}]
blockers: [{id, title, severity, status, blocks[]}]
gates: [{id, name, status, checks[{id, description, status}]}]
preconditions: [{id, target, requires[{artifact|handoff, field, condition}], status}]
handoff_contexts: [{id, title?, producer, producer_type?, version, status, facts[], constraints[], consumed_by[], consumed_status[{consumer, status, consumed_version, consumed_at}], pending_consumers?[]}]
change_events: [{id, time, source, event_type, summary?, affected[], transitions[{artifact, from, to, reason}]}]
evidence: []
assumptions: []
dependencies: []
rules: {research_is_fact_only, status_is_single_source_of_truth, landing_requires_test_ready, plan_invalidates_landing, landing_invalidates_test}
snapshots: {enabled, git_baseline, file_hashes}
```

**Block authority:**
- `validate_status.py` — authoritative consumer for `project`, `rules`, `evidence`, `assumptions`, `dependencies`, `snapshots`.
- `render_status.py` — authoritative consumer for `meta.summary` and `meta.pointers`.
- `apply_changes.py` — authoritative writer for `artifacts`, `handoff_contexts`, `change_events`.
- ELP — authoritative writer for `meta.source_root`, `meta.scope`, `meta.pst_root`, `meta.coding_standards`.

If you add a new block, update both `_schema_default.yaml` and the consuming tool — they must move together.

**Legacy compatibility (facts/constraints):** `handoff_contexts[].facts` / `.constraints` accept BOTH structured dict `{id, statement, source, confidence?}` AND legacy bare-string format. PST tools MUST guard with `isinstance(x, dict)` before `.get()`. ELP v3+ always writes structured.

### §6H Group Derivation Rules

**Purpose:** Assign a `group` field to each artifact for dashboard column grouping. Runs during INIT §3A (Step 2.5) and AUDIT §4 (Step 2, for artifacts missing `group`).

**Priority (per artifact):**
1. If `group` is already non-empty → skip (user-declared, never overwrite)
2. If `type == "plan"` → `group = extract_topic(id)`
3. If `type in ("landing_prompt", "test_prompt")` → walk `depends_on` to find root Plan → use that Plan's group
4. Otherwise → `group = "Other"`

**`extract_topic(plan_id)`:**
1. Strip `Plan.` prefix (case-sensitive)
2. If remainder matches `Phase\d+.*` → return `Phase{N}` (e.g., `Plan.Phase1-BitStream` → `Phase1`)
3. If remainder contains `codegen` (case-insensitive) → return `CodeGen`
4. Otherwise return remainder as-is (e.g., `Plan.PR3_Runtime` → `PR3_Runtime`)

**`find_root_plan(artifact)` — Dependency Chain Walk:**
1. BFS through `depends_on[]` (resolve IDs against `artifacts[]`)
2. Max depth: 5 (prevents infinite loops)
3. Return the first artifact with `type: plan` encountered
4. If multiple Plans at same depth, use the first in `depends_on` order
5. If no Plan found → return None

**`extract_topic_from_id(artifact_id)` — Orphan LP/TP Fallback:**
Used only when `find_root_plan` returns None.
1. Strip known type prefixes: `LP.`, `TP.`, `LandingPrompt.`, `TestPrompt.`, `Plan.`
2. Strip leading numeric prefix + separator (regex: `^\d+[_-]`)
3. Extract first underscore-or-hyphen-delimited segment as candidate
4. Search all Plan artifacts for one whose `extract_topic(plan.id)` starts with or equals candidate
5. If found → return that Plan's group
6. If not found → return None (artifact gets `group: "Other"`)

**Constraints:**
- MUST NOT modify any artifact field other than `group`
- MUST NOT overwrite existing non-empty `group` values
- MUST run AFTER dependency inference (§6B) so `depends_on` is available
- Max BFS depth: 5