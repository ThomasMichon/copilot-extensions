"""Registry reconciliation -- diagnose and fix drift between the two registries.

agent-worktrees keeps two overlapping registries that can drift:

* ``repos.yaml`` -- identity/catalog: name, ``class``, ``remote``, per-platform
  paths, ``srcroot``, ``tags``, ``contributing``  (owned by :mod:`repos`).
* ``projects.yaml`` -- adoption/launch: ``anchor``, ``config_dir``,
  ``default_branch``, ``expose_agent``, ``base_repo``, ``elevated``, ``wsl``,
  ``machines_yaml``  (owned by :mod:`installer`; read by ``config.load_config``).

They overlap on **anchor/path, class, and agent-exposure**.  ``repos doctor``
diagnoses the drift; ``repos doctor --fix`` reconciles the **data-only** cases.

Source-of-truth philosophy (also the target for the eventual single-registry
migration):

* ``repos.yaml`` is authoritative for **identity** -- name, class, path, remote,
  agent-exposure.
* ``projects.yaml`` is authoritative for **adoption-runtime** -- ``config_dir``,
  ``base_repo``, ``elevated``, ``wsl``, ``machines_yaml`` -- and for the *fact*
  that a repo is adopted.

Reconciliation rules:

1. ``missing_repo_entry`` -- an adopted project with no ``repos.yaml`` entry.
   Fix: create a ``worktree``-class entry (path = anchor, branch + agent from
   the project, remote auto-detected from ``git``).
2. ``wrong_class`` -- an adopted project whose entry is ``reference`` (you
   cannot adopt a read-only repo).  Fix: upgrade to ``worktree`` (and take the
   project's ``expose_agent`` as the agent value).
3. ``anchor_mismatch`` -- the project ``anchor`` differs from the entry's path
   for this platform.  Fix: align the entry path to the live anchor.
4. ``agent_mismatch`` -- ``repos.agent`` disagrees with the project's effective
   ``expose_agent``.  Fix: ``repos.yaml`` wins; align ``projects.expose_agent``.
5. ``name_collision`` -- a ``repos.yaml`` entry and a project share one anchor
   under different names.  Fix: rename the ``repos.yaml`` entry to the project
   (adoption) name -- that is the launchable identity.
6. ``unadopted_worktree`` -- a ``worktree``/``singleton`` entry that is not
   adopted (no launch/binstub).  **Report only** (adoption has side effects);
   recommend ``agent-worktrees register <name>``.
7. ``stale_path`` -- an entry whose path for this platform is missing on disk.
   **Report only**; recommend re-cloning or removing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import output, repos

SEV_ERROR = "error"
SEV_WARNING = "warning"

# Finding kinds that ``--fix`` can reconcile automatically (data-only).
_AUTOFIXABLE = {
    "missing_repo_entry",
    "wrong_class",
    "anchor_mismatch",
    "agent_mismatch",
    "name_collision",
}


@dataclass
class Finding:
    """One reconciliation finding."""

    repo: str
    kind: str
    severity: str
    detail: str
    fixable: bool = False
    fix_detail: str = ""
    fixed: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _projects_path() -> Path:
    return Path.home() / ".agent-worktrees" / "projects.yaml"


def _read_projects() -> dict[str, dict]:
    """Return the ``projects`` map from projects.yaml (name -> entry dict)."""
    path = _projects_path()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return {}
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in projects.items()}


def _effective_expose_agent(proj: dict) -> bool:
    """A project's effective agent-exposure (absent ``expose_agent`` => True)."""
    val = proj.get("expose_agent")
    return True if val is None else bool(val)


def _norm(p: str | None) -> str:
    """Normalize a path for cross-entry comparison (case/sep-insensitive)."""
    if not p:
        return ""
    return os.path.normcase(os.path.normpath(os.path.expanduser(str(p))))


def _detect_remote(path: str) -> str:
    """Best-effort ``origin`` remote URL for a checkout (empty on failure)."""
    if not path or not Path(path).exists():
        return ""
    try:
        cp = repos._git(path, "remote", "get-url", "origin")
        if cp.returncode == 0:
            return cp.stdout.strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Diagnose / reconcile
# ---------------------------------------------------------------------------

