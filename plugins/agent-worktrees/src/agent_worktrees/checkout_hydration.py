"""Manual, reversible checkout dehydration (Phase 3 of worktree dehydration).

Dehydration removes a *clean* managed linked checkout while preserving the
worktree's identity, branch, tracking record and agency. The durable reverse
map is the :mod:`checkout_hydration_catalog` entry, written before removal and
retired only after a verified rehydration. Nothing here finalizes, unpairs,
releases claims or deletes branches. Automatic post-exit triggers and
hydrate-before-entry (Phase 4) live in :mod:`checkout_hydration_auto`; UI
(Phase 5) builds on these entry points.
"""

from __future__ import annotations

import base64
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NoReturn

from machine_transport import detect_platform

from . import checkout_hydration_catalog as catalog
from . import config as cfg

SCHEMA = "agent-worktrees.checkout-hydration.v1"
_IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply",
    # A multi-commit cherry-pick/revert leaves a clean tree between steps.
    "sequencer",
)


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
        "operations": ["dehydrate", "rehydrate", "reconcile", "recover", "release-recovery-hold"],
        # Automatic post-exit dehydration is supported but policy-gated (default manual);
        # managed entry always hydrates before resume.
        "trigger": ["manual", "automatic-post-exit"], "automatic": True,
        "automatic_policy": {"key": "checkout_dehydration.mode", "default": "manual"},
        "entry_hydration": True,
        "scope": "clean-managed-linked-checkout-on-recorded-branch",
        "refuses": [
            "anchor-or-unmanaged", "missing-checkout", "branch-mismatch", "detached-head",
            "operation-in-progress", "submodules", "sparse-checkout", "dirty-checkout",
            "hidden-index-flags", "retained-data", "live-session", "mutation-refused",
            "entry-pending",
        ],
        "ignored_files": "removed-without-force",
        "materialization": "add-no-checkout-then-config-then-reset",
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


PAIR_READINESS = "pair-readiness-pending.json"


def pair_preparation_required(record: Any) -> bool:
    """A harness pair (worktree or anchor sibling) composes ignored knowledge settings a restore must regenerate."""
    return getattr(record, "pair_role", None) == "harness" and getattr(record, "pair_kind", None) in ("worktree", "anchor")


def pair_readiness_marker(ledger: Path) -> Path:
    """The per-worktree preparation-needed marker, cleared only by successful paired entry."""
    return ledger.parent / ledger.stem / PAIR_READINESS


def _mark_pair_pending(record: Any, ledger: Path, worktree_id: str) -> None:
    """Persist preparation-needed state before a restore retires a paired catalog entry."""
    if not pair_preparation_required(record):
        return
    from .checkout_hydration_auto import _write_json

    try:
        _write_json(pair_readiness_marker(ledger), {"version": 1, "worktree_id": worktree_id, "at": time.time()})
    except OSError as exc:
        raise HydrationRefused(
            "pair-readiness-unrecorded", f"cannot record pending pair preparation; catalog retained: {exc}",
        ) from exc


def _repo(config: Any) -> tuple[Path, Path]:
    repo = config.default_repo
    return Path(repo.anchor), Path(repo.worktree_root)


def _load(ledger: Path) -> Any:
    from . import tracking

    if not ledger.exists():
        raise HydrationRefused("no-record", f"no tracking record at {ledger}")
    return tracking.load_record(ledger)


def _admit(record: Any, config: Any) -> None:
    """Receiving-side mutation admission; call under the shared guards."""
    from . import execution_spaces

    try:
        execution_spaces.require_record_mutation(record, config)
    except execution_spaces.ExecutionSpaceError as exc:
        raise HydrationRefused("mutation-refused", f"record mutation is not admitted here: {exc}") from exc


def _load_admitted(ledger: Path, config: Any) -> Any:
    record = _load(ledger)
    _admit(record, config)
    return record


