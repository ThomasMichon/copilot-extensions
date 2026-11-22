"""Manual, reversible checkout dehydration (Phase 3 of worktree dehydration).

Dehydration removes a *clean* managed linked checkout while preserving the
worktree's identity, branch, tracking record and agency. The durable reverse
map is the :mod:`checkout_hydration_catalog` entry, written before removal and
retired only after a verified rehydration. Nothing here finalizes, unpairs,
releases claims or deletes branches; automatic triggers (Phase 4) and UI
(Phase 5) build on these entry points.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from . import checkout_hydration_catalog as catalog
from . import config as cfg

SCHEMA = "agent-worktrees.checkout-hydration.v1"
_IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply")


class HydrationRefused(ValueError):
    """A structured refusal; ``code`` is stable for callers and tests."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "error": str(self), **self.details}


def capabilities() -> dict[str, Any]:
    return {
        "schema": SCHEMA, "version": 1, "catalog_schema": catalog.SCHEMA,
        "catalog_schema_version": catalog.SCHEMA_VERSION,
        "operations": ["dehydrate", "rehydrate", "reconcile", "recover"],
        "trigger": "manual", "automatic": False,
        "scope": "clean-managed-linked-checkout-on-recorded-branch",
        "refuses": [
            "anchor-or-unmanaged", "missing-checkout", "branch-mismatch", "detached-head",
            "operation-in-progress", "submodules", "sparse-checkout", "dirty-checkout",
            "retained-data", "live-session",
        ],
        "ignored_files": "removed-without-force",
    }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    from . import git_ops

    return git_ops.git(
        "--no-optional-locks", "-c", "core.fsmonitor=false", *args,
        cwd=cwd, check=check, timeout=60, kill_tree=True, isolated_repository=True,
    )


def _ledger(worktree_id: str, config: Any) -> Path:
    if not worktree_id or Path(worktree_id).name != worktree_id or worktree_id.startswith("@"):
        raise HydrationRefused("invalid-id", "dehydration requires one exact managed worktree ID")
    return cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"


def _repo(config: Any) -> tuple[Path, Path]:
    repo = config.default_repo
    return Path(repo.anchor), Path(repo.worktree_root)


def _load(ledger: Path) -> Any:
    from . import tracking

    if not ledger.exists():
        raise HydrationRefused("no-record", f"no tracking record at {ledger}")
    return tracking.load_record(ledger)


@contextmanager
def _locked(ledger: Path, worktree_root: Path) -> Iterator[None]:
    from . import launch_admission, worktree_lifecycle
    from .finalize_lock import FinalizeLock

    with FinalizeLock(
        worktree_root / ".finalize.lock", timeout=3,
        on_stale=lambda message: print(message, file=sys.stderr),
    ), worktree_lifecycle.guard(ledger), launch_admission.guard(ledger):
        yield


def _set_hydration(ledger: Path, value: dict[str, Any]) -> None:
    """Record the hydration state through the tracking record write owner."""
    from . import tracking

    with tracking._RecordLock(ledger, require_sidecar=True):
        record = tracking.load_record(ledger)
        previous = record.hydration if isinstance(record.hydration, dict) else {}
        revision = previous.get("revision", 0)
        record.hydration = {**value, "revision": (revision if isinstance(revision, int) else 0) + 1, "at": _now()}
        tracking._save_record_unlocked(record, ledger)


def _is_registered(anchor: Path, checkout: Path) -> bool:
    listing = _git(anchor, "worktree", "list", "--porcelain", "-z").stdout.split("\0")
    return any(
        field.startswith("worktree ") and catalog.same_path(field[9:], checkout) for field in listing
    )


def _branch_tip(anchor: Path, branch: str) -> str | None:
    result = _git(anchor, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}", check=False)
    tip = result.stdout.strip() if result.returncode == 0 else ""
    return tip or None


def _valid_checkout(checkout: Path, anchor: Path, branch: str) -> str | None:
    """HEAD of a valid linked checkout on ``branch`` registered to ``anchor``."""
    from . import retained_checkout

    if not checkout.exists():
        return None
    try:
        retained_checkout.require_git_checkout(checkout, anchor, branch)
        return _git(checkout, "rev-parse", "HEAD").stdout.strip()
    except Exception:
        return None


