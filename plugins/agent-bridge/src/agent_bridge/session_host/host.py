"""The Session Host: owns one Copilot ``--acp`` child and serves a reattachable
endpoint, relaying ACP frames 1:1 with a seq/ack control channel.

Asyncio-based to match agent-bridge. The host is intentionally child-agnostic:
it accepts any object satisfying :class:`ChildProcess` (a real
``asyncio.subprocess.Process`` does), so it is unit-testable in-process with a
fake child and has no hard dependency on the spawn path.

Frame identity: ACP over stdio is newline-delimited JSON-RPC, so each
newline-terminated line from the child's stdout is one frame. The host assigns a
**monotonic sequence** it never renumbers -- that stable sequence is what lets a
reattaching frontend (or a cold frontend rebuild) preserve delivery-cursor
identity (Phase 3 anchors event IDs to it).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Protocol, runtime_checkable

from . import protocol as proto

log = logging.getLogger("agent-bridge.session-host")


@runtime_checkable
class ChildProcess(Protocol):
    """The subset of ``asyncio.subprocess.Process`` the host relies on."""

    stdin: asyncio.StreamWriter | None
    stdout: asyncio.StreamReader | None

    @property
    def pid(self) -> int | None: ...

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...


class _Front:
    """State for the single currently-attached frontend."""

    __slots__ = ("reader", "writer", "next_seq", "send_lock", "closed")

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 next_seq: int) -> None:
        self.reader = reader
        self.writer = writer
        self.next_seq = next_seq          # next frame seq owed to this front
        self.send_lock = asyncio.Lock()
        self.closed = False


class SessionHost:
    """Owns a child + its pipes; serves a reattachable 1:1-ACP endpoint.

    The host keeps reading and buffering child frames whether or not a frontend
    is attached -- child progress is decoupled from frontend presence, which is
    the entire point. Buffered frames past the durable ``ack`` cursor are
    retained so a reattaching frontend resumes with no gap and no re-stream.
    """

    def __init__(self, child: ChildProcess, *, nonce: str = "",
                 unexpected_reap_seconds: float = 60.0,
                 active_reap_seconds: float = 0.0) -> None:
        self._child = child
        self._nonce = nonce or ""
        self._frames: dict[int, bytes] = {}
        self._max_seq = 0
        self._ack_cursor = 0
        self._front: _Front | None = None
        self._child_exit: int | None = None
        self._child_done = asyncio.Event()
        self._server: asyncio.base_events.Server | None = None
        self._reader_task: asyncio.Task | None = None
        self._closing = False
        # Auto-reap of an idle child once the front is lost (#51). The front
        # continuously reports whether the child is REAPABLE (its turn completed
        # with no active background tasks) via STATUS, and signals a *graceful*
        # disconnect via DETACH. When the front is lost:
        #   * graceful (DETACH) + reapable -> reap PROMPTLY;
        #   * unexpected (bare EOF) + last-known reapable -> reap after this grace
        #     window (a reattach cancels it);
        #   * not reapable (mid-turn / active background work) -> stay alive so a
        #     reattach can resume the turn -- never reap inadvertently (goal 1).
        # 0 disables the unexpected-grace self-reap (the graceful path still acts).
        self._unexpected_reap_seconds = unexpected_reap_seconds
        # Bounded keep-alive for an ACTIVE (non-reapable, mid-turn / active
        # background work) child after the front is lost (#145). Unlike the
        # unexpected-idle grace above -- which only frees an already-idle child
        # -- this holds a child that is still mid-work so a reconnecting front
        # can resume the in-flight turn/tool call. The hold is progress-aware:
        # each window that sees new child frames re-arms (a task in progress is
        # never killed mid-flight); the child is let go only after a full window
        # with no output and no reattach (the session stays resumable from disk
        # + worktree). This is the operator's "keep the task alive after a sever"
        # bound. 0 disables (the legacy behavior: an active front-less child
        # lives until its own stop). A reattach cancels it.
        self._active_reap_seconds = active_reap_seconds
        self._last_reapable = False
        self._graceful_detach = False
        self._reap_timer: asyncio.TimerHandle | None = None
        self._active_reap_timer: asyncio.TimerHandle | None = None
        # The child-frame high-water mark captured when the active-hold timer was
        # armed. If the child has produced new frames since (``_max_seq`` moved),
        # a task is still actively streaming work, so the hold is *re-armed*
        # rather than the child killed mid-task -- we only reclaim a front-less
        # active child once it has gone quiet for a full window (wedged/idle, no
        # task in progress). This keeps a genuinely-working severed child alive
        # to its natural boundary using only the host's frame-agnostic progress
        # signal, never parsing ACP frames.
        self._active_reap_since_seq = 0
        # Protocol-aware turn state (observational only -- see ``_observe_frame``).
        # The set of ``session/prompt`` JSON-RPC request ids the host has seen
        # written toward the child but not yet answered; non-empty == a turn is
        # genuinely in flight. Used ONLY to make front-less reap decisions
        # turn-boundary-accurate -- so a long turn that falls *silent* (a slow
        # test emitting no output) is held as in-progress rather than mistaken
        # for idle. Frames are still relayed verbatim; this never gates, rewrites
        # or drops them, and any unparseable/unknown frame leaves the state
        # unchanged (fail-safe: uncertainty biases toward "in a turn" -> hold).
        self._inflight_prompt_ids: set[object] = set()
        self._self_reap_task: asyncio.Task | None = None

    # -- introspection (tests / diagnostics) -------------------------------
    @property
    def max_seq(self) -> int:
        return self._max_seq

    @property
    def ack_cursor(self) -> int:
        return self._ack_cursor

    @property
    def buffered_seqs(self) -> list[int]:
        return sorted(self._frames)

    @property
    def child_pid(self) -> int | None:
        return self._child.pid

    @property
    def child_alive(self) -> bool:
        return self._child.returncode is None

    # -- child reader ------------------------------------------------------
    async def _reader_loop(self) -> None:
        stdout = self._child.stdout
        assert stdout is not None
        while True:
            line = await stdout.readline()
            if not line:
                break
            self._max_seq += 1
            self._frames[self._max_seq] = line
            # Observe (never gate) the agent->client frame for turn state.
            self._observe_frame(line, to_child=False)
            front = self._front
            if front is not None and not front.closed:
                await self._flush_front(front)
        # child stdout closed -> child exiting
        try:
            self._child_exit = await self._child.wait()
        except ProcessLookupError:
            self._child_exit = -1
        self._child_done.set()
        front = self._front
        if front is not None and not front.closed:
            await self._safe_send(front, proto.MsgType.LIVENESS,
                                  proto.pack_liveness(False, self._child_exit or 0))

    # -- frontend serving --------------------------------------------------
    async def _flush_front(self, front: _Front) -> None:
        """Send every buffered frame the front is still owed, in order.

        Idempotent and re-entrant-safe: both the reader loop (new frame) and the
        attach handshake (replay) call it; the per-front lock serializes sends
        and ``next_seq`` guarantees no gap and no duplicate.
        """
        async with front.send_lock:
            while not front.closed:
                seq = front.next_seq
                data = self._frames.get(seq)
                if data is None:
                    if seq > self._max_seq:
                        return  # nothing more buffered yet
                    front.next_seq = seq + 1  # trimmed below ack; skip
                    continue
                try:
                    await proto.write_message(
                        front.writer, proto.MsgType.FRAME,
                        proto.pack_frame(seq, data),
                    )
                except (OSError, ConnectionError):
                    front.closed = True
                    return
                front.next_seq = seq + 1

    async def _safe_send(self, front: _Front, mtype: proto.MsgType,
                         payload: bytes) -> None:
        async with front.send_lock:
            if front.closed:
                return
            try:
                await proto.write_message(front.writer, mtype, payload)
            except (OSError, ConnectionError):
                front.closed = True

    async def _handle_front(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> None:
        # Displace any stale front (its process almost always already gone).
        old = self._front
        if old is not None and not old.closed:
            old.closed = True
            with _suppress_close(old.writer):
                old.writer.close()
        # A (re)attach cancels any pending unexpected-disconnect reap and clears the
        # graceful-detach latch: the child is watched again, so its lifetime is
        # once more owned by the front.
        self._cancel_reap_timer()
        self._graceful_detach = False

        first = await proto.read_message(reader)
        if first is None or first[0] != proto.MsgType.ATTACH:
            writer.close()
            return
        last_acked, nonce = proto.unpack_attach(first[1])
        # Connect-auth: a host launched with a nonce refuses any front that does
        # not present the matching token (defense-in-depth against a same-user
        # process dialing the loopback/forwarded port). An unsecured host (no
        # nonce configured) accepts all, preserving legacy behavior.
        if self._nonce and nonce.decode("utf-8", "replace") != self._nonce:
            log.warning("frontend presented an invalid connect nonce; rejecting")
            writer.close()
            return
        self._ack_cursor = max(self._ack_cursor, last_acked)
        front = _Front(reader, writer, next_seq=last_acked + 1)
        self._front = front

        await self._safe_send(
            front, proto.MsgType.HELLO,
            proto.pack_u64(self._max_seq) + proto.pack_u64(self._child.pid or 0),
        )
        if not self.child_alive:
            await self._safe_send(front, proto.MsgType.LIVENESS,
                                  proto.pack_liveness(False, self._child_exit or 0))
        await self._flush_front(front)

        try:
            while not self._closing:
                msg = await proto.read_message(reader)
                if msg is None:
                    break  # front detached; child keeps running
                mtype, payload = msg
                if mtype == proto.MsgType.ACK:
                    self._on_ack(proto.unpack_u64(payload))
                elif mtype == proto.MsgType.WRITE:
                    await self._on_write(payload)
                elif mtype == proto.MsgType.STATUS:
                    self._last_reapable = proto.unpack_flag(payload)
                elif mtype == proto.MsgType.DETACH:
                    # Graceful disconnect: latch it (+ the reapable state it
                    # carries) and drop the connection. The finally below runs
                    # the front-lost decision, which reaps promptly if reapable.
                    self._last_reapable = proto.unpack_flag(payload)
                    self._graceful_detach = True
                    break
                elif mtype == proto.MsgType.TERMINATE:
                    await self._terminate_child()
                    break
        except proto.ProtocolError:
            log.warning("protocol error from frontend; dropping connection")
        finally:
            front.closed = True
            if self._front is front:
                self._front = None
            with _suppress_close(writer):
                writer.close()
            self._on_front_lost()

    def _on_ack(self, seq: int) -> None:
        self._ack_cursor = max(self._ack_cursor, seq)
        # Trim durably-acked frames from the buffer.
        for s in [s for s in self._frames if s <= self._ack_cursor]:
            del self._frames[s]

    async def _on_write(self, payload: bytes) -> None:
        # Observe (never gate) the client->agent frame for turn state, then relay
        # the bytes verbatim.
        self._observe_frame(payload, to_child=True)
        stdin = self._child.stdin
        if stdin is None:
            return
        try:
            stdin.write(payload)
            await stdin.drain()
        except (OSError, ConnectionError):
            log.warning("failed to relay WRITE to child stdin")

    # -- protocol-aware turn state (observational; never gates the relay) ---
    _PROMPT_METHOD = "session/prompt"

    @property
    def _turn_in_flight(self) -> bool:
        """True while at least one ``session/prompt`` request is unanswered."""
        return bool(self._inflight_prompt_ids)

    def _observe_frame(self, raw: bytes, *, to_child: bool) -> bool:
        """Best-effort, tolerant peek at one relayed ACP frame to track whether a
        ``session/prompt`` turn is in flight. Returns True iff this frame closed
        the last outstanding turn (a boundary was just reached).

        Future-compatible by construction: it keys only off JSON-RPC structural
        invariants (a request carries ``method`` + ``id``; a response carries
        ``id`` + ``result``/``error`` and no ``method``) plus the stable
        ``session/prompt`` method name, and silently ignores anything it cannot
        classify -- notifications, unknown methods, batch arrays, partial or
        non-JSON bytes. It never raises and never mutates the frame; the caller
        relays the bytes verbatim regardless.
        """
        was_in_turn = bool(self._inflight_prompt_ids)
        for chunk in raw.split(b"\n"):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                msg = json.loads(chunk)
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            mid = msg.get("id")
            if mid is None:
                continue
            if to_child:
                # client -> agent: a prompt REQUEST opens a turn.
                if msg.get("method") == self._PROMPT_METHOD:
                    try:
                        self._inflight_prompt_ids.add(mid)
                    except TypeError:
                        pass  # unhashable id -- ignore (fail-safe)
            elif "method" not in msg and ("result" in msg or "error" in msg):
                # agent -> client: a RESPONSE to a tracked prompt closes it.
                self._inflight_prompt_ids.discard(mid)
        return was_in_turn and not self._inflight_prompt_ids

    async def _terminate_child(self) -> None:
        """Explicit, sanctioned reap (goal 1's only allowed termination path)."""
        rc = self._child.returncode
        if rc is not None:
            return
        proc = self._child
        # Best-effort: prefer the real Process tree-kill if available.
        killer = getattr(proc, "kill", None)
        if callable(killer):
            try:
                proc.kill()  # type: ignore[attr-defined]
            except (ProcessLookupError, OSError):
                pass

    # -- front-lost auto-reap (#51) ----------------------------------------
    def _on_front_lost(self) -> None:
        """Decide the child's fate when the front connection ends.

        Called from the front handler's ``finally``. Never reaps a non-reapable
        (mid-turn / active-background-work) child, nor one whose front is still
        present (a displacing reattach already cleared these); such a child stays
        alive so a reattach resumes it. Otherwise:

        * a **graceful** detach (DETACH seen) reaps a reapable child at once;
        * an **unexpected** drop (bare EOF) arms a grace-window timer so a quick
          reattach still wins, and only then reaps.

        An **active** (non-reapable) child is never freed by the paths above; if
        an ``active_reap_seconds`` bound is set it instead arms a longer hold
        timer so a reconnecting front can resume the in-flight turn. That hold is
        **turn-aware**: while a ``session/prompt`` turn is genuinely in flight
        (protocol-observed) the child is held regardless of output silence -- a
        long, quiet turn is never killed mid-task. With no turn in flight the
        hold is progress-aware -- it re-arms while frames still stream (e.g. a
        background sub-agent working) and lets the child go only after a full
        window with no output and no reattach (idle, resumable).
        """
        if self._closing or self._front is not None:
            return
        if not self.child_alive:
            return
        if self._last_reapable:
            if self._graceful_detach:
                self._schedule_self_reap("graceful detach + idle child")
            elif self._unexpected_reap_seconds > 0:
                self._arm_reap_timer()
        elif self._active_reap_seconds > 0:
            self._arm_active_reap_timer()

    def _arm_reap_timer(self) -> None:
        self._cancel_reap_timer()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._reap_timer = loop.call_later(
            self._unexpected_reap_seconds, self._on_reap_timer,
        )

    def _cancel_reap_timer(self) -> None:
        if self._reap_timer is not None:
            self._reap_timer.cancel()
            self._reap_timer = None
        if self._active_reap_timer is not None:
            self._active_reap_timer.cancel()
            self._active_reap_timer = None

    def _arm_active_reap_timer(self) -> None:
        """Arm the bounded, progress-aware hold for an active, front-less child
        (#145). Captures the current frame high-water mark so the timer can tell
        whether the child produced work during the window (re-arm) or went quiet
        (reclaim)."""
        self._cancel_reap_timer()
        self._active_reap_since_seq = self._max_seq
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._active_reap_timer = loop.call_later(
            self._active_reap_seconds, self._on_active_reap_timer,
        )

    def _on_active_reap_timer(self) -> None:
        self._active_reap_timer = None
        # A reattach (front present) or the child exiting on its own vetoes the
        # reap.
        if self._closing or self._front is not None:
            return
        if not self.child_alive:
            return
        # A genuinely in-flight prompt turn is protected regardless of output:
        # a long turn that falls silent (a slow test with no stdout) must never
        # be reaped mid-task. Hold by re-arming; the turn's completion (observed
        # as the prompt response -> boundary) or a reattach reclaims it.
        if self._turn_in_flight:
            self._arm_active_reap_timer()
            return
        # No turn in flight. Reclaim only if the child produced nothing during
        # the window (quiet + idle -> safe, resumable). Any output -- e.g. a
        # background sub-agent still emitting -- re-arms rather than reaping.
        if self._max_seq > self._active_reap_since_seq:
            self._arm_active_reap_timer()
            return
        self._schedule_self_reap(
            f"unexpected disconnect + idle child quiet for "
            f"{self._active_reap_seconds:.0f}s with no reattach"
        )

    def _on_reap_timer(self) -> None:
        self._reap_timer = None
        # Re-check every precondition at fire time: a reattach (front present),
        # a new turn (reapable cleared), or the child already exiting all veto.
        if self._closing or self._front is not None:
            return
        if not self.child_alive or not self._last_reapable:
            return
        self._schedule_self_reap(
            f"unexpected disconnect + idle child ({self._unexpected_reap_seconds:.0f}s)"
        )

    def _schedule_self_reap(self, reason: str) -> None:
        if self._self_reap_task is not None and not self._self_reap_task.done():
            return
        self._self_reap_task = asyncio.create_task(self._self_reap(reason))

    async def _self_reap(self, reason: str) -> None:
        """Kill the idle child and stop serving so the host process exits.

        The STOPPED session is resumable from persisted state + worktree (a fresh
        child + ``load_session`` replay), so freeing an idle child here loses
        nothing -- it only reclaims the ~memory the detached host was pinning.
        """
        log.info("Session host self-reaping (child pid=%s): %s",
                 self._child.pid, reason)
        await self._terminate_child()
        await self.close()

    # -- lifecycle ---------------------------------------------------------
    async def serve(self, host: str = "127.0.0.1", port: int = 0) -> int:
        """Start serving on loopback. Returns the bound port."""
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._server = await asyncio.start_server(
            self._handle_front, host, port, limit=proto.MAX_MESSAGE_BYTES)
        sock = self._server.sockets[0]
        return sock.getsockname()[1]

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        self._closing = True
        self._cancel_reap_timer()
        if self._server is not None:
            self._server.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass


class _suppress_close:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True  # swallow close-time errors
