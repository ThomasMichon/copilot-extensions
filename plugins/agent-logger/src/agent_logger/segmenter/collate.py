#!/usr/bin/env python3
"""Collate Copilot CLI session artifacts into a structured digest.

Reads events.jsonl, checkpoints, and workspace.yaml from a session's
state directory and produces a compact Markdown summary suitable for
feeding into a log-writing sub-agent.

Usage:
    # Single-file mode (stdout):
    python collate-session.py <session> [--since ISO] [--cutoff ISO]
                                        [--max-tool-output N] [--max-total N]

    # Segmented mode (files to a directory):
    python collate-session.py <session> --output-dir <dir>
                                        [--segment-size N]
                                        [--since ISO] [--cutoff ISO]
                                        [--max-tool-output N]

The session can be specified as:
  - A bare session UUID  (looks up ~/.copilot/session-state/<id>/)
  - "current"            (finds the most-recently-modified session dir)
  - An absolute path     (to the session-state directory itself)

Single-file mode writes the full digest to stdout.
Segmented mode writes numbered files to --output-dir:
  - 00-context.md  (metadata + checkpoints + stats — written last; its
    presence marks a complete digest)
  - 01-turns.md, 02-turns.md, ...  (transcript segments, split at turn
    boundaries, each ≤ --segment-size chars)

events.jsonl is streamed one line at a time and turns are written as they
complete, so memory stays bounded by one turn (plus the --max-total budget in
single-file mode) rather than by the transcript size.

The events -> Markdown conversion itself lives in the reusable
:mod:`agent_logger.segmenter.digest` engine (shared with ``ramp-up-session``);
this module adds session resolution, the persistent digest store, manifests,
and the CLI. Engine names are re-exported here for existing importers.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_logger import sessions
from agent_logger.config import load_config
from agent_logger.secret_patterns import SECRET_PATTERNS, redact  # noqa: F401
from agent_logger.segmenter.digest import (  # noqa: F401 -- re-exported
    DEFAULT_MAX_TOOL_OUTPUT,
    DEFAULT_MAX_TOTAL,
    DEFAULT_SEGMENT_SIZE,
    TurnStats,
    build_session_digest,
    clear_digest_dir,
    fmt_ts,
    format_checkpoints,
    format_digest,
    format_metadata,
    format_turn,
    format_turn_stats,
    read_checkpoints,
    read_rewind_index,
    read_workspace,
    render_session_digest,
    write_segments,
)
from agent_logger.segmenter.platform import detect_machine
from agent_logger.segmenter.turn_stream import (  # noqa: F401 -- re-exported
    SKIP_EVENT_TYPES,
    SKIP_TOOL_NAMES,
    TurnStream,
    parse_events,
    strip_system_tags,
    truncate,
)

# Pre-extraction private names, kept for existing importers.
_format_metadata = format_metadata
_format_checkpoints = format_checkpoints
_format_turn_stats = format_turn_stats
_format_turn = format_turn

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_BACKFILL_COUNT = 3         # how many previous sessions to link/backfill


def _default_digest_root() -> str:
    """Local persistent digest store root (from agent-logger config).

    Defaults to ``~/.agent-logger/session-digests/``. Override via the
    ``--nas-root`` flag (kept for backward compatibility) or by setting
    ``store_dir`` in the agent-logger config.
    """
    return str(load_config(include_repo=False).store_dir)


DEFAULT_NAS_ROOT = _default_digest_root()
COPILOT_STATE_DIR_NAME = ".copilot"
SESSION_STATE_SUBDIR = "session-state"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_copilot_dir() -> Path:
    """Locate ~/.copilot (USERPROFILE on Windows, HOME elsewhere)."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    p = Path(home) / COPILOT_STATE_DIR_NAME
    if p.is_dir():
        return p
    raise FileNotFoundError(f"Cannot find {COPILOT_STATE_DIR_NAME} in {home}")


def _normalize_cwd(path: str) -> str:
    """Normalize a cwd/git_root path for comparison (case + slashes)."""
    if not path or not path.strip():
        return ""
    return os.path.normcase(os.path.normpath(path.strip()))


def _workspace_cwd(ws: dict[str, str]) -> str:
    """Extract the normalized workspace identity (cwd, falling back to git_root)."""
    raw = ws.get("cwd") or ws.get("git_root") or ""
    return _normalize_cwd(raw)


