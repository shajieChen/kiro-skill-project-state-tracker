"""Apply agent-approved transitions to status.yaml safely.

Reads:
    status/status.yaml
    status/.cache/approved_transitions.json    (written by the agent)
    status/.cache/changed_files.json           (for snapshot refresh)

Writes:
    status/status.yaml   (atomic write)

Behavior:
  - Updates artifact `status` fields.
  - Registers brand-new artifacts (when transition has new_file: true).
  - Appends a single `change_events` entry summarizing this run.
  - Refreshes `snapshots.file_hashes` and `snapshots.git_baseline`.
  - Preserves every user-authored field on existing artifacts; only the `status`
    and `last_checked` fields are touched.
approved_transitions.json shape:
    {
      "event_summary": "Research R-001 update propagation",   (optional)
      "transitions": [
        {
          "artifact": "Plan.rendering_pipeline",
          "type": "plan",
          "from": "approved",
          "to": "invalidated",
          "reason": "Research R-001 invalidated assumption A-001",
          "source": "research/R-001-platform-limit.md"
        },
        ...
        {
          "artifact": null,
          "new_file": true,
          "type": "landing_prompt",
          "to": "draft",
          "proposed_id": "LandingPrompt.new_thing",
          "path": "prompts/landing/LP-007-new-thing.md",
          "reason": "Newly added landing prompt"
        }
      ]
    }
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _yaml_compat as yaml
import handoff as ho_helpers


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_id(existing_ids: list[str], prefix: str) -> str:
    n = 1
    used = set()
    for x in existing_ids:
        if isinstance(x, str) and x.startswith(prefix):
            try:
                used.add(int(x[len(prefix):]))
            except ValueError:
                pass
    while n in used:
        n += 1
    return f"{prefix}{n:03d}"


def atomic_write(path: str, text: str):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".status.", suffix=".yaml.tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ----------------------------- concurrency lock -----------------------------
# Cross-platform exclusive lock via atomic O_CREAT|O_EXCL on a lockfile in
# status/.cache/. Stale-lock detection: if the lockfile is older than
# LOCK_STALE_SECONDS (default 5 min), it is considered orphaned and removed.

LOCK_BASENAME = "apply_changes.lock"
LOCK_TIMEOUT_SECONDS = 30      # how long to wait for an active lock to clear
LOCK_RETRY_INTERVAL = 0.25
LOCK_STALE_SECONDS = 300       # treat lockfile older than this as orphaned


def _lock_path(project: str) -> str:
    return os.path.join(project, "status", ".cache", LOCK_BASENAME)


def acquire_lock(project: str) -> str:
    """Acquire an exclusive lock on status.yaml writes. Returns lock path.

    Raises TimeoutError if another writer holds a fresh lock past
    LOCK_TIMEOUT_SECONDS. Auto-clears stale locks (older than
    LOCK_STALE_SECONDS) before retrying.
    """
    import time
    lock_path = _lock_path(project)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            try:
                os.write(fd, f"{os.getpid()}\n{now_iso()}\n".encode("utf-8"))
            finally:
                os.close(fd)
            return lock_path
        except FileExistsError:
            # Stale-lock cleanup.
            try:
                age = time.time() - os.path.getmtime(lock_path)
            except OSError:
                age = 0
            if age > LOCK_STALE_SECONDS:
                try:
                    os.unlink(lock_path)
                    continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"[apply_changes] could not acquire {lock_path} "
                    f"within {LOCK_TIMEOUT_SECONDS}s (concurrent writer)"
                )
            time.sleep(LOCK_RETRY_INTERVAL)


def release_lock(lock_path: str) -> None:
    try:
        os.unlink(lock_path)
    except OSError:
        pass


# ------------------------- pending writebacks drain -------------------------
# When ELP's Phase B write fails, it appends the unwritten payload to
# status/.cache/pending_writebacks.json. apply_changes drains the queue on each
# invocation BEFORE consuming the current approved_transitions.json, so PST
# AUDIT Step 0 (which simply invokes apply_changes) automatically replays them.

PENDING_WRITEBACKS_BASENAME = "pending_writebacks.json"


def _pending_writebacks_path(project: str) -> str:
    return os.path.join(project, "status", ".cache", PENDING_WRITEBACKS_BASENAME)


def drain_pending_writebacks(project: str, status: dict) -> tuple[list, list]:
    """Drain pending_writebacks.json into transitions to apply this run.

    Returns (drained_transitions, kept_queue). Drained transitions are merged
    into the current run's transitions[]; kept_queue is the remaining queue
    after this drain (entries that failed validation are kept for the next run
    with an attempts[] note appended).
    """
    path = _pending_writebacks_path(project)
    if not os.path.isfile(path):
        return [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"[apply_changes] pending_writebacks unreadable: {e}", file=sys.stderr)
        return [], []
    queue = data.get("queue") or []
    drained: list = []
    kept: list = []
    for entry in queue:
        payload = entry.get("payload") or {}
        ts = payload.get("transitions") or []
        if not ts:
            # Empty entry — drop silently.
            continue
        drained.extend(ts)
    # Atomic-rewrite queue file with the kept (currently always empty after
    # successful drain; if a transition fails inside _apply_transition, it is
    # logged but does NOT re-enter the queue automatically — agent must
    # re-enqueue to avoid infinite loops).
    try:
        atomic_write(path, json.dumps({"queue": kept}, ensure_ascii=False, indent=2))
    except OSError as e:
        print(f"[apply_changes] failed to rewrite pending_writebacks: {e}", file=sys.stderr)
    return drained, kept


def _hash_file(abs_path: str) -> str:
    """Return sha256 hex digest of file at abs_path, or '' if unreadable."""
    h = hashlib.sha256()
    try:
        with open(abs_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _seed_snapshot_from_transitions(status: dict, project: str,
                                    transitions: list) -> None:
    """B6 fix: hash any file path referenced by a transition and merge into
    snapshots.file_hashes.

    Both PSS and ELP invoke apply_changes without first running scan_changes,
    so the old behaviour (only refresh from changed_files.json) left
    file_hashes empty forever. As a result the next scan_changes flagged every
    tracked file as 'added' and propagate.py emitted spurious needs_update
    candidates.

    Hashing is cheap (sha256 of small markdown/yaml). Missing files are
    silently skipped — the path may simply not exist yet for non-new_file
    transitions.
    """
    if not transitions:
        return
    if status.get("snapshots") is None:
        status["snapshots"] = {}
    snap = status["snapshots"]
    fh = snap.get("file_hashes")
    if not isinstance(fh, dict):
        fh = {}
        snap["file_hashes"] = fh
    for t in transitions:
        rel = t.get("path")
        if not isinstance(rel, str) or not rel:
            continue
        abs_path = os.path.join(project, rel)
        if not os.path.isfile(abs_path):
            continue
        digest = _hash_file(abs_path)
        if digest:
            fh[rel] = digest


# -------------------- Handoff operation handlers --------------------
#
# All handoff operations record a single change_event transition row each. They
# never overwrite user-authored fields on existing handoffs/artifacts; they only
# touch the specific field(s) the operation is about.


def _apply_handoff_register(status: dict, t: dict) -> Optional[dict]:
    """Register a new HandoffContext. Idempotent on id collision."""
    hc_list = status["handoff_contexts"]
    hc_id = t.get("handoff_id") or t.get("proposed_id")
    if not hc_id:
        print(f"[apply_changes] WARN: handoff_register missing handoff_id, skipping: {t}", file=sys.stderr)
        return None
    if any(hc.get("id") == hc_id for hc in hc_list):
        # Already present — treat as no-op (do not overwrite user fields).
        return {"artifact": hc_id, "from": "exists", "to": "exists",
                "reason": "handoff_register skipped: id already registered"}
    producer = t.get("producer")
    initial_status = t.get("to") or "draft"
    new_hc = {
        "id": hc_id,
        "title": t.get("title", ""),
        "producer": producer,
        "producer_type": t.get("producer_type"),
        "produced_from": t.get("produced_from", []) or [],
        "status": initial_status,
        "version": int(t.get("version", 1)),
        "facts": t.get("facts", []) or [],
        "results": t.get("results", []) or [],
        "constraints": t.get("constraints", []) or [],
        "consumed_by": t.get("consumed_by", []) or [],
        "consumed_status": _seed_consumed_status(t.get("consumed_by") or []),
        "invalidated_by": [],
        "last_verified": now_iso(),
    }
    hc_list.append(new_hc)
    # Wire produces_handoffs onto producer artifact if it exists.
    if producer:
        for a in status.get("artifacts") or []:
            if a.get("id") == producer:
                ho_helpers.ensure_handoff_fields_on_artifact(a)
                if hc_id not in a["produces_handoffs"]:
                    a["produces_handoffs"].append(hc_id)
                break
    # Wire consumes_handoffs onto each consumer artifact if it exists.
    for consumer in new_hc["consumed_by"]:
        for a in status.get("artifacts") or []:
            if a.get("id") == consumer:
                ho_helpers.ensure_handoff_fields_on_artifact(a)
                if hc_id not in a["consumes_handoffs"]:
                    a["consumes_handoffs"].append(hc_id)
                break
    return {"artifact": hc_id, "from": None, "to": initial_status,
            "reason": t.get("reason", "Handoff registered")}


def _seed_consumed_status(consumers: list) -> list:
    return [{"consumer": c, "status": "pending", "consumed_version": None, "consumed_at": None}
            for c in consumers]


def _apply_handoff_status(status: dict, t: dict) -> Optional[dict]:
    """Set a handoff's status. Does NOT touch version or consumed_status."""
    hc_id = t.get("artifact") or t.get("handoff_id")
    hc = ho_helpers.get_handoff(status, hc_id)
    if not hc:
        print(f"[apply_changes] WARN: handoff_status target {hc_id} not found", file=sys.stderr)
        return None
    prev = hc.get("status")
    new = t.get("to")
    if new not in ho_helpers.HANDOFF_STATUSES:
        print(f"[apply_changes] WARN: handoff_status invalid status {new}", file=sys.stderr)
        return None
    hc["status"] = new
    hc["last_verified"] = now_iso()
    return {"artifact": hc_id, "from": prev, "to": new,
            "reason": t.get("reason", "Handoff status updated")}


