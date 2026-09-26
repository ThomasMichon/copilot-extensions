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

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_procutil import no_window_flags

from . import config as cfg
from . import repos as repos_mod
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


def _run_codespaces(args: list[tuple[str, bool]], *, timeout: float = 660.0):
    """Run agent-codespaces via the identity-verified ``codespace:``
    claim-provider registry (claim-provider-pattern effort); return the
    process, or None if unrunnable. Resolves the provider's own
    payload-local binstub -- never an ambient ``PATH`` lookup -- closing the
    tier-3 -> tier-8 upward call this effort exists to fix. Degrades
    identically to the prior ``shutil.which`` behavior when no provider is
    registered (e.g. agent-codespaces not installed): returns None.

    ``args`` is an ordered ``(token, trusted)`` sequence -- ``trusted=True``
    for a static/literal subcommand name or flag this module itself wrote
    (e.g. ``"claim-reclaim"``, ``"--apply"``), ``trusted=False`` for a
    persisted value (a CodeSpace name). Passed straight through to
    ``claim_providers.build_provider_argv``, which validates every
    ``trusted=False`` token before it ever reaches a possibly-cmd.exe-wrapped
    argv (see that helper's own docstring for why a leading-dash heuristic
    would be unsafe here).

    Default timeout (660s, matching
    ``claim_providers._RECLAIM_CALLBACK_TIMEOUT_SECONDS``) budgets for
    ``agent-codespaces claim-reclaim``'s own internal chain: pre-delete
    session recovery (boot up to 180s, THEN pull up to 300s, THEN the
    session-sync-push subprocess up to 60s) THEN the delete subprocess
    itself (a further 60s timeout) -- every phase must complete in FULL
    sequence within this call's own timeout, not just the first, with
    margin left over.
    """
    from . import claim_providers

    providers, _findings = claim_providers.discover_claim_providers()
    provider = providers.get("codespace")
    if provider is None:
        return None
    full_argv = claim_providers.build_provider_argv_for_manifest(
        provider,
        *args,
        kind="reclaim",
    )
    if full_argv is None:
        return None
    proc = claim_providers._run_provider_process(
        provider,
        callback_args=tuple(token for token, _trusted in args),
        legacy_command=full_argv,
        timeout=timeout,
    )
    if proc is None:
        log.debug("agent-codespaces %s failed to run", [t for t, _ in args][:2])
        return None
    return proc


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


