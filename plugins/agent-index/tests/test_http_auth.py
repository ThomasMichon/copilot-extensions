from __future__ import annotations

import sys
import subprocess

import httpx
import pytest

from agent_index import __main__ as cli
from agent_index import config, http_auth


def policy(monkeypatch, **changes):
    value = {
        "endpoint": "https://index.example.test/api/index",
        "token_command": [sys.executable, "-c", "print('synthetic-read-credential')"],
        "installation_id": "remote-installation",
    }
    value.update(changes)
    monkeypatch.setattr(config, "_load_yaml", lambda _: {"http_auth": value})
    return value


def test_real_header_construction_and_read_contract(monkeypatch):
    policy(monkeypatch)
    monkeypatch.setattr(http_auth, "_credential", lambda _: "synthetic-read-credential")
    real_client = httpx.Client
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"available": True, "hits": []})

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    remote = http_auth.read_client("https://index.example.test/api/index")
    assert remote.search("query", limit=2, source="git:example", language=None, repo=None) == {
        "available": True, "hits": [],
    }
    assert seen[0].headers["Authorization"] == "".join(
        ["Bear", "er ", "synthetic-read-credential"]
    )
    assert seen[0].headers["X-Agent-Index-Installation-Id"] == "remote-installation"
    assert seen[0].url.path == "/api/index/search"
    assert seen[0].url.params["source"] == "git:example"
    with pytest.raises(RuntimeError, match="cannot perform lifecycle"):
        remote.shutdown()
    assert len(seen) == 1


@pytest.mark.parametrize("change", [
    {"endpoint": "https://different.example.test"},
    {"token_command": ["relative-command"]},
    {"token_command": [""]},
    {"token_command": "not-an-argv"},
    {"installation_id": ""},
    {"installation_id": "invalid\nidentity"},
    {"unexpected": True},
])
def test_invalid_policy_refuses_before_credentials(monkeypatch, change):
    policy(monkeypatch, **change)
    monkeypatch.setattr(
        http_auth, "_credential",
        lambda _: pytest.fail("invalid policy must not execute a credential command"),
    )
    with pytest.raises(http_auth.HttpAuthError):
        http_auth.read_client("https://index.example.test/api/index")


@pytest.mark.parametrize("endpoint", [
    "http://index.example.test",
    "https://user:password@index.example.test",
    "https://index.example.test?credential=value",
    "https://index.example.test#fragment",
    "https://index.example.test:99999",
    "https://[invalid",
])
def test_unsafe_endpoint_refuses_before_credentials(monkeypatch, endpoint):
    policy(monkeypatch, endpoint=endpoint)
    monkeypatch.setattr(
        http_auth, "_credential",
        lambda _: pytest.fail("unsafe endpoints must not execute a credential command"),
    )
    with pytest.raises(http_auth.HttpAuthError):
        http_auth.read_client(endpoint)


def test_unselected_native_client_stays_unchanged(monkeypatch):
    monkeypatch.setattr(config, "_load_yaml", lambda _: {})
    monkeypatch.setattr(
        http_auth, "_credential", lambda _: pytest.fail("credentials are opt-in"),
    )
    assert http_auth.read_client("http://native").read_headers is None
    with pytest.raises(http_auth.HttpAuthError, match="configured client endpoint is unavailable"):
        http_auth.read_client(None)


@pytest.mark.parametrize("status", [302, 401])
def test_remote_denial_or_redirect_never_retries_another_endpoint(monkeypatch, status):
    policy(monkeypatch)
    monkeypatch.setattr(http_auth, "_credential", lambda _: "synthetic-read-credential")
    real_client = httpx.Client
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"Location": "https://different.example.test"},
            json={"error": "denied"},
        )

    monkeypatch.setattr(
        httpx, "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    remote = http_auth.read_client("https://index.example.test/api/index")
    with pytest.raises(httpx.HTTPStatusError):
        remote.search("query", limit=1, source=None, language=None, repo=None)
    assert len(calls) == 1
    assert calls[0].url.host == "index.example.test"


def test_remote_status_uses_remote_identity_not_local_process_ownership(monkeypatch):
    policy(monkeypatch)
    monkeypatch.setattr(cli, "client_url", lambda: "https://index.example.test/api/index")
    monkeypatch.setattr("agent_index.transport.plan_route", lambda: ("client", {}))
    monkeypatch.setattr(
        cli, "_owned_service_status",
        lambda *a, **k: pytest.fail("remote reads cannot claim local process ownership"),
    )

    class Remote:
        read_headers = {"Authorization": "synthetic"}
        installation_id = "remote-installation"

        def health(self):
            return {"status": "ok", "installationId": self.installation_id}

        def status(self):
            return {
                "installationId": self.installation_id,
                "index": {"available": True, "chunks": 3},
            }

    remote = Remote()
    monkeypatch.setattr(http_auth, "read_client", lambda _: remote)
    result = cli._status_payload()
    assert result["state"] == "ready"
    assert result["role"] == "client"
    assert result["index"]["chunks"] == 3
    remote.installation_id = "different"
    monkeypatch.setattr(
        remote, "health", lambda: {"status": "ok", "installationId": "remote-installation"},
    )
    assert cli._status_payload()["state"] == "unreachable"


def test_remote_status_sanitizes_credential_failure(monkeypatch):
    policy(monkeypatch)
    monkeypatch.setattr(cli, "client_url", lambda: "https://index.example.test/api/index")
    monkeypatch.setattr("agent_index.transport.plan_route", lambda: ("client", {}))

    def fail(_):
        raise http_auth.HttpAuthError("http-auth-command-failed")

    monkeypatch.setattr(http_auth, "read_client", fail)
    result = cli._status_payload()
    assert result["state"] == "unreachable"
    assert result["index"]["chunks"] is None
    assert result["error"] == "http-auth-command-failed"


def test_credential_uses_bounded_caller_owned_execution(monkeypatch):
    def execute(command, **kwargs):
        assert command == ["credential-provider"]
        assert kwargs == {
            "timeout": 30, "max_stdout_bytes": 16386, "max_stderr_bytes": 4096,
        }
        return subprocess.CompletedProcess(command, 0, "synthetic-credential\n", "")

    monkeypatch.setattr(http_auth, "run_caller_owned", execute)
    assert http_auth._credential(["credential-provider"]) == "synthetic-credential"


def test_containment_failure_is_sanitized_in_status(monkeypatch):
    policy(monkeypatch)
    monkeypatch.setattr(cli, "client_url", lambda: "https://index.example.test/api/index")
    monkeypatch.setattr("agent_index.transport.plan_route", lambda: ("client", {}))

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-sensitive-containment-diagnostic")

    monkeypatch.setattr(http_auth, "run_caller_owned", fail)
    result = cli._status_payload()
    assert result["state"] == "unreachable"
    assert result["error"] == "http-auth-command-failed"
