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
8. ``branch_drift`` -- ``repos.yaml`` and ``projects.yaml`` disagree on
   ``default_branch``.  Fix: align ``projects.yaml`` to ``repos.yaml`` (identity
   is the source of truth).

It also reconciles each adopted project's **machine-local overlay**
(``~/.<project>/config.yaml``) against the registries + global config, so the
overlay stays minimal (it should carry only what the registries can't supply --
e.g. ``env_script``).  ``config.load_config`` falls back to the registries for
``anchor`` (repos.yaml), ``default_branch`` (repos.yaml), ``base_repo``
(projects.yaml), and ``srcroot``/``machine``/``platform`` (global config), so an
overlay that restates any of these is redundant:

* ``overlay_redundant_toplevel`` / ``overlay_conflicting_srcroot`` -- top-level
  ``srcroot``/``machine``/``platform`` equal to (or, for the inert srcroot,
  differing from) global config.  Fix: strip the key from the overlay.
* ``overlay_redundant_anchor`` / ``overlay_redundant_branch`` /
  ``overlay_redundant_base_repo`` -- a per-repo key restating the registry
  value.  Fix: strip it (registry is the single source of truth).
* ``overlay_conflicting_anchor`` / ``overlay_conflicting_branch`` /
  ``overlay_conflicting_base_repo`` -- a per-repo key that **differs** from the
  registry (the overlay wins at launch).  **Report only** -- the operator must
  decide which side is right.

Overlay ``--fix`` edits ``~/.<project>/config.yaml`` in place, removing only the
redundant single-line scalar keys (comments and other keys are preserved).
"""

from __future__ import annotations

import os
import re
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
    "wsl_state_stale",
    "branch_drift",
    "overlay_redundant_anchor",
    "overlay_redundant_toplevel",
    "overlay_redundant_branch",
    "overlay_redundant_base_repo",
    "overlay_conflicting_srcroot",
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


def _wsl_install_present(distro: str | None) -> bool | None:
    """Whether WSL carries a full agent-worktrees install (the venv binstub).

    Read-only probe of the WSL side for the deployed runtime a
    ``wsl.state: "adopted"`` marker asserts.  Returns ``True`` when the binstub
    exists, ``False`` when WSL is reachable but the install is absent, and
    ``None`` when the probe is inconclusive (no ``wsl.exe``, timeout, spawn
    error) -- callers treat ``None`` as "leave the marker untouched".
    """
    import shutil
    import subprocess

    wsl_exe = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl_exe:
        return None
    probe = 'test -x "$HOME/.agent-worktrees/.venv/bin/agent-worktrees"'
    argv = [wsl_exe]
    if distro:
        argv += ["-d", distro]
    argv += ["--", "bash", "-lc", probe]
    try:
        cp = subprocess.run(
            argv, capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return cp.returncode == 0


# ---------------------------------------------------------------------------
# Overlay reconciliation helpers -- the per-project machine-local config
# (``~/.<project>/config.yaml``) vs the registries + global config.
# ---------------------------------------------------------------------------

def _read_global_config() -> dict:
    """Machine-wide ``~/.agent-worktrees/config.yaml`` (srcroot/machine/platform)."""
    path = Path.home() / ".agent-worktrees" / "config.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _overlay_path(config_dir: str | None) -> Path | None:
    """Resolve a project's overlay ``config.yaml`` from its ``config_dir``."""
    if not config_dir:
        return None
    return Path(os.path.expanduser(config_dir)) / "config.yaml"


