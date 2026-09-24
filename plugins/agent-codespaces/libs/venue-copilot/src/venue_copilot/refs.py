"""Reference files for a detached CLI-mode session (``copilot --detach --ref-file``).

An orchestrator hands the *worker* a file it should consult -- a HAR trace, a
session transcript, a log -- without reading it into its own context: it passes
only the host path. The file is copied into the venue outside the product
checkout (so it is never committed) and the worker is told where it is, in its
seed (new session) or in a message (running session).

The transfer is the same egress-free lane plugin staging uses
(agent-codespaces' plugin staging): tar+gzip in memory, base64 over
the SSH exec channel's **stdin**, so the command line stays tiny.
"""
from __future__ import annotations

import base64
import io
import shutil
import subprocess
import tarfile
import time
from collections.abc import Callable
from pathlib import Path

REFS_ROOT = "$HOME/.agent-bridge/refs"
#: The payload travels in memory (tar.gz -> base64); larger inputs belong in a
#: repository or storage account the venue can reach itself.
MAX_REF_BYTES = 256 * 1024 * 1024


class RefFileError(ValueError):
    """A reference file cannot be sent (missing, too large)."""


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size


def _unique_names(paths: list[Path]) -> list[str]:
    names: list[str] = []
    for path in paths:
        stem, suffix, n = path.stem, path.suffix, 1
        name = path.name
        while name in names:
            n += 1
            name = f"{stem}-{n}{suffix}"
        names.append(name)
    return names


def build_refs_upload(raw_paths: list[str], batch: str) -> tuple[str, bytes, list[tuple[str, int]]]:
    """``(remote_command, stdin_bytes, [(name, size), ...])`` for one batch.

    The command extracts the files into ``<REFS_ROOT>/<batch>`` and prints that
    directory's absolute path (so the caller can name exact venue paths).
    """
    paths = [Path(p).expanduser() for p in raw_paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise RefFileError(f"reference file not found: {', '.join(missing)}")
    sizes = [_size(p) for p in paths]
    if sum(sizes) > MAX_REF_BYTES:
        raise RefFileError(
            f"reference files total {sum(sizes) // (1024 * 1024)} MiB; the limit is "
            f"{MAX_REF_BYTES // (1024 * 1024)} MiB -- share larger inputs through a "
            "location the venue can fetch itself"
        )
    names = _unique_names(paths)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, name in zip(paths, names):
            tf.add(str(path), arcname=name)
    dest = f"{REFS_ROOT}/{batch}"
    command = f'mkdir -p "{dest}" && base64 -d | tar -xzf - -C "{dest}" && cd "{dest}" && pwd'
    return command, base64.b64encode(buf.getvalue()), list(zip(names, sizes))


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            break
    return f"{value:.1f} {unit}"


def refs_note(remote_dir: str, files: list[tuple[str, int]]) -> str:
    """The text that tells the worker where its reference files are."""
    lines = [
        "Reference files for this task, provided by the operator. They are outside "
        "the repository -- read them with your tools as the task needs, and never "
        "commit or paste them wholesale. If one is missing or unreadable, say so "
        "(BLOCKED: ...) instead of guessing its contents:",
    ]
    lines += [f"- {remote_dir}/{name} ({_human(size)})" for name, size in files]
    return "\n".join(lines)


def batch_id(scope: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-._" else "-" for c in scope)
    return f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}"


def upload_for(raw_paths: list[str], scope: str) -> tuple[str, bytes, list[tuple[str, int]]] | None:
    """The batch upload for a session's ``--ref-file`` paths, or ``None`` when none."""
    return build_refs_upload(list(raw_paths), batch_id(scope)) if raw_paths else None


def send_refs(
    run_input: Callable[[str, bytes], tuple[int, str, str] | None],
    upload: tuple[str, bytes, list[tuple[str, int]]],
    progress: Callable[[str, str], None],
) -> str | None:
    """Copy one batch into the venue with ``run_input(command, stdin)``.

    Returns the worker-facing note, or ``None`` when the copy failed.
    """
    command, payload, files = upload
    progress("refs", f"copying {len(files)} reference file(s) to the venue")
    result = run_input(command, payload)
    if result is None or result[0] != 0 or not result[1].strip():
        progress("refs-failed", ((result[2] or f"exit {result[0]}") if result else "transport failure").strip()[-500:])
        return None
    return refs_note(result[1].strip().splitlines()[-1], files)


def deliver_note(session_id: str, note: str, *, run=subprocess.run) -> bool:
    """Tell a running session about new reference files (over stdin; never raises)."""
    bridge = shutil.which("agent-bridge") or "agent-bridge"
    try:
        result = run(
            [bridge, "send", session_id, "--prompt-file", "-", "--no-wait"],
            input=note, capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return getattr(result, "returncode", 1) == 0
