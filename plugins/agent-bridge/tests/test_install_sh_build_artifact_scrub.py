"""POSIX regression coverage for install.sh's build-artifact scrub (before AND
after an install attempt, not just after).

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
``$PLUGIN_DIR/*.egg-info`` (including the src-layout location,
``$PLUGIN_DIR/src/*.egg-info``) **before every attempt** (the first call and
every retry) so pre-existing residue can never shadow that attempt's own
build, and again **after every successful install** (first-try or after a
retry) so the payload directory stays pristine for the next install. A hard
failure (every attempt exhausted) still leaves the pre-attempt scrubs' effect
in place -- there is no residue left over from a call that never installed
anything -- but the wrapper does not scrub again itself on that path.
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
if _uv_pip_install_resilient "" --python fake-python some-package --quiet; then
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
if _uv_pip_install_resilient "" --python fake-python some-package --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()


def test_retry_rescrubs_residue_recreated_during_the_failed_attempt(tmp_path: Path) -> None:
    """Regression: the first attempt failing (or a concurrent installer
    racing the retry delay) can recreate build/egg-info residue -- the
    retry must see a clean directory too, not just the very first call."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    if [ "$n" -lt 2 ]; then
        # Simulate residue appearing during the failed first attempt.
        mkdir -p "{plugin_dir}/build/lib/some_pkg"
        mkdir -p "{plugin_dir}/some_pkg.egg-info"
        echo 'AssertionError: SRE module mismatch'
        return 1
    fi
    if [ -e "{plugin_dir}/build" ] || [ -e "{plugin_dir}/some_pkg.egg-info" ]; then
        echo 'residue still present at retry time' >&2
        return 1
    fi
    echo 'Installed 1 package'
    return 0
}}
sleep() {{ :; }}
"""
    extra = """
if _uv_pip_install_resilient "" --python fake-python some-package --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert "residue still present" not in result.stderr


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
if _uv_pip_install_resilient "" --python fake-python some-package --quiet; then
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
if _uv_pip_install_resilient "" --python fake-python some-package --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout


def _seed_src_layout_egg_info(plugin_dir: Path) -> None:
    """A src-layout package's egg-info (``src/<pkg>.egg-info``) sits one
    level deeper than the root-level glob reaches -- the same shadow that
    survived every cleanup pass and broke a live agent-dispatch deployment
    (copilot-extensions#3444) before being caught; agent-bridge is an
    equally src-layout package and equally vulnerable."""
    (plugin_dir / "src" / "some_pkg.egg-info").mkdir(parents=True)
    (plugin_dir / "src" / "some_pkg.egg-info" / "PKG-INFO").write_text("stub\n", encoding="utf-8")


def test_src_layout_egg_info_is_also_scrubbed(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    _seed_src_layout_egg_info(plugin_dir)
    uv_stub = """
uv() { echo 'Installed 1 package'; return 0; }
"""
    extra = """
if _uv_pip_install_resilient "" --python fake-python some-package --quiet; then
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


def test_preexisting_residue_is_gone_before_the_install_call_runs(tmp_path: Path) -> None:
    """Regression (2026-09-23, copilot-extensions#3444): an after-only scrub
    cleans up for the NEXT install but does nothing to stop stale
    build/lib/*.egg-info -- already sitting in $PLUGIN_DIR from an earlier
    attempt, a marketplace resync, or a concurrent process -- from shadowing
    THIS install's own build via setuptools' incremental-build mtime check.
    The stub `uv` below asserts the residue is already gone by the time
    it's invoked, not merely gone by the time the wrapper returns."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    _seed_src_layout_egg_info(plugin_dir)
    uv_stub = f"""
uv() {{
    if [ -e "{plugin_dir}/build" ] || [ -e "{plugin_dir}/some_pkg.egg-info" ] \\
        || [ -e "{plugin_dir}/src/some_pkg.egg-info" ]; then
        echo 'residue still present at install time' >&2
        return 1
    fi
    echo 'Installed 1 package'
    return 0
}}
"""
    extra = """
if _uv_pip_install_resilient "" --python fake-python some-package --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert "residue still present" not in result.stderr


def test_local_vendored_source_dir_is_also_scrubbed(tmp_path: Path) -> None:
    """Regression (copilot-extensions#3456 review): agent-bridge installs
    vendored dependencies (ssh-manager, credential-relay, zdd, ...) from
    their OWN local source trees, not from ``$PLUGIN_DIR``. Those trees use
    the same setuptools src-layout and accumulate the identical stale
    build/egg-info residue -- a ``$PLUGIN_DIR``-only scrub never reaches it,
    so passing the actual source dir must scrub that tree too."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    vendored_dir = tmp_path / "ssh-manager"
    vendored_dir.mkdir()
    _seed_build_residue(vendored_dir)
    _seed_src_layout_egg_info(vendored_dir)
    uv_stub = """
uv() { echo 'Installed 1 package'; return 0; }
"""
    extra = f"""
if _uv_pip_install_resilient "{vendored_dir}" --python fake-python "{vendored_dir}" --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (vendored_dir / "build").exists()
    assert not (vendored_dir / "some_pkg.egg-info").exists()
    assert not (vendored_dir / "src" / "some_pkg.egg-info").exists()


def test_local_vendored_source_dir_scrub_does_not_touch_unrelated_plugin_dir(
    tmp_path: Path,
) -> None:
    """The vendored-source scrub is additive, not a replacement: $PLUGIN_DIR
    residue unrelated to the vendored install must be left alone."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    unrelated = plugin_dir / "unrelated.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")
    vendored_dir = tmp_path / "ssh-manager"
    vendored_dir.mkdir()
    _seed_build_residue(vendored_dir)
    uv_stub = """
uv() { echo 'Installed 1 package'; return 0; }
"""
    extra = f"""
if _uv_pip_install_resilient "{vendored_dir}" --python fake-python "{vendored_dir}" --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (vendored_dir / "build").exists()
    assert unrelated.exists()
