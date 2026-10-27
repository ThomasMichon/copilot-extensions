"""Content-bound build reuse, publication failure and concurrent construction."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time

import pytest

for library in ("installer-engine", "versioned-runtime"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / library))

import runtime_source_wheel_cache as cache
from installer_wheel_inputs import WheelError


@pytest.fixture
def assembly(tmp_path):
    root = tmp_path / "installation"
    root.mkdir()
    builds = []

    def build(output):
        builds.append(output)
        path = output / "example-1-py3-none-any.whl"
        path.write_bytes(b"selected artifact")
        return path

    def assemble(name, *, binding=b"source+target+exact-backend", **changes):
        return cache.cached_source_wheel(
            root, binding, tmp_path / name,
            **({
                "build": build, "validate": lambda _path: None,
                "max_archive_bytes": 100, "deadline": time.monotonic() + 10,
            } | changes),
        )

    return root, builds, assemble


def test_fresh_output_reuses_exact_artifact_and_changed_inputs_rebuild(assembly):
    root, builds, assemble = assembly
    first = assemble("first")
    repeated = assemble("repeated")
    changed = assemble("changed", binding=b"source-body-change+same-version")
    assert first.read_bytes() == repeated.read_bytes() == changed.read_bytes()
    assert first != repeated
    assert len(builds) == 2
    receipts = list((root / ".source-wheel-cache" / "inputs").glob("*/wheel.json"))
    assert len(receipts) == 2
    first.write_bytes(b"caller changed its own copy")
    assert assemble("independent").read_bytes() == b"selected artifact"
    assert len(builds) == 2


@pytest.mark.parametrize("failure", ["build", "validation"])
def test_failed_build_or_validation_cannot_publish_or_poison_retry(assembly, failure):
    root, builds, assemble = assembly

    def rejected(path):
        if failure == "build":
            (path / "partial.whl").write_bytes(b"partial")
        raise RuntimeError("interrupted construction")

    with pytest.raises(RuntimeError, match="interrupted"):
        assemble("failed", **{failure if failure == "build" else "validate": rejected})
    assert not list((root / ".source-wheel-cache" / "inputs").glob("*/wheel.json"))
    assert assemble("retry").read_bytes() == b"selected artifact"
    assert len(builds) == (1 if failure == "build" else 2)


@pytest.mark.parametrize("corruption", ["bytes", "receipt", "extra", "missing"])
def test_corrupt_completed_entry_is_explicit_and_never_rebuilt(assembly, corruption):
    root, builds, assemble = assembly
    assemble("first")
    receipt = next((root / ".source-wheel-cache" / "inputs").glob("*/wheel.json"))
    record = json.loads(receipt.read_text())
    wheel = receipt.parent / record["filename"]
    if corruption == "bytes":
        wheel.write_bytes(b"X" * wheel.stat().st_size)
    elif corruption == "receipt":
        record["key"] = "0" * 64
        receipt.write_text(json.dumps(record))
    elif corruption == "extra":
        (receipt.parent / "foreign").write_bytes(b"not owned")
    else:
        wheel.unlink()
    with pytest.raises((WheelError, FileNotFoundError)):
        assemble("repeated")
    assert len(builds) == 1
    assert not (root.parent / "repeated" / record["filename"]).exists()


def test_existing_caller_output_is_not_removed_on_collision(assembly):
    _root, builds, assemble = assembly
    first = assemble("first")
    first.write_bytes(b"caller owned")
    with pytest.raises(FileExistsError):
        assemble("first")
    assert first.read_bytes() == b"caller owned"
    assert len(builds) == 1


def test_copy_failure_removes_only_its_partial_destination(assembly, monkeypatch):
    root, builds, assemble = assembly
    assemble("first")
    digest = cache._digest

    def interrupted(stream, *args, destination=None, **options):
        if destination is not None:
            destination.write(b"partial")
            raise RuntimeError("copy interrupted")
        return digest(stream, *args, **options)

    monkeypatch.setattr(cache, "_digest", interrupted)
    with pytest.raises(RuntimeError, match="copy interrupted"):
        assemble("second")
    assert not list((root.parent / "second").iterdir())
    assert len(builds) == 1


def test_two_builders_publish_once_under_existing_kernel_lease(assembly):
    root, builds, assemble = assembly
    entered = threading.Event()
    proceed = threading.Event()

    def build(output):
        builds.append(output)
        entered.set()
        assert proceed.wait(5)
        path = output / "example-1-py3-none-any.whl"
        path.write_bytes(b"selected artifact")
        return path

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(assemble, "first", build=build)
        assert entered.wait(5)
        second = workers.submit(assemble, "second", build=build)
        proceed.set()
        assert first.result().read_bytes() == second.result().read_bytes()
    assert len(builds) == 1
    assert len(list((root / ".source-wheel-cache" / "inputs").glob("*/wheel.json"))) == 1


@pytest.mark.parametrize("changed", ["source", "tree-mode", "backend", "target", "extras", "uv", "environment"])
def test_composer_identity_invalidates_each_admitted_build_input(assembly, tmp_path, monkeypatch, changed):
    import runtime_source_wheels as completion
    from build_toolchain_lock import ToolchainLock

    _root, builds, assemble = assembly
    uv = tmp_path / "uv"
    python = tmp_path / "python"
    uv.write_bytes(b"selected uv")
    python.write_bytes(b"selected python")
    record = {
        "source": {"assembly_hash": "1" * 64, "source_tree": "5" * 40},
        "target": {"tags": ["py3-none-any"]}, "extras": [],
    }
    profile = {"wheels": [{
        "name": "backend", "version": "1", "filename": "backend-1-py3-none-any.whl",
        "sha256": "sha256:" + "2" * 64,
    }]}
    toolchain = ToolchainLock(python, {})
    monkeypatch.setattr(completion, "_build_environment_identity", lambda _provider: "selected environment")

    def binding():
        return json.dumps(completion._build_cache_binding(
            record, profile, toolchain, str(uv), deadline=time.monotonic() + 10,
            opaque_identity=lambda text: text,
        ), sort_keys=True).encode()

    assemble("first", binding=binding())
    assemble("repeated", binding=binding())
    assert len(builds) == 1
    if changed == "source":
        record["source"]["assembly_hash"] = "3" * 64
    elif changed == "tree-mode":
        record["source"]["source_tree"] = "6" * 40
    elif changed == "backend":
        profile["wheels"][0]["sha256"] = "sha256:" + "4" * 64
    elif changed == "target":
        record["target"]["tags"] = ["cp313-none-any"]
    elif changed == "extras":
        record["extras"] = ["optional"]
    elif changed == "uv":
        uv.write_bytes(b"different uv")
    else:
        monkeypatch.setattr(completion, "_build_environment_identity", lambda _provider: "changed environment")
    assemble("changed", binding=binding())
    assert len(builds) == 2