@contextmanager
def _locked(ledger: Path, worktree_root: Path) -> Iterator[None]:
    from . import launch_admission, worktree_lifecycle
    from .finalize_lock import FinalizeLock

    with FinalizeLock(
        worktree_root / ".finalize.lock", timeout=3,
        on_stale=lambda message: print(message, file=sys.stderr),
    ), worktree_lifecycle.guard(ledger), launch_admission.guard(ledger):
        yield


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
    # status and `worktree remove` both trust assume-unchanged/skip-worktree, so
    # edits under those flags would pass as clean and be lost.
    listing = _git(checkout, "ls-files", "-v", "-z", check=False)
    if listing.returncode != 0:
        raise HydrationRefused("status-unavailable", "git ls-files failed; checkout retained")
    hidden = [e[2:] for e in listing.stdout.split("\0") if e and (e[0].islower() or e[0] == "S")]
    if hidden:
        raise HydrationRefused(
            "hidden-index-flags", "index hides tracked changes (assume-unchanged/skip-worktree)",
            entries=hidden[:10],
        )


def _worktree_config_entries(checkout: Path) -> list[list[str | None]]:
    """Ordered ``[key, value]`` pairs; ``None`` marks a valueless (implicit true) key."""
    listing = _git(checkout, "config", "--worktree", "--null", "--list", check=False)
    if listing.returncode != 0:
        raise HydrationRefused("worktree-config-unreadable", "per-worktree config could not be listed")
    entries: list[list[str | None]] = []
    for item in listing.stdout.split("\0"):
        if item:
            key, separator, value = item.partition("\n")
            entries.append([key, value if separator else None])
    return entries


def _worktree_config_file(checkout: Path) -> Path:
    return Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-path", "config.worktree").stdout.strip())


def _worktree_config(checkout: Path) -> dict[str, Any] | None:
    """Exact per-worktree config snapshot (raw file plus its parsed entries).

    Restoring the raw file keeps multi-valued keys, their order and valueless
    booleans exactly; the parsed entries verify the restoration.
    """
    enabled = _git(checkout, "config", "--bool", "extensions.worktreeConfig", check=False).stdout.strip()
    if enabled != "true":
        return None
    path = _worktree_config_file(checkout)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    return {
        "file_b64": base64.b64encode(raw).decode("ascii"),
        "entries": _worktree_config_entries(checkout),
    }


_CREDENTIAL_HTTP_KEYS = frozenset({"extraheader", "cookiefile", "sslkey", "sslcert", "sslcertpasswordprotected"})
_CREDENTIAL_SUFFIXES = ("password", "token", "secret")


def _is_credential_key(key: str) -> bool:
    lowered = key.lower()
    section, _, rest = lowered.partition(".")
    final = lowered.rsplit(".", 1)[-1]
    if section == "credential" and rest:
        return True
    if section == "http" and final in _CREDENTIAL_HTTP_KEYS:
        return True
    return final.endswith(_CREDENTIAL_SUFFIXES)


def _require_no_credential_config(checkout: Path) -> None:
    """Refuse when config.worktree holds credential-bearing keys (names only; values are never read)."""
    path = _worktree_config_file(checkout)
    if not path.exists():
        return
    names = _git(checkout, "config", "--file", str(path), "--null", "--list", "--name-only", check=False)
    if names.returncode != 0:
        raise HydrationRefused("worktree-config-unreadable", "per-worktree config could not be listed")
    offending = sorted({key for key in names.stdout.split("\0") if key and _is_credential_key(key)})
    if offending:
        raise HydrationRefused(
            "credential-config", "per-worktree config contains credential-bearing keys", keys=offending,
        )


