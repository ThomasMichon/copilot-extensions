#!/usr/bin/env python3
"""Cross-repo write-routing guard -- a Copilot CLI ``preToolUse`` command hook.

Blocks an agent from writing directly into an **agent-guarded related repo**'s
checkout -- a coordinated repo whose ``related.yaml`` ``delegate`` is not
``none`` (``agent-bridge`` / ``agent-codespaces`` / ``agent-containers``). Such a
repo has its own in-repo agent with the plugins, instructions, and skills the
launching harness lacks, so content edits must be delegated to it, not made from
the harness. Reading a guarded repo is fine; **writing** is denied with a nudge
naming the right delegation channel + the break-glass escape hatch.

This is the agent-worktrees-owned successor to the harness-local
``cross-repo-guard`` extension: a stateless, shareable harness gets write-routing
protection **for free by enabling agent-worktrees**, rather than carrying a
bespoke guard in its tree. It is delivered through the **hooks system** (a
``preToolUse`` command hook wired in ``hooks.json``), NOT an SDK extension --
Copilot hooks and extensions are separate mechanisms, and only a hook can veto a
tool call. It complements the statelessness guard (which blocks personal-state
writes INTO a stateless harness); this one blocks writes INTO a delegated repo.

Wiring: agent-worktrees' ``hooks.json`` declares a ``preToolUse`` command hook
that runs this script (deployed to ``~/.agent-worktrees/bin/``). The hook payload
arrives as JSON on **stdin**; the decision is written as JSON to **stdout**:

    {"permissionDecision": "deny", "permissionDecisionReason": "<nudge>"}

Anything else (empty / an allow) lets the tool proceed.

**Fail-open by construction.** ``preToolUse`` command hooks are fail-closed on a
non-zero exit (a crash would DENY every tool). So this script wraps everything
and, on ANY error or ambiguity, emits nothing and exits 0. It only denies on a
clear write into a confirmed agent-guarded repo checkout.

Guarded roots come only from the caller-scoped map published during managed
preparation and mutation. Missing or unusable maps fail open; no event discovers
topology, refreshes the map or starts a runtime.

Escape hatches / modes:
  * ``CROSS_REPO_GUARD=off`` (or 0/false/no) disables it entirely.
  * ``CROSS_REPO_GUARD_MODE=deny|ask|warn|off`` (default ``deny``) picks the
    action on a hit.
  * ``agent-worktrees repos allow-edits <repo> --reason "..."`` opens a
    time-boxed break-glass (``~/.agent-worktrees/allow-edits.json``) the guard
    honors.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from registry_root import resolve_registry_root

try:
    import guarded_anchor_map as _guard_map
except (ImportError, OSError, SyntaxError):
    _guard_map = None

# --- Tool classification (mirrors statelessness_guard) ------------------------
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

# Write-ish verbs (PowerShell cmdlets + POSIX + git mutations); presence
# alongside a guarded-root literal in a shell command flips a read into a
# suspected write.
_WRITE_VERBS = re.compile(
    "|".join([
        "Set-Content", "Add-Content", "Out-File", "New-Item", "Remove-Item",
        "Move-Item", "Copy-Item", "Clear-Content", "Rename-Item",
        "Set-ItemProperty", "Tee-Object",
        ">>?",
        r"\btee\b", r"\bsed\b\s+-i", r"\bcp\b", r"\bmv\b", r"\brm\b",
        r"\btouch\b", r"\bmkdir\b", r"\bdd\b", r"\btruncate\b", r"\bpatch\b",
        r"""git\s+(?:-C\s+(?:"[^"]*"|'[^']*'|\S+)\s+)*(?:apply|commit|checkout|switch|reset|"""
        r"restore|clean|rm|mv|stash|merge|rebase|pull|cherry-pick|revert|"
        r"add|init)",
    ]),
    re.IGNORECASE,
)

_IS_WIN = os.name == "nt"

