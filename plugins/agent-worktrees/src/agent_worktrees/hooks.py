"""Git hook guardrails for the PR workflow (#583).

Three nudges keep agents on the PR rails.  Each blocks a wrong action *and*
prints a directive telling the agent what to do instead:

- **pre-commit** -- block commits to the default branch from a worktree, AND
  block direct commits in a **worktree-class repo's anchor** (the
  session-independent, git-level counterpart of the Copilot ``anchor_write_guard``
  preToolUse hook; honors ``ANCHOR_WRITE_GUARD=off`` and the ``repos
  allow-edits`` break-glass). Singleton / base-repo anchors stay editable.
- **pre-push** -- in PR mode, block direct pushes from a worktree (the
  legitimate ``create-pr`` / ``push-changes`` feature-branch push sets
  ``AGENT_WORKTREES_PR_PUSH=1`` to bypass).
- **finalize guard** -- lives in ``finalize.py`` (see #586).

Hooks fire only when ``AGENT_WORKTREES_HOOKS=1`` is set in the environment;
the shim short-circuits otherwise, so recovery mode (slim environment) and
external git operations are inert by default.

All logic lives here in Python -- the installed shims are one-liners that
delegate to ``agent-worktrees hook <name>``, so behavior updates with the
plugin and never needs a hook reinstall.
"""

from __future__ import annotations

import contextlib
import os
import stat
import sys
from pathlib import Path

from . import config as cfg
from . import git_ops

HOOK_NAMES = ("pre-commit", "pre-push")

# A POSIX-sh shim. Git ships sh on Windows too, so #!/bin/sh works on every
# platform. The PR-workflow guard runs only when hooks are explicitly enabled;
# a pre-existing hook (saved as <name>.local) ALWAYS runs afterward so wrapping
# never disables a repo's own hook.
_SHIM_TEMPLATE = (
    "#!/bin/sh\n"
    "# agent-worktrees PR-workflow hook shim -- managed; do not edit.\n"
    'if [ "$AGENT_WORKTREES_HOOKS" = "1" ]; then\n'
    '  agent-worktrees hook {name} "$@" || exit $?\n'
    "fi\n"
    'if [ -x "$(dirname "$0")/{name}.local" ]; then\n'
    '  exec "$(dirname "$0")/{name}.local" "$@"\n'
    "fi\n"
    "exit 0\n"
)

_SHIM_MARKER = "agent-worktrees PR-workflow hook shim"


@contextlib.contextmanager
def allow_pr_push():
    """Mark the enclosed git push as a legitimate PR-workflow push.

    Sets ``AGENT_WORKTREES_PR_PUSH=1`` so the pre-push hook permits the
    feature-branch push that ``create-pr`` / ``push-changes`` perform, and
    restores the prior value afterward.
    """
    prev = os.environ.get("AGENT_WORKTREES_PR_PUSH")
    os.environ["AGENT_WORKTREES_PR_PUSH"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("AGENT_WORKTREES_PR_PUSH", None)
        else:
            os.environ["AGENT_WORKTREES_PR_PUSH"] = prev


# --- detection helpers ------------------------------------------------------

def in_worktree(cwd: str | Path) -> bool:
    """Return True if *cwd* is a linked worktree (not the anchor checkout)."""
    gd = git_ops.git("rev-parse", "--git-dir", cwd=cwd, check=False)
    gcd = git_ops.git("rev-parse", "--git-common-dir", cwd=cwd, check=False)
    if gd.returncode != 0 or gcd.returncode != 0:
        return False
    try:
        a = (Path(cwd) / gd.stdout.strip()).resolve() if not Path(gd.stdout.strip()).is_absolute() else Path(gd.stdout.strip()).resolve()
        b = (Path(cwd) / gcd.stdout.strip()).resolve() if not Path(gcd.stdout.strip()).is_absolute() else Path(gcd.stdout.strip()).resolve()
    except Exception:
        return False
    return a != b


def _current_branch(cwd: str | Path) -> str | None:
    r = git_ops.git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd, check=False)
    if r.returncode != 0:
        return None
    name = r.stdout.strip()
    return None if name in ("", "HEAD") else name


