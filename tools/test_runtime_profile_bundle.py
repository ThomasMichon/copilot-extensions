"""The production transport producer composes the existing retained-byte owners."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

from packaging.markers import default_environment
import pytest

import runtime_profile_bundle as producer
from installer_wheel_inputs import WheelError, WheelLimits, _check_deadline
from installer_wheel_snapshot import retain_wheel_bundle
from runtime_profile import ProfileError, SCHEMA, validate_profile
from runtime_profile_wheels import verify_profile_wheels
from wheel_input_fixture import wheel_bytes


@pytest.fixture
def inputs(tmp_path):
    paths = {}
    wheels = []
    for name in ("demo_pkg", "helper"):
        dependencies = ["helper==1.0"] if name == "demo_pkg" else []
        metadata = (
            f"Metadata-Version: 2.4\nName: {name.replace('_', '-')}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {value}\n" for value in dependencies) + "\n"
        ).encode()
        path = tmp_path / f"{name}-1.0-py3-none-any.whl"
        path.write_bytes(wheel_bytes(
            name=name, prefix=f"{name}-1.0.dist-info/", metadata=metadata,
        ))
        paths[path.name] = path
        wheels.append({
            "name": name.replace("_", "-"), "version": "1.0", "filename": path.name,
            "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "requires_python": "", "provides_extras": [], "selected_extras": [],
            "requires_dist": dependencies,
        })
    return {
        "schema": SCHEMA, "version": 1,
        "release": {"identity": "fixture-source", "sha256": "sha256:" + "2" * 64},
        "mode": "source", "artifact": None, "component": "core", "backend": "cpu",
        "approval_source": "fixture-owner", "acquisition_policy": "fixture-policy",
        "target": {"device": "cpu", "environment": default_environment(), "tags": ["py3-none-any"]},
        "roots": ["demo-pkg==1.0"], "wheels": wheels,
    }, paths


def build(record, paths, output, **changes):
    return producer.build_profile_bundle(record, paths, output, **({
        "limits": WheelLimits(100_000, 100_000, 100_000, 10_000, 100, 100, 10_000),
        "max_wheels": 2, "max_total_bytes": 100_000, "max_snapshot_bytes": 200_000,
        "max_unpacked_bytes": 100_000, "max_bundle_bytes": 100_000,
        "deadline": time.monotonic() + 30,
    } | changes))


def test_deterministic_candidate_is_consumed_by_original_snapshot_owner(tmp_path, inputs):
    record, paths = inputs
    first = tmp_path / "first.zip"
    result = build(record, paths, first)
    for path in paths.values():
        os.utime(path, (1_000_000_000, 1_000_000_000))
    second = tmp_path / "second.zip"
    assert build(record, dict(reversed(list(paths.items()))), second) == result
    assert first.read_bytes() == second.read_bytes()
    assert result == {
        "profile_sha256": validate_profile(record).lock_id,
        "sha256": "sha256:" + hashlib.sha256(first.read_bytes()).hexdigest(),
        "size": first.stat().st_size,
    }
    for path in paths.values():
        path.unlink()
    expected = {wheel["filename"]: wheel["sha256"] for wheel in record["wheels"]}
    with retain_wheel_bundle(
        first, expected, bundle_sha256=result["sha256"], max_bundle_bytes=100_000,
        max_wheels=2, max_archive_bytes=100_000, max_total_bytes=100_000,
        max_snapshot_bytes=200_000, deadline=time.monotonic() + 30,
    ) as snapshot:
        verify_profile_wheels(
            validate_profile(record), snapshot.archives, max_archive_bytes=100_000,
            max_unpacked_bytes=100_000, max_metadata_bytes=10_000,
        )
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == sorted("wheels/" + name for name in expected)
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
        assert all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist())


@pytest.mark.parametrize("change", ["missing", "extra", "bytes", "metadata", "closure"])
def test_invalid_candidate_leaves_no_output(tmp_path, inputs, change):
    record, paths = inputs
    if change == "missing":
        paths.pop(next(iter(paths)))
    elif change == "extra":
        paths["extra-1.0-py3-none-any.whl"] = next(iter(paths.values()))
    elif change == "bytes":
        next(iter(paths.values())).write_bytes(b"altered")
    elif change == "metadata":
        record["wheels"][0]["requires_python"] = ">=3.11"
    else:
        record["wheels"].pop()
    output = tmp_path / "result.zip"
    before = set(tmp_path.iterdir())
    with pytest.raises((ProfileError, WheelError, ValueError)):
        build(record, paths, output)
    assert set(tmp_path.iterdir()) == before
    assert not output.exists()


def test_existing_output_is_never_replaced(tmp_path, inputs):
    output = tmp_path / "result.zip"
    output.write_bytes(b"existing artifact")
    with pytest.raises(FileExistsError):
        build(*inputs, output)
    assert output.read_bytes() == b"existing artifact"


@pytest.mark.parametrize("options", [
    {"max_bundle_bytes": 1}, {"max_total_bytes": 1},
    {"max_snapshot_bytes": 1}, {"max_unpacked_bytes": 1},
    {"max_wheels": 1}, {"deadline": 0},
])
def test_existing_limits_still_bound_production(tmp_path, inputs, options):
    output = tmp_path / "result.zip"
    before = set(tmp_path.iterdir())
    with pytest.raises((WheelError, ProfileError, ValueError)):
        build(*inputs, output, **options)
    assert set(tmp_path.iterdir()) == before


def test_retention_and_readback_share_the_aggregate_storage_limit(tmp_path, inputs):
    record, paths = inputs
    result = build(record, paths, tmp_path / "reference.zip")
    total = sum(path.stat().st_size for path in paths.values())
    exact = 3 * total + result["size"]
    assert build(record, paths, tmp_path / "exact.zip", max_snapshot_bytes=exact) == result
    with pytest.raises(WheelError, match="storage"):
        build(record, paths, tmp_path / "exceeded.zip", max_snapshot_bytes=exact - 1)
    assert not (tmp_path / "exceeded.zip").exists()


def test_producer_never_returns_to_mutable_originals(tmp_path, inputs, monkeypatch):
    record, paths = inputs
    write = producer._write_bundle

    def replace_originals(*args, **kwargs):
        for path in paths.values():
            path.write_bytes(b"caller changed originals after retention")
        return write(*args, **kwargs)

    monkeypatch.setattr(producer, "_write_bundle", replace_originals)
    result = build(record, paths, tmp_path / "retained.zip")
    assert result["profile_sha256"] == validate_profile(record).lock_id


def test_failed_writing_leaves_no_partial_artifact(tmp_path, inputs, monkeypatch):
    before = set(tmp_path.iterdir())

    def fail(_snapshot, path, **_kwargs):
        path.write_bytes(b"partial candidate")
        raise OSError("injected production failure")

    monkeypatch.setattr(producer, "_write_bundle", fail)
    with pytest.raises(OSError, match="injected"):
        build(*inputs, tmp_path / "result.zip")
    assert set(tmp_path.iterdir()) == before


def test_original_consumer_refuses_a_corrupted_candidate_before_publication(tmp_path, inputs, monkeypatch):
    write = producer._write_bundle
    before = set(tmp_path.iterdir())

    def corrupt(snapshot, path, **kwargs):
        write(snapshot, path, **kwargs)
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("unexpected.txt", b"not in the selected transport")

    monkeypatch.setattr(producer, "_write_bundle", corrupt)
    with pytest.raises(WheelError, match="member count limit"):
        build(*inputs, tmp_path / "result.zip")
    assert set(tmp_path.iterdir()) == before


def test_publication_race_preserves_the_other_output(tmp_path, inputs, monkeypatch):
    link = os.link
    output = tmp_path / "result.zip"

    def race(source, destination):
        output.write_bytes(b"other producer's complete artifact")
        link(source, destination)

    monkeypatch.setattr(producer.os, "link", race)
    with pytest.raises(FileExistsError):
        build(*inputs, output)
    assert output.read_bytes() == b"other producer's complete artifact"


@pytest.mark.parametrize("budget", ["transport", "storage"])
@pytest.mark.parametrize("boundary", ["header", "closing-directory"])
def test_peak_storage_never_exceeds_budget_during_zip_bookkeeping(
    tmp_path, inputs, monkeypatch, budget, boundary,
):
    record, paths = inputs
    reference = build(record, paths, tmp_path / "reference.zip")
    total = sum(path.stat().st_size for path in paths.values())
    available = 1 if boundary == "header" else reference["size"] - 1
    original_open = Path.open
    peak = 0

    class ObservedOutput:
        def __init__(self, path, stream):
            self.path, self.stream = path, stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, data):
            nonlocal peak
            result = self.stream.write(data)
            self.stream.flush()
            peak = max(peak, self.path.stat().st_size)
            return result

    def observe(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return ObservedOutput(path, stream) if path.name == "wheels.zip" and mode == "xb" else stream

    monkeypatch.setattr(Path, "open", observe)
    options = (
        {"max_bundle_bytes": available} if budget == "transport"
        else {"max_snapshot_bytes": total + available}
    )
    with pytest.raises(WheelError):
        build(record, paths, tmp_path / "refused.zip", **options)
    assert peak <= available
    assert not (tmp_path / "refused.zip").exists()


def test_retained_input_exit_deadline_precedes_output_visibility(tmp_path, inputs, monkeypatch):
    retain = producer.retain_wheel_inputs

    @contextmanager
    def expire_on_exit(*args, **kwargs):
        with retain(*args, **kwargs) as snapshot:
            yield snapshot
            _check_deadline(0)

    monkeypatch.setattr(producer, "retain_wheel_inputs", expire_on_exit)
    output = tmp_path / "result.zip"
    with pytest.raises(WheelError, match="deadline"):
        build(*inputs, output)
    assert not output.exists()


def test_actual_command_emits_candidate_without_original_installation(tmp_path, inputs):
    record, _paths = inputs
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(record), encoding="utf-8")
    output = tmp_path / "command.zip"
    result = subprocess.run(
        [sys.executable, producer.__file__, "--profile", str(profile),
         "--wheel-dir", str(tmp_path), "--output", str(output)],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "profile_sha256": validate_profile(record).lock_id,
        "sha256": "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest(),
        "size": output.stat().st_size,
    }