# --- Shell write-into-repo detection (segment + command-position based) --------
# Fire ONLY when a guarded-repo path is an actual *write target*, never when it
# merely appears in the command text (a ``$var=`` assignment, a ``cd`` argument,
# or a quoted data payload like ``--body`` prose). Fd-dup redirects (``2>&1``)
# are NOT writes. Mirrors anchor_write_guard's detection (dotfiles#1144).
_SHELL_SEP = re.compile(r"\|\||&&|[;|&\n\r]")
_WRITE_CMD_START = re.compile(
    r"""^\s*["']?(?:
        set-content|add-content|out-file|new-item|remove-item|move-item|
        copy-item|clear-content|rename-item|set-itemproperty|tee-object|
        tee|cp|mv|rm|touch|mkdir|dd|truncate|patch|sed\s+-i
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)
_GIT_START = re.compile(r"^\s*[\"']?git\b", re.IGNORECASE)
_GIT_WRITE_SUB = re.compile(
    r"\b(?:add|commit|apply|checkout|switch|reset|restore|clean|rm|mv|stash|"
    r"merge|rebase|pull|cherry-pick|revert|init)\b",
    re.IGNORECASE,
)
_GIT_DASH_C_FLAG = re.compile(r"(?:^|\s)-C\b", re.IGNORECASE)
_GIT_DASH_C_TARGET = re.compile(
    r"""(?:^|\s)-C\s+(?:"([^"]*)"|'([^']*)'|([^\s"';&|]+))""", re.IGNORECASE,
)

# Leading benign prefixes to strip so a write verb after them is still seen at
# command position (``sudo rm ...``, ``VAR=x git commit``). Wrapper flags that
# take a separate arg (``sudo -u user``) are a known best-effort gap.
_SEG_STRIP = re.compile(
    r"^\s*(?:[A-Za-z_]\w*=(?:\"[^\"]*\"|'[^']*'|\S*)"
    r"|(?:sudo|doas|env|nohup|command|builtin|exec|time|xargs))(?:\s+|$)",
    re.IGNORECASE,
)


def _effective_seg(seg: str) -> str:
    prev = None
    while seg != prev:
        prev = seg
        seg = _SEG_STRIP.sub("", seg, count=1)
    return seg


# An in-command ``cd <repo>`` moves the effective cwd for following segments, so
# ``cd <guarded>; git commit`` writes the guarded repo even when the tool cwd is
# elsewhere -- track it (mirrors anchor_write_guard).
_CD_SEG = re.compile(
    r"^\s*(?:cd|chdir|pushd|set-location|sl)\s+"
    r"(?:(?:-{1,2}\S+|/[A-Za-z])\s+)*[\"']?([^\"';|&]+)",
    re.IGNORECASE,
)


def _cd_target(eff_seg: str, base: str) -> str | None:
    m = _CD_SEG.match(eff_seg)
    if not m:
        return None
    raw = m.group(1).strip().strip("\"'").rstrip()
    if not raw or "$" in raw or "%" in raw or raw.startswith("~"):
        return None
    return raw if os.path.isabs(raw) else os.path.join(base, raw)


def _git_target(eff_seg: str, base: str) -> str | None:
    target = base
    found = False
    for match in _GIT_DASH_C_TARGET.finditer(eff_seg):
        found = True
        raw = next(value for value in match.groups() if value is not None)
        if "$" in raw or "%" in raw or raw.startswith("~"):
            return None
        if raw:
            target = raw if os.path.isabs(raw) else os.path.join(target, raw)
    return target if found else None


def _root_token(root_str: str) -> str:
    """Regex for a repo root as a whole path token: the root itself OR a path
    INTO it, with a terminator so a sibling dir sharing the string prefix is not
    matched."""
    return re.escape(root_str) + r"(?:[\\/][^\s\"';|&]*)?(?=[\"'\s;|&]|$)"


def _truthy_off(v: str | None) -> bool:
    return (v or "").strip().lower() in {"off", "0", "false", "no"}


def _mode(env) -> str:
    m = (env.get("CROSS_REPO_GUARD_MODE") or "").strip().lower()
    return m if m in {"deny", "ask", "warn", "off"} else "deny"


# --- Path helpers -------------------------------------------------------------

def _canon(p: str) -> str:
    """Absolute, normalized, case-folded on Windows -- for containment tests."""
    try:
        n = os.path.normpath(os.path.abspath(p))
    except (OSError, ValueError):
        return ""
    return os.path.normcase(n)


def is_inside(child: str, root: str) -> bool:
    c, r = _canon(child), _canon(root)
    if not c or not r:
        return False
    return c == r or c.startswith(r + os.sep)


def find_repo_root(start: str) -> Path | None:
    if _guard_map is None:
        print("cross-repo-guard: guard-map reader unavailable; fail-open.", file=sys.stderr)
        return None
    identity = _guard_map.repository_identity(start)
    return Path(identity["root"]) if identity else None


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