def _restore_worktree_config(checkout: Path, snapshot: object) -> None:
    """Rewrite the recorded per-worktree config; idempotent and retry-safe."""
    from . import checkout_durability

    if not isinstance(snapshot, dict):
        return
    target = _worktree_config_file(checkout)
    staging = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    try:
        with staging.open("wb") as handle:
            handle.write(base64.b64decode(str(snapshot["file_b64"]), validate=True))
            handle.flush()
            os.fsync(handle.fileno())
        checkout_durability.replace(staging, target, existing=True)
    finally:
        if staging.exists():
            staging.unlink()
    if _worktree_config_entries(checkout) != snapshot.get("entries"):
        raise HydrationRefused("worktree-config-unverified", "restored per-worktree config does not match the snapshot")


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
    _require_no_credential_config(checkout)
    verdict = sessions.verify_worktree_active(record, strict=True)
    if not verdict.probes_ok or verdict.active:
        raise HydrationRefused("live-session", "dehydration requires confirmed inactive sessions")
    blocker = procs.checkout_retirement_blocker(str(checkout))
    if blocker:
        raise HydrationRefused("live-process", blocker)
    return {"branch": record.branch, "head": head.stdout.strip(), "worktree_config": _worktree_config(checkout)}


def _require_no_entry(ledger: Path) -> None:
    from . import checkout_hydration_auto

    if checkout_hydration_auto.entry_lease_pending(ledger):
        raise HydrationRefused(
            "entry-pending", "a managed launch or resume is entering this worktree; retry later",
            ttl_seconds=checkout_hydration_auto.ENTRY_LEASE_TTL,
        )


def plan(worktree_id: str, config: Any) -> dict[str, Any]:
    """Read-only admission check; never writes the catalog."""
    ledger = _ledger(worktree_id, config)
    anchor, _root = _repo(config)
    record = _load(ledger)
    git = _inspect(record, Path(record.worktree_path), anchor)
    return {"schema": SCHEMA, "worktree_id": worktree_id, "admissible": True, "git": git}


def _entry_location(record: Any, root: Path, worktree_id: str) -> Path:
    """The persisted catalog location (beside the recorded checkout).

    Falls back to the configured root only when an entry exists there, so a
    later worktree-root change still finds the entry written at dehydration.
    """
    recorded = catalog.record_entry_path(record)
    if recorded is not None and recorded.exists():
        return recorded
    configured = catalog.entry_path(root, worktree_id)
    if configured.exists() or recorded is None:
        return configured
    return recorded


def _identity_conflict(
    entry: dict[str, Any], entry_path: Path, record: Any, worktree_id: str, anchor: Path,
) -> str | None:
    identity, git = entry["identity"], entry["git"]
    checks = (
        (entry_path.stem == worktree_id and identity.get("worktree_id") == worktree_id, "worktree id"),
        (identity.get("repo") == record.repo, "repository"),
        (git.get("branch") == record.branch, "branch"),
        (catalog.same_path(str(identity.get("checkout_path") or ""), record.worktree_path), "checkout path"),
        ((identity.get("creation_nonce") or "") == (record.creation_nonce or ""), "creation nonce"),
        (catalog.same_path(str(identity.get("anchor") or ""), anchor), "anchor"),
    )
    for ok, label in checks:
        if not ok:
            return f"catalog {label} does not match the tracking record"
    return None


def _read_for(worktree_id: str, entry_path: Path) -> dict[str, Any] | None:
    try:
        return catalog.read_entry(entry_path)
    except catalog.CatalogError as exc:
        raise HydrationRefused("hold", "dehydration catalog needs inspection",
                               report=_hold(worktree_id, None, str(exc))) from exc


