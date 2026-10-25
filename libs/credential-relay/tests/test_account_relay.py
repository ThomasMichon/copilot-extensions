import asyncio
import json
import sys
import time
import os
import subprocess
from pathlib import Path

import pytest

from credential_relay.account_relay import AccountRelay, publication_guard
from credential_relay.sources.gh_auth import GhAuthSource

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins" / "agent-index" / "src"))
from agent_index.source_account_relay import acquire


@pytest.fixture
def configured(tmp_path):
    directory = tmp_path / "account credentials"
    directory.mkdir()
    capability = directory / "worker capability"
    capability.write_text("capability-for-isolated-test-only-123456")
    fields = {
        "source": "github:example/repo", "account": "account-a", "recipient": "index-a",
        "host": "github.com", "action": "get-github-token",
    }
    grant = dict(fields, provider="selected", capability_file=str(capability),
                 expires_at=time.time() + 3600)
    policy = {
        "schema": "credential-relay.account-policy", "version": 1,
        "providers": {"selected": {"kind": "gh-auth", "account": "account-a",
                                   "host": "github.com"}},
        "grants": [grant],
    }
    filename = tmp_path / "policy.json"
    filename.write_text(json.dumps(policy))
    return filename, policy, fields, capability


@pytest.mark.asyncio
async def test_legacy_admission_is_closed_while_account_shutdown_is_pending(configured, monkeypatch):
    from credential_relay.server import CredentialRelayServer

    account = AccountRelay(str(configured[0]))
    relay = CredentialRelayServer(port=0, account_relay=account)
    await relay.start()
    port = relay.port
    close_account = account.stop
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_stop():
        entered.set()
        await release.wait()
        await close_account()

    monkeypatch.setattr(account, "stop", blocked_stop)
    stopping = asyncio.create_task(relay.stop())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
        except OSError:
            pass
        else:
            writer.close()
            await writer.wait_closed()
            pytest.fail("legacy admission remained open after shutdown started")
        assert not stopping.done()
    finally:
        release.set()
        await asyncio.wait_for(stopping, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    RuntimeError("account-relay-withdrawal-unconfirmed"), TimeoutError(),
])
async def test_account_shutdown_failure_still_closes_legacy_admission(configured, monkeypatch, failure):
    from credential_relay.server import CredentialRelayServer

    account = AccountRelay(str(configured[0]))
    relay = CredentialRelayServer(port=0, account_relay=account)
    await relay.start()
    port = relay.port
    close_account = account.stop

    async def failing_stop():
        raise failure

    monkeypatch.setattr(account, "stop", failing_stop)
    try:
        with pytest.raises(type(failure)):
            await relay.stop()
        assert relay._server is None
        with pytest.raises(OSError):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
    finally:
        await close_account()


