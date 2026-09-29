"""Guard for install.sh's two `agent-procutil` non-uv (bare-pip) preinstall
blocks (vendor-pointer-generalization effort): the service-runtime venv and
the durable-engine-runtime venv. Both mirror the existing `zdd` preinstall
immediately above them. Exercises each block via real bash execution for
both the uv path and the bare-pip fallback path, confirming plugin-local/
repo-root-canonical resolution -- a regression here would still pass every
other installer guard while silently reintroducing the bare-pip dependency
failure the review on PR #4465 caught."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.sh"

pytestmark = pytest.mark.guard

_RESOLVE_VENDORED_LIB_START = "_resolve_vendored_lib() {"
_RESOLVE_VENDORED_LIB_END = "\n}\n\n"

_BLOCKS = {
    "service": (
        "    # agent-procutil is a `uv`-editable canonical reference in a dev",
        "\n\n    _pip_install() {",
    ),
    "engine": (
        "    # agent-procutil is likewise a `uv`-editable canonical reference in a",
        "\n\n    # agent-index-engine",
    ),
}

# A wider span than `_BLOCKS["engine"]`: includes the `rc`-gate and the
# start of the main-package install call, so a test can prove a failed
# `agent-procutil` preinstall actually SKIPS the main install rather than
# merely being logged (PR #4465 review).
_ENGINE_WITH_GATE_START = _BLOCKS["engine"][0]
_ENGINE_WITH_GATE_END = '\n            uv "${uv_args[@]}" || rc=$?'


def _extract(text: str, start: str, end: str) -> str:
    return start + text.split(start, 1)[1].split(end, 1)[0]


def _resolve_vendored_lib_fn(text: str) -> str:
    return _extract(text, _RESOLVE_VENDORED_LIB_START, _RESOLVE_VENDORED_LIB_END) + "\n}"


def _run(
    bash: str, block_name: str, plugin_dir: Path, have_uv: bool, *, isolated_home: Path
) -> tuple[subprocess.CompletedProcess[str], Path]:
    text = INSTALLER.read_text(encoding="utf-8")
    start, end = _BLOCKS[block_name]
    block = _extract(text, start, end)
    # The engine block references $ENGINE_VENV_PYTHON, the service block
    # $VENV_PYTHON -- normalize both to the same stub variable name.
    block = block.replace("$VENV_PYTHON", "$THE_VENV_PYTHON").replace(
        "$ENGINE_VENV_PYTHON", "$THE_VENV_PYTHON"
    )
    # The engine block redirects its whole install call to `>/dev/null
    # 2>&1` (deliberately, in the real script, to keep noisy output out of
    # the main install log) -- so stub output must go to a marker FILE,
    # never stdout, or the engine variant's own redirect would silently
    # swallow the proof of what was installed.
    marker = isolated_home.parent / "marker.txt"
    script = f"""
