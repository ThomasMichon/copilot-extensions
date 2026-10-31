"""Compose installation-owned intent with authenticated release selection.

This repository-only adapter never installs or approves source bytes. A source
request still needs independent source verification, its own exact lock and feed
admission before the existing constructor may consume it.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installation-context"))
from installation_context import resolve_runtime_profile_policy
from runtime_profile import ProfileError, ProfileUnavailable, validate_profile_preference
from runtime_profile_release import ReleaseSelection, select_release_profile


@dataclass(frozen=True)
class InstallationProfilePlan:
    """Frozen policy intent, not installation permission or source admission."""

    policy_json: bytes
    release_selection: ReleaseSelection | None
    source_trigger: str | None

    def policy(self) -> dict:
        return json.loads(self.policy_json)


def plan_installation_profile(
    *, installation: dict, release_index: Path | None,
    repository: str, signer_digest: str, source_digest: str, source_ref: str,
    release: dict, artifact: dict | None, target: dict, component: str,
    roots: list[str], max_subject_bytes: int,
    backend: str | None = None, retained_backend: str | None = None,
    existing_installation: bool = False, preference: tuple[str, ...] = (),
) -> InstallationProfilePlan:
    """Apply the real owner-bound policy without a second configuration reader.

    None represents an unavailable discovery result, not a failed verification.
    All authentication, malformed-record, I/O and cancellation failures propagate;
    only the selector's typed absence can request permitted source fallback.
    """
    validate_profile_preference(preference)
    resolution = resolve_runtime_profile_policy(
        **installation, backend=backend, retained_backend=retained_backend,
        existing_installation=existing_installation,
    )
    policy = resolution["runtimeProfiles"]
    if policy["status"] != "ready":
        raise ProfileError(f"installation profile policy is blocked: {policy['reason']}")
    frozen = json.dumps(
        policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    selection = policy["selection"]
    trigger = "release-index-unavailable" if release_index is None else None
    if trigger is None:
        try:
            selected = select_release_profile(
                release_index, repository=repository, signer_digest=signer_digest,
                source_digest=source_digest, source_ref=source_ref,
                release=release, artifact=artifact, target=target,
                component=component, roots=roots, max_subject_bytes=max_subject_bytes,
                backend=selection["backend"], preference=preference,
            )
        except ProfileUnavailable:
            trigger = "artifact-unavailable" if artifact is None else "approved-profile-unavailable"
        else:
            return InstallationProfilePlan(frozen, selected, None)
    if not selection["allowSourceFallback"]:
        raise ProfileUnavailable(f"source fallback is disabled: {trigger}")
    return InstallationProfilePlan(frozen, None, trigger)