def _apply_handoff_version(status: dict, t: dict) -> list:
    """Bump a handoff's version. Mark prior consumers stale (consumed_version < new).
    Also writes one transition row per affected consumer."""
    hc_id = t.get("artifact") or t.get("handoff_id")
    hc = ho_helpers.get_handoff(status, hc_id)
    if not hc:
        print(f"[apply_changes] WARN: handoff_version target {hc_id} not found", file=sys.stderr)
        return []
    new_v = t.get("version")
    if new_v is None:
        new_v = int(hc.get("version", 1)) + 1
    try:
        new_v = int(new_v)
    except (TypeError, ValueError):
        print(f"[apply_changes] WARN: handoff_version non-integer version {new_v}", file=sys.stderr)
        return []
    prev_v = hc.get("version")
    hc["version"] = new_v
    # After a re-confirmed version bump the handoff returns to 'available' unless agent specified.
    prev_status = hc.get("status")
    hc["status"] = t.get("to") or "available"
    hc["last_verified"] = now_iso()
    rows = [{"artifact": hc_id, "from": f"v{prev_v}/{prev_status}",
             "to": f"v{new_v}/{hc['status']}",
             "reason": t.get("reason", "Handoff version bumped")}]
    # Mark consumers below the new version as stale.
    for ce in hc.get("consumed_status") or []:
        cv = ce.get("consumed_version")
        if cv is None or (isinstance(cv, int) and cv < new_v):
            prev_cs = ce.get("status")
            if prev_cs != "stale":
                ce["status"] = "stale"
                rows.append({
                    "artifact": f"{hc_id}:{ce.get('consumer')}", "from": prev_cs, "to": "stale",
                    "reason": f"Consumer absorbed v{cv}, handoff now v{new_v}"
                })
    return rows


