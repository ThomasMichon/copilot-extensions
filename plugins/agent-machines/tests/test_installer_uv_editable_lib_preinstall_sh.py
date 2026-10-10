"""Guard for init.sh's non-uv (bare-pip) preinstall loop covering the
uv-editable canonical-reference libs (vendor-pointer-generalization effort):
dropin-registry, plugin-resolve, agent-procutil, machine-transport, plugin-activation.
Exercises the loop via real bash execution for both the uv path and the
bare-pip fallback path, confirming plugin-local/repo-root-canonical
resolution and the required dropin-registry/plugin-resolve-before-
plugin-activation install order -- a regression here would still pass
every other installer guard while silently reintroducing the bare-pip
dependency failure Copilot's review caught on PR #4420."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "init.sh"

pytestmark = pytest.mark.guard


def _extract_loop() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    start = "for _lib_entry in \\\n    'dropin-registry:agent-dropin-registry'"
    end = "\ndone\n"
    return start + text.split(start, 1)[1].split(end, 1)[0] + "\ndone"


def _run(bash: str, plugin_dir: Path, have_uv: bool) -> subprocess.CompletedProcess[str]:
    loop = _extract_loop()
    script = f"""
set -euo pipefail
_fail() {{ :; }}
_ok() {{ echo "OK:$1"; }}
uv() {{ echo "UV_INSTALL:${{@: -2:1}}"; }}
VENV_PYTHON=venv_python_stub
venv_python_stub() {{ echo "PIP_INSTALL:${{@: -1}}"; }}
PLUGIN_DIR='{plugin_dir}'
HAVE_UV={1 if have_uv else 0}
{loop}
"""
    return subprocess.run(
        [bash, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ},
        timeout=30,
    )


def _make_libs(root: Path) -> None:
    for lib in (
        "dropin-registry", "plugin-resolve", "agent-procutil", "machine-transport", "plugin-activation",
    ):
        lib_dir = root / lib
        lib_dir.mkdir(parents=True)
        (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_resolves_plugin_local_copy_and_orders_correctly(
    have_uv: bool, tmp_path: Path
):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-machines"
    _make_libs(plugin_dir / "libs")

    proc = _run(bash, plugin_dir, have_uv)
    assert proc.returncode == 0, proc.stderr

    marker = "UV_INSTALL:" if have_uv else "PIP_INSTALL:"
    installs = [
        line.split(":", 1)[1]
        for line in proc.stdout.splitlines()
        if line.startswith(marker)
    ]
    assert len(installs) == 5
    order = [Path(p).name for p in installs]
    assert "machine-transport" in order
    assert order.index("plugin-activation") > order.index("dropin-registry")
    assert order.index("plugin-activation") > order.index("plugin-resolve")


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_falls_back_to_repo_root_canonical_when_absent(
    have_uv: bool, tmp_path: Path
):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-machines"
    plugin_dir.mkdir(parents=True)
    canonical = tmp_path / "libs"
    _make_libs(canonical)

    proc = _run(bash, plugin_dir, have_uv)
    assert proc.returncode == 0, proc.stderr

    marker = "UV_INSTALL:" if have_uv else "PIP_INSTALL:"
    installs = [
        line.split(":", 1)[1]
        for line in proc.stdout.splitlines()
        if line.startswith(marker)
    ]
    assert len(installs) == 5
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(canonical)))
