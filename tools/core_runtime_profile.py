"""Produce inert core release profiles from independently selected native locks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from promotion_python_artifacts import PromotionArtifactError, validate_manifest
from runtime_profile import ProfileError, RuntimeProfile, _reachable, validate_profile
from runtime_profile_release import (
    APPROVAL_SOURCE,
    _unique_object,
    validate_release_bindings,
)
from runtime_profile_wheels import verify_profile_wheels
from runtime_wheel_inspection import inspect_wheel

# isort: split

from installer_dependency_policy import _private_retained_root, _temporary_inputs
from installer_wheel_inputs import WheelLimits, _check_deadline, _ordinary
from installer_wheel_snapshot import _open, _unchanged, retain_wheel_inputs


def _core_manifest(data: bytes) -> dict:
    if not isinstance(data, bytes) or len(data) > 16 << 20:
        raise ProfileError("core manifest requires bounded exact bytes")
    manifest = json.loads(data, object_pairs_hook=_unique_object)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "copilot-extensions.python-artifact-manifest"
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 5
        or manifest.get("composition") != "core"
    ):
        raise ProfileError(
            "core profile requires a registered core composition manifest"
        )
    validate_manifest(manifest)
    return manifest


def build_core_profile(
    *,
    manifest_data: bytes,
    native_record: dict,
    archives,
    limits: WheelLimits,
    max_wheels: int,
    max_total_bytes: int,
    max_snapshot_bytes: int,
    max_unpacked_bytes: int,
    deadline: float,
) -> RuntimeProfile:
    """Rebind selected native inputs to a core manifest, never resolve or approve.

    The native lock supplies target, backend, acquisition policy and exact external
    inputs. First-party metadata comes from release bytes; its complete applicable
    closure must be satisfied by the unchanged external pins, without resolution.
    """
    _check_deadline(deadline)
    if not isinstance(limits, WheelLimits) or any(
        type(value) is not int or value <= 0
        for value in (
            max_wheels,
            max_total_bytes,
            max_snapshot_bytes,
            max_unpacked_bytes,
        )
    ):
        raise ProfileError("core profile limits must be explicit positive integers")
    manifest = _core_manifest(manifest_data)
    native = validate_profile(
        native_record, max_tags=limits.max_tags, deadline=deadline
    ).record()
    selected = {canonicalize_name(wheel["name"]): wheel for wheel in native["wheels"]}
    first_party = {
        canonicalize_name(wheel["name"]): wheel for wheel in manifest["wheels"]
    }
    if (
        len(first_party) != len(manifest["wheels"])
        or not first_party.keys() <= selected.keys()
    ):
        raise ProfileError("native lock does not cover the exact core distribution set")
    entries = [
        *manifest["wheels"],
        *(wheel for name, wheel in selected.items() if name not in first_party),
    ]
    expected = {wheel["filename"]: wheel["sha256"] for wheel in entries}
    if len(expected) != len(entries):
        raise ProfileError("core and native wheel filenames collide")
    with retain_wheel_inputs(
        archives,
        expected,
        max_wheels=max_wheels,
        max_archive_bytes=limits.max_archive_bytes,
        max_total_bytes=max_total_bytes,
        max_snapshot_bytes=max_snapshot_bytes,
        deadline=deadline,
    ) as snapshot:
        wheels = {}
        remaining = max_unpacked_bytes
        for entry in entries:
            _check_deadline(deadline)
            name = canonicalize_name(entry["name"])
            metadata, size = inspect_wheel(
                snapshot.archives[entry["filename"]],
                entry,
                expected_generator=entry.get("generator"),
                max_unpacked_bytes=remaining,
                max_archive_bytes=limits.max_archive_bytes,
                max_metadata_bytes=limits.max_metadata_bytes,
                limits=limits,
                deadline=deadline,
            )
            remaining -= size
            wheel = {
                "name": name,
                "version": metadata["Version"],
                "filename": entry["filename"],
                "sha256": entry["sha256"],
                "requires_python": metadata.get("Requires-Python", ""),
                "provides_extras": metadata.get_all("Provides-Extra", []),
                "requires_dist": metadata.get_all("Requires-Dist", []),
                "selected_extras": [],
            }
            if name not in first_party and any(
                wheel[key] != selected[name][key]
                for key in (
                    "version",
                    "requires_python",
                    "provides_extras",
                    "requires_dist",
                )
            ):
                raise ProfileError(
                    f"{name}: native lock runtime metadata differs from core inputs"
                )
            wheels[name] = wheel, [Requirement(text) for text in wheel["requires_dist"]]
        roots = []
        for root in manifest["selection"]["roots"]:
            if (
                not isinstance(root, dict)
                or set(root) != {"name", "extra", "condition"}
                or any(not isinstance(value, str) for value in root.values())
            ):
                raise ProfileError("invalid core root selection")
            name = canonicalize_name(root["name"])
            if name not in first_party:
                raise ProfileError("core root is outside the first-party selection")
            extra = "[" + root["extra"] + "]" if root["extra"] else ""
            condition = "; " + root["condition"] if root["condition"] else ""
            roots.append(
                str(
                    Requirement(
                        f"{name}{extra}=={wheels[name][0]['version']}{condition}"
                    )
                )
            )
        roots = sorted(set(roots))
        wanted = _reachable(wheels, roots, native["target"]["environment"], deadline)
        for name, extras in wanted.items():
            wheels[name][0]["selected_extras"] = sorted(extras)
        record = native | {
            "release": {
                "identity": "composition-core:" + manifest["version"],
                "sha256": manifest["payload_hash"],
            },
            "mode": "artifact",
            "component": "composition-core",
            "artifact": {
                "artifact_id": manifest["artifact_id"],
                "manifest_sha256": "sha256:"
                + hashlib.sha256(manifest_data).hexdigest(),
            },
            "roots": roots,
            "approval_source": APPROVAL_SOURCE,
            "wheels": [wheels[name][0] for name in sorted(wheels)],
        }
        profile = validate_profile(record, max_tags=limits.max_tags, deadline=deadline)
        verify_profile_wheels(
            profile,
            snapshot.archives,
            max_archive_bytes=max_total_bytes,
            max_unpacked_bytes=max_unpacked_bytes,
            max_metadata_bytes=limits.max_metadata_bytes,
            limits=limits,
            deadline=deadline,
        )
        from runtime_profile_release import SCHEMA

        validate_release_bindings(
            {
                "artifacts": [{"artifact_id": manifest["artifact_id"]}],
                "runtime_profiles": {
                    "schema": SCHEMA,
                    "version": 1,
                    "profiles": [record],
                },
            },
            {
                manifest["artifact_id"]: (
                    manifest,
                    record["artifact"]["manifest_sha256"],
                )
            },
        )
        snapshot.verify()
    _check_deadline(deadline)
    return profile


def _read(path: Path) -> bytes:
    before = _ordinary(path)
    with _open(path) as source:
        opened = _unchanged(path, before, source)
        data = source.read((16 << 20) + 1)
        _unchanged(path, before, source, opened=opened)
    if len(data) > 16 << 20:
        raise ProfileError("profile input document byte budget exceeded")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--native-profile", type=Path, required=True)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-bytes", type=int, default=512 << 20)
    parser.add_argument("--max-wheels", type=int, default=200)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.timeout
    try:
        _check_deadline(deadline)
        destination = Path(os.path.abspath(args.output))
        if os.path.lexists(destination):
            raise FileExistsError("core profile output already exists")
        manifest_data = _read(args.manifest)
        manifest = _core_manifest(manifest_data)
        native = validate_profile(
            json.loads(_read(args.native_profile), object_pairs_hook=_unique_object),
            deadline=deadline,
        ).record()
        names = {canonicalize_name(wheel["name"]) for wheel in manifest["wheels"]}
        entries = [
            *manifest["wheels"],
            *(
                wheel
                for wheel in native["wheels"]
                if canonicalize_name(wheel["name"]) not in names
            ),
        ]
        profile = build_core_profile(
            manifest_data=manifest_data,
            native_record=native,
            archives={
                wheel["filename"]: args.wheel_dir / wheel["filename"]
                for wheel in entries
            },
            limits=WheelLimits(
                args.max_bytes,
                8 * args.max_bytes,
                args.max_bytes,
                16 << 20,
                100_000,
                10_000,
                4 << 20,
            ),
            max_wheels=args.max_wheels,
            max_total_bytes=args.max_bytes,
            max_snapshot_bytes=2 * args.max_bytes,
            max_unpacked_bytes=8 * args.max_bytes,
            deadline=deadline,
        )
        with _temporary_inputs(destination.parent) as temporary:
            candidate = (
                _private_retained_root(Path(temporary)).resolve() / "profile.json"
            )
            candidate.write_bytes(profile.canonical)
            _check_deadline(deadline)
            os.link(candidate, destination)
        print(json.dumps({"profile_sha256": profile.lock_id}, sort_keys=True))
        return 0
    except (OSError, ValueError, UnicodeError, PromotionArtifactError) as error:
        parser.exit(1, f"core profile refused: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
