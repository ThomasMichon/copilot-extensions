"""Phase 4 (agent-bridge-unified-zdd-cutover): ``send`` survives a mid-command
graceful cutover transparently, exercised at the real CLI call site.

Correction (post-review): an earlier version of this test simulated the
retiring generation refusing ``send``'s delivery with a 503 "draining"
response. That response is real (#3179) but is only ever emitted by the
*session-creation* route (``POST /api/v1/sessions``, when the daemon is
mid-drain and refuses brand-new work) -- ``post_live_message`` (the route
``send`` actually hits when the target already has a live session, the
common case this test exercises) has no draining gate at all, since
delivering into an *already-registered* live session is cheap local-DB work,
not new agent work. That prior test therefore validated a scenario the real
endpoint can never produce.

The real risk window for ``send`` mid-cutover is different: once the
retiring generation's HTTP listener actually closes (post-shutdown, after
the drain grace has elapsed), the *next* delivery attempt against the
remembered port sees a plain connection refusal (a clean ``ECONNREFUSED``,
never a "connection reset" -- nothing was ever sent to the dead process, so
retrying is unambiguously safe even for this non-idempotent POST).
``BridgeClient._request()`` already follows exactly this case to the
routing table's successor and retries (proven generically at the
``BridgeClient`` unit level by ``TestReresolveOnRejection`` in
``test_client_connect.py``); this test closes the same gap as before, this
time against the real endpoint and the real CLI ``send`` code path.
"""

from __future__ import annotations

import argparse
import json
import urllib.error

import pytest

from agent_bridge import __main__ as m
from agent_bridge.client import BridgeClient


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def test_send_cli_survives_connection_refused_mid_delivery(monkeypatch, capsys):
    """A clean ECONNREFUSED against the retired generation's port (post-#3179
    shutdown, not mid-drain) is followed to the successor and retried, even
    for `send`'s non-idempotent delivery POST -- and the CLI prints a normal
    delivery confirmation, never a traceback or hard failure."""
    old_base = "http://127.0.0.1:57585"
    new_base = "http://127.0.0.1:47000"
    client = BridgeClient(
        old_base,
        "tok",
        connect_grace=2.0,
        reresolve=lambda: new_base,
    )
    seen: list[str] = []

    def by_port(req, timeout=None):
        seen.append(req.full_url)
        if req.full_url.endswith("/api/v1/live-sessions/resolve?handle=agent-x"):
            return _FakeResp({"session_id": "sess1", "status": "idle"})
        if req.full_url.startswith(old_base):
            # The old generation has fully shut down -- its port is closed,
            # not merely refusing new work while alive.
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        return _FakeResp({"message_id": "m1", "replied": False})

    monkeypatch.setattr(
        "agent_bridge.client.urllib.request.urlopen", by_port
    )
    monkeypatch.setattr(m, "_get_client", lambda: client)
    monkeypatch.setattr(m, "_live_sender_label", lambda _args: "caller-A")
    monkeypatch.setattr(m, "_live_reply_to", lambda _args: None)
    monkeypatch.setattr(m, "_live_message_kind", lambda _args: "prompt")
    monkeypatch.setattr(m, "_live_message_delivery", lambda _args: "queue")

    args = argparse.Namespace(
        target="agent-x",
        prompt="hello",
        prompt_file=None,
        new=False,
        json=False,
        no_wait=True,
        reply_timeout=120.0,
        idempotency_key=None,
        expected_session_id=None,
    )

    m._cmd_send(args)

    out = capsys.readouterr().out
    assert "Delivered to live session sess1" in out
    # The retired generation's dead port was tried first, then the routing
    # table's successor -- exactly the sequence `send` must follow to look
    # like a brief buffered pause, never a hard error, across a cutover.
    assert seen == [
        f"{old_base}/api/v1/live-sessions/resolve?handle=agent-x",
        f"{old_base}/api/v1/live-sessions/sess1/messages",
        f"{new_base}/api/v1/live-sessions/sess1/messages",
    ]
    assert client._base == new_base


def test_send_with_a_protocol_floor_sends_nothing_to_an_older_daemon(monkeypatch, capsys):
    import pytest

    client = BridgeClient("http://127.0.0.1:57585", "tok")
    monkeypatch.setattr(client, "daemon_supports", lambda version: version <= 19)
    monkeypatch.setattr(client, "resolve_live_session", lambda _t: pytest.fail("sent"))
    monkeypatch.setattr(m, "_get_client", lambda: client)
    args = argparse.Namespace(target="agent-x", prompt="hello", prompt_file=None, new=False,
                              min_daemon_protocol=20)
    with pytest.raises(SystemExit) as exc:
        m._cmd_send(args)
    assert exc.value.code == 3
    assert "predates protocol 20" in capsys.readouterr().err


