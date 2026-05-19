"""Validate status/status.yaml against status/schema.yaml.

Exits 0 if valid, non-zero with a diagnostic message otherwise.

Usage:
    python validate_status.py --project <project_root>
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _yaml_compat as yaml
import handoff as ho_helpers


def fail(msg: str):
    print(f"[validate_status] FAIL: {msg}", file=sys.stderr)
    sys.exit(2)


def warn(msg: str):
    print(f"[validate_status] WARN: {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".", help="Project root containing status/")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    status_path = os.path.join(project, "status", "status.yaml")
    schema_path = os.path.join(project, "status", "schema.yaml")

    if not os.path.isfile(status_path):
        fail(f"status.yaml not found at {status_path}")
    if not os.path.isfile(schema_path):
        # B3 fix: fall back to the schema bundled next to this script so a
        # missing user-side schema.yaml doesn't permanently red-light the
        # quality gate. Auto-copy it into the project so subsequent runs and
        # other tools see a real file at the canonical location.
        bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "_schema_default.yaml")
        if not os.path.isfile(bundled):
            fail(f"schema.yaml not found at {schema_path} (and bundled "
                 f"fallback missing at {bundled})")
        warn(f"schema.yaml missing at {schema_path}; copied bundled default "
             f"from {bundled}")
        os.makedirs(os.path.dirname(schema_path), exist_ok=True)
        with open(bundled, "r", encoding="utf-8") as src, \
             open(schema_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())

    with open(status_path, "r", encoding="utf-8") as f:
        status = yaml.load(f.read()) or {}
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = yaml.load(f.read()) or {}

    # Top-level keys
    for k in schema.get("required_top_level_keys", []):
        if k not in status:
            fail(f"missing top-level key: {k}")

    # Project section
    proj = status.get("project") or {}
    for k in schema.get("project", {}).get("required_fields", []):
        if k not in proj:
            fail(f"project.{k} missing")

    artifact_statuses = set(schema.get("artifact_statuses", []))
    artifact_types = set(schema.get("artifact_types", []))
    artifact_required = schema.get("artifact_required_fields", [])

    ids_seen = set()
    for a in status.get("artifacts") or []:
        if not isinstance(a, dict):
            fail(f"artifacts contains non-mapping entry: {a!r}")
        for rf in artifact_required:
            if rf not in a:
                fail(f"artifact {a.get('id', '?')} missing field: {rf}")
        if a["id"] in ids_seen:
            fail(f"duplicate artifact id: {a['id']}")
        ids_seen.add(a["id"])
        if a["type"] not in artifact_types:
            fail(f"artifact {a['id']} has invalid type: {a['type']}")
        if a["status"] not in artifact_statuses:
            fail(f"artifact {a['id']} has invalid status: {a['status']}")

    # Research findings
    rf_required = schema.get("research_finding_required_fields", [])
    rf_prefix = schema.get("research_finding_id_prefix", "R-")
    rf_ids = set()
    for rf in status.get("research_findings") or []:
        for k in rf_required:
            if k not in rf:
                fail(f"research_finding {rf.get('id', '?')} missing field: {k}")
        if not str(rf["id"]).startswith(rf_prefix):
            warn(f"research_finding id should start with '{rf_prefix}': {rf['id']}")
        if rf["id"] in rf_ids:
            fail(f"duplicate research_finding id: {rf['id']}")
        rf_ids.add(rf["id"])

    # Evidence
    ev_required = schema.get("evidence_required_fields", [])
    ev_prefix = schema.get("evidence_label_prefix", "E-")
    ev_labels = set()
    for ev in status.get("evidence") or []:
        for k in ev_required:
            if k not in ev:
                fail(f"evidence {ev.get('label', '?')} missing field: {k}")
        if not str(ev["label"]).startswith(ev_prefix):
            warn(f"evidence label should start with '{ev_prefix}': {ev['label']}")
        if ev["label"] in ev_labels:
            fail(f"duplicate evidence label: {ev['label']}")
        ev_labels.add(ev["label"])

    # Decisions
    d_required = schema.get("decision_required_fields", [])
    d_prefix = schema.get("decision_id_prefix", "D-")
    for d in status.get("decisions") or []:
        for k in d_required:
            if k not in d:
                fail(f"decision {d.get('id', '?')} missing field: {k}")
        if not str(d["id"]).startswith(d_prefix):
            warn(f"decision id should start with '{d_prefix}': {d['id']}")
        if d.get("status") and d["status"] not in artifact_statuses:
            fail(f"decision {d['id']} has invalid status: {d['status']}")

    # Blockers
    b_required = schema.get("blocker_required_fields", [])
    b_sev = set(schema.get("blocker_severities", []))
    b_stat = set(schema.get("blocker_statuses", []))
    for b in status.get("blockers") or []:
        for k in b_required:
            if k not in b:
                fail(f"blocker {b.get('id', '?')} missing field: {k}")
        if b["severity"] not in b_sev:
            fail(f"blocker {b['id']} has invalid severity: {b['severity']}")
        if b["status"] not in b_stat:
            fail(f"blocker {b['id']} has invalid status: {b['status']}")

    # Gates
    g_required = schema.get("gate_required_fields", [])
    g_stat = set(schema.get("gate_statuses", []))
    for g in status.get("gates") or []:
        for k in g_required:
            if k not in g:
                fail(f"gate {g.get('id', '?')} missing field: {k}")
        if g["status"] not in g_stat:
            fail(f"gate {g['id']} has invalid status: {g['status']}")

    # Preconditions
    pc_required = schema.get("precondition_required_fields", [])
    pc_stat = set(schema.get("precondition_statuses", []))
    for pc in status.get("preconditions") or []:
        for k in pc_required:
            if k not in pc:
                fail(f"precondition {pc.get('id', '?')} missing field: {k}")
        if pc["status"] not in pc_stat:
            fail(f"precondition {pc['id']} has invalid status: {pc['status']}")

    # Change events
    ce_required = schema.get("change_event_required_fields", [])
    for ce in status.get("change_events") or []:
        for k in ce_required:
            if k not in ce:
                fail(f"change_event {ce.get('id', '?')} missing field: {k}")

    # HandoffContext validations.
    validate_handoff_contexts(status, schema, project)
    validate_handoff_references(status)
    validate_handoff_consumption(status, schema)
    validate_handoff_preconditions(status)

    # Snapshots
    snap = status.get("snapshots") or {}
    for k in schema.get("snapshots_required_fields", []):
        if k not in snap:
            fail(f"snapshots.{k} missing")

    # Rules
    rules = status.get("rules") or {}
    for k in schema.get("rules_required_fields", []):
        if k not in rules:
            fail(f"rules.{k} missing")

    print(f"[validate_status] OK  ({len(status.get('artifacts') or [])} artifacts, "
          f"{len(status.get('research_findings') or [])} findings, "
          f"{len(status.get('handoff_contexts') or [])} handoffs, "
          f"{len(status.get('change_events') or [])} change events)")
    sys.exit(0)


def validate_handoff_contexts(status: dict, schema: dict, project: str):
    """Structural validation of each handoff_context entry."""
    required = schema.get("handoff_required_fields", [])
    prefix = schema.get("handoff_id_prefix", "HC-")
    valid_statuses = set(schema.get("handoff_statuses", []))
    fact_req = schema.get("handoff_fact_required_fields", [])
    constraint_req = schema.get("handoff_constraint_required_fields", [])
    result_req = schema.get("handoff_result_required_fields", [])

    hcs = status.get("handoff_contexts") or []
    seen = set()
    for hc in hcs:
        if not isinstance(hc, dict):
            fail(f"handoff_contexts contains non-mapping entry: {hc!r}")
        for k in required:
            if k not in hc:
                fail(f"handoff {hc.get('id', '?')} missing field: {k}")
        hc_id = hc.get("id")
        if not str(hc_id).startswith(prefix):
            warn(f"handoff id should start with '{prefix}': {hc_id}")
        if hc_id in seen:
            fail(f"duplicate handoff id: {hc_id}")
        seen.add(hc_id)
        if hc.get("status") not in valid_statuses:
            fail(f"handoff {hc_id} has invalid status: {hc.get('status')}")
        version = hc.get("version")
        if not isinstance(version, int):
            fail(f"handoff {hc_id} version must be int, got: {type(version).__name__}={version!r}")
        if version < 1:
            fail(f"handoff {hc_id} version must be >= 1, got: {version}")
        # Facts.
        for fact in hc.get("facts") or []:
            for k in fact_req:
                if k not in fact:
                    fail(f"handoff {hc_id} fact {fact.get('id', '?')} missing field: {k}")
        # Constraints.
        for con in hc.get("constraints") or []:
            for k in constraint_req:
                if k not in con:
                    fail(f"handoff {hc_id} constraint {con.get('id', '?')} missing field: {k}")
        # Results: each needs either path or summary; warn if path doesn't exist.
        for res in hc.get("results") or []:
            for k in result_req:
                if k not in res:
                    fail(f"handoff {hc_id} result {res.get('id', '?')} missing field: {k}")
            if not (res.get("path") or res.get("summary")):
                fail(f"handoff {hc_id} result {res.get('id', '?')} must have path or summary")
            rpath = res.get("path")
            if rpath:
                abs_p = os.path.join(project, rpath.replace("/", os.sep))
                if not os.path.exists(abs_p):
                    warn(f"handoff {hc_id} result path does not exist on disk: {rpath}")
        # Soft warning for deprecated handoffs.
        if hc.get("status") == "deprecated":
            warn(f"handoff {hc_id} is deprecated; downstream consumers should migrate")


def validate_handoff_references(status: dict):
    """Cross-references: producer/consumer artifact existence, produces/consumes pointing
    at real handoffs."""
    artifact_ids = {a.get("id") for a in (status.get("artifacts") or [])}
    handoff_ids = {hc.get("id") for hc in (status.get("handoff_contexts") or [])}

    for hc in status.get("handoff_contexts") or []:
        producer = hc.get("producer")
        if producer and producer not in artifact_ids:
            fail(f"handoff {hc['id']} producer references missing artifact: {producer}")
        for consumer in hc.get("consumed_by") or []:
            if consumer not in artifact_ids:
                fail(f"handoff {hc['id']} consumed_by references missing artifact: {consumer}")

    for a in status.get("artifacts") or []:
        for hc_id in a.get("produces_handoffs") or []:
            if hc_id not in handoff_ids:
                fail(f"artifact {a['id']} produces_handoffs references missing handoff: {hc_id}")
        for hc_id in a.get("consumes_handoffs") or []:
            if hc_id not in handoff_ids:
                fail(f"artifact {a['id']} consumes_handoffs references missing handoff: {hc_id}")


def validate_handoff_consumption(status: dict, schema: dict):
    """consumed_status entries must point at consumer in consumed_by; status enum valid."""
    valid = set(schema.get("handoff_consumer_statuses", []))
    arts_by_id = {a.get("id"): a for a in (status.get("artifacts") or [])}

    for hc in status.get("handoff_contexts") or []:
        consumers = set(hc.get("consumed_by") or [])
        for ce in hc.get("consumed_status") or []:
            consumer = ce.get("consumer")
            if consumer not in consumers:
                fail(f"handoff {hc['id']} consumed_status entry {consumer} is not listed in consumed_by")
            if ce.get("status") not in valid:
                fail(f"handoff {hc['id']} consumer {consumer} has invalid consumer status: {ce.get('status')}")
            cv = ce.get("consumed_version")
            if cv is not None and not isinstance(cv, int):
                fail(f"handoff {hc['id']} consumer {consumer} consumed_version must be int or null, got: {cv!r}")
            # Stale handoff cannot be safely consumed; an artifact still in 'ready' that consumes
            # a non-available handoff is suspicious.
            if hc.get("status") in ho_helpers.BLOCKING_HANDOFF_STATUSES:
                art = arts_by_id.get(consumer)
                if art and art.get("status") == "ready":
                    fail(f"artifact {consumer} is ready but consumes blocking handoff "
                         f"{hc['id']} (status={hc.get('status')})")
        # Archived handoffs should not appear in produces/consumes of any artifact in
        # an active status.
        if hc.get("status") == "archived":
            for a in status.get("artifacts") or []:
                if hc["id"] in (a.get("consumes_handoffs") or []) and a.get("status") in {"draft", "reviewed", "approved", "ready"}:
                    warn(f"active artifact {a['id']} still consumes archived handoff {hc['id']}")


def validate_handoff_preconditions(status: dict):
    """Each consumer artifact should have a precondition guarding the handoff it consumes."""
    arts_by_id = {a.get("id"): a for a in (status.get("artifacts") or [])}
    handoff_ids = {hc.get("id") for hc in (status.get("handoff_contexts") or [])}

    # Index preconditions by target.
    pcs_by_target: dict = {}
    for pc in status.get("preconditions") or []:
        pcs_by_target.setdefault(pc.get("target"), []).append(pc)

    for a in status.get("artifacts") or []:
        for hc_id in a.get("consumes_handoffs") or []:
            if hc_id not in handoff_ids:
                continue  # already covered by validate_handoff_references
            # Look for a precondition on this artifact that references this handoff.
            has_pc = False
            for pc in pcs_by_target.get(a["id"], []):
                for req in pc.get("requires") or []:
                    if isinstance(req, dict) and req.get("handoff") == hc_id:
                        has_pc = True
                        break
                if has_pc:
                    break
            if not has_pc:
                warn(f"artifact {a['id']} consumes handoff {hc_id} but has no precondition guarding it")


if __name__ == "__main__":
    main()
