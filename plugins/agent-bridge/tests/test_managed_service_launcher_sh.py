"""Exercise the generated POSIX service binstub without registering a service."""
from __future__ import annotations

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

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "plugins/agent-bridge/scripts"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux systemd service ingress")
Ingress = tuple[Path, Path, dict[str, str]]


def _function(name: str) -> str:
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n\n", start) + 2]


@pytest.fixture
def service_ingress(tmp_path: Path) -> Ingress:
    root = tmp_path / "bridge space"
    home = tmp_path / "home"
    tools = tmp_path / "tools"
    home.mkdir()
    tools.mkdir()
    selection_temp = tmp_path / "selector-temp"
    selection_temp.mkdir()
    for name in ("bash", "dirname", "cat", "tr", "awk", "date", "sed", "grep", "sort", "cut",
                 "cp", "mkdir", "chmod", "stat", "sha256sum", "mktemp", "mv", "rmdir",
                 "readlink", "uname", "mkfifo", "setsid", "sleep", "rm"):
        executable = shutil.which(name)
        assert executable, name
        (tools / name).symlink_to(executable)
    calls = tmp_path / "systemctl-calls"
    script = tmp_path / "publish.sh"
    engine = ROOT / "libs/installer-engine/installer-engine.sh"
    engine_text = engine.read_text(encoding="utf-8")
    start = engine_text.index("_runtime_publication_commit_file() {")
    commit = engine_text[start:engine_text.index("\n}\n", start) + 2].replace(
        "_runtime_publication_commit_file()", "_bridge_test_commit()", 1,
    )
    script.write_text(
        "set -euo pipefail\n"
        f". {shlex.quote(str(engine))}\n"
        "_RUNTIME_PUBLICATION_EVIDENCE=''\n"
        f"INSTALL_DIR={shlex.quote(str(root))}\n"
        f"SCRIPT_DIR={shlex.quote(str(SCRIPTS))}\n"
        f"PLUGIN_DIR={shlex.quote(str(SCRIPTS.parent))}\n"
        f"SYSTEMD_CALLS={shlex.quote(str(calls))}\n"
        'VENV_DIR="$INSTALL_DIR/versions/obsolete"; PUBLISH_GLOBAL_BINSTUBS=false\n'
        'SYSTEMD_UNIT=agent-bridge.service\n'
        '_ok() { :; }; _skip() { :; }; _step() { :; }; _fail() { echo "$*" >&2; }\n'
        '_runtime_publication_copy() { cp -- "$1" "$2"; }\n'
        'systemctl() { printf "%s\\n" "$*" >> "$SYSTEMD_CALLS"; }\n'
        + commit + "\n"
        + '_runtime_publication_commit_file() {\n'
        + '    if [[ "${PAUSE_PUBLICATION:-}" == 1 ]]; then\n'
        + '        printf "ready\\n"\n        IFS= read -r signal\n'
        + '    fi\n    _bridge_test_commit "$@"\n}\n'
        + _function("deploy_binstub") + "\n"
        + _function("_write_service_binstub") + "\n"
        + _function("_install_systemd_unit") + "\n"
        + _function("do_stamp") + "\n"
        + 'if [[ "${1:-install}" == stamp ]]; then do_stamp\n'
        + "else deploy_binstub; _install_systemd_unit; fi\n",
        encoding="utf-8",
    )
    uv = tools / "uv"
    uv.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$@" > "$UV_CALLS"\n'
        'for key in UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON '
        'VIRTUAL_ENV CONDA_PREFIX PYTHONHOME PYTHONPATH UV_WORKING_DIR UV_WORKING_DIRECTORY '
        'UV_ISOLATED UV_ENV_FILE UV_PROJECT UV_FROZEN UV_LOCKED UV_NO_SYNC UV_CONFIG_FILE; do\n'
        '    [[ ! -v "$key" ]] || exit 91\n'
        'done\n[[ "$UV_NO_CONFIG" == 1 ]] || exit 92\n'
        'if [[ -n "${UV_OWNED_GROUP:-}" ]]; then\n'
        '    IFS= read -r identity < "/proc/$BASHPID/stat"\n'
        '    read -r _state _parent group _rest <<<"${identity##*) }"\n'
        '    printf "%s\\n" "$group" > "$UV_OWNED_GROUP"\n'
        'fi\n'
        '[[ "${UV_TEST_MODE:-}" != warning ]] || printf "selector warning\\n" >&2\n'
        '[[ "${UV_TEST_MODE:-}" != denied ]] || { echo "policy denied (os error 448)" >&2; exit 93; }\n'
        '[[ "${UV_TEST_MODE:-}" != wrong ]] || { echo /foreign/python; exit 0; }\n'
        'printf "%s\\n" "${@: -1}"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    subprocess.run(
        ["/bin/bash", str(script)], env={**os.environ, "HOME": str(home), "PATH": str(tools)},
        check=True, capture_output=True, text=True, timeout=15,
    )
    assert calls.read_text().splitlines() == [
        "--user daemon-reload", "--user enable agent-bridge.service",
    ]
    unit = (home / ".config/systemd/user/agent-bridge.service").read_text()
    assert "ExecStart=/bin/bash bin/start-agent-bridge" in unit
    assert "obsolete" not in unit
    assert "KillMode=process\n" in unit
    assert "Restart=on-failure\n" in unit
    assert f'WorkingDirectory={root}' in unit
    assert not (home / ".local/bin/agent-bridge").exists()
    environment = {
        **os.environ, "PATH": str(tools), "HOME": str(home), "TMPDIR": str(selection_temp),
        "UV_CALLS": str(tmp_path / "uv-calls"),
        "WORKER_CALLS": str(tmp_path / "worker-calls"),
        "UV_PYTHON": "poison", "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "PYTHONHOME": "poison", "PYTHONPATH": "poison",
        "VIRTUAL_ENV": "poison", "CONDA_PREFIX": "poison",
        "AGENT_RT_ROOT": "/foreign/root", "AGENT_BRIDGE_INSTALL_DIR": "/foreign/root",
        "PUBLICATION_SCRIPT": str(script), "PUBLICATION_NATIVE_PATH": str(tools),
    }
    return root, tools, environment


