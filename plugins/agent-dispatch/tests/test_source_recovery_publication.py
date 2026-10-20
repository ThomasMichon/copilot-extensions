"""Source recovery and reuse exercise real snapshots, admission and publication."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from . import source_publication_fixture as fixture


def _stage(tmp_path: Path, ext: str):
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX installer fixture")
    return fixture.stage(tmp_path)


def _install(env: dict[str, str], payload: Path, ext: str, action: str = "install") -> None:
    result = fixture.run(ext, payload / "scripts" / f"install.{ext}", [action], env)
    assert result.returncode == 0, result.stdout + result.stderr


def _selected(root: Path, identity) -> None:
    assert (root / "current-version").read_text().strip() == identity.runtime_version
    document = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert document["runtime_version"] == identity.runtime_version
    assert document["runtime_source"] == identity.document()
    assert document["source"]["version"] == fixture.PACKAGE


def _published(root: Path, identity) -> None:
    _selected(root, identity)
    assert (root / "stamped-version").read_text().strip() == identity.runtime_version
    assert Path((root / "payload-dir").read_text().strip()) == root / "snapshots" / identity.runtime_version


def _completion(root: Path, sources) -> dict[str, bytes]:
    return {
        identity.runtime_version: (
            root / "versions" / identity.runtime_version / ".install-complete.json"
        ).read_bytes()
        for _, identity in sources
    }


def _unavailable(sources) -> None:
    for payload, _ in sources:
        payload.rename(payload.with_name(payload.name + "-unavailable"))


def _recover(env: dict[str, str], root: Path, ext: str, identity) -> None:
    resolver = root / "bin" / f"resolve-runtime.{ext}"
    resolver.rename(resolver.with_suffix(f".{ext}.unavailable"))
    stub = Path(env["HOME"]) / ".local" / "bin" / (
        "agent-dispatch.ps1" if ext == "ps1" else "agent-dispatch"
    )
    result = fixture.run(ext, stub, ["--version"], env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == f"agent-dispatch {identity.runtime_version}"
    _published(root, identity)


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_stamp_then_direct_install_recovers_matching_source(tmp_path: Path, ext: str):
    env, root, sources = _stage(tmp_path, ext)
    (a, identity_a), (b, identity_b) = sources
    completion = _completion(root, sources)
    fixture.activate(env, root, identity_a.runtime_version)
    _install(env, a, ext, "stamp")
    assert (root / "current-version").read_text().strip() == identity_a.runtime_version
    assert not (root / "deploy-manifest.json").exists()
    _install(env, b, ext)
    _published(root, identity_b)
    _unavailable(sources)

    # Also recover a legacy mismatch produced before publication aligned the markers.
    (root / "stamped-version").write_text(identity_a.runtime_version, encoding="utf-8")
    _recover(env, root, ext, identity_b)
    assert _completion(root, sources) == completion


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_deferred_stamp_recovers_its_exact_source(tmp_path: Path, ext: str):
    env, root, sources = _stage(tmp_path, ext)
    (a, identity_a), (b, identity_b) = sources
    completion = _completion(root, sources)
    _install(env, b, ext)
    _published(root, identity_b)
    manifest = (root / "deploy-manifest.json").read_bytes()
    _install(env, a, ext, "stamp")
    _selected(root, identity_b)
    assert (root / "deploy-manifest.json").read_bytes() == manifest
    assert (root / "stamped-version").read_text().strip() == identity_a.runtime_version
    assert Path((root / "payload-dir").read_text().strip()) == root / "snapshots" / identity_a.runtime_version
    _unavailable(sources)
    _recover(env, root, ext, identity_a)
    assert _completion(root, sources) == completion


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_source_reuse_republishes_manifest_on_a_b_a(tmp_path: Path, ext: str):
    env, root, sources = _stage(tmp_path, ext)
    (a, identity_a), (b, identity_b) = sources
    completion = _completion(root, sources)
    _install(env, a, ext)
    _published(root, identity_a)
    _install(env, b, ext)
    _published(root, identity_b)
    _unavailable(sources)
    _install(env, root / "snapshots" / identity_a.runtime_version, ext)
    _published(root, identity_a)
    assert _completion(root, sources) == completion


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("broken_module", ["embody", "__main__"])
def test_corrupt_source_reuse_cannot_borrow_health_from_pythonpath(
    tmp_path: Path, ext: str, broken_module: str,
):
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX installer fixture")
    env, root, sources = fixture.stage(tmp_path, broken_b=broken_module == "embody")
    (a, identity_a), (b, identity_b) = sources
    if broken_module == "__main__":
        python = root / "versions" / identity_b.runtime_version / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        query = subprocess.run(
            [str(python), "-I", "-c", 'import sysconfig; print(sysconfig.get_path("purelib"))'],
            env=env, capture_output=True, text=True, check=True, timeout=15,
            creationflags=fixture.HEADLESS,
        )
        (Path(query.stdout.strip()) / "agent_dispatch/__main__.py").write_text(
            "raise RuntimeError('broken fixture CLI')\n", encoding="utf-8",
        )
        probe = subprocess.run(
            [str(python), "-I", "-c",
             "import agent_dispatch, agent_dispatch.embody; import agent_dispatch.__main__"],
            env=env, capture_output=True, text=True, timeout=15,
            creationflags=fixture.HEADLESS,
        )
        assert probe.returncode != 0
        assert "broken fixture CLI" in probe.stderr
    result = fixture.run(ext, a / "scripts" / f"install.{ext}", ["install"], env)
    assert result.returncode == 0, result.stdout + result.stderr
    before = {
        name: (root / name).read_bytes()
        for name in ("current-version", "stamped-version", "payload-dir", "deploy-manifest.json")
    }
    shadow = tmp_path / "shadow" / "agent_dispatch"
    shadow.mkdir(parents=True)
    for name in ("__init__.py", "embody.py", "__main__.py"):
        (shadow / name).write_text("", encoding="utf-8")
    env["PYTHONPATH"] = str(shadow.parent)
    result = fixture.run(ext, b / "scripts" / f"install.{ext}", ["install"], env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "health gate" in (result.stdout + result.stderr)
    assert (root / "current-version").read_text().strip() == identity_a.runtime_version
    for name, content in before.items():
        assert (root / name).read_bytes() == content
