"""POSIX regression coverage for install.sh's build-artifact scrub (before AND
after an install attempt, not just after).

Installing FROM the pristine payload directory (``$PLUGIN_DIR``, under
``~/.copilot/installed-plugins/``) leaves setuptools' own ``build/lib`` +
``*.egg-info`` staging behind IN that tree. Left in place, a stale
``build/lib/`` can silently shadow fresh ``src/`` on a later install if
setuptools' incremental-build mtime check decides nothing "changed" -- the
exact failure mode that crashed agent-bridge's deployed daemon in a restart
loop (the downstream tracker): a since-added function existed only in
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
import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_ENGINE_SH = _PLUGIN_ROOT.parents[1] / "libs" / "installer-engine" / "installer-engine.sh"
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

def _owned_plugin_fixture(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    plugin_dir = repository / "plugins" / "agent-bridge"
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True)
    # Minimal release-shaped projects avoid copying dev-only editable paths.
    # Trust comes from the helper's publisher/roster graph, not install inputs.
    manifest = {
        "name": "agent-bridge",
        "version": "1.0.0",
        "repository": "https://github.com/ThomasMichon/copilot-extensions",
    }
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    roster = repository / ".github" / "plugin" / "marketplace.json"
    roster.parent.mkdir(parents=True)
    roster.write_text(json.dumps({
        "name": "copilot-extensions",
        "plugins": [{
            "name": manifest["name"], "version": manifest["version"],
            "source": "plugins/agent-bridge",
        }],
    }), encoding="utf-8")
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-bridge"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager", "agent-procutil", "agent-dropin-registry"]\n'
        '[tool.uv.sources]\n'
        'ssh-manager = { path = "../../libs/ssh-manager" }\n'
        'agent-procutil = { path = "libs/agent-procutil" }\n'
        'agent-dropin-registry = { path = "libs/dropin-registry" }\n',
        encoding="utf-8",
    )
    for name, source in (
        ("ssh-manager", repository / "libs" / "ssh-manager"),
        ("agent-procutil", plugin_dir / "libs" / "agent-procutil"),
        ("agent-dropin-registry", plugin_dir / "libs" / "dropin-registry"),
    ):
        source.mkdir(parents=True)
        (source / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "1.0.0"\n',
            encoding="utf-8",
        )
    shutil.copyfile(
        _ENGINE_SH.with_name("installer_dependency_policy.py"),
        scripts / "installer_dependency_policy.py",
    )
    shutil.copyfile(
        _ENGINE_SH.with_name("installer_dependency_config.py"),
        scripts / "installer_dependency_config.py",
    )
    return plugin_dir


def _extract_sh_functions(source: Path, *names: str) -> str:
    text = source.read_text(encoding="utf-8")
    chunks = []
    for name in names:
        start = text.index(f"{name}()")
        end = text.index("\n}\n", start)
        chunks.append(text[start : end + 2])
    return "\n\n".join(chunks)


def _run_harness(
    plugin_dir: Path, uv_stub_body: str, extra_script: str
) -> subprocess.CompletedProcess:
    if _BASH is None or os.name == "nt":
        pytest.skip("a POSIX bash environment is not available")
    fake_bin = plugin_dir / "bin"
    fake_bin.mkdir()
    # Shell functions are invisible to the policy's UV subprocess; keep the
    # physical settings probe separate from install residue/counter events.
    uv = fake_bin / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        "for arg do\n"
        '    if [ "$arg" = "--show-settings" ]; then\n'
        "        cat <<'SETTINGS'\n"
        "PipInstallSettings { settings: PipSettings {\n"
        "    index_locations: IndexLocations { indexes: [], flat_index: [], },\n"
        "    exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },\n"
        "}, }\n"
        "SETTINGS\n"
        "        exit 0\n"
        "    fi\n"
        "done\n"
        + f"{shlex.quote(sys.executable)} -c "
        + shlex.quote(
            "import datetime, sys; args = sys.argv[1:]; "
            "assert args[:2] == ['pip', 'install']; "
            "assert '--quiet' in args and '--reinstall' in args; "
            "cutoff = datetime.datetime.fromisoformat(args[args.index('--exclude-newer') + 1].replace('Z', '+00:00')); "
            "assert cutoff <= datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)"
        )
        + ' "$@" || exit 99\n'
        + uv_stub_body
        + '\nuv "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    policy = plugin_dir / "scripts" / "installer_dependency_policy.py"
    if not policy.is_file():
        policy = _ENGINE_SH.with_name("installer_dependency_policy.py")
    harness = plugin_dir / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f'PLUGIN_DIR="{plugin_dir}"\n'
        f"INSTALLER_ENGINE_DEPENDENCY_POLICY={shlex.quote(str(policy))}\n"
        + _extract_sh_functions(
            _ENGINE_SH,
            "test_is_sre_module_mismatch",
            "invoke_uv_pip_install_resilient",
        )
        + "\n\n"
        + _extract_sh_functions(
            _INSTALL_SH,
            "_scrub_payload_build_artifacts",
            "_uv_pip_install_resilient",
        )
        + """