def _floor_send(monkeypatch, client, by_port):
    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", by_port)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    monkeypatch.setattr(m, "_get_client", lambda: client)
    monkeypatch.setattr(m, "_live_sender_label", lambda _args: "caller-A")
    monkeypatch.setattr(m, "_live_reply_to", lambda _args: None)
    monkeypatch.setattr(m, "_live_message_kind", lambda _args: "prompt")
    monkeypatch.setattr(m, "_live_message_delivery", lambda _args: "queue")
    args = argparse.Namespace(
        target="agent-x", prompt="hello", prompt_file=None, new=False, json=False,
        no_wait=True, reply_timeout=120.0, idempotency_key=None, expected_session_id=None,
        min_daemon_protocol=20,
    )
    m._cmd_send(args)


def _floor_daemon(versions: dict, posted: list, refuse_first_post: bool = True):
    """Fake urlopen: /health answers ``versions[base]`` (the latest entry once
    the first POST was refused); the first POST is refused, later ones land."""
    state = {"refused": not refuse_first_post}

    def by_port(req, timeout=None):
        base = req.full_url.split("/api/")[0].split("/health")[0]
        if req.full_url.endswith("/health"):
            version = versions[base][-1 if state["refused"] else 0]
            return _FakeResp({"status": "ok", "protocol_version": version, "min_protocol_version": 1})
        if req.full_url.endswith("/api/v1/live-sessions/resolve?handle=agent-x"):
            return _FakeResp({"session_id": "sess1", "status": "idle"})
        posted.append(req.full_url)
        if not state["refused"]:
            state["refused"] = True
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        return _FakeResp({"message_id": "m1", "replied": False})

    return by_port


def test_a_protocol_floor_also_holds_for_the_replacement_daemon(monkeypatch):
    """The preflight passed on a protocol-20 daemon, but its port then refused the
    delivery; the routing table names a protocol-19 replacement. The send must
    not be retried there (it couldn't carry the message across a rename)."""
    import pytest

    from agent_bridge.client import BridgeClientError

    old_base, new_base = "http://127.0.0.1:57585", "http://127.0.0.1:47000"
    client = BridgeClient(old_base, "tok", connect_grace=2.0, reresolve=lambda: new_base)
    posted: list[str] = []
    by_port = _floor_daemon({old_base: [20], new_base: [19]}, posted)
    with pytest.raises(BridgeClientError) as exc:
        _floor_send(monkeypatch, client, by_port)
    assert exc.value.status == 426
    assert posted == [f"{old_base}/api/v1/live-sessions/sess1/messages"]  # never the replacement


@pytest.mark.parametrize("pinned", [True, False])
def test_a_protocol_floor_holds_when_an_older_daemon_restarts_at_the_same_url(monkeypatch, pinned):
    """Same URL (pinned, or the routing table still names it), but the daemon
    behind it restarted as protocol 19: the refused POST is not retried."""
    from agent_bridge.client import BridgeClientError

    base = "http://127.0.0.1:57585"
    client = BridgeClient(base, "tok", connect_grace=2.0, reresolve=None if pinned else (lambda: base))
    posted: list[str] = []
    with pytest.raises(BridgeClientError) as exc:
        _floor_send(monkeypatch, client, _floor_daemon({base: [20, 19]}, posted))
    assert exc.value.status == 426
    assert posted == [f"{base}/api/v1/live-sessions/sess1/messages"]


def test_a_protocol_floor_still_retries_a_restart_that_keeps_the_protocol(monkeypatch, capsys):
    base = "http://127.0.0.1:57585"
    client = BridgeClient(base, "tok", connect_grace=2.0)
    posted: list[str] = []
    _floor_send(monkeypatch, client, _floor_daemon({base: [20, 20]}, posted))
    assert posted == [f"{base}/api/v1/live-sessions/sess1/messages"] * 2
    assert "Delivered to live session sess1" in capsys.readouterr().out


