from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_chat_service import Client, ClientError
from agent_chat_service.cli import SCAN_CAPABILITY, main
from agent_chat_service.transport import GatewayTransport
from agent_procutil.owned_process import run_caller_owned


def configuration(root: Path, endpoint: str, *, codename="agent-a") -> Path:
    path = root / (codename + ".json")
    path.write_text(json.dumps({
        "schema": "agent-chat.client", "version": 1,
        "endpoint": endpoint, "actor": {"codename": codename},
        "state_dir": str(root / "state"),
        "token_command": [sys.executable, "-c", "print('synthetic-credential')"],
    }), encoding="utf-8")
    return path


def cli(path, *args, text=None, expected=0):
    result = run_caller_owned(
        [sys.executable, "-m", "agent_chat_service.cli", "--config", str(path), *args],
        input_text=text, timeout=20,
        max_stdout_bytes=4 * 1024 * 1024, max_stderr_bytes=4 * 1024 * 1024,
    )
    assert result.returncode == expected, (result.stdout, result.stderr)
    return json.loads(result.stdout if expected == 0 else result.stderr)


def parsed_cli(path, *args, text=None):
    output, error = io.StringIO(), io.StringIO()
    with io.TextIOWrapper(io.BytesIO((text or "").encode("utf-8")), encoding="utf-8") as stream:
        with redirect_stdout(output), redirect_stderr(error), patch.object(sys, "stdin", stream):
            result = main(["--config", str(path), *args])
    assert result == 0, (output.getvalue(), error.getvalue())
    return json.loads(output.getvalue())


def test_real_cli_provider_http_store_and_independent_forward_read(
    rpc_endpoint, tmp_path, installed_chat, fixed_rpc_provider,
):
    path = configuration(tmp_path, rpc_endpoint.endpoint)
    other = configuration(tmp_path, rpc_endpoint.endpoint, codename="agent-b")
    for config_path in (path, other):
        config = json.loads(config_path.read_text(encoding="utf-8"))
        del config["token_command"]
        config["request_command"] = fixed_rpc_provider
        config_path.write_text(json.dumps(config), encoding="utf-8")
    assert all(not (installed_chat.root / name).exists() for name in (
        "source", "helper-source", "lease-source", "endpoint-source",
    ))
    catalog = json.loads(installed_chat.run([str(installed_chat.console), "catalog"], cwd=tmp_path))

    def installed(config_path, *args, text=None, console=False):
        argv = [str(installed_chat.console)] if console else catalog["commands"][0]["argv"]
        output = installed_chat.run(
            [*argv, "--config", str(config_path), *args],
            cwd=tmp_path, input_text=text, timeout=20,
        )
        return json.loads(output)

    for name in ("agent-a", "agent-b"):
        rpc_endpoint.store.dispatch({"op": "join", "channel": "room", "actor": {"codename": name}})
    event = installed(path, "post", "room", "-", "--request-id", "request-1",
                      text="Unicode \N{SNOWMAN}", console=True)["event"]
    assert installed(other, "read", "room")["messages"] == [event]
    assert installed(other, "read", "room")["messages"] == []
    independent = rpc_endpoint.store.dispatch({
        "op": "read", "channel": "room", "codename": "agent-a",
    })
    assert independent["cursor"] == 0 and independent["messages"] == [event]
    assert rpc_endpoint.state.authenticated == len(rpc_endpoint.state.requests)


@pytest.mark.parametrize("args, method, positional, controls, text", [
    (["members", "room"], "members", ["room"], {"limit": None, "after": None}, None),
    (["members", "room", "--limit", "1"], "members", ["room"], {"limit": 1, "after": None}, None),
    (["members", "room", "--after", "agent-a", "--limit", "1"],
     "members", ["room"], {"limit": 1, "after": "agent-a"}, None),
    (["wait", "room", "--timeout", ".01", "--batch-ms", "0"],
     "wait", ["room"], {"timeout": .01, "batch_ms": 0}, None),
    (["resolve", "agent-a"], "resolve", ["agent-a"], {}, None),
    (["scan", "--request-file", "-"], "scan", [],
     {"epoch": None, "after": [{"channel": "room", "event_id": 0}], "timeout": 0},
     '{"epoch":null,"after":[{"channel":"room","event_id":0}],"timeout":0}'),
])
def test_cli_dispatch_preserves_exact_client_controls(args, method, positional, controls, text):
    calls = []

    def invoke(*values, **fields):
        calls.append((list(values), fields))
        return {"dispatched": method}

    with patch("agent_chat_service.cli.configured", return_value=SimpleNamespace(**{method: invoke})):
        assert parsed_cli("explicit-fixture", *args, text=text) == {"dispatched": method}
    assert calls == [(positional, controls)]


