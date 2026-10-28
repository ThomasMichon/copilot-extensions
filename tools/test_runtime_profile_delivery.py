"""Complete candidate delivery preserves separately selected materials."""
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
import runtime_profile_delivery as delivery
from installer_wheel_inputs import WheelError
from runtime_profile import validate_profile
from test_runtime_profile_bundle import build, inputs  # noqa: F401


def digest(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def selected(tmp_path, inputs):  # noqa: F811
    record, paths = inputs
    transport = tmp_path / "wheels.zip"
    built = build(record, paths, transport)
    material = tmp_path / "source.tar.gz"
    material.write_bytes(b"independently qualified source archive")
    materials = {material.name: {
        "kind": "source", "wheels": [record["wheels"][0]["sha256"]],
        "sha256": digest(material), "size": material.stat().st_size,
    }}
    return record, transport, built["sha256"], materials, {material.name: material}


def options(**changes):
    return {
        "max_bytes": 1_000_000, "max_files": 10, "max_snapshot_bytes": 10_000_000,
        "deadline": time.monotonic() + 30,
    } | changes


def produce(selected, output, **changes):
    return delivery.build_profile_delivery(*selected, output, **options(**changes))


def retain(selected, path, sha256, **changes):
    return delivery.retain_profile_delivery(
        selected[0], path, delivery_sha256=sha256, **options(**changes),
    )


def test_deterministic_complete_readback_without_originals(tmp_path, selected):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    result = produce(selected, first)
    assert produce(selected, second) == result
    assert first.read_bytes() == second.read_bytes()
    assert result["profile_sha256"] == validate_profile(selected[0]).lock_id
    selected[1].unlink()
    for path in selected[4].values():
        path.unlink()
    with retain(selected, first, result["sha256"]) as retained:
        assert len(retained.wheels.archives) == 2
        assert retained.materials["source.tar.gz"].read() == b"independently qualified source archive"
        paths = list(retained.wheels.paths.values())
    assert not any(path.exists() for path in paths)


@pytest.mark.parametrize("change", ["bytes", "missing", "extra", "unrelated", "kind", "path", "size"])
def test_selected_material_faults_never_publish(tmp_path, selected, change):
    record, transport, sha, materials, paths = selected
    if change == "bytes":
        paths["source.tar.gz"].write_bytes(b"changed")
    elif change == "missing":
        paths.clear()
    elif change == "extra":
        paths["unexpected"] = transport
    elif change == "unrelated":
        materials["source.tar.gz"]["wheels"] = ["sha256:" + "f" * 64]
    elif change == "kind":
        materials["source.tar.gz"]["kind"] = "approval"
    elif change == "path":
        materials["../source"] = materials.pop("source.tar.gz")
    else:
        materials["source.tar.gz"]["size"] += 1
    output = tmp_path / "result.zip"
    before = set(tmp_path.iterdir())
    with pytest.raises((ValueError, OSError)):
        produce((record, transport, sha, materials, paths), output)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("changes", [
    {"max_bytes": 1}, {"max_files": 1}, {"max_snapshot_bytes": 1}, {"deadline": 0},
    {"max_bytes": True}, {"max_files": 0},
])
def test_delivery_budgets_are_enforced(tmp_path, selected, changes):
    output = tmp_path / "result.zip"
    with pytest.raises(ValueError):
        produce(selected, output, **changes)
    assert not output.exists()


def test_existing_destination_survives(tmp_path, selected):
    output = tmp_path / "result.zip"
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        produce(selected, output)
    assert output.read_bytes() == b"existing"


def test_wrong_profile_and_unselected_outer_bytes_refuse(tmp_path, selected):
    output = tmp_path / "result.zip"
    result = produce(selected, output)
    other = deepcopy(selected[0])
    other["acquisition_policy"] = "another-policy"
    with pytest.raises(ValueError), retain((other, *selected[1:]), output, result["sha256"]):
        pytest.fail("changed selected profile admitted")
    with pytest.raises(ValueError), retain(selected, output, "sha256:" + "f" * 64):
        pytest.fail("unselected delivery admitted")


@pytest.mark.parametrize("change", ["extra", "missing", "material", "profile", "transport", "duplicate"])
def test_corrupt_delivery_refuses_even_with_matching_outer_hash(tmp_path, selected, change):
    output = tmp_path / "original.zip"
    produce(selected, output)
    with zipfile.ZipFile(output) as archive:
        contents = {item.filename: archive.read(item) for item in archive.infolist()}
    if change == "extra":
        contents["extra"] = b"unexpected"
    elif change == "missing":
        del contents["materials/source.tar.gz"]
    elif change == "material":
        contents["materials/source.tar.gz"] = b"changed"
    elif change == "profile":
        contents["profile.json"] = b"{}"
    elif change == "transport":
        contents["wheels.zip"] = b"not a wheel transport"
        manifest = json.loads(contents["delivery.json"])
        manifest["files"]["wheels.zip"] = {
            "sha256": "sha256:" + hashlib.sha256(contents["wheels.zip"]).hexdigest(),
            "size": len(contents["wheels.zip"]),
        }
        contents["delivery.json"] = json.dumps(manifest).encode()
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(corrupt, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
        if change == "duplicate":
            with pytest.warns(UserWarning):
                archive.writestr("profile.json", contents["profile.json"])
    with pytest.raises((ValueError, OSError)), retain(selected, corrupt, digest(corrupt)):
        pytest.fail("corrupt delivery admitted")


def test_material_change_at_context_exit_is_detected(tmp_path, selected):
    output = tmp_path / "result.zip"
    result = produce(selected, output)
    with pytest.raises((WheelError, OSError)), retain(selected, output, result["sha256"]) as retained:
        path = retained.material_paths["source.tar.gz"]
        path.chmod(0o600)
        path.write_bytes(b"changed retained material")


def test_actual_command_matches_api_output(tmp_path, selected):
    profile, _, sha, materials, _ = selected
    profile_path, materials_path = tmp_path / "profile.json", tmp_path / "materials.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    materials_path.write_text(json.dumps(materials), encoding="utf-8")
    expected = produce(selected, tmp_path / "api.zip")
    result = subprocess.run(
        [
            sys.executable, str(Path(delivery.__file__)), "--profile", str(profile_path),
            "--transport", str(selected[1]), "--transport-sha256", sha,
            "--materials", str(materials_path), "--material-dir", str(tmp_path),
            "--output", str(tmp_path / "command.zip"),
        ],
        capture_output=True, text=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("stage", ["write", "readback", "exit"])
def test_failed_production_never_leaves_output(tmp_path, selected, monkeypatch, stage):
    output = tmp_path / "result.zip"
    before = set(tmp_path.iterdir())
    if stage == "write":
        def fail(*args, **kwargs):
            raise OSError("injected write failure")
        monkeypatch.setattr(delivery, "_write_archive", fail)
    elif stage == "readback":
        def fail(*args, **kwargs):
            raise OSError("injected readback failure")
        monkeypatch.setattr(delivery, "retain_profile_delivery", fail)
    else:
        original = delivery._held
        from contextlib import contextmanager
        calls = 0

        @contextmanager
        def fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            this_call = calls
            with original(*args, **kwargs) as streams:
                yield streams
            if this_call == 2:
                raise OSError("injected exit verification failure")
        monkeypatch.setattr(delivery, "_held", fail)
    with pytest.raises(OSError, match="injected"):
        produce(selected, output)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("name", ["../outside", "CON.txt", "a\\b", "a/b", "trailing.", "stream:ads"])
def test_material_names_are_portable_not_extraction_paths(tmp_path, selected, name):
    materials = {name: next(iter(selected[3].values()))}
    paths = {name: next(iter(selected[4].values()))}
    with pytest.raises(ValueError):
        produce((*selected[:3], materials, paths), tmp_path / "result.zip")


def test_late_deadline_failure_does_not_publish_verified_candidate(tmp_path, selected, monkeypatch):
    original = delivery.retain_profile_delivery
    from contextlib import contextmanager

    @contextmanager
    def expire(*args, **kwargs):
        with original(*args, **kwargs) as snapshot:
            yield snapshot
        monkeypatch.setattr(delivery.time, "monotonic", lambda: float("inf"))

    monkeypatch.setattr(delivery, "retain_profile_delivery", expire)
    with pytest.raises(ValueError, match="deadline"):
        produce(selected, tmp_path / "result.zip")
    assert not (tmp_path / "result.zip").exists()


def test_output_created_during_build_is_not_replaced(tmp_path, selected, monkeypatch):
    output = tmp_path / "result.zip"
    original = delivery._write_archive

    def contender(*args, **kwargs):
        output.write_bytes(b"other producer")
        return original(*args, **kwargs)

    monkeypatch.setattr(delivery, "_write_archive", contender)
    with pytest.raises(FileExistsError):
        produce(selected, output)
    assert output.read_bytes() == b"other producer"


def test_no_material_selection_is_explicit_not_a_clearance_claim(tmp_path, selected):
    output = tmp_path / "result.zip"
    selected = (*selected[:3], {}, {})
    result = produce(selected, output)
    with retain(selected, output, result["sha256"]) as retained:
        assert not retained.materials and len(retained.wheels.archives) == 2


@pytest.mark.parametrize("changes", [
    {"max_bytes": 4096}, {"max_snapshot_bytes": 4096},
])
def test_compressed_manifest_is_bounded_before_decoding(tmp_path, selected, monkeypatch, changes):
    output = tmp_path / "oversized-manifest.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("delivery.json", b"{}" + b" " * 8192)
    assert output.stat().st_size < 4096
    loads = delivery.json.loads

    def bounded(data, *args, **kwargs):
        assert len(data) <= 4096, "oversized delivery manifest reached the JSON decoder"
        return loads(data, *args, **kwargs)

    monkeypatch.setattr(delivery.json, "loads", bounded)
    with pytest.raises(WheelError, match="(manifest|total|snapshot).*budget"), retain(
        selected, output, digest(output), **changes,
    ):
        pytest.fail("over-budget manifest admitted")
