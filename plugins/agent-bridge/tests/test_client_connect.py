"""Tests for the CLI client connect-grace (stage 1 transient retry)."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import Mock, patch

import pytest

from agent_bridge.client import (
    DEFAULT_RESTART_GRACE,
    DEFAULT_SESSION_SETTLE_GRACE,
    BridgeClient,
    BridgeClientError,
    BridgeConnectionError,
)


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _not_found(detail: str = "Session not found") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1/api/v1/sessions/s1",
        404,
        "Not Found",
        {},
        io.BytesIO(json.dumps({"detail": detail}).encode()),
    )


class TestConnectGrace:
    def test_default_covers_a_sustained_restart(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok")
        assert client._connect_grace == DEFAULT_RESTART_GRACE == 30.0
        assert (
            client._session_settle_grace
            == DEFAULT_SESSION_SETTLE_GRACE
            == 5.0
        )

    def test_explicit_settle_grace_cannot_exceed_connect_grace(self) -> None:
        client = BridgeClient(
            "http://127.0.0.1:0",
            "tok",
            connect_grace=0.0,
            session_settle_grace=5.0,
        )
        assert client._session_settle_grace == 0.0

    def test_requests_share_one_continuous_outage_budget(self) -> None:
        client = BridgeClient(
            "http://127.0.0.1:0", "tok", connect_grace=2.0
        )
        calls = {"n": 0}
        clock = {"now": -1.0}

        def always_fail(_req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("refused")

        def tick():
            clock["now"] += 0.5
            return clock["now"]

        with (
            patch("agent_bridge.client.urllib.request.urlopen", side_effect=always_fail),
            patch("time.monotonic", side_effect=tick),
            patch("time.sleep"),
        ):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")
            first_request_calls = calls["n"]
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/api/v1/sessions")

        assert first_request_calls == 2
        assert calls["n"] == 3

    def test_session_settle_window_starts_at_first_404(self) -> None:
        client = BridgeClient(
            "http://127.0.0.1:0",
            "tok",
            connect_grace=10.0,
            session_settle_grace=2.0,
            reresolve=lambda: "http://127.0.0.1:0",
        )
        clock = {"now": -1.0}
        outcomes = iter(
            (
                urllib.error.URLError("refused"),
                urllib.error.URLError("refused"),
                urllib.error.URLError("refused"),
                _not_found(),
                _FakeResp({"id": "s1"}),
            )
        )

        def tick():
            clock["now"] += 0.5
            return clock["now"]

        def urlopen(_req, timeout=None):
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with (
            patch("agent_bridge.client.urllib.request.urlopen", side_effect=urlopen),
            patch("time.monotonic", side_effect=tick),
            patch("time.sleep") as sleep,
        ):
            result = client._request("GET", "/api/v1/sessions/s1")

        assert result == {"id": "s1"}
        assert [call.args[0] for call in sleep.call_args_list] == [
            0.25,
            0.5,
            1.0,
            0.25,
        ]

    def test_endpoint_changes_remain_inside_outage_budget(self) -> None:
        endpoints = iter(
            (
                "http://127.0.0.1:47000",
                "http://127.0.0.1:48000",
            )
        )
        client = BridgeClient(
            "http://127.0.0.1:46000",
            "tok",
            connect_grace=2.0,
            reresolve=lambda: next(endpoints),
        )
        clock = {"now": -1.0}
        calls = {"n": 0}

        def tick():
            clock["now"] += 3.0
            return clock["now"]

        def always_fail(_req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("refused")

        with (
            patch("agent_bridge.client.urllib.request.urlopen", side_effect=always_fail),
            patch("time.monotonic", side_effect=tick),
            patch("time.sleep"),
        ):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")

        assert calls["n"] == 2

    def test_replacement_allowance_does_not_renew_across_requests(self) -> None:
        client = BridgeClient(
            "http://127.0.0.1:46000",
            "tok",
            connect_grace=0.0,
            reresolve=Mock(
                side_effect=(
                    "http://127.0.0.1:47000",
                    "http://127.0.0.1:48000",
                )
            ),
        )
        calls = {"n": 0}

        def always_fail(_req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("refused")

        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            side_effect=always_fail,
        ):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/api/v1/sessions")

        assert calls["n"] == 3
        assert client._reresolve.call_count == 1

    def test_session_404_endpoint_churn_is_bounded(self) -> None:
        client = BridgeClient(
            "http://127.0.0.1:46000",
            "tok",
            connect_grace=0.0,
            reresolve=Mock(
                side_effect=(
                    "http://127.0.0.1:47000",
                    "http://127.0.0.1:48000",
                )
            ),
        )
        calls = {"n": 0}

        def not_found(_req, timeout=None):
            calls["n"] += 1
            raise _not_found()

        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            side_effect=not_found,
        ):
            with pytest.raises(BridgeClientError) as exc_info:
                client._request("GET", "/api/v1/sessions/s1")

        assert exc_info.value.status == 404
        assert calls["n"] == 2
        assert client._reresolve.call_count == 1

    def test_retries_then_succeeds(self) -> None:
        """A transient connection refusal within the grace window is retried."""
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=2.0)

        calls = {"n": 0}

        def flaky(_req, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("connection refused")
            return _FakeResp({"ok": True})

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=flaky):
            result = client._request("GET", "/health")

        assert result == {"ok": True}
        assert calls["n"] == 3  # two failures, then success

    def test_gives_up_after_grace(self) -> None:
        """Persistent refusal past the grace window raises BridgeConnectionError.

        #23: it must NOT sys.exit -- a SystemExit (BaseException) tunnels
        through the streaming engine's `except Exception` reconnect guards and
        kills a live dispatch. Raising a catchable Exception lets the engine
        reconnect, and one-shot commands surface it via main()'s top-level guard.
        """
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=0.3)

        def always_fail(_req, timeout=None):
            raise urllib.error.URLError("refused")

        with patch(
            "agent_bridge.client.urllib.request.urlopen", side_effect=always_fail
        ):
            with pytest.raises(BridgeConnectionError) as ei:
                client._request("GET", "/health")
        assert "127.0.0.1:0" in str(ei.value)

    def test_no_grace_fails_immediately(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=0.0)
        calls = {"n": 0}

        def always_fail(_req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("refused")

        with patch(
            "agent_bridge.client.urllib.request.urlopen", side_effect=always_fail
        ):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")
        assert calls["n"] == 1


class TestReresolveOnRejection:
    """follow-the-cutover: on a connection rejection, follow a dynamic-port cutover by
    re-resolving the routing table (listener-verified) instead of hammering the
    remembered-but-dead port."""

    def test_reresolves_to_new_port_and_succeeds(self) -> None:
        # Old port refuses; the re-resolver reports the daemon moved to a new
        # port; the request switches to it and succeeds -- no grace needed.
        new_base = "http://127.0.0.1:47000"
        client = BridgeClient(
            "http://127.0.0.1:57585",
            "tok",
            connect_grace=0.0,
            reresolve=lambda: new_base,
        )
        seen: list[str] = []

        def by_port(req, timeout=None):
            seen.append(req.full_url)
            if req.full_url.startswith("http://127.0.0.1:57585"):
                raise urllib.error.URLError("refused")
            return _FakeResp({"ok": True})

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=by_port):
            result = client._request("GET", "/health")

        assert result == {"ok": True}
        assert client._base == new_base  # switched permanently for later calls
        assert seen[0].startswith("http://127.0.0.1:57585")  # tried old first
        assert seen[-1].startswith("http://127.0.0.1:47000")  # then the new one

    def test_idempotent_lookup_reresolves_after_connection_reset(self) -> None:
        old_base = "http://127.0.0.1:57585"
        new_base = "http://127.0.0.1:47000"
        client = BridgeClient(
            old_base,
            "tok",
            connect_grace=0.0,
            reresolve=lambda: new_base,
        )
        seen: list[str] = []

        def reset_old_endpoint(req, timeout=None):
            seen.append(req.full_url)
            if req.full_url.startswith(old_base):
                raise urllib.error.URLError(
                    ConnectionResetError("daemon generation retired")
                )
            return _FakeResp({"session_id": "s1"})

        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            side_effect=reset_old_endpoint,
        ):
            result = client._request("GET", "/api/v1/sessions/s1")

        assert result == {"session_id": "s1"}
        assert seen == [
            f"{old_base}/api/v1/sessions/s1",
            f"{new_base}/api/v1/sessions/s1",
        ]

    def test_non_idempotent_request_is_not_retried_after_reset(self) -> None:
        reresolves = {"count": 0}

        def reresolve():
            reresolves["count"] += 1
            return "http://127.0.0.1:47000"

        client = BridgeClient(
            "http://127.0.0.1:57585",
            "tok",
            connect_grace=30.0,
            reresolve=reresolve,
        )
        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            side_effect=urllib.error.URLError(
                ConnectionResetError("response lost")
            ),
        ):
            with pytest.raises(
                BridgeConnectionError, match="non-idempotent POST",
            ):
                client._request("POST", "/api/v1/sessions", {"agent": "local"})

        assert reresolves["count"] == 0

    def test_reresolve_preserves_encoded_query(self) -> None:
        old_base = "http://127.0.0.1:57585"
        new_base = "http://127.0.0.1:47000"
        client = BridgeClient(
            old_base,
            "tok",
            connect_grace=0.0,
            reresolve=lambda: new_base,
        )
        seen: list[str] = []

        def by_port(req, timeout=None):
            seen.append(req.full_url)
            if req.full_url.startswith(old_base):
                raise urllib.error.URLError("refused")
            return _FakeResp({"ok": True})

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=by_port):
            result = client._request(
                "GET",
                "/api/v1/live-sessions/resolve",
                params={"handle": "machine/repo & worktree"},
            )

        assert result == {"ok": True}
        suffix = "/api/v1/live-sessions/resolve?handle=machine%2Frepo+%26+worktree"
        assert seen == [f"{old_base}{suffix}", f"{new_base}{suffix}"]

    def test_reresolves_on_each_retry_until_port_moves(self) -> None:
        # The first retry happens before active.json flips. A later retry must
        # consult it again rather than memoizing that first unchanged result.
        old_base = "http://127.0.0.1:57585"
        new_base = "http://127.0.0.1:47000"
        endpoints = iter((old_base, new_base))
        client = BridgeClient(
            old_base,
            "tok",
            connect_grace=2.0,
            reresolve=lambda: next(endpoints),
        )
        seen: list[str] = []

        def by_port(req, timeout=None):
            seen.append(req.full_url)
            if req.full_url.startswith(old_base):
                raise urllib.error.URLError("refused")
            return _FakeResp({"ok": True})

        with (
            patch("agent_bridge.client.urllib.request.urlopen", side_effect=by_port),
            patch("time.sleep"),
        ):
            result = client._request("GET", "/health")

        assert result == {"ok": True}
        assert seen == [f"{old_base}/health", f"{old_base}/health", f"{new_base}/health"]

    def test_reresolve_same_port_falls_through_to_grace(self) -> None:
        # The re-resolver returns the SAME port (no move) -> no switch; degrade
        # through the (zero) grace window to a clean BridgeConnectionError.
        client = BridgeClient(
            "http://127.0.0.1:57585",
            "tok",
            connect_grace=0.0,
            reresolve=lambda: "http://127.0.0.1:57585",
        )

        def always_fail(_req, timeout=None):
            raise urllib.error.URLError("refused")

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=always_fail):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")

    def test_reresolve_none_falls_through_to_grace(self) -> None:
        # The re-resolver finds no live endpoint (dead advertised port healed to
        # None) -> no switch; clean failure after grace, not an infinite loop.
        calls = {"n": 0}
        client = BridgeClient(
            "http://127.0.0.1:57585",
            "tok",
            connect_grace=0.0,
            reresolve=lambda: None,
        )

        def always_fail(_req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("refused")

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=always_fail):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")
        # One initial attempt + at most the re-resolve attempt -- bounded, no loop.
        assert calls["n"] <= 2

    def test_no_reresolver_is_unchanged_behavior(self) -> None:
        # A directly-constructed client (no reresolve) behaves exactly as before.
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=0.0)

        def always_fail(_req, timeout=None):
            raise urllib.error.URLError("refused")

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=always_fail):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")


class TestSessionNotFoundGrace:
    def test_follows_new_endpoint_after_session_404(self) -> None:
        old_base = "http://127.0.0.1:57585"
        new_base = "http://127.0.0.1:47000"
        client = BridgeClient(
            old_base,
            "tok",
            connect_grace=0.0,
            reresolve=lambda: new_base,
        )
        seen: list[str] = []

        def by_port(req, timeout=None):
            seen.append(req.full_url)
            if req.full_url.startswith(old_base):
                raise _not_found()
            return _FakeResp({"id": "s1"})

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=by_port):
            result = client._request("GET", "/api/v1/sessions/s1/status")

        assert result == {"id": "s1"}
        assert seen == [
            f"{old_base}/api/v1/sessions/s1/status",
            f"{new_base}/api/v1/sessions/s1/status",
        ]

    def test_waits_for_routing_flip_after_session_404(self) -> None:
        old_base = "http://127.0.0.1:57585"
        new_base = "http://127.0.0.1:47000"
        endpoints = iter((old_base, new_base))
        client = BridgeClient(
            old_base,
            "tok",
            connect_grace=2.0,
            reresolve=lambda: next(endpoints),
        )
        seen: list[str] = []

        def by_port(req, timeout=None):
            seen.append(req.full_url)
            if req.full_url.startswith(old_base):
                raise _not_found()
            return _FakeResp({"id": "s1"})

        with (
            patch("agent_bridge.client.urllib.request.urlopen", side_effect=by_port),
            patch("time.sleep"),
        ):
            result = client._request("GET", "/api/v1/sessions/s1/status")

        assert result == {"id": "s1"}
        assert seen == [
            f"{old_base}/api/v1/sessions/s1/status",
            f"{old_base}/api/v1/sessions/s1/status",
            f"{new_base}/api/v1/sessions/s1/status",
        ]

    def test_settled_session_404_is_reported_after_grace(self) -> None:
        client = BridgeClient(
            "http://127.0.0.1:57585",
            "tok",
            connect_grace=0.0,
            reresolve=lambda: "http://127.0.0.1:57585",
        )

        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            side_effect=_not_found("Session s1 not found"),
        ):
            with pytest.raises(BridgeClientError) as exc_info:
                client._request("GET", "/api/v1/sessions/s1/status")

        assert exc_info.value.status == 404
        assert exc_info.value.detail == "Session s1 not found"

    def test_non_session_404_remains_immediate(self) -> None:
        reresolve = Mock(return_value="http://127.0.0.1:47000")
        client = BridgeClient(
            "http://127.0.0.1:57585",
            "tok",
            connect_grace=2.0,
            reresolve=reresolve,
        )

        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            side_effect=_not_found("Agent missing"),
        ):
            with pytest.raises(BridgeClientError):
                client._request("GET", "/api/v1/agents/missing")

        reresolve.assert_not_called()


class TestRefreshEndpoint:
    """BridgeClient.refresh_endpoint() follows a routing-table cutover so the
    streaming path can be re-pointed at a new dynamic port (dotfiles#1713)."""

    def test_refresh_follows_new_base(self):
        client = BridgeClient(
            "http://127.0.0.1:9280", "tok",
            reresolve=lambda: "http://127.0.0.1:55123",
        )
        assert client.refresh_endpoint() is True
        assert client._base == "http://127.0.0.1:55123"

    def test_refresh_noop_when_unchanged(self):
        client = BridgeClient(
            "http://127.0.0.1:9280", "tok",
            reresolve=lambda: "http://127.0.0.1:9280",
        )
        client._daemon_proto = (10, 1)
        assert client.refresh_endpoint() is False
        assert client._base == "http://127.0.0.1:9280"
        assert client._daemon_proto is None

    def test_refresh_noop_without_resolver(self):
        client = BridgeClient("http://127.0.0.1:9280", "tok")
        client._daemon_proto = (10, 1)
        assert client.refresh_endpoint() is False
        assert client._daemon_proto is None

    def test_refresh_noop_when_resolver_returns_none(self):
        client = BridgeClient(
            "http://127.0.0.1:9280", "tok", reresolve=lambda: None,
        )
        assert client.refresh_endpoint() is False
        assert client._base == "http://127.0.0.1:9280"