def _repo_root() -> str:
    """Return the normalized repo/worktree root derived from this script's location."""
    return _normalize_cwd(str(Path(__file__).resolve().parent.parent.parent))


def resolve_session_dir(spec: str) -> Path:
    """Turn a user-supplied session specifier into an absolute Path.

    When *spec* is ``"current"``, only sessions whose workspace cwd/git_root
    matches this script's repo root are considered — this prevents picking up
    a session from a different worktree or repo.
    """
    p = Path(spec)
    if p.is_absolute():
        if p.is_dir():
            return p
        if p.is_file() and (session_id := sessions.archive_stem(p)) is not None:
            ref = sessions.SessionRef(
                id=session_id,
                kind="archive",
                path=p,
                store=p.parent,
            )
            if sessions.verify_archive(ref):
                return sessions.materialize_path(ref)

    copilot = find_copilot_dir()
    state_root = copilot / SESSION_STATE_SUBDIR

    if spec.lower() == "current":
        norm_filter = _repo_root()
        candidates = []
        for d in state_root.iterdir():
            ef = d / "events.jsonl"
            if not ef.exists():
                continue
            ws = read_workspace(d)
            if _workspace_cwd(ws) != norm_filter:
                continue
            candidates.append((ef.stat().st_mtime, d))
        if not candidates:
            raise FileNotFoundError("No session directories with events.jsonl found")
        candidates.sort(reverse=True)
        return candidates[0][1]

    # Treat as UUID -- a live directory, else an on-device archive we
    # transparently materialize so downstream path-based reads are unchanged.
    candidate = state_root / spec
    if candidate.is_dir():
        return candidate
    ref = sessions.resolve_ref(spec, state_root, *session_archive_stores())
    if ref is not None and ref.is_archive:
        return sessions.materialize_path(ref)
    raise FileNotFoundError(f"Session directory not found: {candidate}")


def session_archive_stores() -> list[Path]:
    """On-device archive store(s) holding compacted sessions (config-aware).

    Falls back to the ``<home>/archived-sessions`` default if config cannot be
    loaded, so by-id resolution still finds archives on a minimal host.
    """
    try:
        return [load_config(include_repo=False).compact_archive_root]
    except Exception:
        from agent_logger.config import home_dir

        return [home_dir() / "archived-sessions"]


def _is_quip_session(cwd: str) -> bool:
    """Return True if the cwd looks like a quip/sub-agent temp session."""
    cwd_lower = cwd.lower().replace("\\", "/")
    return (
        "/temp/quip-" in cwd_lower
        or "/tmp/quip-" in cwd_lower
        or "\\temp\\quip-" in cwd.lower()
    )


def find_previous_sessions(
    current_session_dir: Path,
    count: int = DEFAULT_BACKFILL_COUNT,
) -> list[dict[str, Any]]:
    """Find the *count* most recent non-quip sessions before the current one.

    Automatically reads the current session's workspace.yaml to determine its
    cwd/git_root, then only returns sessions from the same worktree or repo
    checkout.

    Returns list of dicts: {id, dir, cwd, created_at, branch, summary}.
    Sorted by created_at descending (most recent first).
    """
    state_root = current_session_dir.parent
    current_id = current_session_dir.name

    # Scope to same worktree/repo by reading the current session's cwd
    current_ws = read_workspace(current_session_dir)
    norm_cwd = _workspace_cwd(current_ws)

    candidates: list[dict[str, Any]] = []
    for d in state_root.iterdir():
        if not d.is_dir() or d.name == current_id:
            continue
        ws = read_workspace(d)
        if not ws:
            continue
        cwd = ws.get("cwd", "")
        if _is_quip_session(cwd):
            continue
        # Worktree isolation: skip sessions from different repos/worktrees
        if norm_cwd and _workspace_cwd(ws) != norm_cwd:
            continue
        created = ws.get("created_at", "")
        if not created:
            continue
        # Must have events.jsonl to be a real session
        if not (d / "events.jsonl").exists():
            continue
        candidates.append({
            "id": d.name,
            "dir": d,
            "cwd": cwd,
            "created_at": created,
            "branch": ws.get("branch", ""),
            "summary": ws.get("summary", ""),
        })

    candidates.sort(key=lambda x: x["created_at"], reverse=True)
    return candidates[:count]