def _agency(record: Any, worktree_id: str) -> dict[str, Any]:
    """References only: no transcripts, prompts or credentials."""
    return {
        "status": record.status, "kind": record.kind, "title": record.title,
        "codename": record.codename, "owner_ref": record.owner_ref,
        "lifecycle_revision": record.lifecycle_revision,
        "sessions": [s.session_id for s in (record.sessions or [])],
        "head_session": record.head_session,
        "pair": {k: getattr(record, k) for k in ("pair_id", "pair_role", "pair_ref", "pair_kind")},
        "claims": [{"kind": c.kind, "ref": c.ref, "state": c.state} for c in record.resources],
        "prs": [{"state": p.state, "branch": p.branch, "head_sha": p.head_sha} for p in record.prs],
        "worktree_data": {"project": record.repo, "worktree": worktree_id},
    }


def _gitdir_has(checkout: Path, name: str) -> bool:
    return Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-path", name).stdout.strip()).exists()


def _require_clean(checkout: Path) -> None:
    status = _git(
        checkout, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none",
        check=False,
    )
    if status.returncode != 0:
        raise HydrationRefused("status-unavailable", "git status failed; checkout retained")
    if status.stdout.strip("\0"):
        entries = [e for e in status.stdout.split("\0") if e][:10]
        raise HydrationRefused("dirty-checkout", "checkout has uncommitted or untracked changes", entries=entries)


def _worktree_config(checkout: Path) -> list[list[str]]:
    enabled = _git(checkout, "config", "--bool", "extensions.worktreeConfig", check=False).stdout.strip()
    if enabled != "true":
        return []
    listing = _git(checkout, "config", "--worktree", "--null", "--list", check=False)
    entries: list[list[str]] = []
    for item in (listing.stdout.split("\0") if listing.returncode == 0 else []):
        if item:
            key, _, value = item.partition("\n")
            entries.append([key, value])
    return entries


def _inspect(record: Any, checkout: Path, anchor: Path) -> dict[str, Any]:
    """Every dehydration gate; returns the git snapshot when admissible."""
    from . import checkout_data, procs, retained_checkout, sessions

    if not record.checkout_managed or record.kind != "session":
        raise HydrationRefused("unmanaged", "dehydration requires a managed session worktree checkout")
    if record.status == "finalizing":
        raise HydrationRefused("finalizing", "worktree is being finalized")
    if not checkout.is_dir():
        raise HydrationRefused("missing-checkout", f"checkout is absent: {checkout}")
    head_ref = _git(checkout, "symbolic-ref", "-q", "HEAD", check=False)
    if head_ref.returncode != 0:
        raise HydrationRefused("detached-head", "checkout HEAD is detached")
    if head_ref.stdout.strip() != f"refs/heads/{record.branch}":
        raise HydrationRefused(
            "branch-mismatch", "checkout is not on its recorded branch",
            expected=record.branch, actual=head_ref.stdout.strip(),
        )
    try:
        retained_checkout.require_git_checkout(checkout, anchor, record.branch)
    except Exception as exc:
        raise HydrationRefused("not-linked-checkout", str(exc)) from exc
    head = _git(checkout, "rev-parse", "--verify", "HEAD^{commit}", check=False)
    if head.returncode != 0:
        raise HydrationRefused("unborn-branch", "checkout branch has no commit")
    for marker in _IN_PROGRESS:
        if _gitdir_has(checkout, marker):
            raise HydrationRefused("operation-in-progress", f"git operation in progress ({marker})")
    gitlinks = _git(checkout, "ls-files", "-s", "-z").stdout.split("\0")
    if (checkout / ".gitmodules").exists() or any(e.startswith("160000 ") for e in gitlinks):
        raise HydrationRefused("submodules", "checkouts with submodules are not supported")
    sparse = _git(checkout, "config", "--bool", "core.sparseCheckout", check=False).stdout.strip()
    if sparse == "true" or _gitdir_has(checkout, "info/sparse-checkout"):
        raise HydrationRefused("sparse-checkout", "sparse checkouts are not supported")
    hold = checkout_data.preservation_hint(checkout)
    if hold:
        raise HydrationRefused("retained-data", hold)
    _require_clean(checkout)
    verdict = sessions.verify_worktree_active(record, strict=True)
    if not verdict.probes_ok or verdict.active:
        raise HydrationRefused("live-session", "dehydration requires confirmed inactive sessions")
    blocker = procs.checkout_retirement_blocker(str(checkout))
    if blocker:
        raise HydrationRefused("live-process", blocker)
    return {"branch": record.branch, "head": head.stdout.strip(), "worktree_config": _worktree_config(checkout)}


