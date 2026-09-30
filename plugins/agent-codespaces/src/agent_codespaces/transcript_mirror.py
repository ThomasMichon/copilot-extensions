"""Mirror a running CodeSpace session's transcript to this host as it grows.

A live session's history lives in its CodeSpace
(``~/.copilot/session-state/<id>/events.jsonl``); the host bridge keeps only an
in-memory tail, so after a host restart nothing on this machine could show what
came before. The Connection Owner already probes each running session every
couple of minutes. On that probe it also pulls the bytes appended since last
time to each transcript it mirrors (one short exec, by byte offset), and pushes
its mirror with agent-logger's ``session-sync push`` under its own label,
``.codespaces-live/<name>``, where the bridge's cold-store lookup (agent-logger
``session-fetch``) finds it. That namespace is the mirror's alone: the
close-out capture lands under ``.codespaces/<name>``, so neither ever replaces
or deletes the other's files (the lookup prefers the complete close-out copy),
and each push is an ordinary snapshot of a directory that only grows.

Only whole lines are mirrored, so a reader never sees a torn event; a line
longer than one read makes the next read larger. A remote transcript that
shrank (replaced) is mirrored again from its start.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ._ssh_retry import exec_with_retry
from .config import RUNTIME_DIR

log = logging.getLogger("agent-codespaces")

#: Where the host keeps each CodeSpace's mirror: ``<root>/<codespace>/session-state/<id>/``.
MIRROR_ROOT = RUNTIME_DIR / "transcripts"
#: The hub label group for live mirrors (close-out captures use ``.codespaces``).
LIVE_LABEL_GROUP = ".codespaces-live"
#: New transcripts written within this many minutes are mirrored (a running
#: session's is); one already mirrored keeps catching up regardless.
ACTIVE_MINUTES = 30
#: Bytes pulled per transcript per probe; a longer backlog catches up over later probes.
CHUNK_BYTES = 4 * 1024 * 1024
#: A chunk with no whole line doubles that transcript's read, up to this.
MAX_CHUNK_BYTES = 64 * 1024 * 1024

_SID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$")
_CODESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_HEAD = "===ACS_T "
_WORKSPACE = "===ACS_W "
_DONE = "===ACS_T_DONE"

Opener = Callable[[str], Awaitable[Any]]
Pusher = Callable[[Path, str], "tuple[bool, str]"]


def remote_script(
    offsets: dict[str, int], *, limits: dict[str, int] | None = None,
    active_minutes: int = ACTIVE_MINUTES, chunk: int = CHUNK_BYTES,
) -> str:
    """Bash that prints, for each transcript to mirror, ``<head> sid off size reset``
    then the base64 of its bytes from ``off`` (and its workspace.yaml on first sight).

    A transcript the host already mirrors (in ``offsets``) is read up to its
    ``limits`` entry, however long ago it was written; a new one only when
    written within ``active_minutes``."""
    limits = limits or {}
    known = " ".join(
        f"{sid}:{int(off)}:{int(limits.get(sid, chunk))}"
        for sid, off in sorted(offsets.items()) if _SID.match(sid)
    )
    return (
        "cd ~/.copilot/session-state 2>/dev/null || { echo " + shlex.quote(_DONE) + "; exit 0; }; "
        f"known={shlex.quote(' ' + known + ' ')}; "
        "for d in */; do sid=${d%/}; f=\"$sid/events.jsonl\"; [ -f \"$f\" ] || continue; "
        "ent=$(printf '%s' \"$known\" | tr ' ' '\\n' "
        "| awk -F: -v s=\"$sid\" '$1==s {print $2\" \"$3; exit}'); "
        "if [ -n \"$ent\" ]; then off=${ent%% *}; lim=${ent#* }; else off=0; "
        f"lim={int(chunk)}; "
        f"[ -n \"$(find \"$f\" -mmin -{int(active_minutes)} 2>/dev/null)\" ] || continue; fi; "
        "size=$(stat -c %s \"$f\" 2>/dev/null) || continue; reset=0; "
        "[ \"$size\" -lt \"$off\" ] && { off=0; reset=1; }; [ \"$size\" -gt \"$off\" ] || continue; "
        f"echo \"{_HEAD}$sid $off $size $reset\"; "
        "tail -c +$((off+1)) \"$f\" | head -c \"$lim\" | base64 -w0; echo; "
        "if [ \"$off\" = 0 ] && [ -f \"$sid/workspace.yaml\" ]; then "
        f"echo \"{_WORKSPACE}$sid\"; base64 -w0 \"$sid/workspace.yaml\"; echo; fi; "
        f"done; echo {shlex.quote(_DONE)}"
    )


Chunk = tuple[str, int, int, bool, bytes]


def parse_output(text: str) -> tuple[list[Chunk], dict[str, bytes], bool]:
    """``(chunks, workspaces, complete)``: each chunk is ``(sid, off, size, reset, data)``."""
    chunks: list[Chunk] = []
    workspaces: dict[str, bytes] = {}
    lines = (text or "").splitlines()
    complete = _DONE in (ln.strip() for ln in lines)
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if line.startswith(_HEAD):
            parts = line[len(_HEAD):].split()
            payload = lines[i].strip() if i < len(lines) else ""
            i += 1
            if len(parts) != 4 or not _SID.match(parts[0]):
                continue
            try:
                off, size, reset = int(parts[1]), int(parts[2]), parts[3] == "1"
                data = base64.b64decode(payload, validate=True)
            except ValueError:
                continue
            chunks.append((parts[0], off, size, reset, data))
        elif line.startswith(_WORKSPACE):
            sid = line[len(_WORKSPACE):].strip()
            payload = lines[i].strip() if i < len(lines) else ""
            i += 1
            if _SID.match(sid):
                try:
                    workspaces[sid] = base64.b64decode(payload, validate=True)
                except ValueError:
                    pass
    return chunks, workspaces, complete


class TranscriptMirror:
    """``await mirror(codespace)``: pull new transcript bytes, then push the mirror."""

    def __init__(
        self,
        *,
        open_manager: Opener | None = None,
        push: Pusher | None = None,
        root: Path | None = None,
        active_minutes: int = ACTIVE_MINUTES,
        chunk: int = CHUNK_BYTES,
    ) -> None:
        self._open = open_manager
        self._push = push
        self._root = root or MIRROR_ROOT
        self._active = active_minutes
        self._chunk = chunk
        self._limits: dict[tuple[str, str], int] = {}

    def _session_dir(self, codespace: str, sid: str) -> Path:
        return self._root / codespace / "session-state" / sid

    def offsets(self, codespace: str) -> dict[str, int]:
        base = self._root / codespace / "session-state"
        if not base.is_dir():
            return {}
        return {
            d.name: (d / "events.jsonl").stat().st_size
            for d in base.iterdir()
            if _SID.match(d.name) and (d / "events.jsonl").is_file()
        }

    def limits(self, codespace: str) -> dict[str, int]:
        return {sid: n for (cs, sid), n in self._limits.items() if cs == codespace}

    def apply(self, codespace: str, text: str) -> dict[str, list[str]]:
        """Append the whole lines of each chunk.

        Returns the files written, per session (``events.jsonl`` and, on first
        sight, ``workspace.yaml``); the mirror is pushed only when some were."""
        chunks, workspaces, _complete = parse_output(text)
        changed: dict[str, list[str]] = {}
        for sid, off, _size, reset, data in chunks:
            path = self._session_dir(codespace, sid) / "events.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            have = path.stat().st_size if path.is_file() else 0
            if not reset and off != have:
                continue  # stale offset (a concurrent pass moved it): the next probe resumes
            end = data.rfind(b"\n")
            key = (codespace, sid)
            if end < 0:
                # No whole line yet. A full read with none means one line is
                # longer than the read: read more next time, or it never moves.
                limit = self._limits.get(key, self._chunk)
                if len(data) >= limit and limit < MAX_CHUNK_BYTES:
                    self._limits[key] = min(limit * 2, MAX_CHUNK_BYTES)
                continue
            with open(path, "wb" if reset or off == 0 else "ab") as fh:
                fh.write(data[:end + 1])
            self._limits.pop(key, None)
            changed.setdefault(sid, []).append("events.jsonl")
        for sid, content in workspaces.items():
            ws = self._session_dir(codespace, sid) / "workspace.yaml"
            if ws.parent.is_dir():
                ws.write_bytes(content)
                changed.setdefault(sid, []).append("workspace.yaml")
        return changed

    async def __call__(self, codespace: str) -> dict[str, Any]:
        """One pass: read what's new on the box, append it here, push the mirror.

        The whole pass holds an OS lock on ``<root>/<codespace>.lock``, so two
        Owners (a replacement starting while an old one shuts down) never mirror
        the same CodeSpace at once; a pass that finds it held skips. Once the
        append-and-push runs in its worker thread, that thread owns the lock and
        releases it when it finishes: cancelling this coroutine (Owner shutdown)
        can't stop that thread, so the lock has to outlive the cancellation.
        """
        if not _CODESPACE.match(codespace or ""):
            return {"ok": False, "detail": "not a CodeSpace name"}
        from single_instance_lease import AlreadyRunningError, SingleInstance

        lease = SingleInstance(self._root, service="transcript-mirror", lock_name=f"{codespace}.lock")
        try:
            lease.acquire()
        except AlreadyRunningError:
            return {"ok": True, "changed": 0, "detail": "another pass is mirroring this CodeSpace"}
        handed_off = False
        try:
            try:
                stdout = await self._read(codespace)
            except Exception as exc:  # the box unreachable: a push still owed goes ahead
                log.debug("transcript mirror: read on %s failed: %s", codespace, exc)
                stdout = None
            if stdout is None:
                if not (self._root / f"{codespace}.dirty").exists():
                    return {"ok": False, "detail": "read failed"}
                stdout = ""  # nothing new to append; push what's already here

            def append_and_push() -> dict[str, Any]:
                try:
                    return self._append_and_push(codespace, stdout)
                finally:
                    lease.release()

            handed_off = True
            return await asyncio.to_thread(append_and_push)
        finally:
            if not handed_off:
                lease.release()

    async def _read(self, codespace: str) -> str | None:
        """What the box has beyond this mirror (the script's output), or None."""
        opener = self._open
        if opener is None:
            from .session_forwards import _open_codespace

            opener = _open_codespace
        manager = None
        try:
            manager = await opener(codespace)
            script = remote_script(
                self.offsets(codespace), limits=self.limits(codespace),
                active_minutes=self._active, chunk=self._chunk,
            )
            result = await exec_with_retry(
                manager, codespace, "bash -lc " + shlex.quote(script), timeout=60.0, attempts=2,
            )
            if getattr(result, "exit_code", None) != 0:
                log.debug("transcript mirror: read on %s exited %s", codespace,
                          getattr(result, "exit_code", None))
                return None
            return getattr(result, "stdout", "") or ""
        finally:
            if manager is not None:
                try:
                    await manager.disconnect(codespace)
                except Exception as exc:
                    log.debug("transcript mirror: disconnect from %s failed: %s", codespace, exc)

    def _append_and_push(self, codespace: str, stdout: str) -> dict[str, Any]:
        # Marked before anything is appended, cleared only once a push succeeds:
        # the appended bytes won't be read again, so a failed or interrupted
        # push is retried by the next pass (after a restart too) until it lands.
        dirty = self._root / f"{codespace}.dirty"
        if _HEAD in stdout:
            dirty.parent.mkdir(parents=True, exist_ok=True)
            dirty.touch()
        changed = self.apply(codespace, stdout)
        if not changed and not dirty.exists():
            return {"ok": True, "changed": 0}
        push = self._push
        if push is None:
            from .sessions import _push_via_session_sync

            def push(source: Path, label: str) -> tuple[bool, str]:
                return _push_via_session_sync(source, label, verbose=False)
        ok, detail = push(self._root / codespace, f"{LIVE_LABEL_GROUP}/{codespace}")
        if ok:
            dirty.unlink(missing_ok=True)
        else:
            log.warning("transcript mirror for %s: push failed (retried next pass): %s", codespace, detail)
        return {"ok": ok, "changed": len(changed), "detail": detail}