@pytest.mark.asyncio
async def test_real_consumer_renewal_denial_expiry_disconnect(configured, monkeypatch, tmp_path):
    filename, policy, fields, capability = configured
    calls = []

    async def resolve(self, action, request, *, timeout):
        calls.append((self._account, action, request))
        return (
            f"protocol=https\nhost=github.com\nusername={self._account}\n"
            f"token=isolated-credential-{len(calls)}\n\n"
        )

    monkeypatch.setattr(GhAuthSource, "resolve", resolve)
    relay = AccountRelay(str(filename))
    await relay.start()
    port = relay._publication["port"]
    worker = tmp_path / "worker.json"
    binding = dict(fields, capability_file=str(capability))
    config = {
        "schema": "credential-relay.account-worker", "version": 1,
        "transport": {"host": "127.0.0.1", "port": port}, "bindings": [binding],
    }
    worker.write_text(json.dumps(config))

    async def consume():
        return await asyncio.to_thread(
            acquire, worker, source=fields["source"], account=fields["account"], timeout=2,
        )

    try:
        assert await consume() == "isolated-credential-1"
        assert await consume() == "isolated-credential-2"
        # A fresh worker interpreter exercises the installed-consumer shape,
        # not just the in-process provider callback.
        for _ in range(2):
            environment = dict(os.environ, PYTHONPATH=str(
                Path(__file__).resolve().parents[3] / "plugins" / "agent-index" / "src"
            ))
            child = await asyncio.create_subprocess_exec(
                sys.executable, "-S", "-c",
                "import sys; from pathlib import Path; "
                "from agent_index.source_account_relay import acquire; "
                "value=acquire(Path(sys.argv[1]),source=sys.argv[2],account=sys.argv[3]); "
                "assert value.startswith('isolated-credential-'); print('consumer-ok')",
                str(worker), fields["source"], fields["account"],
                env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                output, error = await asyncio.wait_for(child.communicate(), timeout=10)
                assert child.returncode == 0, error
                assert output.strip() == b"consumer-ok"
            finally:
                if child.returncode is None:
                    child.kill()
                    await child.communicate()
        for key in ("source", "account", "recipient", "host", "action"):
            changed = dict(fields)
            changed[key] = "different"
            assert await relay.resolve(changed, capability.read_text()) is None
        assert len(calls) == 4
        config["bindings"][0]["recipient"] = "wrong-recipient"
        worker.write_text(json.dumps(config))
        with pytest.raises(RuntimeError, match="source-account-relay-unavailable"):
            await consume()
        assert len(calls) == 4
        config["bindings"][0]["recipient"] = fields["recipient"]
        worker.write_text(json.dumps(config))
        # A holder cannot bypass the exact account policy via the old relay wire.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"get-github-token\nhost=github.com\n\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()
        policy["grants"][0]["expires_at"] = time.time() - 1
        filename.write_text(json.dumps(policy))
        with pytest.raises(RuntimeError, match="source-account-relay-unavailable"):
            await consume()
        assert len(calls) == 4
    finally:
        await relay.stop()
    assert not filename.with_name("account-relay-port.json").exists()
    with pytest.raises(RuntimeError, match="source-account-relay-unavailable"):
        await consume()


@pytest.mark.asyncio
async def test_policy_rotation_fences_inflight_credential(configured, monkeypatch):
    filename, policy, fields, capability = configured

    async def resolve(*args, **kwargs):
        policy["grants"] = []
        filename.write_text(json.dumps(policy))
        return "token=must-not-be-delivered\n\n"

    monkeypatch.setattr(GhAuthSource, "resolve", resolve)
    assert await AccountRelay(str(filename)).resolve(fields, capability.read_text()) is None


@pytest.mark.asyncio
async def test_stop_cancels_inflight_without_delivery(configured, monkeypatch):
    filename, _, fields, capability = configured
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def resolve(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(GhAuthSource, "resolve", resolve)
    relay = AccountRelay(str(filename))
    await relay.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", relay._publication["port"])
    wire = dict(fields, auth=capability.read_text())
    writer.write(("get-account-token\n" + "\n".join(f"{k}={v}" for k, v in wire.items())
                  + "\n\n").encode())
    await writer.drain()
    await asyncio.wait_for(entered.wait(), 2)
    server = relay._server
    original_wait = server.wait_closed

    async def wait_after_cancellation():
        assert cancelled.is_set()
        await original_wait()

    monkeypatch.setattr(server, "wait_closed", wait_after_cancellation)
    await relay.stop()
    assert cancelled.is_set()
    assert await asyncio.wait_for(reader.read(), 1) == b""
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_ambiguous_policy_and_wrong_capability_deny(configured, monkeypatch):
    filename, policy, fields, _ = configured
    calls = []
    monkeypatch.setattr(GhAuthSource, "resolve", lambda *a, **k: calls.append(1))
    relay = AccountRelay(str(filename))
    assert await relay.resolve(fields, "not-the-capability") is None
    policy["grants"].append(dict(policy["grants"][0]))
    filename.write_text(json.dumps(policy))
    assert await relay.resolve(fields, "capability-for-isolated-test-only-123456") is None
    assert not calls


@pytest.mark.asyncio
async def test_account_provider_ignores_ambient_tokens_without_global_switch(monkeypatch):
    calls = []
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.setenv(name, "wrong-ambient-value")
    monkeypatch.setenv("GH_HOST", "wrong.example")

    class Process:
        returncode = 0

        async def communicate(self):
            return b"isolated-account-result", b""

    async def spawn(*argv, **kwargs):
        calls.append((argv, kwargs))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = await GhAuthSource(account="account-a").resolve(
        "get-github-token", {"host": "github.com"},
    )
    assert "username=account-a" in result
    argv, kwargs = calls[0]
    assert argv == ("gh", "auth", "token", "--hostname", "github.com", "--user", "account-a")
    assert not any(name in kwargs["env"] for name in (
        "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST",
    ))
    assert os.environ["GH_TOKEN"] == "wrong-ambient-value"


@pytest.mark.asyncio
async def test_legacy_listener_never_dispatches_account_mode(configured, monkeypatch):
    from credential_relay.server import CredentialRelayServer

    filename, _, fields, capability = configured
    account = AccountRelay(str(filename))

    async def forbidden(*args, **kwargs):
        pytest.fail("legacy listener dispatched an untracked account acquisition")

    monkeypatch.setattr(account, "resolve", forbidden)
    relay = CredentialRelayServer(port=0, account_relay=account)
    await relay.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", relay.port)
        values = dict(fields, auth=capability.read_text())
        writer.write(("get-account-token\n" + "\n".join(
            f"{key}={value}" for key, value in values.items()
        ) + "\n\n").encode())
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await relay.stop()


@pytest.mark.asyncio
async def test_publication_and_conditional_withdrawal_share_one_guard(configured, monkeypatch):
    filename, _, _, _ = configured
    old = AccountRelay(str(filename))
    successor = AccountRelay(str(filename))
    await old.start()
    await successor.prepare()
    publication = filename.with_name("account-relay-port.json")
    original_read = Path.read_text
    interleaved = []

    def attempt_publication_between_read_and_unlink(path, *args, **kwargs):
        value = original_read(path, *args, **kwargs)
        if path == publication and not interleaved:
            interleaved.append(True)
            with pytest.raises(RuntimeError, match="publication-busy"):
                successor.activate()
        return value

    monkeypatch.setattr(Path, "read_text", attempt_publication_between_read_and_unlink)
    try:
        await old.stop()
        assert interleaved
        successor.activate()
        marker = json.loads(publication.read_text())
        await old.stop()
        assert json.loads(publication.read_text()) == marker
    finally:
        await old.stop()
        await successor.stop()


def test_publication_guard_excludes_an_independent_process(configured):
    filename, _, _, _ = configured
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    command = [
        sys.executable, "-c",
        "import sys; from credential_relay.account_relay import publication_guard; "
        "guard=publication_guard(sys.argv[1]); guard.__enter__(); guard.__exit__(None,None,None)",
        str(filename),
    ]
    kwargs = {
        "env": environment, "capture_output": True, "timeout": 10,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    with publication_guard(filename):
        assert subprocess.run(command, **kwargs).returncode != 0
    assert subprocess.run(command, **kwargs).returncode == 0
