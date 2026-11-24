"""Native POSIX management verifies generation identity, not unit activity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
import venv

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="native POSIX service management")


def _function(name: str) -> str:
    source = (PLUGIN / "scripts/install.sh").read_text(encoding="utf-8")
    opening, closing = (r"\(", r"\)") if name == "_active_vault_python" else (r"\{", r"\}")
    match = re.search(rf"^{name}\(\) {opening}.*?^{closing}", source, re.M | re.S)
    assert match is not None, name
    return match.group()


@pytest.mark.parametrize(
    "before,after,manager,expected,starts",
    [(0, 0, 0, 0, 0), (1, 0, 0, 0, 1), (1, 1, 0, 1, 1),
     (2, 0, 0, 1, 0), (3, 0, 0, 1, 0), (1, 0, 7, 7, 1)],
)
def test_posix_start_does_not_certify_an_old_active_unit(
    tmp_path: Path, before: int, after: int, manager: int, expected: int, starts: int,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    units = tmp_path / "units"
    units.mkdir()
    (units / "fixture.service").write_text("retained unit", encoding="utf-8")
    calls = tmp_path / "calls"
    script = tmp_path / "start.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"UNIT_DIR={shlex.quote(str(units))}; SYSTEMD_UNIT=fixture.service\n"
        f"CALLS={shlex.quote(str(calls))}; checks=0\n"
        "_active_vault_python() { printf '%s\\n' selected-python; }\n"
        f"systemctl() {{ printf '%s\\n' \"$*\" >> \"$CALLS\"; return {manager}; }}\n"
        "_vault_selected_generation() {\n"
        " [[ \"$1\" == selected-python ]] || return 9\n"
        " checks=$((checks+1)); if [[ \"$checks\" == 1 ]]; then\n"
        f"  [[ \"$2\" == ready && \"$3\" == 0 ]] || return 9; return {before}; fi\n"
        " [[ \"$2\" == ready ]] || return 9\n"
        f" return {after}\n"
        "}\n_install_service() {\n"
        " [[ \"$LINK_PYTHON\" == selected-python && \"$NO_SERVICE\" == 0 ]] || return 9\n"
        " systemctl --user restart \"$SYSTEMD_UNIT\" || return \"$?\"\n"
        " _vault_selected_generation \"$LINK_PYTHON\" ready\n"
        "}\n_fail() { printf '%s\\n' \"$1\" >&2; }\n_ok() { printf '%s\\n' \"$1\"; }\n"
        + _function("do_start") + "\ndo_start\n",
        encoding="utf-8",
    )
    result = subprocess.run([bash, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == expected, result.stdout + result.stderr
    observed = calls.read_text().splitlines() if calls.exists() else []
    assert observed == ["--user restart fixture.service"] * starts
    assert (units / "fixture.service").read_text() == "retained unit"
    if expected:
        assert "generation is ready" not in result.stdout


def test_posix_management_resolver_is_read_only_and_caller_scoped(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    resolver = root / "bin/resolve-runtime.sh"
    resolver.write_text(
        '[[ "$AGENT_RT_RESOLVE_TRACE_MODE" == read-only ]] || return 9\n'
        "AGENT_RT_PY=selected-python\n", encoding="utf-8",
    )
    script = tmp_path / "resolve.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"INSTALL_DIR={shlex.quote(str(root))}; LINK_PYTHON=unbuilt-python\n"
        "AGENT_RT_ROOT=retained-root; AGENT_RT_RESOLVE_TRACE_MODE=retained-mode\n"
        + _function("_active_vault_python")
        + '\nselected="$(_active_vault_python)"\n'
        '[[ "$selected" == selected-python && "$AGENT_RT_ROOT" == retained-root'
        ' && "$AGENT_RT_RESOLVE_TRACE_MODE" == retained-mode ]]\n',
        encoding="utf-8",
    )
    before = resolver.read_bytes()
    result = subprocess.run([bash, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert resolver.read_bytes() == before


def test_posix_replacement_stays_under_the_unit_stop_lifecycle(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    units = tmp_path / "units"
    units.mkdir()
    (units / "fixture.service").write_text("retained unit", encoding="utf-8")
    calls = tmp_path / "calls"
    script = tmp_path / "lifecycle.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"UNIT_DIR={shlex.quote(str(units))}; SYSTEMD_UNIT=fixture.service\n"
        f"CALLS={shlex.quote(str(calls))}; managed=older\n"
        "_active_vault_python() { printf '%s\\n' selected-python; }\n"
        "systemctl() {\n"
        " printf '%s\\n' \"$*\" >> \"$CALLS\"\n"
        " case \"$2\" in restart) managed=current;; stop) managed=absent;;\n"
        " is-active) [[ \"$managed\" != absent ]];; *) return 9;; esac\n"
        "}\n"
        "_vault_selected_generation() {\n"
        " [[ \"$1\" == selected-python && \"$2\" == ready ]] || return 9\n"
        " [[ \"$managed\" == current ]]\n"
        "}\n"
        "_install_service() {\n"
        " [[ \"$LINK_PYTHON\" == selected-python && \"$NO_SERVICE\" == 0 ]] || return 9\n"
        " systemctl --user restart \"$SYSTEMD_UNIT\"\n"
        " _vault_selected_generation \"$LINK_PYTHON\" ready\n"
        "}\n"
        "_fail() { printf '%s\\n' \"$1\" >&2; }\n_ok() { :; }; _skip() { :; }\n"
        + _function("do_start") + "\n" + _function("do_stop")
        + "\ndo_start\n[[ \"$managed\" == current ]]\ndo_stop\n[[ \"$managed\" == absent ]]\n",
        encoding="utf-8",
    )
    result = subprocess.run([bash, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text().splitlines() == [
        "--user restart fixture.service", "--user is-active fixture.service", "--user stop fixture.service",
    ]


def test_posix_start_resolves_and_publishes_only_after_an_overlapping_update(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None and shutil.which("flock") is not None
    source = (PLUGIN / "scripts/install.sh").read_text(encoding="utf-8")
    lock_start = source.index("# === install-transaction lock (fail-closed) ===")
    lock_end = source.index("# === end install-transaction lock ===", lock_start)
    lock = source[lock_start:lock_end]
    admission_start = source.index('case "$ACTION:${DRY_RUN:-0}" in')
    admission = source[admission_start:source.index('\ncase "$ACTION" in', admission_start)]
    root = tmp_path / "runtime"
    root.mkdir()
    marker = root / "current-version"
    marker.write_text("older", encoding="utf-8")
    unit = root / "fixture.service"
    unit.write_text("older", encoding="utf-8")
    common = (
        "set -euo pipefail\n"
        f"INSTALL_DIR={shlex.quote(str(root))}; UNIT_DIR=\"$INSTALL_DIR\"; SYSTEMD_UNIT=fixture.service\n"
        f"FIXTURE={shlex.quote(str(tmp_path))}; COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC=10\n"
        "_fail() { printf '%s\\n' \"$1\" >&2; }\n_ok() { :; }\nsystemctl() { return 9; }\n" + lock + "\n"
    )
    updater = tmp_path / "update.sh"
    updater.write_text(
        common + "ACTION=update\n" + admission
        + '\n: > "$FIXTURE/update-held"\n'
        'deadline=$((SECONDS+8))\n'
        'while [[ ! -f "$FIXTURE/release-update" ]]; do\n'
        ' [[ "$SECONDS" -lt "$deadline" ]] || exit 9; sleep 0.01\n'
        'done\nprintf newer > "$INSTALL_DIR/current-version"\n',
        encoding="utf-8",
    )
    starter = tmp_path / "start.sh"
    starter.write_text(
        common + "ACTION=start\n"
        'flock() { : > "$FIXTURE/start-waiting"; command flock "$@"; }\n'
        + admission + "\n"
        '_active_vault_python() { : > "$FIXTURE/start-resolved"; cat "$INSTALL_DIR/current-version"; }\n'
        '_vault_selected_generation() { return 1; }\n'
        '_install_service() { printf "%s" "$LINK_PYTHON" > "$UNIT_DIR/$SYSTEMD_UNIT"; }\n'
        + _function("do_start") + "\ndo_start\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("COPILOT_EXT_NO_FLOCK", None)
    children: list[subprocess.Popen[str]] = []

    def await_file(path: Path) -> None:
        deadline = time.monotonic() + 5
        while not path.exists():
            assert all(child.poll() is None for child in children), "transaction exited before admission"
            assert time.monotonic() < deadline, "transaction did not reach its admission boundary"
            time.sleep(0.01)

    try:
        children.append(subprocess.Popen(
            [bash, str(updater)], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ))
        await_file(tmp_path / "update-held")
        children.append(subprocess.Popen(
            [bash, str(starter)], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ))
        await_file(tmp_path / "start-waiting")
        assert not (tmp_path / "start-resolved").exists()
        assert unit.read_text() == "older"
        (tmp_path / "release-update").touch()
        for child in children:
            stdout, stderr = child.communicate(timeout=12)
            assert child.returncode == 0, stdout + stderr
        assert (tmp_path / "start-resolved").exists()
        assert marker.read_text() == unit.read_text() == "newer"
    finally:
        (tmp_path / "release-update").touch(exist_ok=True)
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=5)


@pytest.mark.parametrize(
    "mode,ready,selected,expected,staged",
    [("ready", False, True, 1, False), ("ready", True, True, 0, False),
     ("ready", True, False, 3, False), ("ensure", True, True, 0, False),
     ("ensure", True, True, 0, True)],
)
def test_posix_generation_probe_uses_the_selected_isolated_runtime(
    tmp_path: Path, mode: str, ready: bool, selected: bool, expected: int, staged: bool,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    root = tmp_path / "runtime"
    runtime = root / "versions/current"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / "bin/python"
    site = next((runtime / "lib").glob("python*/site-packages"))
    package = site / "agent_vault"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    report = tmp_path / "observed.json"
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent_vault.py").write_text("raise AssertionError('cwd import')\n", encoding="utf-8")
    (package / "configuration.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def service_configuration_snapshot(project):\n"
        " path=Path(os.environ.get('AGENT_VAULT_CONFIG',project+'/native.json')).absolute()\n"
        " return {'root':project,'config':str(path),'native_default_owner':'1'}\n",
        encoding="utf-8",
    )
    (package / "config.py").write_text(
        "import json,os,pathlib,sys\n"
        "def bind_service_configuration(root, *, native_default=False):\n"
        f" pathlib.Path({str(report)!r}).write_text(json.dumps({{"
        "'root':root,'native':native_default,'prefix':sys.prefix,"
        "'home':os.environ.get('AGENT_VAULT_HOME'),"
        "'runtime':os.environ.get('AGENT_RT_ROOT'),"
        "'cwd':os.getcwd(),'staged':os.environ.get('COPILOT_PLUGIN_STAGED_FROM'),"
        "'config':os.environ.get('AGENT_VAULT_CONFIG'),"
        "'port':os.environ.get('AGENT_VAULT_PORT')}))\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text("def run_dir(): return 'fixture'\n", encoding="utf-8")
    (package / "service_generation.py").write_text(
        "def read_endpoint(root): return root\n"
        f"def selected(): return {selected!r}\n"
        f"def endpoint_health(endpoint): return {{'accepting_work':{ready!r}}}\n"
        f"def same_generation(status): return {ready!r}\n"
        f"def ensure_selected(timeout): return {ready!r}\n",
        encoding="utf-8",
    )
    suffix = (
        "set -euo pipefail\n"
        f"INSTALL_DIR={shlex.quote(str(root))}; RUN_DIR=\"$INSTALL_DIR/run\"\n"
        "SOCKET_PATH=fixture; PIPE_PATH=fixture; PID_FILE=fixture; LOG_FILE=fixture\n"
        "SERVICE_SUFFIX=fixture; INSTALLATION_ID=fixture\n"
        f"COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT={shlex.quote(str(project))}\n"
        + _function("_vault_selected_generation")
        + f"\n_vault_selected_generation {shlex.quote(str(python))} {mode} 0\n"
    )
    script = tmp_path / "probe.sh"
    home = tmp_path / "home"
    home.mkdir()
    payload = home / ".copilot/installed-plugins/example/agent-vault"
    if staged:
        scripts = payload / "scripts"
        scripts.mkdir(parents=True)
        (payload / "plugin.json").write_text('{"name":"agent-vault"}', encoding="utf-8")
        (project / "native.json").write_text('{"enabled":true}', encoding="utf-8")
        (scripts / "runtime-activation.sh").write_text(
            "vault_runtime_activation() {\n"
            ' "$FIXTURE_PYTHON" -I -c \'import json,os; '
            'assert json.load(open(os.environ["AGENT_VAULT_CONFIG"]))["enabled"] is True\'\n'
            "}\n", encoding="utf-8",
        )
        source = (PLUGIN / "scripts/install.sh").read_text(encoding="utf-8")
        prefix = source[:source.index("# === install-contract:v4 smoke seam")]
        prefix = prefix.replace('. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"', ":")
        script = scripts / "install.sh"
        script.write_text(prefix + suffix, encoding="utf-8")
    else:
        script.write_text(suffix, encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    for name in (
        "AGENT_VAULT_CONFIG", "COPILOT_PLUGIN_INSTALL_STAGED", "COPILOT_PLUGIN_STAGED_FROM",
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
    ):
        environment.pop(name, None)
    environment["PYTHONPATH"] = str(project)
    environment["AGENT_RT_ROOT"] = str(project)
    if staged:
        environment.update({
            "HOME": str(home), "AGENT_VAULT_CONFIG": "native.json", "FIXTURE_PYTHON": str(python),
        })
    result = subprocess.run(
        [bash, str(script), "start"], cwd=project, env=environment,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert json.loads(report.read_text()) == {
        "root": str(project), "native": True, "prefix": str(runtime), "home": str(root),
        "config": str(project / "native.json"), "port": "0", "runtime": str(root),
        "cwd": str(home if staged else project), "staged": str(payload) if staged else None,
    }