def _apply_handoff_consume(status: dict, t: dict) -> Optional[dict]:
    """Record that a consumer has absorbed (or rejected) a specific handoff version."""
    hc_id = t.get("artifact") or t.get("handoff_id")
    consumer = t.get("consumer")
    if not consumer:
        print(f"[apply_changes] WARN: handoff_consume missing consumer", file=sys.stderr)
        return None
    hc = ho_helpers.get_handoff(status, hc_id)
    if not hc:
        print(f"[apply_changes] WARN: handoff_consume target {hc_id} not found", file=sys.stderr)
        return None
    if consumer not in (hc.get("consumed_by") or []):
        print(f"[apply_changes] WARN: handoff_consume {consumer} not in {hc_id}.consumed_by", file=sys.stderr)
        return None
    consumed_version = t.get("consumed_version")
    if consumed_version is None:
        consumed_version = hc.get("version", 1)
    try:
        consumed_version = int(consumed_version)
    except (TypeError, ValueError):
        print(f"[apply_changes] WARN: handoff_consume non-integer consumed_version", file=sys.stderr)
        return None
    new_status = t.get("to") or "consumed"
    if new_status not in ho_helpers.CONSUMER_STATUSES:
        print(f"[apply_changes] WARN: handoff_consume invalid consumer status {new_status}", file=sys.stderr)
        return None
    if status.get("handoff_contexts") and hc.get("consumed_status") is None:
        hc["consumed_status"] = []
    cs_list = hc.setdefault("consumed_status", [])
    entry = next((x for x in cs_list if x.get("consumer") == consumer), None)
    prev_status = entry.get("status") if entry else None
    if entry is None:
        entry = {"consumer": consumer}
        cs_list.append(entry)
    entry["status"] = new_status
    entry["consumed_version"] = consumed_version if new_status != "rejected" else None
    entry["consumed_at"] = now_iso()
    if new_status == "rejected" and t.get("reason"):
        entry["reason"] = t["reason"]
    # Aggregate handoff status from consumer states.
    all_consumed = all(
        (x.get("status") == "consumed" and x.get("consumed_version") == hc.get("version"))
        for x in cs_list
    ) if cs_list else False
    some_consumed = any(x.get("status") == "consumed" for x in cs_list)
    if hc.get("status") not in ho_helpers.BLOCKING_HANDOFF_STATUSES:
        if all_consumed:
            hc["status"] = "consumed"
        elif some_consumed:
            hc["status"] = "partially_consumed"
    return {"artifact": f"{hc_id}:{consumer}",
            "from": prev_status, "to": new_status,
            "reason": t.get("reason", f"Consumer {consumer} marked {new_status}")}


