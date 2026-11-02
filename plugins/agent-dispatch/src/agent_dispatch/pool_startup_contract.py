"""Explicit supervision transport; support never activates an ordinary lane."""

from __future__ import annotations

POOL_STARTUP_PROTOCOL = "pool_startup/v1"
POOL_STARTUP_VERSION = 1
POOL_STARTUP_PATH = "/pool-placements/startup/v1"


def pool_startup_contract() -> dict[str, object]:
    """Describe the running provider's bounded startup operations."""
    return {
        "protocol": POOL_STARTUP_PROTOCOL,
        "supported_versions": [POOL_STARTUP_VERSION],
        "supported_drivers": ["cli"],
        "configured_startup_timeout": True,
        "durable_claim_budget": True,
        "same_native_decision": True,
        "exhaustion_retains_capacity": True,
        "classified_startup_outcomes": True,
        "fenced_same_native_retry": True,
        "fenced_retry_completion": True,
        "startup_attempt_limit": 3,
        "nudge_seconds": 300,
        "decision_seconds": 1800,
        "cooldown_seconds": [3600, 10800, 21600, 43200],
        "automatic_lane_migration": False,
        "startup_retry_policy_complete": False,
    }