@pytest.mark.parametrize("failure", [
    urllib.error.URLError(TimeoutError("timed out")),
    urllib.error.URLError(BrokenPipeError("broken pipe")),
    ConnectionResetError("reset"),
])
def test_a_post_that_may_have_reached_the_daemon_is_never_resent(failure):
    """Only a refused connection proves nothing was sent; a timeout, broken pipe
    or reset may have delivered the seed, so resending could deliver it twice."""
    import time as _time

    from agent_bridge import session_targeting_cli as stc
    from agent_bridge.client import BridgeConnectionError

    sent: list[str] = []

    class Client:
        _base, _connect_grace = "http://old", 30.0

        def _reresolve(self):
            return "http://old"

        def _request(self, method, path, *a, **k):
            if path == "/health":
                return {"protocol_version": 21}
            sent.append(method)
            raise BridgeConnectionError("lost") from failure

    client = Client()
    stc._hold_protocol_floor(client, 21)
    real_sleep, _time.sleep = _time.sleep, (lambda s: None)
    try:
        with pytest.raises(BridgeConnectionError):
            client._request("POST", "/api/v1/live-sessions/s/messages")
    finally:
        _time.sleep = real_sleep
    assert sent == ["POST"]
    assert stc._connection_refused(BridgeConnectionError("x")) is False
    refused = BridgeConnectionError("refused")
    refused.__cause__ = urllib.error.URLError(ConnectionRefusedError("refused"))
    assert stc._connection_refused(refused) is True


def test_an_unanswered_protocol_check_is_retried_never_skipped(monkeypatch):
    """After the refused POST the replacement's /health refuses too, then it
    answers protocol 19: the POST must not have been retried in between."""
    from agent_bridge.client import BridgeClientError

    old_base, new_base = "http://127.0.0.1:57585", "http://127.0.0.1:47000"
    client = BridgeClient(old_base, "tok", connect_grace=30.0, reresolve=lambda: new_base)
    posted: list[str] = []
    inner = _floor_daemon({old_base: [20], new_base: [19]}, posted)
    probes = {"n": 0}

    def by_port(req, timeout=None):
        if req.full_url == f"{new_base}/health":
            probes["n"] += 1
            if probes["n"] == 1:
                raise urllib.error.URLError(ConnectionRefusedError("still starting"))
        return inner(req, timeout)

    with pytest.raises(BridgeClientError) as exc:
        _floor_send(monkeypatch, client, by_port)
    assert exc.value.status == 426 and probes["n"] == 2
    assert posted == [f"{old_base}/api/v1/live-sessions/sess1/messages"]


def _expected_session_send(monkeypatch, resolved: dict, expected: str, daemon_version: int = 20):
    from agent_bridge import session_targeting_cli as stc

    client = BridgeClient("http://127.0.0.1:57585", "tok")
    monkeypatch.setattr(client, "daemon_supports", lambda version: version <= daemon_version)
    monkeypatch.setattr(client, "resolve_live_session", lambda handle: resolved.get(handle))
    delivered = []
    monkeypatch.setattr(stc, "_deliver_to_live_session",
                        lambda _c, _a, sid, _p: delivered.append(sid))
    monkeypatch.setattr(m, "_get_client", lambda: client)
    args = argparse.Namespace(target="agent-x", prompt="hello", prompt_file=None, new=False,
                              expected_session_id=expected)
    m._cmd_send(args)
    return delivered


def test_send_accepts_a_renamed_expected_session(monkeypatch):
    resumed = {"session_id": "resumed"}
    resolved = {"agent-x": resumed, "placeholder": resumed}  # alias placeholder -> resumed
    assert _expected_session_send(monkeypatch, resolved, "placeholder") == ["resumed"]


def test_send_still_rejects_an_unrelated_replacement(monkeypatch, capsys):
    import pytest

    resolved = {"agent-x": {"session_id": "stranger"}, "placeholder": None}
    with pytest.raises(SystemExit) as exc:
        _expected_session_send(monkeypatch, resolved, "placeholder")
    assert exc.value.code == 1
    assert "not expected session" in capsys.readouterr().err