_warn() { echo "WARN: $*" >&2; }
sleep() { :; }

"""
        + extra_script.replace("fake-python", shlex.quote(sys.executable))
        + "\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    return subprocess.run(
        [_BASH, str(harness)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"},
        cwd=plugin_dir,
        timeout=20,
        check=True,
    )


def _seed_build_residue(plugin_dir: Path) -> None:
    (plugin_dir / "build" / "lib" / "some_pkg").mkdir(parents=True)
    (plugin_dir / "build" / "lib" / "some_pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (plugin_dir / "some_pkg.egg-info").mkdir()
    (plugin_dir / "some_pkg.egg-info" / "PKG-INFO").write_text("stub\n", encoding="utf-8")


def test_fixture_local_sources_have_canonical_ownership(tmp_path: Path) -> None:
    plugin_dir = _owned_plugin_fixture(tmp_path)
    helper = plugin_dir / "scripts" / "installer_dependency_policy.py"
    spec = importlib.util.spec_from_file_location("fixture_dependency_policy", helper)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    roots = policy._anchor_roots()
    expected = {
        plugin_dir.resolve(),
        (plugin_dir.parents[1] / "libs" / "ssh-manager").resolve(),
        (plugin_dir / "libs" / "agent-procutil").resolve(),
        (plugin_dir / "libs" / "dropin-registry").resolve(),
    }
    assert set(roots) == expected
    for source in expected:
        policy._check_requirement(str(source), tmp_path, roots, set())
    uv_script = tmp_path / "uv.py"
    uv_script.write_text(
        f"#!{sys.executable}\n"
        "import datetime, sys\n"
        "arguments = sys.argv[1:]\n"
        "assert arguments[:2] == ['pip', 'install']\n"
        "if '--show-settings' in arguments:\n"
        "    print('PipInstallSettings { settings: PipSettings {\\n"
        "    index_locations: IndexLocations { indexes: [], flat_index: [], },\\n"
        "    exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },\\n"
        "}, }')\n"
        "    sys.exit(0)\n"
        "assert '--reinstall' in arguments and '--quiet' in arguments\n"
        "cutoff = datetime.datetime.fromisoformat(arguments[arguments.index('--exclude-newer') + 1].replace('Z', '+00:00'))\n"
        "assert cutoff <= datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)\n"
        "print('INSTALL:0')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        uv = tmp_path / "uv.cmd"
        uv.write_text(
            f'@echo off\n"{sys.executable}" "{uv_script}" %*\n', encoding="utf-8"
        )
    else:
        uv = tmp_path / "uv"
        uv_script.rename(uv)
        uv.chmod(0o755)
    for source in expected:
        result = subprocess.run(
            [sys.executable, "-I", str(helper), "--uv", str(uv), "--",
             "--python", sys.executable, str(source), "--quiet"],
            cwd=plugin_dir, capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "INSTALL:0"
    unrelated = plugin_dir.parent / "unrelated-plugin"
    unrelated.mkdir()
    with pytest.raises(policy.DependencyPolicyError, match="outside"):
        policy._check_requirement(str(unrelated), tmp_path, roots, set())


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
    plugin_dir = _owned_plugin_fixture(tmp_path)
    vendored_dir = plugin_dir.parents[1] / "libs" / "ssh-manager"
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
    plugin_dir = _owned_plugin_fixture(tmp_path)
    unrelated = plugin_dir / "unrelated.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")
    untouched_plugin = plugin_dir.parent / "unrelated-plugin"
    untouched_plugin.mkdir()
    _seed_build_residue(untouched_plugin)
    vendored_dir = plugin_dir.parents[1] / "libs" / "ssh-manager"
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
    assert (untouched_plugin / "build" / "lib" / "some_pkg" / "mod.py").exists()
    assert (untouched_plugin / "some_pkg.egg-info" / "PKG-INFO").exists()


def test_transitively_resolved_lib_under_payload_libs_dir_is_also_scrubbed(
    tmp_path: Path,
) -> None:
    """Regression: the self-reintroducing JobHandle ImportError incident.

    agent-procutil, dropin-registry, plugin-activation, and plugin-resolve
    are never given a dedicated install call (and therefore never passed as
    the explicit ``scrub_dir`` argument) -- they are resolved TRANSITIVELY
    while installing agent-bridge itself, via agent-bridge's own
    ``[tool.uv.sources]`` workspace path deps. Each still lives under
    ``$PLUGIN_DIR/libs/<name>/`` as its own independent setuptools build
    root, and accumulates the identical stale build/egg-info residue. An
    explicit-scrub_dir-only implementation never reaches these four libs,
    so a stale ``libs/agent-procutil/build/lib`` silently re-shadows a
    fresh ``src/`` on every later install -- the exact failure that broke
    `agent-bridge create` (``ImportError: cannot import name 'JobHandle'``)
    repeatedly, self-reintroducing after every manual hot-patch. The scrub
    must reach every immediate child of ``libs/`` unconditionally, not only
    the libs an explicit scrub_dir call site happens to name."""
    plugin_dir = _owned_plugin_fixture(tmp_path)
    procutil_dir = plugin_dir / "libs" / "agent-procutil"
    _seed_build_residue(procutil_dir)
    _seed_src_layout_egg_info(procutil_dir)
    dropin_dir = plugin_dir / "libs" / "dropin-registry"
    _seed_build_residue(dropin_dir)
    # The stub `uv` asserts the residue is already gone by the time it's
    # invoked (not merely by the time the wrapper returns) -- proving the
    # scrub actually prevents the stale build/lib from shadowing THIS
    # install, the real incident, not just that cleanup happens afterwards.
    uv_stub = f"""
uv() {{
    if [ -e "{procutil_dir}/build" ] || [ -e "{procutil_dir}/some_pkg.egg-info" ] \\
        || [ -e "{procutil_dir}/src/some_pkg.egg-info" ] || [ -e "{dropin_dir}/build" ]; then
        echo 'residue still present at install time' >&2
        return 1
    fi
    echo 'Installed 1 package'
    return 0
}}
"""
    extra = """
if _uv_pip_install_resilient "" --python fake-python --reinstall-package agent-bridge \
        --reinstall-package agent-procutil --reinstall-package agent-dropin-registry \
        "$PLUGIN_DIR" --quiet; then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(plugin_dir, uv_stub, extra)
    assert "EXIT:0" in result.stdout
    assert "residue still present" not in result.stderr
    assert not (procutil_dir / "build").exists()
    assert not (procutil_dir / "some_pkg.egg-info").exists()
    assert not (procutil_dir / "src" / "some_pkg.egg-info").exists()
    assert not (dropin_dir / "build").exists()
