"""Compose configured source acquisition with the real installation authority.

This repository-only provider consumes the existing explicit runtime-source
policy. It neither enables source-backed updates nor treats Manager self-update
preferences, source receipts or artifact availability as source authorization.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import sys

from runtime_profile import ProfileError, ProfileUnavailable
from runtime_profile_policy import InstallationProfilePlan
from runtime_source_lock import dependencies

_ROOT = Path(__file__).resolve().parents[1]
for _parts in (("worktree-manager", "src"), ("libs", "agent-procutil", "src"), ("libs", "installation-context")):
    sys.path.insert(0, str(_ROOT.joinpath(*_parts)))

from installation_context import resolve_runtime_profile_policy
from installer_wheel_inputs import _check_deadline
import prepare_runtime_source
from versioned_runtime_source import SourceIdentity, read_source_identity
from worktree_manager import runtime_source, runtime_source_config
from worktree_manager.source_config import default_root


@dataclass(frozen=True)
class AuthenticatedSource:
    payload: Path
    identity: SourceIdentity
    canonical: bytes
    _verification: Callable[[], None] = field(repr=False, compare=False)

    def record(self) -> dict:
        return json.loads(self.canonical)

    def verify(self) -> None:
        self._verification()


def _policy(plan: InstallationProfilePlan, installation: dict) -> dict:
    current = resolve_runtime_profile_policy(**installation)["runtimeProfiles"]
    if (
        current.get("status") != "ready" or current != plan.policy()
        or plan.release_selection is not None
        or plan.source_trigger not in (
            "release-index-unavailable", "artifact-unavailable", "approved-profile-unavailable",
        )
        or not current["selection"]["allowSourceFallback"]
    ):
        raise ProfileError("source authentication differs from its installation authority")
    return current


@contextmanager
def retain_authenticated_source(
    plan: InstallationProfilePlan, *, installation: dict, deadline: float,
):
    """Hold the actual configured-source lease through preparation and use."""
    _check_deadline(deadline)
    policy = _policy(plan, installation)
    root = default_root().absolute()
    selection = runtime_source_config.configured_source(root)
    if selection.channel == "marketplace":
        raise ProfileUnavailable("configured source authentication provider is not selected")
    with runtime_source.acquire(selection, root=root, deadline=deadline) as acquired:
        _check_deadline(deadline)
        if default_root().absolute() != root or runtime_source_config.configured_source(root) != selection:
            raise ProfileError("configured runtime source changed during authentication")
        with dependencies._temporary_inputs(None) as temporary:
            output = dependencies._private_retained_root(Path(temporary))
            prepared = prepare_runtime_source.prepare(
                acquired.checkout, acquired.commit, policy["owner"]["pluginId"], output,
                branch=selection.branch, deadline=deadline,
            )
            if (
                prepared.identity.source_commit != acquired.commit
                or prepared.identity.source_tree != acquired.tree
            ):
                raise ProfileError("prepared source differs from the authenticated commit and tree")
            record = {
                "schema": "copilot-extensions.authenticated-runtime-source", "version": 1,
                "provider": "configured-runtime-source", "owner_policy": policy["policyId"],
                "selection_sha256": "sha256:" + hashlib.sha256(json.dumps({
                    "selection": selection.document(), "owner": str(root),
                }, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "mode": "source", "trigger": plan.source_trigger,
                "source": prepared.identity.document(),
            }
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()

            def verify():
                _check_deadline(deadline)
                _policy(plan, installation)
                if default_root().absolute() != root or runtime_source_config.configured_source(root) != selection:
                    raise ProfileError("configured runtime source changed during consumption")
                if read_source_identity(
                    prepared.payload, prepared.identity.package_version, plugin=prepared.identity.plugin,
                ) != prepared.identity:
                    raise ProfileError("authenticated prepared source changed during consumption")
                _check_deadline(deadline)

            verify()
            yield AuthenticatedSource(prepared.payload, prepared.identity, canonical, verify)
            verify()
