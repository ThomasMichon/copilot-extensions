"""Targeted removal of an owner reference whose local parent is proven gone.

A child worktree keeps ``owner_ref`` pointing at its creating parent. When that
parent's tracking record (live or archived), checkout, branch and Git
registration have all disappeared from this execution space, ordinary and
retired-owner finalization cannot establish custody. This doctor repair proves
that absence and, only with ``--fix``, removes the child's ``owner_ref`` line
while holding both the child's and the parent ID's record locks, retaining an
exact-bytes backup and a JSON audit entry.
It never touches checkout content, branches, or any other record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable, cast

import yaml

from . import config as cfg, git_ops, output, pr_authority, tracking

if TYPE_CHECKING:
    from .tracking_claims import ClaimRef

_OWNER_LINE = re.compile(r"(?m)^owner_ref: [^\r\n]*(?:\r?\n|$)")
_OTHER_PASSES = (
    "recovery_report", "repair_machine_record", "from_machine", "to_machine",
    "guest_distribution", "repair_creation", "seed_id", "repair_record",
    "bound_agent_end_field", "gc_sessions", "prune_pivots", "apply_daemon_health",
)


class OrphanedOwnerRefused(ValueError):
    """The parent's absence is not positively proven; nothing may change."""


def _detect_platform() -> str:
    return cast("Callable[[], str]", getattr(cfg, "detect_platform"))()


def _parse_claim_ref(ref: str) -> ClaimRef | None:
    parse = cast("Callable[[str], ClaimRef | None]", getattr(tracking, "parse_claim_ref"))
    return parse(ref)


def add_doctor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repair-orphaned-owner", metavar="WORKTREE_ID",
        help="Prove this child's local owner worktree is permanently gone; "
        "--fix removes only the child's owner_ref so finalize can proceed.",
    )