def _read_overlay(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _strip_overlay_keys(
    path: Path, top_keys: set[str], repo_name: str, repo_keys: set[str]
) -> bool:
    """Surgically remove redundant scalar keys from an overlay, preserving
    comments and every other line. Removes top-level ``top_keys`` (col 0) and,
    inside the ``  <repo_name>:`` block, the 4-space ``repo_keys``. Only
    single-line scalars are targeted, so line removal is safe. Returns True if
    anything changed."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception:
        return False
    top_re = re.compile(r"^([A-Za-z0-9_]+)\s*:")
    repo_hdr_re = re.compile(r"^  ([A-Za-z0-9._-]+)\s*:\s*$")
    repo_key_re = re.compile(r"^    ([A-Za-z0-9_]+)\s*:")
    out: list[str] = []
    in_block = False
    changed = False
    for line in lines:
        m = top_re.match(line)
        if m:
            in_block = False  # left any repo block at a col-0 key
            if m.group(1) in top_keys:
                changed = True
                continue
        mh = repo_hdr_re.match(line)
        if mh:
            in_block = mh.group(1) == repo_name
            out.append(line)
            continue
        if in_block:
            mk = repo_key_re.match(line)
            if mk and mk.group(1) in repo_keys:
                changed = True
                continue
        out.append(line)
    if changed:
        path.write_text("".join(out), encoding="utf-8")
    return changed


def _overlay_findings(
    projects: dict[str, dict],
    registry: "repos.ReposRegistry",
    plat: str,
    fix: bool,
) -> list[Finding]:
    """Reconcile each adopted project's machine-local overlay against the
    registries + global config: flag keys that redundantly restate a
    registry/global value (fixable: strip) or conflict with it (report-only for
    anchor/branch/base_repo; strip for the inert srcroot case)."""
    findings: list[Finding] = []
    gcfg = _read_global_config()
    for pname in sorted(projects):
        proj = projects[pname]
        opath = _overlay_path(proj.get("config_dir"))
        overlay = _read_overlay(opath)
        if not overlay:
            continue

        strip_top: set[str] = set()
        strip_repo: set[str] = set()

        # --- top-level srcroot/machine/platform vs global config ------------
        for key in ("srcroot", "machine", "platform"):
            if key not in overlay:
                continue
            oval, gval = overlay.get(key), gcfg.get(key)
            if gval is None:
                continue
            if key == "srcroot":
                equal = _norm(str(oval)) == _norm(str(gval))
            else:
                equal = str(oval) == str(gval)
            if equal:
                findings.append(Finding(
                    repo=pname, kind="overlay_redundant_toplevel",
                    severity=SEV_WARNING,
                    detail=f"overlay '{key}: {oval}' just restates global config",
                    fixable=True, fix_detail=f"remove overlay top-level '{key}'",
                ))
                strip_top.add(key)
            elif key == "srcroot":
                # A conflicting overlay srcroot is inert (only feeds `get
                # src-dir`) and misleading -- strip it to inherit the global.
                findings.append(Finding(
                    repo=pname, kind="overlay_conflicting_srcroot",
                    severity=SEV_WARNING,
                    detail=(f"overlay 'srcroot: {oval}' conflicts with global "
                            f"'{gval}' (inert; only feeds 'get src-dir')"),
                    fixable=True, fix_detail="remove overlay 'srcroot'",
                ))
                strip_top.add("srcroot")

        # --- per-repo anchor / default_branch / base_repo vs registries -----
        repo_over = (overlay.get("repos") or {}).get(pname) or {}
        if isinstance(repo_over, dict):
            entry = registry.repos.get(pname)
            reg_path = entry.paths.get(plat) if entry else None
            if "anchor" in repo_over and reg_path:
                if _norm(str(repo_over["anchor"])) == _norm(str(reg_path)):
                    findings.append(Finding(
                        repo=pname, kind="overlay_redundant_anchor",
                        severity=SEV_WARNING,
                        detail=f"overlay anchor restates repos.yaml path {reg_path}",
                        fixable=True, fix_detail="remove overlay 'anchor'",
                    ))
                    strip_repo.add("anchor")
                else:
                    findings.append(Finding(
                        repo=pname, kind="overlay_conflicting_anchor",
                        severity=SEV_WARNING,
                        detail=(f"overlay anchor {repo_over['anchor']} != repos.yaml "
                                f"path {reg_path} ({plat}); overlay wins at launch"),
                        fixable=False,
                        fix_detail="align repos.yaml or remove the overlay anchor",
                    ))

            reg_branch = (entry.default_branch if entry else "") or str(
                proj.get("default_branch") or ""
            )
            if "default_branch" in repo_over and reg_branch:
                if str(repo_over["default_branch"]) == reg_branch:
                    findings.append(Finding(
                        repo=pname, kind="overlay_redundant_branch",
                        severity=SEV_WARNING,
                        detail=(f"overlay default_branch restates registry "
                                f"'{reg_branch}' (now sourced from the registry)"),
                        fixable=True, fix_detail="remove overlay 'default_branch'",
                    ))
                    strip_repo.add("default_branch")
                else:
                    findings.append(Finding(
                        repo=pname, kind="overlay_conflicting_branch",
                        severity=SEV_WARNING,
                        detail=(f"overlay default_branch '{repo_over['default_branch']}'"
                                f" != registry '{reg_branch}'; overlay wins at launch"),
                        fixable=False,
                        fix_detail="align the registry or remove the overlay key",
                    ))

            proj_base = proj.get("base_repo")
            if "base_repo" in repo_over and proj_base is not None:
                if bool(repo_over["base_repo"]) == bool(proj_base):
                    findings.append(Finding(
                        repo=pname, kind="overlay_redundant_base_repo",
                        severity=SEV_WARNING,
                        detail=("overlay base_repo restates projects.yaml "
                                f"'{bool(proj_base)}' (now sourced from the registry)"),
                        fixable=True, fix_detail="remove overlay 'base_repo'",
                    ))
                    strip_repo.add("base_repo")
                else:
                    findings.append(Finding(
                        repo=pname, kind="overlay_conflicting_base_repo",
                        severity=SEV_WARNING,
                        detail=(f"overlay base_repo {bool(repo_over['base_repo'])} != "
                                f"projects.yaml {bool(proj_base)}; overlay wins at launch"),
                        fixable=False,
                        fix_detail="align projects.yaml or remove the overlay key",
                    ))

        if fix and (strip_top or strip_repo) and opath is not None:
            if _strip_overlay_keys(opath, strip_top, pname, strip_repo):
                for f in findings:
                    if f.repo == pname and f.kind in _AUTOFIXABLE and not f.fixed:
                        f.fixed = True
    return findings


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

        # default_branch drift: repos.yaml (identity SoT) vs projects.yaml.
        if branch and entry.default_branch and entry.default_branch != branch:
            f = Finding(
                repo=pname, kind="branch_drift", severity=SEV_WARNING,
                detail=(f"repos.yaml default_branch '{entry.default_branch}' != "
                        f"projects.yaml '{branch}'"),
                fixable=True,
                fix_detail=(f"set projects.yaml default_branch = "
                            f"{entry.default_branch}"),
            )
            if fix:
                proj["default_branch"] = entry.default_branch
                dirty_projects = True
                f.fixed = True
            findings.append(f)

    # --- WSL adoption state: promote a stale 'bootstrap' marker -------------
    # The ``wsl.state`` marker lives on the Windows host that owns the WSL
    # environment.  A Windows-side install/adopt always re-registers with
    # ``wsl_state=None`` (preserving the existing block verbatim), and only a
    # *WSL-side* install sets ``adopted`` -- in WSL's own registry, never this
    # one.  So once the Windows record holds ``bootstrap`` it can never promote,
    # even after WSL is fully adopted.  Probe WSL here and promote.  Windows-
    # only; other platforms don't own the marker.
    if plat == "windows":
        for pname in sorted(projects):
            wsl = projects[pname].get("wsl")
            if not isinstance(wsl, dict) or wsl.get("state") != "bootstrap":
                continue
            present = _wsl_install_present(wsl.get("distro"))
            if present is None:
                continue  # inconclusive -- leave the marker untouched
            if present:
                f = Finding(
                    repo=pname, kind="wsl_state_stale", severity=SEV_WARNING,
                    detail=(f"'{pname}' wsl.state is 'bootstrap' but WSL carries "
                            "a full agent-worktrees install"),
                    fixable=True,
                    fix_detail="promote wsl.state 'bootstrap' -> 'adopted'",
                )
                if fix:
                    wsl["state"] = "adopted"
                    dirty_projects = True
                    f.fixed = True
                findings.append(f)
            else:
                findings.append(Finding(
                    repo=pname, kind="wsl_unadopted", severity=SEV_WARNING,
                    detail=(f"'{pname}' wsl.state is 'bootstrap' and no WSL "
                            "install was detected"),
                    fixable=False,
                    fix_detail=("adopt inside WSL: run the agent-worktrees "
                                "installer in the WSL distro"),
                ))

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

    # --- per-project overlay reconciliation (redundant/conflicting keys) ----
    findings.extend(_overlay_findings(projects, registry, plat, fix))

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
