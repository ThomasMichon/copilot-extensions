"""POSIX regression coverage for install.sh's post-install payload scrub.

Installing FROM the pristine payload directory (``$PLUGIN_DIR``, under
``~/.copilot/installed-plugins/``) leaves setuptools' own ``build/lib`` +
``*.egg-info`` staging behind IN that tree. Left in place, a stale
``build/lib/`` can silently shadow fresh ``src/`` on a later install if
setuptools' incremental-build mtime check decides nothing "changed" -- the
exact failure mode that crashed agent-bridge's deployed daemon in a restart
loop (aperture-labs#7281/#7279): a since-added function existed only in
``src/``, never made it into the stale ``build/lib`` copy that got installed,
and importing it crashed the daemon on every startup attempt.

``_uv_pip_install_resilient`` must scrub ``$PLUGIN_DIR/build`` and
``$PLUGIN_DIR/*.egg-info`` after every successful install (first-try or after
a retry), and must NOT scrub (or need to) on a hard failure, since nothing
was installed to leave residue from that attempt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
# A bare shutil.which("bash") can resolve to a Windows App Execution Alias
# stub or the classic `C:\Windows\System32\bash.exe` WSL launcher (both
# invoke an actual WSL distro rather than running this script in the
# environment under test). Prefer the real Git Bash location when present;
# otherwise filter both known WSL-launcher locations out of PATH before
# falling back to shutil.which, so this never silently selects one.
_GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
def _resolve_bash() -> str | None:
    if _GIT_BASH.is_file():
        return str(_GIT_BASH)
    path = os.environ.get("PATH")
    if not path:
        return None
    filtered = os.pathsep.join(
        part for part in path.split(os.pathsep)
        if "windowsapps" not in part.lower()
        and part.rstrip("\\").lower() != r"c:\windows\system32"
    )
    return shutil.which("bash", path=filtered)
_BASH = _resolve_bash()

pytestmark = pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)


def _extract_sh_functions(*names: str) -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    chunks = []
    for name in names:
        start = text.index(f"{name}()")
        end = text.index("\n}\n", start)
        chunks.append(text[start : end + 2])
    return "\n\n".join(chunks)


def _run_harness(
    plugin_dir: Path, uv_stub_body: str, extra_script: str
) -> subprocess.CompletedProcess:
    harness = plugin_dir / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'PLUGIN_DIR="{plugin_dir}"\n'
        + _extract_sh_functions(
            "_is_sre_module_mismatch",
            "_scrub_payload_build_artifacts",
            "_uv_pip_install_resilient",
        )
        + """
_warn() { echo "WARN: $*" >&2; }

"""
        + uv_stub_body
        + "\n\n"
        + extra_script
        + "\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    return subprocess.run(
        [_BASH, str(harness)],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=20,
        check=True,
    )


def _seed_build_residue(plugin_dir: Path) -> None:
    (plugin_dir / "build" / "lib" / "some_pkg").mkdir(parents=True)
    (plugin_dir / "build" / "lib" / "some_pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (plugin_dir / "some_pkg.egg-info").mkdir()
    (plugin_dir / "some_pkg.egg-info" / "PKG-INFO").write_text("stub\n", encoding="utf-8")


def test_successful_install_scrubs_build_and_egg_info(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    uv_stub = """
uv() { echo 'Installed 1 package'; return 0; }
"""
    extra = """
if _uv_pip_install_resilient --python fake-python some-package --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()


def test_scrub_runs_after_a_successful_retry(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    if [ "$n" -lt 2 ]; then
        echo 'AssertionError: SRE module mismatch'
        return 1
    fi
    echo 'Installed 1 package'
    return 0
}}
sleep() {{ :; }}
"""
    extra = """
if _uv_pip_install_resilient --python fake-python some-package --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()


def test_hard_failure_leaves_no_crash_even_without_residue(tmp_path: Path) -> None:
    """The scrub is a no-op (never invoked, never errors) on a hard failure --
    this just proves the wrapper's failure path still works with the scrub
    helper defined alongside it (no accidental coupling)."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    uv_stub = """
uv() { echo 'error: network unreachable'; return 1; }
"""
    extra = """
if _uv_pip_install_resilient --python fake-python some-package --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:1" in result.stdout


def test_scrub_is_a_harmless_noop_when_nothing_to_clean(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    uv_stub = """
uv() { echo 'Installed 1 package'; return 0; }
"""
    extra = """
if _uv_pip_install_resilient --python fake-python some-package --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
