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

Guarded-root discovery is delegated to ``agent-worktrees related`` (which already
merges the knowledge-repo config overlay + resolves local checkout paths) and
**cached** to a short-TTL sidecar so at most the first write/shell tool call per
window pays the subprocess cost.

Escape hatches / modes:
  * ``CROSS_REPO_GUARD=off`` (or 0/false/no) disables it entirely.
  * ``CROSS_REPO_GUARD_MODE=deny|ask|warn|off`` (default ``deny``) picks the
    action on a hit.
  * ``agent-worktrees repos allow-edits <repo> --reason "..."`` opens a
    time-boxed break-glass (``~/.agent-worktrees/allow-edits.json``) the guard
    honors.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

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
        r"git\s+(?:-C\s+\S+\s+)?(?:apply|commit|checkout|switch|reset|"
        r"restore|clean|rm|mv|stash|merge|rebase|pull|cherry-pick|revert|"
        r"add|init)",
    ]),
    re.IGNORECASE,
)

_CACHE_TTL_S = 300.0
_IS_WIN = os.name == "nt"


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
    try:
        here = Path(start).resolve()
    except (OSError, ValueError):
        return None
    for d in (here, *here.parents):
        if (d / ".git").exists():
            return d
    return None


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


# --- Guarded-root discovery (via the related CLI, cached) ---------------------

def _cache_path(root: str, home: Path) -> Path:
    h = hashlib.sha256(_canon(root).encode("utf-8", "replace")).hexdigest()[:16]
    return home / ".agent-worktrees" / "cache" / f"guarded-roots.{h}.json"


def _read_cache(path: Path) -> list[dict] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if time.time() - float(data.get("computed_at", 0)) > _CACHE_TTL_S:
        return None
    roots = data.get("roots")
    return roots if isinstance(roots, list) else None


def _write_cache(path: Path, roots: list[dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"computed_at": time.time(), "roots": roots}),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a cache-write failure just means we recompute next time


def _slot_python(slot: Path) -> Path:
    """The runtime python inside a ``versions/<ver>`` slot, per platform."""
    if os.name == "nt":
        return slot / "Scripts" / "python.exe"
    return slot / "bin" / "python"


def _strip_nt_prefix(target: str) -> str:
    """Strip a leading NT object-namespace prefix from a junction/symlink target.

    A junction created by PowerShell ``New-Item -ItemType Junction`` reports its
    target as ``\\??\\C:\\...`` (and some APIs as ``\\\\?\\C:\\...``); such a path
    is not usable as a normal filesystem path. ``mklink /J`` yields a clean one.
    """
    for pfx in ("\\??\\", "\\\\?\\"):
        if target.startswith(pfx):
            return target[len(pfx):]
    return target


def _runtime_argv(root: Path | None = None) -> list[str] | None:
    """An argv prefix that runs the agent-worktrees CLI, WITHOUT the PATH binstubs.

    The binstubs are fragile for a subprocess (#1089): ``shutil.which`` prefers
    the ``.cmd`` over the robust ``.ps1`` on Windows, and that ``.cmd`` parses the
    ``~/.agent-worktrees/.venv`` junction target and breaks (WinError 3) on a
    ``\\??\\``-prefixed target -- which silently disabled this guard. Resolve the
    runtime slot python directly instead, matching the binstubs' own authoritative
    ``current-version`` marker model (junction-free; dotfiles #581/#1085).

    Order: ``current-version`` marker -> ``versions/<ver>`` slot python; else the
    newest ``versions/*`` slot; else the legacy ``.venv`` reparse target (read,
    never traversed -- dotfiles #637 -- with any ``\\??\\``/``\\\\?\\`` prefix
    stripped); else a PATH binstub as a last-resort belt.
    """
    root = root or (Path(os.path.expanduser("~")) / ".agent-worktrees")

    # 1. current-version marker (authoritative; junction-free).
    try:
        ver = (root / "current-version").read_text("utf-8").strip()
    except OSError:
        ver = ""
    if ver:
        p = _slot_python(root / "versions" / ver)
        if p.exists():
            return [str(p), "-m", "agent_worktrees"]

    # 2. newest versions/* slot that has a python.
    try:
        slots = sorted((root / "versions").iterdir())
    except OSError:
        slots = []
    for slot in reversed(slots):
        p = _slot_python(slot)
        if p.exists():
            return [str(p), "-m", "agent_worktrees"]

    # 3. legacy .venv reparse target (read the link, never traverse it).
    try:
        target = _strip_nt_prefix(os.readlink(str(root / ".venv")))
    except OSError:
        target = ""
    if target:
        p = _slot_python(Path(target))
        if p.exists():
            return [str(p), "-m", "agent_worktrees"]

    # 4. last-resort belt: a PATH binstub (rarely reached).
    exe = shutil.which("agent-worktrees")
    return [exe] if exe else None


