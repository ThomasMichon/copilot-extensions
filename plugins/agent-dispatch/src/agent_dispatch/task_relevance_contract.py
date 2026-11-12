"""Relevance support declared by the actual serving coordinator generation."""

from __future__ import annotations

RELEVANCE_PROTOCOL = "task_relevance/v1"
RELEVANCE_PATH = "/task-relevance"
RELEVANCE_SCOPE = "local-direct-command-emitter"
RELEVANCE_EFFECTIVE_SCOPE = "local-effective-emitter-evaluator"


def relevance_contract(control_token: str | None) -> dict:
    return {
        "protocol": RELEVANCE_PROTOCOL, "supported_versions": [1],
        "registration_scopes": [RELEVANCE_SCOPE, RELEVANCE_EFFECTIVE_SCOPE],
        "control_authority_configured": bool(control_token),
    }
