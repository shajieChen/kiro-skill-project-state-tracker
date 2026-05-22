# Project State Tracker — §11 REVIEW Mode (companion)

Loaded on-demand by PST SKILL.md when user triggers "review" / "审计质量" / "quality check" / "review landing quality" / "review quality". Discard from working memory after the review completes.

---

## §11 REVIEW Mode — Landing Quality Audit

Read-only quality audit of ready LandingPrompts. Traces each ready LP back to its Plan and Decision, then evaluates AC satisfaction and architecture conformance through document-based reasoning.

**Core constraints:**
- Only reads — never modifies status.yaml, never triggers propagate/apply_changes
- Only audits `status == "ready"` LP artifacts
- Agent reads code and Result files for judgment; tool only extracts structured context
- Does not execute tests or run any code

### Preconditions

- `status/status.yaml` exists
- At least one LP artifact with `status == "ready"`

### Tool Location (review_quality.py)

**Resolution order (stop at first hit):**

1. Check `<pst_root>/tools/review_quality.py` — the project's own tools directory (deployed via §3C INIT)
2. If not found → use `C:\Users\chenshajie\.kiro\skills\project-state-tracker\tools\review_quality.py`

Step 1 uses the same `<pst_root>` resolved from `meta.pst_root` in status.yaml (or the project path passed to the skill).
Step 2 is the hardcoded canonical fallback — always exists on this machine.

**Do NOT search the workspace.** Only check these two paths in order.

### Pipeline

| Step | Action | Input | Output | Fail |
|------|--------|-------|--------|------|
| 1 | Run `python <resolved_path> --project <p>` where `<resolved_path>` follows Tool Location resolution above | project path | `status/.cache/review_context.json` | No ready LP → report "无可审计对象" and exit |
| 2 | Agent reads review_context.json | JSON | Audit plan | — |
| 3 | **Phase 1: AC satisfaction** — for each ready LP, for each AC: read Result file + code snapshot → judge pass/partial/fail with one-line reason | Result + code | AC verdicts | Single AC unjudgeable → mark `inconclusive` |
| 4 | **Phase 2: Architecture conformance** — read Plan architecture section + Decision constraints + actual code structure → judge each dimension | Plan + Decision + code | Architecture verdicts | Decision missing → skip constraint check |
| 5 | **Phase 3: Overall grade** — combine Phase 1 + 2 into A/B/C/D grade + risks + recommendations (max 5) | Phase 1+2 results | Grade + report | — |
| 6 | Write `views/review_report.md` | All results | Persisted report | Write failure → session summary only |
| 7 | Output session summary | All results | Formatted markdown | — |

### Phase 1 — AC Satisfaction Protocol

For each AC in `review_context.json`:
1. Read the corresponding Result file sections (`## confirmed` + `## 当前 Prompt 执行结果`)
2. Read `modified_files` code snapshots (function signatures, class definitions, core logic)
3. Verdict:
   - `pass` — Result and code clearly satisfy the AC's SHALL statement
   - `partial` — Partially satisfied; edge cases or boundary conditions missing
   - `fail` — Not implemented or contradicts the AC
   - `inconclusive` — Cannot determine (missing Result, code deleted, etc.)
4. One-line reason citing specific code location or Result paragraph

**Token optimization:** When `modified_files` > 5, prioritize files whose names match AC keywords.

### Phase 2 — Architecture Conformance Protocol

1. Read `architecture.plan_architecture_section` (Plan's architecture description)
2. Read `architecture.decision_summary` + `architecture.key_constraints` (Decision's choices and constraints)
3. Read actual code file structure (directory tree + key module imports)
4. Judge each dimension:
   - **Module boundaries** — Do actual files/classes match the design's component split?
   - **Data flow** — Does data pass in the direction the design specifies?
   - **Responsibility separation** — Does each module only do what the design declares?
   - **Constraint adherence** — Are Decision constraints violated?
   - **Rejected alternatives** — Did any rejected approach sneak in?
5. Per dimension: `conformant` / `deviation` / `violation` + reason

### Phase 3 — Overall Grade

| Grade | Condition |
|-------|-----------|
| A (优秀) | All AC pass + all architecture dimensions conformant |
| B (良好) | ≥80% AC pass, zero fail + no architecture violation |
| C (合格) | ≥60% AC pass + no architecture violation |
| D (不合格) | <60% AC pass OR any architecture violation |

Also produce:
- **Key risks** — most impactful partial/fail AC or deviation
- **Recommendations** — concrete, actionable fix directions (max 5)

### Token Budget

| LP count | Strategy |
|----------|----------|
| 1–3 | Read all Result files + all modified_files in full |
| 4–8 | Per LP: latest Result + top 3 most relevant code files |
| >8 | Batch (5 LPs per batch), cache intermediates in `status/.cache/` |

### Output

**Session summary** (always emitted):
```
## Review Summary
Overall Grade: **<grade>**
AC Pass Rate: N/M (percent%)
Architecture: <worst dimension verdict>
Key Findings: ...
Top Recommendations: ...
Full report: views/review_report.md
```

**Persisted report** (`views/review_report.md`): Full detail with AC table per LP, architecture findings table, risks and recommendations. Overwritten on each review run.
