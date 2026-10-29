"""Finite retirement worker; native pins never belong to the installer Job."""

from __future__ import annotations

import os
import socket
import sys
import time

from zdd.private_protocol import (
    Deadline,
    PeerIdentity,
    PrivateIPCError,
    Refusal,
    read_frame,
    write_frame,
)

from .process_retirement import PinnedProcess, RetirementRefused


def _release(pins: dict, held: dict) -> None:
    for pid, pin in reversed(tuple(pins.items())):
        try:
            pin.close()
        except (OSError, RetirementRefused):
            held[pid] = pin
    # A failed Windows resume has ambiguous suspend-count effects. Retain the
    # actual handle rather than retrying the resume or abandoning a live pin.
    while held:
        for pid, pin in tuple(held.items()):
            try:
                if pin.exited(.1):
                    pin.close()
                    del held[pid]
            except (OSError, RetirementRefused):
                pass
        if held:
            time.sleep(.1)


def serve(channel, owner_pid: int) -> None:
    """Admit only explicit original identities over the owner's lifeline."""
    pins = {}
    held = {}
    try:
        write_frame(channel, {"ready": True}, Deadline(10))
        while True:
            deadline = Deadline(30)
            request = read_frame(channel, deadline)
            channel.check_peer(deadline)
            operation, pid = request.get("operation"), request.get("pid")
            expected = (
                {"operation", "pid", "birth"} if operation == "pin" else {"operation", "pid"}
            )
            if set(request) != expected or type(pid) is not int or pid == owner_pid:
                raise PrivateIPCError(Refusal.PROTOCOL)
            try:
                if operation == "pin":
                    if pid in pins or len(pins) >= 4096 or not isinstance(request["birth"], str):
                        raise PrivateIPCError(Refusal.PROTOCOL)
                    pins[pid] = PinnedProcess(pid, request["birth"])
                    result = None
                else:
                    if (
                        operation not in {"pause", "exited", "terminate", "close"}
                        or pid not in pins
                    ):
                        raise PrivateIPCError(Refusal.PROTOCOL)
                    result = getattr(pins[pid], operation)()
                    if operation == "close":
                        del pins[pid]
                response = {"result": result}
            except ProcessLookupError:
                response = {"error": "lookup"}
            except (OSError, ValueError, RetirementRefused):
                if operation == "close" and pid in pins:
                    held[pid] = pins.pop(pid)
                response = {"error": "control"}
            write_frame(channel, response, deadline)
            if held:
                break
    except (OSError, PrivateIPCError):
        pass
    finally:
        try:
            channel.close()
        finally:
            _release(pins, held)


def main() -> None:
    owner_pid = int(sys.argv[1])
    if sys.argv[2] == "pipe" and os.name == "nt" and len(sys.argv) == 6:
        from zdd.private_windows import connect

        channel = connect(
            sys.argv[3], PeerIdentity(owner_pid, sys.argv[4], sys.argv[5]), Deadline(15),
        )
    elif sys.argv[2] == "socket" and sys.platform.startswith("linux") and len(sys.argv) == 5:
        from ._retirement_channel import InheritedChannel

        channel = InheritedChannel(socket.socket(fileno=int(sys.argv[3])), int(sys.argv[4]))
    else:
        raise RetirementRefused("Retirement custodian transport is invalid")
    serve(channel, owner_pid)
