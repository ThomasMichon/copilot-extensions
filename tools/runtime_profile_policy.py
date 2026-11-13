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
    allow_compatible_target: bool = False,
) -> InstallationProfilePlan:
    """Apply the real owner-bound policy without a second configuration reader.

    None represents an unavailable discovery result, not a failed verification.
    All authentication, malformed-record, I/O and cancellation failures propagate;
    only the selector's typed absence can request permitted source fallback.
    """
    validate_profile_preference(preference)
    if type(allow_compatible_target) is not bool:
        raise ProfileError("compatible target selection must be explicit")
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
                allow_compatible_target=allow_compatible_target,
            )
        except ProfileUnavailable:
            trigger = "artifact-unavailable" if artifact is None else "approved-profile-unavailable"
        else:
            return InstallationProfilePlan(frozen, selected, None)
    if not selection["allowSourceFallback"]:
        raise ProfileUnavailable(f"source fallback is disabled: {trigger}")
    return InstallationProfilePlan(frozen, None, trigger)


def plan_discovered_installation_profile(
    directory: Path, origin, *, installation: dict, repository: str,
    source_checkout: Path, main_commit: str, target: dict, component: str,
    roots: list[str], max_subject_bytes: int, deadline: float,
    preference: tuple[str, ...] = (), runner=None, verify_subject=None,
) -> InstallationProfilePlan:
    """Associate real source-bound discovery and bundle facts with owner intent.

    The caller retains acquisition custody, including when an admitted child
    borrows it through the original owner's live callback. Neither absent
    profiles nor unavailable artifacts may be asserted by that caller.
    """
    from artifact_attestation import INDEX_NAME
    from artifact_release_discovery import discover_promoted_source, read_selected_source_artifact
    from installer_wheel_inputs import _check_deadline
    from runtime_source_authentication import AuthenticatedSource

    _check_deadline(deadline)
    if (
        not isinstance(origin, AuthenticatedSource) or origin.record().get("mode") != "origin"
        or origin.identity.source_commit != main_commit or origin.identity.plugin != component
        or not source_checkout.is_absolute() or not source_checkout.is_dir()
        or origin.acquired is not None and (
            origin.acquired.checkout.resolve() != source_checkout.resolve()
            or origin.acquired.commit != main_commit
        )
    ):
        raise ProfileError("release discovery differs from its retained source acquisition")
    origin.verify()
    discovered = discover_promoted_source(
        directory, repository=repository, repo=source_checkout,
        main_commit=main_commit, deadline=deadline, runner=runner, verify_subject=verify_subject,
    )
    origin.verify()
    release_index, artifact = None, None
    release = {
        "identity": "source:" + origin.identity.source_commit,
        "sha256": "sha256:" + origin.identity.assembly_hash,
    }
    admission = {
        "signer_digest": main_commit, "source_digest": main_commit,
        "source_ref": "refs/heads/main",
    }
    if discovered is not None:
        index, assets = discovered
        release_index = directory / INDEX_NAME
        admission = index["admission"]
        binding = read_selected_source_artifact(
            directory, index=index, assets=assets, repository=repository,
            component=component, version=origin.identity.package_version,
            candidate_root=source_checkout, runner=runner, verify_subject=verify_subject,
        )
        if binding is not None:
            manifest, manifest_digest = binding
            release = {
                "identity": f"{component}:{manifest['version']}",
                "sha256": manifest["payload_hash"],
            }
            artifact = {
                "artifact_id": manifest["artifact_id"], "manifest_sha256": manifest_digest,
            }
    origin.verify()
    result = plan_installation_profile(
        installation=installation, release_index=release_index, repository=repository,
        **admission, release=release, artifact=artifact,
        target=target, component=component, roots=roots, max_subject_bytes=max_subject_bytes,
        backend=origin.policy()["selection"]["backend"], preference=preference,
    )
    if result.policy() != origin.policy():
        raise ProfileError("release selection differs from the original installation policy")
    origin.verify()
    _check_deadline(deadline)
    return result
