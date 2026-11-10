"""Read accepted relationship defaults; native identity and binding stay with Worktrees."""

from __future__ import annotations

from pathlib import Path

from . import preferences as codec
from .preference_exchange import _admitted, _distinct, _plain_path, _state

SCHEMA = "agent-machines.preferences.repositories"


def project(state: Path, *, machine: str, harness: str, local: Path | None = None) -> dict:
    _, paths = _state(state)
    policy, policy_digest = codec.read_snapshot(paths[0], validator=codec.validate_policy)
    if "repositories" not in policy["domains"]:
        raise codec.PreferenceError("domain-not-permitted")
    accepted, accepted_digest = codec.read_snapshot(paths[2], optional=True)
    overrides, local_digest = None, None
    if local is not None:
        local = _plain_path(local)
        _distinct([*paths, local])
        overrides, local_digest = codec.read_snapshot(local, local=True)
    if not _admitted(accepted, policy) or not _admitted(overrides, policy):
        raise codec.PreferenceError("domain-not-permitted")
    projection = codec.preview(
        None, accepted, publisher=policy["publisher"], machine=machine, harness=harness, local=overrides,
    )
    return {
        "schema": SCHEMA, "version": 1, "read_only": True,
        "input_revision": codec.digest({
            "paths": [str(path) for path in paths], "local_path": str(local) if local else None,
            "policy": policy_digest, "accepted": accepted_digest, "local": local_digest,
            "machine": machine, "harness": harness,
        }),
        "accepted_revision": projection["accepted_revision"],
        "scope": projection["scope"], "knowledge_preference": projection["knowledge_preference"],
        "provenance": projection["provenance"].get(f"repositories.knowledge:{harness}"),
        "configuration_applied": False, "binding_changed": False,
    }