def test_cli_lost_post_response_retains_original_and_explicit_recovery_deduplicates(rpc_endpoint, tmp_path):
    path = configuration(tmp_path, rpc_endpoint.endpoint)
    cli(path, "join", "room")
    rpc_endpoint.state.mode = "drop-post"
    error = cli(path, "post", "room", "original", "--request-id", "original-request", expected=1)
    assert error["error"] == "post_unconfirmed"
    assert cli(path, "status")["pending_posts"] == [
        {"channel": "room", "request_id": "original-request"},
    ]
    assert len([request for request in rpc_endpoint.state.requests if request["op"] == "post"]) == 1
    result = cli(path, "post", "room", "original", "--request-id", "original-request")
    assert result["duplicate"]
    assert cli(path, "status")["pending_posts"] == []
    assert rpc_endpoint.store._db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_offline_status_and_capability_never_execute_provider_or_network(tmp_path):
    path = configuration(tmp_path, "http://127.0.0.1:1")
    marker = tmp_path / "provider-was-called"
    config = json.loads(path.read_text("utf-8"))
    config["token_command"] = [
        sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker),
    ]
    path.write_text(json.dumps(config), encoding="utf-8")
    status = parsed_cli(path, "status")
    assert status["scope"] == "local_only" and not status["remote_verified"]
    assert not status["native_claims_verified"] and not marker.exists()
    assert parsed_cli(tmp_path / "missing", "scan", "--describe") == SCAN_CAPABILITY
    assert not marker.exists()