def plan(worktree_id: str, config: Any) -> dict[str, Any]:
    """Read-only admission check; never writes the catalog."""
    ledger = _ledger(worktree_id, config)
    anchor, _root = _repo(config)
    record = _load(ledger)
    git = _inspect(record, Path(record.worktree_path), anchor)
    return {"schema": SCHEMA, "worktree_id": worktree_id, "admissible": True, "git": git}


def dehydrate(worktree_id: str, config: Any) -> dict[str, Any]:
    ledger = _ledger(worktree_id, config)
    anchor, root = _repo(config)
    with _locked(ledger, root):
        record = _load(ledger)
        entry_path = catalog.entry_path(root, worktree_id)
        existing = catalog.read_entry(entry_path)
        if existing is not None:
            report = _reconcile_locked(worktree_id, ledger, anchor, entry_path, existing)
            if report["state"] == catalog.STATE_DEHYDRATED:
                return {**report, "complete": True, "already": True}
            if report["state"] != catalog.STATE_HYDRATED:
                raise HydrationRefused("hold", "dehydration catalog needs inspection", report=report)
            record = _load(ledger)
        checkout = Path(record.worktree_path)
        git = _inspect(record, checkout, anchor)
        entry: dict[str, Any] = {
            "schema": catalog.SCHEMA, "schema_version": catalog.SCHEMA_VERSION,
            "operation_id": catalog.new_operation_id(), "stage": catalog.STAGE_REMOVING,
            "prior_state": {"status": record.status, "hydration": catalog.STATE_HYDRATED},
            "created_at": _now(),
            "identity": {
                "repo": record.repo, "worktree_id": worktree_id, "creation_nonce": record.creation_nonce,
                "checkout_path": str(checkout), "anchor": str(anchor), "catalog_path": str(entry_path),
            },
            "git": git,
            "agency": _agency(record, worktree_id),
        }
        catalog.write_entry(entry_path, entry)
        _set_hydration(ledger, {
            "state": catalog.STAGE_REMOVING, "catalog": str(entry_path), "operation_id": entry["operation_id"],
        })
        try:
            _require_clean(checkout)
        except HydrationRefused:
            catalog.delete_entry(entry_path)
            _set_hydration(ledger, {"state": catalog.STATE_HYDRATED})
            raise
        removed = _git(anchor, "worktree", "remove", str(checkout), check=False)
        if removed.returncode != 0:
            _abandon_removal(ledger, entry_path, checkout, anchor, record.branch, removed.stderr)
        if checkout.exists() or _is_registered(anchor, checkout):
            raise HydrationRefused("removal-unverified", "checkout still present after removal; catalog retained")
        entry["stage"] = catalog.STAGE_DEHYDRATED
        entry["dehydrated_at"] = _now()
        catalog.write_entry(entry_path, entry)
        _set_hydration(ledger, {
            "state": catalog.STAGE_DEHYDRATED, "catalog": str(entry_path), "operation_id": entry["operation_id"],
            "head": git["head"],
        })
        return {
            "schema": SCHEMA, "worktree_id": worktree_id, "state": catalog.STATE_DEHYDRATED,
            "complete": True, "catalog": str(entry_path), "branch": git["branch"], "head": git["head"],
        }


def _abandon_removal(ledger: Path, entry_path: Path, checkout: Path, anchor: Path, branch: str, stderr: str) -> NoReturn:
    if _valid_checkout(checkout, anchor, branch):
        catalog.delete_entry(entry_path)
        _set_hydration(ledger, {"state": catalog.STATE_HYDRATED})
    raise HydrationRefused("remove-failed", f"git worktree remove refused: {stderr.strip()}")