# -------------------- Imports kept local to avoid circular import --------------------
from typing import Optional


# -------------------- Precondition operation handler --------------------
#
# Per PST §6C, every Landing Prompt must have preconditions guarding upstream
# Plan readiness (and optionally downstream TP / consumed HC versions). PSS
# emits `precondition_register` transitions when scaffolding LP artifacts so
# the PCs are created exactly once, idempotently, with structured `requires[]`
# clauses that propagate.py / validate_status.py can evaluate.

_VALID_PC_STATUSES = {"pending", "passed", "failed", "skipped"}


def _next_pc_id(status: dict) -> str:
    used = {p.get("id") for p in (status.get("preconditions") or [])}
    n = 1
    while f"PC-{n:03d}" in used:
        n += 1
    return f"PC-{n:03d}"


def _ensure_lp_gate(status: dict, pc_id: str) -> Optional[dict]:
    """Make sure Gate G-001 exists and references the new PC.

    Returns a change_event row when the gate was newly created or updated.
    """
    gates = status.get("gates")
    if gates is None:
        status["gates"] = []
        gates = status["gates"]
    gate = next((g for g in gates if g.get("id") == "G-001"), None)
    created = False
    if gate is None:
        gate = {
            "id": "G-001",
            "name": "Landing Prompt readiness",
            "status": "pending",
            "checks": [],
        }
        gates.append(gate)
        created = True
    checks = gate.setdefault("checks", [])
    if not any(c.get("id") == pc_id for c in checks):
        checks.append({"id": pc_id, "description": f"Precondition {pc_id}",
                       "status": "pending"})
        return {"artifact": "G-001",
                "from": None if created else "exists",
                "to": "pending",
                "reason": f"Gate references new PC {pc_id}"}
    return None


