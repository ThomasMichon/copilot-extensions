#!/usr/bin/env python3
"""Read collated session digest files from local persistent store,
NAS (legacy), or local temp directory.

Provides the same fidelity as ``view`` and ``grep`` but scoped to digest
content, with local and NAS paths internalized.  Designed for sub-agents
that must not trigger Copilot CLI file-access permission prompts.

Usage:
    read-session-digest <session-id> context [--digest-window WINDOW]
    read-session-digest <session-id> manifest [--digest-window WINDOW]
    read-session-digest <session-id> segment <N> [--digest-window WINDOW]
    read-session-digest <session-id> list [--digest-window WINDOW]
    read-session-digest <session-id> grep --pattern PATTERN [--digest-window WINDOW]
    read-session-digest <session-id> previous [--count N]

Modes:
    context   — Read 00-context.md (metadata, stats, segment inventory)
    manifest  — Read manifest.yaml (persistent digest store only)
    segment   — Read segment file NN-turns.md by number
    list      — List available segments and their sizes
    grep      — Search across all segments for a pattern
    previous  — Read 00-context.md from N previous sessions

Path resolution:
    Local:     ~/.agent-logger/session-digests/{machine}/
    NAS:       {nas_share}/Services/Copilot/sessions/{machine}/
    Temp:      $TEMP/session-digest/ or /tmp/session-digest/
    Falls back from local → NAS → temp automatically.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from agent_logger.config import load_config
from agent_logger.segmenter.platform import detect_machine

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _persistent_digest_root() -> Path:
    """Local persistent digest store root (from agent-logger config).

    Defaults to ``~/.agent-logger/session-digests/``.
    """
    return load_config(include_repo=False).store_dir


_LOCAL_ROOT = _persistent_digest_root()


def _remote_store_root() -> Path | None:
    """Optional remote/shared digest store root.

    Replaces the facility's hard-coded NAS path. Returns ``None`` unless
    ``AGENT_LOGGER_REMOTE_STORE`` is set, so the segmenter works fully
    locally without any remote.
    """
    env = os.environ.get("AGENT_LOGGER_REMOTE_STORE")
    return Path(env).expanduser() if env else None


_NAS_ROOT = _remote_store_root()


def _local_digest_root() -> Path:
    """Return the local temp directory for session digests."""
    tmp = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
    return Path(tmp) / "session-digest"


def _resolve_digest_dir(
    session_id: str,
    machine: str,
    digest_window: str,
) -> tuple[Path, str]:
    """Resolve the digest directory, preferring local persistent store over NAS.

    Search order:
      1. Local persistent (~/.agent-logger/session-digests/{machine}/{id}/{window})
      2. NAS (for legacy digests)
      3. Local temp ($TEMP/session-digest/{id})

    Returns (digest_dir, source) where source is 'local', 'nas', or 'local'.
    """
    # Try local persistent store first
    local_persistent = _LOCAL_ROOT / machine / session_id / digest_window
    if local_persistent.is_dir():
        return local_persistent, "local"

    # Try NAS (legacy / explicitly pushed digests)
    if _NAS_ROOT is not None:
        nas_session = _NAS_ROOT / machine / session_id
        nas_digest = nas_session / digest_window
        if nas_digest.is_dir():
            return nas_digest, "nas"

    # Fallback: local temp (flat layout — only has the current session)
    local = _local_digest_root() / session_id
    if local.is_dir():
        return local, "local"

    # Also check bare local temp without session-id nesting
    local_flat = _local_digest_root()
    if local_flat.is_dir() and (local_flat / "00-context.md").exists():
        return local_flat, "local"

    return local_persistent, "not_found"


def _nas_session_dir(session_id: str, machine: str) -> Path:
    """Return the persistent session root for a given session (local store)."""
    return _LOCAL_ROOT / machine / session_id


# ---------------------------------------------------------------------------
# Simple YAML reader (reused from collate-session.py)
# ---------------------------------------------------------------------------

def _read_simple_yaml(path: Path) -> dict[str, str]:
    """Parse a flat YAML file into a string dict (top-level keys only)."""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line and not line.startswith(" ") and not line.startswith("-"):
            k, v = line.split(":", 1)
            result[k.strip()] = v.strip().strip('"')
    return result


def _read_index(session_nas_dir: Path) -> list[dict[str, str]]:
    """Read index.yaml from a session NAS directory."""
    idx_file = session_nas_dir / "index.yaml"
    if not idx_file.exists():
        return []
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in idx_file.read_text(encoding="utf-8").splitlines():
        line = line.rstrip()
        if line.startswith("  - dir:"):
            if current:
                entries.append(current)
            current = {"dir": line.split(":", 1)[1].strip().strip('"')}
        elif line.startswith("    ") and ":" in line and current:
            k, v = line.strip().split(":", 1)
            current[k.strip()] = v.strip().strip('"')
    if current:
        entries.append(current)
    return entries


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def cmd_context(digest_dir: Path) -> None:
    """Read 00-context.md."""
    ctx = digest_dir / "00-context.md"
    if not ctx.exists():
        print(f"error: 00-context.md not found in {digest_dir}", file=sys.stderr)
        sys.exit(1)
    print(ctx.read_text(encoding="utf-8"))


def cmd_manifest(digest_dir: Path, source: str) -> None:
    """Read manifest.yaml (NAS only)."""
    if source == "local":
        print("error: manifest is only available from NAS digests", file=sys.stderr)
        print(
            "hint: the local digest directory has a flat layout without manifests",
            file=sys.stderr,
        )
        sys.exit(1)
    manifest = digest_dir / "manifest.yaml"
    if not manifest.exists():
        print(f"error: manifest.yaml not found in {digest_dir}", file=sys.stderr)
        sys.exit(1)
    print(manifest.read_text(encoding="utf-8"))


def cmd_segment(digest_dir: Path, segment_num: int) -> None:
    """Read segment NN-turns.md by number."""
    name = f"{segment_num:02d}-turns.md"
    seg = digest_dir / name
    if not seg.exists():
        print(f"error: segment {name} not found in {digest_dir}", file=sys.stderr)
        # List available segments as a hint
        available = sorted(f.name for f in digest_dir.iterdir()
                          if f.is_file() and f.name.endswith("-turns.md"))
        if available:
            print(f"hint: available segments: {', '.join(available)}", file=sys.stderr)
        sys.exit(1)
    print(seg.read_text(encoding="utf-8"))


def cmd_list(digest_dir: Path) -> None:
    """List available segments and their sizes."""
    files = sorted(f for f in digest_dir.iterdir()
                  if f.is_file() and (f.name.endswith(".md") or f.name.endswith(".yaml")))
    if not files:
        print("No digest files found.", file=sys.stderr)
        sys.exit(1)
    for f in files:
        size = f.stat().st_size
        if size >= 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size} B"
        print(f"{f.name}  ({size_str})")


def cmd_grep(digest_dir: Path, pattern: str) -> None:
    """Search across all segments for a pattern."""
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        print(f"error: invalid pattern: {e}", file=sys.stderr)
        sys.exit(1)

    segments = sorted(f for f in digest_dir.iterdir()
                     if f.is_file() and f.name.endswith("-turns.md"))
    # Also search 00-context.md
    ctx = digest_dir / "00-context.md"
    if ctx.exists():
        segments = [ctx, *segments]

    found = False
    for seg in segments:
        text = seg.read_text(encoding="utf-8")
        for line_num, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                if not found:
                    found = True
                print(f"{seg.name}:{line_num}: {line.rstrip()}")

    if not found:
        print(f"No matches for pattern: {pattern}", file=sys.stderr)
        sys.exit(1)


def cmd_previous(
    session_id: str,
    machine: str,
    count: int,
) -> None:
    """Read 00-context.md from previous sessions (local persistent or remote)."""
    # Try local persistent store first, fall back to remote store if configured
    local_machine_dir = _LOCAL_ROOT / machine
    machine_dir = None
    if local_machine_dir.is_dir():
        machine_dir = local_machine_dir
    elif _NAS_ROOT is not None and (_NAS_ROOT / machine).is_dir():
        machine_dir = _NAS_ROOT / machine
    else:
        print(f"error: no session digest directory found for {machine}", file=sys.stderr)
        print(f"  local: {local_machine_dir}", file=sys.stderr)
        if _NAS_ROOT is not None:
            print(f"  remote: {_NAS_ROOT / machine}", file=sys.stderr)
        print("hint: previous session context requires local or remote digests", file=sys.stderr)
        sys.exit(1)

    # Read manifest from current session to find previous_sessions
    current_session_dir = _nas_session_dir(session_id, machine)
    manifest_path = None

    # Check full/ digest first, then session root
    for candidate in [
        current_session_dir / "full" / "manifest.yaml",
        current_session_dir / "manifest.yaml",
    ]:
        if candidate.exists():
            manifest_path = candidate
            break

    prev_ids: list[str] = []

    if manifest_path:
        # Parse previous_sessions from manifest
        in_prev = False
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("previous_sessions:"):
                in_prev = True
                continue
            if in_prev and line.strip().startswith("- id:"):
                pid = line.split(":", 1)[1].strip().strip('"')
                prev_ids.append(pid)
            elif in_prev and not line.startswith(" "):
                break
    else:
        # Fallback: scan digest directory for session dirs sorted by mtime
        session_dirs = []
        for d in machine_dir.iterdir():
            if d.is_dir() and d.name != session_id:
                session_dirs.append(d)
        session_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        prev_ids = [d.name for d in session_dirs[:count]]

    if not prev_ids:
        print("No previous sessions found.", file=sys.stderr)
        sys.exit(1)

    shown = 0
    for prev_id in prev_ids[:count]:
        prev_dir = machine_dir / prev_id
        # Look for full/00-context.md first, then flat 00-context.md
        ctx = None
        for candidate in [
            prev_dir / "full" / "00-context.md",
            prev_dir / "00-context.md",
        ]:
            if candidate.exists():
                ctx = candidate
                break

        if ctx:
            if shown > 0:
                print("\n---\n")
            print(f"## Previous session: {prev_id}\n")
            print(ctx.read_text(encoding="utf-8"))
            shown += 1

    if shown == 0:
        print("No previous session context files found.", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read collated session digest files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "session",
        help="Session UUID",
    )
    parser.add_argument(
        "mode",
        choices=["context", "manifest", "segment", "list", "grep", "previous"],
        help="Read mode",
    )
    parser.add_argument(
        "segment_num",
        nargs="?",
        type=int,
        help="Segment number (for 'segment' mode)",
    )
    parser.add_argument(
        "--pattern",
        help="Search pattern (for 'grep' mode)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of previous sessions to read (for 'previous' mode, default: 3)",
    )
    parser.add_argument(
        "--digest-window",
        default="full",
        help="Digest window subdirectory name (default: 'full')",
    )
    parser.add_argument(
        "--machine",
        default=None,
        help="Override auto-detected machine name",
    )
    args = parser.parse_args()

    machine = (args.machine or detect_machine()).lower()

    # Validate mode-specific args
    if args.mode == "segment" and args.segment_num is None:
        parser.error("segment mode requires a segment number")
    if args.mode == "grep" and not args.pattern:
        parser.error("grep mode requires --pattern")

    # Handle previous mode (local persistent or NAS)
    if args.mode == "previous":
        cmd_previous(args.session, machine, args.count)
        return

    # Resolve digest directory
    digest_dir, source = _resolve_digest_dir(
        args.session, machine, args.digest_window,
    )

    if source == "not_found":
        local_checked = _LOCAL_ROOT / machine / args.session / args.digest_window
        print(f"error: no digest found for session {args.session}", file=sys.stderr)
        print(f"  Local path checked: {local_checked}", file=sys.stderr)
        if _NAS_ROOT is not None:
            remote_checked = _NAS_ROOT / machine / args.session / args.digest_window
            print(f"  Remote path checked: {remote_checked}", file=sys.stderr)
        print(f"  Temp path checked: {_local_digest_root() / args.session}", file=sys.stderr)
        sys.exit(1)

    # Dispatch
    if args.mode == "context":
        cmd_context(digest_dir)
    elif args.mode == "manifest":
        cmd_manifest(digest_dir, source)
    elif args.mode == "segment":
        cmd_segment(digest_dir, args.segment_num)
    elif args.mode == "list":
        cmd_list(digest_dir)
    elif args.mode == "grep":
        cmd_grep(digest_dir, args.pattern)


if __name__ == "__main__":
    main()