def dehydrate(
    worktree_id: str, config: Any, *, final_check: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """Remove a clean, retired checkout behind a published catalog entry.

    ``final_check`` (automatic callers) runs inside the lifecycle/admission
    locks after catalog publication, immediately before removal; a returned
    refusal code withdraws the entry and raises :class:`HydrationRefused`.
    """
    from . import checkout_data

    ledger = _ledger(worktree_id, config)
    anchor, root = _repo(config)
    with _locked(ledger, root):
        record = _load_admitted(ledger, config)
        entry_path = _entry_location(record, root, worktree_id)
        existing = _read_for(worktree_id, entry_path)
        if existing is not None:
            report = _reconcile_locked(worktree_id, record, anchor, entry_path, existing, ledger=ledger)
            if report["state"] == catalog.STATE_DEHYDRATED:
                return {**report, "complete": True, "already": True}
            if report["state"] != catalog.STATE_HYDRATED:
                raise HydrationRefused("hold", "dehydration catalog needs inspection", report=report)
            record = _load_admitted(ledger, config)
            entry_path = _entry_location(record, root, worktree_id)
        _require_no_entry(ledger)
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
        try:
            # The registry must name this root before its first entry exists.
            catalog.register_root(record.repo, entry_path.parent.parent)
            reservation = catalog.reservation_lock(anchor)
            reservation.__enter__()
        except (catalog.CatalogError, OSError, TimeoutError) as exc:
            raise HydrationRefused("catalog-unavailable", f"cannot publish the dehydration catalog: {exc}") from exc
        try:
            # Managed branch writes recheck the reservation under this lock, so none
            # can move or delete the branch between publication and removal.
            catalog.write_entry(entry_path, entry)
            try:
                # Entry publishes its lease before reading the catalog; checking it after
                # publishing ours means at least one side observes the other.
                _require_no_entry(ledger)
                refusal = final_check() if final_check is not None else None
                if refusal is not None:
                    # Seed staging and entry withdrawal serialize with these locks.
                    raise HydrationRefused(refusal, "the automatic request's final pre-removal check refused")
                _require_clean(checkout)
                # Retained data can appear while the registry and lock were awaited.
                hold = checkout_data.preservation_hint(checkout)
                if hold:
                    raise HydrationRefused("retained-data", hold)
                _require_no_credential_config(checkout)
            except HydrationRefused:
                catalog.delete_entry(entry_path)
                raise
            removed = _git(anchor, "worktree", "remove", str(checkout), check=False)
            if removed.returncode != 0:
                _abandon_removal(worktree_id, record, anchor, entry_path, entry, removed.stderr, ledger=ledger)
            if checkout.exists() or _is_registered(anchor, checkout):
                raise HydrationRefused("removal-unverified", "checkout still present after removal; catalog retained")
            entry["stage"] = catalog.STAGE_DEHYDRATED
            entry["dehydrated_at"] = _now()
            catalog.write_entry(entry_path, entry)
        finally:
            reservation.__exit__(None, None, None)
        return {
            "schema": SCHEMA, "worktree_id": worktree_id, "state": catalog.STATE_DEHYDRATED,
            "complete": True, "catalog": str(entry_path), "branch": git["branch"], "head": git["head"],
        }


def _abandon_removal(
    worktree_id: str, record: Any, anchor: Path, entry_path: Path, entry: dict[str, Any], stderr: str,
    *, ledger: Path,
) -> NoReturn:
    """A failed removal may have deleted some files: let reconciliation decide.

    Only a complete, clean checkout at the snapshot HEAD rolls back (catalog
    retired); anything else stays a hold with the catalog retained.
    """
    report = _reconcile_locked(worktree_id, record, anchor, entry_path, entry, ledger=ledger)
    raise HydrationRefused("remove-failed", f"git worktree remove refused: {stderr.strip()}", report=report)


def rehydrate(worktree_id: str, config: Any) -> dict[str, Any]:
    ledger = _ledger(worktree_id, config)
    anchor, root = _repo(config)
    with _locked(ledger, root):
        record = _load_admitted(ledger, config)
        entry_path = _entry_location(record, root, worktree_id)
        entry = _read_for(worktree_id, entry_path)
        if entry is None:
            raise HydrationRefused("not-dehydrated", "no dehydration catalog entry for this worktree")
        if entry.get("recovery_hold"):
            raise HydrationRefused(
                "recovery-hold", "recovered record has unreconciled relationships; release the hold first",
                recovery_hold=entry["recovery_hold"],
            )
        report = _reconcile_locked(worktree_id, record, anchor, entry_path, entry, ledger=ledger)
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
        entry.pop("config_restored", None)  # a replacement checkout always needs the snapshot config
        catalog.write_entry(entry_path, entry)
        added = _git(anchor, "worktree", "add", "--no-checkout", str(checkout), branch, check=False)
        if added.returncode != 0:
            entry["stage"] = catalog.STAGE_DEHYDRATED
            entry.pop("config_restored", None)
            catalog.write_entry(entry_path, entry)
            raise HydrationRefused("add-failed", f"git worktree add failed: {added.stderr.strip()}")
        return _complete_rehydrate(worktree_id, anchor, entry_path, entry, ledger=ledger, record=record)


def _unpopulated(checkout: Path) -> bool:
    """True only for the exact shape `worktree add --no-checkout` leaves: no index, only `.git`."""
    index = Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout.strip())
    return not index.exists() and [p.name for p in checkout.iterdir()] == [".git"]