_RUNTIME_UNRESOLVED_WARNED = False


def _run_related(args: list[str], cwd: str) -> str | None:
    """Run ``agent-worktrees related <args>`` and return stdout, or None."""
    argv = _runtime_argv()
    if not argv:
        global _RUNTIME_UNRESOLVED_WARNED
        if not _RUNTIME_UNRESOLVED_WARNED:
            _RUNTIME_UNRESOLVED_WARNED = True
            # Make the fail-open VISIBLE: the guard denies nothing when it cannot
            # discover guarded roots, so surface that protection is off rather
            # than silently allowing writes (#1089).
            print(
                "cross-repo-guard: could not locate the agent-worktrees runtime "
                "python; guarded-repo discovery is unavailable, so the "
                "write-routing guard is INACTIVE (fail-open) this session.",
                file=sys.stderr,
            )
        return None
    try:
        proc = subprocess.run(
            [*argv, "related", *args], cwd=cwd,
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _discover_guarded_roots(root: str) -> list[dict]:
    """Guarded related repos with a local checkout path, via the related CLI.

    Guarded == ``delegate`` not in (none/empty) AND a local ``registry.path``
    (only a repo actually present on this machine is enforceable). Mirrors the
    retired extension's ``loadGuardedRoots``; the CLI already merges the
    knowledge-repo config overlay + resolves paths.
    """
    list_out = _run_related(["list", "--json", "--repo", root], root)
    if not list_out:
        return []
    try:
        related = (json.loads(list_out) or {}).get("related") or []
    except ValueError:
        return []
    guarded: list[dict] = []
    for r in related:
        if not isinstance(r, dict):
            continue
        delegate = r.get("delegate")
        if not delegate or delegate == "none":
            continue
        name = r.get("name")
        if not name:
            continue
        show_out = _run_related(["show", name, "--json", "--repo", root], root)
        path = ""
        locus = r.get("locus")
        if show_out:
            try:
                show = json.loads(show_out) or {}
                path = (show.get("registry") or {}).get("path") or ""
                locus = show.get("locus") or locus
            except ValueError:
                path = ""
        if path:
            guarded.append({"name": name, "delegate": delegate,
                            "path": path, "locus": locus})
    return guarded


def load_guarded_roots(root: str, home: Path) -> list[dict]:
    """Cached :func:`_discover_guarded_roots` (short TTL sidecar)."""
    cache = _cache_path(root, home)
    cached = _read_cache(cache)
    if cached is not None:
        return cached
    roots = _discover_guarded_roots(root)
    _write_cache(cache, roots)
    return roots


# --- Core evaluation ----------------------------------------------------------

def evaluate(tool: str, args: dict, cwd: str, guarded: list[dict]) -> dict | None:
    """Return the guarded repo hit (with a ``reason``), or None to allow."""
    if not guarded:
        return None
    if tool in WRITE_TOOLS:
        raw = _pick(args, PATH_ARG_KEYS)
        if not raw:
            return None
        abs_path = _resolve(raw, cwd)
        for g in guarded:
            if g.get("path") and is_inside(abs_path, g["path"]):
                return {**g, "reason": _deny_reason(
                    g, f"('{tool}' targeting {raw}).")}
        return None
    if tool in SHELL_TOOLS:
        cmd = _pick(args, CMD_ARG_KEYS)
        if not cmd or not _WRITE_VERBS.search(cmd):
            return None  # pure reads into a guarded repo are allowed
        cmd_norm = (cmd.replace("/", os.sep) if _IS_WIN else cmd)
        for g in guarded:
            gp = g.get("path")
            if not gp:
                continue
            root_str = _canon(gp)
            flags = re.IGNORECASE if _IS_WIN else 0
            for m in re.finditer(re.escape(root_str) + r"[\\/][^\s\"']+",
                                 os.path.normcase(cmd_norm) if _IS_WIN else cmd_norm,
                                 flags):
                if is_inside(m.group(0), gp):
                    return {**g, "reason": _deny_reason(
                        g, "(a shell command writes into it).")}
            # Also catch a bare guarded-root literal (mkdir/rm of the root dir).
            hay = os.path.normcase(cmd_norm) if _IS_WIN else cmd_norm
            if root_str in hay:
                return {**g, "reason": _deny_reason(
                    g, "(a shell command writes into it).")}
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
           guarded_roots=None) -> dict | None:
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

    cwd = str(payload.get("cwd") or "")
    if guarded_roots is None:
        root = find_repo_root(cwd)
        if root is None:
            return None
        guarded = load_guarded_roots(str(root), home)
    else:
        guarded = guarded_roots
    if not guarded:
        return None

    args = _as_args(payload.get("toolArgs") if "toolArgs" in payload
                    else payload.get("tool_input"))
    hit = evaluate(tool, args, cwd, guarded)
    if hit is None:
        return None
    # A live break-glass grant for the target repo lets the write through.
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