# --- Break-glass (persisted allow-edits.json) ---------------------------------

def active_break_glass(
    repo_name: str, home: Path, *, registry_root: Path | None = None
) -> bool:
    """Honor a persisted ``allow-edits`` grant for the repo (epoch-ms)."""
    if not repo_name:
        return False
    try:
        data = json.loads(
            ((registry_root or home / ".agent-worktrees") / "allow-edits.json").read_text("utf-8"))
        g = (data.get("grants") or {}).get(repo_name)
        return bool(g) and float(g.get("expires_at_ms", 0)) > time.time() * 1000
    except (OSError, ValueError, TypeError, KeyError):
        return False


# --- Delegation nudge ---------------------------------------------------------

def _machine_hint(g: dict) -> str:
    locus = g.get("locus") or {}
    pref = locus.get("preferred") or ""
    if pref.startswith("machine:"):
        return pref[len("machine:"):]
    machines = locus.get("machines")
    if isinstance(machines, list) and machines:
        return machines[0]
    return "<its host>"


def _channel_hint(g: dict) -> str:
    name = g.get("name", "")
    resolve = f"`agent-worktrees related resolve {name}`"
    delegate = g.get("delegate")
    if delegate == "agent-codespaces":
        return (f"work it in its CodeSpace via agent-codespaces (run {resolve} "
                f"for the exact agent-codespaces ssh/dispatch command)")
    if delegate == "agent-containers":
        return (f"work it in its dev-container fleet via agent-containers "
                f"(run {resolve} for the exact command)")
    return (f"dispatch to its in-repo agent via agent-bridge -- "
            f"`agent-bridge send {_machine_hint(g)} \"<task>\"` "
            f"(run {resolve} for the exact target)")


def _deny_reason(g: dict, detail: str) -> str:
    name, delegate, path = g.get("name", ""), g.get("delegate", ""), g.get("path", "")
    return (
        f"cross-repo-guard: '{name}' is an agent-guarded repo (delegate: "
        f"{delegate}). Do NOT edit its checkout ({path}) directly from the "
        f"harness -- its in-repo agent has the plugins, instructions, and skills "
        f"the harness lacks. {detail} Instead, {_channel_hint(g)}. Also honor "
        f"that repo's own AGENTS.md/CLAUDE.md. If a direct edit is genuinely "
        f"unavoidable (maintaining the target agent's OWN instructions/skills, or "
        f"a direct action to unblock), break glass: `agent-worktrees repos "
        f"allow-edits {name} --reason \"<why>\"` (logged, time-boxed), then "
        f"retry. (Operator override: CROSS_REPO_GUARD=off / "
        f"CROSS_REPO_GUARD_MODE=warn.)"
    )


def load_guarded_roots(
    root: str, home: Path, deadline: float | None = None,
    *, registry_root: Path | None = None,
) -> list[dict]:
    """Read prepared facts only; ``deadline`` remains a compatibility argument."""
    if _guard_map is None:
        return []
    result = _guard_map.read_map(root, registry_root or home / ".agent-worktrees")
    if result.status != "ready":
        print(
            f"cross-repo-guard: {result.status} guard map; fail-open. "
            "Run agent-worktrees refresh-guards or prepare this checkout.",
            file=sys.stderr,
        )
    return result.roots


# --- Core evaluation ----------------------------------------------------------

