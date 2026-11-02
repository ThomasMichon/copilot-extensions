"""Core candidates preserve native selections and inspect complete wheel bytes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import pytest
from promotion_python_artifacts import PromotionArtifactError
from runtime_profile import ProfileError, validate_profile
from runtime_profile_cases import _record
from runtime_wheel_inspection import inspect_wheel
from test_core_python_artifacts import _build
from test_promotion_python_artifacts import _wheel_bytes

# isort: split

from installer_wheel_inputs import WheelError, WheelLimits


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    directory, manifest, _ = _build(tmp_path, monkeypatch)
    record = _record()
    record["roots"] = ["agent-sample==1.0", "agent-pull-requests-service==1.0"]
    record["wheels"] = []
    paths = {}
    for wheel in manifest["wheels"]:
        path = directory / wheel["filename"]
        paths[path.name] = path
        with path.open("rb") as stream:
            metadata, _ = inspect_wheel(stream, wheel)
        record["wheels"].append(
            {
                "name": wheel["name"],
                "version": metadata["Version"],
                "filename": path.name,
                "sha256": wheel["sha256"],
                "requires_python": metadata.get("Requires-Python", ""),
                "provides_extras": metadata.get_all("Provides-Extra", []),
                "requires_dist": metadata.get_all("Requires-Dist", []),
                "selected_extras": [],
            }
        )
    validate_profile(record)
    return manifest, record, paths


def _produce(inputs, **overrides):
    from core_runtime_profile import build_core_profile

    manifest, native, paths = inputs
    arguments = {
        "manifest_data": json.dumps(manifest).encode(),
        "native_record": native,
        "archives": paths,
        "limits": WheelLimits(
            1 << 20,
            8 << 20,
            1 << 20,
            1 << 20,
            1000,
            1000,
            1 << 20,
        ),
        "max_wheels": 20,
        "max_total_bytes": 4 << 20,
        "max_snapshot_bytes": 8 << 20,
        "max_unpacked_bytes": 8 << 20,
        "deadline": time.monotonic() + 60,
    }
    return build_core_profile(**(arguments | overrides))


def test_candidate_binds_raw_manifest_and_preserves_native_policy(inputs):
    manifest, native, _ = inputs
    before = deepcopy(native)
    profile = _produce(inputs)
    record = profile.record()
    assert native == before
    assert record["component"] == "composition-core"
    assert record["release"] == {
        "identity": "composition-core:" + manifest["version"],
        "sha256": manifest["payload_hash"],
    }
    assert record["artifact"] == {
        "artifact_id": manifest["artifact_id"],
        "manifest_sha256": "sha256:"
        + hashlib.sha256(json.dumps(manifest).encode()).hexdigest(),
    }
    assert record["mode"] == "artifact"
    assert record["approval_source"] == "promotion-main"
    for key in ("backend", "target", "acquisition_policy"):
        assert record[key] == native[key]
    assert {wheel["filename"] for wheel in record["wheels"]} == set(inputs[2])
    assert _produce(inputs).canonical == profile.canonical


def test_whitespace_changes_exact_manifest_binding(inputs):
    manifest, _, _ = inputs
    compact = _produce(inputs)
    pretty = _produce(inputs, manifest_data=json.dumps(manifest, indent=2).encode())
    assert pretty.record()["artifact"] != compact.record()["artifact"]
    assert pretty.record()["wheels"] == compact.record()["wheels"]


@pytest.mark.parametrize("change", ["missing", "extra", "bytes", "native-version"])
def test_changed_or_unselected_inputs_refuse(inputs, tmp_path, change):
    manifest, native, paths = inputs
    if change == "missing":
        paths.pop(next(iter(paths)))
    elif change == "extra":
        paths["unselected-1.0-py3-none-any.whl"] = next(iter(paths.values()))
    elif change == "bytes":
        next(iter(paths.values())).write_bytes(b"not the selected wheel")
    else:
        native["wheels"][0]["version"] = "9"
    with pytest.raises((ProfileError, WheelError, ValueError)):
        _produce((manifest, native, paths))


def test_core_metadata_is_derived_from_release_bytes_not_native_assertions(inputs):
    baseline = _produce(inputs)
    inputs[1]["wheels"][0]["requires_python"] = ">=3.10"
    assert _produce(inputs).canonical == baseline.canonical


@pytest.mark.parametrize("change", ["schema", "schema-version", "non-object"])
def test_only_the_registered_core_manifest_schema_is_accepted(inputs, change):
    manifest, native, paths = inputs
    if change == "schema":
        manifest["schema"] = "other"
    elif change == "schema-version":
        manifest["schema_version"] = True
    else:
        manifest = []
    with pytest.raises(ProfileError, match="core composition manifest"):
        _produce((manifest, native, paths))


@pytest.mark.parametrize(
    "change", ["identity", "noncore", "unknown-root", "unreachable", "root-type"]
)
def test_manifest_or_composition_drift_refuses(inputs, change):
    manifest, native, paths = inputs
    if change == "identity":
        manifest["artifact_id"] = "sha256:" + "0" * 64
    elif change == "noncore":
        manifest["composition"] = "other"
    elif change == "unknown-root":
        manifest["selection"]["roots"][0]["name"] = "unknown"
    elif change == "root-type":
        manifest["selection"]["roots"][0]["name"] = []
    else:
        manifest["selection"]["roots"] = [
            {"name": "agent-pull-requests-service", "extra": "", "condition": ""},
        ]
    with pytest.raises((ProfileError, ValueError, PromotionArtifactError)):
        _produce((manifest, native, paths))


def test_expired_deadline_and_explicit_limits_refuse(inputs):
    with pytest.raises((ProfileError, WheelError, ValueError)):
        _produce(inputs, deadline=time.monotonic() - 1)
    with pytest.raises((ProfileError, WheelError, ValueError)):
        _produce(inputs, max_total_bytes=0)


@pytest.fixture
def external_inputs(tmp_path, monkeypatch, request):
    import test_core_python_artifacts as core_tests

    original = core_tests._wheel_bytes

    def encoded(name, version, **options):
        if name == "agent_sample":
            members = options["members"]
            key = name + "-" + version + ".dist-info/METADATA"
            members[key] += (
                b'Provides-Extra: metrics\nRequires-Dist: counter>=1; extra == "metrics"\n'
            )
            if getattr(request, "param", None) == "new-dependency":
                members[key] = members[key].replace(b"counter>=1", b"counter>=2")
        return original(name, version, **options)

    monkeypatch.setattr(core_tests, "_wheel_bytes", encoded)
    directory, manifest, _ = _build(tmp_path, monkeypatch)
    path = directory / "counter-1.0-py3-none-any.whl"
    path.write_bytes(_wheel_bytes("counter", "1.0"))
    native = _record()
    native["roots"] = ["agent-sample[metrics]==1.0", "agent-pull-requests-service==1.0"]
    native["wheels"] = []
    paths = {}
    for path in directory.glob("*.whl"):
        with path.open("rb") as stream:
            metadata, _ = inspect_wheel(stream, {"filename": path.name})
        name = metadata["Name"].replace("_", "-")
        native["wheels"].append(
            {
                "name": name,
                "version": metadata["Version"],
                "filename": path.name,
                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "requires_python": metadata.get("Requires-Python", ""),
                "provides_extras": metadata.get_all("Provides-Extra", []),
                "requires_dist": [
                    text.replace("counter>=2", "counter>=1")
                    for text in metadata.get_all("Requires-Dist", [])
                ],
                "selected_extras": ["metrics"] if name == "agent-sample" else [],
            }
        )
        paths[path.name] = path
    for root in manifest["selection"]["roots"]:
        if root["name"] == "agent-sample":
            root["extra"] = "metrics"
    validate_profile(native)
    return manifest, native, paths


@pytest.mark.parametrize("external_inputs", ["new-dependency"], indirect=True)
def test_new_release_cannot_silently_change_external_pins(external_inputs):
    with pytest.raises(ProfileError, match="locked version violates dependency"):
        _produce(external_inputs)


def test_external_pins_extras_and_target_conditions_are_preserved(external_inputs):
    manifest, native, _ = external_inputs
    for root in manifest["selection"]["roots"]:
        if root["name"] == "agent-sample":
            root["condition"] = 'sys_platform == "win32"'
    record = _produce(external_inputs).record()
    external = next(wheel for wheel in native["wheels"] if wheel["name"] == "counter")
    assert external in record["wheels"]
    assert 'agent-sample[metrics]==1.0; sys_platform == "win32"' in record["roots"]
    for root in manifest["selection"]["roots"]:
        if root["name"] == "agent-sample":
            root["condition"] = 'sys_platform == "linux"'
    with pytest.raises(ProfileError, match="unreachable"):
        _produce(external_inputs)


def test_changed_external_metadata_and_bytes_refuse(external_inputs):
    _, native, paths = external_inputs
    external = next(wheel for wheel in native["wheels"] if wheel["name"] == "counter")
    external["requires_python"] = ">=3.10"
    with pytest.raises(ProfileError, match="metadata differs"):
        _produce(external_inputs)
    external["requires_python"] = ""
    paths[external["filename"]].write_bytes(b"changed")
    with pytest.raises((ProfileError, WheelError)):
        _produce(external_inputs)


def test_cli_emits_identical_candidate_and_refuses_overwrite(external_inputs, tmp_path):
    manifest, native, paths = external_inputs
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(json.dumps(manifest).encode())
    native_path = tmp_path / "native.json"
    native_path.write_text(json.dumps(native))
    output = tmp_path / "candidate.json"
    command = [
        sys.executable,
        str(Path(__file__).with_name("core_runtime_profile.py")),
        "--manifest",
        str(manifest_path),
        "--native-profile",
        str(native_path),
        "--wheel-dir",
        str(next(iter(paths.values())).parent),
        "--output",
        str(output),
    ]
    options = {
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    }
    options["env"] = {
        key: value for key, value in os.environ.items() if key != "PYTHONPATH"
    }
    result = subprocess.run(command, check=False, **options)
    assert result.returncode == 0, result.stderr
    expected = _produce(external_inputs)
    assert output.read_bytes() == expected.canonical
    assert json.loads(result.stdout) == {"profile_sha256": expected.lock_id}
    refused = subprocess.run(command, check=False, **options)
    assert refused.returncode != 0 and "already exists" in refused.stderr
    assert output.read_bytes() == expected.canonical