def _apply_precondition_register(status: dict, t: dict) -> list:
    """Register a new precondition. Idempotent on (target, requires) tuple.

    Transition shape:
        {op: precondition_register,
         target: "LP-001",
         requires: [{artifact: "Plan.x", field: "status",
                     condition: "in [approved, ready]"}, ...],
         proposed_id?: "PC-001",            # optional, allocated if absent
         status?: "pending",
         reason?: "...",
         source: "..."}
    """
    target = t.get("target")
    requires = t.get("requires") or []
    if not target:
        print("[apply_changes] WARN: precondition_register missing target, skipping",
              file=sys.stderr)
        return []
    if not isinstance(requires, list) or not requires:
        print(f"[apply_changes] WARN: precondition_register for {target} has empty requires",
              file=sys.stderr)
        return []

    pcs = status.get("preconditions")
    if pcs is None:
        status["preconditions"] = []
        pcs = status["preconditions"]

    # Idempotency: same target + same requires set already registered -> no-op.
    def _norm(r):
        return (r.get("artifact") or r.get("handoff"), r.get("field"),
                r.get("condition"))
    new_sig = sorted(_norm(r) for r in requires)
    for existing in pcs:
        if existing.get("target") != target:
            continue
        ex_sig = sorted(_norm(r) for r in (existing.get("requires") or []))
        if ex_sig == new_sig:
            return [{"artifact": existing.get("id"),
                     "from": existing.get("status"),
                     "to": existing.get("status"),
                     "reason": "precondition_register skipped: already present"}]

    pc_id = t.get("proposed_id") or _next_pc_id(status)
    init_status = t.get("status", "pending")
    if init_status not in _VALID_PC_STATUSES:
        init_status = "pending"
    pc = {
        "id": pc_id,
        "target": target,
        "requires": requires,
        "status": init_status,
    }
    pcs.append(pc)
    rows = [{"artifact": pc_id, "from": None, "to": init_status,
             "reason": t.get("reason", f"Precondition registered for {target}")}]
    # Auto-wire to G-001 when the target is a Landing Prompt.
    if isinstance(target, str) and target.startswith("LP-"):
        gate_row = _ensure_lp_gate(status, pc_id)
        if gate_row:
            rows.append(gate_row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".")
    args = ap.parse_args()
    project = os.path.abspath(args.project)

    status_path = os.path.join(project, "status", "status.yaml")
    approved_path = os.path.join(project, "status", ".cache", "approved_transitions.json")
    changed_path = os.path.join(project, "status", ".cache", "changed_files.json")

    if not os.path.isfile(status_path):
        print("[apply_changes] FAIL: status.yaml not found", file=sys.stderr)
        sys.exit(2)

    # R3: serialize all writers via lockfile so PST AUDIT + ELP direct-write
    # cannot race on status.yaml.
    try:
        lock_path = acquire_lock(project)
    except TimeoutError as e:
        print(str(e), file=sys.stderr)
        sys.exit(4)

    try:
        _main_locked(project, status_path, approved_path, changed_path)
    finally:
        release_lock(lock_path)