def test_a_protocol_floor_is_checked_before_every_attempt_not_only_retries(monkeypatch):
    """The preflight passed, then an older daemon restarted at the same URL with
    no connection failure in between: nothing may reach it."""
    from agent_bridge.client import BridgeClientError

    base = "http://127.0.0.1:57585"
    client = BridgeClient(base, "tok", connect_grace=2.0)
    calls: list[str] = []
    healths = iter([20])  # the preflight sees 20; every later probe sees 19

    def by_port(req, timeout=None):
        calls.append(req.full_url)
        if req.full_url.endswith("/health"):
            version = next(healths, 19)
            return _FakeResp({"status": "ok", "protocol_version": version, "min_protocol_version": 1})
        if req.full_url.endswith("/api/v1/live-sessions/resolve?handle=agent-x"):
            return _FakeResp({"session_id": "sess1", "status": "idle"})
        return _FakeResp({"message_id": "m1", "replied": False})

    with pytest.raises(BridgeClientError) as exc:
        _floor_send(monkeypatch, client, by_port)
    assert exc.value.status == 426
    assert not any(u.endswith("/messages") or "resolve" in u for u in calls)

@pytest.mark.parametrize("detail, followed", [
    ("the bridge daemon is draining; retry against the replacement", True),
    ("service unavailable", False),  # any other 503 is the answer
])
def test_a_protocol_floor_follows_a_draining_daemons_refusal(monkeypatch, detail, followed):
    """A retiring daemon refuses the POST outright with 503 "draining" (it was
    never accepted): the send follows the replacement within the grace, after
    re-probing the replacement's protocol. Other HTTP errors aren't retried."""
    import time as _time

    from agent_bridge import session_targeting_cli as stc
    from agent_bridge.client import BridgeClientError

    monkeypatch.setattr(_time, "sleep", lambda s: None)
    calls: list[tuple[str, str, str]] = []

    class Client:
        _base, _connect_grace = "http://old", 30.0

        def _reresolve(self):
            return "http://new"

        def _request(self, method, path, *a, **k):
            calls.append((self._base, method, path))
            if path == "/health":
                return {"protocol_version": 21}
            if self._base == "http://old":
                raise BridgeClientError(503, detail)
            return {"ok": True}

    client = Client()
    stc._hold_protocol_floor(client, 21)
    if not followed:
        with pytest.raises(BridgeClientError):
            client._request("POST", "/api/v1/live-sessions/s/messages")
        assert [c for c in calls if c[2] != "/health"] == [("http://old", "POST", "/api/v1/live-sessions/s/messages")]
        return
    assert client._request("POST", "/api/v1/live-sessions/s/messages") == {"ok": True}
    assert calls[-2:] == [("http://new", "GET", "/health"), ("http://new", "POST", "/api/v1/live-sessions/s/messages")]


def test_a_protocol_floor_retries_a_starting_daemons_initializing_refusal(monkeypatch):
    """/health already advertises the floor, but session creation isn't ready
    yet (503 "initializing"; never accepted). client.py's own retry for that is
    off under the floor, so the wrapper retries it within the same grace,
    re-probing /health before each attempt -- and still gives up at the deadline."""
    import time as _time

    from agent_bridge import session_targeting_cli as stc
    from agent_bridge.client import BridgeClientError

    clock = [0.0]
    monkeypatch.setattr(_time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])
    calls: list[str] = []
    refusals = [2]

    class Client:
        _base, _connect_grace = "http://d", 30.0
        _reresolve = None

        def _request(self, method, path, *a, **k):
            calls.append(path)
            if path == "/health":
                return {"protocol_version": 21}
            if refusals[0] > 0:
                refusals[0] -= 1
                raise BridgeClientError(503, "the bridge daemon is initializing; retry shortly")
            return {"session_id": "s1"}

    client = Client()
    stc._hold_protocol_floor(client, 21)
    assert client._request("POST", "/api/v1/sessions") == {"session_id": "s1"}
    assert calls == ["/health", "/api/v1/sessions"] * 3  # re-probed before every attempt
    refusals[0] = 10**6  # never becomes ready: the deadline still ends it
    with pytest.raises(BridgeClientError) as exc:
        client._request("POST", "/api/v1/sessions")
    assert exc.value.status == 503 and clock[0] >= 30.0


