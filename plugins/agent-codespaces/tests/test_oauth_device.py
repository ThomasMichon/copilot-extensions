"""Device OAuth uses vendor endpoints, never a shared CLI keyring mutation."""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError
from unittest.mock import Mock

import pytest

from agent_codespaces import oauth_device


def test_real_header_construction_and_redirect_rejection(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"login":"example-user"}'
    response.headers = {"X-OAuth-Scopes": "codespace"}
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(oauth_device, "build_opener", lambda *_: opener)
    assert oauth_device.verify("synthetic-credential", "example-user")["status"] == "ready"
    request = opener.open.call_args.args[0]
    assert request.get_header("Authorization").split(" ", 1) == [
        "".join(("Bear", "er")), "synthetic-credential",
    ]
    assert request.full_url == "https://api.github.com/user"
    with pytest.raises(RuntimeError, match="redirect"):
        oauth_device._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


def test_device_flow_pending_slowdown_and_success(monkeypatch):
    responses = iter([
        ({"device_code": "private-device", "user_code": "ABCD-EFGH",
          "verification_uri": "https://github.com/login/device", "interval": 5,
          "expires_in": 900}, {}),
        ({"error": "authorization_pending"}, {}),
        ({"error": "slow_down"}, {}),
        ({"access_token": "synthetic-credential"}, {}),
    ])
    calls = []
    sleeps = []
    monkeypatch.setattr(oauth_device.time, "sleep", sleeps.append)

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(oauth_device, "_request", request)
    messages = []
    assert oauth_device.acquire(messages.append) == "synthetic-credential"
    assert sleeps == [5, 5, 10]
    assert "ABCD-EFGH" in messages[0] and "https://github.com/login/device" in messages[0]
    assert all("private-device" not in message and "synthetic-credential" not in message for message in messages)
    assert calls[0][1]["fields"]["client_id"] == oauth_device.CLIENT_ID
    assert calls[-1][1]["fields"]["grant_type"].endswith("device_code")


@pytest.mark.parametrize("error", ["access_denied", "expired_token"])
def test_device_denial_and_expiration_are_errors(monkeypatch, error):
    responses = iter([
        ({"device_code": "private", "user_code": "ABCD-EFGH",
          "verification_uri": "https://github.com/login/device", "interval": 1}, {}),
        ({"error": error}, {}),
    ])
    monkeypatch.setattr(oauth_device, "_request", lambda *a, **kw: next(responses))
    monkeypatch.setattr(oauth_device.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match=error):
        oauth_device.acquire(lambda _: None)


def test_unauthorized_response_has_no_credential_body_in_error(monkeypatch):
    opener = Mock()
    opener.open.side_effect = HTTPError(
        "https://api.github.com/user", 401, "unauthorized", {}, io.BytesIO(b"synthetic-secret"),
    )
    monkeypatch.setattr(oauth_device, "build_opener", lambda *_: opener)
    with pytest.raises(oauth_device.CredentialUnavailable) as error:
        oauth_device.verify("synthetic-credential")
    assert "synthetic" not in str(error.value)


def test_malformed_verification_url_never_polls(monkeypatch):
    request = Mock(return_value=({
        "device_code": "private", "user_code": "ABCD",
        "verification_uri": "https://example.com",
    }, {}))
    monkeypatch.setattr(oauth_device, "_request", request)
    with pytest.raises(RuntimeError, match="verification URL"):
        oauth_device.acquire(lambda _: None)
    assert request.call_count == 1


def test_post_body_is_vendor_device_flow_not_pat(monkeypatch):
    opener = Mock()
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps({"error": "authorization_pending"}).encode()
    response.headers = {}
    opener.open.return_value = response
    monkeypatch.setattr(oauth_device, "build_opener", lambda *_: opener)
    oauth_device._request("https://github.com/login/device/code", fields={
        "client_id": oauth_device.CLIENT_ID, "scope": "codespace",
    })
    request = opener.open.call_args.args[0]
    assert request.data and b"client_id=" in request.data
    assert request.get_header("Authorization") is None
