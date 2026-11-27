"""Exercise scheduled sync ingress without scheduling a host sync."""
from __future__ import annotations

import importlib
import importlib.util
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
from typing import Any
import venv

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "plugins/agent-logger/scripts"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux scheduled sync ingress")
Ingress = tuple[Path, Path, dict[str, str]]
SUPPORT = ("resolve-runtime.sh", "bootstrap-uv.sh", "native_owned.sh", "daemon_instance_runtime.pyz")


def _function(name: str) -> str:
    source = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    start = source.index(f"{name}() {{")
    return source[start:source.index("\n}\n", start) + 2]


@pytest.fixture
def ingress(tmp_path: Path) -> Ingress:
    root = tmp_path / 'sync space % "$\\'
    (root / "bin").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    temporary = tmp_path / "selector-temp"
    temporary.mkdir()
    for name in (
        "dirname", "cat", "tr", "awk", "date", "sed", "grep", "sort", "cut", "cp",
        "mkdir", "chmod", "stat", "sha256sum", "mktemp", "mv", "rmdir", "readlink",
        "uname", "mkfifo", "setsid", "sleep", "rm",
    ):
        executable = shutil.which(name)
        assert executable, name
        (tools / name).symlink_to(executable)
    uv = tools / "bound-uv"
    uv.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$@" >> "$UV_CALLS"\n'
        '[[ "$UV_NO_CONFIG" == 1 && ! -v UV_PYTHON && ! -v VIRTUAL_ENV ]] || exit 91\n'
        '[[ "${UV_TEST_MODE:-}" != denied ]] || { echo "policy denied" >&2; exit 93; }\n'
        '[[ "${UV_TEST_MODE:-}" != wrong ]] || { echo /foreign/python; exit 0; }\n'
        'echo "selector warning" >&2\nprintf "%s\\n" "${@: -1}"\n', encoding="utf-8",
    )
    uv.chmod(0o755)
    for name in ("uv", "python", "python3"):
        poison = tools / name
        poison.write_text("#!/bin/sh\nexit 92\n", encoding="utf-8")
        poison.chmod(0o755)
    publish = tmp_path / "publish.sh"
    publish.write_text(
        "set -euo pipefail\n"
        f". {shlex.quote(str(ROOT / 'libs/installer-engine/installer-engine.sh'))}\n"
        f"INSTALL_DIR={shlex.quote(str(root))}\nSCRIPT_DIR={shlex.quote(str(SCRIPTS))}\n"
        f"UV_CMD={shlex.quote(str(uv))}\n"
        '_fail() { echo "$*" >&2; }\n'
        + _function("_publish_sync_assets") + "\n_publish_sync_assets\n", encoding="utf-8",
    )
    env = {
        **os.environ, "HOME": str(home), "PATH": str(tools), "TMPDIR": str(temporary),
        "UV_CALLS": str(tmp_path / "uv-calls"), "WORKER_CALLS": str(tmp_path / "worker-calls"),
        "UV_PYTHON": "poison", "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "PYTHONHOME": "poison", "PYTHONPATH": "poison",
        "VIRTUAL_ENV": "poison", "CONDA_PREFIX": "poison", "UV_PYTHON_DOWNLOADS": "never",
        "AGENT_LOGGER_HOME": "/foreign/root", "PUBLICATION_SCRIPT": str(publish),
    }
    for name in (*SUPPORT, "logger-sync.sh"):
        shutil.copyfile(SCRIPTS / name, root / "bin" / name)
    (root / "bin/logger-sync-uv").write_text(str(uv), encoding="utf-8")
    return root, uv, env


