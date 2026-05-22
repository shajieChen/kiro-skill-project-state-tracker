"""Compute candidate state transitions based on changed files and the dependency graph.

Reads:
    status/status.yaml
    status/.cache/changed_files.json

Writes:
    status/.cache/candidate_transitions.json

A "candidate transition" is a proposed change to an artifact's status. The agent
then reviews each candidate (reading the actual file content) before approving.

The five propagation rules implemented here are the mechanical part of design §14:

  Rule 1: Research changed -> Plans that list it in `affected_by` become candidates
          for `needs_update`; if research declares `invalidates` for an assumption
          or decision that a Plan depends on, the Plan becomes a candidate for
          `invalidated`. Downstream LandingPrompts -> `blocked`, TestPrompts ->
          `needs_update`.

  Rule 2: Plan changed -> dependent LandingPrompts -> `needs_update`; their
          TestPrompts -> `needs_update`.

  Rule 3: LandingPrompt changed -> its TestPrompt -> `needs_update`. Also a
          precondition re-check (no auto-ready).

  Rule 4: TestPrompt changed -> if its content suggests it's complete, the script
          flags it as a candidate for `ready` for agent confirmation. Then
          LandingPrompt preconditions are recomputed.

  Rule 5: Blockers: re-evaluate preconditions and gates; mark `blocked` for
          high/critical open blockers' targets; never auto-clear.

The script does NOT modify status.yaml. It only emits candidates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _yaml_compat as yaml
import handoff as ho_helpers


def load_status(project: str) -> dict:
    p = os.path.join(project, "status", "status.yaml")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.load(f.read()) or {}


def load_changes(project: str) -> dict:
    p = os.path.join(project, "status", ".cache", "changed_files.json")
    if not os.path.isfile(p):
        return {"changes": []}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def find_artifact_by_path(status: dict, path: str) -> dict | None:
    path_norm = path.replace("\\", "/")
    for a in status.get("artifacts") or []:
        ap = (a.get("path") or "").replace("\\", "/")
        if ap == path_norm:
            return a
    return None


def find_finding_by_path(status: dict, path: str) -> dict | None:
    path_norm = path.replace("\\", "/")
    for rf in status.get("research_findings") or []:
        rp = (rf.get("path") or "").replace("\\", "/")
        if rp == path_norm:
            return rf
    return None


def artifacts_depending_on(status: dict, target_id: str) -> list[dict]:
    """Return artifacts whose depends_on or affected_by mentions target_id."""
    result = []
    for a in status.get("artifacts") or []:
        deps = (a.get("depends_on") or []) + (a.get("affected_by") or [])
        if target_id in deps:
            result.append(a)
    return result


def transitions_for_type(art: dict, reason: str, source: str, kind_chain: str) -> list[dict]:
    """Generate type-appropriate candidate transitions for a single artifact."""
    t = art.get("type")
    cur = art.get("status")
    if t == "plan":
        new = "needs_update" if "invalidate" not in reason else "invalidated"
    elif t == "landing_prompt":
        new = "blocked" if "invalidate" in reason else "needs_update"
    elif t == "test_prompt":
        new = "needs_update"
    elif t == "decision":
        new = "needs_update"
    else:
        new = "needs_update"
    if cur == new:
        return []
    return [{
        "artifact": art["id"],
        "type": t,
        "from": cur,
        "to": new,
        "reason": reason,
        "source": source,
        "kind_chain": kind_chain,
        "requires_agent_review": True,
    }]


def propagate_from_research(status: dict, finding: dict, source: str) -> list[dict]:
    out = []
    visited = set()

    # Direct: artifacts the finding explicitly affects.
    direct_targets = set(finding.get("affects") or [])

    # If the finding invalidates assumptions/decisions, find Plans depending on those.
    invalidates = set(finding.get("invalidates") or [])
    invalidation_targets = set()
    for inv_id in invalidates:
        for a in status.get("artifacts") or []:
            deps = (a.get("depends_on") or []) + (a.get("affected_by") or [])
            if inv_id in deps:
                invalidation_targets.add(a["id"])

    # Process direct (needs_update style).
    for tid in direct_targets:
        art = next((a for a in status.get("artifacts") or [] if a["id"] == tid), None)
        if art and art["id"] not in visited:
            visited.add(art["id"])
            out.extend(transitions_for_type(art, f"Research {finding['id']} affects this artifact",
                                            source, "research->artifact"))
            # Cascade: dependents of this artifact also drift.
            for dep in artifacts_depending_on(status, art["id"]):
                if dep["id"] in visited:
                    continue
                visited.add(dep["id"])
                out.extend(transitions_for_type(
                    dep, f"Upstream {art['id']} changed due to research {finding['id']}",
                    source, "research->artifact->dependent"))

    # Process invalidation chain.
    for tid in invalidation_targets:
        art = next((a for a in status.get("artifacts") or [] if a["id"] == tid), None)
        if art and art["id"] not in visited:
            visited.add(art["id"])
            out.extend(transitions_for_type(
                art, f"Research {finding['id']} invalidated a dependency",
                source, "research->invalidate"))
            for dep in artifacts_depending_on(status, art["id"]):
                if dep["id"] in visited:
                    continue
                visited.add(dep["id"])
                out.extend(transitions_for_type(
                    dep, f"Upstream {art['id']} invalidated by research {finding['id']}",
                    source, "research->invalidate->dependent"))
    return out


def propagate_from_artifact(status: dict, art: dict, source: str, change: str) -> list[dict]:
    """Plan/LandingPrompt/TestPrompt file changed. Propagate to dependents and produced handoffs."""
    out = []
    visited = {art["id"]}

    # The changed artifact itself may need a status drop (e.g. approved -> needs_update).
    if change == "deleted":
        out.append({
            "artifact": art["id"],
            "type": art["type"],
            "from": art["status"],
            "to": "archived",
            "reason": "Source file was deleted",
            "source": source,
            "kind_chain": "file->archive",
            "requires_agent_review": True,
        })
        return out

    if art["status"] in ("approved", "ready"):
        out.append({
            "artifact": art["id"],
            "type": art["type"],
            "from": art["status"],
            "to": "needs_update",
            "reason": "Source file content changed",
            "source": source,
            "kind_chain": "file->self",
            "requires_agent_review": True,
        })

    for dep in artifacts_depending_on(status, art["id"]):
        if dep["id"] in visited:
            continue
        visited.add(dep["id"])
        out.extend(transitions_for_type(
            dep, f"Upstream {art['id']} ({art['type']}) changed",
            source, f"{art['type']}->dependent"))

    # Handoff propagation: producer file changed -> produced handoffs become stale -> downstream consumers
    # of those handoffs whose status is currently ready get needs_update candidates.
    out.extend(propagate_from_producer_change(status, art, source))
    return out


def propagate_from_producer_change(status: dict, art: dict, source: str) -> list[dict]:
    """Producer artifact's file changed. Mark each produced handoff stale and cascade
    needs_update candidates to downstream consumers that are currently ready."""
    out = []
    produced = art.get("produces_handoffs") or []
    if not produced:
        return out

    for hc_id in produced:
        hc = ho_helpers.get_handoff(status, hc_id)
        if not hc:
            continue
        cur_status = hc.get("status")
        # Only emit a stale candidate when the handoff is currently advertised as usable.
        if cur_status in ("available", "consumed", "partially_consumed"):
            out.append({
                "artifact": hc_id,
                "op": "handoff_status",
                "type": "handoff",
                "from": cur_status,
                "to": "stale",
                "reason": f"Producer {art['id']} file changed; handoff content may be out of date",
                "source": source,
                "kind_chain": "producer->handoff_stale",
                "requires_agent_review": True,
            })
        # Cascade: each consumer that is currently ready or approved becomes a candidate.
        for consumer_id in ho_helpers.consumer_ids(hc):
            cons = next((a for a in status.get("artifacts") or [] if a.get("id") == consumer_id), None)
            if not cons:
                continue
            if cons.get("status") in ("ready", "approved"):
                out.append({
                    "artifact": consumer_id,
                    "type": cons.get("type"),
                    "from": cons.get("status"),
                    "to": "needs_update",
                    "reason": f"Consumed handoff {hc_id} expected to go stale (producer {art['id']} changed)",
                    "source": source,
                    "kind_chain": "producer->handoff->consumer",
                    "requires_agent_review": True,
                })
    return out


def candidate_register_new(path: str, kind: str) -> dict:
    """Generate a candidate transition for a new file with no matching artifact."""
    fname = os.path.basename(path)
    stem = os.path.splitext(fname)[0]
    type_map = {
        "research": "research",
        "decision": "decision",
        "plan": "plan",
        "landing": "landing_prompt",
        "test": "test_prompt",
    }
    art_type = type_map.get(kind, "plan")
    return {
        "artifact": None,
        "new_file": True,
        "type": art_type,
        "from": None,
        "to": "draft",
        "proposed_id": _proposed_id(art_type, stem),
        "path": path,
        "reason": "New file with no registered artifact",
        "source": path,
        "kind_chain": "new_file",
        "requires_agent_review": True,
    }


def _proposed_id(art_type: str, stem: str) -> str:
    import re
    if art_type == "research":
        m = re.match(r"^(R-\d+)", stem)
        return m.group(1) if m else (stem if stem.startswith("R-") else f"R-{stem}")
    if art_type == "decision":
        m = re.match(r"^(D-\d+)", stem)
        return m.group(1) if m else (stem if stem.startswith("D-") else f"D-{stem}")
    base = stem.lower().replace("-", "_")
    for prefix in ("p_", "lp_", "tp_"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    while base and base[0].isdigit():
        base = base[1:]
    base = base.lstrip("_")
    if art_type == "plan":
        return f"Plan.{base}" if base else f"Plan.{stem}"
    if art_type == "landing_prompt":
        return f"LandingPrompt.{base}" if base else f"LandingPrompt.{stem}"
    if art_type == "test_prompt":
        return f"TestPrompt.{base}" if base else f"TestPrompt.{stem}"
    return stem


def evaluate_preconditions(status: dict) -> list[dict]:
    """Compute current precondition status. Return list of transitions for preconditions
    whose computed status differs from recorded.

    Supports two kinds of requires-clauses:
      - artifact-targeted: {artifact: <id>, field: status, equals: <value>}
      - handoff-targeted:  {handoff: HC-xxx, field: status, in: [...]} or {field: version, min: N}
    """
    out = []
    arts_by_id = {a["id"]: a for a in status.get("artifacts") or []}
    for pc in status.get("preconditions") or []:
        requires = pc.get("requires") or []
        passing = True
        for req in requires:
            if ho_helpers.is_handoff_requires(req):
                if not ho_helpers.evaluate_handoff_requirement(status, req):
                    passing = False
                    break
                continue
            tgt = arts_by_id.get(req.get("artifact"))
            if not tgt:
                passing = False
                break
            field = req.get("field", "status")
            actual = tgt.get(field)
            # B3 fix: support multiple comparison operators in requires-clauses.
            # PSS generates "condition": "in [approved, ready]" format.
            if "equals" in req:
                if actual != req["equals"]:
                    passing = False
                    break
            elif "condition" in req:
                cond = req["condition"]
                if cond.startswith("in [") and cond.endswith("]"):
                    allowed = [v.strip() for v in cond[4:-1].split(",")]
                    if actual not in allowed:
                        passing = False
                        break
                else:
                    passing = False
                    break
            elif "in" in req:
                if actual not in (req["in"] or []):
                    passing = False
                    break
            else:
                passing = False
                break
        new = "passing" if passing else "failed"
        if pc.get("status") != new:
            out.append({
                "artifact": pc["id"],
                "type": "precondition",
                "from": pc.get("status"),
                "to": new,
                "reason": "Recomputed from current artifact / handoff statuses",
                "source": "preconditions",
                "kind_chain": "precondition->recompute",
                "requires_agent_review": False,
            })
    return out


def evaluate_blocker_effects(status: dict) -> list[dict]:
    """For each high/critical open blocker, mark blocked artifacts.
    For closed blockers, do NOT auto-clear — agent must re-check preconditions."""
    out = []
    arts_by_id = {a["id"]: a for a in status.get("artifacts") or []}
    for b in status.get("blockers") or []:
        if b.get("status") == "open" and b.get("severity") in ("high", "critical"):
            for tid in b.get("blocks") or []:
                art = arts_by_id.get(tid)
                if not art:
                    continue
                if art["status"] not in ("blocked", "invalidated", "archived"):
                    out.append({
                        "artifact": tid,
                        "type": art["type"],
                        "from": art["status"],
                        "to": "blocked",
                        "reason": f"Blocker {b['id']} is open ({b['severity']})",
                        "source": b["id"],
                        "kind_chain": "blocker->artifact",
                        "requires_agent_review": True,
                    })
    return out


def evaluate_handoff_consumer_drift(status: dict) -> list[dict]:
    """Sweep handoffs: any consumer in 'ready' or 'approved' that consumes a blocking
    handoff (stale/invalidated/deprecated/archived) is a candidate for needs_update / blocked."""
    out = []
    arts_by_id = {a.get("id"): a for a in (status.get("artifacts") or [])}
    for hc in status.get("handoff_contexts") or []:
        hc_status = hc.get("status")
        if hc_status not in ho_helpers.BLOCKING_HANDOFF_STATUSES:
            continue
        # invalidated/archived -> consumers go blocked; stale/deprecated -> needs_update.
        target_status = "blocked" if hc_status in ("invalidated", "archived") else "needs_update"
        for consumer_id in ho_helpers.consumer_ids(hc):
            cons = arts_by_id.get(consumer_id)
            if not cons:
                continue
            if cons.get("status") in ("ready", "approved"):
                out.append({
                    "artifact": consumer_id,
                    "type": cons.get("type"),
                    "from": cons.get("status"),
                    "to": target_status,
                    "reason": f"Consumed handoff {hc['id']} is {hc_status}",
                    "source": hc["id"],
                    "kind_chain": "handoff_blocking->consumer",
                    "requires_agent_review": True,
                })
    # Also: any consumer whose consumed_version is below the handoff's current version is candidate for needs_update.
    for hc in status.get("handoff_contexts") or []:
        if hc.get("status") not in ("available", "consumed", "partially_consumed"):
            continue
        cur_v = hc.get("version", 0)
        for ce in hc.get("consumed_status") or []:
            cv = ce.get("consumed_version")
            if cv is None or not isinstance(cv, int):
                continue
            if cv >= cur_v:
                continue
            consumer_id = ce.get("consumer")
            cons = arts_by_id.get(consumer_id)
            if not cons or cons.get("status") not in ("ready", "approved"):
                continue
            out.append({
                "artifact": consumer_id,
                "type": cons.get("type"),
                "from": cons.get("status"),
                "to": "needs_update",
                "reason": f"Consumed handoff {hc['id']} version {cur_v} but consumer absorbed v{cv}",
                "source": hc["id"],
                "kind_chain": "handoff_version->consumer",
                "requires_agent_review": True,
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    args = ap.parse_args()
    project = os.path.abspath(args.project)

    status = load_status(project)
    changes = load_changes(project).get("changes", [])

    candidates = []
    seen_keys = set()

    def add(c):
        key = (c.get("artifact"), c.get("to"), c.get("source"))
        if key in seen_keys:
            return
        seen_keys.add(key)
        candidates.append(c)

    for ch in changes:
        path = ch["path"]
        kind = ch["kind"]
        change = ch["change"]

        if kind == "research":
            finding = find_finding_by_path(status, path)
            if not finding:
                # New research file with no registered finding.
                candidates.append(candidate_register_new(path, kind))
                continue
            for c in propagate_from_research(status, finding, path):
                add(c)
        else:
            art = find_artifact_by_path(status, path)
            if not art:
                candidates.append(candidate_register_new(path, kind))
                continue
            for c in propagate_from_artifact(status, art, path, change):
                add(c)

    # Always recompute preconditions and blocker effects (cheap; ensures consistency).
    for c in evaluate_preconditions(status):
        add(c)
    for c in evaluate_blocker_effects(status):
        add(c)
    for c in evaluate_handoff_consumer_drift(status):
        add(c)

    out_path = os.path.join(project, "status", ".cache", "candidate_transitions.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"candidates": candidates}, f, indent=2)
    print(f"[propagate] {len(candidates)} candidate transitions -> {out_path}")


if __name__ == "__main__":
    main()
