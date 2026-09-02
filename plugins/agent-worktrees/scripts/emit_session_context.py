#!/usr/bin/env python3
"""Compose bounded, read-only agent-worktrees context for aggregation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


MAX_CONTEXT_BYTES = 1_600
DEADLINE_SECONDS = 8.5
SNAPSHOT_WAIT_SECONDS = 3.2
MACHINE_TIMEOUT_SECONDS = 8.0
CONDUCT_TIMEOUT_SECONDS = 8.0
SNAPSHOT_TIMESTAMP_SKEW_MS = 5_000
SNAPSHOT_MTIME_GRANULARITY_SECONDS = 2.0
SNAPSHOT_RETENTION_SECONDS = 60 * 60
SNAPSHOT_COMPLETION_WINDOW_SECONDS = 5 * 60
SNAPSHOT_NAMES = (
    "marketplace-overrides",
    "register-session",
    "register-nudge",
)
_DISPLAY_ORDER = (
    "marketplace",
    "binding",
    "machine",
    "conduct",
    "nudge",
)
_ADMISSION_ORDER = (
    "binding",
    "conduct",
    "machine",
    "marketplace",
    "nudge",
)


def _context(raw: str) -> str:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ""
    context = value.get("additionalContext") if isinstance(value, dict) else None
    return context.strip() if isinstance(context, str) else ""


def _compact_binding(context: str) -> str:
    marker = "[agent-worktrees] This Copilot session"
    offset = context.rfind(marker)
    return context[offset:] if offset >= 0 else context


def _compact_machine(context: str) -> str:
    keep = {
        "Machine",
        "Hostname",
        "Environment",
        "Platform",
        "Role",
        "Project",
        "Binstub",
    }
    lines = [
        line.strip()
        for line in context.splitlines()
        if ":" in line and line.split(":", 1)[0].strip() in keep
    ]
    return "\n".join(lines)


def _strip_owner(context: str) -> str:
    lines = context.splitlines()
    if lines and lines[0].startswith("[owner: agent-worktrees@"):
        return "\n".join(lines[1:]).strip()
    return context


def _serialize(context: str) -> str:
    return json.dumps(
        {"additionalContext": context},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _launch_keys(
    payload: bytes,
    version: str,
    *,
    timestamp_offsets: tuple[int, ...] = (0,),
) -> tuple[str, ...]:
    try:
        value = json.loads(payload.decode("utf-8"))
        session_id = value.get("sessionId")
        cwd = value.get("cwd")
        source = value.get("source", "")
        timestamp = value.get("timestamp")
        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(cwd, str)
            or not os.path.isabs(cwd)
            or not isinstance(source, str)
            or not version
            or isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
        ):
            return ()
        canonical_candidates = [os.path.abspath(cwd), os.path.realpath(cwd)]
        if os.name == "nt":
            canonical_candidates.extend(
                [
                    f"{candidate[0].upper()}{candidate[1:]}"
                    for candidate in canonical_candidates
                    if len(candidate) >= 2 and candidate[1] == ":"
                ]
            )
        canonical_cwds = tuple(dict.fromkeys(canonical_candidates))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError):
        return ()

    timestamp_texts = (
        tuple(str(timestamp + offset) for offset in timestamp_offsets)
        if isinstance(timestamp, int)
        else (f"f64:{struct.pack('>d', timestamp).hex()}",)
    )
    keys = []
    for timestamp_text in timestamp_texts:
        for canonical_cwd in canonical_cwds:
            identity = json.dumps(
                [session_id, canonical_cwd, source, version, timestamp_text],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            keys.append(hashlib.sha256(identity).hexdigest())
    return tuple(keys)


def _sibling_hook_timestamp_offsets() -> tuple[int, ...]:
    offsets = [0]
    for delta in range(1, SNAPSHOT_TIMESTAMP_SKEW_MS + 1):
        offsets.extend((-delta, delta))
    return tuple(offsets)


def _payload_timestamp_seconds(payload: bytes) -> float | None:
    try:
        value = json.loads(payload.decode("utf-8"))
        timestamp = value.get("timestamp")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
        ):
            return None
        return timestamp / 1000 if timestamp >= 10_000_000_000 else float(timestamp)
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None


def _snapshot_root() -> Path:
    return Path.home() / ".agent-worktrees" / ".session-context"


def _read_snapshot(
    name: str,
    launch_keys: tuple[str, ...],
    *,
    min_mtime: float | None = None,
) -> str | None:
    root = _snapshot_root()
    ranks = {launch_key: index for index, launch_key in enumerate(launch_keys)}
    candidates: list[tuple[int, Path, str, bool]] = []
    try:
        paths = list(root.iterdir())
    except OSError:
        return None
    prefix = f"{name}-"
    prune_before = time.time() - SNAPSHOT_RETENTION_SECONDS
    for path in paths:
        filename = path.name
        if not filename.startswith(prefix):
            continue
        try:
            if (
                not path.is_symlink()
                and path.is_file()
                and path.stat().st_mtime < prune_before
            ):
                path.unlink()
                continue
        except OSError:
            continue
        is_json = filename.endswith(".json")
        launch_key = filename[
            len(prefix) : -len(".json") if is_json else None
        ]
        rank = ranks.get(launch_key)
        if rank is not None:
            candidates.append((rank, path, launch_key, is_json))

    for _, path, launch_key, is_json in sorted(candidates):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if min_mtime is not None and path.stat().st_mtime < min_mtime:
                continue
        except OSError:
            continue
        if is_json:
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(value, dict)
                and value.get("launchKey") == launch_key
                and isinstance(value.get("output"), str)
            ):
                return _context(value["output"])
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        stored_key, separator, output = raw.partition("\n")
        if separator and stored_key == launch_key:
            return _context(output)
    return None


def _read_fresh_binding_snapshot(
    payload: bytes,
    *,
    min_mtime: float,
    max_mtime: float,
) -> str | None:
    try:
        value = json.loads(payload.decode("utf-8"))
        cwd = value.get("cwd")
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            return None
        expected_cwd = os.path.normcase(os.path.realpath(cwd))
        paths = list(_snapshot_root().glob("register-session-*"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, OSError):
        return None

    candidates = []
    for path in paths:
        try:
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_mtime < min_mtime
                or path.stat().st_mtime > max_mtime
            ):
                continue
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _, path in sorted(candidates, reverse=True):
        try:
            if path.name.endswith(".json"):
                state = json.loads(path.read_text(encoding="utf-8-sig"))
                output = state.get("output") if isinstance(state, dict) else None
                context = _context(output) if isinstance(output, str) else ""
            else:
                raw = path.read_text(encoding="utf-8")
                _stored_key, separator, output = raw.partition("\n")
                context = _context(output) if separator else ""
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not context.startswith("## agent-worktrees session command catalog"):
            continue
        checkout_line = next(
            (
                line
                for line in context.splitlines()
                if line.startswith("Checkout: ")
            ),
            "",
        )
        _prefix, separator, checkout_path = checkout_line.partition("; path=")
        if not separator:
            continue
        checkout_path = checkout_path.split(". Mux:", 1)[0].removesuffix(".")
        try:
            actual_cwd = os.path.normcase(os.path.realpath(checkout_path))
        except OSError:
            continue
        if actual_cwd == expected_cwd:
            return context
    return None


def _await_snapshots(
    payload: bytes,
    version: str,
    *,
    deadline: float,
) -> dict[str, str]:
    launch_keys = _launch_keys(
        payload,
        version,
        timestamp_offsets=_sibling_hook_timestamp_offsets(),
    )
    if not launch_keys:
        return {name: "" for name in SNAPSHOT_NAMES}
    results: dict[str, str] = {}
    payload_timestamp = _payload_timestamp_seconds(payload)
    min_mtime = (
        payload_timestamp
        - (SNAPSHOT_TIMESTAMP_SKEW_MS / 1000)
        - SNAPSHOT_MTIME_GRANULARITY_SECONDS
        if payload_timestamp is not None
        else time.time()
        - SNAPSHOT_MTIME_GRANULARITY_SECONDS
    )
    max_mtime = (
        payload_timestamp
        + SNAPSHOT_COMPLETION_WINDOW_SECONDS
        + SNAPSHOT_MTIME_GRANULARITY_SECONDS
        if payload_timestamp is not None
        else time.time()
        + SNAPSHOT_MTIME_GRANULARITY_SECONDS
    )
    optional_deadline = min(deadline, time.monotonic() + SNAPSHOT_WAIT_SECONDS)
    while len(results) < len(SNAPSHOT_NAMES):
        for name in SNAPSHOT_NAMES:
            if name in results:
                continue
            if name != "register-session" and time.monotonic() >= optional_deadline:
                results[name] = ""
                continue
            context = _read_snapshot(
                name,
                launch_keys,
                min_mtime=min_mtime,
            )
            if context is None and name == "register-session":
                context = _read_fresh_binding_snapshot(
                    payload,
                    min_mtime=min_mtime,
                    max_mtime=max_mtime,
                )
            if context is not None:
                results[name] = context
        if len(results) == len(SNAPSHOT_NAMES) or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    return {name: results.get(name, "") for name in SNAPSHOT_NAMES}


def _compose(version: str, fragments: dict[str, str]) -> str:
    owner = f"[owner: agent-worktrees@{version}]"
    selected: set[str] = set()
    for name in _ADMISSION_ORDER:
        fragment = fragments.get(name, "").strip()
        if not fragment:
            continue
        candidate = "\n\n".join(
            [
                owner,
                *[
                    fragments[item].strip()
                    for item in _DISPLAY_ORDER
                    if item in selected or item == name
                ],
            ]
        )
        if len(_serialize(candidate).encode("utf-8")) <= MAX_CONTEXT_BYTES:
            selected.add(name)
    context = "\n\n".join(
        [
            owner,
            *[
                fragments[name].strip()
                for name in _DISPLAY_ORDER
                if name in selected
            ],
        ]
    )
    output = _serialize(context)
    if len(output.encode("utf-8")) > MAX_CONTEXT_BYTES:
        output = _serialize(owner)
    if len(output.encode("utf-8")) > MAX_CONTEXT_BYTES:
        output = "{}"
    return output


def _command(script: Path, *args: str) -> list[str] | None:
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if powershell is None:
            return None
        return [
            powershell,
            "-NoProfile",
            "-File",
            str(script.with_suffix(".ps1")),
            *args,
        ]
    return ["bash", str(script.with_suffix(".sh")), *args]


def _run(
    script: Path,
    payload: bytes,
    *args: str,
    deadline: float,
    timeout: float = 1.0,
) -> str:
    command = _command(script, *args)
    if command is None:
        return ""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return ""
    try:
        result = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            timeout=min(timeout, remaining),
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    try:
        return _context(result.stdout.decode("utf-8", errors="strict"))
    except UnicodeDecodeError:
        return ""


def _collect_fragments(root: Path, payload: bytes, version: str) -> dict[str, str]:
    scripts = root / "scripts"
    deadline = time.monotonic() + DEADLINE_SECONDS
    with ThreadPoolExecutor(max_workers=3) as executor:
        snapshots = executor.submit(
            _await_snapshots,
            payload,
            version,
            deadline=deadline,
        )
        machine = executor.submit(
            _run,
            scripts / "session-machine",
            payload,
            deadline=deadline,
            timeout=MACHINE_TIMEOUT_SECONDS,
        )
        conduct = executor.submit(
            _run,
            scripts / "session-conduct",
            payload,
            "--aggregate",
            deadline=deadline,
            timeout=CONDUCT_TIMEOUT_SECONDS,
        )
        cached = snapshots.result()
        return {
            "marketplace": cached["marketplace-overrides"],
            "binding": cached["register-session"],
            "machine": machine.result(),
            "conduct": conduct.result(),
            "nudge": cached["register-nudge"],
        }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        version = manifest["version"]
        if not isinstance(version, str) or not version:
            raise ValueError
        payload = sys.stdin.buffer.read(64 * 1024 + 1)
        if len(payload) > 64 * 1024:
            raise ValueError
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        sys.stdout.write("{}")
        return 0

    raw_fragments = _collect_fragments(root, payload, version)
    fragments = {
        **raw_fragments,
        "binding": _compact_binding(raw_fragments["binding"]),
        "machine": _compact_machine(raw_fragments["machine"]),
        "conduct": _strip_owner(raw_fragments["conduct"]),
    }
    sys.stdout.write(_compose(version, fragments))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
