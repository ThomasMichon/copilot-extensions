"""Separate real OS processes; owned bounded fixtures, never production credentials."""

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fleet_manager.gateway_contract import GatewayError
from fleet_manager.planning import digest
from gateway_fixtures import Fixtures, credential_request

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "libs" / "agent-procutil" / "src"))
from agent_procutil import no_window_kwargs

SCRIPT = Path(__file__).with_name("gateway_process_fixture.py")


@contextmanager
def owned(root, role):
    env = dict(os.environ)
    env.pop("COPILOT_AGENT_SESSION_ID", None)
    process = subprocess.Popen([sys.executable, str(SCRIPT), role, str(root)], cwd=root,
                               env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, **no_window_kwargs())
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            out, err = process.communicate(timeout=6)
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate(timeout=6)
        assert process.poll() is not None
        assert "SYNTHETIC-SECRET" not in out + err


def wait_ready(root, role, process):
    path = root / f"{role}-ready.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        if process.poll() is not None:
            out, err = process.communicate(timeout=2)
            assert "SYNTHETIC-SECRET" not in out + err
            pytest.fail(f"fixture exited: {out} {err}")
        time.sleep(0.05)
    process.terminate()
    out, err = process.communicate(timeout=6)
    assert "SYNTHETIC-SECRET" not in out + err
    pytest.fail(f"owned {role} fixture did not become responsive: {out} {err}")


def completed(process, expected):
    out, err = process.communicate(timeout=10)
    assert process.returncode == 0, (out, err)
    assert json.loads(out)["state"] == expected
    assert "SYNTHETIC-SECRET" not in out + err


def test_separate_gateway_backend_controller_worker_and_restart(tmp_path):
    fixture = Fixtures(tmp_path)
    fixture.value["lease_seconds"] = 6
    fixture.save()
    with owned(tmp_path, "backend") as backend:
        endpoint = wait_ready(tmp_path, "backend", backend)
        fixture.value["backends"][0]["endpoint"] = f'https://127.0.0.1:{endpoint["port"]}'
        fixture.save()
        with owned(tmp_path, "gateway") as gateway:
            first = wait_ready(tmp_path, "gateway", gateway)
            sdk = fixture.client("alice", first["port"])
            assert sdk.connect()["state"] == "ADMITTED"  # Real process responsiveness, not a PID check.
            with owned(tmp_path, "controller") as controller:
                assert wait_ready(tmp_path, "controller", controller)["state"] == "SUBSCRIBED"
                with owned(tmp_path, "query") as query_process:
                    completed(query_process, "SCOPED")
                with owned(tmp_path, "worker") as worker_process:
                    completed(worker_process, "ACKED")
                gateway.terminate()
                gateway.wait(timeout=6)
                # A disconnected coordinator cannot turn a ready/in-flight response into success.
                with pytest.raises(GatewayError):
                    sdk.query({"request_id": "after-crash"})
        (tmp_path / "gateway-ready.json").unlink()
        with owned(tmp_path, "gateway") as gateway:
            second = wait_ready(tmp_path, "gateway", gateway)
            assert second["epoch"] != first["epoch"]
            with owned(tmp_path, "probe") as probe:
                completed(probe, "UNKNOWN")
            worker = fixture.client("worker", second["port"])
            worker.connect()
            with pytest.raises(GatewayError, match="controller-unavailable"):
                worker.request_credential(credential_request("new-request"))


def test_controller_crash_lease_and_restart_are_truthful(tmp_path):
    fixture = Fixtures(tmp_path)
    fixture.value["lease_seconds"] = 3
    fixture.save()
    with owned(tmp_path, "gateway") as gateway:
        ready = wait_ready(tmp_path, "gateway", gateway)
        with owned(tmp_path, "controller") as controller:
            wait_ready(tmp_path, "controller", controller)
            controller.terminate()
            controller.wait(timeout=6)
        worker = fixture.client("worker", ready["port"])
        worker.connect()
        request = credential_request()
        # Submission may still be pending under the last bounded lease, never SUCCESS.
        worker.request_credential(request)
        time.sleep(3.1)
        assert worker.credential_result(request["request_id"], digest(request))["state"] == "UNAVAILABLE"
        with pytest.raises(GatewayError, match="controller-unavailable"):
            worker.request_credential(credential_request("after-outage"))
        (tmp_path / "controller-ready.json").unlink()
        with owned(tmp_path, "controller") as controller:
            wait_ready(tmp_path, "controller", controller)
            fresh = credential_request("fresh-after-restart")
            worker.request_credential(fresh)
            deadline = time.monotonic() + 3
            state = "PENDING"
            while state == "PENDING" and time.monotonic() < deadline:
                state = worker.credential_result(fresh["request_id"], digest(fresh))["state"]
                time.sleep(0.05)
            assert state in ("DISPATCHED", "READY")


def test_default_sdk_import_is_light_and_side_effect_free(tmp_path):
    code = (
        "import sys; import fleet_manager.gateway_client; "
        "assert not {'jwt','jwcrypto','docker','agent_index'} & set(sys.modules); "
        "print('thin-sdk')"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=10, check=False, **no_window_kwargs())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "thin-sdk"
    assert list(tmp_path.iterdir()) == []


def test_process_fixture_does_not_import_test_framework(tmp_path):
    code = (
        f"import runpy, sys; sys.path.insert(0, {str(SCRIPT.parent)!r}); "
        f"runpy.run_path({str(SCRIPT)!r}, run_name='fixture_import'); "
        "assert 'pytest' not in sys.modules; print('fixture-import')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=tmp_path,
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=10, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "fixture-import"
    assert list(tmp_path.iterdir()) == []