def _materialize(checkout: Path) -> None:
    """Populate a just-added empty checkout from HEAD.

    `reset --hard` is safe here only because the caller proved the checkout
    holds nothing but its `.git` file and has no index yet.
    """
    _git(checkout, "reset", "-q", "--hard", "HEAD")


def _restore_config_once(checkout: Path, entry_path: Path, entry: dict[str, Any], *, populated: bool) -> None:
    """Restore the snapshot config only while restoration is provably unfinished.

    The ``config_restored`` marker proves restoration already happened, so a
    retry keeps any newer per-worktree settings. Without it, only a checkout
    created by this rehydration (stage ``rehydrating``) is restored; a populated
    one is restored only when it has no differing per-worktree config yet.
    """
    snapshot = entry["git"].get("worktree_config")
    if entry.get("config_restored") or not isinstance(snapshot, dict):
        return
    if entry.get("stage") != catalog.STAGE_REHYDRATING:
        raise HydrationRefused(
            "rehydrate-unverified", "checkout was not created by rehydration; catalog retained",
        )
    if populated:
        target = _worktree_config_file(checkout)
        wanted = base64.b64decode(str(snapshot["file_b64"]), validate=True)
        if target.exists() and target.read_bytes() != wanted:
            raise HydrationRefused(
                "worktree-config-conflict", "checkout has different per-worktree config; catalog retained",
            )
    _restore_worktree_config(checkout, snapshot)
    entry["config_restored"] = True
    catalog.write_entry(entry_path, entry)