def rehydrate(worktree_id: str, config: Any) -> dict[str, Any]:
    ledger = _ledger(worktree_id, config)
    anchor, root = _repo(config)
    with _locked(ledger, root):
        _load(ledger)
        entry_path = catalog.entry_path(root, worktree_id)
        entry = catalog.read_entry(entry_path)
        if entry is None:
            raise HydrationRefused("not-dehydrated", "no dehydration catalog entry for this worktree")
        report = _reconcile_locked(worktree_id, ledger, anchor, entry_path, entry)
        if report["state"] == catalog.STATE_HYDRATED:
            return {**report, "complete": True, "already": True}
        if report["state"] != catalog.STATE_DEHYDRATED:
            raise HydrationRefused("hold", "dehydration catalog needs inspection", report=report)
        entry = catalog.read_entry(entry_path) or entry
        checkout = Path(entry["identity"]["checkout_path"])
        branch, head = entry["git"]["branch"], entry["git"]["head"]
        tip = _branch_tip(anchor, branch)
        if tip != head:
            raise HydrationRefused(
                "branch-tip-conflict", "branch tip no longer matches the dehydrated snapshot",
                branch=branch, expected=head, actual=tip,
            )
        if checkout.exists() and (not checkout.is_dir() or any(checkout.iterdir())):
            raise HydrationRefused("destination-occupied", f"checkout path is not empty: {checkout}")
        entry["stage"] = catalog.STAGE_REHYDRATING
        catalog.write_entry(entry_path, entry)
        _set_hydration(ledger, {"state": catalog.STAGE_REHYDRATING, "catalog": str(entry_path)})
        added = _git(anchor, "worktree", "add", str(checkout), branch, check=False)
        if added.returncode != 0:
            entry["stage"] = catalog.STAGE_DEHYDRATED
            catalog.write_entry(entry_path, entry)
            _set_hydration(ledger, {"state": catalog.STAGE_DEHYDRATED, "catalog": str(entry_path), "head": head})
            raise HydrationRefused("add-failed", f"git worktree add failed: {added.stderr.strip()}")
        return _complete_rehydrate(worktree_id, ledger, anchor, entry_path, entry)