def _worker(python: Path) -> None:
    package = next(python.parent.parent.glob("lib/python*/site-packages")) / "agent_logger"
    (package / "sync").mkdir(parents=True)
    for path in (package, package / "sync"):
        (path / "__init__.py").write_text("", encoding="utf-8")
    (package / "sync/engine.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "value={'pid':os.getpid(),'python':sys.executable,'base':sys._base_executable,"
        "'cwd':os.getcwd(),'argv':sys.argv[1:],'stdin':sys.stdin.read(),"
        "'root':os.environ['AGENT_LOGGER_HOME'],'utf8':sys.flags.utf8_mode}\n"
        "Path(os.environ['WORKER_CALLS']).write_text(json.dumps(value))\n"
        "print('worker output')\nprint('worker error',file=sys.stderr)\n"
        "raise SystemExit(19)\n", encoding="utf-8",
    )
    slot = python.parent.parent
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": slot.name, "completed_at": "2026-01-01T00:00:00Z", "pid": 0,
    }) + "\n", encoding="utf-8")


def _slot(root: Path, version: str = "1.0.0") -> Path:
    slot = root / "versions" / version
    venv.EnvBuilder(with_pip=False, symlinks=True).create(slot)
    python = slot / "bin/python"
    _worker(python)
    return python


def _launch(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["/bin/bash", "-c", 'printf "%s\\n" "$$"; exec /bin/sh "$@"', "sync",
         str(root / "bin/logger-sync.sh"), "run", "--prune", "two words", "", "\u03bb"],
        cwd=root.parent, env=env, input="retained input\n", capture_output=True,
        text=True, timeout=15,
    )
    assert not list(Path(env["TMPDIR"]).iterdir())
    return result


def _assert_worker(
    result: subprocess.CompletedProcess[str], root: Path, python: Path, env: dict[str, str],
    arguments: list[str] | None = None,
) -> dict[str, Any]:
    assert result.returncode == 19, result.stdout + result.stderr
    pid, output = result.stdout.splitlines()
    assert output == "worker output"
    assert "worker error" in result.stderr and "selector warning" in result.stderr
    observed: dict[str, Any] = json.loads(Path(env["WORKER_CALLS"]).read_text())
    assert observed["pid"] == int(pid)
    assert observed["python"] == str(python)
    assert observed["root"] == str(root)
    assert observed["cwd"] == str(root.parent)
    assert observed["argv"] == (
        ["run", "--prune", "two words", "", "\u03bb"] if arguments is None else arguments
    )
    assert observed["stdin"] == "retained input\n"
    assert observed["utf8"] == 1
    return observed


@pytest.mark.parametrize("selection", ("current", "lkg", "newest", "nested"))
def test_sync_preserves_selected_runtime_pid_stdio_cwd_and_cutover(
    ingress: Ingress, selection: str,
) -> None:
    root, _uv, env = ingress
    python = _slot(root)
    if selection in {"current", "lkg"}:
        _slot(root, "2.0.0")
        (root / "current-version").write_text(
            "1.0.0\n" if selection == "current" else "missing\n", encoding="utf-8",
        )
        if selection == "lkg":
            (root / "last-known-good").write_text("1.0.0\n", encoding="utf-8")
    elif selection == "nested":
        slot = python.parent.parent
        venv.EnvBuilder(with_pip=False, symlinks=True).create(slot / ".venv")
        python = slot / ".venv/bin/python"
        _worker(python)
        (slot / "bin/python").unlink()
    for _ in range(2):
        _assert_worker(_launch(root, env), root, python, env)
    replacement = _slot(root, "3.0.0")
    (root / "current-version").write_text("3.0.0\n", encoding="utf-8")
    _assert_worker(_launch(root, env), root, replacement, env)
    assert Path(env["UV_CALLS"]).read_text().splitlines() == sum(([
        "python", "find", "--no-project", "--managed-python", "--no-python-downloads", str(p),
    ] for p in (python, python, replacement)), [])


@pytest.mark.parametrize("failure", ("denied", "wrong", "uv", "runtime", *SUPPORT, "logger-sync-uv"))
def test_sync_refuses_before_application_python(ingress: Ingress, failure: str) -> None:
    root, uv, env = ingress
    if failure != "runtime":
        _slot(root)
    if failure in {"denied", "wrong"}:
        env["UV_TEST_MODE"] = failure
    elif failure != "runtime":
        path = uv if failure == "uv" else root / "bin" / failure
        path.rename(path.with_suffix(path.suffix + ".unavailable"))
    result = _launch(root, env)
    assert result.returncode not in {0, 19}, result.stdout + result.stderr
    assert not Path(env["WORKER_CALLS"]).exists()
    if failure not in {"denied", "wrong"}:
        assert not Path(env["UV_CALLS"]).exists()


