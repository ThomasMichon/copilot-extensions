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
  - 00-context.md  (metadata + checkpoints + stats — always present)
  - 01-turns.md, 02-turns.md, ...  (transcript segments, split at turn
    boundaries, each ≤ --segment-size chars)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_logger.config import load_config
from agent_logger.segmenter.platform import detect_machine

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_TOOL_OUTPUT = 600      # chars per tool result body
DEFAULT_MAX_TOTAL = 120_000        # overall output budget (~30k tokens)
DEFAULT_SEGMENT_SIZE = 80_000      # ~20k tokens per segment file
DEFAULT_BACKFILL_COUNT = 3         # how many previous sessions to link/backfill


def _default_digest_root() -> str:
    """Local persistent digest store root (from agent-logger config).

    Defaults to ``~/.agent-logger/session-digests/``. Override via the
    ``--nas-root`` flag (kept for backward compatibility) or by setting
    ``store_dir`` in the agent-logger config.
    """
    return str(load_config().store_dir)


DEFAULT_NAS_ROOT = _default_digest_root()
COPILOT_STATE_DIR_NAME = ".copilot"
SESSION_STATE_SUBDIR = "session-state"

# Patterns that look like secrets — redact on sight
SECRET_PATTERNS = [
    re.compile(r'(?i)(token|secret|password|apikey|api_key|credential)\s*[:=]\s*\S+'),
    re.compile(r'ghp_[A-Za-z0-9_]{36,}'),
    re.compile(r'ghu_[A-Za-z0-9_]{36,}'),
    re.compile(r'ghs_[A-Za-z0-9_]{36,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{22,}'),
    re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*'),
    re.compile(r'eyJ[A-Za-z0-9\-_]{20,}\.eyJ[A-Za-z0-9\-_]{20,}'),  # JWT
]

# Event types to skip entirely — no useful log content
SKIP_EVENT_TYPES = {
    "hook.start",
    "hook.end",
}

# Tool calls that are noise for logging purposes
SKIP_TOOL_NAMES = {
    "report_intent",
}


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
    if p.is_absolute() and p.is_dir():
        return p

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

    # Treat as UUID
    candidate = state_root / spec
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(f"Session directory not found: {candidate}")