def _yaml_escape(value: str) -> str:
    """Escape a string for safe YAML output (simple quoting)."""
    if not value:
        return '""'
    # Quote if value contains special chars
    if any(c in value for c in ":#{}[]|>&*!%@`,'\"\\"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return f'"{value}"'


def write_manifest(
    output_dir: Path,
    session_id: str,
    machine: str,
    workspace: dict[str, str],
    session_start: dict[str, Any],
    segments: list[str],
    checkpoints: list[dict[str, str]],
    turn_count: int,
    previous_sessions: list[dict[str, Any]],
    nas_root: Path,
    since: str | None = None,
    cutoff: str | None = None,
) -> Path:
    """Write manifest.yaml to the digest directory.

    Returns the path to the written manifest.
    """
    created_at = session_start.get("start_time") or workspace.get("created_at", "")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    branch = session_start.get("branch") or workspace.get("branch", "")
    summary = workspace.get("summary", "")
    is_full = since is None

    lines: list[str] = [
        f"session_id: {_yaml_escape(session_id)}",
        f"machine: {_yaml_escape(machine)}",
        f"created_at: {_yaml_escape(created_at)}",
        f"digested_at: {_yaml_escape(now)}",
        f"branch: {_yaml_escape(branch)}",
        f"summary: {_yaml_escape(summary)}",
        f"is_full_session: {str(is_full).lower()}",
        f"since: {_yaml_escape(since or '')}",
        f"cutoff: {_yaml_escape(cutoff or '')}",
        f"checkpoint_count: {len(checkpoints)}",
        f"turn_count: {turn_count}",
        "segments:",
    ]
    for seg in segments:
        lines.append(f'  - "{seg}"')

    if previous_sessions:
        lines.append("previous_sessions:")
        for prev in previous_sessions:
            prev_id = prev["id"]
            prev_machine = machine  # same machine since we filter by local sessions
            prev_created = prev.get("created_at", "")
            # Check NAS for existing full digest
            prev_nas_dir = nas_root / machine / prev_id
            has_full = _has_full_digest(prev_nas_dir)
            lines.append(f"  - id: {_yaml_escape(prev_id)}")
            lines.append(f"    machine: {_yaml_escape(prev_machine)}")
            lines.append(f"    created_at: {_yaml_escape(prev_created)}")
            lines.append(f"    has_full_digest: {str(has_full).lower()}")
    else:
        lines.append("previous_sessions: []")

    manifest_path = output_dir / "manifest.yaml"
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def _sanitize_ts(ts: str) -> str:
    """Sanitize an ISO timestamp for use as a directory name."""
    return ts.replace(":", "-")


def _digest_dir_name(since: str | None, cutoff: str | None) -> str:
    """Derive the digest subdirectory name from the time window.

    Returns 'full' when no --since is given (even if cutoff is set, the
    digest starts from session start), or 'since-{ts}' / 'since-{ts}__until-{ts}'
    for windowed digests.
    """
    if not since:
        return "full"
    parts = [f"since-{_sanitize_ts(since)}"]
    if cutoff:
        parts.append(f"until-{_sanitize_ts(cutoff)}")
    return "__".join(parts)


def _has_full_digest(session_dir: Path) -> bool:
    """Check if a session directory contains a full digest.

    Supports both the new layout (full/ subdir) and legacy flat layout
    (manifest.yaml at the session root).
    """
    # New layout
    full_dir = session_dir / "full"
    if full_dir.is_dir() and (full_dir / "manifest.yaml").exists():
        return True
    # Legacy flat layout
    if (session_dir / "manifest.yaml").exists():
        # Check it's not an index.yaml-only dir — legacy has segments too
        return any(f.name.endswith("-turns.md") for f in session_dir.iterdir() if f.is_file())
    return False


def _read_index(session_dir: Path) -> list[dict[str, Any]]:
    """Read index.yaml from a session directory, returning digest entries."""
    idx_file = session_dir / "index.yaml"
    if not idx_file.exists():
        return []
    # Simple YAML list parser for our known structure
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in idx_file.read_text(encoding="utf-8").splitlines():
        line = line.rstrip()
        if line.startswith("  - dir:"):
            if current:
                entries.append(current)
            current = {"dir": line.split(":", 1)[1].strip().strip('"')}
        elif line.startswith("    ") and ":" in line and current:
            k, v = line.strip().split(":", 1)
            v = v.strip().strip('"')
            if v in ("true", "false"):
                v = v == "true"
            current[k.strip()] = v
    if current:
        entries.append(current)
    return entries


def _write_index(session_persist_dir: Path, session_id: str) -> Path:
    """Scan digest subdirectories and write/update index.yaml.

    Returns the path to the written index file.
    """
    entries: list[dict[str, Any]] = []
    if not session_persist_dir.is_dir():
        return session_persist_dir / "index.yaml"
    for sub in sorted(session_persist_dir.iterdir()):
        if not sub.is_dir():
            continue
        manifest = sub / "manifest.yaml"
        if not manifest.exists():
            continue
        # Parse basic metadata from manifest
        meta: dict[str, str] = {}
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
        is_full = sub.name == "full"
        entries.append({
            "dir": sub.name,
            "since": meta.get("since", ""),
            "cutoff": meta.get("cutoff", ""),
            "is_full_session": is_full,
            "digested_at": meta.get("digested_at", ""),
            "turn_count": meta.get("turn_count", "0"),
        })

    lines = [f"session_id: {_yaml_escape(session_id)}", "digests:"]
    for entry in entries:
        lines.append(f'  - dir: "{entry["dir"]}"')
        lines.append(f'    since: "{entry.get("since", "")}"')
        lines.append(f'    cutoff: "{entry.get("cutoff", "")}"')
        lines.append(f'    is_full_session: {str(entry["is_full_session"]).lower()}')
        lines.append(f'    digested_at: "{entry.get("digested_at", "")}"')
        lines.append(f'    turn_count: {entry["turn_count"]}')

    idx_path = session_persist_dir / "index.yaml"
    idx_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return idx_path



def write_to_nas(
    session_dir: Path,
    session_id: str,
    machine: str,
    nas_root: Path,
    cutoff: str | None,
    max_tool_output: int,
    segment_size: int,
    backfill: bool = True,
    backfill_count: int = DEFAULT_BACKFILL_COUNT,
    since: str | None = None,
) -> tuple[Path, Path]:
    """Write a session digest (segments + manifest) to the persistent store.

    Digests are stored in subdirectories keyed by time window:
      {machine}/{session-id}/full/          — no --since
      {machine}/{session-id}/since-{ts}/    — windowed digest

    Returns (session_dir, digest_dir).
    """
    nas_session_dir = nas_root / machine / session_id
    digest_name = _digest_dir_name(since, cutoff)
    nas_digest_dir = nas_session_dir / digest_name
    nas_digest_dir.mkdir(parents=True, exist_ok=True)

    # Clean any previous files in this digest subdir
    for f in nas_digest_dir.iterdir():
        if f.is_file():
            f.unlink()

    # Collate the session; turns are streamed straight into segment files.
    # ``TurnStream`` is resolved from this module so callers/tests may swap it.
    digest = build_session_digest(
        session_dir,
        nas_digest_dir,
        since=since,
        cutoff=cutoff,
        segment_size=segment_size,
        max_tool_output=max_tool_output,
        clean=False,
        stream_factory=TurnStream,
    )

    # Find previous sessions (auto-scoped to same worktree/repo)
    previous = find_previous_sessions(session_dir, count=backfill_count)

    # Backfill previous sessions that lack full digests
    if backfill:
        for prev in previous:
            prev_nas_dir = nas_root / machine / prev["id"]
            if _has_full_digest(prev_nas_dir):
                continue
            print(f"Backfilling session {prev['id'][:8]}...", file=sys.stderr)
            try:
                write_to_nas(
                    session_dir=prev["dir"],
                    session_id=prev["id"],
                    machine=machine,
                    nas_root=nas_root,
                    cutoff=None,
                    max_tool_output=max_tool_output,
                    segment_size=segment_size,
                    backfill=False,
                )
            except Exception as e:
                print(f"Warning: backfill failed for {prev['id'][:8]}: {e}", file=sys.stderr)

    # Write manifest into digest subdir
    write_manifest(
        output_dir=nas_digest_dir,
        session_id=session_id,
        machine=machine,
        workspace=digest.workspace,
        session_start=digest.session_start,
        segments=digest.files,
        checkpoints=digest.checkpoints,
        turn_count=digest.turn_count,
        previous_sessions=previous,
        nas_root=nas_root,
        since=since,
        cutoff=cutoff,
    )

    # Update session-level index.yaml
    _write_index(nas_session_dir, session_id)

    return nas_session_dir, nas_digest_dir


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="collate-session",
        description="Collate Copilot CLI session artifacts into a structured digest.",
    )
    parser.add_argument(
        "session",
        help='Session UUID, "current", or absolute path to session-state dir',
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO timestamp — ignore events before this point (start of digest window)",
    )
    parser.add_argument(
        "--cutoff",
        default=None,
        help="ISO timestamp — ignore events after this point (avoids self-reference)",
    )
    parser.add_argument(
        "--max-tool-output",
        type=int,
        default=DEFAULT_MAX_TOOL_OUTPUT,
        help=f"Max chars per tool result (default: {DEFAULT_MAX_TOOL_OUTPUT})",
    )
    parser.add_argument(
        "--max-total",
        type=int,
        default=DEFAULT_MAX_TOTAL,
        help=f"Max total output chars, single-file mode only (default: {DEFAULT_MAX_TOTAL})",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Write segmented digest files to this directory instead of stdout",
    )
    parser.add_argument(
        "--segment-size",
        type=int,
        default=DEFAULT_SEGMENT_SIZE,
        help=f"Max chars per transcript segment (default: {DEFAULT_SEGMENT_SIZE})",
    )
    # NAS output options
    parser.add_argument(
        "--nas",
        action="store_true",
        help="Write segmented digest + manifest to the persistent store",
    )
    parser.add_argument(
        "--nas-root",
        default=DEFAULT_NAS_ROOT,
        help=f"Persistent digest root path (default: {DEFAULT_NAS_ROOT})",
    )
    parser.add_argument(
        "--machine",
        default=None,
        help="Override auto-detected machine name (will be lowercased)",
    )
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Skip backfilling previous sessions that lack digests",
    )
    args = parser.parse_args()

    try:
        session_dir = resolve_session_dir(args.session)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    session_id = session_dir.name

    nas_ok = False
    nas_session_dir: Path | None = None
    nas_digest_dir: Path | None = None
    if args.nas:
        # Persistent mode — write segments + manifest (best-effort)
        machine = (args.machine or detect_machine()).lower()
        nas_root = Path(args.nas_root)
        try:
            nas_session_dir, nas_digest_dir = write_to_nas(
                session_dir=session_dir,
                session_id=session_id,
                machine=machine,
                nas_root=nas_root,
                cutoff=args.cutoff,
                max_tool_output=args.max_tool_output,
                segment_size=args.segment_size,
                backfill=not args.no_backfill,
                since=args.since,
            )
            nas_ok = True
            print(f"Digest written to: {nas_digest_dir}")
            for f in sorted(nas_digest_dir.iterdir()):
                print(f"  {f.name}  ({f.stat().st_size:,} bytes)")
        except Exception as e:
            print(f"Warning: digest write failed: {e}", file=sys.stderr)
            print("nas_status: unreachable")

    if args.output_dir:
        # Segmented mode — write files to local directory (stale files cleaned)
        out_dir = Path(args.output_dir)
        files = build_session_digest(
            session_dir,
            out_dir,
            since=args.since,
            cutoff=args.cutoff,
            segment_size=args.segment_size,
            max_tool_output=args.max_tool_output,
            stream_factory=TurnStream,
        ).files
        print(f"Wrote {len(files)} segment(s) to {out_dir.resolve()}:")
        for f in files:
            fpath = out_dir / f
            size = fpath.stat().st_size
            print(f"  {f}  ({size:,} bytes)")

    if args.nas and nas_ok:
        print("nas_status: ok")
        print(f"nas_session_path: {nas_session_dir}")
        print(f"nas_digest_path: {nas_digest_dir}")

    if not (args.nas or args.output_dir):
        # Single-file mode — write to stdout
        digest = render_session_digest(
            session_dir,
            since=args.since,
            cutoff=args.cutoff,
            max_tool_output=args.max_tool_output,
            max_total=args.max_total,
            stream_factory=TurnStream,
        )
        print(digest)


if __name__ == "__main__":
    main()
