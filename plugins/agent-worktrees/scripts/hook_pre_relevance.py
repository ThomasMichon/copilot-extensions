"""Conservative, filesystem-free pre-tool relevance using the guards' predicates."""

from __future__ import annotations

import re


_READ_TOOLS = frozenset({"view", "rg", "glob"})
_SAFE_READ_SHELL = re.compile(
    r"[ \t]*(?:Get-Date|Get-Item|Get-ChildItem|Get-Content|Get-Location|pwd|ls|cat)"
    r"(?:[ \t]+[A-Za-z0-9_./:\\?*=-]+)*[ \t]*",
    re.IGNORECASE,
)
_GUARDS = (
    "statelessness_guard.py", "cross_repo_guard.py",
    "anchor_write_guard.py", "pr_supersede_guard.py",
)


def is_irrelevant(payload: dict, contextual: bool, load_guard) -> bool:
    # Cell admission and uncertain input retain the original policy path.
    if contextual or not isinstance(payload, dict):
        return False
    tool = str(payload.get("toolName") or payload.get("tool_name") or "")
    if tool not in _READ_TOOLS and tool not in {
        "bash", "sh", "shell", "powershell", "pwsh", "cmd", "run",
        "run_command", "execute", "exec", "terminal",
    }:
        return False
    command = None
    for name in _GUARDS:
        guard = load_guard(name)
        if guard is None or not hasattr(guard, "SHELL_TOOLS"):
            return False
        if tool in getattr(guard, "WRITE_TOOLS", ()):
            return False
        if tool not in guard.SHELL_TOOLS:
            continue
        args = guard._as_args(payload.get("toolArgs") if "toolArgs" in payload
                              else payload.get("tool_input"))
        command = guard._pick(args, guard.CMD_ARG_KEYS)
        if not command:
            return False
        write_verbs = getattr(guard, "_WRITE_VERBS", None)
        if write_verbs is not None and write_verbs.search(command):
            return False
        close_attempts = getattr(guard, "_find_close_attempts", None)
        if close_attempts is not None and close_attempts(command):
            return False
    # No quoted text, substitutions, separators, redirection, or arbitrary programs.
    return tool in _READ_TOOLS or bool(_SAFE_READ_SHELL.fullmatch(command or ""))
