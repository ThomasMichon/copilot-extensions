"""Fixed loopback-only Linux observation; no admission or lifecycle authority."""

from __future__ import annotations

import errno
import json
import os
import re
import signal
import socket
import sys
import time
from dataclasses import dataclass

MAX_FILE_BYTES = 8192
MAX_RECORDS = 32
MAX_SECONDS = 5
_HEADER = "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT".split()
_LOOPBACK = "0" * 31 + "1"


@dataclass(frozen=True)
class OfflineNetwork:
    namespace_inode: int
    loopback_index: int
    loopback_flags: int
    ipv6_route_count: int
    ipv6_address_count: int


@dataclass(frozen=True)
class _Snapshot:
    namespace_inode: int
    loopback_index: int
    loopback_flags: int
    routes: tuple[tuple[str, ...], ...]
    addresses: tuple[tuple[str, ...], ...]


def _lines(value: object) -> list[list[str]]:
    if not isinstance(value, str) or len(value) > MAX_FILE_BYTES or not value.isascii():
        raise ValueError("offline kernel observation exceeds its supported shape")
    lines = value.splitlines()
    if len(lines) > MAX_RECORDS:
        raise ValueError("offline kernel record ceiling exceeded")
    return [line.split() for line in lines]


def _check_snapshot(value: object) -> _Snapshot:
    if not isinstance(value, dict) or set(value) != {
        "namespace_inode", "interfaces", "loopback_flags", "ipv4_routes", "ipv6_routes", "ipv6_addresses",
    }:
        raise ValueError("offline kernel observation fields are incomplete")
    inode, interfaces = value["namespace_inode"], value["interfaces"]
    if (
        type(inode) is not int or not 0 < inode < 2**64
        or not isinstance(interfaces, list) or len(interfaces) != 1
        or not isinstance(interfaces[0], list) or len(interfaces[0]) != 2
        or type(interfaces[0][0]) is not int or not 0 < interfaces[0][0] < 2**31
        or interfaces[0][1] != "lo"
    ):
        raise ValueError("offline namespace must expose only its own loopback")
    raw_flags = value["loopback_flags"]
    if not isinstance(raw_flags, str) or re.fullmatch(r"0x[0-9a-f]{1,8}\n?", raw_flags) is None:
        raise ValueError("offline loopback flags are unavailable")
    flags = int(raw_flags, 16)
    if flags & 0x9 != 0x9:
        raise ValueError("offline interface must be up and have the loopback flag")
    if _lines(value["ipv4_routes"]) != [_HEADER]:
        raise ValueError("offline namespace has unexpected IPv4 routes")
    routes = _lines(value["ipv6_routes"])
    for fields in routes:
        widths = (32, 2, 32, 2, 32, 8, 8, 8, 8)
        if (
            len(fields) != 10 or fields[-1] != "lo"
            or any(re.fullmatch(f"[0-9a-f]{{{width}}}", field) is None
                   for field, width in zip(fields, widths))
            or (fields[0], fields[1]) not in {("0" * 32, "00"), (_LOOPBACK, "80")}
            or fields[2:5] != ["0" * 32, "00", "0" * 32]
        ):
            raise ValueError("offline namespace has unexpected IPv6 routes")
    addresses = _lines(value["ipv6_addresses"])
    for fields in addresses:
        if (
            len(fields) != 6 or fields[0] != _LOOPBACK or fields[2:4] != ["80", "10"]
            or fields[-1] != "lo"
            or re.fullmatch(r"[0-9a-f]{2,8}", fields[1]) is None
            or int(fields[1], 16) != interfaces[0][0]
            or re.fullmatch(r"[0-9a-f]{2}", fields[4]) is None
        ):
            raise ValueError("offline namespace has unexpected IPv6 addresses")
    # Kernel reference/use counters may change on the no-route lookups.
    configurations = tuple(sorted(tuple(fields[:6] + fields[8:]) for fields in routes))
    return _Snapshot(inode, interfaces[0][0], flags, configurations, tuple(sorted(map(tuple, addresses))))


def inspect_observation(value: object) -> OfflineNetwork:
    if not isinstance(value, dict) or set(value) != {"before", "after", "ipv4_error", "ipv6_error"}:
        raise ValueError("offline observation fields are incomplete")
    before, after = _check_snapshot(value["before"]), _check_snapshot(value["after"])
    if before != after:
        raise ValueError("offline namespace changed during observation")
    if value["ipv4_error"] != "ENETUNREACH" or value["ipv6_error"] != "ENETUNREACH":
        raise ValueError("both offline no-route outcomes must be observed")
    return OfflineNetwork(
        before.namespace_inode, before.loopback_index, before.loopback_flags,
        len(before.routes), len(before.addresses),
    )


def _read(path: str) -> str:
    with open(path, "rb") as source:
        value = source.read(MAX_FILE_BYTES + 1)
    if len(value) > MAX_FILE_BYTES:
        raise ValueError("offline kernel file exceeds its observation ceiling")
    return value.decode("ascii", errors="strict")


def _snapshot() -> dict[str, object]:
    return {
        "namespace_inode": os.stat("/proc/self/ns/net").st_ino,
        "interfaces": [list(item) for item in socket.if_nameindex()],
        "loopback_flags": _read("/sys/class/net/lo/flags"),
        "ipv4_routes": _read("/proc/net/route"),
        "ipv6_routes": _read("/proc/net/ipv6_route"),
        "ipv6_addresses": _read("/proc/net/if_inet6"),
    }


def _no_route(family: int, address: tuple[str, int] | tuple[str, int, int, int]) -> str:
    with socket.socket(family, socket.SOCK_STREAM) as channel:
        channel.settimeout(1)
        try:
            channel.connect(address)
        except OSError as error:
            if error.errno != errno.ENETUNREACH:
                raise ValueError("offline attempt did not establish a no-route result") from error
        else:
            raise ValueError("offline connection unexpectedly succeeded")
    return "ENETUNREACH"


def observe() -> dict[str, object]:
    started = time.monotonic()
    before = _snapshot()
    _check_snapshot(before)
    ipv4 = _no_route(socket.AF_INET, ("192.0.2.1", 9))
    ipv6 = _no_route(socket.AF_INET6, ("2001:db8::1", 9, 0, 0))
    value = {"before": before, "after": _snapshot(), "ipv4_error": ipv4, "ipv6_error": ipv6}
    inspect_observation(value)
    if time.monotonic() - started >= MAX_SECONDS:
        raise TimeoutError("offline observation expired")
    return value


def _expired(_signal: int, _frame: object) -> None:
    raise TimeoutError("offline observation expired")


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
    try:
        print(json.dumps(observe(), allow_nan=False))
    except (OSError, ValueError, TimeoutError):
        print("offline network observation unavailable", file=sys.stderr)
        sys.exit(1)