def _main_locked(project: str, status_path: str, approved_path: str, changed_path: str):
    with open(status_path, "r", encoding="utf-8") as f:
        status = yaml.load(f.read()) or {}

    # R1: drain ELP's failed-Phase-B queue BEFORE reading approved_transitions
    # so AUDIT Step 0 replays it automatically.
    drained_transitions, _kept = drain_pending_writebacks(project, status)

    if not os.path.isfile(approved_path):
        if not drained_transitions:
            print("[apply_changes] no approved_transitions.json — nothing to apply")
            return
        approved = {
            "event_summary": "Drained pending_writebacks from prior ELP failures",
            "transitions": [],
        }
    else:
        with open(approved_path, "r", encoding="utf-8") as f:
            approved = json.load(f) or {}

    transitions = approved.get("transitions") or []
    # Prepend drained entries so they apply first (they predate this run).
    if drained_transitions:
        transitions = drained_transitions + transitions
    event_summary = approved.get("event_summary") or "Skill run state propagation"

    if not transitions:
        # Even on a no-op run, refresh the AI-facing meta block so its
        # last_run timestamp and counts reflect reality. Also drain
        # changed_files.json into snapshots if scan_changes was run first
        # (this is how `scan_changes -> apply_changes` baseline-seeds).
        if os.path.isfile(changed_path):
            try:
                with open(changed_path, "r", encoding="utf-8") as f:
                    changed = json.load(f) or {}
            except Exception:
                changed = {}
            if changed.get("current_hashes"):
                if status.get("snapshots") is None:
                    status["snapshots"] = {}
                status["snapshots"]["file_hashes"] = changed["current_hashes"]
            if changed.get("method") == "git" and changed.get("head"):
                if status.get("snapshots") is None:
                    status["snapshots"] = {}
                status["snapshots"]["git_baseline"] = changed["head"]
        try:
            from meta import refresh_meta
        except Exception:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(__file__))
            from meta import refresh_meta
        refresh_meta(status)
        atomic_write(status_path, yaml.dump(status))
        print("[apply_changes] approved_transitions.json had no transitions (meta refreshed)")
        sys.exit(0)

    if status.get("artifacts") is None:
        status["artifacts"] = []
    if status.get("handoff_contexts") is None:
        status["handoff_contexts"] = []
    artifacts = status["artifacts"]
    by_id = {a["id"]: a for a in artifacts}

    # Backfill produces_handoffs / consumes_handoffs on existing artifacts (idempotent).
    for a in artifacts:
        ho_helpers.ensure_handoff_fields_on_artifact(a)

    affected = []
    recorded_transitions = []
    sources = set()

    for t in transitions:
        sources.add(t.get("source", ""))
        op = t.get("op", "")

        # ---------- Handoff operations ----------
        if op == "handoff_register":
            ce_entry = _apply_handoff_register(status, t)
            if ce_entry:
                recorded_transitions.append(ce_entry)
                affected.append(ce_entry["artifact"])
            continue
        if op == "handoff_status":
            ce_entry = _apply_handoff_status(status, t)
            if ce_entry:
                recorded_transitions.append(ce_entry)
                affected.append(ce_entry["artifact"])
            continue
        if op == "handoff_version":
            entries = _apply_handoff_version(status, t)
            for e in entries:
                recorded_transitions.append(e)
                affected.append(e["artifact"])
            continue
        if op == "handoff_consume":
            ce_entry = _apply_handoff_consume(status, t)
            if ce_entry:
                recorded_transitions.append(ce_entry)
                affected.append(ce_entry["artifact"])
            continue

        # ---------- Precondition operations ----------
        if op == "precondition_register":
            rows = _apply_precondition_register(status, t)
            for r in rows:
                recorded_transitions.append(r)
                affected.append(r["artifact"])
            continue

        # ---------- New file registrations and artifact updates ----------
        if t.get("new_file"):
            target_type = t.get("type", "plan")
            new_id = t.get("proposed_id")
            if not new_id:
                print(f"[apply_changes] WARN: new_file transition missing proposed_id, skipping: {t}", file=sys.stderr)
                continue
            # Research findings register in research_findings[], not artifacts[].
            if target_type in ("research", "research_finding"):
                if status.get("research_findings") is None:
                    status["research_findings"] = []
                rf_list = status["research_findings"]
                if any(rf.get("id") == new_id for rf in rf_list):
                    recorded_transitions.append({
                        "artifact": new_id, "from": None, "to": "draft",
                        "reason": t.get("reason", "") + " (already present)"
                    })
                else:
                    rf_list.append({
                        "id": new_id,
                        "title": t.get("title", ""),
                        "path": t.get("path"),
                        "type": "finding",
                        "confidence": t.get("confidence", "medium"),
                        "evidence": t.get("evidence", []) or [],
                        "affects": t.get("affects", []) or [],
                        "invalidates": t.get("invalidates", []) or [],
                        "status_effect": t.get("status_effect", []) or [],
                    })
                    recorded_transitions.append({
                        "artifact": new_id, "from": None, "to": "registered",
                        "reason": t.get("reason", "")
                    })
                affected.append(new_id)
                continue
            # Decisions register in decisions[], not artifacts[].
            if target_type == "decision":
                if status.get("decisions") is None:
                    status["decisions"] = []
                d_list = status["decisions"]
                if not any(d.get("id") == new_id for d in d_list):
                    d_list.append({
                        "id": new_id,
                        "title": t.get("title", ""),
                        "path": t.get("path"),
                        "status": t.get("to", "draft"),
                        "based_on": t.get("based_on", []) or [],
                        "rejects": t.get("rejects", []) or [],
                        "affects": t.get("affects", []) or [],
                    })
                recorded_transitions.append({
                    "artifact": new_id, "from": None, "to": t.get("to", "draft"),
                    "reason": t.get("reason", "")
                })
                affected.append(new_id)
                continue
            if new_id in by_id:
                # Already registered (maybe a prior run). Treat as modification.
                a = by_id[new_id]
                prev = a.get("status")
                a["status"] = t.get("to", a.get("status", "draft"))
                a["last_checked"] = now_iso()
                recorded_transitions.append({
                    "artifact": new_id, "from": prev, "to": a["status"], "reason": t.get("reason", "")
                })
                affected.append(new_id)
            else:
                new_art = {
                    "id": new_id,
                    "type": target_type,
                    "path": t.get("path"),
                    "status": t.get("to", "draft"),
                    "depends_on": t.get("depends_on", []) or [],
                    "affected_by": t.get("affected_by", []) or [],
                    "blocks": [],
                    "blocked_by": [],
                    "invalidated_by": [],
                    "last_checked": now_iso(),
                }
                artifacts.append(new_art)
                by_id[new_id] = new_art
                recorded_transitions.append({
                    "artifact": new_id, "from": None, "to": new_art["status"],
                    "reason": t.get("reason", "")
                })
                affected.append(new_id)
        else:
            aid = t.get("artifact")
            if not aid:
                continue
            # Handle preconditions and other non-artifact targets via a parallel path.
            if t.get("type") == "precondition":
                for pc in status.get("preconditions") or []:
                    if pc.get("id") == aid:
                        prev = pc.get("status")
                        pc["status"] = t["to"]
                        recorded_transitions.append({
                            "artifact": aid, "from": prev, "to": pc["status"],
                            "reason": t.get("reason", "")
                        })
                        affected.append(aid)
                        break
                continue
            a = by_id.get(aid)
            if not a:
                print(f"[apply_changes] WARN: artifact {aid} not found, skipping", file=sys.stderr)
                continue
            prev = a.get("status")
            a["status"] = t["to"]
            a["last_checked"] = now_iso()
            recorded_transitions.append({
                "artifact": aid, "from": prev, "to": a["status"], "reason": t.get("reason", "")
            })
            affected.append(aid)

    # Record change event.
    if status.get("change_events") is None:
        status["change_events"] = []
    change_events = status["change_events"]
    existing_ce_ids = [ce.get("id") for ce in change_events]
    ce_id = next_id(existing_ce_ids, "CE-")
    change_events.append({
        "id": ce_id,
        "time": now_iso(),
        "source": "; ".join(sorted(s for s in sources if s)) or "skill_run",
        "event_type": approved.get("event_type", "skill_run"),
        "summary": event_summary,
        "affected": sorted(set(affected)),
        "transitions": recorded_transitions,
    })

    # Refresh snapshots.
    if status.get("snapshots") is None:
        status["snapshots"] = {}
    snap = status["snapshots"]
    if os.path.isfile(changed_path):
        with open(changed_path, "r", encoding="utf-8") as f:
            changed = json.load(f) or {}
        if changed.get("current_hashes"):
            snap["file_hashes"] = changed["current_hashes"]
        if changed.get("method") == "git" and changed.get("head"):
            snap["git_baseline"] = changed["head"]
    # B6 fix: ALWAYS merge per-transition path hashes so PSS/ELP callers (which
    # don't pre-run scan_changes) keep snapshots in sync. Runs AFTER the
    # changed_files.json drain above so a full-tree scan_changes table is
    # authoritative when both are present.
    _seed_snapshot_from_transitions(status, project, transitions)

    # Refresh AI-facing meta block so the top of status.yaml always reflects
    # the just-applied state.
    try:
        from meta import refresh_meta
    except Exception:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(__file__))
        from meta import refresh_meta
    refresh_meta(status, event_id=ce_id)

    text = yaml.dump(status)
    atomic_write(status_path, text)

    print(f"[apply_changes] applied {len(transitions)} transition(s), wrote {ce_id}")


if __name__ == "__main__":
    main()
