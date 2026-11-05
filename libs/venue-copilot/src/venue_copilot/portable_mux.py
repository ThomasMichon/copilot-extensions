"""Exact native addressing for a provider's retained portable mux attempt."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
import shlex
import uuid
from typing import Any


@dataclass(frozen=True)
class MuxEndpoint:
    """Immutable pending or confirmed endpoint; never an implicit default server."""

    attempt: str
    session_id: str
    mux_session: str
    mux_socket: str
    incarnation: str | None = None
    pane: str | None = None
    device: int | None = None
    inode: int | None = None

    @classmethod
    def parse(cls, value: object) -> MuxEndpoint:
        """Validate a versioned endpoint without interpreting missing native proof."""
        if not isinstance(value, dict) or set(value) != {
            "version", "attempt", "session_id", "mux_session", "mux_socket",
            "native", "socket_identity",
        } or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("Unsupported portable mux endpoint")
        for key in ("attempt", "session_id"):
            if not isinstance(value[key], str) or str(uuid.UUID(value[key])) != value[key]:
                raise ValueError("Portable mux endpoint requires canonical launch UUIDs")
        if value["mux_session"] != "wt-bridge-" + value["attempt"]:
            raise ValueError("Portable mux session differs from its retained attempt")
        socket = value["mux_socket"]
        if (
            not isinstance(socket, str) or not PurePosixPath(socket).is_absolute()
            or any(part in {"", ".", ".."} for part in socket.split("/")[1:])
            or "\\" in socket or "\0" in socket or len(socket.encode()) >= 104
            or PurePosixPath(socket).parts[-2:] != (value["attempt"], "tmux.sock")
        ):
            raise ValueError("Portable mux endpoint requires its exact private socket")
        native, identity = value["native"], value["socket_identity"]
        if native is None and identity is None:
            return cls(value["attempt"], value["session_id"], value["mux_session"], socket)
        if (
            not isinstance(native, dict) or set(native) != {"incarnation", "pane"}
            or not isinstance(native["incarnation"], str)
            or re.fullmatch(r"\$[0-9]+:[0-9]+", native["incarnation"]) is None
            or not isinstance(native["pane"], str) or re.fullmatch(r"%[0-9]+", native["pane"]) is None
            or not isinstance(identity, dict) or set(identity) != {"device", "inode"}
            or any(type(identity[key]) is not int or identity[key] < 0 for key in identity)
        ):
            raise ValueError("Portable mux endpoint has incomplete native proof")
        return cls(
            value["attempt"], value["session_id"], value["mux_session"], socket,
            native["incarnation"], native["pane"],
            identity["device"], identity["inode"],
        )

    @classmethod
    def from_receipt(cls, receipt: dict[str, Any]) -> MuxEndpoint:
        """Select addressing from the receiver's retained launch receipt."""
        return cls.parse({
            "version": receipt.get("version"), "attempt": receipt.get("attempt"),
            "session_id": receipt.get("session_id"), "mux_session": receipt.get("mux_session"),
            "mux_socket": receipt.get("mux_socket"), "native": receipt.get("native"),
            "socket_identity": receipt.get("socket_identity"),
        })

    @property
    def confirmed(self) -> bool:
        return self.incarnation is not None

    def record(self) -> dict[str, Any]:
        """Return the transport form, retaining pending identity explicitly."""
        return {
            "version": 1, "attempt": self.attempt, "session_id": self.session_id,
            "mux_session": self.mux_session, "mux_socket": self.mux_socket,
            "native": {"incarnation": self.incarnation, "pane": self.pane} if self.confirmed else None,
            "socket_identity": {
                "device": self.device, "inode": self.inode,
            } if self.confirmed else None,
        }

    def command(self, action: str, *, lines: int = 80) -> str:
        """Build a bounded control command which first proves the native endpoint."""
        self.parse(self.record())
        if not self.confirmed:
            raise ValueError("Portable mux creation is unconfirmed; retain its original attempt")
        if action not in {"observe", "tail", "stop", "attach"}:
            raise ValueError("Unsupported portable mux action")
        if type(lines) is not int or not 1 <= lines <= 2000:
            raise ValueError("Portable mux tail requires 1..2000 lines")
        return shlex.join([
            "python3", "-I", "-S", "-c", _CONTROL, action,
            json.dumps(self.record(), sort_keys=True, separators=(",", ":")), str(lines),
        ])


_CONTROL = r'''
import json, os, pathlib, stat, subprocess, sys

def main():
    action, endpoint, lines = sys.argv[1], json.loads(sys.argv[2]), int(sys.argv[3])
    socket = pathlib.Path(endpoint["mux_socket"])
    expected = endpoint["socket_identity"]
    def check():
        for parent in socket.parents:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ValueError("Portable mux parent changed")
        info = socket.lstat()
        if not stat.S_ISSOCK(info.st_mode) or expected != {
            "device": info.st_dev, "inode": info.st_ino,
        }:
            raise ValueError("Portable mux socket changed")
    check()
    command = ["tmux", "-S", endpoint["mux_socket"]]
    result = subprocess.run(
        [*command, "list-panes", "-a", "-F",
         "#{session_name}|#{session_id}:#{session_created}|#{pane_id}"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
    )
    check()
    if result.returncode:
        raise ValueError("Portable mux observation failed")
    rows = [row.split("|") for row in result.stdout.splitlines()]
    if any(len(row) != 3 for row in rows):
        raise ValueError("Portable mux observation is incomplete")
    native = endpoint["native"]
    matching = [row for row in rows if row[1:] == [native["incarnation"], native["pane"]]]
    if not matching:
        if any(row[0] == endpoint["mux_session"] or row[1] == native["incarnation"] for row in rows) or action != "observe":
            raise ValueError("Portable mux incarnation changed or unavailable")
        print("gone")
        return
    if action == "observe":
        print("present")
        return
    check()
    if action == "attach":
        os.execvp(command[0], [*command, "attach-session", "-t",
                              endpoint["native"]["incarnation"].split(":")[0]])
    args = (["kill-session", "-t", endpoint["native"]["incarnation"].split(":")[0]]
            if action == "stop" else
            ["capture-pane", "-p", "-t", endpoint["native"]["pane"], "-S", "-" + str(lines)])
    result = subprocess.run(
        [*command, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        raise ValueError("Portable mux control did not confirm success")
    if action == "tail":
        print("\n".join(result.stdout.splitlines()[-lines:]))
    else:
        print("STOPPED")

try:
    main()
except (OSError, ValueError, KeyError, subprocess.SubprocessError):
    if sys.argv[1] == "observe":
        print("unknown")
    else:
        sys.exit("Portable mux endpoint is unconfirmed; original custody is retained")
'''
