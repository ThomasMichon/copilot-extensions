"""Native identity checks preserve the scheme without leaking probe input."""

from __future__ import annotations

import io
import json
import os
import sys
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from agent_worktrees import update_auth as auth
from agent_worktrees import update_auth_probe as probe
from agent_worktrees import update_auth_process as process

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("scheme", ["Bear" + "er", "Ba" + "sic", "fixture-scheme"])
@pytest.mark.parametrize("host", ["GitHub.com", "GitHub.com:443", "enterprise.test:8443"])
def test_real_request_preserves_scheme_and_credential(monkeypatch, scheme, host):
    opener = Mock()
    opener.open.return_value = io.BytesIO(b'{"login": "mapped"}')
    factory = Mock(return_value=opener)
    monkeypatch.setattr(probe, "build_opener", factory)
    credential = "unmasked-fixture-value"

    assert probe.login(host, scheme, credential) == {"status": "ok", "login": "mapped"}

    request = opener.open.call_args.args[0]
    assert request.get_header("Authorization") == scheme + " " + credential
    assert opener.open.call_args.kwargs["timeout"] == 4
    expected = (
        "https://api.github.com/user" if host.casefold() in ("github.com", "github.com:443")
        else "https://" + host + "/api/v3/user"
    )
    assert request.full_url == expected
    assert isinstance(factory.call_args.args[0], probe._NoRedirect)


@pytest.mark.parametrize(("host", "scheme", "credential"), [
    ("example.test/path", "fixture", "value"),
    ("user@example.test", "fixture", "value"),
    ("example.test", "invalid scheme", "value"),
    ("example.test", "fixture", "value\r\nInjected: header"),
    ("example.test", "fixture", ""),
])
def test_invalid_native_headers_never_contact_a_server(monkeypatch, host, scheme, credential):
    factory = Mock()
    monkeypatch.setattr(probe, "build_opener", factory)
    assert probe.login(host, scheme, credential) == {"status": "denied"}
    factory.assert_not_called()


def test_native_credentials_are_not_forwarded_on_redirect():
    assert probe._NoRedirect().redirect_request(
        None, None, 302, "redirect", {}, "https://another.test/user",
    ) is None


@pytest.mark.parametrize(("failure", "status"), [
    (HTTPError("https://example.test", 401, "denied", {}, None), "denied"),
    (TimeoutError(), "timeout"),
    (URLError(TimeoutError()), "timeout"),
    (URLError("network unavailable"), "unavailable"),
])
def test_identity_failures_have_safe_structured_outcomes(monkeypatch, failure, status):
    opener = Mock()
    opener.open.side_effect = failure
    monkeypatch.setattr(probe, "build_opener", Mock(return_value=opener))
    assert probe.login("example.test", "fixture", "sensitive-fixture") == {"status": status}


@pytest.mark.parametrize("body", [
    b"not JSON", b"[]", b'{"login": ""}', b'{"login": 123}', b"x" * 65537,
], ids=["malformed", "array", "empty-login", "non-string-login", "oversized"])
def test_invalid_or_oversized_identity_response_is_not_success(monkeypatch, body):
    opener = Mock()
    opener.open.return_value = io.BytesIO(body)
    monkeypatch.setattr(probe, "build_opener", Mock(return_value=opener))
    assert probe.login("example.test", "fixture", "value")["status"] != "ok"


def test_isolated_probe_process_does_not_echo_rejected_credentials():
    value = "sensitive-fixture-not-output"
    result = process.run(
        [sys.executable, "-I", "-X", "utf8", "-m", "agent_worktrees.update_auth_probe"],
        input=json.dumps({
            "host": "example.test", "authtype": "invalid scheme", "credential": value,
        }),
        env=os.environ,
    )
    assert result.status == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "denied"}
    assert value not in result.stdout + result.stderr


def test_timed_out_native_identity_never_proves_account_or_contents():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return process.Result(0, '{"status":"ok","login":"mapped"}', timed_out=True)

    source = auth.source("fixture", "https://example.test/team/repo")
    result = auth._check_native(
        {"authtype": "fixture", "credential": "value"}, source, "mapped", {}, runner,
    )
    assert result == {"status": "timeout"}
    assert len(calls) == 1
