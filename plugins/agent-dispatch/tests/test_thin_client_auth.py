"""Thin HTTP clients must not import the coordinator's server framework."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from tests._helpers import TEST_REPO, RepoDefaultingQueue as TaskQueue


@pytest.fixture
def thin_client_server(tmp_path):
    from agent_dispatch.coordinator import create_app
    from tests.test_coordinator import _registration_machine
    from tests.test_mcp_http import _boot

    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("thin client fixture", origin_ref="review-emitter")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.register_registration(
        "emitter",
        {
            "id": "review-emitter", "repo": TEST_REPO,
            "command": ["echo", "fixture"], "interval_seconds": 60,
        },
        reg_id="fixture-sender", machine=_registration_machine(),
    )
    app = create_app(
        queue, control_token="fixture-control", enable_mcp=False, verification_interval=0,
    )
    proofs = []

    @app.middleware("http")
    async def observe_sender_proof(request, call_next):
        if request.url.path.endswith("/event-note"):
            proofs.append(request.headers.get("X-Agent-Dispatch-Sender-Proof"))
        return await call_next(request)

    url, stop = _boot(app)
    try:
        yield url, task.id, reservation.key, proofs
    finally:
        stop()


@pytest.mark.parametrize("mode", ["client", "list", "cleanup-history"])
def test_fresh_thin_client_auth_import_graph(thin_client_server, mode):
    url, task_id, key, _ = thin_client_server
    code = r'''
import json
import runpy
import sys
from agent_dispatch.client import DispatchClient

mode, url, task_id, key, repo = sys.argv[1:]
if mode == "client":
    with DispatchClient(url, control_token="fixture-control") as client:
        assert client.list(repo=repo)[0]["id"] == task_id
        assert client.cleanup_recovery_history(key) == []
else:
    args = ["agent-dispatch", "--url", url]
    if mode == "list":
        args += ["list", "--repo", repo]
    else:
        args += ["--control-token", "fixture-control", "reservations", "cleanup-history", key]
    sys.argv = args
    try:
        runpy.run_module("agent_dispatch", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
frameworks = ("fastapi", "pydantic", "pydantic_core", "uvicorn")
loaded = sorted(name for name in sys.modules if
    any(name == prefix or name.startswith(prefix + ".") for prefix in frameworks)
    or (name.startswith("agent_dispatch.coordinator") and name != "agent_dispatch.coordinator_cli"))
assert not loaded, "thin client imported server modules: " + repr(loaded[:12])
'''
    result = subprocess.run(
        [sys.executable, "-c", code, mode, url, task_id, key, TEST_REPO],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "AGENT_DISPATCH_SUPERVISE_MACHINE": "fixture-host"},
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    if mode != "client":
        payload = json.loads(result.stdout)
        if mode == "cleanup-history":
            assert payload == []
        else:
            assert payload[0]["id"] == task_id


@pytest.mark.parametrize("control,scope", [
    ("fixture-control", "event-note:fixture-sender"),
    ("", ""),
    ("clé", "event-note:émetteur"),
])
def test_scoped_control_token_vectors(control, scope):
    from agent_dispatch.coordinator_auth import scoped_control_token

    expected = hmac.new(control.encode("utf-8"), scope.encode("utf-8"), hashlib.sha256).hexdigest()
    assert scoped_control_token(control, scope) == expected


def test_scoped_control_token_legacy_export_is_shared():
    from agent_dispatch import coordinator_auth
    from agent_dispatch.control_token import scoped_control_token

    assert coordinator_auth.scoped_control_token is scoped_control_token


def test_thin_client_event_note_proof_over_real_http(thin_client_server):
    from agent_dispatch.client import DispatchClient

    url, task_id, _, proofs = thin_client_server
    with DispatchClient(url, control_token="fixture-control") as client:
        result = client.append_event_note(task_id, sender="fixture-sender", note="fixture note")
        events = client.events(task_id)
    expected = hmac.new(
        b"fixture-control", b"event-note:fixture-sender", hashlib.sha256,
    ).hexdigest()
    assert proofs == [expected]
    assert result["id"] == task_id
    assert events[-1]["note"] == "event note: fixture note"
