"""Stub Session Host for the Phase-0 spike (aperture-labs #1761).

Owns exactly one child process (synthetic ``child_sim`` or a real
``copilot --acp --stdio``) and its stdio pipes -- it is the child's *real*
parent, the thing that must never blink. Serves a reattachable loopback
endpoint (``wire`` protocol) so any front generation can attach/detach without
the child noticing.

Design invariants honored here:

* **1:1 ACP data relay** -- child stdout is split only on newline frame
  boundaries; each frame's bytes are forwarded verbatim. No ACP semantics.
* **Control channel** -- reattach handshake (``ATTACH``/``HELLO``), monotonic
  ``seq`` + ``ACK`` cursor with an unacked buffer (so a reattaching front misses
  nothing and re-reads nothing), child liveness, explicit ``TERMINATE``.
* **Per-OS survival** -- POSIX new-session; Windows own kill-on-close Job after
  having been spawned with ``CREATE_BREAKAWAY_FROM_JOB`` (see ``osutil``).

The host keeps reading + buffering child frames whether or not a front is
attached: child progress is decoupled from front presence. That is the whole
point of the spike.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import osutil
import wire


class SessionHost:
    def __init__(self, child_cmd: list[str], port: int, state_file: Path) -> None:
        self.child_cmd = child_cmd
        self.port = port
        self.state_file = state_file

        self._lock = threading.Lock()
        self._frames: dict[int, bytes] = {}
        self._max_seq = 0
        self._ack_cursor = 0
        self._front: dict | None = None  # {sock, send_lock, next_seq}
        self._running = True

        self.proc: subprocess.Popen | None = None
        self.child_exited = threading.Event()
        self.child_exit_code: int | None = None

    # ---- child lifecycle -------------------------------------------------
    def spawn_child(self) -> None:
        creationflags = 0
        start_new_session = False
        if osutil.IS_WIN:
            # Host was itself spawned with CREATE_BREAKAWAY_FROM_JOB, so it is
            # outside the front's kill-on-close job. Give the host its OWN
            # kill-on-close job so the child dies with the HOST (correct) and
            # not with the front. Children inherit this job automatically.
            osutil.arm_self_job(kill_on_close=True, breakaway_ok=True)
            creationflags = osutil.CREATE_NO_WINDOW
        else:
            start_new_session = True

        self.proc = subprocess.Popen(
            self.child_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=start_new_session,
            bufsize=0,
        )

    def _reader_loop(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            if not line:
                break
            with self._lock:
                self._max_seq += 1
                self._frames[self._max_seq] = line
                front = self._front
            if front is not None:
                self._flush_front(front)
        # child stdout closed -> child is exiting
        self.proc.wait()
        self.child_exit_code = self.proc.returncode
        self.child_exited.set()
        with self._lock:
            front = self._front
        if front is not None:
            try:
                with front["send_lock"]:
                    wire.send_msg(front["sock"], wire.LIVENESS,
                                  wire.pack_liveness(False, self.child_exit_code or 0))
            except OSError:
                pass

    # ---- front push (in-order, no-dup, no-gap) ---------------------------
    def _flush_front(self, front: dict) -> None:
        try:
            with front["send_lock"]:
                while True:
                    with self._lock:
                        if self._front is not front:
                            return
                        seq = front["next_seq"]
                        data = self._frames.get(seq)
                        cur_max = self._max_seq
                    if data is None:
                        if seq > cur_max:
                            return  # nothing more buffered yet
                        front["next_seq"] = seq + 1  # trimmed; skip
                        continue
                    wire.send_msg(front["sock"], wire.FRAME, wire.pack_frame(seq, data))
                    front["next_seq"] = seq + 1
        except OSError:
            with self._lock:
                if self._front is front:
                    self._front = None

    # ---- control channel -------------------------------------------------
    def _serve_front(self, conn: socket.socket) -> None:
        first = wire.recv_msg(conn)
        if first is None or first[0] != wire.ATTACH:
            conn.close()
            return
        last_acked = wire.unpack_u64(first[1])
        front = {"sock": conn, "send_lock": threading.Lock(),
                 "next_seq": last_acked + 1}
        with self._lock:
            self._front = front
            max_seq = self._max_seq
            child_pid = self.proc.pid if self.proc else 0
            self._ack_cursor = max(self._ack_cursor, last_acked)
        with front["send_lock"]:
            wire.send_msg(conn, wire.HELLO,
                          wire.pack_u64(max_seq) + wire.pack_u64(child_pid))
        # Replay any buffered frames past last_acked, then live tail.
        self._flush_front(front)

        while self._running:
            msg = wire.recv_msg(conn)
            if msg is None:
                break  # front detached -- child keeps running
            mtype, payload = msg
            if mtype == wire.ACK:
                seq = wire.unpack_u64(payload)
                with self._lock:
                    self._ack_cursor = max(self._ack_cursor, seq)
                    # trim acked frames from the buffer
                    for s in [s for s in self._frames if s <= self._ack_cursor]:
                        del self._frames[s]
            elif mtype == wire.WRITE:
                if self.proc and self.proc.stdin:
                    try:
                        self.proc.stdin.write(payload)
                        self.proc.stdin.flush()
                    except OSError:
                        pass
            elif mtype == wire.TERMINATE:
                self._terminate_child()
                self._running = False
                break
        with self._lock:
            if self._front is front:
                self._front = None
        try:
            conn.close()
        except OSError:
            pass

    def _terminate_child(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        pid = self.proc.pid
        if osutil.IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import os
            import signal
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                self.proc.terminate()

    # ---- run -------------------------------------------------------------
    def run(self) -> int:
        self.spawn_child()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", self.port))
        server.listen(4)
        actual_port = server.getsockname()[1]

        self.state_file.write_text(json.dumps({
            "host_pid": __import__("os").getpid(),
            "child_pid": self.proc.pid if self.proc else None,
            "port": actual_port,
        }))

        reader = threading.Thread(target=self._reader_loop, daemon=True)
        reader.start()

        server.settimeout(0.5)
        while self._running:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                self._serve_front(conn)
            except OSError:
                # A front dying (RST, closed handle) must never bring the host
                # down -- that is the entire point of survive-and-reattach.
                with self._lock:
                    self._front = None
                try:
                    conn.close()
                except OSError:
                    pass
        server.close()
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--state-file", required=True)
    ap.add_argument("--child-cmd", required=True, help="JSON list")
    args = ap.parse_args()

    child_cmd = json.loads(args.child_cmd)
    host = SessionHost(child_cmd, args.port, Path(args.state_file))
    return host.run()


if __name__ == "__main__":
    sys.exit(main())
