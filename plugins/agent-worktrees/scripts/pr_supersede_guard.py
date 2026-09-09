#!/usr/bin/env python3
"""PR-supersede guard -- a Copilot CLI ``preToolUse`` command hook.

Blocks an agent from closing another author's open pull request via the ``gh``
CLI -- the exact action the agent-dispatch reviewer vision's non-goals forbid
in prose ("never closes, supersedes, or replaces another author's open pull
request with a competing PR under its own identity"), but which prose alone
failed to prevent once (see
``efforts/active/review-automation-reliability`` Phase 6 and
``efforts/active/declarative-dispatch-engine-generalization`` Phase 2's
structural-guard open question). This hook makes that rule a structural
guard the acting identity cannot talk itself out of, independent of whatever
declaration/prompt prose a given repository-issue-loop or reviewer-loop
worker was seeded with.

It is the fourth member of the write-routing guard family, complementing:
  * ``statelessness_guard`` -- blocks personal-state writes INTO a stateless
    harness.
  * ``cross_repo_guard`` -- blocks writes INTO an agent-guarded related repo.
  * ``anchor_write_guard`` -- blocks writes INTO a worktree-class repo's anchor.
  * ``pr_supersede_guard`` (this one) -- blocks CLOSING another author's open
    pull request.

Wiring: agent-worktrees' ``hooks.json`` declares a ``preToolUse`` command hook
that runs ``hook_client.py``, which loads and runs this module's ``decide()``
alongside its siblings. The hook payload arrives as JSON on **stdin**; the
decision is written as JSON to **stdout**:

    {"permissionDecision": "deny", "permissionDecisionReason": "<nudge>"}

Anything else (empty / an allow) lets the tool proceed.

**Scope, deliberately narrow.** Only ``gh pr close <n>`` (and the equivalent
``gh api .../pulls/<n>`` PATCH-to-closed) is blocked. Merging another
contributor's already-approved PR is ordinary, legitimate maintainer behavior
and is NOT blocked -- the forbidden pattern is specifically closing someone
else's open PR (typically to replace it with a competing one), not merging
it. Only a PR that is currently OPEN and authored by someone other than the
currently gh-authenticated identity trips the guard; closing your own PR, or
a PR already closed/merged, always passes.

**Fail-open by construction.** ``preToolUse`` command hooks are fail-closed on
a non-zero exit (a crash would DENY every tool). So this script wraps
everything and, on ANY error or ambiguity -- ``gh`` unavailable, not
authenticated, network/API failure, unparseable command -- emits nothing and
exits 0. It only denies on a clear, confirmed close of a confirmed
other-author, confirmed-open PR.

Escape hatches / modes:
  * ``PR_SUPERSEDE_GUARD=off`` (or 0/false/no) disables it entirely.
  * ``PR_SUPERSEDE_GUARD_MODE=deny|ask|warn|off`` (default ``deny``) picks the
    action on a hit.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# --- Tool classification (mirrors the sibling guards) -------------------------
SHELL_TOOLS = frozenset({
    "bash", "sh", "shell", "powershell", "pwsh", "cmd", "run", "run_command",
    "execute", "exec", "terminal",
})
CMD_ARG_KEYS = ("command", "cmd", "script", "commandLine", "commandline", "input")

_IS_WIN = os.name == "nt"
_GH_TIMEOUT_S = 15.0

# ``gh pr close <number> [--repo <owner>/<repo>] [...]``. The number must be a
# standalone token immediately after ``close`` (not a URL) -- ``gh`` also
# accepts a PR URL or branch name in that position, which is out of scope for
# this narrow, deliberately conservative first cut (fail open on that shape).
_GH_PR_CLOSE = re.compile(
    r"\bgh\s+pr\s+close\s+(?P<number>\d+)\b(?P<rest>[^;|&\n\r]*)",
    re.IGNORECASE,
)
_REPO_FLAG = re.compile(
    r"--repo(?:=|\s+)[\"']?(?P<repo>[\w.-]+/[\w.-]+)[\"']?",
    re.IGNORECASE,
)

# The REST-API equivalent: ``gh api repos/<owner>/<repo>/pulls/<number> ...
# -f state=closed`` (or ``-X PATCH`` with a JSON body containing
# ``"state": "closed"`` / ``"state":"closed"``).
_GH_API_PULL = re.compile(
    r"\bgh\s+api\s+(?:[\w.-]+\.com)?/?repos/(?P<repo>[\w.-]+/[\w.-]+)/pulls/"
    r"(?P<number>\d+)\b(?P<rest>[^;|&\n\r]*)",
    re.IGNORECASE,
)
_STATE_CLOSED = re.compile(
    r"""(?:-f\s+["']?state=closed["']?|["']state["']\s*:\s*["']closed["'])""",
    re.IGNORECASE,
)


def _truthy_off(v: str | None) -> bool:
    return (v or "").strip().lower() in {"off", "0", "false", "no"}


def _mode(env) -> str:
    m = (env.get("PR_SUPERSEDE_GUARD_MODE") or "").strip().lower()
    return m if m in {"deny", "ask", "warn", "off"} else "deny"


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


def _find_close_attempts(cmd: str) -> list[dict]:
    """Every ``gh pr close``/API-equivalent invocation found in ``cmd``.

    Each result carries ``number`` and an optional ``repo`` (``None`` when the
    command relies on ambient ``gh`` repo resolution, e.g. run from within a
    checkout -- the caller resolves that case via ``gh repo view``).
    """
    attempts = []
    for m in _GH_PR_CLOSE.finditer(cmd):
        repo_m = _REPO_FLAG.search(m.group("rest"))
        attempts.append({
            "number": int(m.group("number")),
            "repo": repo_m.group("repo") if repo_m else None,
        })
    for m in _GH_API_PULL.finditer(cmd):
        if not _STATE_CLOSED.search(m.group("rest")):
            continue
        attempts.append({
            "number": int(m.group("number")),
            "repo": m.group("repo"),
        })
    return attempts


def _run_gh(args: list[str], cwd: str, deadline: float | None) -> str | None:
    timeout = _GH_TIMEOUT_S
    if deadline is not None:
        timeout = max(0.1, min(timeout, deadline - time.monotonic()))
        if timeout <= 0:
            return None
    try:
        proc = subprocess.run(
            ["gh", *args], cwd=cwd or None,
            capture_output=True, text=True, timeout=timeout,
            creationflags=(0x08000000 if _IS_WIN else 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _default_current_login(cwd: str, deadline: float | None) -> str | None:
    out = _run_gh(["api", "user", "--jq", ".login"], cwd, deadline)
    return out.strip() if out else None


def _default_pr_lookup(
    repo: str | None, number: int, cwd: str, deadline: float | None
) -> dict | None:
    args = ["pr", "view", str(number), "--json", "author,state"]
    if repo:
        args += ["--repo", repo]
    out = _run_gh(args, cwd, deadline)
    if not out:
        return None
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return None
    author = data.get("author") or {}
    login = author.get("login") if isinstance(author, dict) else None
    state = data.get("state")
    if not isinstance(login, str) or not isinstance(state, str):
        return None
    return {"login": login, "state": state}


def _deny_reason(number: int, repo: str | None, author: str) -> str:
    where = f" in {repo}" if repo else ""
    return (
        f"Blocked: closing PR #{number}{where} would close another "
        f"contributor's ({author}) open pull request. Per the agent-dispatch "
        "reviewer vision's non-goals, never close, supersede, or replace "
        "another author's open PR -- even when their branch cannot be "
        "updated. Instead: leave constructive review feedback on the "
        "existing PR, then record the dependency (e.g. the "
        "`blocked-on-external-pr` label + a comment naming the blocking PR "
        "URL and head SHA) for a maintainer to reconcile."
    )


def _hit_to_output(reason: str, mode: str) -> dict | None:
    if not reason or mode == "off":
        return None
    if mode == "warn":
        return {"additionalContext": reason}
    if mode == "ask":
        return {"permissionDecision": "ask", "permissionDecisionReason": reason}
    return {"permissionDecision": "deny", "permissionDecisionReason": reason}


def decide(
    payload: dict, *, env=None, home=None,
    current_login=None, pr_lookup=None, deadline: float | None = None,
) -> dict | None:
    """Return a hook-output decision dict, or None to allow. Pure/injectable.

    ``current_login`` and ``pr_lookup`` are injectable callables (tests supply
    fakes to avoid real ``gh`` subprocess calls):
        current_login(cwd, deadline) -> str | None
        pr_lookup(repo, number, cwd, deadline) -> {"login": str, "state": str} | None
    """
    env = env if env is not None else os.environ
    home = Path(home) if home is not None else Path.home()  # noqa: F841 (parity w/ siblings)

    if _truthy_off(env.get("PR_SUPERSEDE_GUARD")):
        return None
    mode = _mode(env)
    if mode == "off":
        return None

    tool = str(payload.get("toolName") or payload.get("tool_name") or "")
    if tool not in SHELL_TOOLS:
        return None  # only shell-invoked `gh` is in scope

    args = _as_args(payload.get("toolArgs") if "toolArgs" in payload
                    else payload.get("tool_input"))
    cmd = _pick(args, CMD_ARG_KEYS)
    if not cmd or "close" not in cmd.lower():
        return None  # cheap pre-filter before the heavier regex/subprocess path

    attempts = _find_close_attempts(cmd)
    if not attempts:
        return None

    cwd = str(payload.get("cwd") or "")
    lookup = pr_lookup if pr_lookup is not None else (
        lambda repo, number, cwd_, dl: _default_pr_lookup(repo, number, cwd_, dl)
    )
    whoami = current_login if current_login is not None else (
        lambda cwd_, dl: _default_current_login(cwd_, dl)
    )

    for attempt in attempts:
        pr = lookup(attempt["repo"], attempt["number"], cwd, deadline)
        if pr is None or pr.get("state") != "OPEN":
            continue  # unresolvable or already settled: nothing to protect
        author = pr.get("login")
        if not author:
            continue
        me = whoami(cwd, deadline)
        if not me or me.lower() == author.lower():
            continue  # closing your own PR is fine
        reason = _deny_reason(attempt["number"], attempt["repo"], author)
        output = _hit_to_output(reason, mode)
        if output:
            return output
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