def _anchor_from_cwd(cwd: str | Path) -> Path | None:
    """Resolve the repo anchor (main checkout) from *cwd* via git-common-dir.

    Works from a linked worktree or the anchor itself and -- unlike the
    project-name resolver behind ``load_config()`` -- needs no ``--project`` /
    ``$WORKTREE_PROJECT`` context, so it is safe from a bare git-hook process
    where no active project is resolvable (#234 defect 3).
    """
    r = git_ops.git("rev-parse", "--git-common-dir", cwd=cwd, check=False)
    if r.returncode != 0:
        return None
    common = Path(r.stdout.strip())
    if not common.is_absolute():
        common = (Path(cwd) / common).resolve()
    # ``common`` is ``<anchor>/.git`` -> the anchor is its parent.
    return common.parent


def _inrepo(cwd: str | Path) -> dict:
    """Read the repo's committed ``.agent-worktrees/config.yaml`` from *cwd*.

    Resolves the anchor from git-common-dir and reads the in-repo config
    directly, bypassing the ambient project-discovery ``load_config()`` (which
    raises "No active project" from a bare hook). Never raises -- returns ``{}``
    when the anchor or file can't be resolved.
    """
    try:
        anchor = _anchor_from_cwd(cwd)
        if anchor is None:
            return {}
        raw = cfg._load_inrepo_config(str(anchor))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _default_branch(cwd: str | Path) -> str | None:
    """Best-effort default branch: in-repo config first, then origin/HEAD."""
    db = _inrepo(cwd).get("default_branch")
    if db:
        return str(db)
    r = git_ops.git(
        "symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=cwd, check=False
    )
    if r.returncode == 0 and "/" in r.stdout:
        return r.stdout.strip().split("/", 1)[1]
    return None


def _pr_enabled(cwd: str | Path) -> bool:
    """Return True when the repo at *cwd* has PR mode enabled.

    Reads the committed in-repo config directly (see ``_inrepo``) so it works
    from a bare git-hook, where ``load_config()`` cannot resolve a project
    (#234 defect 3). Fails open (False) only when the config can't be read.
    """
    try:
        raw = _inrepo(cwd)
        return bool(cfg._parse_pr(raw.get("pr")).enabled)
    except Exception:
        return False


# --- hook handlers ----------------------------------------------------------

def _err(msg: str) -> None:
    """Write a directive guard message to stderr (ASCII -- git-sh safe)."""
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


# Shared master kill-switch for the write-routing guard family -- honored by the
# Copilot preToolUse ``anchor_write_guard`` too, so one env var disables both the
# session hook and this git-level guard.
def _anchor_guard_off() -> bool:
    for var in ("ANCHOR_WRITE_GUARD", "CROSS_REPO_GUARD"):
        if os.environ.get(var, "").strip().lower() in {"off", "0", "false", "no"}:
            return True
    return False


def _worktree_class_anchor(cwd: str | Path):
    """If *cwd*'s anchor is a **worktree-class** repo in the registry, return its
    ``RepoEntry``; else None.

    Resolves the anchor from git-common-dir (bare-hook safe) and matches it,
    path-normalized, against the machine-local ``repos.yaml`` worktree-class
    entries. Singleton / reference / unregistered anchors return None (they are
    NOT guarded -- a singleton is edited in its anchor by design). Never raises.
    """
    try:
        anchor = _anchor_from_cwd(cwd)
        if anchor is None:
            return None
        from . import repos
        target = os.path.normcase(os.path.normpath(str(anchor)))
        for entry in repos.list_repos("worktree"):
            lp = entry.local_path()
            if lp and os.path.normcase(os.path.normpath(lp)) == target:
                return entry
        return None
    except Exception:
        return None


