#!/usr/bin/env python3
"""Anchor-write guard -- a Copilot CLI ``preToolUse`` command hook.

Blocks an agent from writing directly into the **anchor** (the main checkout) of
a **worktree-class** repo. Such a repo is only ever edited through an
agent-worktrees *linked worktree* -- committing/editing in the anchor is a latent
hazard (a stray anchor commit, a dirty anchor that blocks pulls, work that never
lands through the PR flow). Reading the anchor is fine; **writing** is denied with
a nudge to create/use a worktree, plus the break-glass escape hatch.

It is the third member of the write-routing guard family, complementing:
  * ``statelessness_guard`` -- blocks personal-state writes INTO a stateless
    harness (route to the bound knowledge repo).
  * ``cross_repo_guard`` -- blocks writes INTO an agent-guarded *related* repo
    (delegate to that repo's own in-repo agent).
  * ``anchor_write_guard`` (this one) -- blocks writes INTO the *current* repo's
    anchor when that repo is worktree-class (edit a worktree instead).

Wiring: agent-worktrees' ``hooks.json`` declares a ``preToolUse`` command hook
that runs this script (deployed to ``~/.agent-worktrees/bin/``). The hook payload
arrives as JSON on **stdin**; the decision is written as JSON to **stdout**:

    {"permissionDecision": "deny", "permissionDecisionReason": "<nudge>"}

Anything else (empty / an allow) lets the tool proceed.

**How anchor vs. worktree is told apart (robustly).** For a write target, the
guard finds the nearest enclosing checkout (walks up to ``.git``). A *linked
worktree* has ``.git`` as a **file** (a gitdir pointer) -- those always pass. The
*main checkout* has ``.git`` as a **directory**; if that directory is a repo
registered ``class: worktree`` in ``~/.agent-worktrees/repos.yaml``, the write is
an anchor edit and is denied. Singleton / reference repos (``.git`` dir but not
worktree-class -- e.g. SPO.Core) are never blocked.

**Fail-open by construction.** ``preToolUse`` command hooks are fail-closed on a
non-zero exit (a crash would DENY every tool). So this script wraps everything
and, on ANY error or ambiguity, emits nothing and exits 0. It only denies on a
clear write into a confirmed worktree-class anchor. It is cheap: one small
``repos.yaml`` read (stdlib parse; no subprocess) plus a couple of ``.git``
stats.

Escape hatches / modes:
  * ``ANCHOR_WRITE_GUARD=off`` (or 0/false/no) disables it entirely.
  * ``CROSS_REPO_GUARD=off`` is honored as a shared master kill switch for the
    write-routing guard family.
  * ``ANCHOR_WRITE_GUARD_MODE=deny|ask|warn|off`` (default ``deny``) picks the
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

# --- Tool classification (mirrors the sibling guards) -------------------------
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
# alongside an anchor-path literal in a shell command flips a read into a
# suspected write. Mirrors cross_repo_guard.
_WRITE_VERBS = re.compile(
    "|".join([
        "Set-Content", "Add-Content", "Out-File", "New-Item", "Remove-Item",
        "Move-Item", "Copy-Item", "Clear-Content", "Rename-Item",
        "Set-ItemProperty", "Tee-Object",
        ">>?",
        r"\btee\b", r"\bsed\b\s+-i", r"\bcp\b", r"\bmv\b", r"\brm\b",
        r"\btouch\b", r"\bmkdir\b", r"\bdd\b", r"\btruncate\b", r"\bpatch\b",
        r"git\s+(?:-C\s+\S+\s+)?(?:apply|commit|checkout|switch|reset|"
        r"restore|clean|rm|mv|stash|merge|rebase|pull|cherry-pick|revert|"
        r"add|init)",
    ]),
    re.IGNORECASE,
)

_IS_WIN = os.name == "nt"

# A git mutation whose target repo is the shell's CWD (no ``-C <path>`` redirect).
# Such a command run FROM a worktree-class anchor mutates the anchor WITHOUT ever
# naming its path, so the literal-path scan can't see it -- the cwd-based check
# below catches it. A ``git -C <path>`` command names its target explicitly and
# is left to the literal-path scan (so ``git -C <worktree>`` from the anchor cwd
# still correctly targets the worktree, not the anchor).
_GIT_CWD_WRITE = re.compile(
    r"\bgit\s+(?!-C\b)(?:[a-z-]+\s+)*?"
    r"(?:add|commit|apply|checkout|switch|reset|restore|clean|rm|mv|stash|"
    r"merge|rebase|pull|cherry-pick|revert|init)\b",
    re.IGNORECASE,
)
_GIT_DASH_C = re.compile(r"\bgit\s+-C\b", re.IGNORECASE)


def _truthy_off(v: str | None) -> bool:
    return (v or "").strip().lower() in {"off", "0", "false", "no"}


def _mode(env) -> str:
    m = (env.get("ANCHOR_WRITE_GUARD_MODE") or "").strip().lower()
    return m if m in {"deny", "ask", "warn", "off"} else "deny"


# --- Path helpers -------------------------------------------------------------

def _canon(p: str) -> str:
    """Absolute, normalized, case-folded on Windows -- for containment tests."""
    try:
        n = os.path.normpath(os.path.abspath(p))
    except (OSError, ValueError):
        return ""
    return os.path.normcase(n)


def find_repo_root(start: str) -> Path | None:
    """Nearest ancestor of ``start`` containing a ``.git`` (file or dir)."""
    try:
        here = Path(start).resolve()
    except (OSError, ValueError):
        return None
    for d in (here, *here.parents):
        if (d / ".git").exists():
            return d
    return None


def is_linked_worktree(root: Path) -> bool:
    """True if ``root``'s ``.git`` is a FILE -- a linked worktree (gitdir pointer),
    as opposed to a main checkout whose ``.git`` is a directory."""
    try:
        return (root / ".git").is_file()
    except OSError:
        return False


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

def active_break_glass(repo_name: str, home: Path) -> bool:
    """Honor a persisted ``allow-edits`` grant for the repo (epoch-ms)."""
    if not repo_name:
        return False
    try:
        data = json.loads(
            (home / ".agent-worktrees" / "allow-edits.json").read_text("utf-8"))
        g = (data.get("grants") or {}).get(repo_name)
        return bool(g) and float(g.get("expires_at_ms", 0)) > time.time() * 1000
    except (OSError, ValueError, TypeError, KeyError):
        return False


# --- Worktree-class anchor discovery (repos.yaml, stdlib parse) ---------------

def _repos_yaml(home: Path) -> Path:
    return home / ".agent-worktrees" / "repos.yaml"


def _yaml_unquote(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        quote = val[0]
        val = val[1:-1]
        if quote == '"':
            # Double-quoted YAML: an escaped backslash ``\\`` is a single one
            # (Windows paths are stored this way, e.g. "C:\\Data\\Src\\repo").
            val = val.replace("\\\\", "\\")
    return val


def load_worktree_anchors(home: Path) -> list[dict]:
    """Parse ``repos.yaml`` -> ``[{name, path}]`` for every ``class: worktree``
    repo, across all platform path keys (windows/wsl/linux).

    Stdlib-only (this hook runs under whatever ``python`` is on PATH, so PyYAML
    may be unavailable); a tiny indentation-aware parser tailored to the known,
    regular ``repos:`` shape. Prefers PyYAML when importable. Never raises.
    """
    try:
        text = _repos_yaml(home).read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    # Fast path: real YAML when the runtime happens to have it.
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
        anchors: list[dict] = []
        repos = (data or {}).get("repos") or {}
        if isinstance(repos, dict):
            for name, meta in repos.items():
                if not isinstance(meta, dict) or meta.get("class") != "worktree":
                    continue
                for plat in ("windows", "wsl", "linux"):
                    p = meta.get(plat)
                    if isinstance(p, str) and p.strip():
                        anchors.append({"name": str(name), "path": p.strip()})
        return anchors
    except Exception:
        pass  # fall through to the stdlib mini-parser

    anchors = []
    in_repos = False
    cur_name: str | None = None
    cur_class: str | None = None
    cur_paths: list[str] = []

    def flush() -> None:
        if cur_name and cur_class == "worktree":
            for p in cur_paths:
                anchors.append({"name": cur_name, "path": p})

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indent == 0:
            flush()
            cur_name, cur_class, cur_paths = None, None, []
            in_repos = stripped.rstrip() == "repos:"
            continue
        if not in_repos:
            continue
        if indent == 2 and stripped.endswith(":"):
            flush()
            cur_name = _yaml_unquote(stripped[:-1])
            cur_class, cur_paths = None, []
            continue
        if indent >= 4 and cur_name:
            key, sep, val = stripped.partition(":")
            if not sep:
                continue
            key = key.strip()
            if key == "class":
                cur_class = _yaml_unquote(val)
            elif key in ("windows", "wsl", "linux"):
                v = _yaml_unquote(val)
                if v:
                    cur_paths.append(v)
    flush()
    return anchors


# --- Delegation nudge ---------------------------------------------------------

def _deny_reason(name: str, path: str) -> str:
    return (
        f"anchor-write-guard: '{name}' is a worktree-class repo, and {path} is "
        f"its ANCHOR (main) checkout -- do NOT edit it in place. A stray anchor "
        f"edit/commit is a latent hazard (dirty anchor blocks pulls; work that "
        f"never lands through the PR flow). Create/use a linked worktree and edit "
        f"THERE: `{name} create --json` (or `agent-worktrees create`), then work "
        f"in the returned path. Reading the anchor is fine. If a direct anchor "
        f"edit is genuinely unavoidable (a recovery/bootstrap action), break "
        f"glass: `agent-worktrees repos allow-edits {name} --reason \"<why>\"` "
        f"(logged, time-boxed), then retry. (Disable: ANCHOR_WRITE_GUARD=off.)"
    )


# --- Core evaluation ----------------------------------------------------------

def evaluate(tool: str, args: dict, cwd: str, anchors: list[dict]) -> dict | None:
    """Return the anchor hit (with a ``reason``), or None to allow."""
    if not anchors:
        return None
    canon_anchor = {_canon(a["path"]): a for a in anchors if a.get("path")}

    if tool in WRITE_TOOLS:
        raw = _pick(args, PATH_ARG_KEYS)
        if not raw:
            return None
        abs_path = _resolve(raw, cwd)
        root = find_repo_root(abs_path)
        if root is None or is_linked_worktree(root):
            return None  # not a checkout, or a linked worktree -> always fine
        a = canon_anchor.get(_canon(str(root)))
        if a is not None:
            return {**a, "reason": _deny_reason(a["name"], a["path"])}
        return None

    if tool in SHELL_TOOLS:
        cmd = _pick(args, CMD_ARG_KEYS)
        if not cmd or not _WRITE_VERBS.search(cmd):
            return None  # pure reads into an anchor are allowed
        # (a) cwd-based: a repo-scoped git mutation (e.g. ``git commit``,
        # ``git add``) run WITH cwd inside a worktree-class anchor mutates it
        # without ever naming the path -- the literal scan below can't see that.
        # Gated to CWD-scoped git verbs (no ``-C`` redirect) to stay precise:
        # a linked worktree cwd (.git file) is exempt, and ``git -C <path>`` is
        # left to the literal scan.
        if _GIT_CWD_WRITE.search(cmd) and not _GIT_DASH_C.search(cmd):
            root = find_repo_root(cwd)
            if root is not None and not is_linked_worktree(root):
                a = canon_anchor.get(_canon(str(root)))
                if a is not None:
                    return {**a, "reason": _deny_reason(a["name"], a["path"])}
        # (b) literal-path: a command that writes into an anchor by (absolute)
        # path, regardless of cwd (e.g. ``git -C "<anchor>" commit`` or
        # ``Set-Content "<anchor>\x"`` from elsewhere).
        cmd_norm = (cmd.replace("/", os.sep) if _IS_WIN else cmd)
        hay = os.path.normcase(cmd_norm) if _IS_WIN else cmd_norm
        flags = re.IGNORECASE if _IS_WIN else 0
        for a in anchors:
            gp = a.get("path")
            if not gp:
                continue
            root_str = _canon(gp)
            # Match the anchor as a whole path token: the root itself (e.g.
            # ``git -C "<anchor>" commit``) OR a path INTO it
            # (``<anchor><sep><subpath>``), but require a terminator (quote /
            # whitespace / end) right after so a sibling worktree dir that only
            # shares the anchor's string prefix (``<repo>.worktrees\...``) is
            # NOT matched.
            pat = re.escape(root_str) + r"(?:[\\/][^\s\"']*)?(?=[\"'\s]|$)"
            if re.search(pat, hay, flags):
                return {**a, "reason": _deny_reason(a["name"], a["path"])}
        return None

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
           anchors=None) -> dict | None:
    """Return a hook-output decision dict, or None to allow. Pure/injectable."""
    env = env if env is not None else os.environ
    home = Path(home) if home is not None else Path.home()

    if _truthy_off(env.get("ANCHOR_WRITE_GUARD")):
        return None
    if _truthy_off(env.get("CROSS_REPO_GUARD")):  # shared master kill switch
        return None
    mode = _mode(env)
    if mode == "off":
        return None

    tool = str(payload.get("toolName") or payload.get("tool_name") or "")
    if tool not in WRITE_TOOLS and tool not in SHELL_TOOLS:
        return None  # reads / everything else: fast allow

    cwd = str(payload.get("cwd") or "")
    if anchors is None:
        anchors = load_worktree_anchors(home)
    if not anchors:
        return None

    args = _as_args(payload.get("toolArgs") if "toolArgs" in payload
                    else payload.get("tool_input"))
    hit = evaluate(tool, args, cwd, anchors)
    if hit is None:
        return None
    # A live break-glass grant for the anchor repo lets the write through.
    if active_break_glass(hit.get("name", ""), home):
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
