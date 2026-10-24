"""Exact-profile metadata carried by the existing pinned Main release channel.

This source-tool adapter authenticates approval, not wheel availability, feed
policy, artifact integrity or permission to install. No consumer is enabled.
"""
from __future__ import annotations

import json
import re
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime_profile import SelectedProfile


def freeze_release_profiles(value: object) -> list[dict]:
    """Validate and detach producer metadata without granting it approval."""
    from runtime_profile import ProfileError, validate_profile

    if not isinstance(value, list):
        raise ProfileError("release runtime_profiles must be a list")
    profiles = [validate_profile(record) for record in value]
    if len({profile.lock_id for profile in profiles}) != len(profiles):
        raise ProfileError("duplicate release runtime profile")
    return [profile.record() for profile in profiles]


def promotion_profiles(value: object, *, schema_version: int = 2) -> list[dict]:
    """Adapt optional metadata to the existing promotion error contract."""
    from promotion_python_artifacts import PromotionArtifactError
    from runtime_profile import ProfileError

    try:
        if type(schema_version) is not int or schema_version != 2:
            raise ProfileError("runtime profiles require a source-owned artifact index")
        return freeze_release_profiles(value)
    except ProfileError as exc:
        raise PromotionArtifactError("invalid promotion runtime profiles") from exc


def select_release_profile(
    index_path: Path, *, repository: str, signer_digest: str, dev_head: str,
    content_tree: str, approval_source: str, release: dict, target: dict,
    component: str, roots: list[str], mode: str, backend: str | None = None,
    retained_backend: str | None = None, existing_installation: bool = False,
    preference: tuple[str, ...] = (),
) -> SelectedProfile:
    """Select only metadata authenticated by the authority's pinned Main signer.

    The existing installation authority supplies every expected identity and
    source reference independently of the subject. An immutable source index
    retains its original admission signer even after later republication.
    """
    from promotion_python_artifacts import INDEX_NAME, SCHEMA, _MANIFEST_LIMIT
    from publish_python_artifacts import verify_attestation_subject
    from runtime_profile import ProfileError, select_profile, validate_profile

    if any(
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None
        for value in (signer_digest, dev_head, content_tree)
    ):
        raise ProfileError("invalid expected release profile identity")
    if not isinstance(approval_source, str) or re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}", approval_source,
    ) is None:
        raise ProfileError("invalid expected profile approval source")
    try:
        if not stat.S_ISREG(index_path.lstat().st_mode):
            raise ProfileError("release profile index must be an ordinary file")
        with index_path.open("rb") as stream:
            subject = stream.read(_MANIFEST_LIMIT + 1)
        if len(subject) > _MANIFEST_LIMIT:
            raise ProfileError("release profile index exceeds its existing size limit")
        with tempfile.TemporaryDirectory(prefix="release-profile-subject-") as temporary:
            retained = Path(temporary) / INDEX_NAME
            retained.write_bytes(subject)
            verify_attestation_subject(
                retained, repository=repository, signer_digest=signer_digest,
                source_digest=signer_digest, source_ref="refs/heads/main",
            )
            if retained.is_symlink() or retained.read_bytes() != subject:
                raise ProfileError("retained release profile subject changed during verification")
        index = json.loads(subject)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError("cannot read release profile index") from exc
    if (
        not isinstance(index, dict) or index.get("schema") != SCHEMA
        or type(index.get("schema_version")) is not int or index["schema_version"] != 2
        or not isinstance(index.get("artifacts"), list)
        or "main_before" in index or index.get("dev_head") != dev_head
        or index.get("content_tree") != content_tree
        or index.get("admission") != {
            "signer_digest": signer_digest, "source_digest": signer_digest,
            "source_ref": "refs/heads/main",
        }
    ):
        raise ProfileError("authenticated profile index differs from the selected release")
    profiles = freeze_release_profiles(index.get("runtime_profiles", []))
    if any(record["approval_source"] != approval_source for record in profiles):
        raise ProfileError("release profile approval source differs from authority selection")
    approvals = {
        validate_profile(record).lock_id: approval_source for record in profiles
    }
    return select_profile(
        profiles, approvals=approvals, release=release, target=target,
        component=component, roots=roots, mode=mode, backend=backend,
        retained_backend=retained_backend, existing_installation=existing_installation,
        preference=preference,
    )
