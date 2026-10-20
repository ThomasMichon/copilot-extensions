"""Build real repository wheels in temporary copies; never publish test versions."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_index_service.release import ReleaseError, build_descriptor, verify_descriptor


def test_real_wheels_verify_and_reject_frozen_pre_seam_core(distribution_root):
    repo = Path(__file__).resolve().parents[3]
    sources = (
        repo / "services" / "index",
        repo / "plugins" / "agent-index",
        repo / "libs" / "zdd",
        repo / "libs" / "agent-procutil",
        repo / "plugins" / "agent-index" / "libs" / "dropin-registry",
    )
    bundle = distribution_root / "bundle"
    bundle.mkdir()
    core_build = None
    for index, source in enumerate(sources):
        build = distribution_root / f"source-{index}"
        build.mkdir()
        for filename in ("pyproject.toml", "README.md"):
            shutil.copy2(source / filename, build / filename)
        if source.name == "index":
            for filename in ("setup.py", "_build_runtime_resource.py", "MANIFEST.in"):
                shutil.copy2(source / filename, build / filename)
            primitive = distribution_root / "libs" / "versioned-runtime" / "versioned_runtime.py"
            primitive.parent.mkdir(parents=True)
            shutil.copy2(repo / "libs" / "versioned-runtime" / "versioned_runtime.py", primitive)
            for name in (
                "versioned_runtime_leases.py", "versioned_runtime_construction.py",
                "versioned_runtime_source.py", "versioned_runtime_publication.py",
            ):
                shutil.copy2(repo / "libs/versioned-runtime" / name, primitive.with_name(name))
            shutil.copy2(
                repo / "libs" / "versioned-runtime" / "standalone_build.py",
                primitive.with_name("standalone_build.py"),
            )
        shutil.copytree(
            source / "src", build / "src",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        if source.name == "agent-index":
            core_build = build
        if source.name in {"agent-index", "agent-procutil"}:
            version = "0.10.11-dev1" if source.name == "agent-index" else "0.1.0-dev8"
            metadata = build / "pyproject.toml"
            metadata.write_text(re.sub(
                r'^version\s*=\s*"[^"]+"', f'version = "{version}"',
                metadata.read_text(encoding="utf-8"), count=1, flags=re.MULTILINE,
            ), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-c",
             f"from setuptools.build_meta import build_wheel; build_wheel({str(bundle)!r})"],
            cwd=build, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if source.name == "index":
            with zipfile.ZipFile(next(bundle.glob("agent_index_service-*.whl"))) as wheel:
                assert wheel.read("agent_index_service/versioned_runtime_publication.py") == (
                    repo / "libs" / "versioned-runtime" / "versioned_runtime_publication.py"
                ).read_bytes()
    commit = "a" * 40
    with pytest.raises(ReleaseError, match="incompatible bundled requirement: agent-index"):
        build_descriptor(bundle, source_commit=commit)
    assert core_build is not None
    old_core = next(bundle.glob("agent_index-*.whl"))
    old_core.unlink()
    metadata = core_build / "pyproject.toml"
    # Pin both sides of the seam independently of dev or promoted source versions.
    metadata.write_text(re.sub(
        r'^version\s*=\s*"[^"]+"', 'version = "0.10.12-dev1"',
        metadata.read_text(encoding="utf-8"), count=1, flags=re.MULTILINE,
    ), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c",
         f"from setuptools.build_meta import build_wheel; build_wheel({str(bundle)!r})"],
        cwd=core_build, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    descriptor = build_descriptor(bundle, source_commit=commit)
    assert descriptor["core_version"] == "0.10.12.dev1"
    path = bundle / "release.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    assert verify_descriptor(path, expected_source_commit=commit) == descriptor

    fleet_source = repo / "fleet-manager"
    fleet_build = distribution_root / "fleet"
    fleet_build.mkdir()
    for filename in ("pyproject.toml", "README.md"):
        shutil.copy2(fleet_source / filename, fleet_build / filename)
    shutil.copytree(
        fleet_source / "src", fleet_build / "src", ignore=shutil.ignore_patterns("__pycache__"),
    )
    result = subprocess.run(
        [sys.executable, "-c",
         f"from setuptools.build_meta import build_wheel; build_wheel({str(bundle)!r})"],
        cwd=fleet_build, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    fleet = build_descriptor(bundle, source_commit=commit, profile="fleet")
    assert {item["distribution"] for item in fleet["artifacts"]} == {
        *(item["distribution"] for item in descriptor["artifacts"]), "fleet-manager",
    }
    path.write_text(json.dumps(fleet), encoding="utf-8")
    assert verify_descriptor(path, expected_source_commit=commit, profile="fleet") == fleet
    with pytest.raises(ReleaseError):
        verify_descriptor(path, expected_source_commit=commit)