def _shell_hit(cmd: str, cwd: str, guarded: list[dict]) -> dict | None:
    """Return a guarded-repo hit for a shell command that WRITES into a guarded
    repo, or None. Fires only on a real write target (redirect target /
    command-position write verb argument / git mutation via ``-C <repo>`` or the
    cwd), never a path merely mentioned in an assignment, ``cd``, or a quoted
    data payload (dotfiles#1144)."""
    if not _WRITE_VERBS.search(cmd):
        return None
    eff_cwd = cwd
    identity_cache = {}
    for seg in _SHELL_SEP.split(cmd):
        seg_hay = os.path.normcase(seg.replace("/", os.sep)) if _IS_WIN else seg
        eff = _effective_seg(seg)
        at_write_cmd = bool(_WRITE_CMD_START.match(eff))
        is_git = bool(_GIT_START.match(eff))
        git_write = is_git and bool(_GIT_WRITE_SUB.search(seg))
        has_dash_c = is_git and bool(_GIT_DASH_C_FLAG.search(seg))
        for g in guarded:
            gp = g.get("path")
            if not gp:
                continue
            reason = _deny_reason(g, "(a shell command writes into it).")
            identity = g.get("identity") or {}
            for root in {gp, identity.get("root", gp), g.get("registered_path", gp)}:
                tok = _root_token(_canon(root))
                # 1. A file redirect whose target is the repo (excludes fd dups).
                if re.search(r">>?(?!\s*&)\s*[\"']?" + tok, seg_hay):
                    return {**g, "reason": reason}
                # 2. A write cmdlet/verb at COMMAND POSITION with the repo as an arg.
                if at_write_cmd and re.search(tok, seg_hay):
                    return {**g, "reason": reason}
        git_cwd = _git_target(eff, eff_cwd) if has_dash_c else eff_cwd
        if git_write and git_cwd:
            for g in guarded:
                gp = g.get("path")
                if gp and _guard_map.matches(git_cwd, g, identity_cache=identity_cache):
                    return {**g, "reason": _deny_reason(
                        g, "(a shell command writes into it).")}
        # Follow an in-command ``cd`` to a resolvable path for later segments.
        nd = _cd_target(eff, eff_cwd)
        if nd:
            eff_cwd = nd
    return None


def evaluate(tool: str, args: dict, cwd: str, guarded: list[dict]) -> dict | None:
    """Return the guarded repo hit (with a ``reason``), or None to allow."""
    if not guarded or _guard_map is None:
        return None
    if tool in WRITE_TOOLS:
        raw = _pick(args, PATH_ARG_KEYS)
        if not raw:
            return None
        abs_path = _resolve(raw, cwd)
        identity_cache = {}
        for g in guarded:
            if g.get("path") and _guard_map.matches(abs_path, g, identity_cache=identity_cache):
                return {**g, "reason": _deny_reason(
                    g, f"('{tool}' targeting {raw}).")}
        return None
    if tool in SHELL_TOOLS:
        cmd = _pick(args, CMD_ARG_KEYS)
        if not cmd:
            return None
        return _shell_hit(cmd, cwd, guarded)
    return None


def _hit_to_output(hit: dict, mode: str) -> dict | None:
    if not hit or mode == "off":
        return None
    if mode == "warn":
        return {"additionalContext": hit["reason"]}
    if mode == "ask":
        return {"permissionDecision": "ask",
                "permissionDecisionReason": hit["reason"]}
    return {"permissionDecision": "deny",
            "permissionDecisionReason": hit["reason"]}


def decide(payload: dict, *, env=None, home=None,
           guarded_roots=None, deadline: float | None = None) -> dict | None:
    """Return a hook-output decision dict, or None to allow. Pure/injectable."""
    env = env if env is not None else os.environ
    home = Path(home) if home is not None else Path.home()

    if _truthy_off(env.get("CROSS_REPO_GUARD")):
        return None
    mode = _mode(env)
    if mode == "off":
        return None

    tool = str(payload.get("toolName") or payload.get("tool_name") or "")
    if tool not in WRITE_TOOLS and tool not in SHELL_TOOLS:
        return None  # reads / everything else: fast allow

    args = _as_args(payload.get("toolArgs") if "toolArgs" in payload
                    else payload.get("tool_input"))
    if tool in SHELL_TOOLS:
        cmd = _pick(args, CMD_ARG_KEYS)
        if not cmd or not _WRITE_VERBS.search(cmd):
            return None  # identical to _shell_hit's topology-independent allow

    override = env.get("AGENT_HOME", "").strip()
    legacy_home = Path(os.path.abspath(override)) if override else home
    selected_root = resolve_registry_root(
        legacy_root=legacy_home / ".agent-worktrees", environment=env,
    )
    cwd = str(payload.get("cwd") or "")
    if guarded_roots is None:
        root = find_repo_root(cwd)
        if root is None:
            return None
        guarded = load_guarded_roots(str(root), home, deadline, registry_root=selected_root)
    else:
        guarded = guarded_roots
    if not guarded:
        return None

    hit = evaluate(tool, args, cwd, guarded)
    if hit is None:
        return None
    # A live break-glass grant for the target repo lets the write through.
    if active_break_glass(hit.get("name", ""), home, registry_root=selected_root):
        return None
    return _hit_to_output(hit, mode)


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
