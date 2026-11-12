"""Compose configured source acquisition with the real installation authority.

This repository-only provider consumes the existing explicit runtime-source
policy. It neither enables source-backed updates nor treats Manager self-update
preferences, source receipts or artifact availability as source authorization.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING

from runtime_profile_errors import ProfileError, ProfileUnavailable

if TYPE_CHECKING:
    from runtime_profile_policy import InstallationProfilePlan

_ROOT = Path(__file__).resolve().parents[1]
for _parts in (
    ("worktree-manager", "src"), ("libs", "agent-procutil", "src"),
    ("libs", "installation-context"), ("libs", "installer-engine"),
):
    sys.path.insert(0, str(_ROOT.joinpath(*_parts)))

from installation_context import resolve_runtime_profile_policy
import installer_dependency_policy as dependencies
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
    acquired: runtime_source.ResolvedSource | None = field(default=None, repr=False, compare=False)
    _policy_json: bytes | None = field(default=None, repr=False, compare=False)

    def record(self) -> dict:
        return json.loads(self.canonical)

    def verify(self) -> None:
        self._verification()

    def policy(self) -> dict:
        if self._policy_json is None:
            raise ProfileError("source origin has no retained installation policy")
        return json.loads(self._policy_json)

    def for_plan(self, plan: InstallationProfilePlan, *, installation: dict) -> AuthenticatedSource:
        """Associate a held origin only after real discovery selects source mode."""
        self.verify()
        if isinstance(plan, _OriginPolicy):
            raise ProfileError("source origin has no selected source fallback plan")
        policy = _policy(plan, installation)
        record = self.record()
        if record.get("owner_policy") != policy["policyId"]:
            raise ProfileError("source origin differs from the selected profile policy")
        if record.get("mode") != "origin" or record.get("trigger") is not None:
            raise ProfileError("source origin has already been associated with a source plan")

        def verify():
            self.verify()
            _policy(plan, installation)

        return replace(
            self, canonical=json.dumps({
                **record, "mode": "source", "trigger": plan.source_trigger,
            }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
            _verification=verify,
        )


@dataclass(frozen=True)
class _OriginPolicy:
    """A policy snapshot for acquisition/discovery, never source-mode approval."""

    canonical: bytes
    source_trigger: None = None
    release_selection: None = None

    def policy(self) -> dict:
        return json.loads(self.canonical)


@contextmanager
def borrow_installation_origin(
    payload: Path, record: dict, policy: dict, *, installation: dict,
    verify_owner: Callable[[], None], deadline: float,
):
    """Borrow a caller-held origin through its live invocation-local verifier.

    The process retaining acquisition custody supplies the original facts and
    callback. These facts alone are not authentication and never reacquire the
    source's exclusive lease. Release discovery stays with the acquisition owner.
    """
    _check_deadline(deadline)
    if not callable(verify_owner):
        raise ProfileError("borrowed source origin has no live acquisition owner")
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    policy_json = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    retained = json.loads(canonical)
    intent = _OriginPolicy(policy_json)
    if (
        retained.get("schema") != "copilot-extensions.authenticated-runtime-source"
        or type(retained.get("version")) is not int or retained["version"] != 1
        or retained.get("provider") not in ("configured-runtime-source", "registered-marketplace-source")
        or retained.get("mode") != "origin" or retained.get("trigger") is not None
        or retained.get("owner_policy") != policy.get("policyId")
        or not isinstance(retained.get("selection_sha256"), str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", retained["selection_sha256"]) is None
        or not isinstance(retained.get("source"), dict)
        or not payload.is_absolute()
    ):
        raise ProfileError("borrowed source origin differs from its acquisition owner")
    active = True
    verify_owner()
    current = _policy(intent, installation)
    identity = read_source_identity(
        payload, retained["source"].get("package_version"),
        plugin=current["owner"]["pluginId"],
    )
    if identity is None or identity.document() != retained["source"]:
        raise ProfileError("borrowed source origin bytes differ from acquisition")

    def verify() -> None:
        if not active:
            raise ProfileError("borrowed source origin custody is closed")
        _check_deadline(deadline)
        verify_owner()
        current = _policy(intent, installation)
        selected = read_source_identity(
            payload, retained["source"].get("package_version"),
            plugin=current["owner"]["pluginId"],
        )
        if selected is None or selected != identity or selected.document() != retained["source"]:
            raise ProfileError("borrowed source origin bytes differ from acquisition")
        _check_deadline(deadline)

    try:
        verify()
        yield AuthenticatedSource(
            payload, identity, canonical, verify, _policy_json=policy_json,
        )
        verify()
    finally:
        active = False


def _policy(plan: InstallationProfilePlan, installation: dict) -> dict:
    expected = plan.policy()
    current = resolve_runtime_profile_policy(
        **installation, backend=expected.get("selection", {}).get("backend"),
    )["runtimeProfiles"]
    discovery = isinstance(plan, _OriginPolicy)
    if (
        current.get("status") != "ready" or current != expected
        or plan.release_selection is not None
        or (not discovery and plan.source_trigger not in (
            "release-index-unavailable", "artifact-unavailable", "approved-profile-unavailable",
        ))
        or (not discovery and not current["selection"]["allowSourceFallback"])
    ):
        raise ProfileError("source authentication differs from its installation authority")
    return current


@contextmanager
def retain_installation_origin(*, installation: dict, deadline: float, backend: str | None = None):
    """Acquire the real source before release selection or Packaging bootstrap."""
    _check_deadline(deadline)
    policy = resolve_runtime_profile_policy(
        **installation, backend=backend,
    )["runtimeProfiles"]
    if policy.get("status") != "ready":
        raise ProfileError("source origin has no ready installation policy")
    intent = _OriginPolicy(json.dumps(
        policy, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode())
    with retain_installation_source(intent, installation=installation, deadline=deadline) as source:
        yield source


@contextmanager
def retain_installation_source(
    plan: InstallationProfilePlan, *, installation: dict, deadline: float,
):
    """Select the declared provider once; failed authentication never reselects."""
    _check_deadline(deadline)
    _policy(plan, installation)
    selection = runtime_source_config.configured_source(default_root().absolute())
    if selection.channel == "marketplace":
        from runtime_marketplace_source import retain_authenticated_marketplace_source

        provider = retain_authenticated_marketplace_source
    else:
        provider = retain_authenticated_source
    with provider(plan, installation=installation, deadline=deadline) as authenticated:
        yield authenticated


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
                "mode": "origin" if isinstance(plan, _OriginPolicy) else "source",
                "trigger": plan.source_trigger,
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
            yield AuthenticatedSource(
                prepared.payload, prepared.identity, canonical, verify, acquired,
                json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
            )
            verify()