def test_sync_real_uv_repeats_offline_managed_execution(
    ingress: Ingress, request: pytest.FixtureRequest,
) -> None:
    root, uv, env = ingress
    actual_uv = shutil.which("uv")
    assert actual_uv
    clean = {key: value for key, value in os.environ.items() if key not in {
        "PYTHONHOME", "PYTHONPATH", "UV_CONFIG_FILE", "VIRTUAL_ENV", "CONDA_PREFIX",
    }}
    clean.update(UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never")
    prepared = subprocess.run(
        [actual_uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        env=clean, capture_output=True, text=True, timeout=15,
    )
    if prepared.returncode:
        pytest.skip("Prepare managed CPython 3.11.18 before this offline witness")
    slot = root / "versions/1.0.0"
    subprocess.run(
        [actual_uv, "venv", "--no-project", "--python", prepared.stdout.strip(),
         "--no-python-downloads", str(slot)],
        env=clean, check=True, capture_output=True, text=True, timeout=15,
    )
    python = slot / "bin/python"
    _worker(python)
    executable = uv.with_name("prepared-uv-executable")
    shutil.copyfile(actual_uv, executable)
    executable.chmod(0o755)
    uv.write_text(
        '#!/bin/bash\nprintf "selection\\n" >> "$UV_CALLS"\necho "selector warning" >&2\n'
        f'exec {shlex.quote(str(executable))} "$@"\n', encoding="utf-8",
    )
    samples = []
    for _ in range(2):
        started = time.monotonic()
        observed = _assert_worker(_launch(root, env), root, python, env)
        samples.append(time.monotonic() - started)
        assert Path(observed["base"]).resolve() == Path(prepared.stdout.strip()).resolve()
    assert Path(env["UV_CALLS"]).read_text().splitlines() == ["selection", "selection"]
    request.node.user_properties.append(("logger_sync_ingress", json.dumps({
        "samples_seconds": samples, "cycles": 2, "network": "no-downloads",
        "runtime": "managed-venv", "uv_calls_per_launch": 1,
    })))


@pytest.mark.parametrize("cold", (False, True))
def test_sync_publishes_complete_support(ingress: Ingress, cold: bool) -> None:
    root, _uv, env = ingress
    entry = root / "bin/logger-sync.sh"
    original = entry.read_bytes()
    entry.write_bytes(original + b"\n# previous publication\n")
    if cold:
        entry.parent.rename(root / "retained-bin")
    result = subprocess.run(
        ["/bin/bash", env["PUBLICATION_SCRIPT"]], env=env, capture_output=True,
        text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert entry.read_bytes() == original
    python = _slot(root)
    _assert_worker(_launch(root, env), root, python, env)


def test_sync_publisher_refuses_link_without_replacing_entry(ingress: Ingress) -> None:
    root, _uv, env = ingress
    entry = root / "bin/logger-sync.sh"
    original = entry.read_bytes()
    binding = root / "bin/logger-sync-uv"
    binding.rename(binding.with_suffix(".retained"))
    binding.symlink_to(binding.with_suffix(".retained"))
    result = subprocess.run(
        ["/bin/bash", env["PUBLICATION_SCRIPT"]], env=env, capture_output=True,
        text=True, timeout=15,
    )
    assert result.returncode != 0
    assert entry.read_bytes() == original
    assert binding.is_symlink()


@pytest.mark.parametrize("termination", (signal.SIGTERM, getattr(signal, "SIGKILL", 9), None))
def test_sync_caller_loss_and_deadline_retire_selector_tree(
    ingress: Ingress, termination: signal.Signals | None,
) -> None:
    if sys.platform != "linux":
        pytest.skip("Linux pidfd ownership")
    from agent_procutil._linux_pidfd import open_pidfd, send_pidfd_signal

    root, uv, env = ingress
    _slot(root)
    uv.write_text(
        '#!/bin/bash\n/bin/sleep 300 &\n'
        'printf "%s %s\\n" "$BASHPID" "$!" > "$UV_CALLS"\nwait\n', encoding="utf-8",
    )
    control = subprocess.Popen(
        ["/bin/sleep", "300"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    parent = subprocess.Popen(
        ["/bin/sh", str(root / "bin/logger-sync.sh")], cwd=root, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    descriptors: list[int] = []
    try:
        ready = Path(env["UV_CALLS"])
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
            descriptor = open_pidfd(pid)
            descriptors.append(descriptor)
            assert not select.select([descriptor], [], [], 0)[0]
        if termination is not None:
            parent.send_signal(termination)
        parent.wait(timeout=15)
        assert parent.returncode != 0
        deadline = time.monotonic() + 5
        while not all(select.select([fd], [], [], 0)[0] for fd in descriptors):
            assert time.monotonic() < deadline, "sync shutdown left a selector process alive"
            time.sleep(.01)
        assert control.poll() is None
        assert not Path(env["WORKER_CALLS"]).exists()
        assert not list(Path(env["TMPDIR"]).iterdir())
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        for descriptor in descriptors:
            if not select.select([descriptor], [], [], 0)[0]:
                send_pidfd_signal(descriptor, signal.SIGKILL)
            os.close(descriptor)
        control.kill()
        control.wait(timeout=5)


def test_sync_support_materializes_and_retires_finitely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    materializer = importlib.import_module("materialize_installer_engine")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    names = frozenset({"bootstrap-uv.sh", "native_owned.sh"})
    log = materializer.materialize_derived_files(
        candidate, canonical_root=ROOT, copied_consumer=(SCRIPTS.parent, candidate), file_names=names,
    )
    assert log and not any(line.startswith("SKIP") for line in log), log
    canonical = {
        "bootstrap-uv.sh": ROOT / "libs/installer-engine/bootstrap-uv.sh",
        "native_owned.sh": ROOT / "libs/agent-procutil/src/agent_procutil/native_owned.sh",
    }
    for name in names:
        assert (candidate / "scripts" / name).read_bytes() == canonical[name].read_bytes()
    monkeypatch.syspath_prepend(str(ROOT / "libs/installer-engine"))
    uninstall = importlib.import_module("installer_engine_uninstall")
    profile = uninstall.profile_for(SCRIPTS.parent, "agent-logger")
    legacy_payload = tmp_path / "legacy-payload"
    (legacy_payload / "scripts").mkdir(parents=True)
    legacy = uninstall.profile_for(legacy_payload, "agent-logger")
    assert profile.root_entries == legacy.root_entries
    assert profile.script_entries == legacy.script_entries
    assert profile.local_bin_entries == legacy.local_bin_entries
    assert "logger-sync.sh" not in legacy.bin_entries
    expected = {*SUPPORT, "logger-sync.sh", "logger-sync-uv"}
    assert expected <= set(profile.bin_entries)
    assert "operator-helper.sh" not in profile.bin_entries
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    for name in (*expected, "operator-helper.sh"):
        (runtime / "bin" / name).write_text("retained", encoding="utf-8")
    operations = uninstall._plan(
        SCRIPTS.parent, runtime, profile, (uninstall.Target(runtime, (), ()),),
    )
    selected = {directory / name for directory, entries in operations for name in entries}
    assert {runtime / "bin" / name for name in expected} <= selected
    assert runtime / "bin/operator-helper.sh" not in selected


@pytest.mark.parametrize("alias", ("/", "/./"))
@pytest.mark.parametrize("active", (False, True))
@pytest.mark.parametrize("selector", (".venv", "versions/1.0.0"))
def test_sync_preserves_historical_alias_uninstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str, active: bool, selector: str,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    legacy = importlib.import_module("test_logger_runtime_uninstall")
    legacy.test_posix_logger_custom_alias_normalizes_and_retires_legacy_units(
        tmp_path, alias, active, selector,
    )


@pytest.mark.parametrize("replacement", ("publisher", "identity"))
def test_sync_preserves_original_command_retirement_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    legacy = importlib.import_module("test_logger_runtime_uninstall")
    legacy.test_logger_command_removal_preserves_publication_custody_and_identity(
        tmp_path, monkeypatch, replacement,
    )


@pytest.mark.parametrize("simple", (False, True))
def test_sync_unit_and_uninstall_keep_literal_root_and_policy(
    ingress: Ingress, monkeypatch: pytest.MonkeyPatch, simple: bool,
) -> None:
    root, _uv, env = ingress
    if simple:
        previous = root
        root = root.with_name("plain-root")
        previous.rename(root)
    units = Path(env["HOME"]) / ".config/systemd/user"
    script = root.parent / "units.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"INSTALL_DIR={shlex.quote(str(root))}\nUNIT_DIR={shlex.quote(str(units))}\n"
        "VENV=/unavailable\nTIMER_NAME=agent-logger-sync\n"
        '_fail() { echo "$*" >&2; }; chg() { :; }\n'
        + _function("write_units") + "\nwrite_units\n", encoding="utf-8",
    )
    result = subprocess.run(
        ["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    service = units / "agent-logger-sync.service"
    body = service.read_text(encoding="utf-8")
    assert "ExecStart=:/bin/sh " in body and " run --prune\n" in body
    assert "Type=oneshot\n" in body and "TimeoutStartSec=1800\n" in body
    assert "WorkingDirectory=" not in body
    timer = service.with_suffix(".timer").read_text(encoding="utf-8")
    assert "OnBootSec=5min\nOnUnitActiveSec=4h\nRandomizedDelaySec=10min\nPersistent=true\n" in timer
    spec = importlib.util.spec_from_file_location("_logger_sync_uninstall", SCRIPTS / "uninstall_guard.py")
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    monkeypatch.setattr(Path, "home", lambda: Path(env["HOME"]))
    assert guard.legacy_unit_names(root, units) == ["agent-logger-sync"]
    for changed in (
        body.replace(" run --prune\n", " foreign\n"),
        body + "Environment=AGENT_LOGGER_HOME=/foreign/root\n",
    ):
        service.write_text(changed, encoding="utf-8")
        with pytest.raises(RuntimeError, match="runtime binding"):
            guard.legacy_unit_names(root, units)
    service.write_text(
        f"Environment=AGENT_LOGGER_HOME={root}\n"
        f"ExecStart={root}/.venv/bin/session-sync run --prune\n", encoding="utf-8",
    )
    assert guard.legacy_unit_names(root, units) == ["agent-logger-sync"]
    service.write_text(body, encoding="utf-8")
    analyzer = shutil.which("systemd-analyze")
    assert analyzer, "native scheduled-unit qualification requires the systemd static parser"
    parsed = subprocess.run(
        [analyzer, "verify", "--man=no", str(service)],
        env={**os.environ, "SYSTEMD_LOG_LEVEL": "debug"},
        capture_output=True, text=True, timeout=15,
    )
    assert parsed.returncode == 0, parsed.stdout + parsed.stderr
    dump = [line.lstrip() for line in (parsed.stdout + parsed.stderr).splitlines()]
    assert f"Environment: AGENT_LOGGER_HOME={root}" in dump
    commands = [line.removeprefix("Command Line: ") for line in dump if line.startswith("Command Line:")]
    assert len(commands) == 1
    # systemd's display quotes dollars for a shell; shlex retains that escape.
    command = shlex.split(commands[0].replace("\\$", "$"))
    assert command == ["/bin/sh", str(root / "bin/logger-sync.sh"), "run", "--prune"]
    python = _slot(root)
    launched = subprocess.run(
        ["/bin/bash", "-c", 'printf "%s\\n" "$$"; exec "$@"', "sync", *command],
        cwd=root.parent, env=env, input="retained input\n", capture_output=True,
        text=True, timeout=15,
    )
    _assert_worker(launched, root, python, env, ["run", "--prune"])
