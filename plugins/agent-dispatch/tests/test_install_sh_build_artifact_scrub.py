"""POSIX regression coverage for install.sh's post-install payload scrub.

Installing FROM the pristine payload directory (``$PLUGIN_DIR``, under
``~/.copilot/installed-plugins/``) leaves setuptools' own ``build/lib`` +
``*.egg-info`` staging behind IN that tree. Left in place, a stale
``build/lib/`` can silently shadow fresh ``src/`` on a later install if
setuptools' incremental-build mtime check decides nothing "changed" -- the
exact failure mode that crashed agent-bridge's deployed daemon in a restart
loop (aperture-labs#7281/#7279): a since-added function existed only in
``src/``, never made it into the stale ``build/lib`` copy that got installed,
and importing it crashed the daemon on every startup attempt. agent-dispatch
shares the identical "install straight from $PLUGIN_DIR" pattern, so
``_pip_install`` gets the same fix.

``_pip_install`` must scrub ``$PLUGIN_DIR/build`` and
``$PLUGIN_DIR/*.egg-info`` after every install attempt (uv or plain-pip
branch, success or failure), and must still return the underlying install's
real exit code. It must also scrub ``$PLUGIN_DIR/src/*.egg-info`` -- the
src-layout egg-info location a bare root-level glob never reaches, which
shadowed a real upstream fix and broke a live deployment for an extended
period before being caught.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)


def _extract_function(name: str) -> str:
    """Extract one shell function's full text by brace-counting, tolerant of
    the function's own indentation (``_pip_install`` is nested inside a
    larger do_install-style function, unlike this repo's other top-level
    extracted helpers)."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index(f"{name}()")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {name!r}")


def _run_harness(
    plugin_dir: Path, uv_stub_body: str, extra_script: str, *, have_uv: str = "1"
) -> subprocess.CompletedProcess:
    harness = plugin_dir / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'PLUGIN_DIR="{plugin_dir}"\n'
        f'have_uv={have_uv}\n'
        'VENV_PYTHON="python3"\n'
        '_STALE_CACHE_REFRESH_PACKAGES=(agent-dispatch)\n'
        + _extract_function("_pip_install")
        + "\n\n"
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


def _seed_src_layout_egg_info(plugin_dir: Path) -> None:
    """A src-layout package's egg-info (``src/<pkg>.egg-info``) sits one
    level deeper than the root-level glob reaches -- the exact shadow that
    survived every cleanup pass and broke a live deployment: a stale
    ``src/agent_dispatch.egg-info`` shadowed
    ``src/agent_dispatch/registrar.py``'s real `no_pair` field with an older
    cached copy that predated it."""
    (plugin_dir / "src" / "some_pkg.egg-info").mkdir(parents=True)
    (plugin_dir / "src" / "some_pkg.egg-info" / "PKG-INFO").write_text("stub\n", encoding="utf-8")


def test_successful_uv_install_scrubs_build_and_egg_info(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    uv_stub = """
uv() { echo 'Installed 1 package'; return 0; }
"""
    extra = """
if _pip_install "$PLUGIN_DIR"; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()


def test_failed_uv_install_still_scrubs_and_reports_failure(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    uv_stub = """
uv() { echo 'error: network unreachable'; return 1; }
"""
    extra = """
if _pip_install "$PLUGIN_DIR"; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:1" in result.stdout
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()


def test_plain_pip_fallback_branch_also_scrubs(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    uv_stub = """
python3() {
    shift  # drop -m
    shift  # drop pip
    echo "python3 $*"
    return 0
}
"""
    extra = """
if _pip_install "$PLUGIN_DIR"; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra, have_uv="0")
    assert "EXIT:0" in result.stdout
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()


def test_scrub_is_a_harmless_noop_when_nothing_to_clean(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    uv_stub = """
uv() { echo 'Installed 1 package'; return 0; }
"""
    extra = """
if _pip_install "$PLUGIN_DIR"; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout


def test_src_layout_egg_info_is_also_scrubbed(tmp_path: Path) -> None:
    """Regression: a src-layout egg-info one level below $PLUGIN_DIR must
    be cleaned too, not just the root-level glob."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    _seed_src_layout_egg_info(plugin_dir)
    uv_stub = """
uv() { echo 'Installed 1 package'; return 0; }
"""
    extra = """
if _pip_install "$PLUGIN_DIR"; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()
    assert not (plugin_dir / "src" / "some_pkg.egg-info").exists()
