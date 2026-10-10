"""Build real repository wheels in temporary copies; never publish test versions."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_index_service.release import ReleaseError, build_descriptor, verify_descriptor


def test_real_wheels_verify_and_reject_frozen_pre_seam_core(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    sources = (
        repo / "agent-index-service",
        repo / "plugins" / "agent-index",
        repo / "libs" / "zdd",
        repo / "libs" / "agent-procutil",
        repo / "plugins" / "agent-index" / "libs" / "dropin-registry",
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    core_build = None
    for index, source in enumerate(sources):
        build = tmp_path / f"source-{index}"
        build.mkdir()
        for filename in ("pyproject.toml", "README.md"):
            shutil.copy2(source / filename, build / filename)
        shutil.copytree(
            source / "src", build / "src",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        if source.name == "agent-index":
            core_build = build
        result = subprocess.run(
            [sys.executable, "-c",
             f"from setuptools.build_meta import build_wheel; build_wheel({str(bundle)!r})"],
            cwd=build, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
    commit = "a" * 40
    with pytest.raises(ReleaseError, match="incompatible bundled requirement"):
        build_descriptor(bundle, source_commit=commit)
    assert core_build is not None
    old_core = next(bundle.glob("agent_index-*.whl"))
    old_core.unlink()
    metadata = core_build / "pyproject.toml"
    # dev deliberately freezes source versions; this temporary fixture models
    # the release pipeline's core bump without editing any repository version.
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
    path = bundle / "release.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    assert verify_descriptor(path, expected_source_commit=commit) == descriptor
