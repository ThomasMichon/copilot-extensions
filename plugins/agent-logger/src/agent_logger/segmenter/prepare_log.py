#!/usr/bin/env python3
"""Prepare metadata for a session log.

Detects machine and environment (native vs WSL), generates cutoff timestamp,
ensures the target directory exists, and prints structured info for the
calling agent.

Log path structure:
    rendered from agent-logger config (optionally repo-local .agent-logger.yaml)

Usage:
    prepare-session-log [--title TITLE] [--session ID] [--json]

Prints YAML-style key: value pairs to stdout for easy parsing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_logger.config import RepositoryConfigError, load_config
from agent_logger.segmenter.platform import detect_machine, sanitize_path_component


def _parse_machine(raw: str) -> tuple[str, bool]:
    """Split detect_machine() result into (machine, is_wsl).

    If *raw* ends with ``-wsl`` (e.g. ``lambda-core-wsl``), strip the suffix
    and flag the environment as WSL.
    """
    if raw.endswith("-wsl"):
        return raw[:-4], True
    return raw, False


def _read_start_time(session_dir: Path) -> str | None:
    """Try to extract the timestamp from the first event in events.jsonl."""
    events_file = session_dir / "events.jsonl"
    if not events_file.exists():
        return None
    try:
        with events_file.open("r", encoding="utf-8", errors="replace") as fh:
            first_line = fh.readline().strip()
        if not first_line:
            return None
        obj = json.loads(first_line)
        # Common field names for the timestamp
        for key in ("timestamp", "ts", "time", "created_at"):
            if key in obj:
                return str(obj[key])
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _normalize_cwd(path: str) -> str:
    """Normalize a cwd/git_root path for comparison (case + slashes)."""
    if not path or not path.strip():
        return ""
    return os.path.normcase(os.path.normpath(path.strip()))


def _project_root() -> str:
    """Return the normalized current working directory.

    Sessions are scoped to the project the user is currently in (CWD), not
    the script location -- the segmenter runs from an installed venv, not
    from inside the consuming repo.
    """
    return _normalize_cwd(str(Path.cwd()))


def find_current_session() -> tuple[str, Path]:
    """Find the most recently modified session directory for this repo/worktree.

    Automatically scopes to sessions whose workspace cwd/git_root matches
    this script's repo root, preventing cross-worktree contamination.

    Returns (session_id, session_dir).
    """
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    state_root = Path(home) / ".copilot" / "session-state"
    if not state_root.is_dir():
        raise FileNotFoundError(f"No session state directory: {state_root}")

    norm_filter = _project_root()

    candidates = []
    for d in state_root.iterdir():
        ef = d / "events.jsonl"
        if not ef.exists():
            continue
        ws_file = d / "workspace.yaml"
        if ws_file.exists():
            ws_cwd = ""
            for line in ws_file.read_text(encoding="utf-8").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip()
                    if k in ("cwd", "git_root") and not ws_cwd:
                        ws_cwd = _normalize_cwd(v.strip())
            if ws_cwd != norm_filter:
                continue
        else:
            continue  # no workspace.yaml → can't verify, skip
        candidates.append((ef.stat().st_mtime, d))
    if not candidates:
        raise FileNotFoundError("No session directories with events.jsonl")
    candidates.sort(reverse=True)
    best = candidates[0][1]
    return best.name, best


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare session log metadata.")
    parser.add_argument("--title", default=None, help="Log title (used in filename)")
    parser.add_argument("--session", default="current", help="Session UUID or 'current'")
    parser.add_argument("--log-root", default=None, help="Root dir for logs (default: config/CWD)")
    parser.add_argument("--machine", default=None, help="Override auto-detected machine name")
    parser.add_argument("--json", action="store_true", help="Print all values as JSON")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        cfg = load_config()
    except RepositoryConfigError as exc:
        print(f"Error: invalid repository configuration: {exc}", file=sys.stderr)
        sys.exit(2)
    organization = cfg.organization_manifest()

    # Machine & environment
    raw_machine = args.machine or cfg.machine_name or detect_machine()
    machine, is_wsl = _parse_machine(raw_machine)

    # Session
    if args.session == "current":
        session_id, session_dir = find_current_session()
    else:
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
        session_dir = Path(home) / ".copilot" / "session-state" / args.session
        session_id = args.session
        if not session_dir.is_dir():
            print(f"Error: session directory not found: {session_dir}", file=sys.stderr)
            sys.exit(1)

    # Cutoff
    now = datetime.now(timezone.utc)
    cutoff = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Local date/time. Configurable timezone; None -> system local time.
    tz = ZoneInfo(cfg.log_timezone) if cfg.log_timezone else None
    local_now = now.astimezone(tz)
    local_date = local_now.date()
    date_str = local_date.strftime("%Y-%m-%d")
    dd = local_date.strftime("%d")
    mm = local_date.strftime("%m")
    hhmmss = local_now.strftime("%H%M%S")

    title_part = args.title or "<Title>"
    # Strip any caller-supplied "(WSL)" suffix — we no longer append it.
    if title_part.rstrip().endswith("(WSL)"):
        title_part = title_part.rstrip()[: -len("(WSL)")].rstrip()
    # Sanitize for NTFS — colons, slashes, etc. break Windows git operations
    title_part = sanitize_path_component(title_part)

    # Log path — rendered from the configurable template under the log root.
    # Tokens: {year} {month} {day} {hhmmss} {machine} {title}.
    log_root = (
        Path(args.log_root).expanduser()
        if args.log_root
        else Path(organization["output_root"])
    )
    rel_path = cfg.log_path_template.format(
        year=local_date.year,
        month=mm,
        day=dd,
        hhmmss=hhmmss,
        machine=machine,
        title=title_part,
    )
    log_path = log_root / rel_path
    log_dir = log_path.parent
    log_filename = log_path.name
    log_dir.mkdir(parents=True, exist_ok=True)

    # Digest dir (session-scoped to avoid stale files on retries)
    digest_dir = Path(os.environ.get("TEMP", "/tmp")) / "session-digest" / session_id

    # Session start time (best-effort from first event)
    start_time = _read_start_time(session_dir)

    result = {
        "machine": machine,
        "environment": "WSL" if is_wsl else None,
        "session_id": session_id,
        "session_dir": str(session_dir),
        "cutoff": cutoff,
        "date": date_str,
        "start_time": start_time,
        "log_dir": str(log_dir),
        "log_filename": log_filename,
        "log_path": str(log_path),
        "digest_dir": str(digest_dir),
        **organization,
        "output_root": str(log_root),
        "repo_config_path": str(cfg.repo_config_path) if cfg.repo_config_path else None,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return

    # Output
    print(f"machine: {machine}")
    if is_wsl:
        print("environment: WSL")
    print(f"session_id: {session_id}")
    print(f"session_dir: {session_dir}")
    print(f"cutoff: {cutoff}")
    print(f"date: {date_str}")
    if start_time:
        print(f"start_time: {start_time}")
    print(f"log_dir: {log_dir}")
    print(f"log_filename: {log_filename}")
    print(f"log_path: {log_path}")
    print(f"digest_dir: {digest_dir}")
    print(f"output_root: {log_root}")
    print(f"log_path_template: {cfg.log_path_template}")
    if cfg.log_timezone:
        print(f"timezone: {cfg.log_timezone}")
    print(f"note_marker: {cfg.note_marker}")
    if cfg.repo_config_path:
        print(f"repo_config_path: {cfg.repo_config_path}")
    if cfg.log_template:
        print("log_template: <configured; use --json to read>")
    if cfg.narration_style:
        print("narration_style: <configured; use --json to read>")
    if cfg.exemplars:
        print("exemplars: <configured; use --json to read>")
    if cfg.closing_remark:
        print("closing_remark: <configured; use --json to read>")


if __name__ == "__main__":
    main()
