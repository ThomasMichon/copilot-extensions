"""Core candidate decisions with actual wheel bytes and substituted custody."""

from __future__ import annotations

import hashlib
import io
import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import core_runtime_profile as producer
import pytest
from build_toolchain_lock import ToolchainLock, _hash_fields
from promotion_unit_tests.test_runtime_profile_wheels import _encoded, _record
from runtime_profile import ProfileError


@pytest.fixture
def inputs(monkeypatch):
    native = _record()
    monkeypatch.setattr(
        "packaging.markers.default_environment",
        lambda: dict(native["target"]["environment"]),
    )
    lock = ToolchainLock(
        Path("/unused"),
        {"setuptools": "80.0", "wheel": "0.45.0", "packaging": "25.0"},
        provenance={
            "source_policy": "governed",
            "python": "fixture-python",
            "validated_index_identity": "fixture",
        },
    )
    archives = {}
    for wheel in native["wheels"]:
        data = _encoded(
            wheel,
            wheel_header=(
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
                f"Generator: {lock.generator}\n"
            ),
        )
        wheel["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
        archives[wheel["filename"]] = data
    first = native["wheels"][0]
    manifest = {
        "schema": "copilot-extensions.python-artifact-manifest",
        "schema_version": 5,
        "composition": "core",
        "version": "2.0",
        "payload_hash": "sha256:" + "2" * 64,
        "python_tag": "py3",
        "abi_tag": "none",
        "platform_tag": "any",
        "build_toolchain": lock.record,
        "build_resources": [],
        "selection": {
            "roots": [{"name": "engine", "extra": "metrics", "condition": ""}],
            "projects": [
                {"name": "engine", "source": "plugins/engine", "role": "plugin"}
            ],
            "edges": [],
        },
        "wheels": [
            {
                "name": "engine",
                "source": "plugins/engine",
                "role": "plugin",
                "filename": first["filename"],
                "sha256": first["sha256"],
                "generator": lock.generator,
                "python_tag": "py3",
                "abi_tag": "none",
                "platform_tag": "any",
            }
        ],
    }
    manifest["artifact_id"] = "sha256:" + _hash_fields(
        manifest["payload_hash"],
        "py3",
        "none",
        "any",
        lock.lock_id,
        first["filename"],
        first["sha256"],
    )
    custody = []

    @contextmanager
    def retain(paths, expected, **options):
        assert paths == archives
        assert expected == {
            wheel["filename"]: wheel["sha256"] for wheel in native["wheels"]
        }
        assert options["max_wheels"] == 10 and options["max_total_bytes"] == 1 << 20
        yield SimpleNamespace(
            archives={name: io.BytesIO(data) for name, data in archives.items()},
            verify=lambda: custody.append("verified"),
        )

    monkeypatch.setattr(producer, "retain_wheel_inputs", retain)
    return manifest, native, archives, custody


def build(inputs, **overrides):
    manifest, native, archives, _ = inputs
    return producer.build_core_profile(
        **(
            {
                "manifest_data": json.dumps(manifest).encode(),
                "native_record": native,
                "archives": archives,
                "limits": producer.WheelLimits(
                    1 << 20, 1 << 20, 1 << 20, 1 << 20, 1000, 1000, 1 << 20
                ),
                "max_wheels": 10,
                "max_total_bytes": 1 << 20,
                "max_snapshot_bytes": 2 << 20,
                "max_unpacked_bytes": 1 << 20,
                "deadline": time.monotonic() + 10,
            }
            | overrides
        )
    )


def test_candidate_inspects_exact_external_bytes_and_binds_manifest(inputs):
    manifest, native, _, custody = inputs
    profile = build(inputs)
    record = profile.record()
    assert record["artifact"]["artifact_id"] == manifest["artifact_id"]
    assert (
        record["artifact"]["manifest_sha256"]
        == "sha256:" + hashlib.sha256(json.dumps(manifest).encode()).hexdigest()
    )
    assert (
        record["target"] == native["target"] and record["backend"] == native["backend"]
    )
    assert record["acquisition_policy"] == native["acquisition_policy"]
    assert record["roots"] == ["engine[metrics]==1.0"]
    assert record["wheels"] == sorted(native["wheels"], key=lambda wheel: wheel["name"])
    assert custody == ["verified"]


@pytest.mark.parametrize(
    "change",
    [
        "metadata",
        "digest",
        "root",
        "extra",
        "unreachable",
        "target",
        "schema",
        "schema-version",
    ],
)
def test_candidate_cannot_repair_or_drop_selected_inputs(inputs, change):
    manifest, native, archives, _ = inputs
    external = native["wheels"][1]
    if change == "metadata":
        external["requires_python"] = ">=3.10"
    elif change == "digest":
        archives[external["filename"]] = _encoded(
            external, extra={"extra.txt": b"changed external"}
        )
    elif change == "root":
        manifest["selection"]["roots"][0]["name"] = "other"
    elif change == "extra":
        manifest["selection"]["roots"][0]["extra"] = "unknown"
    elif change == "unreachable":
        manifest["selection"]["roots"][0]["extra"] = ""
    elif change == "target":
        native["target"]["tags"] = ["cp311-cp311-win_amd64"]
    elif change == "schema":
        manifest["schema"] = "other"
    else:
        manifest["schema_version"] = True
    with pytest.raises(ProfileError):
        build(inputs)
    assert not inputs[3]


def test_raw_manifest_and_duplicate_properties_are_not_normalized_away(inputs):
    manifest = inputs[0]
    first = build(inputs)
    second = build(inputs, manifest_data=json.dumps(manifest, indent=2).encode())
    assert first.lock_id != second.lock_id
    raw = json.dumps(manifest).replace(
        '"composition": "core"', '"composition": "core", "composition": "core"'
    )
    with pytest.raises(ProfileError, match="duplicate JSON property"):
        build(inputs, manifest_data=raw.encode())
