"""Prepare one admitted Git application and existing wheels; never build or install."""
from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import sys
from typing import Any
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

import local_install_source as source
import local_install_profile as profiles
from application_sources import application_sources
from artifact_bundle_validation import validate_wheel_sources
from build_toolchain_lock import _query_marker_environment
from installer_wheel_inputs import _check_deadline, inspect_archive
from installer_wheel_snapshot import retain_wheel_inputs
from runtime_profile import ProfileError
from runtime_profile_wheels import profile_wheel_inputs

CONTRACT = {
    "schema": "copilot-extensions.closed-wheel-source-preparation",
    "version": 1,
}


def prepare(
    repository: Path, commit: str, component: str, output: Path, *,
    archives: Mapping[str, Path], digests: Mapping[str, str],
    target: dict[str, Any], roots: list[str], artifact_provenance: dict[str, Any],
    policy_id: str, acquisition_id: str, uv: str, deadline: float,
) -> Path:
    """Compose the canonical Source, wheel profile, application and actual UV lock.

    The caller independently admits the Git source, wheel digests, target, policy
    and tooling. Consistency checks here do not confer source/feed approval.
    Source code and backends are data only. Occupied or partial output is retained.
    """
    _check_deadline(deadline)
    if (
        not isinstance(component, str) or component.startswith("standalone:")
        or not isinstance(artifact_provenance, dict) or not artifact_provenance
        or not output.is_absolute()
        or not isinstance(uv, str) or not Path(uv).is_absolute() or not Path(uv).is_file()
        or set(archives) != set(digests)
    ):
        raise ValueError("closed-wheel preparation requires explicit source, wheels, provenance and tooling")
    output = Path(source.native_retained_path(output))
    provenance = json.loads(json.dumps(artifact_provenance, allow_nan=False))
    if len(json.dumps(provenance).encode("utf-8")) > 4 << 20:
        raise ValueError("source artifact provenance exceeds its document bound")
    environment = _query_marker_environment(Path(sys.executable), deadline=deadline)
    if target.get("environment") != environment:
        raise ProfileError("closed-wheel preparation target differs from its selected native interpreter")
    # Retain exact admitted bytes before any source output or child operation.
    with retain_wheel_inputs(
        dict(archives), dict(digests), max_wheels=256, max_archive_bytes=128 << 20,
        max_total_bytes=512 << 20, max_snapshot_bytes=1 << 30, deadline=deadline,
    ) as retained:
        record = source.compose(
            repository, commit, None, output, projects=(component,),
            closed_wheel_source=True, deadline=deadline,
        )
        _roster, _selected, payloads, identities = source._source_projects(
            output / "source", record, (component,),
        )
        payload, identity = payloads[component], identities[component]
        metadata = profiles.dependencies._toml(payload / "pyproject.toml")
        declared_sources = application_sources(metadata)
        name = canonicalize_name(metadata["project"]["name"])
        associated: dict[str, Any] | None = None
        for filename, archive in retained.archives.items():
            _check_deadline(deadline)
            if canonicalize_name(parse_wheel_filename(filename)[0]) != name:
                continue
            facts = inspect_archive(archive, limits=profiles.wheel_limits(), deadline=deadline)
            if canonicalize_name(facts.name) != name:
                continue
            if associated is not None or Version(facts.version) != Version(identity.package_version):
                raise ProfileError("source requires one matching first-party wheel")
            with zipfile.ZipFile(archive) as bundle:
                validate_wheel_sources(
                    bundle, dict(facts.member_hashes), source_dir=payload,
                    prefix=facts.prefix, metadata=facts.metadata,
                    candidate_root=output / "source",
                )
            _check_deadline(deadline)
            associated = {
                "name": name, "version": facts.version, "filename": filename,
                "sha256": retained.digests[filename],
            }
        if associated is None:
            raise ProfileError("closed-wheel inputs omit their source-associated application")
        record["projects"] = {component: {
            "payload": payload.relative_to(output).as_posix(),
            "source": identity.document(), "input_mode": "closed-wheels",
            "source_artifact": {
                "schema": "copilot-extensions.source-associated-wheel", "version": 1,
                "source": identity.document(), "wheel": associated,
                "provenance": provenance,
            },
        }}
        (output / source.MANIFEST).write_text(
            json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
        selected = profiles.source_inputs.select_project(output, component)
        profile = profiles.complete_profile(
            selected, target, roots, retained, policy_id=policy_id,
            acquisition_id=acquisition_id, deadline=deadline,
        )
        if {row["filename"] for row in profile.record()["wheels"]} != set(retained.archives):
            raise ProfileError("closed-wheel inputs contain unselected distributions")
        if declared_sources:
            wheels = {row["name"]: row for row in profile.record()["wheels"]}
            wheels[name] = {**wheels[name], "application_sources": declared_sources}
            for root in roots:
                profiles.registry_roots(
                    wheels, root, target["environment"], deadline=deadline,
                )
        inputs = profile_wheel_inputs(
            profile, retained, max_archive_bytes=512 << 20, max_unpacked_bytes=1 << 30,
            max_metadata_bytes=4 << 20, limits=profiles.wheel_limits(), deadline=deadline,
        )
        inputs.verify()
        prepared = profiles._write_snapshot(
            selected, output, profile, retained, deadline=deadline, uv=uv,
            source_in_place=True,
        )
        inputs.verify()
        # All original application bytes, including development metadata, remain
        # bound to the same Source identity after emission and the owned UV lock.
        if profiles.source_inputs.select_project(prepared, component).identity != identity:
            raise ProfileError("original application source changed during preparation")
        return prepared