def reclaim_codespace(name: str, *, apply: bool) -> ReclaimResult:
    """Reclaim an orphaned CodeSpace via the provider's own ``claim-reclaim``
    callback (best-effort, idempotent).

    In dry-run (``apply=False``) reports the intent without acting. On
    apply, invokes ``agent-codespaces claim-reclaim <name> --apply`` --
    NOT the legacy human-facing ``delete ... --force`` shape this used to
    shell directly, which bypassed the callback's own lease guard and
    pre-delete session-recovery gate (claim-provider-pattern effort review
    finding: "Invoke claim-reclaim instead of the legacy delete command").
    Parses the callback's small JSON envelope (``{"reclaimed": bool,
    "detail": str}``, matching ``agent_worktrees.claim_providers``'
    ``resolve_claim_reclaim`` contract) rather than grepping free-text
    stdout for a 404/not-found heuristic -- the callback already resolves
    idempotent-already-gone itself and reports it as ``reclaimed: true``.
    """
    if not name:
        return ReclaimResult("failed", "orphan entry has no CodeSpace name")
    if not apply:
        return ReclaimResult("reclaimed", f"would delete CodeSpace {name}")
    proc = _run_codespaces([("claim-reclaim", True), (name, False), ("--apply", True)])
    if proc is None:
        return ReclaimResult("failed", "agent-codespaces binstub unavailable")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else f"claim-reclaim exited {proc.returncode}"
        return ReclaimResult("failed", f"claim-reclaim failed: {detail}")
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return ReclaimResult(
            "failed", f"claim-reclaim returned invalid JSON: {proc.stdout.strip()!r}"
        )
    if not isinstance(data, dict) or not isinstance(data.get("reclaimed"), bool):
        return ReclaimResult(
            "failed", f"claim-reclaim returned an unexpected shape: {proc.stdout.strip()!r}"
        )
    detail = data.get("detail") or (
        f"reclaimed CodeSpace {name}" if data["reclaimed"] else f"could not reclaim CodeSpace {name}"
    )
    return ReclaimResult("reclaimed" if data["reclaimed"] else "failed", detail)


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
    this_machine = getattr(config, "machine", None)
    if parsed.machine and this_machine and parsed.machine != this_machine:
        return ReclaimResult(
            "skipped", f"cross-machine worktree (owned by {parsed.machine}); "
                       "run claims cleanup there")
    repo = sweep_mod.repo_for_project(parsed.project, config)
    anchor = getattr(repo, "anchor", None) if repo is not None else None
    if not anchor and parsed.project:
        # The orphanage belongs to the closing project, so its loaded config
        # need not enumerate every cross-project child. Fall back to the global
        # repos registry -- the same source `repos find` / project binstubs use.
        entry = repos_mod.find_repo(parsed.project)
        candidate = entry.local_path() if entry is not None else None
        if candidate and Path(candidate).is_dir():
            anchor = candidate
    if not anchor:
        return ReclaimResult(
            "failed", f"cannot resolve project {parsed.project!r} on this machine")
    if not apply:
        return ReclaimResult(
            "reclaimed",
            f"would finalize worktree {parsed.worktree_id} in {parsed.project}")
    proc = _run_worktrees(
        ["finalize", parsed.worktree_id, "--abandon",
         "--handoff-to", "claims-cleanup", "--json"],
        cwd=anchor)
    if proc is None:
        return ReclaimResult("failed", "agent-worktrees binstub unavailable")
    if proc.returncode == 0:
        try:
            nested = [
                entry for entry in tracking.load_orphaned_obligations_strict(
                    parsed.project)
                if entry.get("source_worktree") == parsed.worktree_id
                and (entry.get("handoff_to") or "").strip() == "claims-cleanup"
            ]
        except Exception as exc:
            return ReclaimResult(
                "failed", f"child orphanage is unreadable; handoff acceptance "
                          f"cannot be proven: {exc}")
        if nested:
            return ReclaimResult(
                "failed",
                f"finalized child but {len(nested)} nested obligation(s) await "
                f"handoff acceptance; from {parsed.project} run: "
                f"agent-worktrees claims cleanup "
                f"{parsed.worktree_id} --apply, then retry this cleanup")
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
    selectors: set[str] | None = None,
) -> list[dict]:
    """Run the cleanup consumer over the durable orphanage.

    With ``selectors``, acts only on entries whose exact resource ``ref`` OR
    ``source_worktree`` matches, so one closing agent can discharge its own
    abandoned obligations without touching unrelated orphanage residents.
    Otherwise reclaims every actionable entry (see :func:`reclaim_orphan`).
    On apply, drops the successfully-reclaimed entries from the registry via
    :func:`tracking.remove_orphaned_obligations`. Returns one result row per
    entry: ``{kind, ref, source_worktree, status, detail}`` -- so a caller (the
    ``claims cleanup`` verb) can render text or JSON. Best-effort throughout.
    """
    rows: list[dict] = []
    reclaimed_keys: list[tuple[str | None, str | None]] = []
    entries = tracking.load_orphaned_obligations(project)
    if selectors:
        entries = [
            entry for entry in entries
            if entry.get("ref") in selectors
            or entry.get("source_worktree") in selectors
        ]
    for entry in entries:
        result = reclaim_orphan(entry, config, apply=apply)
        rows.append({
            "kind": entry.get("kind"),
            "ref": entry.get("ref"),
            "source_worktree": entry.get("source_worktree"),
            "handoff_to": entry.get("handoff_to"),
            "status": result.status,
            "detail": result.detail,
        })
        if apply and result.reclaimed:
            reclaimed_keys.append(
                (entry.get("source_worktree"), entry.get("ref")))
    if apply and reclaimed_keys:
        tracking.remove_orphaned_obligations(reclaimed_keys, project=project)
    return rows
