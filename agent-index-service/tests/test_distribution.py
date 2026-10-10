from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

from agent_procutil import no_window_kwargs
from packaging.requirements import Requirement
from packaging.version import Version

from agent_index_service import __version__


def test_normal_wheel_metadata_and_console_program(tmp_path):
    source = Path(__file__).resolve().parents[1]
    build = tmp_path / "build-source"
    build.mkdir()
    for name in ("pyproject.toml", "README.md", "setup.py", "_build_runtime_resource.py", "MANIFEST.in"):
        shutil.copy2(source / name, build / name)
    canonical = source.parent / "libs" / "versioned-runtime" / "versioned_runtime.py"
    primitive = tmp_path / "libs" / "versioned-runtime" / "versioned_runtime.py"
    primitive.parent.mkdir(parents=True)
    shutil.copy2(canonical, primitive)
    shutil.copytree(source / "src", build / "src", ignore=shutil.ignore_patterns("__pycache__"))
    output = tmp_path / "wheel"
    output.mkdir()
    result = subprocess.run(
        [sys.executable, "-c",
         f"from setuptools.build_meta import build_wheel; build_wheel({str(output)!r})"],
        cwd=build, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        members = wheel.namelist()
        metadata_path = next(name for name in members if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(wheel.read(metadata_path).decode())
        assert metadata["Name"] == "agent-index-service"
        assert Version(metadata["Version"]) == Version(__version__)
        requirements = metadata.get_all("Requires-Dist")
        assert any(req.startswith("agent-index>=") for req in requirements)
        assert any(req.startswith("agent-index[server,store]>=") for req in requirements)
        core_requirements = [
            Requirement(req) for req in requirements if Requirement(req).name == "agent-index"
        ]
        assert len(core_requirements) == 2
        for requirement in core_requirements:
            assert not requirement.specifier.contains("0.1.0.dev210", prereleases=True)
            assert not requirement.specifier.contains("0.10.11.dev1", prereleases=True)
            assert requirement.specifier.contains("0.10.12.dev1", prereleases=True)
        assert all(" @ " not in req and "file:" not in req for req in requirements)
        entries = next(name for name in members if name.endswith(".dist-info/entry_points.txt"))
        assert "agent-index-service = agent_index_service.__main__:main" in (
            wheel.read(entries).decode()
        )
        assert not any("plugin.json" in name or "agent_index_engine" in name for name in members)
        assert wheel.read("agent_index_service/_versioned_runtime.py") == canonical.read_bytes()
