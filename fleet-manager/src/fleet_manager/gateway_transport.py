"""Deadline-bounded name resolution and sockets; TLS retains the original hostname."""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import sys
import time
from http.client import HTTPSConnection
from subprocess import Popen

from .gateway_contract import GatewayError, require

_RESOLVE = """
import json, socket, sys
rows = socket.getaddrinfo(sys.argv[1], int(sys.argv[2]), type=socket.SOCK_STREAM,
                          proto=socket.IPPROTO_TCP)
print(json.dumps([[family, address] for family, _, _, _, address in rows
                  if family in (socket.AF_INET, socket.AF_INET6)][:16]))
"""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    require(remaining > 0, "transport-unavailable")
    return remaining


def _addresses(host: str, port: int, deadline: float) -> list[tuple[int, tuple]]:
    require("%" not in host, "transport-unavailable")
    try:
        numeric = ipaddress.ip_address(host)
    except ValueError:
        numeric = None
    if numeric is not None:
        return [(socket.AF_INET, (host, port))] if numeric.version == 4 else [
            (socket.AF_INET6, (host, port, 0, 0))]
    _remaining(deadline)
    # The system resolver has no cancellable timeout. Own and reap one isolated
    # child instead of accumulating blocked resolver threads inside the service.
    with Popen([sys.executable, "-I", "-c", _RESOLVE, host, str(port)],
               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0) as child:
        try:
            output, _ = child.communicate(timeout=_remaining(deadline))
        except (subprocess.TimeoutExpired, GatewayError):
            child.kill()
            child.communicate()
            raise GatewayError("transport-unavailable") from None
        require(child.returncode == 0 and len(output) <= 8192, "transport-unavailable")
    try:
        rows = json.loads(output)
        require(type(rows) is list and 0 < len(rows) <= 16, "transport-unavailable")
        addresses = []
        for family, address in rows:
            require(type(family) is int and family in (socket.AF_INET, socket.AF_INET6)
                    and type(address) is list
                    and len(address) == (2 if family == socket.AF_INET else 4),
                    "transport-unavailable")
            require(type(address[0]) is str and "%" not in address[0], "transport-unavailable")
            ip = ipaddress.ip_address(address[0])
            require(ip.version == (4 if family == socket.AF_INET else 6)
                    and type(address[1]) is int and address[1] == port,
                    "transport-unavailable")
            if family == socket.AF_INET6:
                require(all(type(v) is int and 0 <= v <= 0xffffffff for v in address[2:]),
                        "transport-unavailable")
            addresses.append((family, tuple(address)))
        _remaining(deadline)
        return addresses
    except (ValueError, TypeError):
        raise GatewayError("transport-unavailable") from None


class DeadlineHTTPSConnection(HTTPSConnection):
    def __init__(self, host: str, port: int, *, context, deadline: float):
        super().__init__(host, port, context=context, timeout=_remaining(deadline))
        self._deadline = deadline
        self._create_connection = self._connect_address

    def _connect_address(self, address, timeout=None, source_address=None):
        for family, resolved in _addresses(address[0], address[1], self._deadline):
            connection = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            try:
                connection.settimeout(_remaining(self._deadline))
                connection.connect(resolved)
                # HTTPSConnection now wraps this socket with the original host
                # for SNI and verification, using only the remaining TLS budget.
                connection.settimeout(_remaining(self._deadline))
                return connection
            except OSError:
                connection.close()
            except GatewayError:
                connection.close()
                raise
        raise GatewayError("transport-unavailable")
