"""Exercise the installed owner entry, not a host service or Codespace."""
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
import venv

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "plugins/agent-codespaces/scripts"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux systemd owner ingress")
Ingress = tuple[Path, Path, dict[str, str]]


def _function(name: str) -> str:
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 2]


@pytest.fixture
def ingress(tmp_path: Path) -> Ingress:
    root = tmp_path / 'owner space % "$'
    (root / "bin").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    temporary = tmp_path / "selector-temp"
    temporary.mkdir()
    for name in (
        "dirname", "cat", "tr", "awk", "date", "sed", "grep", "sort", "cut", "cp",
        "mkdir", "chmod", "stat", "sha256sum", "mktemp", "mv", "rmdir",
        "readlink", "uname", "mkfifo", "setsid", "sleep", "rm",
    ):
        executable = shutil.which(name)
        assert executable, name
        (tools / name).symlink_to(executable)
    uv = tools / "bound-uv"
    uv.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$@" >> "$UV_CALLS"\n'
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
        f"INSTALL_DIR={shlex.quote(str(root))}\n"
        f"SCRIPT_DIR={shlex.quote(str(SCRIPTS))}\n"
        f"UV_CMD={shlex.quote(str(uv))}\n"
        "_fail() { echo \"$*\" >&2; }\n"
        + _function("_publish_owner_assets") + "\n_publish_owner_assets\n",
        encoding="utf-8",
    )
    env = {
        **os.environ, "HOME": str(home), "PATH": str(tools), "TMPDIR": str(temporary),
        "UV_CALLS": str(tmp_path / "uv-calls"), "WORKER_CALLS": str(tmp_path / "worker-calls"),
        "UV_PYTHON": "poison", "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "PYTHONHOME": "poison", "PYTHONPATH": "poison",
        "VIRTUAL_ENV": "poison", "CONDA_PREFIX": "poison", "UV_PYTHON_DOWNLOADS": "never",
        "AGENT_CODESPACES_HOME": "/foreign/root", "PUBLICATION_SCRIPT": str(publish),
    }
    result = subprocess.run(
        ["/bin/bash", str(publish)], env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return root, uv, env


def _worker(python: Path) -> None:
    site = next(python.parent.parent.glob("lib/python*/site-packages"))
    package = site / "agent_codespaces"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "value = {'pid': os.getpid(), 'python': sys.executable, "
        "'base': sys._base_executable, 'cwd': os.getcwd(), 'argv': sys.argv[1:], "
        "'stdin': sys.stdin.read(), 'root': os.environ['AGENT_CODESPACES_HOME']}\n"
        "Path(os.environ['WORKER_CALLS']).write_text(json.dumps(value))\n"
        "print('worker output')\nprint('worker error', file=sys.stderr)\n"
        "raise SystemExit(19)\n", encoding="utf-8",
    )
    slot = python.parent.parent
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": slot.name, "completed_at": "2026-01-01T00:00:00Z", "pid": 0,
    }) + "\n", encoding="utf-8")


def _slot(root: Path, version: str = "1.0.0") -> Path:
    slot = root / "versions" / version
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / "bin/python"
    _worker(python)
    return python


def _launch(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["/bin/bash", "-c", 'printf "%s\\n" "$$"; exec /bin/sh "$@"', "owner",
         str(root / "bin/codespaces-owner.sh"), "two words", "", "--flag"],
        cwd=root.parent, env=env, input="retained input\n", capture_output=True,
        text=True, timeout=15,
    )
    assert not list(Path(env["TMPDIR"]).iterdir())
    return result


