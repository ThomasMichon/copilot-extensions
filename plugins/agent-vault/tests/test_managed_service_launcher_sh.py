"""Exercise prepared Vault service ingress without contacting a service manager."""
from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
import venv
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux systemd service ingress")
Ingress = tuple[Path, Path, dict[str, str]]


@pytest.fixture
def ingress(tmp_path: Path) -> Ingress:
    root = tmp_path / "vault space"
    support = root / "bin"
    support.mkdir(parents=True)
    for name in (
        "vault-service.sh", "vault-cache-service.sh", "bootstrap-uv.sh",
        "native_owned.sh", "daemon_instance_runtime.pyz",
    ):
        shutil.copyfile(SCRIPTS / name, support / name)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in (
        "cat", "mktemp", "rm", "rmdir", "dirname", "readlink", "setsid",
        "sleep", "stat", "date", "uname", "mkfifo", "wc",
    ):
        executable = shutil.which(name)
        assert executable, name
        (tools / name).symlink_to(executable)
    uv = tools / "bound-uv"
    uv.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$@" > "$UV_CALLS"\n'
        '[[ "${UV_TEST_MODE:-}" != denied ]] || { echo "policy denied" >&2; exit 93; }\n'
        '[[ "${UV_TEST_MODE:-}" != wrong ]] || { echo /foreign/python; exit 0; }\n'
        'printf "selector warning\\n" >&2\n'
        'printf "%s\\n" "${@: -1}"\n', encoding="utf-8",
    )
    uv.chmod(0o755)
    (support / "vault-service-uv").write_text(str(uv), encoding="utf-8")
    (tools / "python").write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    (tools / "python").chmod(0o755)
    (tools / "uv").write_text("#!/bin/sh\nexit 92\n", encoding="utf-8")
    (tools / "uv").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    temporary = tmp_path / "selector-temp"
    temporary.mkdir()
    environment = {
        **os.environ, "HOME": str(home), "PATH": str(tools), "TMPDIR": str(temporary),
        "UV_CALLS": str(tmp_path / "uv-calls"), "WORKER_CALLS": str(tmp_path / "worker-calls"),
        "UV_PYTHON": "poison", "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "PYTHONHOME": "poison", "PYTHONPATH": "poison",
        "VIRTUAL_ENV": "poison", "CONDA_PREFIX": "poison", "UV_PYTHON_DOWNLOADS": "never",
        "VAULT_FIXTURE_CONTEXT": "retained context",
    }
    return root, uv, environment


def _worker(python: Path) -> None:
    site = next(python.parent.parent.glob("lib/python*/site-packages"))
    package = site / "agent_vault"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "value = {'pid': os.getpid(), 'python': sys.executable, "
        "'base': sys._base_executable, 'cwd': os.getcwd(), 'argv': sys.argv[1:], "
        "'stdin': sys.stdin.read(), 'context': os.environ['VAULT_FIXTURE_CONTEXT']}\n"
        "Path(os.environ['WORKER_CALLS']).write_text(json.dumps(value))\n"
        "print('worker output')\nprint('worker error', file=sys.stderr)\n"
        "raise SystemExit(19)\n", encoding="utf-8",
    )


def _slot(root: Path) -> Path:
    slot = root / "versions/1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / "bin/python"
    _worker(python)
    return python


