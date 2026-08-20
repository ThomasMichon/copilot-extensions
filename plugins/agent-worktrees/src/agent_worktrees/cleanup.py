"""Orphanage cleanup consumer (resource-obligation-settlement, dotfiles#1161).

Reads the durable orphanage -- obligations re-homed by an ``--abandon`` finalize
(:func:`tracking.load_orphaned_obligations`) -- and actually **reclaims** the
resources those obligations named (deleting an orphaned CodeSpace, ...), then
removes the settled entry from the registry. The read-only lister is
``agent-worktrees claims orphans``; this is the acting consumer,
``agent-worktrees claims cleanup``.

Conservative + degrade-safe, matching the rest of the effort:

* **Dry-run by default** -- ``apply=False`` reports what *would* be reclaimed and
  touches nothing.
* **Same-machine only** -- an entry whose ``machine`` is not this box is
  *skipped* (surfaced for cleanup on its own machine), never acted on from here.
* **Best-effort, never raises** -- every reclaimer swallows failure and reports
  it; a failed reclaim leaves the entry in the registry (never lost) for a
  retry. Only a *positive* reclaim removes the entry.
* **Idempotent** -- an already-gone resource (e.g. ``agent-codespaces delete``
  reports the box is a 404 / not found) counts as reclaimed: the obligation is
  discharged either way.

(Distinct from :mod:`agent_worktrees.sweep`, which flips a *still-owned* active
claim to ``abandoned`` on a crashed holder; this module acts on obligations
already *re-homed* to the durable orphanage and disposes of the underlying
resource.)
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

from agent_procutil import no_window_flags

from . import config as cfg
from . import sweep as sweep_mod
from . import tracking

log = logging.getLogger(__name__)


@dataclass
class ReclaimResult:
    """The verdict for one orphanage entry.

    ``status`` is one of:

    * ``"reclaimed"`` -- the resource is disposed (or already gone); drop the
      entry.
    * ``"failed"`` -- the reclaim was attempted but did not succeed; keep the
      entry for a retry.
    * ``"skipped"`` -- not actionable here (cross-machine); keep the entry.
    * ``"unsupported"`` -- no reclaimer for this kind yet; keep the entry.
    """

    status: str
    detail: str = ""

    @property
    def reclaimed(self) -> bool:
        return self.status == "reclaimed"


def _creationflags() -> int:
    return no_window_flags()


def _run_codespaces(args: list[str], *, timeout: float = 300.0):
    """Run ``agent-codespaces <args>``; return the process, or None if unrunnable."""
    binstub = shutil.which("agent-codespaces")
    if not binstub:
        return None
    try:
        return subprocess.run(
            [binstub, *args],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_creationflags(),
        )
    except Exception as exc:  # binstub vanished / exec error
        log.debug("agent-codespaces %s failed to run: %s", args[:2], exc)
        return None


def _run_worktrees(args: list[str], *, cwd: str | None = None,
                   timeout: float = 300.0):
    """Run ``agent-worktrees <args>`` (optionally in ``cwd``); None if unrunnable.

    ``agent-worktrees`` resolves its project from the current directory, so a
    cross-project reclaim runs the binstub with ``cwd`` set to the child's repo
    anchor.
    """
    binstub = shutil.which("agent-worktrees")
    if not binstub:
        return None
    try:
        return subprocess.run(
            [binstub, *args], cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
            creationflags=_creationflags(),
        )
    except Exception as exc:
        log.debug("agent-worktrees %s failed to run: %s", args[:2], exc)
        return None


def _looks_gone(output: str) -> bool:
    """Heuristic: does a failed delete mean the CodeSpace is already gone?

    ``agent-codespaces delete`` on a non-existent box exits non-zero with an
    HTTP 404 / Not Found from ``gh`` -- the resource is *already* reclaimed, so
    the obligation is discharged. Any other failure is a genuine failure.
    """
    low = output.lower()
    return "404" in low or "not found" in low or "could not resolve" in low


def reclaim_codespace(name: str, *, apply: bool) -> ReclaimResult:
    """Reclaim an orphaned CodeSpace by deleting it (best-effort, idempotent).

    In dry-run (``apply=False``) reports the intent without acting. On apply,
    shells ``agent-codespaces delete <name> --force`` (keeps the graceful
    pre-delete Copilot-session recovery, skips the interactive prompt). Exit 0
    -> reclaimed; a 404/not-found -> already gone -> reclaimed (idempotent); any
    other outcome -> failed (entry retained).
    """
    if not name:
        return ReclaimResult("failed", "orphan entry has no CodeSpace name")
    if not apply:
        return ReclaimResult("reclaimed", f"would delete CodeSpace {name}")
    proc = _run_codespaces(["delete", name, "--force"])
    if proc is None:
        return ReclaimResult("failed", "agent-codespaces binstub unavailable")
    if proc.returncode == 0:
        return ReclaimResult("reclaimed", f"deleted CodeSpace {name}")
    combined = f"{proc.stdout}\n{proc.stderr}"
    if _looks_gone(combined):
        return ReclaimResult("reclaimed", f"CodeSpace {name} already gone (404)")
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = tail[-1] if tail else f"delete exited {proc.returncode}"
    return ReclaimResult("failed", f"delete failed: {detail}")


def reclaim_worktree(
    ref: str, config: cfg.Config, *, apply: bool,
) -> ReclaimResult:
    """Reclaim an orphaned cross-repo worktree by finalizing it (best-effort).

    The ``ref`` is a qualified worktree ClaimRef (``machine/project/worktree_id``).
    On apply, shells ``agent-worktrees finalize <worktree_id> --abandon --json``
    **from the child project's repo anchor** (agent-worktrees resolves its
    project from cwd; there is no ``--project`` flag). ``--abandon`` bypasses only
    the child's own *obligation* gate (cascading any grandchild obligations back
    to the orphanage) -- the content-on-upstream **safety** check is always
    enforced, so an unmerged child is never reclaimed (finalize refuses ->
    ``failed``, entry retained for a human). A child that is already gone
    finalizes trivially (-> reclaimed, idempotent).
    """
    parsed = tracking.parse_claim_ref(ref)
    if parsed is None or not parsed.worktree_id:
        return ReclaimResult("failed", f"unparseable worktree ref {ref!r}")
    repo = sweep_mod.repo_for_project(parsed.project, config)
    if repo is None:
        return ReclaimResult(
            "failed", f"cannot resolve project {parsed.project!r} on this machine")
    if not apply:
        return ReclaimResult(
            "reclaimed",
            f"would finalize worktree {parsed.worktree_id} in {parsed.project}")
    proc = _run_worktrees(
        ["finalize", parsed.worktree_id, "--abandon", "--json"],
        cwd=repo.anchor)
    if proc is None:
        return ReclaimResult("failed", "agent-worktrees binstub unavailable")
    if proc.returncode == 0:
        return ReclaimResult(
            "reclaimed", f"finalized worktree {parsed.worktree_id}")
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = tail[-1] if tail else f"finalize exited {proc.returncode}"
    return ReclaimResult("failed", f"finalize refused: {detail}")


#: Kinds this consumer knows how to dispose of. Others are surfaced as
#: ``unsupported`` (entry retained) until a reclaimer is wired.
_RECLAIMERS = {
    "codespace": lambda ref, apply, config: reclaim_codespace(ref, apply=apply),
    "worktree": lambda ref, apply, config: reclaim_worktree(
        ref, config, apply=apply),
}


def reclaim_orphan(
    entry: dict, config: cfg.Config, *, apply: bool,
) -> ReclaimResult:
    """Dispatch one orphanage entry to its kind-specific reclaimer.

    Same-machine guard first: an entry naming a different ``machine`` is
    ``skipped`` (its resource must be reclaimed on that box). An unknown kind is
    ``unsupported``. Everything is best-effort -- a reclaimer never raises.
    """
    machine = entry.get("machine")
    this_machine = getattr(config, "machine", None)
    if machine and this_machine and machine != this_machine:
        return ReclaimResult(
            "skipped", f"cross-machine (owned by {machine}); "
                       f"run 'claims cleanup' there")
    kind = entry.get("kind")
    reclaimer = _RECLAIMERS.get(kind or "")
    if reclaimer is None:
        return ReclaimResult(
            "unsupported", f"no reclaimer for kind {kind!r} yet")
    try:
        return reclaimer(entry.get("ref") or "", apply, config)
    except Exception as exc:  # a reclaimer must never break the pass
        log.debug("reclaim of %s failed: %s", entry.get("ref"), exc)
        return ReclaimResult("failed", f"reclaimer error: {exc}")


def cleanup_orphanage(
    config: cfg.Config, *, apply: bool, project: str | None = None,
) -> list[dict]:
    """Run the cleanup consumer over the durable orphanage.

    Reclaims every actionable entry (see :func:`reclaim_orphan`) and, on apply,
    drops the successfully-reclaimed entries from the registry via
    :func:`tracking.remove_orphaned_obligations`. Returns one result row per
    entry: ``{kind, ref, source_worktree, status, detail}`` -- so a caller (the
    ``claims cleanup`` verb) can render text or JSON. Best-effort throughout.
    """
    rows: list[dict] = []
    reclaimed_keys: list[tuple[str | None, str | None]] = []
    for entry in tracking.load_orphaned_obligations(project):
        result = reclaim_orphan(entry, config, apply=apply)
        rows.append({
            "kind": entry.get("kind"),
            "ref": entry.get("ref"),
            "source_worktree": entry.get("source_worktree"),
            "status": result.status,
            "detail": result.detail,
        })
        if apply and result.reclaimed:
            reclaimed_keys.append(
                (entry.get("source_worktree"), entry.get("ref")))
    if apply and reclaimed_keys:
        tracking.remove_orphaned_obligations(reclaimed_keys, project=project)
    return rows
