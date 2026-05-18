"""Meta block computation and AI-facing AGENTS.md generation.

This module is the single place where the high-level summary an AI agent reads
first is computed. Both apply_changes.py (writes status.yaml) and
render_status.py (writes AGENTS.md) call into it so the two artifacts stay in
sync.

Design intent
-------------
An AI agent landing in a project should be able to answer in under 5 seconds:
  - what state is the project in?
  - what are the top 3-5 things that need attention right now?
  - where are the canonical views I should read for more detail?

The `meta` block at the top of status.yaml and AGENTS.md at the project root
are the two answers to that question. Everything else is detail.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional


HOTSPOT_LIMIT = 5


def _now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def compute_summary(status: dict) -> dict:
    """Return the counts that go into status.meta.summary."""
    arts = status.get("artifacts") or []
    blockers = status.get("blockers") or []
    gates = status.get("gates") or []
    hcs = status.get("handoff_contexts") or []

    pending_consumers = 0
    for h in hcs:
        for ce in h.get("consumed_status") or []:
            if ce.get("status") == "pending":
                pending_consumers += 1

    return {
        "artifacts_total": len(arts),
        "artifacts_ready": sum(1 for a in arts if a.get("status") == "ready"),
        "artifacts_blocked": sum(1 for a in arts if a.get("status") == "blocked"),
        "artifacts_needs_update": sum(1 for a in arts if a.get("status") == "needs_update"),
        "blockers_open": sum(1 for b in blockers if b.get("status") == "open"),
        "gates_failed": sum(1 for g in gates if g.get("status") == "failed"),
        "handoffs_total": len(hcs),
        "handoffs_stale": sum(1 for h in hcs if h.get("status") == "stale"),
        "handoffs_invalidated": sum(1 for h in hcs if h.get("status") == "invalidated"),
        "handoffs_pending_consumers": pending_consumers,
    }


def compute_hotspots(status: dict) -> list:
    """Return up to HOTSPOT_LIMIT high-priority lines an AI agent should look at.

    Each entry is a short string with severity prefix so the agent can sort or
    skim. Order is: critical blockers > failed gates > invalidated handoffs >
    stale handoffs > blocked LPs > needs_update LPs/TPs.
    """
    out = []
    for b in status.get("blockers") or []:
        if b.get("status") == "open" and b.get("severity") in ("critical", "high"):
            out.append(f"[blocker:{b.get('severity')}] {b.get('id')} {b.get('title', '')}")
    for g in status.get("gates") or []:
        if g.get("status") == "failed":
            out.append(f"[gate:failed] {g.get('id')} {g.get('name', '')}")
    for h in status.get("handoff_contexts") or []:
        if h.get("status") == "invalidated":
            out.append(f"[handoff:invalidated] {h.get('id')} producer={h.get('producer', '?')}")
    for h in status.get("handoff_contexts") or []:
        if h.get("status") == "stale":
            out.append(f"[handoff:stale] {h.get('id')} producer={h.get('producer', '?')}")
    for a in status.get("artifacts") or []:
        if a.get("type") == "landing_prompt" and a.get("status") == "blocked":
            out.append(f"[lp:blocked] {a.get('id')} {a.get('path', '')}")
    for a in status.get("artifacts") or []:
        if a.get("type") in ("landing_prompt", "test_prompt") and a.get("status") == "needs_update":
            out.append(f"[{a.get('type').split('_')[0]}:needs_update] {a.get('id')}")
    return out[:HOTSPOT_LIMIT]


def refresh_meta(status: dict, event_id: Optional[str] = None) -> None:
    """In-place: refresh status['meta'] with current counts, hotspots, and timestamp.

    Idempotent. Never overwrites user-authored pointers (only updates the values
    we own). Safe to call on legacy status.yaml that has no meta block yet.
    """
    meta = status.setdefault("meta", {})
    meta["schema_version"] = meta.get("schema_version", 1)
    meta["last_run"] = _now_iso()
    if event_id:
        meta["last_event"] = event_id
    meta["summary"] = compute_summary(status)
    meta["hotspots"] = compute_hotspots(status)
    pointers = meta.setdefault("pointers", {})
    pointers.setdefault("entry_point", "AGENTS.md")
    pointers.setdefault("views_dir", "views/")
    pointers.setdefault("status_report", "views/status_report.md")
    pointers.setdefault("handoff_view", "views/handoff_view.md")
    pointers.setdefault("prompt_chain", "views/prompt_chain_view.md")


def render_agents_md(status: dict) -> str:
    """Return the body of AGENTS.md — the AI-facing entry point at project root.

    Goal: an LLM that reads only this file should know:
      - what kind of project this is and how it is organized
      - where the authoritative state lives
      - what is currently broken / pending
      - what file to open next for any specific question
    """
    proj = status.get("project") or {}
    summary = compute_summary(status)
    hotspots = compute_hotspots(status)

    lines = []
    lines.append("<!-- AUTOGENERATED by tools/render_status.py — do not edit by hand. -->")
    lines.append("<!-- AI-AGENT-ENTRY-POINT: read this file first to orient yourself. -->")
    lines.append("")
    lines.append("# Project AI Index")
    lines.append("")
    lines.append("> **For AI agents**: this is the entry point. Read the TL;DR and Hotspots, ")
    lines.append("> then jump into the linked view that matches the user's question. ")
    lines.append("> Do **not** parse `status/status.yaml` directly unless you need a field ")
    lines.append("> that is not surfaced in any view.")
    lines.append("")
    lines.append("## TL;DR")
    lines.append("")
    lines.append(f"- **Project**: `{proj.get('name', 'unknown')}` (phase: `{proj.get('phase', '?')}`, version: `{proj.get('version', '?')}`)")
    lines.append(f"- **Artifacts**: {summary['artifacts_total']} total — ✅ {summary['artifacts_ready']} ready, 🚫 {summary['artifacts_blocked']} blocked, 🟡 {summary['artifacts_needs_update']} needs_update")
    lines.append(f"- **Blockers open**: {summary['blockers_open']}  **Gates failed**: {summary['gates_failed']}")
    lines.append(f"- **Handoffs**: {summary['handoffs_total']} total — ⚠️ {summary['handoffs_stale']} stale, ❌ {summary['handoffs_invalidated']} invalidated, ⏳ {summary['handoffs_pending_consumers']} pending consumers")
    lines.append("")
    lines.append("## Hotspots (top items needing attention)")
    lines.append("")
    if hotspots:
        for h in hotspots:
            lines.append(f"- {h}")
    else:
        lines.append("- _(none — project is healthy)_")
    lines.append("")
    lines.append("## Where to read what")
    lines.append("")
    lines.append("| Question | File to read |")
    lines.append("| --- | --- |")
    lines.append("| What is the overall state? | `views/status_report.md` |")
    lines.append("| Can LandingPrompt X execute? | `views/landing_prompt_checklist.md` (find X by id) |")
    lines.append("| Why is a LandingPrompt blocked? | `views/blocker_view.md` + `views/landing_prompt_checklist.md` |")
    lines.append("| What did the last change affect? | `views/change_impact_report.md` |")
    lines.append("| What is the dependency graph? | `views/dependency_graph.md` |")
    lines.append("| What handoffs exist and who consumes them? | `views/handoff_view.md` |")
    lines.append("| Is the Prompt chain broken anywhere? | `views/prompt_chain_view.md` |")
    lines.append("| What is the authoritative state for field X? | `status/status.yaml` (read the `meta:` block first) |")
    lines.append("")
    lines.append("## Directory map")
    lines.append("")
    lines.append("```")
    lines.append("research/           R-xxx-*.md     — facts and evidence only")
    lines.append("decisions/          D-xxx-*.yaml   — chosen approaches built on research")
    lines.append("plan/               P-xxx-*.md     — execution plans")
    lines.append("prompts/landing/    LP-xxx-*.md    — prompts that perform implementation")
    lines.append("prompts/test/       TP-xxx-*.md    — prompts that verify the implementation")
    lines.append("outputs/                           — concrete result files referenced by HandoffContext.results")
    lines.append("status/status.yaml                 — single source of truth (read meta block first)")
    lines.append("views/                             — read-only derived views (regenerated on every run)")
    lines.append("tools/                             — Python scripts (do not edit user files)")
    lines.append("```")
    lines.append("")
    lines.append("## Working rules for AI agents")
    lines.append("")
    lines.append("1. **Never** edit `status/status.yaml` directly. Write `status/.cache/approved_transitions.json` and run `tools/apply_changes.py`.")
    lines.append("2. **Never** treat `views/*.md` as state — they are read-only derivatives. Fix state in `status.yaml` and re-render.")
    lines.append("3. **Never** copy full Research text into status.yaml. Use IDs, paths, and short summaries.")
    lines.append("4. A LandingPrompt cannot be `ready` while any precondition / gate / consumed handoff is in a blocking state.")
    lines.append("5. When a producer file changes, the produced HandoffContext goes `stale` and its consumers go `needs_update`. Re-bump version only after re-reading the producer content.")
    lines.append("")
    lines.append(f"_Last refreshed: {_now_iso()}_")
    return "\n".join(lines) + "\n"
