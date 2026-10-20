import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from agent_chat_service import ClientError
from agent_chat_service.transport import (
    GatewayTransport, STDIO_REQUEST, STDIO_RESPONSE, authorization, run_command,
)


def config(**fields):
    return {"endpoint": "http://127.0.0.1", "token_command": [sys.executable, "-c", "print('fixture')"],
            **fields}


def test_real_authorization_construction_preserves_the_supplied_value():
    token = "synthetic-value"
    prefix = "Bear" + "er "
    value = authorization(token)["Authorization"]
    assert value == prefix + token
    assert value.endswith(token) and "*" not in value


@pytest.mark.parametrize(
    "value", ["", "a\nb", "a b", "\N{SNOWMAN}", "x" * 32769],
    ids=["empty", "newline", "space", "unicode", "oversized"],
)
def test_invalid_credentials_are_rejected_without_echo(value):
    with pytest.raises(ClientError) as caught:
        authorization(value)
    assert caught.value.code == "invalid_credential"


@pytest.mark.parametrize("endpoint", [
    None, "", "file:///example", "http://example.invalid", "http://user:secret@localhost",
    "http://localhost/path", "http://localhost?", "http://localhost#", "http://localhost:0",
    "http://localhost:65536", "http://localhost\n", "http://localh\N{SNOWMAN}st",
])
def test_endpoint_selection_is_explicit_and_does_not_downgrade(endpoint):
    with pytest.raises(ClientError):
        GatewayTransport(config(endpoint=endpoint))


def test_real_owned_provider_keeps_stdin_stdout_stderr_separate():
    raw = run_command(
        [sys.executable, "-c",
         "import sys; data=sys.stdin.read(); sys.stderr.write('diagnostic'); print(data,end='')"],
        timeout=5, payload="Unicode \N{SNOWMAN}".encode(),
    )
    assert raw.decode() == "Unicode \N{SNOWMAN}"


def test_real_owned_provider_has_a_deadline():
    with pytest.raises(ClientError) as caught:
        run_command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=.1)
    assert caught.value.code == "provider_timeout"


def test_missing_caller_death_capability_refuses_before_legacy_helper_execution(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Unsupported provider execution reached the helper")

    monkeypatch.setitem(
        sys.modules, "agent_procutil.owned_process", SimpleNamespace(run_owned=unexpected),
    )
    with pytest.raises(ClientError) as caught:
        run_command([str(tmp_path / "native-provider")], timeout=1)
    assert caught.value.code == "provider_unsupported"
    assert "run_caller_owned" in caught.value.message


def test_owned_provider_passes_separate_capture_budgets_and_stdin(monkeypatch):
    import agent_procutil.owned_process as owned

    calls = []

    def bounded(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="reply")

    monkeypatch.setattr(owned, "run_caller_owned", bounded)
    assert run_command([sys.executable], timeout=2, payload=b"request") == b"reply"
    assert calls == [([sys.executable], {
        "timeout": 2, "input_text": "request",
        "max_stdout_bytes": 1048576, "max_stderr_bytes": 65536,
    })]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_real_provider_overflow_refuses_before_its_deadline_without_disclosing_output(stream):
    with pytest.raises(ClientError) as caught:
        run_command([
            sys.executable, "-c",
            f"import sys,time; sys.{stream}.buffer.write(b'private' * 200000); "
            f"sys.{stream}.flush(); time.sleep(30)",
        ], timeout=5)
    assert caught.value.code == "invalid_response"
    assert stream in caught.value.message and "private" not in caught.value.message


def test_unconfirmed_cleanup_retains_exact_typed_custody(monkeypatch):
    import agent_procutil.owned_process as owned

    process, job = object(), object()
    failure = owned.OwnedCleanupError(process, job)

    def unconfirmed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(owned, "run_caller_owned", unconfirmed)
    with pytest.raises(owned.OwnedCleanupError) as caught:
        run_command([sys.executable], timeout=1)
    assert caught.value is failure
    assert caught.value.process is process and caught.value.job is job


def test_credential_provider_has_its_own_raw_byte_budget(monkeypatch):
    import agent_chat_service.transport as transport

    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        if len(calls) == 1:
            return b"fixture\n"
        return b'{"schema":"agent-chat.http-result","version":1,"status":200}\n{}'

    monkeypatch.setattr(transport, "_execute", execute)
    assert GatewayTransport(config()).call({"op": "members", "channel": "room"}) == {}
    assert calls[0][1] == {"timeout": 30, "payload": None, "max_capture": 32770}
    assert calls[1][1]["max_capture"] == 1048576 + 512
    assert calls[1][0][1:] == [
        "-I", "-m", "agent_chat_service.transport", "--http-worker",
    ]
    assert b'"credential":"fixture"' in calls[1][1]["payload"]
    assert "fixture" not in calls[1][0]


def test_owned_provider_exit_status_is_visible_without_its_stderr():
    with pytest.raises(ClientError) as caught:
        run_command(
            [sys.executable, "-c", "import sys; sys.stderr.write('private-diagnostic'); sys.exit(7)"],
            timeout=5,
        )
    assert caught.value.code == "provider_failed"
    assert "7" in str(caught.value) and "private-diagnostic" not in str(caught.value)


@pytest.mark.parametrize("argv", [[], ["relative"], ["x\n"], [str(Path(sys.executable).with_suffix(".cmd"))]])
def test_incomplete_or_implicit_shell_commands_refuse(argv):
    with pytest.raises(ClientError):
        GatewayTransport(config(token_command=argv))

def test_cross_os_provider_is_refused_before_execution():
    program = "wsl.exe" if os.name == "nt" else "windows-provider.exe"
    with pytest.raises(ClientError) as caught:
        GatewayTransport(config(token_command=[str(Path(sys.executable).with_name(program))]))
    assert caught.value.code == "provider_unsupported"


def test_explicit_stdio_contract_is_bounded_and_has_no_hidden_credential_call():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return json.dumps({
            "schema": STDIO_RESPONSE, "version": 1, "status": 200, "result": {"members": []},
        }).encode()

    transport = GatewayTransport({
        "endpoint": "http://127.0.0.1",
        "request_command": [sys.executable, "-c", "print('unused')"],
    }, runner=runner)
    assert transport.call({"op": "members", "channel": "room"}) == {"members": []}
    assert len(calls) == 1
    envelope = json.loads(calls[0][1]["payload"])
    assert envelope == {
        "schema": STDIO_REQUEST, "version": 1, "endpoint": "http://127.0.0.1",
        "payload": {"op": "members", "channel": "room"},
    }
    assert calls[0][1]["timeout"] == 60


@pytest.mark.parametrize("reply", [
    b"not-json", b'{"schema":"a","schema":"b"}', b"[]",
    b'{"schema":"agent-chat.gateway-response","version":true,"status":200,"result":{}}',
])
def test_invalid_stdio_responses_are_not_retried(reply):
    calls = []

    def runner(*args, **kwargs):
        calls.append(True)
        return reply

    transport = GatewayTransport({
        "endpoint": "http://127.0.0.1", "request_command": [sys.executable],
    }, runner=runner)
    with pytest.raises(ClientError):
        transport.call({"op": "members", "channel": "room"})
    assert len(calls) == 1
