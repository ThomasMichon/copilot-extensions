#!/usr/bin/env python3
"""Statelessness write-routing guard -- a Copilot CLI ``preToolUse`` command hook.

Blocks an agent from writing **personal state** (efforts/, logs/, weekly-updates/,
icm/, ownership/assignment files) INTO a **stateless harness** checkout, nudging
it to route the write to the bound knowledge repo instead (resolve with
``agent-worktrees state-root``). This is the runtime enforcement of the
statelessness routing rule the harness AGENTS.md documents; it complements the
commit-time CI lint (``statelessness_lint.py``) in the harness repo.

Wiring: agent-worktrees' ``hooks.json`` declares a ``preToolUse`` command hook
that runs this script (deployed to ``~/.agent-worktrees/bin/``). The hook payload
arrives as JSON on **stdin**; the decision is written as JSON to **stdout**:

    {"permissionDecision": "deny", "permissionDecisionReason": "<nudge>"}

Anything else (empty / ``{"permissionDecision":"allow"}``) lets the tool proceed.

**Fail-open by construction.** ``preToolUse`` command hooks are *fail-closed on a
non-zero exit* (a crash would DENY every tool). So this script wraps everything
and, on ANY error or ambiguity, prints an allow (or nothing) and exits 0. It only
denies on a clear personal-state write into a confirmed stateless-harness
checkout. It is intentionally cheap (no subprocess, small file reads) to stay well
under the per-tool-call timeout.

Escape hatches:
  * ``AGENT_WORKTREES_STATELESS_GUARD=off`` (or 0/false/no) disables it.
  * ``agent-worktrees repos allow-edits <harness> --reason "..."`` opens a
    time-boxed break-glass (``~/.agent-worktrees/allow-edits.json``) that the
    guard honors.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# --- Personal-state paths that must never be written into a stateless harness -
FORBIDDEN_PREFIXES = ("efforts/", "logs/", "weekly-updates/", "icm/")
FORBIDDEN_FILES = ("ownership.yml", "dev-assignments.yml")

# --- Tool classification ------------------------------------------------------
WRITE_TOOLS = frozenset({
    "create", "edit", "str_replace", "str_replace_editor",
    "str_replace_based_edit_tool", "write", "write_file", "insert",
    "apply_patch", "new_file", "multi_edit",
})
SHELL_TOOLS = frozenset({
    "bash", "sh", "shell", "powershell", "pwsh", "cmd", "run", "run_command",
    "execute", "exec", "terminal",
})
PATH_ARG_KEYS = ("path", "file_path", "filePath", "filename", "fileName",
                 "target_file", "targetFile")
CMD_ARG_KEYS = ("command", "cmd", "script", "commandLine", "commandline", "input")

# Write-ish verbs (PowerShell + POSIX + git mutations); a shell command that
# writes into a personal-state path under the harness flips it into a block.
_WRITE_VERBS = re.compile(
    "|".join([
        "Set-Content", "Add-Content", "Out-File", "New-Item", "Remove-Item",
        "Move-Item", "Copy-Item", "Clear-Content", "Rename-Item", "Tee-Object",
        ">>?",
        r"\btee\b", r"\bsed\b\s+-i", r"\bcp\b", r"\bmv\b", r"\brm\b",
        r"\btouch\b", r"\bmkdir\b",
        r"git\s+(?:-C\s+\S+\s+)?(?:add|commit|apply|mv|rm|checkout|restore)",
    ]),
    re.IGNORECASE,
)

_STATELESS_RE = re.compile(
    r"^\s*(?:stateless|requires_external_state_root)\s*:\s*true\s*(?:#.*)?$",
    re.MULTILINE,
)


def _truthy_off(v: str | None) -> bool:
    return (v or "").strip().lower() in {"off", "0", "false", "no"}


def find_repo_root(start: str) -> Path | None:
    """Walk up from ``start`` to the nearest dir containing ``.git`` (file or dir).

    A worktree's ``.git`` is a file (gitdir pointer); a normal checkout's is a
    dir -- either counts. Returns None if none found (not a git repo => allow).
    """
    try:
        here = Path(start).resolve()
    except (OSError, ValueError):
        return None
    for d in (here, *here.parents):
        if (d / ".git").exists():
            return d
    return None


def requires_external_state_root(root: Path) -> bool:
    """True if the repo declares ``stateless: true`` or
    ``requires_external_state_root: true`` in its in-repo agent-worktrees config."""
    cfg = root / ".agent-worktrees" / "config.yaml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return bool(_STATELESS_RE.search(text))


def _rel_is_personal_state(rel: str) -> bool:
    rel = rel.replace("\\", "/").lstrip("./")
    if any(rel == p.rstrip("/") or rel.startswith(p) for p in FORBIDDEN_PREFIXES):
        return True
    base = rel.rsplit("/", 1)[-1]
    return rel in FORBIDDEN_FILES or base in FORBIDDEN_FILES


def path_is_personal_state_in_harness(abs_path: str, root: Path) -> bool:
    """True when ``abs_path`` is a personal-state path inside the harness root.

    Uses ``os.path.normcase`` containment (case-insensitive on Windows,
    exact on POSIX) rather than ``Path.relative_to`` -- the latter is a
    case-SENSITIVE string compare and would miss a lowercased shell path on
    Windows.
    """
    try:
        p = os.path.normcase(os.path.normpath(os.path.abspath(abs_path)))
        r = os.path.normcase(os.path.normpath(os.path.abspath(str(root))))
    except (OSError, ValueError):
        return False
    if p == r or not p.startswith(r + os.sep):
        return False
    return _rel_is_personal_state(p[len(r) + 1:])


def _as_args(tool_args) -> dict:
    if isinstance(tool_args, str):
        try:
            parsed = json.loads(tool_args)
            return parsed if isinstance(parsed, dict) else {"command": tool_args}
        except (ValueError, TypeError):
            return {"command": tool_args}
    return tool_args if isinstance(tool_args, dict) else {}


def _pick(args: dict, keys) -> str | None:
    for k in keys:
        v = args.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _resolve(p: str, cwd: str) -> str:
    return p if os.path.isabs(p) else os.path.join(cwd or os.getcwd(), p)


def active_break_glass(repo_name: str, home: Path) -> bool:
    """Honor a persisted ``allow-edits`` grant for the harness repo (epoch-ms)."""
    if not repo_name:
        return False
    try:
        data = json.loads((home / ".agent-worktrees" / "allow-edits.json").read_text("utf-8"))
        g = (data.get("grants") or {}).get(repo_name)
        import time
        return bool(g) and float(g.get("expires_at_ms", 0)) > time.time() * 1000
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _deny_reason(target: str, harness: str) -> str:
    return (
        f"stateless-harness guard: '{target}' is personal state (efforts/logs/"
        f"weekly-updates/icm/ownership) and must NOT be written into the stateless "
        f"harness checkout ('{harness}'). Route it to the bound knowledge repo: run "
        f"`agent-worktrees state-root` to resolve the destination and write there "
        f"instead. If a direct write is genuinely required, break glass: "
        f"`agent-worktrees repos allow-edits {harness} --reason \"<why>\"` (logged, "
        f"time-boxed), then retry. (Disable: AGENT_WORKTREES_STATELESS_GUARD=off.)"
    )


def decide(payload: dict, *, env=None, home=None) -> dict | None:
    """Return a deny decision dict, or None to allow. Pure/injectable for tests."""
    env = env if env is not None else os.environ
    home = Path(home) if home is not None else Path.home()

    if _truthy_off(env.get("AGENT_WORKTREES_STATELESS_GUARD")):
        return None
    if _truthy_off(env.get("CROSS_REPO_GUARD")):  # shared kill switch
        return None

    tool = str(payload.get("toolName") or payload.get("tool_name") or "")
    if tool not in WRITE_TOOLS and tool not in SHELL_TOOLS:
        return None  # reads / everything else: fast allow

    cwd = str(payload.get("cwd") or "")
    root = find_repo_root(cwd)
    if root is None or not requires_external_state_root(root):
        return None  # not a stateless harness => not our business

    harness = (env.get("WORKTREE_PROJECT") or root.name or "the-harness").strip()
    if active_break_glass(harness, home):
        return None

    args = _as_args(payload.get("toolArgs") if "toolArgs" in payload
                    else payload.get("tool_input"))

    if tool in WRITE_TOOLS:
        raw = _pick(args, PATH_ARG_KEYS)
        if raw:
            abs_path = _resolve(raw, cwd)
            if path_is_personal_state_in_harness(abs_path, root):
                return {"permissionDecision": "deny",
                        "permissionDecisionReason": _deny_reason(raw, harness)}
        return None

    # Shell: conservative -- an absolute personal-state path under the harness
    # root, alongside a write verb. (Relative shell paths are too ambiguous to
    # judge safely, so we don't guess -- fail open.)
    cmd = _pick(args, CMD_ARG_KEYS)
    if not cmd or not _WRITE_VERBS.search(cmd):
        return None
    root_str = str(root.resolve())
    flags = re.IGNORECASE if os.name == "nt" else 0
    # Normalize forward slashes to the OS separator on Windows so a POSIX-style
    # path in a shell command still matches; keep original case for the match.
    cmd_norm = cmd.replace("/", os.sep) if os.name == "nt" else cmd
    for m in re.finditer(re.escape(root_str) + r"[\\/][^\s\"']+", cmd_norm, flags):
        if path_is_personal_state_in_harness(m.group(0), root):
            return {"permissionDecision": "deny",
                    "permissionDecisionReason": _deny_reason(m.group(0), harness)}
    return None


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        decision = decide(payload)
        if decision:
            sys.stdout.write(json.dumps(decision))
    except Exception:
        # Fail OPEN: never deny on a guard error (preToolUse is fail-closed on a
        # non-zero exit, so we must exit 0 and not emit a deny).
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
