"""Compose attested first-party bytes and current governed dependency inputs."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import zipfile

from artifact_attestation import INDEX_LIMIT, INDEX_NAME, verify_attestation_subject
from artifact_bundle_validation import validate_bundle_binding
from installer_dependency_policy import _private_retained_root, _temporary_inputs
from installer_wheel_acquisition import AcquisitionLimits, acquire_wheel_inputs
from installer_wheel_inputs import WheelLimits, _check_deadline, _member_chunks, _ordinary
from installer_wheel_snapshot import WheelInstallInputs, _digest, _open, _unchanged, retain_wheel_inputs
from installation_context import resolve_runtime_profile_policy
from packaging.utils import canonicalize_name
from packaging.specifiers import SpecifierSet
from promotion_source_history import is_ancestor, read_promoted_source
from runtime_feed_configuration import retain_native_feed_inputs
from runtime_input_revocation import verify_digests_not_revoked
from runtime_profile import ProfileError, RuntimeProfile
from runtime_profile_release import select_release_profile, validate_release_bindings
from runtime_profile_wheels import profile_wheel_inputs
from runtime_source_authentication import AuthenticatedSource
from versioned_runtime_source import SourceIdentity


@dataclass(frozen=True)
class ArtifactRuntimeInputs:
    payload: Path
    identity: SourceIdentity
    profile: RuntimeProfile
    wheels: WheelInstallInputs
    _verification: Callable[[], None] = field(repr=False, compare=False)

    @property
    def runtime_version(self) -> str:
        return self.identity.runtime_version + ".profile." + self.wheels.receipt()["lock_id"][7:23]

    def verify(self) -> None:
        self._verification()
        self.wheels.verify()


def _selection(plan, directory, deadline):
    selected = plan.release_selection
    if selected is None or plan.source_trigger is not None:
        raise ProfileError("artifact inputs require an actual selected release profile")
    record = selected.selected.profile.record()
    checked = select_release_profile(
        directory / INDEX_NAME, repository=selected.repository,
        signer_digest=selected.signer_digest, source_digest=selected.source_digest,
        source_ref=selected.source_ref, release=record["release"], artifact=record["artifact"],
        target=record["target"], component=record["component"], roots=record["roots"],
        backend=record["backend"], preference=(selected.selected.profile.lock_id,),
        max_subject_bytes=INDEX_LIMIT,
    )
    _check_deadline(deadline)
    if checked != selected:
        raise ProfileError("artifact inputs differ from their selected attested subject")
    return checked.selected.profile


@contextmanager
def retain_artifact_runtime_inputs(
    plan, origin: AuthenticatedSource, directory: Path, *, installation: dict,
    source_checkout: Path, uv: str, python: Path, procutil,
    acquisition_limits: AcquisitionLimits, wheel_limits: WheelLimits,
    max_snapshot_bytes: int, deadline: float, env: dict[str, str] | None = None,
    revoked_inputs=None,
):
    """Keep both byte owners and the original source owner live through health.

    The release authenticates the complete profile and first-party bundle.
    Only its remaining distributions are acquired from the native governed feed.
    This neither selects fallback nor grants lifecycle/publication authority.
    """
    _check_deadline(deadline)
    acquisition_limits.verify()
    if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0:
        raise ProfileError("artifact inputs require an explicit snapshot budget")
    if (
        not isinstance(origin, AuthenticatedSource) or origin.record().get("mode") != "origin"
        or not source_checkout.is_absolute() or not source_checkout.is_dir()
        or origin.acquired is not None and (
            origin.acquired.checkout.resolve() != source_checkout.resolve()
            or origin.acquired.commit != origin.identity.source_commit
        )
    ):
        raise ProfileError("artifact inputs differ from the retained source acquisition")
    policy = plan.policy()
    active = True

    def verify_owner():
        if not active:
            raise ProfileError("artifact runtime input custody is closed")
        _check_deadline(deadline)
        origin.verify()
        current = resolve_runtime_profile_policy(
            **installation, backend=policy["selection"]["backend"],
        )["runtimeProfiles"]
        if (
            current != policy or origin.policy() != policy
            or current.get("status") != "ready"
        ):
            raise ProfileError("artifact input policy differs from its current installation owner")

    try:
        verify_owner()
        profile = _selection(plan, directory, deadline)
        record = profile.record()
        if record["component"] != origin.identity.plugin:
            raise ProfileError("artifact component differs from its original source owner")
        with ExitStack() as held:
            index_path = directory / INDEX_NAME
            before = _ordinary(index_path)
            index_stream = held.enter_context(_open(index_path))
            opened = _unchanged(index_path, before, index_stream)
            if _digest(index_stream, INDEX_LIMIT, deadline)[0] != plan.release_selection.subject_sha256:
                raise ProfileError("selected artifact subject changed after authentication")
            index = json.loads(index_stream.read(INDEX_LIMIT + 1))
            promoted = read_promoted_source(
                source_checkout, origin.identity.source_commit, deadline=deadline,
            )
            if (
                index["dev_head"] != promoted.dev_head or index["content_tree"] != promoted.content_tree
                or not is_ancestor(
                    source_checkout, plan.release_selection.signer_digest, promoted.main_before,
                    deadline=deadline,
                )
            ):
                raise ProfileError("artifact subject differs from acquired promoted source history")
            entries = [
                entry for entry in index["artifacts"]
                if entry["artifact_id"] == record["artifact"]["artifact_id"]
            ]
            if len(entries) != 1:
                raise ProfileError("selected artifact has no unique attested bundle")
            entry = entries[0]
            bundle = directory / entry["filename"]
            bundle_before = _ordinary(bundle)
            if bundle_before.st_size > max_snapshot_bytes:
                raise ProfileError("artifact bundle exceeds the snapshot byte budget")
            stream = held.enter_context(_open(bundle))
            bundle_opened = _unchanged(bundle, bundle_before, stream)
            verify_attestation_subject(
                bundle, repository=plan.release_selection.repository,
                signer_digest=plan.release_selection.signer_digest,
                source_digest=plan.release_selection.source_digest,
                source_ref=plan.release_selection.source_ref,
            )
            manifest, manifest_digest = validate_bundle_binding(
                bundle, entry, candidate_root=source_checkout,
            )
            if (
                manifest_digest != record["artifact"]["manifest_sha256"]
                or manifest["version"] != origin.identity.package_version
            ):
                raise ProfileError("artifact manifest differs from the selected whole profile")
            validate_release_bindings(
                {**index, "runtime_profiles": {
                    **index["runtime_profiles"], "profiles": [record],
                }},
                {entry["artifact_id"]: (manifest, manifest_digest)},
            )
            first_party = {canonicalize_name(wheel["name"]): wheel for wheel in manifest["wheels"]}
            selected_first = [
                wheel for wheel in record["wheels"] if wheel["name"] in first_party
            ]
            third_party = [
                wheel for wheel in record["wheels"] if wheel["name"] not in first_party
            ]
            if not selected_first or any(
                any(wheel[key] != first_party[wheel["name"]][key] for key in ("filename", "sha256"))
                for wheel in selected_first
            ):
                raise ProfileError("artifact wheel set differs from its first-party manifest")
            digests = [
                profile.lock_id, plan.release_selection.subject_sha256, "sha256:" + entry["sha256"],
                manifest_digest, "sha256:" + origin.identity.assembly_hash,
                *(wheel["sha256"] for wheel in record["wheels"]),
            ]
            verify_digests_not_revoked(digests, revoked_inputs)
            temporary = held.enter_context(_temporary_inputs(None))
            root = _private_retained_root(Path(temporary)).resolve()
            paths, first_bytes = {}, 0
            with zipfile.ZipFile(stream) as archive:
                for wheel in selected_first:
                    _check_deadline(deadline)
                    member = archive.getinfo(wheel["filename"])
                    first_bytes += member.file_size
                    if (
                        member.file_size > wheel_limits.max_archive_bytes
                        or first_bytes > acquisition_limits.max_closure_bytes
                        or 2 * first_bytes > max_snapshot_bytes
                    ):
                        raise ProfileError("first-party artifact wheels exceed their byte budget")
                    path = root / wheel["filename"]
                    with path.open("xb") as output:
                        for chunk in _member_chunks(
                            stream, archive, member, limit=member.file_size, deadline=deadline,
                        ):
                            output.write(chunk)
                    path.chmod(0o400)
                    paths[wheel["filename"]] = path
            feed, acquired, acquisition = None, None, None
            held_bytes = first_bytes
            if third_party:
                feed = held.enter_context(retain_native_feed_inputs(
                    plan, installation=installation, uv=uv, python=python, env=env,
                    deadline=deadline,
                ))
                acquired = held.enter_context(acquire_wheel_inputs(
                    third_party, feeds=feed.feeds, download_origins=feed.download_origins,
                    cutoffs=feed.cutoffs, policy_id=feed.policy_id,
                    limits=replace(
                        acquisition_limits,
                        max_temporary_bytes=min(
                            acquisition_limits.max_temporary_bytes, max_snapshot_bytes - first_bytes,
                        ),
                        max_closure_bytes=acquisition_limits.max_closure_bytes - first_bytes,
                    ),
                    deadline=deadline, ssl_context=feed.context, proxies=feed.proxies,
                    procutil=procutil,
                ))
                acquisition = acquired.evidence()
                selected_wheels = {wheel["filename"]: wheel for wheel in third_party}
                if any(
                    SpecifierSet(item["requires_python"])
                    != SpecifierSet(selected_wheels[item["filename"]]["requires_python"])
                    for item in acquisition["files"] if "requires_python" in item
                ):
                    raise ProfileError("artifact dependency metadata differs from its selected index hint")
                held_bytes += acquisition["temporary_highwater_bytes"]
                paths.update(acquired.snapshot.paths)
            with retain_wheel_inputs(
                paths, {wheel["filename"]: wheel["sha256"] for wheel in record["wheels"]},
                max_wheels=acquisition_limits.max_packages,
                max_archive_bytes=wheel_limits.max_archive_bytes,
                max_total_bytes=acquisition_limits.max_closure_bytes,
                max_snapshot_bytes=max_snapshot_bytes - held_bytes, deadline=deadline,
            ) as snapshot:
                inputs = profile_wheel_inputs(
                    profile, snapshot, max_archive_bytes=acquisition_limits.max_closure_bytes,
                    max_unpacked_bytes=wheel_limits.max_expanded_bytes,
                    max_metadata_bytes=wheel_limits.max_metadata_member_bytes,
                    limits=wheel_limits, deadline=deadline,
                )
                inputs = replace(inputs, binding_json=json.dumps({
                    "schema": "copilot-extensions.artifact-runtime-inputs", "version": 1,
                    "profile": record, "source": origin.record(), "policy": policy,
                    "artifact_manifest": manifest,
                    "release_subject": plan.release_selection.subject_sha256,
                    "bundle": "sha256:" + entry["sha256"],
                    "acquisition": None if acquisition is None else {
                        key: acquisition[key] for key in ("schema", "version", "policy_id", "files")
                    },
                }, sort_keys=True, separators=(",", ":"), allow_nan=False))

                def verify():
                    verify_owner()
                    _unchanged(index_path, before, index_stream, opened=opened)
                    if _digest(index_stream, INDEX_LIMIT, deadline)[0] != plan.release_selection.subject_sha256:
                        raise ProfileError("selected artifact subject changed during consumption")
                    _unchanged(bundle, bundle_before, stream, opened=bundle_opened)
                    if validate_bundle_binding(bundle, entry, candidate_root=source_checkout) != (
                        manifest, manifest_digest,
                    ):
                        raise ProfileError("selected artifact changed during consumption")
                    if feed is not None:
                        feed.verify()
                        acquired.snapshot.verify()
                    verify_digests_not_revoked(
                        [*digests, inputs.receipt()["lock_id"]], revoked_inputs,
                    )

                result = ArtifactRuntimeInputs(
                    origin.payload, origin.identity, profile, inputs, verify,
                )
                result.verify()
                yield result
                result.verify()
    finally:
        active = False
