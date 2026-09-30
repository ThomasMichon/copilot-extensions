"""Mirror a running CodeSpace session's transcript to this host as it grows.

A live session's history lives in its CodeSpace
(``~/.copilot/session-state/<id>/events.jsonl``); the host bridge keeps only an
in-memory tail, so after a host restart nothing on this machine could show what
came before. The Connection Owner already probes each running session every
couple of minutes. On that probe it also pulls the bytes appended since last
time to each recently written transcript (one short exec, by byte offset), and
hands its mirror to agent-logger's ``session-sync push`` under
``.codespaces/<name>`` -- the same storage and label the close-out capture uses,
where the bridge's cold-store lookup (agent-logger ``session-fetch``) finds it.

Only whole lines are mirrored, so a reader never sees a torn event. A remote
transcript that shrank (replaced) is mirrored again from its start.
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
#: Transcripts written within this many minutes are mirrored (a running session's is).
ACTIVE_MINUTES = 30
#: Bytes pulled per transcript per probe; a longer backlog catches up over later probes.
CHUNK_BYTES = 4 * 1024 * 1024

_SID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$")
_CODESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_HEAD = "===ACS_T "
_WORKSPACE = "===ACS_W "
_DONE = "===ACS_T_DONE"

Opener = Callable[[str], Awaitable[Any]]
Pusher = Callable[[Path, str], "tuple[bool, str]"]


def remote_script(
    offsets: dict[str, int], *, active_minutes: int = ACTIVE_MINUTES, chunk: int = CHUNK_BYTES,
) -> str:
    """Bash that prints, for each recently written transcript, ``<head> sid off size reset``
    then the base64 of its bytes from ``off`` (and its workspace.yaml on first sight)."""
    known = " ".join(
        f"{sid}:{int(off)}" for sid, off in sorted(offsets.items()) if _SID.match(sid)
    )
    return (
        "cd ~/.copilot/session-state 2>/dev/null || { echo " + shlex.quote(_DONE) + "; exit 0; }; "
        f"known={shlex.quote(' ' + known + ' ')}; "
        "for d in */; do sid=${d%/}; f=\"$sid/events.jsonl\"; [ -f \"$f\" ] || continue; "
        f"[ -n \"$(find \"$f\" -mmin -{int(active_minutes)} 2>/dev/null)\" ] || continue; "
        "off=$(printf '%s' \"$known\" | tr ' ' '\\n' | sed -n \"s/^$sid:\\([0-9]*\\)$/\\1/p\"); "
        "off=${off:-0}; size=$(stat -c %s \"$f\" 2>/dev/null) || continue; reset=0; "
        "[ \"$size\" -lt \"$off\" ] && { off=0; reset=1; }; [ \"$size\" -gt \"$off\" ] || continue; "
        f"echo \"{_HEAD}$sid $off $size $reset\"; "
        f"tail -c +$((off+1)) \"$f\" | head -c {int(chunk)} | base64 -w0; echo; "
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

    def apply(self, codespace: str, text: str) -> int:
        """Append the whole lines of each chunk; returns how many transcripts changed."""
        chunks, workspaces, _complete = parse_output(text)
        changed = 0
        for sid, off, _size, reset, data in chunks:
            path = self._session_dir(codespace, sid) / "events.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            have = path.stat().st_size if path.is_file() else 0
            if not reset and off != have:
                continue  # stale offset (a concurrent pass moved it): the next probe resumes
            end = data.rfind(b"\n")
            if end < 0:
                continue  # no whole line yet
            with open(path, "wb" if reset or off == 0 else "ab") as fh:
                fh.write(data[:end + 1])
            changed += 1
        for sid, content in workspaces.items():
            ws = self._session_dir(codespace, sid) / "workspace.yaml"
            if ws.parent.is_dir():
                ws.write_bytes(content)
        return changed

    async def __call__(self, codespace: str) -> dict[str, Any]:
        if not _CODESPACE.match(codespace or ""):
            return {"ok": False, "detail": "not a CodeSpace name"}
        opener = self._open
        if opener is None:
            from .session_forwards import _open_codespace

            opener = _open_codespace
        manager = None
        try:
            manager = await opener(codespace)
            script = remote_script(
                self.offsets(codespace), active_minutes=self._active, chunk=self._chunk,
            )
            result = await exec_with_retry(
                manager, codespace, "bash -lc " + shlex.quote(script), timeout=60.0, attempts=2,
            )
            code = getattr(result, "exit_code", None)
            if code != 0:
                return {"ok": False, "detail": f"read failed (exit {code})"}
            changed = self.apply(codespace, getattr(result, "stdout", "") or "")
        finally:
            if manager is not None:
                try:
                    await manager.disconnect(codespace)
                except Exception as exc:
                    log.debug("transcript mirror: disconnect from %s failed: %s", codespace, exc)
        if not changed:
            return {"ok": True, "changed": 0}
        push = self._push
        if push is None:
            from .sessions import _push_via_session_sync

            def push(source: Path, label: str) -> tuple[bool, str]:
                return _push_via_session_sync(source, label, verbose=False)
        label = f".codespaces/{codespace}"
        ok, detail = await asyncio.to_thread(push, self._root / codespace, label)
        if not ok:
            log.warning("transcript mirror for %s: push failed: %s", codespace, detail)
        return {"ok": ok, "changed": changed, "detail": detail}