def test_actual_installed_cli_death_releases_reader_lock_before_outer_cleanup(
    rpc_endpoint, tmp_path, installed_chat, fixed_rpc_provider, caller_observer,
):
    from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

    path = configuration(tmp_path, rpc_endpoint.endpoint)
    config = json.loads(path.read_text("utf-8"))
    config["token_command"][0] = str(installed_chat.interpreter)
    path.write_text(json.dumps(config), encoding="utf-8")
    command = [str(installed_chat.console), "--config", str(path)]
    run = installed_chat.run
    assert json.loads(run([*command, "join", "room"], cwd=tmp_path))["membership"]["active"]
    fixture = tmp_path / "process-fixture.py"
    fixture.write_bytes(Path(caller_observer.fixture.__file__).read_bytes())
    provider = tmp_path / "observed-provider.py"
    provider.write_text("""
import sys
from pathlib import Path
fixture,original,endpoint,directory=sys.argv[1:]
root=Path(directory)
(root/"provider-entry").write_text("entered",encoding="ascii")
import json,runpy,subprocess
import agent_chat_service,agent_procutil
support=runpy.run_path(fixture)
subprocess.Popen(
    [sys.executable,"-I",fixture,"leaf",directory,"kill"],
    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
    **agent_procutil.no_window_kwargs(),
)
support["_write"](root,"provider",{
    "self":support["identity"](),"package":agent_chat_service.__file__,"prefix":sys.prefix,
})
sys.argv=[original,endpoint]
runpy.run_path(original,run_name="__main__")
""", encoding="utf-8")
    del config["token_command"]
    config["request_command"] = [
        str(installed_chat.interpreter), "-I", str(provider), str(fixture),
        fixed_rpc_provider[2], rpc_endpoint.endpoint, str(tmp_path),
    ]
    path.write_text(json.dumps(config), encoding="utf-8")
    event = rpc_endpoint.store.dispatch({
        "op": "post", "channel": "room", "codename": config["actor"]["codename"],
        "text": "retained", "request_id": "retained",
    })["event"]
    rpc_endpoint.state.mode = "hold-read"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    process, job = spawn_sync_in_kill_on_close_job(
        [*command, "read", "room"],
        require_job=True, allow_breakaway=False, env=environment, cwd=tmp_path,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    pins = {}
    try:
        ready_deadline = time.monotonic() + 5
        assert rpc_endpoint.state.read_entered.wait(5), (
            "Installed read did not reach the fixture before its existing synchronization bound; "
            f"console_returncode={process.poll()}; "
            f"provider_entered={(tmp_path / 'provider-entry').exists()}; "
            f"provider_ready={bool(caller_observer._read(tmp_path / 'provider.json'))}; "
            f"leaf_ready={bool(caller_observer._read(tmp_path / 'leaf.json'))}; "
            f"descendant_pids={[child.pid for child in caller_observer.psutil.Process(process.pid).children(recursive=True)]}."
        )
        def ready():
            records = [
                caller_observer._read(tmp_path / f"{role}.json") for role in ("provider", "leaf")
            ]
            return records if all(records) else None

        provider_record, leaf_record = caller_observer._wait(
            ready,
            max(0, ready_deadline - time.monotonic()), "installed provider/leaf not ready",
        )
        assert leaf_record["parent"] == caller_observer.fixture.identity(
            leaf_record["parent"]["pid"],
        )
        assert Path(provider_record["package"]).resolve().is_relative_to(
            installed_chat.target.resolve(),
        )
        assert Path(provider_record["prefix"]).resolve() == installed_chat.target.resolve()
        chain = caller_observer.psutil.Process(leaf_record["self"]["pid"])
        identities = [leaf_record["self"], provider_record["self"]]
        for parent in chain.parents():
            identities.append(caller_observer.fixture.identity(parent.pid))
            if parent.pid == process.pid:
                break
        else:
            raise AssertionError("installed provider is not the actual console's descendant")
        assert provider_record["self"] in identities, (
            "leaf ancestry does not contain the birth-identified installed provider"
        )
        for identity in identities:
            if identity["pid"] not in pins:
                pins[identity["pid"]] = caller_observer.PinnedProcess(identity)
        assert not any(pin.gone() for pin in pins.values())
        process.kill()
        process.wait(timeout=5)
        caller_observer._wait(
            lambda: all(pin.gone() for pin in pins.values()), 3,
            "installed CLI/provider/descendant survived before observer cleanup",
        )
        assert json.loads(run([*command, "status"], cwd=tmp_path))["pending_posts"] == []
        assert time.monotonic() < rpc_endpoint.state.read_hold_deadline, (
            "Caller death and lock reacquisition were not proved before the hold watchdog expired."
        )
        rpc_endpoint.state.release_read.set()
        assert rpc_endpoint.store.dispatch({
            "op": "read", "channel": "room", "codename": "agent-a",
        })["cursor"] == 0
        # The actual CLI is dead; the outer fixture Job is deliberately still open.
        assert json.loads(run([*command, "read", "room"], cwd=tmp_path))["messages"] == [event]
        assert rpc_endpoint.store.dispatch({
            "op": "read", "channel": "room", "codename": "agent-a",
        })["messages"] == []
    finally:
        rpc_endpoint.state.release_read.set()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if job is not None:
            job.close()
        for pin in pins.values():
            try:
                pin.kill()
            finally:
                pin.close()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize("mode", ["server-error", "redirect", "oversized", "wrong-type"])
def test_http_failures_are_not_retried_or_redirected(rpc_endpoint, mode):
    transport = GatewayTransport({
        "endpoint": rpc_endpoint.endpoint, "token_command": [sys.executable],
    }, runner=lambda *args, **kwargs: b"synthetic-credential")
    rpc_endpoint.state.mode = mode
    with pytest.raises(ClientError):
        transport.call({"op": "members", "channel": "room"})
    assert len(rpc_endpoint.state.requests) == 1


def test_http_response_bound_is_separate_from_request_bound(rpc_endpoint):
    transport = GatewayTransport({
        "endpoint": rpc_endpoint.endpoint, "token_command": [sys.executable],
    }, runner=lambda *args, **kwargs: b"synthetic-credential")
    rpc_endpoint.state.mode = "large"
    assert len(transport.call({"op": "members", "channel": "room"})["value"]) == 65536


@pytest.mark.parametrize("mode", ["trickle-header", "trickle-body"])
def test_http_total_deadline_releases_viewer_lock_despite_continuing_traffic(
    rpc_endpoint, tmp_path, monkeypatch, mode,
):
    import io
    import agent_chat_service.transport as transport_module

    transport = GatewayTransport({
        "endpoint": rpc_endpoint.endpoint, "token_command": [sys.executable],
    }, runner=lambda *args, **kwargs: b"synthetic-credential")
    client = Client(transport, {"codename": "agent-a"}, tmp_path / "state")
    rpc_endpoint.store.dispatch({"op": "join", "channel": "room", "actor": client.actor})
    event = rpc_endpoint.store.dispatch({
        "op": "post", "channel": "room", "codename": client.actor["codename"],
        "text": "unread", "request_id": "unread-request",
    })["event"]
    monkeypatch.setattr(transport_module, "HTTP_ALLOWANCE", 3)
    rpc_endpoint.state.mode = mode
    started = time.monotonic()
    with pytest.raises(ClientError) as caught:
        client.read("room", io.StringIO())
    assert caught.value.code == "http_deadline"
    assert time.monotonic() - started < 8
    assert rpc_endpoint.state.trickle_progress.is_set()
    rpc_endpoint.state.stop_trickle.set()
    rpc_endpoint.state.mode = "normal"
    monkeypatch.setattr(transport_module, "HTTP_ALLOWANCE", 15)
    assert client.read("room", io.StringIO())["messages"] == [event]
    assert rpc_endpoint.store.dispatch({
        "op": "read", "channel": "room", "codename": client.actor["codename"],
    })["messages"] == []