def redact(text: str) -> str:
    """Replace secret-shaped strings with [REDACTED]."""
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def truncate(text: str, limit: int) -> str:
    """Truncate to *limit* chars, appending a note if trimmed."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} total chars]"



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
    turns: list[dict[str, Any]],
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
        f"turn_count: {len(turns)}",
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

    # Read session artifacts
    workspace = read_workspace(session_dir)
    checkpoints = read_checkpoints(session_dir)
    snapshots = read_rewind_index(session_dir)
    parsed = parse_events(session_dir, cutoff, max_tool_output, since=since)

    # Write segments
    segments = write_segments(
        workspace=workspace,
        session_start=parsed["session_start"],
        checkpoints=checkpoints,
        turns=parsed["turns"],
        snapshots=snapshots,
        cutoff_time=cutoff,
        output_dir=nas_digest_dir,
        segment_size=segment_size,
        max_tool_output=max_tool_output,
        since_time=since,
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
        workspace=workspace,
        session_start=parsed["session_start"],
        segments=segments,
        checkpoints=checkpoints,
        turns=parsed["turns"],
        previous_sessions=previous,
        nas_root=nas_root,
        since=since,
        cutoff=cutoff,
    )

    # Update session-level index.yaml
    _write_index(nas_session_dir, session_id)

    return nas_session_dir, nas_digest_dir


def fmt_ts(iso: str | None) -> str:
    """Format an ISO timestamp to a readable form."""
    if not iso:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return iso


def strip_system_tags(text: str) -> str:
    """Remove <current_datetime>, <reminder>, and similar system wrapper tags."""
    # Remove known system-injected blocks
    text = re.sub(
        r'<current_datetime>.*?</current_datetime>\s*',
        '', text, flags=re.DOTALL,
    )
    text = re.sub(
        r'<reminder>.*?</reminder>\s*',
        '', text, flags=re.DOTALL,
    )
    return text.strip()


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def read_workspace(session_dir: Path) -> dict[str, str]:
    """Parse workspace.yaml (simple key: value format)."""
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.exists():
        return {}
    result: dict[str, str] = {}
    for line in ws_file.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            result[k.strip()] = v.strip()
    return result


def read_checkpoints(session_dir: Path) -> list[dict[str, str]]:
    """Read checkpoint markdown files, returning list of {filename, content}."""
    cp_dir = session_dir / "checkpoints"
    if not cp_dir.is_dir():
        return []
    results = []
    for f in sorted(cp_dir.iterdir()):
        if f.name == "index.md" or not f.suffix == ".md":
            continue
        try:
            results.append({
                "filename": f.name,
                "content": f.read_text(encoding="utf-8"),
            })
        except Exception:
            pass
    return results


def read_rewind_index(session_dir: Path) -> list[dict[str, Any]]:
    """Read rewind-snapshots/index.json for per-turn metadata."""
    idx_file = session_dir / "rewind-snapshots" / "index.json"
    if not idx_file.exists():
        return []
    try:
        data = json.loads(idx_file.read_text(encoding="utf-8"))
        return data.get("snapshots", [])
    except Exception:
        return []


def parse_events(
    session_dir: Path,
    cutoff: str | None,
    max_tool_output: int,
    since: str | None = None,
) -> dict[str, Any]:
    """Parse events.jsonl into structured sections.

    Returns a dict with keys:
        session_start  — dict of session metadata
        turns          — list of turn dicts (user msg, assistant msgs, tools)
    """
    events_file = session_dir / "events.jsonl"
    if not events_file.exists():
        return {"session_start": {}, "turns": []}

    events: list[dict[str, Any]] = []
    with open(events_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    session_start: dict[str, Any] = {}
    turns: list[dict[str, Any]] = []
    current_turn: dict[str, Any] | None = None

    for evt in events:
        evt_type = evt.get("type", "")
        data = evt.get("data", {})
        timestamp = evt.get("timestamp", "")

        # Time window checks
        if since and timestamp and timestamp < since:
            # Still parse session.start for metadata even if before window
            if evt_type != "session.start":
                continue
        if cutoff and timestamp and timestamp > cutoff:
            break

        if evt_type in SKIP_EVENT_TYPES:
            continue

        if evt_type == "session.start":
            session_start = {
                "session_id": data.get("sessionId", ""),
                "version": data.get("copilotVersion", ""),
                "start_time": data.get("startTime", ""),
                "cwd": data.get("context", {}).get("cwd", ""),
                "git_root": data.get("context", {}).get("gitRoot", ""),
                "branch": data.get("context", {}).get("branch", ""),
                "head_commit": data.get("context", {}).get("headCommit", ""),
            }

        elif evt_type == "user.message":
            # Start a new turn
            content = data.get("content", "")
            content = strip_system_tags(content)
            current_turn = {
                "user_message": redact(content),
                "timestamp": timestamp,
                "assistant_messages": [],
                "tool_calls": [],
            }
            turns.append(current_turn)

        elif evt_type == "assistant.turn_start":
            if current_turn is None:
                current_turn = {
                    "user_message": "",
                    "timestamp": timestamp,
                    "assistant_messages": [],
                    "tool_calls": [],
                }
                turns.append(current_turn)

        elif evt_type == "assistant.message":
            if current_turn is None:
                continue

            # Extract reasoning/chain-of-thought (when available in plaintext)
            reasoning = data.get("reasoningText", "")
            if reasoning and reasoning.strip():
                current_turn["assistant_messages"].append(
                    f"*[Reasoning]:* {redact(reasoning)}"
                )

            # Extract prose content (non-tool-call text)
            content = data.get("content", "")
            if content and content.strip():
                current_turn["assistant_messages"].append(redact(content))

            # Extract tool requests (names + key args)
            for tr in data.get("toolRequests", []):
                tool_name = tr.get("name", "")
                if tool_name in SKIP_TOOL_NAMES:
                    continue
                args = tr.get("arguments", {})
                tool_call_id = tr.get("toolCallId", "")
                # Summarize args — keep small ones, truncate large
                args_summary = _summarize_tool_args(tool_name, args)
                current_turn["tool_calls"].append({
                    "id": tool_call_id,
                    "tool": tool_name,
                    "args": args_summary,
                    "result": None,  # filled by execution_complete
                    "success": None,
                })

        elif evt_type == "tool.execution_complete":
            if current_turn is None:
                continue
            call_id = data.get("toolCallId", "")
            success = data.get("success", True)
            result = data.get("result", {})
            result_content = result.get("content", "") if isinstance(result, dict) else str(result)

            # Find matching tool call and attach result
            for tc in current_turn["tool_calls"]:
                if tc["id"] == call_id:
                    tc["success"] = success
                    tc["result"] = truncate(
                        redact(str(result_content)),
                        max_tool_output,
                    )
                    break

    return {
        "session_start": session_start,
        "turns": turns,
    }


def _summarize_tool_args(tool_name: str, args: dict[str, Any]) -> str:
    """Produce a brief summary of tool call arguments."""
    if not args:
        return ""

    # For shell/powershell commands, show the command
    if tool_name in ("powershell", "bash"):
        cmd = args.get("command", "")
        desc = args.get("description", "")
        if desc:
            return f"{desc}: {truncate(cmd, 200)}"
        return truncate(cmd, 200)

    # For file operations, show the path
    if tool_name in ("view", "create", "edit"):
        path = args.get("path", "")
        if tool_name == "edit":
            old = args.get("old_str", "")
            return f"{path} (edit: {truncate(old, 80)})"
        return path

    # For grep/glob, show the pattern
    if tool_name in ("grep", "glob"):
        pattern = args.get("pattern", "")
        path = args.get("path", "")
        return f"pattern={pattern}" + (f" in {path}" if path else "")

    # For task/sub-agents, show the description
    if tool_name == "task":
        desc = args.get("description", "")
        agent = args.get("agent_type", "")
        return f"{agent}: {desc}"

    # For session_store_sql / sql
    if tool_name in ("session_store_sql", "sql"):
        return args.get("description", "") or truncate(args.get("query", ""), 120)

    # For web_fetch / web_search
    if tool_name in ("web_fetch", "web_search"):
        return args.get("url", "") or args.get("query", "")

    # For skill invocation
    if tool_name == "skill":
        return args.get("skill", "")

    # For ask_user
    if tool_name == "ask_user":
        return truncate(args.get("question", ""), 150)

    # For MCP tools, show the tool name hint and compact args
    compact = json.dumps(args, separators=(",", ":"), default=str)
    return truncate(compact, 200)


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _format_metadata(
    workspace: dict[str, str],
    session_start: dict[str, Any],
    cutoff_time: str | None,
    since_time: str | None = None,
) -> list[str]:
    """Render the metadata block as lines."""
    parts: list[str] = []
    sid = session_start.get("session_id") or workspace.get("id", "unknown")
    parts.append("# Session Digest\n")
    parts.append("## Metadata\n")
    parts.append(f"- **Session ID:** `{sid}`")
    _start = fmt_ts(session_start.get("start_time") or workspace.get("created_at"))
    parts.append(f"- **Session start:** {_start}")
    if since_time:
        parts.append(f"- **Digest since:** {fmt_ts(since_time)}")
    if cutoff_time:
        parts.append(f"- **Digest cutoff:** {fmt_ts(cutoff_time)}")
    _branch = session_start.get("branch") or workspace.get("branch", "unknown")
    parts.append(f"- **Branch:** {_branch}")
    _cwd = session_start.get("cwd") or workspace.get("cwd", "unknown")
    parts.append(f"- **Working dir:** {_cwd}")
    head = session_start.get("head_commit", "")
    if head:
        parts.append(f"- **Head commit:** `{head[:12]}`")
    summary = workspace.get("summary", "")
    if summary:
        parts.append(f"- **Auto-summary:** {summary}")
    cli_ver = session_start.get("version", "")
    if cli_ver:
        parts.append(f"- **CLI version:** {cli_ver}")
    parts.append("")
    return parts


def _format_checkpoints(checkpoints: list[dict[str, str]]) -> list[str]:
    """Render checkpoints as lines."""
    if not checkpoints:
        return []
    parts: list[str] = []
    parts.append("## Checkpoints (pre-compaction summaries)\n")
    parts.append("These are curated summaries written by the CLI before context")
    parts.append("compaction. They capture work done up to that point.\n")
    for i, cp in enumerate(checkpoints, 1):
        parts.append(f"### Checkpoint {i}: {cp['filename']}\n")
        parts.append(cp["content"])
        parts.append("")
    return parts


def _format_stats(
    turns: list[dict[str, Any]],
    checkpoints: list[dict[str, str]],
    snapshots: list[dict[str, Any]],
) -> list[str]:
    """Render session stats as lines."""
    total_tools = sum(len(t.get("tool_calls", [])) for t in turns)
    failed_tools = sum(
        1 for t in turns
        for tc in t.get("tool_calls", [])
        if tc.get("success") is False
    )
    parts: list[str] = []
    parts.append("## Session Stats\n")
    parts.append(f"- **Turns:** {len(turns)}")
    parts.append(f"- **Tool calls:** {total_tools}")
    if failed_tools:
        parts.append(f"- **Failed tool calls:** {failed_tools}")
    parts.append(f"- **Checkpoints:** {len(checkpoints)}")
    parts.append(f"- **Rewind snapshots:** {len(snapshots)}")
    parts.append("")
    return parts


def _format_turn(turn: dict[str, Any], index: int) -> str:
    """Render a single turn as a Markdown string."""
    parts: list[str] = []
    parts.append(f"### Turn {index}")
    if turn.get("timestamp"):
        parts.append(f"*{fmt_ts(turn['timestamp'])}*\n")

    user_msg = turn.get("user_message", "").strip()
    if user_msg:
        parts.append("**User:**\n")
        parts.append(user_msg)
        parts.append("")

    for msg in turn.get("assistant_messages", []):
        msg = msg.strip()
        if msg:
            parts.append("**Assistant:**\n")
            parts.append(msg)
            parts.append("")

    tool_calls = turn.get("tool_calls", [])
    if tool_calls:
        parts.append("**Tool usage:**\n")
        for tc in tool_calls:
            status = "✓" if tc.get("success") else ("✗" if tc.get("success") is False else "?")
            parts.append(f"- `{tc['tool']}` [{status}] — {tc.get('args', '')}")
            result = tc.get("result")
            if result and tc.get("success") is False:
                parts.append(f"  ```\n  {result}\n  ```")
            elif result and len(result) > 20:
                parts.append("  <details><summary>Result preview</summary>\n")
                parts.append(f"  ```\n  {result}\n  ```")
                parts.append("  </details>")
        parts.append("")

    return "\n".join(parts)


def format_digest(
    workspace: dict[str, str],
    session_start: dict[str, Any],
    checkpoints: list[dict[str, str]],
    turns: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    cutoff_time: str | None,
    max_total: int,
    since_time: str | None = None,
) -> str:
    """Render the full digest as a single string (stdout mode)."""
    parts: list[str] = []
    parts.extend(_format_metadata(workspace, session_start, cutoff_time, since_time))
    parts.extend(_format_checkpoints(checkpoints))

    parts.append("## Conversation Transcript\n")
    if checkpoints:
        parts.append("*Note: Checkpoints above cover earlier work. The transcript")
        parts.append("below includes the full session for completeness, but checkpoint")
        parts.append("content is authoritative for the periods they cover.*\n")

    for i, turn in enumerate(turns, 1):
        parts.append(_format_turn(turn, i))

    parts.extend(_format_stats(turns, checkpoints, snapshots))

    output = "\n".join(parts)
    if len(output) > max_total:
        output = output[:max_total] + f"\n\n... [digest truncated at {max_total} chars]"
    return output


def write_segments(
    workspace: dict[str, str],
    session_start: dict[str, Any],
    checkpoints: list[dict[str, str]],
    turns: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    cutoff_time: str | None,
    output_dir: Path,
    segment_size: int,
    max_tool_output: int,
    since_time: str | None = None,
) -> list[str]:
    """Write segmented digest files to output_dir.

    Returns a list of created file paths (relative to output_dir).

    Layout:
      00-context.md   — metadata + checkpoints + stats (always present)
      01-turns.md     — first batch of turns
      02-turns.md     — next batch, etc.

    Turns are never split mid-turn. Each segment file is ≤ segment_size
    chars (soft limit — a single huge turn may exceed it).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    # ── Context file (always written) ──
    ctx_parts: list[str] = []
    ctx_parts.extend(_format_metadata(workspace, session_start, cutoff_time, since_time))
    ctx_parts.extend(_format_checkpoints(checkpoints))
    ctx_parts.extend(_format_stats(turns, checkpoints, snapshots))

    # Add segment inventory so each reader knows the full picture
    total_turns = len(turns)
    ctx_parts.append("## Segment Inventory\n")
    ctx_parts.append(f"The conversation transcript ({total_turns} turns) has been split")
    ctx_parts.append("into numbered segment files in this directory. Each segment")
    ctx_parts.append("contains a consecutive batch of turns. Read them in order to")
    ctx_parts.append("reconstruct the full session.\n")

    ctx_file = output_dir / "00-context.md"
    ctx_file.write_text("\n".join(ctx_parts), encoding="utf-8")
    created.append(ctx_file.name)

    # ── Turn segments ──
    seg_num = 1
    seg_lines: list[str] = []
    seg_chars = 0
    first_turn_in_seg = 1

    def _flush_segment() -> None:
        nonlocal seg_num, seg_lines, seg_chars, first_turn_in_seg
        if not seg_lines:
            return
        fname = f"{seg_num:02d}-turns.md"
        header = f"# Transcript Segment {seg_num}\n\n"
        content = header + "\n".join(seg_lines)
        (output_dir / fname).write_text(content, encoding="utf-8")
        created.append(fname)
        seg_num += 1
        seg_lines = []
        seg_chars = 0

    for i, turn in enumerate(turns, 1):
        turn_text = _format_turn(turn, i)
        turn_len = len(turn_text)

        # If adding this turn would exceed the segment size, flush first
        if seg_chars > 0 and seg_chars + turn_len > segment_size:
            _flush_segment()
            first_turn_in_seg = i

        seg_lines.append(turn_text)
        seg_chars += turn_len

    _flush_segment()  # final segment

    return created


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
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
        # Segmented mode — write files to local directory
        out_dir = Path(args.output_dir)
        # Clean stale files from previous runs
        if out_dir.exists():
            for f in out_dir.iterdir():
                if f.is_file() and (f.name.endswith(".md") or f.name == "manifest.yaml"):
                    f.unlink()
        workspace = read_workspace(session_dir)
        checkpoints = read_checkpoints(session_dir)
        snapshots = read_rewind_index(session_dir)
        parsed = parse_events(session_dir, args.cutoff, args.max_tool_output, since=args.since)
        out_dir = Path(args.output_dir)
        files = write_segments(
            workspace=workspace,
            session_start=parsed["session_start"],
            checkpoints=checkpoints,
            turns=parsed["turns"],
            snapshots=snapshots,
            cutoff_time=args.cutoff,
            output_dir=out_dir,
            segment_size=args.segment_size,
            max_tool_output=args.max_tool_output,
            since_time=args.since,
        )
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
        workspace = read_workspace(session_dir)
        checkpoints = read_checkpoints(session_dir)
        snapshots = read_rewind_index(session_dir)
        parsed = parse_events(session_dir, args.cutoff, args.max_tool_output, since=args.since)
        digest = format_digest(
            workspace=workspace,
            session_start=parsed["session_start"],
            checkpoints=checkpoints,
            turns=parsed["turns"],
            snapshots=snapshots,
            cutoff_time=args.cutoff,
            max_total=args.max_total,
            since_time=args.since,
        )
        print(digest)


if __name__ == "__main__":
    main()
