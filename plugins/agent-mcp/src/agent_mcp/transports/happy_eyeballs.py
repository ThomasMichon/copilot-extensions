"""RFC 8305-lite dual-stack connect racing for the HTTP MCP bridge transport.

``urllib``/``http.client`` (via ``socket.create_connection``) tries each
``getaddrinfo`` candidate **strictly in order**, waiting the *full* connect
timeout on one address family before ever trying the next. When a host's
route for one family is black-holed -- a dropped SYN, not a refused
connection -- that means every single request eats the whole timeout before
falling back, if it falls back within the timeout at all. Confirmed live
(aperture-labs#7553): a stale WSL2-mirrored IPv6 lease left ``eth0``'s global
addresses "deprecated preferred_lft 0sec" but still present, so DNS kept
returning an unreachable AAAA record and every ``agent-mcp`` HTTP-bridge call
hung for the full ~30s ``cfg.timeout`` before failing, stalling three
Intelligence Dampener PR reviews for 45+ minutes each until the interface was
cycled by hand. ``curl`` and browsers avoid exactly this with Happy Eyeballs
(RFC 8305): race the address families with a short head start for whichever
the resolver ranks first, and take whichever connects first.

This is a *lite* implementation scoped to what a single-endpoint MCP bridge
actually needs: one attempt per **distinct address family** (never per
address -- a bridge target is one endpoint, not a CDN with dozens of DNS
records), raced with threads rather than an event loop. The HTTP transport
already calls ``_post`` off the event loop via ``asyncio.to_thread``, so a
thread-based race costs nothing extra there.
"""

from __future__ import annotations

import http.client
import socket
import threading
import urllib.request

#: RFC 8305's own suggested "Connection Attempt Delay" (it recommends
#: 150-250ms; the top of that range gives the slower family a fair shot
#: without meaningfully lengthening the common case where the primary family
#: is healthy).
DEFAULT_HEAD_START = 0.25


