#!/usr/bin/env python3
"""Session ramp-up -- take over a dormant worktree's Copilot session.

When a Copilot session can no longer be resumed (a wedged CLI, a machine
restart, an abandoned worktree), the work it was doing is not lost: the raw
event stream still sits on disk at
``~/.copilot/session-state/<id>/events.jsonl``. This tool lets a *fresh*
session ramp up into that dormant session's situation and pick up the torch.

It deliberately reuses the segmenter engine wholesale -- the same
``collate-session`` machinery that turns ``events.jsonl`` into a digest -- and
adds only the one primitive the digest system lacked: **worktree -> session
discovery**. ``collate-session`` can resolve ``"current"`` only for its own
repo root; ramp-up resolves the most recent session for *any* worktree you
name, then:

  1. collates it into an ephemeral digest (segments written under
     ``$TEMP/session-digest/<id>/`` so ``read-session-digest`` can read them
     back without persisting anything to the digest store), and
  2. prints a concise **takeover brief**: session metadata, the CLI's
     pre-compaction checkpoints, session stats, and a "where it left off"
     tail of the last few turns.

Usage:
    ramp-up-session [WORKTREE] [--list] [--session ID] [--tail-turns N]
                    [--segment-size N] [--max-tool-output N]
                    [--machine NAME] [--output-dir DIR] [--json]

WORKTREE may be a short worktree **suffix** (e.g. ``fbc5``), a full path, or
``.`` for the current directory (the default). A bare suffix is hunted down in
the local session store by matching worktree directory names ending in
``-<suffix>``. With ``--machine NAME`` naming another host, the hunt is
delegated over ``ssh <NAME>`` (a session's raw data lives on the machine that
produced it); locally, ``--machine`` disambiguates a suffix reused across hosts.
With ``--list`` the candidate sessions are enumerated (most recent first) and
nothing is collated. With ``--session ID`` a specific session is ramped up.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_logger.segmenter.collate import (
    DEFAULT_MAX_TOOL_OUTPUT,
    DEFAULT_SEGMENT_SIZE,
    SESSION_STATE_SUBDIR,
    _format_checkpoints,
    _format_metadata,
    _format_stats,
    _format_turn,
    _is_quip_session,
    _normalize_cwd,
    _workspace_cwd,
    find_copilot_dir,
    fmt_ts,
    parse_events,
    read_checkpoints,
    read_rewind_index,
    read_workspace,
    write_segments,
)
from agent_logger.segmenter.platform import detect_machine

DEFAULT_TAIL_TURNS = 6


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _session_state_root() -> Path:
    """Return ``~/.copilot/session-state``."""
    return find_copilot_dir() / SESSION_STATE_SUBDIR


def _iter_sessions():
    """Yield ``(dir, workspace, cwd, events_path)`` for every real session.

    A "real" session has an ``events.jsonl`` and is not a quip/sub-agent temp
    session.
    """
    state_root = _session_state_root()
    if not state_root.is_dir():
        return
    for d in state_root.iterdir():
        if not d.is_dir():
            continue
        events = d / "events.jsonl"
        if not events.exists():
            continue
        ws = read_workspace(d)
        if not ws:
            continue
        cwd = ws.get("cwd", "")
        if _is_quip_session(cwd):
            continue
        yield d, ws, cwd, events


def _session_record(d: Path, ws: dict[str, str], cwd: str, events: Path) -> dict[str, Any]:
    try:
        mtime = events.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {
        "id": d.name,
        "dir": d,
        "cwd": cwd,
        "branch": ws.get("branch", ""),
        "name": ws.get("name", ""),
        "summary": ws.get("summary", ""),
        "created_at": ws.get("created_at", ""),
        "updated_at": ws.get("updated_at", ""),
        "mtime": mtime,
    }


def _sort_recent(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sessions.sort(key=lambda x: (x["updated_at"] or "", x["mtime"]), reverse=True)
    return sessions


def _worktree_suffix(cwd: str) -> str:
    """Return the trailing hyphen-delimited segment of a worktree dir name.

    Worktrees are named like ``<machine>-<env>-<date>-<time>-<suffix>``; the
    short suffix (e.g. ``fbc5``) is the last segment. Lowercased.
    """
    base = Path(cwd).name.lower()
    return base.rsplit("-", 1)[-1] if base else ""


def discover_sessions(worktree_root: str) -> list[dict[str, Any]]:
    """Find all real Copilot sessions whose workspace maps to *worktree_root*.

    Matching is on the normalized workspace cwd (falling back to git_root), so
    it is case- and slash-insensitive. Sorted most recent first.
    """
    target = _normalize_cwd(worktree_root)
    out = [
        _session_record(d, ws, cwd, events)
        for d, ws, cwd, events in _iter_sessions()
        if _workspace_cwd(ws) == target
    ]
    return _sort_recent(out)


def discover_by_suffix(suffix: str, machine: str | None = None) -> list[dict[str, Any]]:
    """Find real sessions whose worktree directory ends with ``-<suffix>``.

    This is the "hunt it down from a short id" path: the operator passes just
    the 4-ish-char worktree suffix (e.g. ``fbc5``) instead of a full path.
    When *machine* is given, candidates are additionally filtered to worktrees
    whose directory name starts with that machine designation (worktree names
    begin with ``<machine>-...``), disambiguating a suffix reused across hosts.
    Sorted most recent first.
    """
    suffix = suffix.lower().lstrip("-")
    machine = machine.lower() if machine else None
    out: list[dict[str, Any]] = []
    for d, ws, cwd, events in _iter_sessions():
        base = Path(cwd).name.lower()
        if not (_worktree_suffix(cwd) == suffix or base.endswith("-" + suffix)):
            continue
        if machine and not base.startswith(machine):
            continue
        out.append(_session_record(d, ws, cwd, events))
    return _sort_recent(out)


def _session_dir_by_id(session_id: str) -> Path | None:
    """Resolve a session directory by bare UUID, if it exists."""
    candidate = _session_state_root() / session_id
    return candidate if candidate.is_dir() else None


def _default_output_dir(session_id: str) -> Path:
    """Ephemeral digest dir that ``read-session-digest`` can find via its
    temp fallback (``$TEMP/session-digest/<id>``)."""
    tmp = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
    return Path(tmp) / "session-digest" / session_id


# ---------------------------------------------------------------------------
# Cross-machine delegation
# ---------------------------------------------------------------------------


def _is_local_machine(name: str | None) -> bool:
    """True if *name* refers to the machine we're running on.

    Compared against the detected hostname (``-wsl`` suffix ignored), tolerating
    the machine-designation prefix worktree names carry (e.g. ``lambda-core``
    vs ``lambda-core-win``).
    """
    if not name:
        return True
    local = detect_machine().lower().removesuffix("-wsl")
    n = name.lower().removesuffix("-wsl")
    return n == local or local.startswith(n) or n.startswith(local)


def _delegate_remote(machine: str, ref: str, passthrough: list[str]) -> int:
    """Run ``ramp-up-session`` on *machine* over SSH and relay its output.

    The session's raw ``events.jsonl`` lives on the machine that produced it, so
    a worktree on another host is ramped up *there*. ``machine`` is used as the
    SSH destination directly — in a facility whose SSH aliases are the machine
    names this "just works"; anywhere else it must be an SSH-resolvable host
    with ``ramp-up-session`` on PATH.
    """
    remote = ["ramp-up-session", ref, *passthrough]
    print(f"# Hunting worktree '{ref}' on {machine} via ssh...", file=sys.stderr)
    try:
        proc = subprocess.run(["ssh", machine, *remote], check=False)
    except FileNotFoundError:
        print("error: ssh not found on PATH (needed to reach another machine)", file=sys.stderr)
        return 1
    return proc.returncode


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_candidate_list(worktree_root: str, sessions: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    parts.append("# Ramp-Up Candidates\n")
    parts.append(f"- **Worktree:** `{worktree_root}`")
    parts.append(f"- **Sessions found:** {len(sessions)}\n")
    if not sessions:
        parts.append("_No dormant sessions found for this worktree._")
        parts.append("")
        parts.append(
            "A session matches when its `workspace.yaml` cwd/git_root equals the "
            "worktree path above. If you expected one, confirm the path is the "
            "worktree root (not a subdirectory)."
        )
        return "\n".join(parts)
    for i, s in enumerate(sessions):
        marker = " (most recent)" if i == 0 else ""
        parts.append(f"### {i + 1}. `{s['id']}`{marker}")
        if s.get("name"):
            parts.append(f"- **Name:** {s['name']}")
        parts.append(f"- **Branch:** {s.get('branch') or 'unknown'}")
        parts.append(f"- **Last active:** {fmt_ts(s.get('updated_at')) or 'unknown'}")
        parts.append(f"- **Started:** {fmt_ts(s.get('created_at')) or 'unknown'}")
        if s.get("summary"):
            parts.append(f"- **Auto-summary:** {s['summary']}")
        parts.append("")
    parts.append(
        "Ramp up the most recent with `ramp-up-session` (no `--list`), or a "
        "specific one with `--session <id>`."
    )
    return "\n".join(parts)


def _render_brief(
    session: dict[str, Any],
    workspace: dict[str, str],
    session_start: dict[str, Any],
    checkpoints: list[dict[str, str]],
    turns: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    tail_turns: int,
    digest_dir: Path,
    other_count: int,
) -> str:
    """Render the human-facing takeover brief."""
    parts: list[str] = []
    parts.append("# Session Ramp-Up Brief\n")
    parts.append(
        "You are taking over a dormant Copilot session. The section below "
        "reconstructs where it left off so you can pick up the torch."
    )
    parts.append("")

    # Metadata (reuse the collate formatter; drop its leading "# Session Digest").
    meta = _format_metadata(workspace, session_start, cutoff_time=None)
    parts.extend(m for m in meta if not m.startswith("# Session Digest"))

    # Checkpoints are the CLI's own pre-compaction summaries -- the single best
    # signal of accumulated work.
    parts.extend(_format_checkpoints(checkpoints))

    parts.extend(_format_stats(turns, checkpoints, snapshots))

    # The tail: the last few turns, verbatim from the transcript.
    total = len(turns)
    if total == 0:
        parts.append("## Where It Left Off\n")
        parts.append("_No conversation turns were recorded in this session._")
        parts.append("")
    else:
        shown = min(tail_turns, total)
        first = total - shown + 1
        parts.append("## Where It Left Off\n")
        parts.append(
            f"The last {shown} of {total} turn(s) are shown below "
            f"(turns {first}-{total}). Checkpoints above cover earlier work.\n"
        )
        for i in range(first, total + 1):
            parts.append(_format_turn(turns[i - 1], i))
            parts.append("")

    # Deep-dive pointer.
    parts.append("## Read More\n")
    parts.append(
        f"The full transcript was collated (ephemerally) to `{digest_dir}`. "
        "Read deeper with:"
    )
    parts.append("")
    parts.append("```")
    parts.append(f"read-session-digest {session['id']} list")
    parts.append(f"read-session-digest {session['id']} context")
    parts.append(f"read-session-digest {session['id']} segment <N>")
    parts.append(f"read-session-digest {session['id']} grep --pattern <regex>")
    parts.append("```")
    if other_count:
        parts.append("")
        parts.append(
            f"_There are {other_count} older session(s) for this worktree; "
            "list them with `ramp-up-session <worktree> --list`._"
        )
    parts.append("")
    parts.append("## Take Over\n")
    parts.append(
        "Before continuing the work: inspect the worktree's own state "
        "(`git status`, `git log`, uncommitted diffs) to see what has and has "
        "not been committed, and reconcile it against the tail above. Then "
        "resume the in-flight task."
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ramp up into a dormant worktree's Copilot session.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "worktree",
        nargs="?",
        default=".",
        metavar="WORKTREE",
        help="Worktree to ramp up: a short suffix (e.g. 'fbc5'), a full path, "
        "or '.' for the current directory (default)",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Ramp up a specific session UUID (overrides worktree discovery)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List candidate sessions for the worktree and exit",
    )
    parser.add_argument(
        "--tail-turns",
        type=int,
        default=DEFAULT_TAIL_TURNS,
        help=f"Turns to surface inline in the brief (default: {DEFAULT_TAIL_TURNS})",
    )
    parser.add_argument(
        "--segment-size",
        type=int,
        default=DEFAULT_SEGMENT_SIZE,
        help=f"Max chars per transcript segment (default: {DEFAULT_SEGMENT_SIZE})",
    )
    parser.add_argument(
        "--max-tool-output",
        type=int,
        default=DEFAULT_MAX_TOOL_OUTPUT,
        help=f"Max chars per tool result (default: {DEFAULT_MAX_TOOL_OUTPUT})",
    )
    parser.add_argument(
        "--machine",
        default=None,
        help="Machine the worktree lives on. When it names another host, the "
        "hunt is delegated over `ssh <machine>` (session data is local to the "
        "machine that produced it). Locally, it filters suffix matches by host.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Ephemeral digest output dir (default: $TEMP/session-digest/<id>)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON summary instead of the Markdown brief",
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ref = args.worktree

    # ── Cross-machine: delegate the hunt over SSH ──
    # A worktree on another host has its session data there, so ramp it up there.
    # (Not for --session, which is a raw UUID meaningful only where it lives, nor
    # for an explicit local path.)
    if args.machine and not _is_local_machine(args.machine) and not args.session:
        if Path(ref).is_dir():
            print(
                "error: --machine names another host but a local path was given; "
                "pass the worktree suffix instead.",
                file=sys.stderr,
            )
            sys.exit(1)
        passthrough: list[str] = ["--tail-turns", str(args.tail_turns)]
        if args.list:
            passthrough.append("--list")
        if args.json:
            passthrough.append("--json")
        sys.exit(_delegate_remote(args.machine, ref, passthrough))

    machine = (args.machine or detect_machine()).lower()
    local_filter = args.machine if (args.machine and _is_local_machine(args.machine)) else None

    # ── Resolve the target session(s) locally ──
    sessions: list[dict[str, Any]] = []
    worktree_root: str | None = None

    if args.session:
        session_dir = _session_dir_by_id(args.session)
        if session_dir is None:
            print(
                f"error: session not found: {args.session}\n"
                f"  checked: {_session_state_root() / args.session}",
                file=sys.stderr,
            )
            sys.exit(1)
        ws = read_workspace(session_dir)
        chosen = {
            "id": session_dir.name,
            "dir": session_dir,
            "cwd": ws.get("cwd", ""),
            "branch": ws.get("branch", ""),
            "name": ws.get("name", ""),
            "summary": ws.get("summary", ""),
            "created_at": ws.get("created_at", ""),
            "updated_at": ws.get("updated_at", ""),
        }
        worktree_root = _normalize_cwd(ws.get("cwd", "")) or None
        sessions = [chosen]
    elif ref in (".", "") or Path(ref).is_dir():
        # An explicit worktree path (or the current directory).
        base = Path(ref) if Path(ref).is_dir() else Path.cwd()
        worktree_root = _normalize_cwd(str(base.resolve()))
        sessions = discover_sessions(worktree_root)
    else:
        # A short worktree suffix -- hunt it down in the local session store.
        sessions = discover_by_suffix(ref, local_filter)
        if sessions:
            worktree_root = _normalize_cwd(sessions[0]["cwd"]) or None

    query_label = worktree_root or f"suffix '{ref}'"

    # ── List mode ──
    if args.list:
        if args.json:
            print(
                json.dumps(
                    {
                        "query": query_label,
                        "machine": machine,
                        "sessions": [
                            {k: (str(v) if k == "dir" else v) for k, v in s.items()}
                            for s in sessions
                        ],
                    },
                    indent=2,
                )
            )
        else:
            print(_render_candidate_list(query_label, sessions))
        return

    if not sessions:
        print(
            f"error: no dormant sessions found for: {query_label}\n"
            "hint: pass the worktree suffix (e.g. 'fbc5'), a full path, or "
            "`--session <id>`; add `--machine <name>` to hunt another host; "
            "use `--list` to enumerate candidates.",
            file=sys.stderr,
        )
        sys.exit(1)

    chosen = sessions[0]
    session_dir: Path = chosen["dir"]
    session_id: str = chosen["id"]

    # ── Collate (reuse the segmenter engine) into an ephemeral digest ──
    out_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(session_id)
    if out_dir.exists():
        for f in out_dir.iterdir():
            if f.is_file() and (f.name.endswith(".md") or f.name == "manifest.yaml"):
                f.unlink()

    workspace = read_workspace(session_dir)
    checkpoints = read_checkpoints(session_dir)
    snapshots = read_rewind_index(session_dir)
    parsed = parse_events(session_dir, cutoff=None, max_tool_output=args.max_tool_output)

    write_segments(
        workspace=workspace,
        session_start=parsed["session_start"],
        checkpoints=checkpoints,
        turns=parsed["turns"],
        snapshots=snapshots,
        cutoff_time=None,
        output_dir=out_dir,
        segment_size=args.segment_size,
        max_tool_output=args.max_tool_output,
    )

    turns = parsed["turns"]
    if args.json:
        total_tools = sum(len(t.get("tool_calls", [])) for t in turns)
        failed_tools = sum(
            1
            for t in turns
            for tc in t.get("tool_calls", [])
            if tc.get("success") is False
        )
        print(
            json.dumps(
                {
                    "machine": machine,
                    "session_id": session_id,
                    "session_dir": str(session_dir),
                    "digest_dir": str(out_dir),
                    "worktree": worktree_root,
                    "branch": chosen.get("branch", ""),
                    "name": chosen.get("name", ""),
                    "updated_at": chosen.get("updated_at", ""),
                    "turns": len(turns),
                    "tool_calls": total_tools,
                    "failed_tool_calls": failed_tools,
                    "checkpoints": len(checkpoints),
                    "other_sessions": len(sessions) - 1,
                },
                indent=2,
            )
        )
        return

    print(
        _render_brief(
            session=chosen,
            workspace=workspace,
            session_start=parsed["session_start"],
            checkpoints=checkpoints,
            turns=turns,
            snapshots=snapshots,
            tail_turns=args.tail_turns,
            digest_dir=out_dir,
            other_count=len(sessions) - 1,
        )
    )


if __name__ == "__main__":
    main()