@pytest.mark.parametrize("first_failure", ["refused", "unanswered-probe"])
def test_a_protocol_floor_never_sends_after_a_slow_retry_probe(monkeypatch, first_failure):
    """A retry's /health probe that answers only after the grace has run out
    does not send the request late: the failure that started the retry stands."""
    import time as _time

    from agent_bridge import session_targeting_cli as stc
    from agent_bridge.client import BridgeClientError, BridgeConnectionError

    clock = [0.0]
    monkeypatch.setattr(_time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])
    calls: list[str] = []

    class Client:
        _base, _connect_grace = "http://d", 1.0
        _reresolve = None

        def _request(self, method, path, *a, **k):
            calls.append(path)
            if path == "/health":
                probes = calls.count("/health")
                if probes == 1 and first_failure == "unanswered-probe":
                    raise BridgeConnectionError("connection refused")
                if probes > 1:
                    clock[0] += 2.0  # the retry's probe is slow
                return {"protocol_version": 21}
            raise BridgeClientError(503, "the bridge daemon is initializing; retry shortly")

    client = Client()
    stc._hold_protocol_floor(client, 21)
    expected = BridgeClientError if first_failure == "refused" else BridgeConnectionError
    with pytest.raises(expected):
        client._request("POST", "/api/v1/sessions")
    sent = [c for c in calls if c != "/health"]
    assert sent == (["/api/v1/sessions"] if first_failure == "refused" else [])
    assert clock[0] >= 1.0


def test_a_protocol_floor_retries_a_health_read_timeout(monkeypatch):
    """A /health probe that connects but times out reading (a bare TimeoutError
    the client doesn't wrap) is an unanswered probe: retried within the grace,
    and the request goes out only once the protocol is confirmed."""
    import time as _time

    from agent_bridge import session_targeting_cli as stc

    clock = [0.0]
    monkeypatch.setattr(_time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])
    calls: list[str] = []

    class Client:
        _base, _connect_grace = "http://d", 30.0
        _reresolve = None

        def _request(self, method, path, *a, **k):
            calls.append(path)
            if path == "/health":
                if calls.count("/health") == 1:
                    raise TimeoutError("The read operation timed out")
                return {"protocol_version": 21}
            return {"ok": True}

    client = Client()
    stc._hold_protocol_floor(client, 21)
    assert client._request("POST", "/api/v1/live-sessions/s/messages") == {"ok": True}
    assert calls == ["/health", "/health", "/api/v1/live-sessions/s/messages"]

@pytest.mark.parametrize("daemon_version, refused", [(21, False), (20, True)])
def test_send_leaves_the_expected_session_check_to_an_alias_aware_daemon(
        monkeypatch, capsys, daemon_version, refused):
    """Two lookups can straddle a rollover that moves both handles (``placeholder``
    and the target): on an alias-aware daemon the client sends with
    ``expected_session_id`` and lets the atomic enqueue check decide; an older
    daemon still gets the client-side precheck."""
    client = BridgeClient("http://127.0.0.1:57585", "tok")
    monkeypatch.setattr(client, "daemon_supports", lambda version: version <= daemon_version)
    resolved = iter([{"session_id": "resumed-1"}, {"session_id": "resumed-2"}])  # rolled over between
    monkeypatch.setattr(client, "resolve_live_session", lambda _h: next(resolved))
    sent: list = []
    monkeypatch.setattr(m, "_get_client", lambda: client)
    from agent_bridge import session_targeting_cli as stc
    monkeypatch.setattr(stc, "_deliver_to_live_session",
                        lambda _c, args, sid, prompt: sent.append((sid, args.expected_session_id)))
    args = argparse.Namespace(target="wt-handle", prompt="hello", prompt_file=None, new=False,
                              expected_session_id="placeholder")
    if refused:
        with pytest.raises(SystemExit):
            m._cmd_send(args)
        assert sent == [] and "not expected session" in capsys.readouterr().err
    else:
        m._cmd_send(args)
        assert sent == [("resumed-1", "placeholder")]  # the daemon checks it atomically

def test_a_protocol_floor_suspends_endpoint_discovery_only_during_its_own_attempts():
    """The floor turns off the client's own retries while it probes and sends --
    and only then: afterwards (a stream refreshing its endpoint after a cutover)
    the resolver and grace are the client's again."""
    from agent_bridge import session_targeting_cli as stc

    client = BridgeClient("http://127.0.0.1:57585", "tok", connect_grace=5.0,
                          reresolve=lambda: "http://127.0.0.1:47000")
    resolver = client._reresolve
    during: list = []

    def fake_request(method, path, *a, **k):
        during.append((client._reresolve, client._connect_grace))
        return {"protocol_version": 21} if path == "/health" else {"ok": True}

    client._request = fake_request
    stc._hold_protocol_floor(client, 21)
    assert client._reresolve is resolver and client._connect_grace == 5.0  # intact between attempts
    assert client._request("GET", "/api/v1/x") == {"ok": True}
    assert during == [(None, 0.0), (None, 0.0)]  # probe and request: retries off
    assert client._reresolve is resolver and client._connect_grace == 5.0  # restored after