def _complete_rehydrate(worktree_id: str, ledger: Path, anchor: Path, entry_path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    checkout = Path(entry["identity"]["checkout_path"])
    branch = entry["git"]["branch"]
    actual = _valid_checkout(checkout, anchor, branch)
    if actual is None:
        raise HydrationRefused("rehydrate-unverified", "rehydrated checkout failed verification; catalog retained")
    for key, value in entry["git"].get("worktree_config") or []:
        _git(checkout, "config", "--worktree", key, value)
    _set_hydration(ledger, {"state": catalog.STATE_HYDRATED})
    catalog.delete_entry(entry_path)
    return {
        "schema": SCHEMA, "worktree_id": worktree_id, "state": catalog.STATE_HYDRATED, "complete": True,
        "checkout": str(checkout), "branch": branch, "head": actual,
        "head_advanced": actual != entry["git"]["head"],
    }


def _hold(worktree_id: str, entry: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "worktree_id": worktree_id, "state": "hold", "reason": reason,
        "stage": entry.get("stage") if entry else None,
    }


def _reconcile_locked(
    worktree_id: str, ledger: Path, anchor: Path, entry_path: Path, entry: dict[str, Any],
) -> dict[str, Any]:
    checkout = Path(entry["identity"]["checkout_path"])
    branch, stage = entry["git"]["branch"], entry["stage"]
    valid = _valid_checkout(checkout, anchor, branch)
    base = {"schema": SCHEMA, "worktree_id": worktree_id, "stage": stage}
    if valid is not None:
        if stage == catalog.STAGE_REMOVING and valid == entry["git"]["head"]:
            catalog.delete_entry(entry_path)
            _set_hydration(ledger, {"state": catalog.STATE_HYDRATED})
            return {**base, "state": catalog.STATE_HYDRATED, "reconciled": "interrupted-dehydrate-rolled-back"}
        if stage == catalog.STAGE_REMOVING:
            return _hold(worktree_id, entry, "checkout changed during an interrupted dehydration")
        # A valid checkout always wins over a stale snapshot; never remove it.
        report = _complete_rehydrate(worktree_id, ledger, anchor, entry_path, entry)
        return {**report, "stage": stage, "reconciled": "interrupted-rehydrate-completed"}
    if checkout.exists() and (not checkout.is_dir() or any(checkout.iterdir())):
        return _hold(worktree_id, entry, "checkout path holds a partial or foreign checkout")
    if _is_registered(anchor, checkout):
        return _hold(worktree_id, entry, "git still registers the absent checkout")
    if stage != catalog.STAGE_DEHYDRATED:
        entry["stage"] = catalog.STAGE_DEHYDRATED
        entry.setdefault("dehydrated_at", _now())
        catalog.write_entry(entry_path, entry)
    _set_hydration(ledger, {
        "state": catalog.STAGE_DEHYDRATED, "catalog": str(entry_path),
        "operation_id": entry.get("operation_id"), "head": entry["git"]["head"],
    })
    reconciled = {catalog.STAGE_REMOVING: "interrupted-dehydrate-completed",
                  catalog.STAGE_REHYDRATING: "interrupted-rehydrate-reverted"}.get(stage)
    return {**base, "state": catalog.STATE_DEHYDRATED, **({"reconciled": reconciled} if reconciled else {})}


def reconcile(worktree_id: str, config: Any) -> dict[str, Any]:
    """Settle an interrupted transition; never removes a valid checkout."""
    ledger = _ledger(worktree_id, config)
    anchor, root = _repo(config)
    with _locked(ledger, root):
        record = _load(ledger)
        entry_path = catalog.entry_path(root, worktree_id)
        try:
            entry = catalog.read_entry(entry_path)
        except catalog.CatalogError as exc:
            return _hold(worktree_id, None, str(exc))
        if entry is None:
            if catalog.is_dehydrated(record):
                return _hold(worktree_id, None, "record claims dehydration but the catalog entry is missing")
            return {"schema": SCHEMA, "worktree_id": worktree_id, "state": catalog.STATE_HYDRATED}
        return _reconcile_locked(worktree_id, ledger, anchor, entry_path, entry)


def recover(config: Any, *, apply: bool = False) -> dict[str, Any]:
    """Report (and optionally rebuild) tracking from catalog entries.

    Only a catalog whose branch still points at its snapshot HEAD is
    reconstructable; the rebuilt record is non-active (``complete``) and held
    dehydrated, so it never becomes a live owner. Everything else is a hold.
    """
    from . import tracking

    anchor, root = _repo(config)
    tracking_dir = cfg.tracking_dir(config.repo_name)
    results: list[dict[str, Any]] = []
    for entry_path in catalog.iter_entries(root):
        item: dict[str, Any] = {"catalog": str(entry_path), "worktree_id": entry_path.stem}
        results.append(item)
        try:
            entry = catalog.read_entry(entry_path)
        except catalog.CatalogError as exc:
            item.update(verdict="hold", reason=str(exc))
            continue
        if entry is None:
            continue
        identity, git = entry["identity"], entry["git"]
        item.update(stage=entry["stage"], branch=git["branch"], head=git["head"])
        if identity["worktree_id"] != entry_path.stem or identity["repo"] != config.repo_name:
            item.update(verdict="hold", reason="catalog identity does not match its location or project")
            continue
        ledger = tracking_dir / f"{entry_path.stem}.yaml"
        if ledger.exists():
            item["verdict"] = "tracked"
            continue
        if entry["stage"] != catalog.STAGE_DEHYDRATED or _branch_tip(anchor, git["branch"]) != git["head"]:
            item.update(verdict="hold", reason="branch/stage evidence contradicts the catalog snapshot")
            continue
        item["verdict"] = "reconstructable"
        if apply:
            agency = entry.get("agency") or {}
            now = _now()
            record = tracking.WorktreeRecord(
                worktree_id=identity["worktree_id"], branch=git["branch"], worktree_path=identity["checkout_path"],
                repo=identity["repo"], machine="", platform="", started_at=entry.get("created_at") or now,
                last_resumed_at=now, resume_count=0, title=agency.get("title"), status="complete",
                completed_at=now, creation_nonce=identity.get("creation_nonce") or "",
                hydration={"state": catalog.STAGE_DEHYDRATED, "catalog": str(entry_path),
                           "recovered": True, "revision": 1, "at": now},
            )
            tracking.save_record(record, ledger)
            item["applied"] = True
    return {"schema": SCHEMA, "catalog_dir": str(catalog.catalog_dir(root)), "entries": results, "apply": apply}