def _complete_rehydrate(
    worktree_id: str, anchor: Path, entry_path: Path, entry: dict[str, Any], *, ledger: Path, record: Any,
) -> dict[str, Any]:
    checkout = Path(entry["identity"]["checkout_path"])
    branch = entry["git"]["branch"]
    actual = _valid_checkout(checkout, anchor, branch)
    if actual is None:
        raise HydrationRefused("rehydrate-unverified", "rehydrated checkout failed verification; catalog retained")
    if _unpopulated(checkout):
        # Per-worktree config (e.g. core.symlinks, core.autocrlf) must apply before
        # files exist. Restore it once: a retry after the marker keeps newer settings.
        _restore_config_once(checkout, entry_path, entry, populated=False)
        _materialize(checkout)
    elif not Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout.strip()).exists():
        raise HydrationRefused("rehydrate-partial", "checkout has files but no index; catalog retained")
    else:
        _restore_config_once(checkout, entry_path, entry, populated=True)
    try:
        _require_clean(checkout)
    except HydrationRefused as exc:
        raise HydrationRefused(
            "rehydrate-unverified", f"rehydrated checkout is not clean ({exc.code}); catalog retained",
        ) from exc
    # Every restore (entry, manual rehydrate or reconciliation) leaves paired entry incomplete.
    _mark_pair_pending(record, ledger, worktree_id)
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
    worktree_id: str, record: Any, anchor: Path, entry_path: Path, entry: dict[str, Any], *, ledger: Path,
) -> dict[str, Any]:
    conflict = _identity_conflict(entry, entry_path, record, worktree_id, anchor)
    if conflict:
        return _hold(worktree_id, entry, conflict)
    checkout = Path(entry["identity"]["checkout_path"])
    branch, stage = entry["git"]["branch"], entry["stage"]
    valid = _valid_checkout(checkout, anchor, branch)
    base = {"schema": SCHEMA, "worktree_id": worktree_id, "stage": stage}
    if valid is not None:
        if entry.get("recovery_hold"):
            return _hold(worktree_id, entry, "recovered entry has a checkout but unreconciled relationships")
        if stage == catalog.STAGE_REMOVING and valid == entry["git"]["head"]:
            try:
                _require_clean(checkout)
            except HydrationRefused as exc:
                return _hold(worktree_id, entry, f"interrupted dehydration left a partial checkout ({exc.code})")
            catalog.delete_entry(entry_path)
            return {**base, "state": catalog.STATE_HYDRATED, "reconciled": "interrupted-dehydrate-rolled-back"}
        if stage == catalog.STAGE_REMOVING:
            return _hold(worktree_id, entry, "checkout changed during an interrupted dehydration")
        # A valid checkout always wins over a stale snapshot; never remove it.
        try:
            report = _complete_rehydrate(worktree_id, anchor, entry_path, entry, ledger=ledger, record=record)
        except HydrationRefused as exc:
            return _hold(worktree_id, entry, f"{exc.code}: {exc}")
        return {**report, "stage": stage, "reconciled": "interrupted-rehydrate-completed"}
    if checkout.exists() and (not checkout.is_dir() or any(checkout.iterdir())):
        return _hold(worktree_id, entry, "checkout path holds a partial or foreign checkout")
    if _is_registered(anchor, checkout):
        return _hold(worktree_id, entry, "git still registers the absent checkout")
    if stage != catalog.STAGE_DEHYDRATED or "config_restored" in entry:
        # The checkout is gone, so its restoration marker no longer applies.
        entry.pop("config_restored", None)
        entry["stage"] = catalog.STAGE_DEHYDRATED
        entry.setdefault("dehydrated_at", _now())
        catalog.write_entry(entry_path, entry)
    reconciled = {catalog.STAGE_REMOVING: "interrupted-dehydrate-completed",
                  catalog.STAGE_REHYDRATING: "interrupted-rehydrate-reverted"}.get(stage)
    return {**base, "state": catalog.STATE_DEHYDRATED, **({"reconciled": reconciled} if reconciled else {})}


def reconcile(worktree_id: str, config: Any) -> dict[str, Any]:
    """Settle an interrupted transition; never removes a valid checkout."""
    ledger = _ledger(worktree_id, config)
    anchor, root = _repo(config)
    with _locked(ledger, root):
        record = _load_admitted(ledger, config)
        entry_path = _entry_location(record, root, worktree_id)
        try:
            entry = catalog.read_entry(entry_path)
        except catalog.CatalogError as exc:
            return _hold(worktree_id, None, str(exc))
        if entry is None:
            return {"schema": SCHEMA, "worktree_id": worktree_id, "state": catalog.STATE_HYDRATED}
        return _reconcile_locked(worktree_id, record, anchor, entry_path, entry, ledger=ledger)


def release_recovery_hold(worktree_id: str, config: Any) -> dict[str, Any]:
    """Explicitly accept a recovered record after its relationships were reconciled."""
    ledger = _ledger(worktree_id, config)
    anchor, root = _repo(config)
    with _locked(ledger, root):
        record = _load_admitted(ledger, config)
        entry_path = _entry_location(record, root, worktree_id)
        entry = _read_for(worktree_id, entry_path)
        if entry is None:
            raise HydrationRefused("not-dehydrated", "no dehydration catalog entry for this worktree")
        conflict = _identity_conflict(entry, entry_path, record, worktree_id, anchor)
        if conflict:
            raise HydrationRefused("hold", conflict, report=_hold(worktree_id, entry, conflict))
        released = entry.pop("recovery_hold", None)
        if released:
            catalog.write_entry(entry_path, entry)
        return {"schema": SCHEMA, "worktree_id": worktree_id, "released": bool(released)}