def _git(anchor: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return git_ops.git(
            *args, cwd=anchor, timeout=10, kill_tree=True,
            isolated_repository=True, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OrphanedOwnerRefused("parent repository Git proof timed out") from exc


def _present(name: str) -> OrphanedOwnerRefused:
    return OrphanedOwnerRefused(
        f"parent tracking state is still present ({name}); use ordinary or "
        "retired-owner finalization instead"
    )


def _residual_lock(lock: Path) -> str:
    """Accept only an unheld, empty, regular lock left by ordinary record removal."""
    try:
        if lock.is_symlink() or not lock.is_file() or lock.stat().st_size != 0:
            raise _present(lock.name)
    except OSError as exc:
        raise _present(lock.name) from exc
    # The record lock's own non-blocking probe; it opens only the existing file.
    probe = tracking._RecordLock(lock.with_suffix(".yaml"), timeout=0, blocking=False)
    with probe:
        held = not probe.acquired
    if held:
        raise OrphanedOwnerRefused(f"parent record lock {lock.name} is currently held")
    return f"{lock.name} (empty, unheld)"


def _residual_sidecar(sidecar: Path) -> str:
    """Accept only a sidecar tree made solely of real, empty directories."""
    if sidecar.is_symlink() or not sidecar.is_dir():
        raise _present(f"{sidecar.name}/")
    def unreadable(error: OSError) -> None:
        raise _present(f"{sidecar.name}/") from error

    try:
        # onerror: an unenumerable subtree must never read as empty.
        for root, dirs, files in os.walk(sidecar, onerror=unreadable, followlinks=False):
            linked = [name for name in dirs if Path(root, name).is_symlink()]
            if files or linked:
                raise _present(f"{sidecar.name}/")
    except OSError as exc:
        raise _present(f"{sidecar.name}/") from exc
    return f"{sidecar.name}/ (empty directories only)"


def _parent_tracking_absent(records: Path, worktree_id: str) -> list[str]:
    """Refuse on any parent tracking state; return tolerated removal residue.

    Ordinary record removal leaves the sidecar ``<id>.lock`` behind, and
    handoff sidecar directories can outlive their record as empty trees. An
    unheld empty lock and an all-directory sidecar are reported, never removed.
    """
    if not records.is_dir() or records.is_symlink():
        raise OrphanedOwnerRefused("parent project tracking directory is not locally resolvable")
    prefix = f"{worktree_id}."
    residuals: list[str] = []
    for entry in sorted(records.iterdir()):
        if entry.name == f"{worktree_id}.lock":
            residuals.append(_residual_lock(entry))
        elif entry.name == worktree_id:
            residuals.append(_residual_sidecar(entry))
        elif entry.name.startswith(prefix):
            raise _present(entry.name)
    for entry in records.glob("*.yaml"):
        try:
            text = entry.read_text(encoding="utf-8")
        except OSError as exc:
            raise OrphanedOwnerRefused(f"cannot read tracking record {entry.name}") from exc
        if worktree_id not in text:
            continue
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise OrphanedOwnerRefused(
                f"unreadable tracking record {entry.name} may describe the parent"
            ) from exc
        if isinstance(data, dict) and data.get("worktree_id") == worktree_id:
            raise OrphanedOwnerRefused(f"parent identity is still recorded in {entry.name}")
    return residuals


def _parent_git_absent(anchor: Path, checkout: Path, branch: str) -> None:
    if checkout.exists() or checkout.is_symlink():
        raise OrphanedOwnerRefused(f"parent checkout still exists at {checkout}")
    if not anchor.is_dir() or _git(anchor, "rev-parse", "--git-dir").returncode != 0:
        raise OrphanedOwnerRefused("parent repository anchor is not a local Git repository")
    if _git(anchor, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode != 1:
        raise OrphanedOwnerRefused(f"parent branch {branch} still exists or cannot be proven absent")
    listing = _git(anchor, "worktree", "list", "--porcelain", "-z")
    if listing.returncode != 0:
        raise OrphanedOwnerRefused("parent repository worktree registrations are unreadable")
    for field in listing.stdout.split("\0"):
        if field == f"branch refs/heads/{branch}" or (
            field.startswith("worktree ") and Path(field[9:]).name == checkout.name
        ):
            raise OrphanedOwnerRefused("parent checkout is still registered with Git")


def _no_live_evidence(record: tracking.WorktreeRecord, owner: ClaimRef) -> None:
    from . import sessions

    try:
        terminal = sessions.has_mux_session(owner.worktree_id, strict=True)
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        raise OrphanedOwnerRefused("parent terminal-session absence cannot be proven") from exc
    if terminal:
        raise OrphanedOwnerRefused("parent still has a live terminal session")
    session_ids = {getattr(record, "parent_session", None)}
    session_ids.update(
        relation.controller_session_id for relation in record.controllers or ()
        if relation.state == "active"
    )
    from . import session_liveness

    for session_id in sorted(filter(None, session_ids)):
        state = session_liveness.session_liveness(session_id)
        if state == "live":
            raise OrphanedOwnerRefused("a controlling parent session is still live")
        if state != "dead":
            raise OrphanedOwnerRefused(
                f"controlling parent session {session_id} cannot be proven inactive"
            )


def _owner_locality(
    owner: ClaimRef, record: tracking.WorktreeRecord, raw: dict[str, Any], path: Path,
    config: cfg.Config,
) -> str:
    """Prove the owner label names the current execution space, or refuse.

    Record naming fields are transient reporter facts, so a label is local
    only when it names the current execution space today. A historical alias
    is accepted when the shared legacy resolver maps that label, in the
    child's own receiving namespace, back to this child's record.

    Otherwise, one record's labels were written by one reporter in one
    vocabulary: when the owner label equals the child's own serialized
    machine label, and the child itself is already proven to be a local
    checkout on the actual platform with no explicit foreign execution
    identity, the owner label names that same execution space. An owner label
    that differs from the child's own label still refuses.
    """
    from . import retained_checkout
    from .creation_repair_identity import retained_owner_is_current
    from .legacy_wsl_record_locality import resolve_legacy_wsl_owner_ref
    from .tracking_claims import ClaimRef

    owner_machine = owner.machine
    if not owner_machine:
        raise OrphanedOwnerRefused("owner belongs to another machine or execution space")
    if retained_owner_is_current(owner_machine, config):
        return "current execution space"
    probe = ClaimRef(worktree_id=record.worktree_id, machine=owner_machine, project=record.repo)
    resolved = resolve_legacy_wsl_owner_ref(probe, config)
    if resolved is not None and Path(resolved).resolve() == path.resolve():
        return "legacy alias of current execution space"
    stored = raw.get("machine")
    nested = raw.get("machine_identity")
    explicit: list[object] = [raw.get("execution_machine")]
    if isinstance(nested, dict):
        explicit.append(nested.get("execution_machine"))
    elif nested is not None:
        explicit.append(nested)
    actual = _detect_platform()
    if (
        isinstance(stored, str) and stored
        and owner_machine.casefold() == stored.casefold()
        and raw.get("platform") == actual and record.platform == actual
        and retained_checkout.is_local(record, config)
        and all(
            value is None or (isinstance(value, str) and retained_owner_is_current(value, config))
            for value in explicit
        )
    ):
        return "legacy label shared with the proven-local child record"
    raise OrphanedOwnerRefused("owner belongs to another machine or execution space")


def prove(record_id: str) -> dict[str, Any]:
    """Return evidence that the child's local owner is gone, or refuse."""
    from . import execution_spaces, receiving_allocation, record_reporting, retained_checkout

    if (
        not isinstance(record_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", record_id) or ".." in record_id
    ):
        raise OrphanedOwnerRefused("repair requires an exact worktree ID, not a path")
    project = cfg.project_name()
    if not project:
        raise OrphanedOwnerRefused("orphaned-owner repair requires an explicitly selected project")
    path = cfg.tracking_dir() / f"{record_id}.yaml"
    if path.is_symlink() or not path.is_file():
        raise OrphanedOwnerRefused("child tracking record is missing or redirected")
    observed = path.read_bytes()
    record = tracking.load_record(path)
    if record.worktree_id != record_id or not record.owner_ref:
        raise OrphanedOwnerRefused("child record has no owner reference to repair")
    from . import checkout_relocation_state

    try:
        checkout_relocation_state.require_idle(path)
    except ValueError as exc:
        raise OrphanedOwnerRefused(f"child checkout relocation is not settled: {exc}") from exc
    owner = _parse_claim_ref(record.owner_ref)
    if (
        owner is None or not owner.machine or not owner.project
        or owner.session or owner.is_anchor
        or not cfg._PROJECT_NAME_RE.fullmatch(owner.project)
        or Path(owner.worktree_id).name != owner.worktree_id
        or owner.worktree_id in {"", ".", ".."}
        or "/" in owner.worktree_id or "\\" in owner.worktree_id
    ):
        raise OrphanedOwnerRefused("owner reference is not an ordinary qualified worktree owner")
    if record.is_paired or record.pair_ref:
        raise OrphanedOwnerRefused("paired records keep their reciprocal relation")
    config = execution_spaces.project_record_config(record)
    try:
        receiving = record.receiving_allocation_present or (
            receiving_allocation.orphan_retains_receiving_operation(record, path.parent)
        )
    except ValueError as exc:
        raise OrphanedOwnerRefused(f"receiving custody cannot be classified: {exc}") from exc
    if receiving:
        raise OrphanedOwnerRefused("child retains receiving allocation metadata")
    record_reporting.require_current_subject(record, config)
    if not retained_checkout.is_local(record, config):
        raise OrphanedOwnerRefused("child is not a proven local checkout in this execution space")
    raw_mapping = yaml.safe_load(observed)
    if not isinstance(raw_mapping, dict):
        raise OrphanedOwnerRefused("child tracking record is not a mapping")
    owner_locality = _owner_locality(owner, record, raw_mapping, path, config)
    try:
        parent_config = execution_spaces.project_record_config(SimpleNamespace(repo=owner.project))
        repo = parent_config.repos[owner.project]
    except (execution_spaces.ExecutionSpaceError, KeyError) as exc:
        raise OrphanedOwnerRefused("owner project is not locally resolvable") from exc
    records = cfg.project_dir(owner.project) / "worktrees"
    checkout = Path(repo.worktree_root) / owner.worktree_id
    branch = f"worktree/{owner.worktree_id}"
    residuals = _parent_tracking_absent(records, owner.worktree_id)
    _parent_git_absent(Path(repo.anchor), checkout, branch)
    _no_live_evidence(record, owner)
    raw = observed.decode("utf-8")
    if len(_OWNER_LINE.findall(raw)) != 1:
        raise OrphanedOwnerRefused("owner_ref is not a single serialized line")
    fixed = _OWNER_LINE.sub("", raw, count=1)
    from .tracking_yaml_repair import strict_mapping

    before = strict_mapping(raw)
    before.pop("owner_ref", None)
    if strict_mapping(fixed) != before:
        raise OrphanedOwnerRefused("owner_ref removal would change other fields")
    return {
        "path": path, "observed": observed, "fixed": fixed, "project": project,
        "parent_path": records / f"{owner.worktree_id}.yaml",
        "evidence": {
            "owner_ref": record.owner_ref, "parent_project": owner.project,
            "parent_worktree_id": owner.worktree_id,
            "parent_record": f"absent from {records}",
            "residual_parent_artifacts": residuals,
            "parent_checkout": f"absent at {checkout}", "parent_branch": f"absent ({branch})",
            "git_registration": "absent", "live_evidence": "none",
            "owner_locality": owner_locality,
        },
    }


def _write_private(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError(f"existing audit file {path.name} cannot be verified") from None
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def apply(record_id: str) -> dict[str, Any]:
    """Re-prove under the child and parent-ID record locks, then remove only ``owner_ref``.

    The parent-ID lock is the one ``create_new_record_if_absent`` takes, so no
    parent record can appear between the final proof and the write. Holding it
    may leave an empty lock file behind, the same residue as record removal.
    """
    proof = prove(record_id)
    path, parent = proof["path"], proof["parent_path"]
    ordered = sorted({path, parent}, key=lambda item: os.path.normcase(os.path.abspath(item)))
    with pr_authority.guard(timeout=0), ExitStack() as held:
        for target in ordered:
            lock = held.enter_context(
                tracking._RecordLock(target, blocking=False, require_sidecar=True),
            )
            if not lock.acquired:
                raise TimeoutError(f"tracking record lock {target.stem} is busy; repair refused")
        current = prove(record_id)
        if current["observed"] != proof["observed"] or current["parent_path"] != parent:
            raise ValueError("tracking record changed; inspect again before repair")
        digest = hashlib.sha256(current["observed"]).hexdigest()[:16]
        backup = path.with_name(f"{path.name}.orphaned-owner-{digest}.bak")
        audit = path.with_name(f"{path.name}.orphaned-owner-{digest}.json")
        _write_private(backup, current["observed"])
        _write_private(audit, json.dumps({
            "version": 1, "action": "remove-orphaned-owner-ref",
            "worktree_id": record_id, "project": current["project"],
            "repaired_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "backup": backup.name, "evidence": current["evidence"],
        }, indent=2, sort_keys=True).encode("utf-8"))
        tracking._atomic_write(path, current["fixed"])
    return {**current, "backup": backup.name, "audit": audit.name}


def targeted_doctor(args: argparse.Namespace) -> int:
    record_id = args.repair_orphaned_owner
    fix = getattr(args, "fix", False)
    report: dict[str, Any] = {
        "version": 1, "scope": "orphaned-owner", "worktree_id": record_id,
        "mode": "fix" if fix else "report", "repairable": False,
        "repaired": False, "error": None, "evidence": None,
    }
    try:
        if any(getattr(args, name, None) for name in _OTHER_PASSES):
            raise ValueError("orphaned-owner repair cannot be combined with other repair passes")
        result = apply(record_id) if fix else prove(record_id)
        report.update(
            project=result["project"], evidence=result["evidence"], repairable=True,
            repaired=fix,
        )
        if fix:
            report.update(backup=result["backup"], audit=result["audit"])
    except (OSError, ValueError, TypeError, RuntimeError, yaml.YAMLError, git_ops.GitError) as exc:
        report["error"] = str(exc)
    if getattr(args, "json", False):
        output._json_output(report)
    elif report["error"]:
        output.err(f"Orphaned-owner repair refused: {report['error']}")
    else:
        for key, value in report["evidence"].items():
            print(f"  {key}: {value}")
        output.ok(f"Record {record_id}: " + (
            "owner_ref removed; plain finalize now applies" if report["repaired"]
            else "owner is proven gone; rerun with --fix to remove owner_ref"
        ))
    return 1 if report["error"] else 0