def _pre_commit() -> int:
    cwd = os.getcwd()
    if not in_worktree(cwd):
        # In the ANCHOR (main checkout). A worktree-class repo's anchor must NOT
        # receive direct commits -- work flows through a linked worktree + PR, so
        # a stray anchor commit is a latent hazard (never lands through the flow;
        # a dirty anchor blocks pulls). This is the session-independent, git-level
        # counterpart of the Copilot preToolUse ``anchor_write_guard`` (which is
        # absent whenever the plugin's hooks don't load). Singleton / base-repo /
        # unregistered anchors are edited in place by design and stay allowed.
        # Honors the shared kill-switch and the ``repos allow-edits`` break-glass.
        if _anchor_guard_off():
            return 0
        entry = _worktree_class_anchor(cwd)
        if entry is None:
            return 0  # not a worktree-class anchor -> allow (base-repo mode)
        from . import allow_edits
        if allow_edits.is_active(entry.name):
            return 0  # live break-glass grant
        _err(
            f"BLOCKED: '{entry.name}' is a worktree-class repo and you are in its "
            f"ANCHOR (main checkout) -- do NOT commit directly in the anchor. A "
            f"stray anchor commit bypasses the worktree + PR flow. Create/use a "
            f"linked worktree and commit THERE: 'agent-worktrees create' (then "
            f"work in the returned path). If a direct anchor commit is genuinely "
            f"unavoidable (bootstrap/recovery), break glass: 'agent-worktrees "
            f"repos allow-edits {entry.name} --reason \"<why>\"' (logged, "
            f"time-boxed), then retry. (Disable: ANCHOR_WRITE_GUARD=off.)"
        )
        return 1
    branch = _current_branch(cwd)
    default_branch = _default_branch(cwd)
    if branch and default_branch and branch == default_branch:
        _err(
            f"BLOCKED: You are in a worktree but committing to the default "
            f"branch '{default_branch}'. Commits in a worktree belong on the "
            f"worktree branch (worktree/<id>) or a feature branch. To submit "
            f"work, create a feature branch and open a pull request "
            f"(agent-worktrees create-pr)."
        )
        return 1
    return 0


def _pre_push() -> int:
    # The CLI's own create-pr / push-changes set this for their legit push.
    if os.environ.get("AGENT_WORKTREES_PR_PUSH") == "1":
        return 0
    cwd = os.getcwd()
    if not in_worktree(cwd):
        return 0
    if not _pr_enabled(cwd):
        return 0  # direct-push repos: pre-push is a no-op
    _err(
        "BLOCKED: This repository uses pull requests. Do not push directly "
        "from a worktree. Use 'agent-worktrees create-pr' to push a feature "
        "branch and open a PR, or 'agent-worktrees push-changes' to update an "
        "existing PR branch."
    )
    return 1


def run_hook(name: str, argv: list[str]) -> int:
    """Dispatch a hook by name. Unknown hooks are allowed (exit 0)."""
    if name == "pre-commit":
        return _pre_commit()
    if name == "pre-push":
        return _pre_push()
    if name == "install":
        return _cmd_install(argv)
    _err(f"agent-worktrees: unknown hook '{name}' -- allowing.")
    return 0


# --- shim installation ------------------------------------------------------

def hooks_dir_for(anchor: str | Path) -> Path | None:
    """Return the shared hooks directory for *anchor* (its common .git/hooks)."""
    r = git_ops.git("rev-parse", "--git-common-dir", cwd=anchor, check=False)
    if r.returncode != 0:
        return None
    common = Path(r.stdout.strip())
    if not common.is_absolute():
        common = (Path(anchor) / common).resolve()
    return common / "hooks"


def install_hooks(anchor: str | Path) -> list[str]:
    """Install the PR-workflow shims into *anchor*'s shared hooks dir.

    Idempotent.  A pre-existing, non-shim hook is preserved as ``<name>.local``
    and chained after our check.  Returns the list of hook names installed.
    """
    hdir = hooks_dir_for(anchor)
    if hdir is None:
        return []
    hdir.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for name in HOOK_NAMES:
        target = hdir / name
        shim = _SHIM_TEMPLATE.format(name=name)
        if target.exists():
            existing = target.read_text(encoding="utf-8", errors="replace")
            if _SHIM_MARKER in existing:
                if existing != shim:
                    target.write_text(shim, encoding="utf-8", newline="\n")
                installed.append(name)
                _make_executable(target)
                continue
            # Preserve a foreign hook and chain it.
            local = hdir / f"{name}.local"
            if not local.exists():
                target.replace(local)
                _make_executable(local)
        target.write_text(shim, encoding="utf-8", newline="\n")
        _make_executable(target)
        installed.append(name)
    return installed