def _manager_path(tools: Path) -> Path:
    directory = tools.with_name("manager-tools")
    directory.mkdir()
    for source in tools.iterdir():
        if source.name not in {"uv", "uv-real"}:
            (directory / source.name).symlink_to(source)
    return directory


def _slot(root: Path, version: str, *, nested: bool = False) -> Path:
    slot = root / "versions" / version
    python = slot / (".venv/bin/python" if nested else "bin/python")
    python.parent.mkdir(parents=True)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "fixture", "pid": 0,
    }) + "\n", encoding="utf-8")
    python.write_text(
        "#!/bin/bash\n"
        '[[ ! -v PYTHONHOME && ! -v PYTHONPATH ]] || exit 94\n'
        'if [[ "${1:-}" == -I && "${2:-}" == -B && "${3:-}" == -c ]]; then\n'
        f'    exec {shlex.quote(sys.executable)} "$@"\nfi\n'
        'printf "%s\\n" "$$" "$0" "$PWD" "$AGENT_BRIDGE_INSTALL_DIR" '
        '"$AGENT_BRIDGE_CONFIG_DIR" "$AGENT_BRIDGE_CONNECT_LOG" "$@" > "$WORKER_CALLS"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    return python


def _launch(root: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    unit = Path(environment["HOME"]) / ".config/systemd/user/agent-bridge.service"
    values = dict(line.split("=", 1) for line in unit.read_text().splitlines() if "=" in line)
    command = shlex.split(values["ExecStart"])
    directory = values["WorkingDirectory"].replace("%%", "%")
    assert directory == str(root)
    result = subprocess.run(
        ["/bin/bash", "-c", 'printf "%s\\n" "$$"; exec "$@"', "service",
         *command, "two words", ""],
        cwd=directory, env=environment, capture_output=True, text=True, timeout=15,
    )
    assert not list(Path(environment["TMPDIR"]).iterdir())
    return result


def test_posix_service_unit_uses_systemd_scalar_directory(service_ingress: Ingress) -> None:
    analyzer = shutil.which("systemd-analyze")
    if not analyzer:
        pytest.skip("systemd static parser unavailable")
    root, _tools, environment = service_ingress
    original = Path(environment["HOME"]) / ".config/systemd/user/agent-bridge.service"
    special_root = root.with_name('bridge space "quoted" 20% \\slash')
    special_root.mkdir()
    result = subprocess.run(
        ["/bin/bash", "-c",
         '. "$PUBLICATION_SCRIPT"; INSTALL_DIR="$1"; deploy_binstub; _install_systemd_unit',
         "fixture", str(special_root)],
        env=environment, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = subprocess.run(
        [analyzer, "verify", "--man=no", str(original)],
        env={**os.environ, "SYSTEMD_LOG_LEVEL": "debug"},
        capture_output=True, text=True, timeout=15,
    )
    assert parsed.returncode == 0, parsed.stdout + parsed.stderr
    dump = (parsed.stdout + parsed.stderr).splitlines()
    assert f"WorkingDirectory: {special_root}" in [line.strip() for line in dump]
    assert f"Environment: AGENT_BRIDGE_INSTALL_DIR={special_root}" in [
        line.strip() for line in dump
    ]


@pytest.mark.parametrize("termination", [signal.SIGTERM, getattr(signal, "SIGKILL", 9)])
def test_posix_service_caller_loss_retires_selector_tree(
    service_ingress: Ingress, termination: signal.Signals,
) -> None:
    if sys.platform != "linux":
        pytest.skip("Linux pidfd ownership")
    root, tools, environment = service_ingress
    _slot(root, "1.0.0")
    (root / "current-version").write_text("1.0.0\n")
    (tools / "uv").write_text(
        '#!/bin/bash\n/bin/sleep 300 &\n'
        'printf "%s %s\\n" "$BASHPID" "$!" > "$UV_CALLS"\nwait\n',
        encoding="utf-8",
    )
    control = subprocess.Popen(
        ["/bin/sleep", "300"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    parent = subprocess.Popen(
        ["/bin/bash", str(root / "bin/start-agent-bridge")],
        cwd=root, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    descriptors: list[int] = []
    try:
        ready = Path(environment["UV_CALLS"])
        deadline = time.monotonic() + 5
        while not ready.is_file() or not ready.read_text().strip():
            assert parent.poll() is None
            assert time.monotonic() < deadline, "selector did not reach its owned child"
            time.sleep(.01)
        pids = [int(value) for value in ready.read_text().split()]
        leader = os.getpgid(pids[0])
        pids.append(leader)
        pids.extend(int(value) for value in Path(
            f"/proc/{leader}/task/{leader}/children",
        ).read_text().split())
        for pid in set(pids):
            descriptor = os.pidfd_open(pid)
            descriptors.append(descriptor)
            assert not select.select([descriptor], [], [], 0)[0]
        parent.send_signal(termination)
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while not all(select.select([fd], [], [], 0)[0] for fd in descriptors):
            assert time.monotonic() < deadline, "service shutdown left a selector process alive"
            time.sleep(.01)
        assert control.poll() is None
        assert not Path(environment["WORKER_CALLS"]).exists()
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        for descriptor in descriptors:
            if not select.select([descriptor], [], [], 0)[0]:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            os.close(descriptor)
        control.kill()
        control.wait(timeout=5)


@pytest.mark.parametrize("selection", ["current", "lkg", "newest", "nested", "cutover"])
def test_posix_service_admits_selected_runtime_before_exec(
    service_ingress: Ingress, selection: str,
) -> None:
    root, _tools, environment = service_ingress
    selected = _slot(root, "1.0.0", nested=selection == "nested")
    _slot(root, "2.0.0")
    if selection in {"current", "nested", "cutover"}:
        (root / "current-version").write_text("1.0.0\n")
    elif selection == "lkg":
        (root / "current-version").write_text("missing\n")
        (root / "last-known-good").write_text("1.0.0\n")
    else:
        selected = root / "versions/2.0.0/bin/python"
    for cycle in range(2):
        if selection == "cutover" and cycle:
            (root / "current-version").write_text("2.0.0\n")
            selected = root / "versions/2.0.0/bin/python"
        before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        result = _launch(root, environment)
        assert result.returncode == 0, result.stdout + result.stderr
        assert Path(environment["UV_CALLS"]).read_text().splitlines() == [
            "python", "find", "--no-project", "--managed-python", "--no-python-downloads",
            str(selected),
        ]
        assert Path(environment["WORKER_CALLS"]).read_text().splitlines() == [
            result.stdout.strip(), str(selected), str(root), str(root), str(root),
            str(root / "logs/connect.log"), "-m", "agent_bridge", "start", "two words", "",
        ]
        assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_posix_service_preserves_selector_diagnostics(service_ingress: Ingress) -> None:
    root, _tools, environment = service_ingress
    _slot(root, "1.0.0")
    (root / "current-version").write_text("1.0.0\n")
    result = _launch(root, {**environment, "UV_TEST_MODE": "warning"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == "selector warning\n"
    assert Path(environment["WORKER_CALLS"]).is_file()


@pytest.mark.parametrize("failure", [
    "runtime", "uv", "provider", "custody", "archive", "denied", "wrong",
])
def test_posix_service_refuses_before_application_python(
    service_ingress: Ingress, failure: str,
) -> None:
    root, tools, environment = service_ingress
    if failure != "runtime":
        _slot(root, "1.0.0")
        (root / "current-version").write_text("1.0.0\n")
    if failure == "uv":
        (tools / "uv").unlink()
    elif failure == "provider":
        (root / "bin/bootstrap-uv.sh").unlink()
    elif failure == "custody":
        (root / "bin/native_owned.sh").unlink()
    elif failure == "archive":
        (root / "bin/daemon_instance_runtime.pyz").unlink()
    else:
        environment["UV_TEST_MODE"] = failure
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    if failure in {"denied", "wrong"}:
        environment["UV_OWNED_GROUP"] = str(root.parent / "selector-group")
    for _cycle in range(2 if failure in {"denied", "wrong"} else 1):
        result = _launch(root, environment)
        assert result.returncode != 0
        if failure == "denied":
            assert "policy denied (os error 448)" in result.stderr
        assert not Path(environment["WORKER_CALLS"]).exists()
        assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
        if failure in {"runtime", "uv", "provider", "custody", "archive"}:
            assert not Path(environment["UV_CALLS"]).exists()
        if failure in {"denied", "wrong"} and sys.platform == "linux":
            group = int(Path(environment["UV_OWNED_GROUP"]).read_text())
            try:
                descriptor = os.pidfd_open(group)
            except ProcessLookupError:
                continue
            try:
                assert select.select([descriptor], [], [], 5)[0], "rejected selector retained its owner"
            finally:
                os.close(descriptor)


def test_posix_stamp_preserves_prepared_service_binding(service_ingress: Ingress) -> None:
    root, tools, environment = service_ingress
    _slot(root, "1.0.0")
    (root / "current-version").write_text("1.0.0\n")
    launcher = root / "bin/start-agent-bridge"
    before = launcher.read_bytes(), launcher.stat().st_ino
    environment["PATH"] = str(_manager_path(tools))
    result = subprocess.run(
        ["/bin/bash", environment["PUBLICATION_SCRIPT"], "stamp"],
        env=environment, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (launcher.read_bytes(), launcher.stat().st_ino) == before
    assert not Path(environment["UV_CALLS"]).exists()
    launched = _launch(root, environment)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert Path(environment["WORKER_CALLS"]).is_file()


def test_posix_service_publication_refuses_symlink_destination(service_ingress: Ingress) -> None:
    root, _tools, environment = service_ingress
    launcher = root / "bin/start-agent-bridge"
    target = root.parent / "unrelated"
    target.write_bytes(b"operator data\n")
    target.chmod(0o600)
    launcher.unlink()
    launcher.symlink_to(target)
    before = target.stat()
    result = subprocess.run(
        ["/bin/bash", environment["PUBLICATION_SCRIPT"]],
        env=environment, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert launcher.is_symlink() and launcher.resolve() == target
    assert target.read_bytes() == b"operator data\n"
    assert target.stat().st_mode == before.st_mode


def test_posix_service_start_reads_complete_launcher_during_publication(
    service_ingress: Ingress,
) -> None:
    root, _tools, environment = service_ingress
    _slot(root, "1.0.0")
    (root / "current-version").write_text("1.0.0\n")
    launcher = root / "bin/start-agent-bridge"
    before = launcher.read_bytes(), launcher.stat().st_ino
    with subprocess.Popen(
        ["/bin/bash", environment["PUBLICATION_SCRIPT"]],
        env={**environment, "PAUSE_PUBLICATION": "1"}, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as publisher:
        assert publisher.stdout is not None
        assert select.select([publisher.stdout], [], [], 10)[0], "publisher did not reach commit"
        assert publisher.stdout.readline() == "ready\n"
        assert (launcher.read_bytes(), launcher.stat().st_ino) == before
        launched = _launch(root, environment)
        assert launched.returncode == 0, launched.stdout + launched.stderr
        assert Path(environment["WORKER_CALLS"]).is_file()
        output, error = publisher.communicate("commit\n", timeout=15)
        assert publisher.returncode == 0, output + error
    assert launcher.read_bytes() == before[0]
    assert launcher.stat().st_ino != before[1]
    assert not list((root / "bin").glob(".start-agent-bridge.publication-stage.*"))


def test_posix_service_real_uv_keeps_offline_managed_runtime(
    service_ingress: Ingress, request: pytest.FixtureRequest,
) -> None:
    root, tools, environment = service_ingress
    uv = shutil.which("uv")
    assert uv, "The launch test environment requires prepared UV"
    clean_environment = {
        key: value for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "UV_CONFIG_FILE", "VIRTUAL_ENV", "CONDA_PREFIX"}
    }
    clean_environment["UV_NO_CONFIG"] = "1"
    clean_environment["UV_PYTHON_DOWNLOADS"] = "never"
    prepared = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        env=clean_environment, capture_output=True, text=True, timeout=15,
    )
    if prepared.returncode:
        pytest.skip("Explicitly prepare managed CPython 3.11.18 before this offline native witness")
    slot = root / "versions/1.0.0"
    subprocess.run(
        [uv, "venv", "--no-project", "--python", prepared.stdout.strip(),
         "--no-python-downloads", str(slot)],
        env=clean_environment, check=True, capture_output=True, text=True, timeout=15,
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.0.0", "completed_at": "fixture", "pid": 0,
    }) + "\n", encoding="utf-8")
    (root / "current-version").write_text("1.0.0\n")
    site_packages = next(slot.glob("lib/python*/site-packages"))
    (site_packages / "agent_bridge.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['WORKER_CALLS']).write_text(json.dumps({\n"
        "    'python': sys.executable, 'base': sys._base_executable,\n"
        "    'prefix': sys.prefix, 'pid': os.getpid(), 'argv': sys.argv[1:],\n"
        "}))\n",
        encoding="utf-8",
    )
    shutil.copyfile(uv, tools / "uv-real")
    (tools / "uv-real").chmod(0o755)
    (tools / "uv").write_text(
        '#!/bin/bash\nprintf "selection\\n" >> "$UV_CALLS"\n'
        'exec "$(dirname -- "$0")/uv-real" "$@"\n',
        encoding="utf-8",
    )
    environment["PATH"] = str(_manager_path(tools))
    samples = []
    for _cycle in range(2):
        started = time.perf_counter()
        result = _launch(root, environment)
        samples.append(time.perf_counter() - started)
        assert result.returncode == 0, result.stdout + result.stderr
        worker = json.loads(Path(environment["WORKER_CALLS"]).read_text())
        assert Path(worker.pop("base")).samefile(prepared.stdout.strip())
        assert worker == {
            "python": str(slot / "bin/python"),
            "prefix": str(slot), "pid": int(result.stdout.strip()),
            "argv": ["start", "two words", ""],
        }, worker
    assert Path(environment["UV_CALLS"]).read_text().splitlines() == ["selection"] * 2
    request.node.user_properties.append(("bridge_posix_service_performance", json.dumps({
        "samples_seconds": samples, "cycles": 2, "uv_calls_per_launch": 1,
        "network": "no-downloads", "runtime": "managed-venv",
    })))