def reconcile(fix: bool = False, plat: str | None = None) -> list[Finding]:
    """Diagnose drift and, when ``fix`` is set, reconcile the data-only cases.

    Returns the list of findings; each ``fixed`` flag reflects whether ``--fix``
    resolved it.  ``unadopted_worktree`` and ``stale_path`` are reported but
    never auto-fixed.
    """
    plat = plat or repos._current_platform()
    registry = repos.read_registry()
    projects = _read_projects()
    findings: list[Finding] = []

    # Index repos.yaml entries by normalized current-platform path for
    # collision detection.
    path_to_repo: dict[str, str] = {}
    for name, entry in registry.repos.items():
        np = _norm(entry.paths.get(plat))
        if np:
            path_to_repo.setdefault(np, name)

    dirty_repos = False
    dirty_projects = False

    # --- adopted projects must have a coherent repos.yaml entry --------------
    for pname in sorted(projects):
        proj = projects[pname]
        anchor = proj.get("anchor") or ""
        branch = proj.get("default_branch") or ""
        expose = _effective_expose_agent(proj)
        entry = registry.repos.get(pname)

        if entry is None:
            # No same-name entry.  Is there an entry at the same anchor under a
            # different name?  -> name collision (canonicalize to project name).
            other = path_to_repo.get(_norm(anchor))
            if other and other != pname:
                f = Finding(
                    repo=pname, kind="name_collision", severity=SEV_WARNING,
                    detail=(f"repos.yaml '{other}' and project '{pname}' share "
                            f"anchor {anchor}; canonical name is '{pname}'"),
                    fixable=True,
                    fix_detail=f"rename repos.yaml entry '{other}' -> '{pname}'",
                )
                if fix:
                    moved = registry.repos.pop(other)
                    moved.name = pname
                    if moved.repo_class == "reference":
                        moved.repo_class = "worktree"
                        moved.agent = expose   # adoption's exposure on upgrade
                    if anchor and _norm(moved.paths.get(plat)) != _norm(anchor):
                        moved.paths[plat] = anchor
                    registry.repos[pname] = moved
                    path_to_repo[_norm(anchor)] = pname
                    dirty_repos = True
                    f.fixed = True
                findings.append(f)
                continue

            # Genuinely missing -> create a worktree-class entry.
            f = Finding(
                repo=pname, kind="missing_repo_entry", severity=SEV_ERROR,
                detail=f"adopted project '{pname}' has no repos.yaml entry",
                fixable=True,
                fix_detail=f"create worktree entry at {anchor or '(no anchor)'}",
            )
            if fix:
                registry.repos[pname] = repos.RepoEntry(
                    name=pname,
                    repo_class="worktree",
                    remote=_detect_remote(anchor),
                    default_branch=branch,
                    agent=expose,
                    paths={plat: anchor} if anchor else {},
                )
                if anchor:
                    path_to_repo[_norm(anchor)] = pname
                dirty_repos = True
                f.fixed = True
            findings.append(f)
            continue

        # Entry exists -- check class, anchor, agent.
        if entry.repo_class == "reference":
            f = Finding(
                repo=pname, kind="wrong_class", severity=SEV_ERROR,
                detail=(f"'{pname}' is adopted but classed 'reference' "
                        "(read-only cannot be adopted)"),
                fixable=True, fix_detail="upgrade class reference -> worktree",
            )
            if fix:
                entry.repo_class = "worktree"
                entry.agent = expose  # take adoption's exposure on upgrade
                dirty_repos = True
                f.fixed = True
            findings.append(f)

        repo_path = entry.paths.get(plat)
        if anchor and _norm(repo_path) != _norm(anchor):
            f = Finding(
                repo=pname, kind="anchor_mismatch", severity=SEV_WARNING,
                detail=(f"project anchor {anchor} != repos.yaml path "
                        f"{repo_path or '(unset)'} ({plat})"),
                fixable=True, fix_detail=f"set repos.yaml {plat} path = {anchor}",
            )
            if fix:
                entry.paths[plat] = anchor
                dirty_repos = True
                f.fixed = True
            findings.append(f)

        # Agent-exposure: repos.yaml wins; align the project entry to it.
        if entry.repo_class != "reference" and entry.agent != expose:
            f = Finding(
                repo=pname, kind="agent_mismatch", severity=SEV_WARNING,
                detail=(f"repos.agent={entry.agent} but project "
                        f"expose_agent={expose}"),
                fixable=True,
                fix_detail=f"set project expose_agent = {entry.agent}",
            )
            if fix:
                proj["expose_agent"] = entry.agent
                dirty_projects = True
                f.fixed = True
            findings.append(f)

    # --- worktree/singleton entries that are not adopted --------------------
    for name in sorted(registry.repos):
        entry = registry.repos[name]
        if entry.repo_class in ("worktree", "singleton") and name not in projects:
            findings.append(Finding(
                repo=name, kind="unadopted_worktree", severity=SEV_WARNING,
                detail=(f"'{name}' is {entry.repo_class}-class but not adopted "
                        "(no launch/binstub)"),
                fixable=False,
                fix_detail=f"adopt with: agent-worktrees register {name}",
            ))

    # --- entries whose path is missing on disk ------------------------------
    for name in sorted(registry.repos):
        entry = registry.repos[name]
        p = entry.paths.get(plat)
        if p and not Path(os.path.expanduser(p)).exists():
            findings.append(Finding(
                repo=name, kind="stale_path", severity=SEV_WARNING,
                detail=f"path for {plat} does not exist: {p}",
                fixable=False,
                fix_detail=f"re-clone or remove with: repos remove {name}",
            ))

    if fix and dirty_repos:
        repos.write_registry(registry)
    if fix and dirty_projects:
        # Write the (already-mutated) projects map back to the same path we
        # read it from, reusing installer's faithful formatter.
        from . import installer
        installer.write_projects_registry(
            {"projects": projects}, path=_projects_path()
        )

    return findings


def diagnose(plat: str | None = None) -> list[Finding]:
    """Diagnose drift without mutating anything."""
    return reconcile(fix=False, plat=plat)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(findings: list[Finding], *, fixed_mode: bool) -> None:
    """Print a human-readable doctor report."""
    if not findings:
        output.ok("Registries are reconciled -- no drift found.")
        return

    errors = [f for f in findings if f.severity == SEV_ERROR]
    warnings = [f for f in findings if f.severity == SEV_WARNING]
    output.header("Registry Doctor")
    print(f"  {len(errors)} error(s), {len(warnings)} warning(s)\n")

    for f in findings:
        mark = "✓ fixed" if f.fixed else ("✗" if f.severity == SEV_ERROR else "!")
        print(f"  [{mark}] {f.repo}  ({f.kind})")
        print(f"        {f.detail}")
        if f.fix_detail and not f.fixed:
            verb = "fix" if f.fixable else "manual"
            print(f"        -> ({verb}) {f.fix_detail}")
        print()

    if not fixed_mode:
        n = sum(1 for f in findings if f.fixable)
        if n:
            print(f"  Run 'agent-worktrees repos doctor --fix' to reconcile "
                  f"{n} fixable finding(s).")
