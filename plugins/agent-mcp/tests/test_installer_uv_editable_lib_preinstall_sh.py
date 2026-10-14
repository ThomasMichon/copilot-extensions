"""Source-resolution guard for the canonical dependency-policy preinstall loop.

Both discovery branches must use the policy adapter and preserve library
resolution/order. The adapter spy does not exercise dependency-age admission.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "init.sh"

pytestmark = pytest.mark.guard

_LOOP_START = "for _lib_entry in \\\n    'credential-relay:agent-credential-relay'"
_LOOP_END = "\ndone\n\n# -- 3."
LIB_ORDER = ["credential-relay", "agent-procutil", "single-instance-lease", "zdd"]


def _extract_loop() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    return _LOOP_START + text.split(_LOOP_START, 1)[1].split(_LOOP_END, 1)[0] + "\ndone"


def _run(
    bash: str, plugin_dir: Path, have_uv: bool, *, marker: Path
) -> subprocess.CompletedProcess[str]:
    loop = _extract_loop()
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True)
    policy = scripts / "installer_dependency_policy.py"
    shutil.copyfile(INSTALLER.parent / policy.name, policy)
    shutil.copyfile(
        INSTALLER.parent / "installer_dependency_config.py",
        scripts / "installer_dependency_config.py",
    )
    script = f"""
set -euo pipefail
_fail() {{ echo "FAIL:$1"; exit 1; }}
uv() {{ _fail 'Preinstall bypassed canonical dependency policy'; }}
VENV_PYTHON=venv_python_stub
SCRIPT_DIR={shlex.quote(str(scripts))}
venv_python_stub() {{
    [[ "$#" -ge 9 && "$1" == -I &&
       "$2" == "$SCRIPT_DIR/installer_dependency_policy.py" && -f "$2" &&
       "$3" == --uv && "$4" == uv && "$5" == -- &&
       "$6" == --python && "$7" == "$VENV_PYTHON" ]] ||
        _fail 'Preinstall bypassed canonical dependency policy'
    if [[ "$HAVE_UV" == 1 ]]; then
        [[ "$#" == 11 && "$8" == --reinstall-package &&
           "${{9}}" == "$_lib_pkg_name" && "${{11}}" == --quiet ]] ||
            _fail 'Invalid policy reinstall arguments'
        _installed_path="${{10}}"
    else
        [[ "$#" == 9 && "$8" == --quiet ]] ||
            _fail 'Invalid policy arguments'
        _installed_path="${{9}}"
    fi
    printf 'POLICY_INSTALL:%s\\n' "$_installed_path" >> {shlex.quote(str(marker))}
}}
PLUGIN_DIR={shlex.quote(str(plugin_dir))}
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


def _installed_paths(marker: Path) -> list[str]:
    if not marker.exists():
        return []
    return [
        line.removeprefix("POLICY_INSTALL:")
        for line in marker.read_text(encoding="utf-8").splitlines()
        if line.startswith("POLICY_INSTALL:")
    ]


def _make_libs(root: Path) -> None:
    for lib in LIB_ORDER:
        lib_dir = root / lib
        lib_dir.mkdir(parents=True)
        (lib_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")


def test_preinstall_loop_requires_canonical_policy_in_both_branches():
    loop = _extract_loop()
    assert loop.count(
        '"$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" '
        '--uv uv -- --python "$VENV_PYTHON"'
    ) == 2
    assert "pip install" not in loop


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_resolves_plugin_local_copy(have_uv: bool, tmp_path: Path):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-mcp"
    _make_libs(plugin_dir / "libs")
    marker = tmp_path / "marker.txt"

    proc = _run(bash, plugin_dir, have_uv, marker=marker)
    assert proc.returncode == 0, proc.stderr

    installs = _installed_paths(marker)
    assert len(installs) == 4
    assert [Path(path).name for path in installs] == LIB_ORDER
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(plugin_dir / "libs")))


@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_loop_falls_back_to_repo_root_canonical_when_absent(
    have_uv: bool, tmp_path: Path
):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")

    plugin_dir = tmp_path / "plugins" / "agent-mcp"
    plugin_dir.mkdir(parents=True)
    canonical = tmp_path / "libs"
    _make_libs(canonical)
    marker = tmp_path / "marker.txt"

    proc = _run(bash, plugin_dir, have_uv, marker=marker)
    assert proc.returncode == 0, proc.stderr

    installs = _installed_paths(marker)
    assert len(installs) == 4
    assert [Path(path).name for path in installs] == LIB_ORDER
    for resolved in installs:
        assert os.path.realpath(resolved).startswith(os.path.realpath(str(canonical)))