def _recoverable(entry: dict[str, Any], anchor: Path) -> bool:
    git = entry["git"]
    return bool(entry["stage"] == catalog.STAGE_DEHYDRATED and _branch_tip(anchor, str(git["branch"])) == git["head"])


def recover(config: Any, *, apply: bool = False) -> dict[str, Any]:
    """Report (and optionally rebuild) tracking from catalog entries.

    Only a catalog whose branch still points at its snapshot HEAD is
    reconstructable. A rebuilt record is non-active (``complete``) and its
    catalog entry gains a ``recovery_hold``: the record never becomes a live
    owner and the catalog is retained until the relationships recorded in it
    are reconciled and the hold is explicitly released.
    """
    from . import tracking

    anchor, root = _repo(config)
    results: list[dict[str, Any]] = []
    roots = [root]
    try:
        registered = catalog.registered_roots(config.repo_name)
    except catalog.CatalogError as exc:
        registered = []
        results.append({"catalog": None, "verdict": "hold", "reason": str(exc)})
    for extra in registered:
        if not any(catalog.same_path(extra, known) for known in roots):
            roots.append(extra)
    for entry_path in (path for each in roots for path in catalog.iter_entries(each)):
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
        try:
            ledger = _ledger(entry_path.stem, config)
        except HydrationRefused as exc:
            item.update(verdict="hold", reason=str(exc))
            continue
        if ledger.exists():
            item["verdict"] = "tracked"
            continue
        if not catalog.same_path(str(identity.get("anchor") or ""), anchor) or not _recoverable(entry, anchor):
            item.update(verdict="hold", reason="anchor/branch/stage evidence contradicts the catalog snapshot")
            continue
        item["verdict"] = "reconstructable"
        if apply:
            item.update(_recover_one(entry_path, ledger, anchor, root, tracking, config))
    return {
        "schema": SCHEMA, "catalog_dir": str(catalog.catalog_dir(root)),
        "catalog_dirs": [str(catalog.catalog_dir(each)) for each in roots], "entries": results, "apply": apply,
    }


def _recover_one(
    entry_path: Path, ledger: Path, anchor: Path, root: Path, tracking: Any, config: Any,
) -> dict[str, Any]:
    with _locked(ledger, root):
        if ledger.exists():
            return {"verdict": "tracked", "applied": False}
        try:
            entry = catalog.read_entry(entry_path)
        except catalog.CatalogError as exc:
            return {"verdict": "hold", "reason": str(exc), "applied": False}
        if entry is None or not _recoverable(entry, anchor):
            return {"verdict": "hold", "reason": "catalog changed before recovery", "applied": False}
        identity, git = entry["identity"], entry["git"]
        agency = entry.get("agency") or {}
        now = _now()
        record = tracking.WorktreeRecord(
            worktree_id=identity["worktree_id"], branch=git["branch"], worktree_path=identity["checkout_path"],
            repo=identity["repo"], machine=str(getattr(config, "machine", "") or ""),
            platform=detect_platform(), started_at=entry.get("created_at") or now,
            last_resumed_at=now, resume_count=0, title=agency.get("title"), status="complete",
            completed_at=now, creation_nonce=identity.get("creation_nonce") or "",
        )
        try:
            _admit(record, config)
        except HydrationRefused as exc:
            return {"verdict": "hold", "reason": str(exc), "applied": False}
        entry["recovery_hold"] = {"at": now, "reason": "relationships-unreconciled"}
        catalog.write_entry(entry_path, entry)
        tracking.save_record(record, ledger)
        return {"applied": True, "recovery_hold": True}
