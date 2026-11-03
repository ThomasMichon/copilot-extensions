"""The offline resolver sees only the retained pool, including during races."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_core_native_delivery import (
    _configure_native_tls,
    _offline_target,
    _write_offline_pool,
    local_tls_feed,  # noqa: F401
    tls_source,  # noqa: F401
)
from test_promotion_python_artifacts import _wheel_bytes


def test_transient_unheld_wheel_cannot_influence_offline_selection(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, digest = tls_source
    pool = _write_offline_pool(tmp_path, wheel, digest)
    _configure_native_tls(tmp_path, monkeypatch, state)
    snapshots = []
    inspect = producer._inspect_offline_pool
    run = producer.run_owned

    def remember(snapshot, **options):
        inspect(snapshot, **options)
        snapshots.append(snapshot)

    def inject(*args, **options):
        directory = next(iter(snapshots[0].paths.values())).parent
        extra = directory / "fixture-2.0-py3-none-any.whl"
        extra.write_bytes(_wheel_bytes("fixture", "2.0"))
        try:
            return run(*args, **options)
        finally:
            extra.unlink()

    monkeypatch.setattr(producer, "_inspect_offline_pool", remember)
    monkeypatch.setattr(producer, "run_owned", inject)
    deadline = time.monotonic() + 120
    destination = tmp_path / "delivery.zip"
    producer._build_from_snapshot(
        tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
        json.dumps(manifest).encode(), wheels, _offline_target(producer, deadline),
        uv=uv, download_origins=(), materials={}, material_paths={}, destination=destination,
        deadline=deadline, max_bytes=4 << 20, native_lock=None, offline_wheel_pool=pool,
    )
    with zipfile.ZipFile(destination) as delivery:
        profile = json.loads(delivery.read("profile.json"))
    selected = next(row for row in profile["wheels"] if row["name"] == "fixture")
    assert selected["version"] == "1.0" and selected["sha256"] == "sha256:" + digest
    assert [path for path, _ in state["requests"]] == ["/simple/fixture/", "/files/fixture-1.0-py3-none-any.whl"]


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length pool paths")
def test_offline_pool_accepts_deep_windows_input_paths(tmp_path, monkeypatch, tls_source):  # noqa: F811
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, digest = tls_source
    directory = tmp_path / ("deep-" + "x" * 140) / ("nested-" + "y" * 65)
    native = Path(producer.source_owner.native_retained_path(directory))
    native.mkdir(parents=True)
    try:
        filename = "fixture-1.0-py3-none-any.whl"
        (native / filename).write_bytes(wheel)
        (native / "pool.json").write_text(json.dumps({filename: "sha256:" + digest}))
        _configure_native_tls(tmp_path, monkeypatch, state)
        deadline = time.monotonic() + 120
        result = producer._build_from_snapshot(
            tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
            json.dumps(manifest).encode(), wheels, _offline_target(producer, deadline),
            uv=uv, download_origins=(), materials={}, material_paths={},
            destination=tmp_path / "delivery.zip", deadline=deadline, max_bytes=4 << 20,
            native_lock=None, offline_wheel_pool=directory / "pool.json",
        )
        assert result["selection"] == "offline-wheel-pool"
    finally:
        shutil.rmtree(native)


@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.parametrize("location", ["url", "windows", "posix"])
def test_offline_pool_failure_is_useful_bounded_and_private(tmp_path, monkeypatch, timeout, location):
    import core_native_delivery as producer

    directory = tmp_path / "pool" / "wheels"
    directory.mkdir(parents=True)
    wheel = directory / "fixture-1.0-py3-none-any.whl"
    wheel.write_bytes(b"held")
    pool = SimpleNamespace(paths={wheel.name: wheel}, verify=lambda: None)
    secret = "private-example-value"
    location_text = {
        "url": "https://private.example.invalid/path private-tail",
        "windows": r"C:\Users\private owner\hidden>private-tail",
        "posix": "/home/private owner/hidden>private-tail",
    }[location]
    stderr = (
        "No solution: fixture>=2 conflicts with fixture<2\n"
        + location_text + "\n"
        + "credential=" + secret + "\n" + "more details " * 200
    )

    def fail(*_args, **_kwargs):
        if timeout:
            raise producer.ChildTimeoutExpired(["private-command"], 1, stderr=stderr)
        raise subprocess.CalledProcessError(1, ["private-command"], stderr=stderr)

    monkeypatch.setattr(producer, "run_owned", fail)
    transaction = SimpleNamespace(argv=("fixture-uv",), cwd=tmp_path, environment={"FEED_TOKEN": secret})
    with pytest.raises(producer.ProfileError) as failure:
        producer._compile_offline_pool(transaction, pool, deadline=time.monotonic() + 60)
    message = str(failure.value)
    if timeout:
        assert "timed out" in message
    else:
        assert "fixture>=2" in message
    assert secret not in message and "private.example.invalid" not in message
    assert "private owner" not in message and "private-tail" not in message and "private-command" not in message
    assert len(message) < 600 and failure.value.__suppress_context__