set -uo pipefail
_fail() {{ echo "FAIL:$1"; exit 1; }}
uv() {{ echo "UV_INSTALL_ARGS:$*" >> '{marker}'; }}
THE_VENV_PYTHON=venv_python_stub
venv_python_stub() {{ echo "PIP_INSTALL_ARGS:$*" >> '{marker}'; }}
PLUGIN_DIR='{plugin_dir}'
have_uv={1 if have_uv else 0}
{_resolve_vendored_lib_fn(text)}
{block}
"""
    environment = {**os.environ, "HOME": str(isolated_home)}
    proc = subprocess.run(
        [bash, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )
    return proc, marker


def _install_path(marker: Path) -> str | None:
    if not marker.exists():
        return None
    for line in marker.read_text(encoding="utf-8").splitlines():
        if line.startswith("PIP_INSTALL_ARGS:"):
            args = line[len("PIP_INSTALL_ARGS:"):].split()
            return args[-1] if args else None
        if not line.startswith("UV_INSTALL_ARGS:"):
            continue
        args = line[len("UV_INSTALL_ARGS:"):].split()
        for arg in args:
            if arg not in {
                "pip", "install", "--python", "--reinstall-package",
                "--refresh-package", "--quiet", "agent-procutil",
                "venv_python_stub",
            }:
                return arg
    return None


@pytest.mark.parametrize("block_name", ["service", "engine"])
@pytest.mark.parametrize("have_uv", [True, False])
def test_procutil_preinstall_resolves_plugin_local_copy(
    block_name: str, have_uv: bool, tmp_path: Path
):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-index"
    lib_dir = plugin_dir / "libs" / "agent-procutil"
    lib_dir.mkdir(parents=True)
    (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc, marker = _run(bash, block_name, plugin_dir, have_uv, isolated_home=tmp_path / "home")
    assert proc.returncode == 0, proc.stderr

    resolved = _install_path(marker)
    assert resolved is not None
    assert os.path.realpath(resolved) == os.path.realpath(lib_dir)


@pytest.mark.parametrize("block_name", ["service", "engine"])
@pytest.mark.parametrize("have_uv", [True, False])
def test_procutil_preinstall_falls_back_to_repo_root_canonical_when_absent(
    block_name: str, have_uv: bool, tmp_path: Path
):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-index"
    plugin_dir.mkdir(parents=True)
    canonical = tmp_path / "libs" / "agent-procutil"
    canonical.mkdir(parents=True)
    (canonical / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    proc, marker = _run(bash, block_name, plugin_dir, have_uv, isolated_home=tmp_path / "home")
    assert proc.returncode == 0, proc.stderr

    resolved = _install_path(marker)
    assert resolved is not None
    assert os.path.realpath(resolved) == os.path.realpath(canonical)


@pytest.mark.parametrize("block_name", ["service", "engine"])
@pytest.mark.parametrize("have_uv", [True, False])
def test_procutil_preinstall_is_a_noop_when_neither_copy_exists(
    block_name: str, have_uv: bool, tmp_path: Path
):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-index"
    plugin_dir.mkdir(parents=True)

    proc, marker = _run(bash, block_name, plugin_dir, have_uv, isolated_home=tmp_path / "home")
    assert proc.returncode == 0, proc.stderr
    assert _install_path(marker) is None


def test_engine_skips_main_install_when_procutil_preinstall_fails(tmp_path: Path):
    """`agent-index` declares only an UNVERSIONED `agent-procutil`
    requirement, so if the preinstall refresh fails, the subsequent main-
    package install could otherwise still "succeed" against a stale copy
    already present in a preserved engine venv (PR #4465 review). Proves
    the main install is genuinely SKIPPED, not merely logged."""
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")

    text = INSTALLER.read_text(encoding="utf-8")
    block = _extract(text, _ENGINE_WITH_GATE_START, _ENGINE_WITH_GATE_END)
    block += "\n        fi\n    fi"
    block = block.replace("$ENGINE_VENV_PYTHON", "$THE_VENV_PYTHON")

    plugin_dir = tmp_path / "plugins" / "agent-index"
    lib_dir = plugin_dir / "libs" / "agent-procutil"
    lib_dir.mkdir(parents=True)
    (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    marker = tmp_path / "marker.txt"

    script = f"""
set -uo pipefail
_fail() {{ echo "FAIL:$1"; exit 1; }}
uv() {{
    echo "UV_CALL:$*" >> '{marker}'
    if [[ "$1 $2" == "pip install" && "$*" == *"agent-procutil"* ]]; then
        return 1
    fi
    return 0
}}
THE_VENV_PYTHON=venv_python_stub
venv_python_stub() {{ echo "PIP_CALL:$*" >> '{marker}'; return 0; }}
_resolve_vendored_lib() {{
    local candidate="$PLUGIN_DIR/libs/$1"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        echo "$candidate"
        return 0
    fi
    return 1
}}
run_it() {{
    local PLUGIN_DIR='{plugin_dir}'
    local have_uv=1
    local upgrade=0
{block}
}}
run_it
"""
    proc = subprocess.run(
        [bash, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "HOME": str(tmp_path / "home")},
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    calls = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    uv_calls = [line for line in calls if line.startswith("UV_CALL:")]
    assert len(uv_calls) == 1, uv_calls
    assert "agent-procutil" in uv_calls[0]