def _launch(root: Path, python: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - fixture-owned service and selected runtime.
        ["/bin/bash", "-c", 'printf "%s\\n" "$$"; exec /bin/sh "$@"', "service",
         str(root / "bin/vault-service.sh"), str(python), str(root), "two words", "", "--flag"],
        cwd=root, env=env, input="handoff input\n", capture_output=True, text=True, timeout=15,
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
    assert observed["cwd"] == str(root)
    assert observed["argv"] == ["two words", "", "--flag"]
    assert observed["stdin"] == "handoff input\n"
    assert observed["context"] == "retained context"
    return observed


def test_service_keeps_pid_streams_arguments_and_context(ingress: Ingress) -> None:
    root, _uv, env = ingress
    python = _slot(root)
    for _ in range(2):
        result = _launch(root, python, env)
        _assert_worker(result, root, python, env)
        assert "selector warning" in result.stderr
        assert Path(env["UV_CALLS"]).read_text().splitlines() == [
            "python", "find", "--no-project", "--managed-python", "--no-python-downloads",
            str(python),
        ]


def test_raw_checkout_bootstrap_keeps_its_companion_declaration(ingress: Ingress) -> None:
    _root, bound_uv, env = ingress
    uv = bound_uv.with_name("uv")
    uv.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$@" >> "$UV_CALLS"\n'
        '[[ "${UV_NO_CONFIG:-}" == 1 && ! -v UV_PYTHON && ! -v VIRTUAL_ENV ]] || exit 91\n'
        'if [[ "$*" == --version ]]; then printf "uv 0.13.7\\n"; exit 0; fi\n'
        '[[ "$*" == "python find --no-project --system --managed-python '
        '--no-python-downloads cpython@3.11.18" ]] || exit 92\n'
        'printf "%s\\n" "$FIXTURE_BOOTSTRAP_PYTHON"\n', encoding="utf-8",
    )
    env["FIXTURE_BOOTSTRAP_PYTHON"] = sys.executable
    result = subprocess.run(  # noqa: S603 - actual source reader and fixture-owned UV.
        ["/bin/bash", "-c",
         '. "$1"; vault_bootstrap_python || exit "$?"; '
         'IFS= read -r retained; [[ "$retained" == "preserved input" ]]',
         "fixture", str(SCRIPTS / "runtime-activation.sh")],
        env=env, input="preserved input\n", capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [sys.executable]
    assert Path(env["UV_CALLS"]).read_text().splitlines() == [
        "--version", "python", "find", "--no-project", "--system",
        "--managed-python", "--no-python-downloads", "cpython@3.11.18",
    ]


@pytest.mark.parametrize("failure", [
    "denied", "wrong", "uv", "bootstrap-uv.sh", "native_owned.sh",
    "daemon_instance_runtime.pyz", "vault-cache-service.sh", "vault-service-uv",
])
def test_service_refuses_unprepared_support_before_worker(ingress: Ingress, failure: str) -> None:
    root, uv, env = ingress
    python = _slot(root)
    if failure in {"denied", "wrong"}:
        env["UV_TEST_MODE"] = failure
    else:
        path = uv if failure == "uv" else root / "bin" / failure
        path.rename(path.with_suffix(path.suffix + ".unavailable"))
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = _launch(root, python, env)
    assert result.returncode not in {0, 19}, result.stdout + result.stderr
    assert not Path(env["WORKER_CALLS"]).exists()
    assert {path: path.read_bytes() for path in before} == before


def test_service_real_uv_keeps_offline_managed_runtime(
    ingress: Ingress, request: pytest.FixtureRequest,
) -> None:
    root, uv, env = ingress
    actual_uv = shutil.which("uv")
    assert actual_uv, "The launch test environment requires prepared UV"
    clean = {
        key: value for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "UV_CONFIG_FILE", "VIRTUAL_ENV", "CONDA_PREFIX"}
    }
    clean.update(UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never")
    prepared = subprocess.run(  # noqa: S603 - prepared UV, fixed offline query.
        [actual_uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        env=clean, capture_output=True, text=True, timeout=15,
    )
    if prepared.returncode:
        pytest.skip("Prepare managed CPython 3.11.18 before this offline witness")
    slot = root / "versions/1.0.0"
    subprocess.run(  # noqa: S603 - prepared UV creates the private fixture environment.
        [actual_uv, "venv", "--no-project", "--python", prepared.stdout.strip(),
         "--no-python-downloads", str(slot)],
        env=clean, check=True, capture_output=True, text=True, timeout=15,
    )
    python = slot / "bin/python"
    _worker(python)
    shutil.copyfile(actual_uv, uv)
    uv.chmod(0o755)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    samples = []
    for _ in range(2):
        started = time.monotonic()
        observed = _assert_worker(_launch(root, python, env), root, python, env)
        samples.append(time.monotonic() - started)
        assert Path(str(observed["base"])).resolve() == Path(prepared.stdout.strip()).resolve()
    assert {path: path.read_bytes() for path in before} == before
    request.node.user_properties.append(("vault_posix_service_performance", json.dumps({
        "samples_seconds": samples, "cycles": 2, "network": "no-downloads",
        "runtime": "managed-venv",
    })))


@pytest.mark.parametrize("termination", [signal.SIGTERM, getattr(signal, "SIGKILL", 9), None])
def test_service_selector_caller_loss_and_deadline_retire_owned_tree(
    ingress: Ingress, termination: signal.Signals | None,
) -> None:
    if sys.platform != "linux":
        pytest.skip("Linux pidfd ownership")
    root, uv, env = ingress
    python = _slot(root)
    uv.write_text(
        '#!/bin/bash\n/bin/sleep 300 &\n'
        'printf "%s %s\\n" "$BASHPID" "$!" > "$UV_CALLS"\nwait\n', encoding="utf-8",
    )
    control = subprocess.Popen(
        ["/bin/sleep", "300"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    parent = subprocess.Popen(  # noqa: S603 - fixture-owned service and selected runtime.
        ["/bin/sh", str(root / "bin/vault-service.sh"), str(python), str(root)],
        cwd=root, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
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
            descriptor = os.pidfd_open(pid)
            descriptors.append(descriptor)
            assert not select.select([descriptor], [], [], 0)[0]
        if termination is not None:
            parent.send_signal(termination)
        parent.wait(timeout=15)
        assert parent.returncode != 0
        deadline = time.monotonic() + 5
        while not all(select.select([fd], [], [], 0)[0] for fd in descriptors):
            assert time.monotonic() < deadline, "service shutdown left a selector process alive"
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
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            os.close(descriptor)
        control.kill()
        control.wait(timeout=5)
