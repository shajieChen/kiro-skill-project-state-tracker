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

    # TODO: will be filled in Task 2 and Task 3
    print(f"[review_quality] Found {len(ready_lps)} ready LP(s)")


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
