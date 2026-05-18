"""HandoffContext helpers shared by validate / propagate / apply / render.

A HandoffContext (HC-xxx) records the produced facts, results, and constraints
of an upstream artifact, plus the consumption status of each downstream artifact.

This module is pure data manipulation — no I/O, no script entry points.

Glossary
--------
- producer: the artifact (LandingPrompt, Plan, Decision) that produces the handoff.
- consumer: a downstream artifact that needs the handoff before it can run.
- consumed_version: the version of the handoff a consumer has already absorbed.
- stale: handoff status when producer changed but new version not yet confirmed.
- invalidated: handoff is no longer valid (producer invalidated, design rejected).
"""
from __future__ import annotations

from typing import Optional


HANDOFF_STATUSES = {
    "draft", "available", "consumed", "partially_consumed",
    "stale", "invalidated", "deprecated", "archived",
}

CONSUMER_STATUSES = {"pending", "consumed", "stale", "rejected"}

# Statuses that block a consumer from staying ready.
BLOCKING_HANDOFF_STATUSES = {"stale", "invalidated", "deprecated", "archived"}


def get_handoff(status: dict, hc_id: str) -> Optional[dict]:
    for hc in status.get("handoff_contexts") or []:
        if hc.get("id") == hc_id:
            return hc
    return None


def all_handoffs(status: dict) -> list:
    return list(status.get("handoff_contexts") or [])


def producer_artifact_id(hc: dict) -> Optional[str]:
    return hc.get("producer")


def consumer_ids(hc: dict) -> list:
    return list(hc.get("consumed_by") or [])


def consumer_entry(hc: dict, consumer_id: str) -> Optional[dict]:
    for ce in hc.get("consumed_status") or []:
        if ce.get("consumer") == consumer_id:
            return ce
    return None


def consumers_with_status(hc: dict, statuses) -> list:
    statuses = set(statuses) if not isinstance(statuses, set) else statuses
    out = []
    for ce in hc.get("consumed_status") or []:
        if ce.get("status") in statuses:
            out.append(ce.get("consumer"))
    return out


def stale_handoffs(status: dict) -> list:
    return [hc for hc in all_handoffs(status) if hc.get("status") == "stale"]


def invalidated_handoffs(status: dict) -> list:
    return [hc for hc in all_handoffs(status) if hc.get("status") == "invalidated"]


def is_blocking_status(hc_status: str) -> bool:
    return hc_status in BLOCKING_HANDOFF_STATUSES


def evaluate_handoff_requirement(status: dict, req: dict) -> bool:
    """Evaluate a single precondition requires-clause that targets a handoff.

    Supported shapes:
        {handoff: HC-001, field: status, in: [available, consumed]}
        {handoff: HC-001, field: status, equals: available}
        {handoff: HC-001, field: version, min: 1}
    """
    hc_id = req.get("handoff")
    if not hc_id:
        return False
    hc = get_handoff(status, hc_id)
    if not hc:
        return False
    field = req.get("field", "status")
    if field == "status":
        actual = hc.get("status")
        if "in" in req:
            return actual in (req.get("in") or [])
        if "equals" in req:
            return actual == req.get("equals")
        return False
    if field == "version":
        try:
            actual = int(hc.get("version", 0))
        except (TypeError, ValueError):
            return False
        if "min" in req:
            try:
                return actual >= int(req["min"])
            except (TypeError, ValueError):
                return False
        if "equals" in req:
            try:
                return actual == int(req["equals"])
            except (TypeError, ValueError):
                return False
        return False
    return False


def is_handoff_requires(req: dict) -> bool:
    """Return True if a precondition.requires entry targets a handoff (not an artifact)."""
    return isinstance(req, dict) and "handoff" in req


def artifact_consumes_handoff(art: dict, hc_id: str) -> bool:
    return hc_id in (art.get("consumes_handoffs") or [])


def artifact_produces_handoff(art: dict, hc_id: str) -> bool:
    return hc_id in (art.get("produces_handoffs") or [])


def find_artifact_id_by_path(status: dict, path: str) -> Optional[str]:
    path_norm = (path or "").replace("\\", "/")
    for a in status.get("artifacts") or []:
        if (a.get("path") or "").replace("\\", "/") == path_norm:
            return a.get("id")
    return None


def ensure_handoff_fields_on_artifact(art: dict) -> None:
    """Backfill optional handoff fields on an artifact in-place. Never overwrites."""
    if "produces_handoffs" not in art:
        art["produces_handoffs"] = []
    if "consumes_handoffs" not in art:
        art["consumes_handoffs"] = []


def ensure_handoff_top_level(status: dict) -> None:
    """Backfill the top-level handoff_contexts key. Never overwrites."""
    if status.get("handoff_contexts") is None:
        status["handoff_contexts"] = []
