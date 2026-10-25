"""Vault service endpoint advertisement policy."""


def advertised_endpoint(
    *,
    is_windows: bool,
    unix_bound: bool,
    socket_path: str,
    pipe_bound: bool = False,
    pipe_address: str | None = None,
    tcp_bound: bool,
    tcp_address: str | None,
) -> tuple[str, str, list[tuple[str, str]]] | None:
    """Pick the endpoint to advertise for discovery, preferring the OS-native one.

    Rung order (``docs/patterns/service-transport.md``): a POSIX host advertises
    its **Unix domain socket**; a Windows host advertises its **named pipe** when
    bound, else falls back to its loopback **TCP** endpoint. When the primary is
    the Windows **named pipe** and TCP is *also* bound, the TCP endpoint rides
    along as an **alternate** so a cross-boundary client that can't open the pipe
    -- a WSL guest reaching a Windows-hosted daemon (#3426) -- still finds a
    dialable port. POSIX (unix) and TCP-primary advertisements are unchanged (empty
    ``alt``). Returns ``(transport, address, alt)`` where ``alt`` is a
    possibly-empty list of ``(transport, address)`` secondaries, or ``None`` if
    nothing bound.
    """
    if unix_bound and not is_windows:
        return ("unix", socket_path, [])
    if pipe_bound and pipe_address and is_windows:
        tcp_alt = [("tcp", tcp_address)] if tcp_bound and tcp_address else []
        return ("pipe", pipe_address, tcp_alt)
    if tcp_bound and tcp_address:
        return ("tcp", tcp_address, [])
    return None