def _assert_worker(
    result: subprocess.CompletedProcess[str], root: Path, python: Path, env: dict[str, str],
) -> dict[str, object]:
    assert result.returncode == 19, result.stdout + result.stderr
    pid, output = result.stdout.splitlines()
    assert output == "worker output"
    assert "worker error" in result.stderr
    observed: dict[str, object] = json.loads(Path(env["WORKER_CALLS"]).read_text())
    assert observed["pid"] == int(pid)
    assert observed["python"] == str(python)
    assert observed["cwd"] == observed["root"] == str(root)
    assert observed["argv"] == ["owner", "two words", "", "--flag"]
    assert observed["stdin"] == "retained input\n"
    return observed


@pytest.mark.parametrize("selection", ("current", "lkg", "newest", "nested"))
def test_owner_keeps_selected_runtime_pid_streams_and_cutover(ingress: Ingress, selection: str) -> None:
    root, _uv, env = ingress
    python = _slot(root)
    if selection == "current":
        _slot(root, "2.0.0")
        (root / "current-version").write_text("1.0.0\n", encoding="utf-8")
    elif selection == "lkg":
        _slot(root, "2.0.0")
        (root / "current-version").write_text("missing\n", encoding="utf-8")
        (root / "last-known-good").write_text("1.0.0\n", encoding="utf-8")
    elif selection == "nested":
        slot = python.parent.parent
        nested = slot / ".venv"
        # Build at the final path: moving a venv would invalidate interpreter identity.
        venv.EnvBuilder(with_pip=False).create(nested)
        python = nested / "bin/python"
        _worker(python)
        (slot / "bin/python").unlink()
    for _ in range(2):
        _assert_worker(_launch(root, env), root, python, env)
    (root / "current-version").write_text("3.0.0\n", encoding="utf-8")
    replacement = _slot(root, "3.0.0")
    _assert_worker(_launch(root, env), root, replacement, env)
    calls = Path(env["UV_CALLS"]).read_text().splitlines()
    assert calls == sum(([
        "python", "find", "--no-project", "--managed-python", "--no-python-downloads", str(p),
    ] for p in (python, python, replacement)), [])


@pytest.mark.parametrize("failure", (
    "denied", "wrong", "uv", "runtime", "resolve-runtime.sh", "bootstrap-uv.sh",
    "native_owned.sh", "daemon_instance_runtime.pyz", "codespaces-owner-uv",
))
def test_owner_refuses_before_application_python(ingress: Ingress, failure: str) -> None:
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


def test_owner_real_uv_keeps_offline_managed_runtime(ingress: Ingress, request: pytest.FixtureRequest) -> None:
    root, uv, env = ingress
    actual_uv = shutil.which("uv")
    assert actual_uv, "The launch test environment requires prepared UV"
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
        '#!/bin/bash\nprintf "selection\\n" >> "$UV_CALLS"\n'
        f'exec {shlex.quote(str(executable))} "$@"\n', encoding="utf-8",
    )
    samples = []
    for _ in range(2):
        started = time.monotonic()
        observed = _assert_worker(_launch(root, env), root, python, env)
        samples.append(time.monotonic() - started)
        assert Path(str(observed["base"])).resolve() == Path(prepared.stdout.strip()).resolve()
    assert Path(env["UV_CALLS"]).read_text().splitlines() == ["selection", "selection"]
    request.node.user_properties.append(("codespaces_owner_performance", json.dumps({
        "samples_seconds": samples, "cycles": 2, "network": "no-downloads", "runtime": "managed-venv",
        "uv_calls_per_launch": 1,
    })))


