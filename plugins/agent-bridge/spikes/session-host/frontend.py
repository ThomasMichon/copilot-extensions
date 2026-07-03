"""Stub frontend for the Phase-0 spike (aperture-labs #1761).

Two roles:

* **role 1** -- spawns the Session Host (exercising the front->host job/session
  chain), attaches fresh, drives a prompt, reads a few frames, ACKs them, then
  **hard-exits mid-turn** (``os._exit`` -- a crash, not a graceful shutdown) to
  simulate the front dying while a turn is in flight.
* **role 2** -- a *fresh* front process reattaches to the surviving host from
  role 1's last-acked ``seq`` and streams the turn through to ``turn_complete``.

On Windows role 1 optionally arms a kill-on-close Job on itself (modelling
agent-bridge's ``winjob``) and spawns the host with ``CREATE_BREAKAWAY_FROM_JOB``
-- the crux under test. ``--front-breakaway-ok`` toggles whether the front's job
permits that escape (the negative control when false).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import osutil
import wire

HERE = Path(__file__).resolve().parent


def _wait_state(state_file: Path, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                if data.get("port") and data.get("child_pid"):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.05)
    raise TimeoutError(f"host state file never became ready: {state_file}")


def _connect(port: int, timeout: float = 10.0) -> socket.socket:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=5.0)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return s
        except OSError as e:
            last_err = e
            time.sleep(0.05)
    raise TimeoutError(f"could not connect to host on :{port}: {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", type=int, required=True, choices=(1, 2))
    ap.add_argument("--state-file", required=True)
    ap.add_argument("--result-file", required=True)
    ap.add_argument("--last-acked", type=int, default=0)
    ap.add_argument("--cutoff", type=int, default=3, help="role 1: frames before mid-turn crash")
    ap.add_argument("--crash-after", type=float, default=0.0,
                    help="role 1: seconds before mid-turn crash even if <cutoff frames (real-copilot survival)")
    ap.add_argument("--read-for", type=float, default=0.0,
                    help="role 2: max seconds to read before graceful exit (real-copilot survival)")
    ap.add_argument("--spawn-host", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--child-cmd", default="", help="JSON list; role 1 with --spawn-host")
    ap.add_argument("--front-job", action="store_true", help="Windows: arm kill-on-close job on self")
    ap.add_argument("--front-breakaway-ok", action="store_true")
    ap.add_argument("--host-breakaway", action="store_true")
    ap.add_argument("--no-prompt", action="store_true",
                    help="role 1: do not send the synthetic prompt (real-copilot survival)")
    args = ap.parse_args()

    state_file = Path(args.state_file)
    result_file = Path(args.result_file)

    # Windows crux: model agent-bridge's kill-on-close job on the front.
    if args.front_job and osutil.IS_WIN:
        osutil.arm_self_job(kill_on_close=True, breakaway_ok=args.front_breakaway_ok)

    _host_proc: subprocess.Popen | None = None
    if args.spawn_host:
        child_cmd = json.loads(args.child_cmd)
        cflags = osutil.child_creationflags(breakaway=args.host_breakaway)
        _host_proc = subprocess.Popen(
            [sys.executable, str(HERE / "session_host.py"),
             "--port", str(args.port), "--state-file", str(state_file),
             "--child-cmd", json.dumps(child_cmd)],
            creationflags=cflags,
            start_new_session=(not osutil.IS_WIN),
        )

    state = _wait_state(state_file)
    port = state["port"]
    child_pid = state["child_pid"]
    host_pid = state.get("host_pid")

    sock = _connect(port)
    wire.send_msg(sock, wire.ATTACH, wire.pack_u64(args.last_acked))

    hello = wire.recv_msg(sock)
    if hello is None or hello[0] != wire.HELLO:
        raise RuntimeError("no HELLO from host")
    hello_max = wire.unpack_u64(hello[1][:8])
    hello_child = wire.unpack_u64(hello[1][8:16])

    if args.role == 1 and not args.no_prompt:
        # Trigger the child's turn stream.
        wire.send_msg(sock, wire.WRITE,
                      (json.dumps({"prompt": "spike-go"}) + "\n").encode())

    seqs: list[int] = []
    saw_complete = False
    last_acked = args.last_acked

    def write_result() -> None:
        result_file.write_text(json.dumps({
            "role": args.role,
            "child_pid": child_pid,
            "host_pid": host_pid,
            "hello_child": hello_child,
            "hello_max": hello_max,
            "port": port,
            "seqs": seqs,
            "first_seq": seqs[0] if seqs else None,
            "last_seq": seqs[-1] if seqs else None,
            "count": len(seqs),
            "last_acked": last_acked,
            "saw_complete": saw_complete,
        }))

    sock.settimeout(0.25)
    start = time.time()
    while True:
        try:
            msg = wire.recv_msg(sock)
        except socket.timeout:
            if args.role == 1 and args.crash_after and (time.time() - start) >= args.crash_after:
                write_result()
                sys.stdout.flush()
                os._exit(7)
            if args.role == 2 and args.read_for and (time.time() - start) >= args.read_for:
                break
            continue
        if msg is None:
            break
        mtype, payload = msg
        if mtype == wire.FRAME:
            seq, data = wire.unpack_frame(payload)
            seqs.append(seq)
            wire.send_msg(sock, wire.ACK, wire.pack_u64(seq))
            last_acked = seq
            try:
                saw_complete = saw_complete or (b"turn_complete" in data)
            except TypeError:
                pass
            if args.role == 1 and len(seqs) >= args.cutoff:
                # Persist progress and CRASH mid-turn (no cleanup).
                write_result()
                sys.stdout.flush()
                os._exit(7)
            if args.role == 2 and saw_complete:
                break
        elif mtype == wire.LIVENESS:
            alive = payload[:1] == b"\x01"
            if not alive:
                break

    write_result()
    try:
        sock.close()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