def _make_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


# --- core.hooksPath reconciliation (adopt-owned) ----------------------------
#
# Git honors a set ``core.hooksPath`` *over* the default ``.git/hooks``. When a
# repo carries a stale repo-local ``core.hooksPath`` -- e.g. a retired in-repo
# hooks dir -- git runs that directory and silently ignores the managed
# ``.git/hooks`` shims, so the PR-workflow guard never fires. Clearing it is a
# *mutation* of the repo's git config, so it belongs to the adopt/register flow;
# install/update may only *detect* it (read-only warn).

def _managed_shim_present(hooks_dir: Path | None) -> bool:
    """True if *hooks_dir* holds the managed (by-marker) pre-commit shim."""
    if hooks_dir is None:
        return False
    try:
        target = hooks_dir / "pre-commit"
        return target.exists() and _SHIM_MARKER in target.read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False


def _local_hooks_path(anchor: str | Path) -> str | None:
    """Return the repo-*local* ``core.hooksPath`` value, or None if unset.

    Reads ``--local`` only: a ``core.hooksPath`` set at global/system scope is a
    machine-wide user choice this flow must not touch.
    """
    r = git_ops.git(
        "config", "--local", "--get", "core.hooksPath", cwd=anchor, check=False
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def stale_hooks_path(anchor: str | Path) -> str | None:
    """Return a repo-local ``core.hooksPath`` that *shadows* the managed shims.

    Stale means: set locally and not resolving to the shared ``.git/hooks`` dir
    where :func:`install_hooks` places the shims -- so git would run the other
    directory and the PR-workflow guard would never fire. Read-only (no
    mutation): returns the stale value, or None when unset or already pointing
    at the managed hooks dir.
    """
    val = _local_hooks_path(anchor)
    if val is None:
        return None
    managed = hooks_dir_for(anchor)
    if managed is None:
        return None
    p = Path(val)
    if not p.is_absolute():
        p = Path(anchor) / p
    try:
        return None if p.resolve() == managed.resolve() else val
    except OSError:
        return val


def clear_stale_hooks_path(anchor: str | Path) -> str | None:
    """Unset a stale repo-local ``core.hooksPath`` so git honors ``.git/hooks``.

    Mutation -- **adopt/register only**. Returns the cleared value, or None when
    there was nothing stale to clear.
    """
    val = stale_hooks_path(anchor)
    if val is None:
        return None
    git_ops.git(
        "config", "--local", "--unset", "core.hooksPath", cwd=anchor, check=False
    )
    return val


def hook_health(anchor: str | Path) -> tuple[bool, str | None]:
    """Read-only PR-workflow-hook health for *anchor* (install/update warn).

    Returns ``(shims_present, stale_hooks_path)``:

    * ``shims_present`` -- the managed ``pre-commit`` shim is installed in the
      shared ``.git/hooks``.
    * ``stale_hooks_path`` -- a repo-local ``core.hooksPath`` shadowing the
      shims, or None.

    Never mutates -- callers (install/update) only warn; arming/clearing is an
    adopt (register) concern.
    """
    return _managed_shim_present(hooks_dir_for(anchor)), stale_hooks_path(anchor)


def _cmd_install(argv: list[str]) -> int:
    """`agent-worktrees hook install [--anchor PATH]` -- install shims."""
    anchor: str | None = None
    if "--anchor" in argv:
        i = argv.index("--anchor")
        if i + 1 < len(argv):
            anchor = argv[i + 1]
    if not anchor:
        try:
            anchor = cfg.load_config().default_repo.anchor
        except Exception:
            anchor = os.getcwd()
    installed = install_hooks(anchor)
    if installed:
        sys.stderr.write(
            f"Installed PR-workflow hooks ({', '.join(installed)}) into "
            f"{hooks_dir_for(anchor)}\n"
        )
        return 0
    sys.stderr.write(f"No hooks installed (could not resolve hooks dir for {anchor}).\n")
    return 1
