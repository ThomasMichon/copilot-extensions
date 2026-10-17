"""Contained real-CLI proof of non-activation and a current daemon's no-op."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import ExitStack
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from zdd import diagnostics, process_exit, routing

from agent_bridge.client import BridgeClient
from agent_bridge.install_paths import CONFIG_DIR_ENV, INSTALL_DIR_ENV


def _environment(root: Path) -> dict[str, str]:
    return {
        **os.environ, CONFIG_DIR_ENV: str(root), INSTALL_DIR_ENV: str(root),
        "AGENT_BRIDGE_WATCHDOG": "0",
    }


def _run_reconcile(root: Path, *, timeout: float = 12) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agent_bridge", "deploy", "--reconcile-serving", "--json"],
        cwd=root, env=_environment(root), capture_output=True, text=True,
        timeout=timeout, check=False, **no_window_kwargs(),
    )


def _spawn_fixture(argv, **kwargs):
    return spawn_sync_in_kill_on_close_job(
        argv, require_job=sys.platform == "win32", allow_breakaway=False,
        **({"start_new_session": True} if os.name == "posix" else {}),
        **kwargs, **no_window_kwargs(),
    )


def _stop_fixture(daemon, job) -> None:
    if job is not None:
        job.close()
    elif os.name == "posix":
        try:
            os.killpg(daemon.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if daemon.poll() is None:
        daemon.terminate()
    daemon.wait(timeout=2)


@pytest.mark.parametrize(("body", "code", "state"), [
    (None, 0, "inactive-or-forwarded"),
    ('{"active":{"port":9011,"forwarded":true}}', 0, "inactive-or-forwarded"),
    ('{"active":{"port":9011}}', 0, "inactive-or-forwarded"),
    ("{broken", 1, None),
])
def test_serving_reconcile_native_first_touch_never_activates(tmp_path, body, code, state):
    root = tmp_path / "runtime"
    root.mkdir()
    if body is not None:
        (root / "active.json").write_text(body)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    result = _run_reconcile(root)
    assert result.returncode == code, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is (code == 0)
    if state is not None:
        assert data["serving_reconcile"] == state
        assert data["skipped"] is True
    else:
        assert "error" in data
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


@pytest.mark.parametrize(("credential", "serving"), [
    (None, True), (b"token: ''\n", True), (b"token: [\n", True), (None, False),
])
def test_serving_worker_never_creates_or_repairs_credentials(tmp_path, credential, serving):
    import hashlib

    root = tmp_path / "runtime"
    root.mkdir()
    path = root / "auth.yaml"
    if credential is not None:
        path.write_bytes(credential)
    (root / "config.yaml").write_text(
        "schema_version: 2\nbind: '127.0.0.1'\nenable_credential_relay: false\n"
        "topologies: {}\nretention:\n  enabled: false\n",
    )
    with socket.socket() as occupied, (root / "worker.log").open("wb") as log, ExitStack() as cleanup:
        if sys.platform == "win32":
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        worker, job = _spawn_fixture(
            [
                sys.executable, "-m", "agent_bridge", "start", "--passive",
                "--port", str(occupied.getsockname()[1]),
                *(["--serving-launch-id", "ab" * 16] if serving else []),
            ],
            cwd=root, env=_environment(root), stdout=log, stderr=log,
        )
        cleanup.callback(_stop_fixture, worker, job)
        worker.wait(timeout=20)
        assert worker.returncode != 0, "invalid worker credentials unexpectedly admitted startup"
        if not serving:
            assert path.exists(), "ordinary startup lost its create-capable credential loader"
            return
        assert path.exists() is (credential is not None), "worker created a missing credential"
        if credential is not None:
            assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(credential).digest()
        assert "existing Bridge credential" in (root / "worker.log").read_text()
        assert not list(root.glob("serving-launch-*.json"))


@pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="native reference platform")
def test_native_fixture_early_failure_stops_broker_and_worker_before_receipt(tmp_path):
    broker_file, worker_file = tmp_path / "broker.json", tmp_path / "worker.json"
    worker_code = (
        "import os,sys,time; from pathlib import Path; "
        "p=Path(sys.argv[1]); t=p.with_suffix('.tmp'); "
        "t.write_text(str(os.getpid())); t.replace(p); time.sleep(60)"
    )
    broker_code = (
        "import os,subprocess,sys; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[3]],"
        "creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0); "
        "p=Path(sys.argv[2]); t=p.with_suffix('.tmp'); "
        "t.write_text(str(os.getpid())); t.replace(p); child.wait()"
    )
    with ExitStack() as references:
        with pytest.raises(RuntimeError, match="injected early readiness failure"):
            with ExitStack() as cleanup:
                launcher, job = _spawn_fixture(
                    [sys.executable, "-c", broker_code, worker_code, str(broker_file), str(worker_file)],
                    cwd=tmp_path, env=_environment(tmp_path),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                cleanup.callback(_stop_fixture, launcher, job)
                deadline = time.monotonic() + 10
                while not (broker_file.exists() and worker_file.exists()):
                    assert time.monotonic() < deadline, "fixture children did not report startup"
                    time.sleep(0.05)
                captured = []
                for path in (broker_file, worker_file):
                    pid = int(path.read_text())
                    reference = process_exit.open_process(pid, diagnostics.process_start_time(pid))
                    references.callback(reference.close)
                    captured.append(reference)
                assert captured[0].pid != captured[1].pid
                assert not list(tmp_path.glob("serving-launch-*.json"))
                raise RuntimeError("injected early readiness failure")
        assert launcher.poll() is not None
        assert all(reference.wait(2) for reference in captured), "fixture leaked an actual child"


@pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="native reference platform")
@pytest.mark.parametrize(("published", "refuse_cleanup"), [(False, False), (True, False), (True, True)])
def test_serving_candidate_native_worker_is_not_its_launcher(tmp_path, published, refuse_cleanup):
    import agent_bridge
    from agent_bridge.serving_candidate import ServingCandidate, receipt_path
    from agent_bridge.service_process_state import _service_health_on_port
    from agent_bridge.serving_reconcile import ServingReconcileError

    root = tmp_path / "runtime"
    root.mkdir()
    (root / "config.yaml").write_text(
        "schema_version: 2\nbind: '127.0.0.1'\nenable_credential_relay: false\n"
        "topologies: {}\nretention:\n  enabled: false\n",
    )
    token = "contained-fixture-token"
    (root / "auth.yaml").write_text(f"token: {token}\n")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    def health():
        with urllib.request.urlopen(f"{base}/health", timeout=0.25) as response:
            return json.load(response)

    candidate = ServingCandidate(
        root, port, agent_bridge.__version__,
        lambda: _service_health_on_port(port, timeout=0.25, require_ready=False),
        lambda: BridgeClient(base, token, timeout=1, connect_grace=0),
    )
    deadline = time.monotonic() + 25
    with (root / "fixture.log").open("wb") as log, ExitStack() as cleanup:
        worker_command = [
            sys.executable, "-u", "-m", "agent_bridge", "start",
            "--port", str(port), "--passive", "--serving-launch-id", candidate.launch_id,
        ]
        command = [
            sys.executable, "-u", "-c",
            "print('fixture launcher started',flush=True); import subprocess,sys; "
            "sys.exit(subprocess.call(sys.argv[1:], "
            "creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0))",
            *worker_command,
        ]
        launcher, job = _spawn_fixture(
            command,
            cwd=root, env=_environment(root), stdout=log, stderr=log,
        )
        candidate.launcher = launcher
        cleanup.callback(_stop_fixture, launcher, job)
        cleanup.callback(candidate.close, completed=False)
        last_probe = None
        while time.monotonic() < deadline:
            try:
                body = health()
                candidate.verify_health(body)
                break
            except (OSError, ValueError, ServingReconcileError) as exc:
                last_probe = f"{type(exc).__name__}: {exc}"
                if candidate.pid is not None:
                    assert candidate.poll() is None, "actual passive worker exited during startup"
                time.sleep(0.05)
        else:
            pytest.fail(
                f"worker={candidate.pid}, probe={last_probe}; "
                f"{(root / 'fixture.log').read_text()}"
            )
        assert candidate.process is not None
        assert candidate.pid != launcher.pid
        assert candidate.process.start_time == diagnostics.process_start_time(candidate.pid)
        assert body["slot"]["pid"] == candidate.pid
        assert not (root / "active.json").exists()
        with pytest.raises(ServingReconcileError):
            candidate.verify_health({**body, "slot": {**body["slot"], "pid": launcher.pid}})
        if published:
            endpoint = routing.publish_active(
                root, bind="127.0.0.1", port=port, pid=candidate.pid,
                version=agent_bridge.__version__, process_start_time=candidate.process.start_time,
            )
            candidate.mark_published(endpoint)
            candidate.verify_health(health())
        if refuse_cleanup:
            def refuse_shutdown():
                raise RuntimeError("fixture predecessor unavailable")

            candidate._before_shutdown = refuse_shutdown
            with pytest.raises(RuntimeError, match="fixture predecessor unavailable"):
                candidate.terminate()
            restored = health()
            candidate.verify_health(restored)
            assert restored["draining"] is False, "real HTTP compensation left admission closed"
            assert candidate.poll() is None
            assert receipt_path(root, candidate.launch_id).exists()
            assert routing.read_table(root)["active"] == endpoint.to_dict()
            candidate._before_shutdown = None
        candidate.terminate()
        assert candidate.process.wait(2), "owned actual worker did not gracefully exit"
        launcher.wait(timeout=2)
        assert time.monotonic() < deadline, "worker proof exhausted the shared consumer budget"


@pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="native reference platform")
@pytest.mark.parametrize(("bind", "stopped"), [
    ("127.0.0.1", False), ("::1", False), ("127.0.0.2", False), ("127.0.0.1", True),
])
def test_serving_reconcile_native_daemon_lifecycle_never_activates(tmp_path, bind, stopped):
    if bind != "127.0.0.1":
        try:
            family = socket.AF_INET6 if ":" in bind else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.bind((bind, 0))
        except OSError:
            pytest.skip(f"native local bind {bind} is unavailable")
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "config.yaml").write_text(
        f"schema_version: 2\nport: 0\nbind: '{bind}'\n"
        "enable_credential_relay: false\ntopologies: {}\n"
        "retention:\n  enabled: false\n",
    )
    token = "contained-fixture-token"
    (root / "auth.yaml").write_text(f"token: {token}\n")
    # One shared budget includes cold daemon imports and the cold client, with
    # four seconds left for cleanup inside the runner's unchanged 30-second cap.
    deadline = time.monotonic() + 25
    with (root / "fixture.log").open("wb") as log, ExitStack() as cleanup:
        client_out = cleanup.enter_context((root / "client.out").open("wb"))
        client_err = cleanup.enter_context((root / "client.err").open("wb"))
        # Import in a separate cold CLI process while the daemon imports.
        # The barrier prevents any command execution before verified readiness.
        client, client_job = _spawn_fixture(
            [
                sys.executable, "-u", "-c",
                "import sys; from agent_bridge.__main__ import main; "
                "sys.stdin.buffer.read(1); main()",
                "deploy", "--reconcile-serving", "--json",
                *(["--probe-serving"] if bind == "::1" else []),
            ],
            cwd=root, env=_environment(root), stdin=subprocess.PIPE,
            stdout=client_out, stderr=client_err,
        )
        cleanup.callback(_stop_fixture, client, client_job)
        daemon, job = _spawn_fixture(
            [sys.executable, "-u", "-m", "agent_bridge", "start"],
            cwd=root, env=_environment(root), stdout=log, stderr=log,
        )
        cleanup.callback(_stop_fixture, daemon, job)
        last_probe = None
        while time.monotonic() < deadline:
            table = routing.read_table(root) or {}
            row = table.get("active")
            if isinstance(row, dict):
                endpoint = routing.Endpoint.from_dict(row)
                if endpoint is not None:
                    try:
                        with urllib.request.urlopen(
                            f"{endpoint.base_url}/health", timeout=0.25,
                        ) as response:
                            ready = json.load(response).get("ready")
                            last_probe = f"HTTP {response.status}, ready={ready!r}"
                            if ready is True:
                                break
                    except (OSError, ValueError) as exc:
                        last_probe = f"{type(exc).__name__}: {exc}"
            assert daemon.poll() is None, (root / "fixture.log").read_text()
            time.sleep(0.05)
        else:
            pytest.fail(
                f"fixture daemon never became ready: row={row!r}, probe={last_probe}; "
                f"{(root / 'fixture.log').read_text()}"
            )
        reference = process_exit.open_process(
            row["pid"], diagnostics.process_start_time(row["pid"]),
        )
        cleanup.callback(reference.close)
        if stopped:
            BridgeClient(endpoint.base_url, token, timeout=1).shutdown()
            assert reference.wait(2), "owned fixture did not stop before reconciliation"
            assert (routing.read_table(root) or {}).get("active") is None
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
                and path.name not in ("fixture.log", "client.out", "client.err")
            }
        remaining = deadline - time.monotonic()
        assert remaining > 0, "daemon startup exhausted the shared consumer budget"
        assert client.poll() is None, (root / "client.err").read_text()
        assert client.stdin is not None
        client.stdin.write(b"\n")
        client.stdin.close()
        client.wait(timeout=min(12, remaining))
        stdout = (root / "client.out").read_text()
        stderr = (root / "client.err").read_text()
        assert client.returncode == 0, stdout + stderr
        assert json.loads(stdout)["serving_reconcile"] == (
            "inactive-or-forwarded" if stopped else "current"
        )
        if stopped:
            assert {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
                and path.name not in ("fixture.log", "client.out", "client.err")
            } == before
        else:
            assert (routing.read_table(root) or {}).get("active") == row
        assert not (root / "update-in-progress").exists()
        assert reference.wait(0) is stopped
        if not stopped:
            BridgeClient(endpoint.base_url, token, timeout=1).shutdown()
            assert reference.wait(2), "owned fixture daemon did not gracefully stop"
