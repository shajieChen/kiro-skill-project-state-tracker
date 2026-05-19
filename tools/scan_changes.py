"""Scan project for changed files since last skill run.

Strategy:
  1. If a git repo, use `git status -s` + `git diff --name-status <baseline>` against the
     baseline ref stored in status.yaml#snapshots.git_baseline (default: previous HEAD).
  2. Otherwise, hash every tracked file under scanned directories and compare to
     status.yaml#snapshots.file_hashes.

Output: <project>/status/.cache/changed_files.json with the shape:
    {
      "method": "git" | "hash",
      "baseline": "<git-sha or null>",
      "changes": [
         {"path": "research/R-001.md", "kind": "research", "change": "modified", "new_hash": "..."},
         ...
      ]
    }

Classification mapping:
    research/*    -> research
    decisions/*   -> decision
    plan/*        -> plan
    prompts/landing/* -> landing
    prompts/test/*    -> test
    everything else under those roots -> other

Usage:
    python scan_changes.py --project <project_root>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _yaml_compat as yaml


# NOTE: Only these directories are scanned. This allow-list IS the contract
# referenced by PST SKILL.md §10 (scan_changes Path Scope). Status/, views/,
# AGENTS.md, tools/, templates/, outputs/, and any other repo-top-level paths
# are implicitly excluded. Artifacts with path "external:*" (managed by
# Execute-LandingPrompt) are implicitly skipped because they don't reside
# in any of these directories. Updates here MUST update PST §10.
SCANNED_DIRS = [
    ("research", "research"),
    ("decisions", "decision"),
    ("plan", "plan"),
    (os.path.join("prompts", "landing"), "landing"),
    (os.path.join("prompts", "test"), "test"),
]

# Filenames that live inside scanned dirs but are NOT artifacts.
META_FILES = {"README.md", "readme.md", ".gitkeep", ".gitignore"}

# Paths starting with "external:" in status.yaml are agent-managed (by ELP).
# They are never on disk under scanned dirs, so scan_changes naturally skips them.
# This constant documents the convention for future maintainers.
EXTERNAL_PATH_PREFIX = "external:"


def is_meta(rel_path: str) -> bool:
    base = os.path.basename(rel_path)
    return base in META_FILES or base.startswith(".")


def classify(rel_path: str) -> str:
    if is_meta(rel_path):
        return "other"
    rel_norm = rel_path.replace("\\", "/")
    for prefix, kind in SCANNED_DIRS:
        prefix_norm = prefix.replace("\\", "/")
        if rel_norm.startswith(prefix_norm + "/") or rel_norm == prefix_norm:
            return kind
    return "other"


def hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_git_repo(project: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", project, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, check=False,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except FileNotFoundError:
        return False


def git_head(project: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", project, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except FileNotFoundError:
        pass
    return None


def scan_git(project: str, baseline: str | None):
    """Return list of (relpath, change). change in {added, modified, deleted}."""
    results = []
    seen = set()

    # Working tree vs HEAD (uncommitted)
    r = subprocess.run(
        ["git", "-C", project, "status", "--porcelain=v1"],
        capture_output=True, text=True, check=False,
    )
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        x = line[0]
        y = line[1]
        path = line[3:].strip().strip('"')
        if "->" in path:  # rename
            path = path.split("->", 1)[1].strip()
        change = "modified"
        if "D" in (x, y):
            change = "deleted"
        elif "A" in (x, y) or "?" in (x, y):
            change = "added"
        results.append((path, change))
        seen.add(path)

    # Committed changes since baseline (if baseline given)
    if baseline:
        r = subprocess.run(
            ["git", "-C", project, "diff", "--name-status", baseline, "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                code = parts[0]
                path = parts[-1]
                if path in seen:
                    continue
                seen.add(path)
                if code.startswith("A"):
                    results.append((path, "added"))
                elif code.startswith("D"):
                    results.append((path, "deleted"))
                else:
                    results.append((path, "modified"))
    return results


def scan_hashes(project: str, prior: dict):
    """Compare hashes vs prior dict {relpath: sha256}. Return list of (relpath, change, new_hash)."""
    current = {}
    for prefix, _kind in SCANNED_DIRS:
        root = os.path.join(project, prefix)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if fn in META_FILES or fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, project).replace("\\", "/")
                current[rel] = hash_file(full)

    changes = []
    for rel, h in current.items():
        if rel not in prior:
            changes.append((rel, "added", h))
        elif prior[rel] != h:
            changes.append((rel, "modified", h))
    for rel in prior:
        if rel not in current:
            changes.append((rel, "deleted", None))
    return changes, current


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    args = ap.parse_args()
    project = os.path.abspath(args.project)

    status_path = os.path.join(project, "status", "status.yaml")
    if not os.path.isfile(status_path):
        print(f"[scan_changes] FAIL: status.yaml not found at {status_path}", file=sys.stderr)
        sys.exit(2)

    with open(status_path, "r", encoding="utf-8") as f:
        status = yaml.load(f.read()) or {}
    snap = status.get("snapshots") or {}
    prior_hashes = snap.get("file_hashes") or {}
    baseline = snap.get("git_baseline")

    cache_dir = os.path.join(project, "status", ".cache")
    os.makedirs(cache_dir, exist_ok=True)

    out = {"method": None, "baseline": None, "changes": [], "current_hashes": {}, "head": None}

    if is_git_repo(project):
        out["method"] = "git"
        out["baseline"] = baseline
        out["head"] = git_head(project)
        raw = scan_git(project, baseline)
        for rel, change in raw:
            kind = classify(rel)
            if kind == "other":
                continue
            new_hash = None
            full = os.path.join(project, rel)
            if change != "deleted" and os.path.isfile(full):
                new_hash = hash_file(full)
            out["changes"].append({"path": rel, "kind": kind, "change": change, "new_hash": new_hash})
        # Also compute current full hash table for snapshot continuity.
        _, full_hashes = scan_hashes(project, {})
        out["current_hashes"] = full_hashes
    else:
        out["method"] = "hash"
        out["baseline"] = None
        raw, full_hashes = scan_hashes(project, prior_hashes)
        for rel, change, new_hash in raw:
            kind = classify(rel)
            if kind == "other":
                continue
            out["changes"].append({"path": rel, "kind": kind, "change": change, "new_hash": new_hash})
        out["current_hashes"] = full_hashes

    out_path = os.path.join(cache_dir, "changed_files.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"[scan_changes] {out['method']} method, {len(out['changes'])} changes -> {out_path}")


if __name__ == "__main__":
    main()
