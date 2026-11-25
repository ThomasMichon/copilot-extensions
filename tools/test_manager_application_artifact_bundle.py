"""Verify that each Manager installer bundle can load its full-setup helper."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("provider", ["runtime-input-provider", "runtime-source-tools"])
def test_packaged_artifact_loader(provider: str, tmp_path: Path) -> None:
    groups = json.loads((ROOT / "worktree-manager/packaged-assets.json").read_bytes())["groups"]
    group, = (g for g in groups if provider in Path(g["payloadDir"]).parts
              and any(Path(name).name == "runtime_application_install.py" for name in g["files"]))
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    installer, = (Path(name) for name in group["files"] if Path(name).name == "runtime_application_install.py")
    for name in map(Path, group["files"]):
        if name.parent == installer.parent and name.suffix == ".py":
            shutil.copyfile(ROOT / group["canonicalDir"] / name, runtime / name.name)
    probe = """
import sys
from pathlib import Path
slot = Path.cwd()
runtime = slot / 'runtime'
sys.path.insert(0, str(runtime))
import runtime_application_install
helper = runtime_application_install._artifacts(slot)
assert Path(helper.__file__).samefile(runtime / 'runtime_application_artifacts.py')
assert Path(sys.modules['installer_dependency_config'].__file__).samefile(runtime / 'installer_dependency_config.py')
"""
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe], cwd=tmp_path, check=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
