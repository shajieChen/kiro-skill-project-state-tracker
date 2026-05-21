"""Extract review context for PST §11 REVIEW Mode.

Reads status.yaml, finds ready LandingPrompts, traces back to their Plan and
Decision artifacts, extracts Acceptance Criteria and architecture info, and
writes a structured JSON to status/.cache/review_context.json.

Usage:
    python review_quality.py --project <pst_root>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _yaml_compat as yaml


def main():
    ap = argparse.ArgumentParser(description="Extract review context for §11 REVIEW")
    ap.add_argument("--project", default=".", help="PST root directory")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    status_path = os.path.join(project, "status", "status.yaml")

    if not os.path.isfile(status_path):
        print(f"[review_quality] FAIL: status.yaml not found at {status_path}", file=sys.stderr)
        sys.exit(2)

    with open(status_path, "r", encoding="utf-8") as f:
        status = yaml.load(f.read()) or {}

    meta = status.get("meta") or {}
    artifacts = status.get("artifacts") or []

    # Build lookup indexes
    arts_by_id = {a["id"]: a for a in artifacts if isinstance(a, dict) and "id" in a}

    # Find ready LPs
    ready_lps = [
        a for a in artifacts
        if a.get("type") == "landing_prompt" and a.get("status") == "ready"
    ]

    if not ready_lps:
        # Write empty context and exit cleanly
        _write_context(project, meta, [], None)
        print("[review_quality] No ready LPs found. Nothing to review.")
        sys.exit(0)

    lp_contexts = []
    plan_art = None  # Will hold the last found plan for architecture extraction

    for lp in ready_lps:
        lp_ctx = {
            "id": lp["id"],
            "title": lp.get("title", lp["id"]),
            "path": lp.get("path", ""),
            "plan_id": None,
            "plan_path": None,
            "decision_id": None,
            "decision_path": None,
            "acceptance_criteria": [],
            "result_files": [],
            "modified_files": [],
        }

        # Trace to Plan
        found_plan = _trace_plan(lp, arts_by_id)
        if found_plan:
            plan_art = found_plan
            lp_ctx["plan_id"] = found_plan["id"]
            lp_ctx["plan_path"] = found_plan.get("path")

            # Trace to Decision
            found_decision = _trace_decision(found_plan, arts_by_id)
            if found_decision:
                lp_ctx["decision_id"] = found_decision["id"]
                lp_ctx["decision_path"] = found_decision.get("path")

        lp_contexts.append(lp_ctx)

    # Extract AC for each LP
    for lp_ctx in lp_contexts:
        # Try Decision first, fall back to Plan
        if lp_ctx["decision_path"]:
            acs = _extract_ac_from_decision(lp_ctx["decision_path"], project)
            if not acs and lp_ctx["plan_path"]:
                acs = _extract_ac_from_plan(lp_ctx["plan_path"], project)
        elif lp_ctx["plan_path"]:
            acs = _extract_ac_from_plan(lp_ctx["plan_path"], project)
        else:
            acs = []
        lp_ctx["acceptance_criteria"] = acs

        # Find Result files
        lp_ctx["result_files"] = _find_result_files(lp_ctx["id"], project)

        # Extract modified files from most recent Result
        if lp_ctx["result_files"]:
            lp_ctx["modified_files"] = _extract_modified_files(
                lp_ctx["result_files"][0], project
            )

    # Extract architecture info (use the first plan found)
    architecture = None
    if plan_art:
        first_decision_path = None
        for lp_ctx in lp_contexts:
            if lp_ctx["decision_path"]:
                first_decision_path = lp_ctx["decision_path"]
                break
        architecture = _extract_architecture(
            plan_art.get("path"), first_decision_path, project
        )

    _write_context(project, meta, lp_contexts, architecture)
    print(f"[review_quality] Processed {len(lp_contexts)} LP(s), "
          f"{sum(len(lp['acceptance_criteria']) for lp in lp_contexts)} AC(s)")


def _trace_plan(art: dict, arts_by_id: dict) -> dict | None:
    """Walk depends_on to find the first Plan artifact. BFS, max depth 3."""
    queue = list(art.get("depends_on") or [])
    visited = set()
    depth = 0
    while queue and depth < 3:
        next_queue = []
        for dep_id in queue:
            if dep_id in visited:
                continue
            visited.add(dep_id)
            dep = arts_by_id.get(dep_id)
            if dep and dep.get("type") == "plan":
                return dep
            if dep:
                next_queue.extend(dep.get("depends_on") or [])
        queue = next_queue
        depth += 1
    return None


def _trace_decision(plan_art: dict, arts_by_id: dict) -> dict | None:
    """From a Plan artifact, find the first Decision in depends_on."""
    for dep_id in plan_art.get("depends_on") or []:
        dep = arts_by_id.get(dep_id)
        if dep and dep.get("type") == "decision":
            return dep
    return None


def _extract_ac_from_decision(decision_path: str, project: str) -> list:
    """Parse acceptance_criteria from a Decision YAML file."""
    abs_path = os.path.join(project, decision_path.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        return []
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    data = yaml.load(content)
    if not isinstance(data, dict):
        return []
    acs = []
    for ac_group in data.get("acceptance_criteria") or []:
        if isinstance(ac_group, dict):
            ac_id = ac_group.get("id", "")
            for stmt in ac_group.get("statements") or []:
                acs.append({
                    "id": ac_id,
                    "statement": stmt if isinstance(stmt, str) else str(stmt),
                    "validates_property": ac_group.get("validates_property"),
                })
    return acs


def _extract_ac_from_plan(plan_path: str, project: str) -> list:
    """Fallback: parse AC from Plan markdown (## Acceptance Criteria or ## Properties)."""
    abs_path = os.path.join(project, plan_path.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        return []
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    acs = []
    # Look for ## Acceptance Criteria or ## Properties section
    pattern = r"^##\s+(?:Acceptance Criteria|Properties|验收标准)\s*\n(.*?)(?=^##|\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    section = match.group(1)
    # Parse bullet items as AC statements
    ac_num = 0
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            ac_num += 1
            acs.append({
                "id": f"AC-{ac_num}",
                "statement": line[2:].strip(),
                "validates_property": None,
            })
    return acs


def _find_result_files(lp_id: str, project: str) -> list:
    """Find Result files for a given LP artifact ID, sorted newest first."""
    result_dir = os.path.join(project, "Result")
    if not os.path.isdir(result_dir):
        return []
    # LP ID may contain dots/dashes; match files starting with the ID
    prefix = lp_id.lower()
    matches = []
    for fn in os.listdir(result_dir):
        if fn.lower().startswith(prefix) and fn.endswith(".md"):
            matches.append(os.path.join("Result", fn))
    # Sort by timestamp in filename (YYYYMMDD-HHmmss), newest first
    matches.sort(reverse=True)
    return matches


def _extract_modified_files(result_path: str, project: str) -> list:
    """Parse '- 修改文件:' line from a Result file."""
    abs_path = os.path.join(project, result_path.replace("/", os.sep))
    if not os.path.isfile(abs_path):
        return []
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Match "- 修改文件:" or "- Modified files:" followed by file list
    pattern = r"[-*]\s*(?:修改文件|Modified files)\s*[:：]\s*(.*)"
    match = re.search(pattern, content)
    if not match:
        return []
    raw = match.group(1).strip()
    # Could be comma-separated or newline-separated
    files = [f.strip() for f in re.split(r"[,\n]", raw) if f.strip()]
    return files


def _extract_architecture(plan_path: str | None, decision_path: str | None,
                          project: str) -> dict | None:
    """Extract architecture section from Plan and constraints from Decision."""
    arch = {
        "decision_summary": None,
        "plan_architecture_section": None,
        "key_constraints": [],
    }
    has_content = False

    # From Plan: extract ## Architecture / ## 架构 section
    if plan_path:
        abs_plan = os.path.join(project, plan_path.replace("/", os.sep))
        if os.path.isfile(abs_plan):
            with open(abs_plan, "r", encoding="utf-8") as f:
                plan_content = f.read()
            pattern = r"^##\s+(?:Architecture|架构)\s*\n(.*?)(?=^##|\Z)"
            match = re.search(pattern, plan_content, re.MULTILINE | re.DOTALL)
            if match:
                arch["plan_architecture_section"] = match.group(1).strip()
                has_content = True

    # From Decision: extract summary and constraints
    if decision_path:
        abs_dec = os.path.join(project, decision_path.replace("/", os.sep))
        if os.path.isfile(abs_dec):
            with open(abs_dec, "r", encoding="utf-8") as f:
                dec_content = f.read()
            dec_data = yaml.load(dec_content)
            if isinstance(dec_data, dict):
                arch["decision_summary"] = dec_data.get("title") or dec_data.get("summary")
                # Extract constraints from various possible fields
                constraints = dec_data.get("constraints") or []
                if isinstance(constraints, list):
                    arch["key_constraints"] = [str(c) for c in constraints]
                elif isinstance(constraints, str):
                    arch["key_constraints"] = [constraints]
                has_content = True

    return arch if has_content else None


def _write_context(project: str, meta: dict, ready_lps: list, architecture: dict | None):
    """Write review_context.json to status/.cache/."""
    cache_dir = os.path.join(project, "status", ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, "review_context.json")
    context = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_name": meta.get("project_name", "unknown"),
        "ready_lps": ready_lps,
        "architecture": architecture,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2, ensure_ascii=False)
    print(f"[review_quality] Context written to {out_path}")


if __name__ == "__main__":
    main()