def happy_eyeballs_connect(
    host: str,
    port: int,
    *,
    timeout: float,
    head_start: float = DEFAULT_HEAD_START,
) -> socket.socket:
    """Connect to ``host``:``port``, racing address families.

    Resolves once via :func:`socket.getaddrinfo` (which already orders
    candidates per RFC 6724 -- typically the OS's preferred family first),
    then starts one connect attempt per distinct address family: the
    top-ranked family immediately, every other family after ``head_start``
    seconds. Returns the socket of whichever connects first; any other
    in-flight attempt that completes late is closed immediately. Raises the
    first-ranked attempt's exception if every attempt fails, or an
    :class:`OSError` if none connects within ``timeout + head_start``.

    A single-family result (the common, healthy case) connects directly with
    no race and no head-start delay -- there is nothing to race against.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not infos:
        raise OSError(f"getaddrinfo({host!r}, {port!r}) returned no results")

    primaries: list[tuple] = []
    seen_families: set[int] = set()
    for info in infos:
        family = info[0]
        if family not in seen_families:
            seen_families.add(family)
            primaries.append(info)

    if len(primaries) == 1:
        return _connect_one(primaries[0], timeout)

    winner: dict[str, socket.socket] = {}
    errors: list[tuple[int, BaseException]] = []
    in_flight: dict[int, socket.socket] = {}
    done = threading.Event()
    all_settled = threading.Condition()
    remaining = len(primaries)

    def _settle() -> None:
        nonlocal remaining
        remaining -= 1
        all_settled.notify_all()

    def attempt(order: int, info: tuple, delay: float) -> None:
        if delay and done.wait(delay):
            with all_settled:
                _settle()
            return  # a higher-priority attempt already won before we started
        family, socktype, proto, _canonname, sockaddr = info
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        with all_settled:
            if done.is_set():
                # A winner was already picked during socket creation itself
                # (vanishingly rare, but free to check) -- never even dial.
                sock.close()
                _settle()
                return
            in_flight[order] = sock
        try:
            sock.connect(sockaddr)
        except OSError as exc:
            with all_settled:
                in_flight.pop(order, None)
                errors.append((order, exc))
                _settle()
            return
        with all_settled:
            in_flight.pop(order, None)
            if done.is_set():
                sock.close()  # connected, but lost the race -- drop it
            else:
                winner["sock"] = sock
                done.set()
                # Actively abort every other still-in-flight attempt instead
                # of letting it block out its own full timeout for nothing:
                # closing a socket while another thread is blocked in
                # connect() on it reliably unblocks that call with an error
                # on this transport's supported platforms.
                for other in in_flight.values():
                    try:
                        other.close()
                    except OSError:
                        pass
                in_flight.clear()
            _settle()

    threads = [
        threading.Thread(
            target=attempt, args=(idx, info, 0.0 if idx == 0 else head_start), daemon=True
        )
        for idx, info in enumerate(primaries)
    ]
    for t in threads:
        t.start()

    # Return as soon as a winner exists OR every attempt has settled (all
    # failed) -- never block on a still-in-flight loser (real sockets bound
    # that by their own ``settimeout``; the active-abort above bounds it
    # further; daemon threads bound it for the process even if one somehow
    # never returns).
    deadline = timeout + head_start + 1.0
    with all_settled:
        while "sock" not in winner and remaining > 0:
            if not all_settled.wait(timeout=deadline):
                break

    if "sock" in winner:
        return winner["sock"]
    if errors:
        errors.sort(key=lambda pair: pair[0])
        raise errors[0][1]
    raise OSError(f"connection to {host}:{port} timed out (all address families)")


def _connect_one(info: tuple, timeout: float) -> socket.socket:
    family, socktype, proto, _canonname, sockaddr = info
    sock = socket.socket(family, socktype, proto)
    sock.settimeout(timeout)
    try:
        sock.connect(sockaddr)
    except OSError:
        sock.close()
        raise
    # Deliberately NOT cleared to ``None``: ``socket.create_connection`` (the
    # function this replaces) leaves the connect-time timeout in place for
    # subsequent reads too, and ``http.client.HTTPConnection.connect()``
    # never reapplies ``self.timeout`` after this hook returns -- clearing it
    # here would silently make the HTTP response read unbounded (caught in
    # PR review).
    return sock


def _create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Drop-in replacement for ``socket.create_connection`` -- the exact call
    signature ``http.client.HTTPConnection`` invokes via its
    ``self._create_connection`` hook. ``source_address`` (an outbound-bind
    override) is not something this transport's config ever sets; accepted
    only for signature compatibility.
    """
    host, port = address
    resolved_timeout = None if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    return happy_eyeballs_connect(host, port, timeout=resolved_timeout or 30.0)


class HappyEyeballsHTTPConnection(http.client.HTTPConnection):
    """``HTTPConnection`` whose connect races address families (see module
    docstring). ``_create_connection`` is the one hook ``connect()`` calls
    for the actual socket -- everything else about HTTP request/response
    handling is untouched."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _create_connection


class HappyEyeballsHTTPSConnection(http.client.HTTPSConnection):
    """TLS counterpart of :class:`HappyEyeballsHTTPConnection` -- the TLS
    handshake in ``HTTPSConnection.connect()`` wraps whatever socket
    ``_create_connection`` returns, so overriding the same hook covers both."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _create_connection


class _HappyEyeballsHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(HappyEyeballsHTTPConnection, req)


class _HappyEyeballsHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(HappyEyeballsHTTPSConnection, req, context=self._context)


def build_opener() -> urllib.request.OpenerDirector:
    """A ``urlopen``-compatible opener whose HTTP(S) connections race dual-stack
    address families instead of blocking out the full timeout on a
    black-holed family (see module docstring)."""
    return urllib.request.build_opener(_HappyEyeballsHTTPHandler(), _HappyEyeballsHTTPSHandler())
