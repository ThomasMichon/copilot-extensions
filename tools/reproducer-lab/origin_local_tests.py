"""Explicit local interpreter controls with synthetic package/metadata only."""

from __future__ import annotations

import json
import sys
import venv
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from process import run

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="explicit local Linux interpreter lane")

CONTROL = """
import dataclasses, json, runpy, sys
from pathlib import Path
probe = runpy.run_path(sys.argv[1])
value = probe["observe"]("bridge", runtime=Path(sys.argv[2]), source=Path(sys.argv[3]))
print(json.dumps(dataclasses.asdict(value), sort_keys=True))
"""


@pytest.mark.parametrize("change", ["none", "bytes", "editable", "version"])
def test_actual_isolated_interpreter_observes_only_its_synthetic_package(
    tmp_path: Path, change: str,
) -> None:
    runtime, source = tmp_path / "runtime", tmp_path / "source"
    prefix = runtime / "bridge"
    # No pip, project build, dependencies or actual plugin is installed.
    venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
    site = prefix / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    installed = site / "agent_bridge"
    original = source / "plugins/agent-bridge/src/agent_bridge"
    for package in (installed, original):
        package.mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"# synthetic main package\n")
    project = original.parent.parent
    (project / "pyproject.toml").write_text(
        '[project]\nname = "agent-bridge"\nversion = "1.2.3-dev4"\n', encoding="utf-8",
    )
    metadata = site / "agent_bridge-1.2.3.dev4.dist-info"
    metadata.mkdir()
    metadata_version = "1.2.3" if change == "version" else "1.2.3.dev4"
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: agent-bridge\nVersion: {metadata_version}\n",
        encoding="utf-8",
    )
    (metadata / "direct_url.json").write_text(json.dumps({
        "url": project.as_uri(),
        "dir_info": {"editable": True} if change == "editable" else {},
    }), encoding="utf-8")
    if change == "bytes":
        (installed / "__init__.py").write_bytes(b"# wrong synthetic bytes\n")
    poison = tmp_path / "ambient"
    poison.mkdir()
    (poison / "agent_bridge.py").write_text(
        'raise RuntimeError("ambient PYTHONPATH must not execute")\n', encoding="utf-8",
    )
    result = run(
        [str(prefix / "bin/python3"), "-I", "-B", "-c", CONTROL,
         str(Path(__file__).with_name("runtime_origin_probe.py")), str(runtime), str(source)],
        timeout=10, max_stdout_bytes=16 * 1024,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "PYTHONPATH": str(poison)},
    )
    if change == "none":
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["prefix"] == str(prefix)
        assert data["executable"] == str(prefix / "bin/python3")
        assert data["package"] == str(installed)
        assert data["source_package"] == str(original)
        assert data["version"] == "1.2.3.dev4" and data["python_files"] == 1
        assert data["python_bytes"] == 25
        assert not list(installed.glob("__pycache__/*"))
    else:
        assert result.returncode != 0 and not result.stdout
        assert {
            "bytes": "Python bytes differ", "editable": "noneditable local project",
            "version": "distribution version differs",
        }[change] in result.stderr
    assert "ambient PYTHONPATH must not execute" not in result.stderr
