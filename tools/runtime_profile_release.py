"""Whole-profile approval through the existing pinned promotion attestation.

This is a release adapter, not an installation configuration reader, feed
admission decision or runtime entry point. Source fallback remains independent.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re

from runtime_profile import ProfileError, SelectedProfile, select_profile, validate_profile

SCHEMA = "copilot-extensions.release-runtime-profiles"
APPROVAL_SOURCE = "promotion-main"


def release_profiles(index: dict) -> tuple:
    """Validate candidate data; this function establishes no approval."""
    extension = index.get("runtime_profiles")
    if extension is None and "runtime_profiles" not in index:
        return ()
    if (
        not isinstance(extension, dict)
        or set(extension) != {"schema", "version", "profiles"}
        or extension["schema"] != SCHEMA
        or type(extension["version"]) is not int or extension["version"] != 1
        or not isinstance(extension["profiles"], list)
        or not extension["profiles"]
    ):
        raise ProfileError("invalid release runtime-profile extension")
    artifacts = index.get("artifacts")
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        raise ProfileError("release profiles require artifact entries")
    identities = set()
    for item in artifacts:
        identity = item.get("artifact_id")
        if not isinstance(identity, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
            raise ProfileError("invalid release profile artifact identity")
        if identity in identities:
            raise ProfileError("duplicate release profile artifact identity")
        identities.add(identity)
    result, seen = [], set()
    for candidate in extension["profiles"]:
        profile = validate_profile(candidate)
        record = profile.record()
        if (
            record["approval_source"] != APPROVAL_SOURCE
            or record["mode"] != "artifact"
            or record["artifact"]["artifact_id"] not in identities
        ):
            raise ProfileError("profile is not bound to this promotion artifact channel")
        if profile.lock_id in seen:
            raise ProfileError("duplicate release runtime profile")
        seen.add(profile.lock_id)
        result.append(profile)
    return tuple(result)


def validate_release_bindings(index: dict, manifests: dict) -> None:
    """Pre-signing composition check against already-validated bundle manifests."""
    from packaging.utils import canonicalize_name
    from promotion_python_artifacts import _artifact_label

    for profile in release_profiles(index):
        record = profile.record()
        binding = manifests.get(record["artifact"]["artifact_id"])
        if binding is None:
            raise ProfileError("release profile has no validated first-party manifest")
        manifest, digest = binding
        label = _artifact_label(manifest)
        if (
            record["artifact"]["manifest_sha256"] != digest
            or record["release"] != {
                "identity": f"{label}:{manifest['version']}",
                "sha256": manifest["payload_hash"],
            }
            or record["component"] != label
        ):
            raise ProfileError("release profile differs from its exact first-party manifest")
        wheels = {canonicalize_name(item["name"]): item for item in record["wheels"]}
        for first_party in manifest["wheels"]:
            wheel = wheels.get(canonicalize_name(first_party["name"]))
            if wheel is None and first_party["role"] == "vendored-lib":
                continue
            if wheel is None or any(
                wheel[field] != first_party[field] for field in ("filename", "sha256")
            ):
                raise ProfileError("release profile omits or changes a first-party wheel")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError("duplicate JSON property in release profile subject")
        result[key] = value
    return result


def _attested_index(path: Path, *, max_subject_bytes: int, identity: dict) -> tuple[dict, str]:
    from installer_dependency_policy import _private_retained_root, _temporary_inputs
    from installer_wheel_inputs import WheelError, _ordinary
    from installer_wheel_snapshot import _open, _unchanged
    from publish_python_artifacts import verify_attestation_subject
    from promotion_python_artifacts import INDEX_NAME, SCHEMA as INDEX_SCHEMA

    if type(max_subject_bytes) is not int or max_subject_bytes <= 0:
        raise ProfileError("release subject byte limit must be an explicit positive integer")
    path = Path(os.path.abspath(path))
    try:
        before = _ordinary(path)
        if before.st_size > max_subject_bytes:
            raise ProfileError("release profile subject byte budget exceeded")
        with _open(path) as stream:
            opened = _unchanged(path, before, stream)
            data = stream.read(max_subject_bytes + 1)
            _unchanged(path, before, stream, opened=opened)
        if len(data) > max_subject_bytes:
            raise ProfileError("release profile subject byte budget exceeded")
        with _temporary_inputs(None) as temporary:
            root = _private_retained_root(Path(temporary)).resolve()
            subject = root / INDEX_NAME
            subject.write_bytes(data)
            subject.chmod(0o400)
            before = _ordinary(subject)
            with _open(subject) as stream:
                opened = _unchanged(subject, before, stream)
                verify_attestation_subject(subject, **identity)
                _unchanged(subject, before, stream, opened=opened)
                if stream.read(max_subject_bytes + 1) != data:
                    raise ProfileError("attested release profile subject changed")
        index = json.loads(data, object_pairs_hook=_unique_object)
        if (
            not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA
            or type(index.get("schema_version")) is not int or index["schema_version"] != 2
            or index.get("admission") != {
                "signer_digest": identity["signer_digest"],
                "source_digest": identity["source_digest"],
                "source_ref": identity["source_ref"],
            }
        ):
            raise ProfileError("release profile subject has another source admission")
        return index, "sha256:" + hashlib.sha256(data).hexdigest()
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, WheelError) as exc:
        raise ProfileError("release profile subject could not be retained or decoded") from exc


@dataclass(frozen=True)
class ReleaseSelection:
    selected: SelectedProfile
    subject_sha256: str
    repository: str
    signer_digest: str
    source_digest: str
    source_ref: str


def select_release_profile(
    path: Path, *, repository: str, signer_digest: str, source_digest: str,
    source_ref: str, release: dict, artifact: dict | None, target: dict,
    component: str, roots: list[str], max_subject_bytes: int,
    backend: str | None = None, retained_backend: str | None = None,
    existing_installation: bool = False, preference: tuple[str, ...] = (),
) -> ReleaseSelection:
    """Authenticate whole records before selection; accept no caller approval map.

    The existing installation authority supplies independently selected producer,
    source, manifest and target facts. Neither the subject nor ambient settings
    may choose those facts. This does not download or admit dependency bytes.
    An absent artifact cannot match, but never skips subject authentication.
    """
    if source_ref != "refs/heads/main" or signer_digest != source_digest:
        raise ProfileError("release profile approval requires pinned trusted Main")
    identity = dict(
        repository=repository, signer_digest=signer_digest,
        source_digest=source_digest, source_ref=source_ref,
    )
    index, digest = _attested_index(path, max_subject_bytes=max_subject_bytes, identity=identity)
    profiles = release_profiles(index)
    records = [profile.record() for profile in profiles]
    selected = select_profile(
        [record for record in records if record["artifact"] == artifact],
        approvals={profile.lock_id: APPROVAL_SOURCE for profile in profiles},
        release=release, target=target, component=component, roots=roots, mode="artifact",
        backend=backend, retained_backend=retained_backend,
        existing_installation=existing_installation, preference=preference,
    )
    return ReleaseSelection(selected, digest, **identity)
