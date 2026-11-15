"""Relevance support declared by the actual serving coordinator generation."""

from __future__ import annotations

import hashlib
import json

RELEVANCE_PROTOCOL = "task_relevance/v1"
RELEVANCE_PATH = "/task-relevance"
RELEVANCE_SCOPE = "local-direct-command-emitter"
RELEVANCE_EFFECTIVE_SCOPE = "local-effective-emitter-evaluator"
CLOSEOUT_PROTOCOL = "task_relevance_closeout/v1"


def emitter_spec_revision(spec: dict) -> str:
    """Correlate a running emitter's spec with the receiver's current definition."""
    encoded = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def relevance_contract(control_token: str | None) -> dict:
    return {
        "protocol": RELEVANCE_PROTOCOL, "supported_versions": [1],
        "registration_scopes": [RELEVANCE_SCOPE, RELEVANCE_EFFECTIVE_SCOPE],
        "bounded_batches": True,
        "periodic_evaluation": True,
        "control_authority_configured": bool(control_token),
        "owner_closeout": {
            "protocol": CLOSEOUT_PROTOCOL, "supported_versions": [1],
            "path": "/tasks/{task_id}/relevance-closeout",
        },
    }
