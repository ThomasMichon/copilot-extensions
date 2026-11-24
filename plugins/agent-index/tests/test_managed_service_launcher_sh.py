"""Fixture-owned systemd ingress; no registration, engine or host-state effects."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
import venv

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux systemd ingress")
Ingress = tuple[Path, Path, Path, Path, dict[str, str]]


def _function(name: str) -> str:
    text = (PLUGIN / "scripts/install.sh").read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 2]


@pytest.fixture
def ingress(tmp_path: Path) -> Ingress:
    root = tmp_path / "index 'quoted' $literal 20% \\slash "
    engine = tmp_path / "independent engine"
    tools = tmp_path / "tools"
    runtime_tools = tmp_path / "runtime-tools"
    temp = tmp_path / "selector-temp"
    for path in (root, engine, tools, runtime_tools, temp):
        path.mkdir()
    for name in ("bash", "dirname", "cat", "tr", "awk", "date", "sed", "grep", "sort",
                 "cut", "cp", "mkdir", "chmod", "stat", "sha256sum", "mktemp", "mv",
                 "rmdir", "readlink", "uname", "mkfifo", "setsid", "sleep", "rm"):
        binary = shutil.which(name)
        assert binary, name
        (tools / name).symlink_to(binary)
        (runtime_tools / name).symlink_to(binary)
    for name in ("uv", "python", "python3"):
        poison = runtime_tools / name
        poison.write_text("#!/bin/bash\nprintf 'AMBIENT_TOOL_RAN\\n' >&2\nexit 97\n")
        poison.chmod(0o755)
    uv = tools / "uv"
    uv.write_text(
        "#!/bin/bash\n"
        '[[ "${1:-}" != --version ]] || { printf "uv 0.13.7\\n"; exit 0; }\n'
        'printf "%s\\n" "$@" > "$UV_CALLS"\n'
        'for key in UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON '
        'VIRTUAL_ENV CONDA_PREFIX PYTHONHOME PYTHONPATH UV_WORKING_DIR UV_WORKING_DIRECTORY '
        'UV_ISOLATED UV_ENV_FILE UV_PROJECT UV_FROZEN UV_LOCKED UV_NO_SYNC UV_CONFIG_FILE; do\n'
        ' [[ ! -v "$key" ]] || exit 91\n'
        'done\n[[ "$UV_NO_CONFIG" == 1 ]] || exit 92\n'
        'case "${SELECTOR_MODE:-selected}" in\n'
        ' denied) printf "policy denied (os error 448)\\n" >&2; exit 93;;\n'
        ' wrong) printf "/foreign/python\\n"; exit 0;;\n'
        ' warning) printf "selector warning\\n" >&2;;\n'
        'esac\nprintf "%s\\n" "${@: -1}"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    service_env = "AGENT_INDEX_PORT=19420\n"
    engine_env = "AGENT_INDEX_ENGINE_PORT=19421\n"
    (root / "service.env").write_text(service_env, encoding="utf-8")
    (engine / "engine.env").write_text(engine_env, encoding="utf-8")
    legacy = _slot(engine, "legacy", legacy=True)
    script = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(REPO / 'libs/installer-engine/installer-engine.sh'))}\n"
        "_RUNTIME_PUBLICATION_EVIDENCE=''\n"
        f"SCRIPT_DIR={shlex.quote(str(PLUGIN / 'scripts'))}\n"
        f"INSTALL_DIR={shlex.quote(str(root))}\n"
        f"ENGINE_HOME={shlex.quote(str(engine))}\n"
        f"ENGINE_VENV_PYTHON={shlex.quote(str(legacy))}\n"
        f"UNIT_DIR={shlex.quote(str(tmp_path / 'units'))}\n"
        'ENV_FILE="$INSTALL_DIR/service.env"; SERVICE_LAUNCHER="$INSTALL_DIR/service.sh"\n'
        'ENGINE_ENV_FILE="$ENGINE_HOME/engine.env"; ENGINE_LAUNCHER="$ENGINE_HOME/engine.sh"\n'
        "SYSTEMD_UNIT=agent-index.service; ENGINE_SYSTEMD_UNIT=agent-index-engine.service\n"
        "NO_SERVICE=0\n"
        '_ok() { :; }; _skip() { :; }; _warn() { :; }; _fail() { echo "$*" >&2; }\n'
        'systemctl() { printf "%s\\n" "$*" >> "$CALLS"; }\n'
        + "\n".join(_function(name) for name in (
            "_write_native_service_launcher", "_write_service_files", "_write_engine_files",
            "_install_service", "_register_engine_daemon",
        ))
        + '\nNO_SERVICE="${FIXTURE_NO_SERVICE:-0}"\n'
        + '_install_service --no-restart\n_register_engine_daemon\n'
    )
    publish = tmp_path / "publish.sh"
    publish.write_text(script, encoding="utf-8")
    environment = {
        **os.environ, "PATH": str(tools), "CALLS": str(tmp_path / "systemctl-calls"),
        "UV_CALLS": str(tmp_path / "uv-calls"), "TMPDIR": str(temp),
        "UV_PYTHON": "poison", "UV_CONFIG_FILE": "relative.toml", "UV_NO_CONFIG": "false",
        "VIRTUAL_ENV": "poison", "CONDA_PREFIX": "poison",
        "PYTHONHOME": "poison", "PYTHONPATH": "poison",
        "AGENT_RT_ROOT": "/foreign/root",
    }
    result = subprocess.run(
        ["/bin/bash", str(publish)], capture_output=True, text=True,
        env=environment, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(environment["CALLS"]).read_text().splitlines() == [
        "--user daemon-reload", "--user enable agent-index.service",
        "--user daemon-reload", "--user enable agent-index-engine.service",
        "--user is-active agent-index-engine.service",
    ]
    assert (root / "service.env").read_text() == service_env
    assert (engine / "engine.env").read_text() == engine_env
    environment["PATH"] = str(runtime_tools)
    return root, engine, uv, publish, environment


def _slot(root: Path, version: str, *, legacy: bool = False, nested: bool = False) -> Path:
    slot = root / ".venv" if legacy else root / "versions" / version
    environment = slot / ".venv" if nested else slot
    venv.EnvBuilder(with_pip=False).create(environment)
    site = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    package = site / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'pid': os.getpid(), 'runtime': sys.prefix, 'args': sys.argv[1:],"
        " 'cwd': os.getcwd(), 'isolated': sys.flags.isolated,"
        " 'bytecode': sys.dont_write_bytecode, 'port': os.environ.get('AGENT_INDEX_PORT'),"
        " 'engine_port': os.environ.get('AGENT_INDEX_ENGINE_PORT'),"
        " 'engine_home': os.environ.get('AGENT_INDEX_ENGINE_HOME')}))\n"
        "print('application stderr', file=sys.stderr)\nraise SystemExit(23)\n",
        encoding="utf-8",
    )
    if not legacy:
        (slot / ".install-complete.json").write_text(
            json.dumps({"version": version, "completed_at": "fixture", "pid": 0}) + "\n",
            encoding="utf-8",
        )
    return environment / "bin/python"


def _launch(
    root: Path, mode: str, environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    name = "agent-index" if mode == "service" else "agent-index-engine"
    unit = root.parent / "units" / f"{name}.service"
    values = dict(line.split("=", 1) for line in unit.read_text().splitlines() if "=" in line)
    directory = Path(values["WorkingDirectory"].replace("%%", "%"))
    assert directory == root
    result = subprocess.run(
        ["/bin/bash", "-c", 'printf "%s\\n" "$$"; exec "$@"', "fixture",
         *shlex.split(values["ExecStart"]), "two words", ""],
        capture_output=True, text=True, env=environment, timeout=20, cwd=directory,
    )
    assert not list(Path(environment["TMPDIR"]).iterdir())
    return result


@pytest.mark.parametrize("mode,selection", [
    ("service", "current"), ("service", "last-known-good"), ("service", "newest"),
    ("engine", "current"), ("engine", "last-known-good"), ("engine", "legacy"),
])
def test_native_launch_qualifies_selected_runtime_and_preserves_exec(
    ingress: Ingress, mode: str, selection: str,
) -> None:
    service, engine, _uv, _publish, environment = ingress
    root = service if mode == "service" else engine
    if selection == "legacy":
        python = root / ".venv/bin/python"
    else:
        python = _slot(root, "1.0.0", nested=mode == "service")
        if selection == "current":
            (root / "current-version").write_text("1.0.0\n")
        elif selection == "last-known-good":
            (root / "current-version").write_text("absent\n")
            (root / "last-known-good").write_text("1.0.0\n")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    for _cycle in range(2):
        result = _launch(root, mode, environment)
        assert result.returncode == 23, result.stdout + result.stderr
        original_pid, output = result.stdout.splitlines()
        observed = json.loads(output)
        assert observed["pid"] == int(original_pid)
        assert observed["runtime"] == str(python.parent.parent)
        assert observed["cwd"] == str(root)
        assert observed["args"] == (
            ["start"] if mode == "service" else ["engine", "run"]
        ) + ["two words", ""]
        assert observed["isolated"] == 1 and observed["bytecode"] is True
        assert observed["engine_port" if mode == "engine" else "port"] == (
            "19421" if mode == "engine" else "19420"
        )
        if mode == "engine":
            assert observed["engine_home"] == str(root)
        assert result.stderr == "application stderr\n"
        assert Path(environment["UV_CALLS"]).read_text().splitlines() == [
            "python", "find", "--no-project", "--managed-python", "--no-python-downloads",
            str(python),
        ]
        assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("mode", ["service", "engine"])
@pytest.mark.parametrize("failure", ["absent-runtime", "denied", "wrong", "missing-uv", "warning"])
def test_selector_failures_never_fall_back_to_ambient_tools(
    ingress: Ingress, mode: str, failure: str,
) -> None:
    service, engine, uv, _publish, environment = ingress
    root = service if mode == "service" else engine
    if failure != "absent-runtime":
        _slot(root, "1.0.0")
    (root / "current-version").write_text("1.0.0\n")
    if failure == "missing-uv":
        uv.unlink()
    environment["SELECTOR_MODE"] = failure
    result = _launch(root, mode, environment)
    assert result.returncode == {
        "absent-runtime": 126, "denied": 93, "wrong": 1, "missing-uv": 127, "warning": 23,
    }[failure]
    assert "AMBIENT_TOOL_RAN" not in result.stderr
    assert (len(result.stdout.splitlines()) == 2) is (failure == "warning")
    if failure == "warning":
        assert result.stderr == "selector warning\napplication stderr\n"


@pytest.mark.parametrize("mode", ["service", "engine"])
def test_real_prepared_uv_runs_offline_without_ambient_python(
    ingress: Ingress, mode: str, record_property: Callable[[str, object], None],
) -> None:
    actual_uv = shutil.which("uv")
    assert actual_uv
    service, engine, uv, _publish, environment = ingress
    uv.unlink()
    uv.symlink_to(actual_uv)
    root = service if mode == "service" else engine
    python = _slot(root, "1.0.0")
    (root / "current-version").write_text("1.0.0\n")
    environment["UV_OFFLINE"] = "1"
    environment["UV_CACHE_DIR"] = str(root.parent / "private-uv-cache")
    samples = []
    for _cycle in range(2):
        started = time.monotonic()
        result = _launch(root, mode, environment)
        samples.append(time.monotonic() - started)
        assert result.returncode == 23, result.stdout + result.stderr
        assert json.loads(result.stdout.splitlines()[1])["runtime"] == str(python.parent.parent)
        assert result.stderr == "application stderr\n"
    record_property("index_service_ingress", json.dumps({
        "mode": mode, "cycles": 2, "samples_seconds": samples,
        "runtime": "managed-venv", "network": "offline",
    }))


def test_generated_units_parse_literal_roots_without_starting_services(ingress: Ingress) -> None:
    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd static parser unavailable")
    root, _engine, _uv, _publish, _environment = ingress
    for unit in (root.parent / "units").glob("*.service"):
        result = subprocess.run(
            [analyzer, "verify", "--man=no", str(unit)],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "SYSTEMD_LOG_LEVEL": "debug"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        directory = root if unit.stem == "agent-index" else root.parent / "independent engine"
        assert f"WorkingDirectory: {directory}" in result.stdout + result.stderr


def test_no_service_keeps_registration_and_launcher_publication_inert(ingress: Ingress) -> None:
    service, engine, uv, publish, environment = ingress
    before = {path: path.read_bytes() for root in (service, engine) for path in root.rglob("*")
              if path.is_file()}
    calls = Path(environment["CALLS"])
    previous_calls = calls.read_bytes()
    result = subprocess.run(
        ["/bin/bash", str(publish)], capture_output=True, text=True, timeout=15,
        env={**environment, "FIXTURE_NO_SERVICE": "1", "PATH": str(uv.parent)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_bytes() == previous_calls
    assert {path: path.read_bytes() for root in (service, engine) for path in root.rglob("*")
            if path.is_file()} == before


@pytest.mark.parametrize("mode", ["service", "engine", "missing"])
def test_publication_finds_private_uv_without_path_uv(ingress: Ingress, mode: str) -> None:
    service, engine, uv, publish, environment = ingress
    private = service / "tool" / "uv"
    if mode == "missing":
        uv.unlink()
    else:
        private.parent.mkdir()
        uv.replace(private)
    (Path(environment["PATH"]) / "uv").unlink()
    before = {path: path.read_bytes() for root in (service, engine) for path in root.rglob("*")
              if path.is_file()}
    result = subprocess.run(
        ["/bin/bash", str(publish)], capture_output=True, text=True,
        env=environment, timeout=15,
    )
    if mode == "missing":
        assert result.returncode != 0
        assert not private.parent.exists()
        assert {path: path.read_bytes() for root in (service, engine) for path in root.rglob("*")
                if path.is_file()} == before
        return
    assert result.returncode == 0, result.stdout + result.stderr
    root = service if mode == "service" else engine
    _slot(root, "1.0.0")
    (root / "current-version").write_text("1.0.0\n")
    result = _launch(root, mode, environment)
    assert result.returncode == 23, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[1])["args"][0] == (
        "start" if mode == "service" else "engine"
    )
    assert result.stderr == "application stderr\n"


@pytest.mark.parametrize("termination", [signal.SIGTERM, getattr(signal, "SIGKILL", 9), None])
def test_caller_loss_retires_only_the_owned_selector_tree(
    ingress: Ingress, termination: int | None,
) -> None:
    if sys.platform != "linux":
        raise AssertionError("Linux native-custody fixture")
    root, _engine, uv, _publish, environment = ingress
    _slot(root, "1.0.0")
    (root / "current-version").write_text("1.0.0\n")
    uv.write_text(
        '#!/bin/bash\n/bin/sleep 300 &\n'
        'printf "%s %s\\n" "$BASHPID" "$!" > "$UV_CALLS"\nwait\n',
        encoding="utf-8",
    )
    control = subprocess.Popen(["/bin/sleep", "300"])
    parent = subprocess.Popen(
        ["/bin/bash", str(root / "service.sh")], env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    descriptors = []
    try:
        ready = Path(environment["UV_CALLS"])
        deadline = time.monotonic() + 5
        while (not ready.exists() or not ready.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.02)
        descriptors = [os.pidfd_open(int(value)) for value in ready.read_text().split()]
        if termination is not None:
            parent.send_signal(termination)
        parent.wait(timeout=15)
        assert parent.returncode != 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if len(select.select(descriptors, [], [], 0)[0]) == len(descriptors):
                break
            time.sleep(0.02)
        assert len(select.select(descriptors, [], [], 0)[0]) == len(descriptors)
        assert control.poll() is None
        assert not list(Path(environment["TMPDIR"]).iterdir())
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        control.terminate()
        control.wait(timeout=5)
        for descriptor in descriptors:
            os.close(descriptor)
