"""Finite native PSMux observations without executing its cleanup-bearing CLI."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import re
import socket
import threading
import time

from agent_remote_driver.mux_observation_types import Observation, ObservationUnavailable, Pane

CONTRACT = "copilot-driver.mux-observation"
VERSION = 1
_LIMIT = 256 * 1024
_QUERIES = tuple(query.encode("ascii") for query in (
    'list-panes -s -F "P\t#{session_name}\t#{session_id}:#{session_created}'
    '\t#{pane_id}\t#{pane_dead}\t#{pane_active}\t#{pane_current_path}"\n',
    'list-clients -F "C\t#{session_name}"\n',
    'display-message -p "I\t#{session_name}\t#{session_id}:#{session_created}\t#{pane_id}"\n',
))


def _data_dir() -> Path:
    override = os.environ.get("PSMUX_DATA_DIR")
    if override is not None:
        if not override or not Path(override).is_absolute():
            raise ValueError("Invalid native data root")
        return Path(override)
    home = os.environ.get("USERPROFILE")
    if not home and os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, buffer) == 0:
            home = buffer.value
    if not home:
        drive, tail = os.environ.get("HOMEDRIVE", ""), os.environ.get("HOMEPATH", "")
        if drive and tail and Path(drive + tail).is_dir():
            home = drive + tail
    home = home or os.environ.get("HOME")
    if not home or not Path(home).is_absolute():
        raise ValueError("Unavailable native home")
    return Path(home) / ".psmux"


def _read(path: Path) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(4097)
    if not value or len(value) > 4096:
        raise ValueError("Invalid native rendezvous")
    return value


def _namespace(value: str | None = None) -> str:
    if value is None:
        value = os.environ.get("COPILOT_DRIVER_PSMUX_NAMESPACE", "")
    if not isinstance(value, str) or value and not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise ValueError("Invalid native namespace")
    return value


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Native observation timed out")
    return remaining


def _exchange(port: int, key: str, deadline: float) -> str:
    scheme = "AU" + "TH "
    output = bytearray()
    # Older providers accept only one command on each authenticated connection.
    for query in _QUERIES:
        request = (scheme + key + "\n").encode("utf-8") + query
        with socket.create_connection(("127.0.0.1", port), timeout=_remaining(deadline)) as stream:
            stream.settimeout(_remaining(deadline))
            stream.sendall(request)
            chunks = bytearray()
            while True:
                stream.settimeout(_remaining(deadline))
                block = stream.recv(min(16384, _LIMIT + 1 - len(output) - len(chunks)))
                if not block:
                    break
                chunks.extend(block)
                if len(output) + len(chunks) > _LIMIT:
                    raise ValueError("Oversized native observation")
        ack, separator, body = chunks.partition(b"\n")
        if not separator or ack.removesuffix(b"\r") != b"OK":
            raise ValueError("Native authentication was not confirmed")
        output.extend(body)
    return output.decode("utf-8").replace("\r\n", "\n")


def _parse(body: str, session: str) -> Observation:
    panes: list[Pane] = []
    identities: set[str] = set()
    clients = 0
    complete = False
    current_pane = ""
    for line in body.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if complete or len(fields) < 2 or fields[1] != session:
            raise ValueError("Unconfirmed native session")
        if fields[0] == "C" and len(fields) == 2:
            clients += 1
            continue
        if len(fields) < 3 or not re.fullmatch(r"\$[0-9]+:[0-9]+", fields[2]):
            raise ValueError("Unconfirmed native session")
        identities.add(fields[2])
        if fields[0] == "I" and len(fields) == 4 and re.fullmatch(r"%[0-9]+", fields[3]):
            complete = True
            current_pane = fields[3]
        elif (
            fields[0] == "P" and len(fields) == 7
            and re.fullmatch(r"%[0-9]+", fields[3])
            and fields[4] in {"0", "1"} and fields[5] in {"0", "1"}
        ):
            panes.append(Pane(fields[3], fields[4] == "1", fields[5] == "1", fields[6]))
        else:
            raise ValueError("Malformed native observation")
    pane_ids = {p.pane_id for p in panes}
    if not complete or current_pane not in pane_ids or len(identities) != 1 or len(pane_ids) != len(panes):
        raise ValueError("Unconfirmed native session identity")
    # pane_active is window-local; the final identity identifies the current pane.
    return Observation(
        "present", incarnation=identities.pop(), clients=clients,
        panes=tuple(replace(p, active=p.pane_id == current_pane) for p in panes),
    )


def _settled_inventory(root: Path, deadline: float) -> dict[str, tuple[str, int]]:
    from agent_remote_driver import mux_inventory

    while True:
        try:
            return mux_inventory.observe_registrations(timeout=_remaining(deadline), data_dir=root)
        except ObservationUnavailable:
            # Warm/cold servers publish several rendezvous files before readiness.
            time.sleep(min(0.05, _remaining(deadline)))


def observe_psmux(
    session: str, *, timeout: float = 1.0, data_dir: Path | None = None,
    namespace: str | None = None, _inventory=None,
) -> Observation:
    """Read native rendezvous hints, then require an authenticated live response.

    Missing rendezvous needs complete native coverage before absence is known.
    A stale endpoint, denied read, timeout or protocol mismatch is unknown.
    No observation deletes, reconstructs or refreshes the provider's files.
    An explicit namespace selects the creation transport without changing the
    process-wide default used by independent observers.
    """
    if (
        not isinstance(session, str)
        or not session or len(session) > 200 or session.startswith(".")
        or any(
            not (char.isalnum() or char in "_-." or (
                ord(char) > 127 and char.isprintable() and not char.isspace()
            ))
            for char in session
        )
        or session.endswith(".") or not 0 < timeout <= 5
    ):
        return Observation("unsupported", "unsupported-target")
    deadline = time.monotonic() + timeout
    try:
        root = data_dir if data_dir is not None else _data_dir()
        namespace = _namespace(namespace)
        base = namespace + "__" + session if namespace else session
        paths = {ext: root / (base + "." + ext) for ext in ("port", "key", "sid", "pid")}
        try:
            port_raw = _read(paths["port"])
        except FileNotFoundError:
            inventory = (
                _inventory() if _inventory is not None
                else _settled_inventory(root, deadline)
            )
            return (
                Observation("absent") if base not in inventory
                else Observation("unknown", "missing-rendezvous")
            )
        key_raw = _read(paths["key"])
        port_text = port_raw.decode("ascii").strip()
        key = key_raw.decode("utf-8").strip()
        if not port_text.isdecimal() or not 0 < int(port_text) < 65536:
            raise ValueError("Invalid native port")
        if not key or any(c in key for c in "\r\n\0"):
            raise ValueError("Invalid native authentication")
        result = _parse(_exchange(int(port_text), key, deadline), session)
        if _read(paths["port"]) != port_raw or _read(paths["key"]) != key_raw:
            return Observation("unknown", "changed-rendezvous")
        return result
    except (OSError, ValueError, UnicodeError, ObservationUnavailable):
        return Observation("unknown", "native-query-unavailable")


def observe_psmux_many(sessions: list[str], *, timeout: float = 1.0) -> dict[str, Observation]:
    """Share one finite budget across a listing, including unresponsive rows."""
    deadline = time.monotonic() + timeout
    lock = threading.Lock()
    snapshot = []

    def inventory():
        from agent_remote_driver import mux_inventory

        with lock:
            if not snapshot:
                try:
                    snapshot.append(mux_inventory.observe_registrations(
                        timeout=_remaining(deadline), data_dir=_data_dir(),
                    ))
                except (OSError, ValueError, ObservationUnavailable):
                    snapshot.append(None)
            if snapshot[0] is None:
                raise ObservationUnavailable("Native coverage unavailable")
            return snapshot[0]

    def observe(session):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return Observation("unknown", "listing-budget")
        return observe_psmux(session, timeout=min(remaining, 5), _inventory=inventory)

    names = list(dict.fromkeys(sessions))
    if not names:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
        return dict(zip(names, pool.map(observe, names)))


def list_psmux() -> dict[str, Observation] | None:
    """Observe the default native namespace; partial enumeration stays unknown."""
    deadline = time.monotonic() + 1
    try:
        root = _data_dir()
        namespace = _namespace()
        prefix = namespace + "__" if namespace else ""
        names = set()
        try:
            for index, path in enumerate(root.iterdir()):
                if index >= 4096:
                    return None
                if path.suffix not in {".port", ".key", ".sid", ".pid"}:
                    continue
                names.add(path.stem)
        except FileNotFoundError:
            pass
        from agent_remote_driver import mux_inventory

        inventory = mux_inventory.observe_registrations(timeout=_remaining(deadline), data_dir=root)
        selected = set()
        for name in names - inventory.keys():
            if prefix and name.startswith(prefix):
                selected.add(name[len(prefix):])
            elif not prefix and "__" not in name:
                selected.add(name)
        for base, (name, _clients) in inventory.items():
            if base == prefix + name:
                selected.add(name)
        return observe_psmux_many(sorted(selected), timeout=_remaining(deadline))
    except (OSError, ValueError, ObservationUnavailable):
        return None


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate observation property")
        value[key] = item
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--describe-contract", action="store_true")
    mode.add_argument("--observe", action="store_true")
    args = parser.parse_args(argv)
    if args.describe_contract:
        import sys

        print(json.dumps({
            "contract": CONTRACT, "version": VERSION, "operations": ["psmux-observe"],
            "python": str(Path(sys.executable).absolute()),
            "entrypoint": str(Path(__file__).resolve()),
        }))
        return 0
    import sys

    try:
        raw = sys.stdin.read(4097)
        if len(raw) > 4096:
            raise ValueError("Oversized request")
        request = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(request, dict) or set(request) != {"version", "session"}:
            raise ValueError("Invalid observation request")
        if type(request["version"]) is not int or request["version"] != VERSION:
            raise ValueError("Unsupported observation request")
        result = observe_psmux(request["session"])
    except (ValueError, TypeError):
        result = Observation("unsupported", "invalid-request")
    print(json.dumps({"contract": CONTRACT, "version": VERSION, **asdict(result)}))
    return 0 if result.status in {"present", "absent"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
