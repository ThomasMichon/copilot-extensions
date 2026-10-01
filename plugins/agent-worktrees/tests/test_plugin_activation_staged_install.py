from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[3]
PLUGIN_LIBS = REPO / "plugins" / "agent-worktrees" / "libs"
pytestmark = pytest.mark.guard


def _copy_installed_package(stage_root: Path, site_packages: Path, lib: str) -> None:
    src_pkg = stage_root / lib / "src" / lib.replace("-", "_")
    dst_pkg = site_packages / lib.replace("-", "_")
    shutil.copytree(src_pkg, dst_pkg)


def test_plugin_activation_import_survives_stage_cleanup_when_installed_from_real_copy(
    tmp_path: Path,
) -> None:
    """Regression for #4788: a staged non-editable install may record its
    source as a throwaway `.install-stage/...` tree, then delete that tree
    after installation. The installed `agent-worktrees` vendored
    `plugin-activation` copy must remain importable anyway -- the runtime
    package itself must be self-contained, not a passthrough stub that
    still needs the staging source to exist later."""
    stage_root = tmp_path / ".install-stage" / "agent-worktrees"
    site_packages = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)

    for lib in ("plugin-activation", "dropin-registry", "plugin-resolve"):
        shutil.copytree(PLUGIN_LIBS / lib, stage_root / lib)
        _copy_installed_package(stage_root, site_packages, lib)

    dist_info = site_packages / "agent_plugin_activation-0.1.0.dev0.dist-info"
    dist_info.mkdir()
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": stage_root.as_uri(), "dir_info": {}}) + "\n",
        encoding="utf-8",
    )

    shutil.rmtree(stage_root)
    assert not stage_root.exists()

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, plugin_activation;"
                "print(json.dumps({"
                "'pkg': plugin_activation.__file__, "
                "'resolver': plugin_activation.resolve_active_plugins.__module__"
                "}))"
            ),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(site_packages)},
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert Path(payload["pkg"]).is_relative_to(site_packages)
    assert payload["resolver"] == "plugin_activation.resolver"