def test_owner_unit_uses_installed_entry_and_preserves_unknown_config(ingress: Ingress) -> None:
    root, _uv, env = ingress
    runner = (
        "set -euo pipefail\n"
        f"INSTALL_DIR={shlex.quote(str(root))}\n"
        "SYSTEMD_OWNER_UNIT=agent-codespaces-owner.service\n"
        "_publish_owner_assets() { :; }\n"
        '_owner_enabled() { echo "$OWNER_STATE"; }\n'
        '_ok() { :; }; _warn() { :; }; _skip() { :; }\n'
        'systemctl() { printf "%s\\n" "$*" >> "$HOME/systemctl-calls"; }\n'
        '_remove_owner_service() { echo removed >> "$HOME/systemctl-calls"; }\n'
        + _function("_sync_owner_service") + "\n_sync_owner_service\n"
    )
    script = root.parent / "unit.sh"
    script.write_text(runner, encoding="utf-8")
    result = subprocess.run(
        ["/bin/bash", str(script)], env={**env, "OWNER_STATE": "enabled"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    unit = Path(env["HOME"]) / ".config/systemd/user/agent-codespaces-owner.service"
    original = unit.read_bytes()
    assert b"ExecStart=/bin/sh bin/codespaces-owner.sh\n" in original
    assert b"Restart=on-failure\nRestartSec=5\n" in original
    assert f"WorkingDirectory={str(root).replace('%', '%%')}/.\n".encode() in original
    calls = Path(env["HOME"]) / "systemctl-calls"
    before = calls.read_bytes()
    for state in ("unknown", "disabled"):
        result = subprocess.run(
            ["/bin/bash", str(script)], env={**env, "OWNER_STATE": state},
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert unit.read_bytes() == original
        if state == "unknown":
            assert calls.read_bytes() == before
        else:
            assert calls.read_bytes() == before + b"removed\n"


@pytest.mark.parametrize("termination", (signal.SIGTERM, signal.SIGKILL, None))
def test_owner_caller_loss_and_deadline_retire_selector_tree(
    ingress: Ingress, termination: signal.Signals | None,
) -> None:
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
        ["/bin/sh", str(root / "bin/codespaces-owner.sh")], cwd=root, env=env,
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
            assert time.monotonic() < deadline, "owner shutdown left a selector process alive"
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


def test_owner_publication_refuses_symlink_without_replacing_entry(ingress: Ingress) -> None:
    root, _uv, env = ingress
    entry = root / "bin/codespaces-owner.sh"
    original = entry.read_bytes()
    binding = root / "bin/codespaces-owner-uv"
    binding.rename(binding.with_suffix(".retained"))
    binding.symlink_to(binding.with_suffix(".retained"))
    result = subprocess.run(
        ["/bin/bash", env["PUBLICATION_SCRIPT"]], env=env, capture_output=True,
        text=True, timeout=15,
    )
    assert result.returncode != 0
    assert entry.read_bytes() == original
    assert binding.is_symlink()


def test_owner_support_materializes_from_canonical_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import materialize_installer_engine as materializer

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    names = frozenset({"bootstrap-uv.sh", "native_owned.sh"})
    log = materializer.materialize_derived_files(
        candidate, canonical_root=ROOT, copied_consumer=(SCRIPTS.parent, candidate), file_names=names,
    )
    assert log and not any(line.startswith("SKIP") for line in log), log
    for name in names:
        canonical = (
            ROOT / "libs/agent-procutil/src/agent_procutil" / name
            if name == "native_owned.sh" else ROOT / "libs/installer-engine" / name
        )
        assert (candidate / "scripts" / name).read_bytes() == canonical.read_bytes()


def test_owner_support_retirement_preserves_unknown_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "libs/installer-engine"))
    import installer_engine_uninstall as uninstall

    profile = uninstall.profile_for(SCRIPTS.parent, "agent-codespaces", SCRIPTS / "uninstall-profile.json")
    root = tmp_path / "runtime"
    names = (
        "bootstrap-uv.sh", "native_owned.sh", "daemon_instance_runtime.pyz",
        "codespaces-owner.sh", "codespaces-owner-uv", "operator-helper.sh",
    )
    for name in names:
        path = root / "bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture code")
    operations = uninstall._plan(SCRIPTS.parent, root, profile, (uninstall.Target(root, (), ()),))
    selected = {directory / name for directory, entries in operations for name in entries}
    assert {root / "bin" / name for name in names[:-1]} <= selected
    assert root / "bin" / names[-1] not in selected
