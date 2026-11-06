"""Derived-output claims: a worktree's disposable generated directories.

A producer (a test runner, a build step, a cache warmer) that creates a
regenerable directory inside a worktree's checkout records it as an open
``derived`` claim on that worktree's outbound obligation ledger. The claim
blocks finalize like any other unsettled obligation, so the owner decides
explicitly, one path at a time, whether to delete it
(``claims settle <path> --reclaim``) or keep it (``claims release <path>``).
Nothing here deletes anything implicitly: finalize never removes a derived
directory, the reclaim sweep skips the kind, and each reclaim acts on exactly
one ledger entry after re-proving that this worktree created that directory.

Proof of creation is a marker file, :data:`MARKER_NAME`, written into the
directory at claim time. It carries the owner's worktree id and a random nonce
that is also stored on the ledger entry's note. A directory that already
exists without a matching marker is never claimed -- a producer must not adopt
something it did not create.

This module is the producer-facing Python API (:func:`claim_directory`) plus
the pure safety checks behind a reclaim (:func:`reclaim_refusals`). The CLI
glue lives in ``derived_claims_cli``; producers outside this plugin use the
``agent-worktrees claims add derived <path> --creator <tool>`` command.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

KIND = "derived"
MARKER_NAME = ".derived-claim.json"
MARKER_SCHEMA = "agent-worktrees.derived-claim"
MARKER_VERSION = 1

_NONCE_RE = re.compile(r"(?:^|\s)nonce=([0-9a-f]{32})(?=\s|$)")
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class DerivedClaimError(RuntimeError):
    """A derived claim cannot be created or adopted safely."""


@dataclass(frozen=True)
class Marker:
    worktree_id: str
    nonce: str
    creator: str
    created_at: str


def new_nonce() -> str:
    return secrets.token_hex(16)


def format_note(*, creator: str, nonce: str) -> str:
    """The ledger note for a derived claim; carries the nonce."""
    return f"creator={creator or 'unknown'} nonce={nonce}"


def nonce_from_note(note: str) -> str | None:
    match = _NONCE_RE.search(note or "")
    return match.group(1) if match else None


def canonical_path(path: str | os.PathLike[str], *, base: str | os.PathLike[str] | None = None) -> Path:
    """Absolute, lexically-normalized path without following any link."""
    raw = os.fspath(path)
    if not os.path.isabs(raw):
        raw = os.path.join(os.fspath(base) if base is not None else os.getcwd(), raw)
    return Path(os.path.abspath(raw))


def same_path(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(a))) == os.path.normcase(
        os.path.abspath(os.fspath(b))
    )


def _strictly_inside(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.abspath(child))
    p = os.path.normcase(os.path.abspath(parent))
    if c == p:
        return False
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:
        return False


def inside_checkout(path: str | os.PathLike[str], checkout: str | os.PathLike[str]) -> bool:
    """True when ``path`` is strictly below ``checkout`` both lexically and
    after resolving links, and does not sit inside a ``.git`` directory."""
    target, root = os.fspath(path), os.fspath(checkout)
    if not root or not _strictly_inside(target, root):
        return False
    real_target, real_root = os.path.realpath(target), os.path.realpath(root)
    if not _strictly_inside(real_target, real_root):
        return False
    for child, parent in ((target, root), (real_target, real_root)):
        rel = os.path.relpath(os.path.abspath(child), os.path.abspath(parent))
        if ".git" in {part.lower() for part in Path(rel).parts}:
            return False
    return True


def linked_ancestor(path: str | os.PathLike[str], checkout: str | os.PathLike[str]) -> str | None:
    """First existing link/junction/reparse point between ``checkout`` and
    ``path`` (exclusive of both); such an alias could redirect the target
    into tracked content or Git metadata, so it is never trusted."""
    root = Path(os.path.abspath(os.fspath(checkout)))
    rel = Path(os.path.relpath(os.path.abspath(os.fspath(path)), root))
    current = root
    for part in rel.parts[:-1]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return None
        except OSError as exc:
            return f"{current} (uninspectable: {exc})"
        if is_reparse(info):
            return str(current)
    return None


def is_reparse(info: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def find_reparse_point(root: Path) -> str | None:
    """First symlink/junction/reparse point at or below ``root``; never follows one."""
    try:
        if is_reparse(os.lstat(root)):
            return str(root)
    except OSError as exc:
        return f"{root} (uninspectable: {exc})"
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or is_reparse(info):
                        return entry.path
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))
        except OSError as exc:
            return f"{current} (uninspectable: {exc})"
    return None


def write_marker(directory: Path, marker: Marker) -> Path:
    path = directory / MARKER_NAME
    payload = {
        "schema": MARKER_SCHEMA,
        "version": MARKER_VERSION,
        "worktree_id": marker.worktree_id,
        "nonce": marker.nonce,
        "creator": marker.creator,
        "created_at": marker.created_at,
    }
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def read_marker(directory: Path) -> Marker | None:
    """The directory's marker, or None when absent, linked, or malformed."""
    path = directory / MARKER_NAME
    try:
        info = os.lstat(path)
        if is_reparse(info) or not stat.S_ISREG(info.st_mode):
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != MARKER_SCHEMA:
        return None
    fields = [data.get(k) for k in ("worktree_id", "nonce", "creator", "created_at")]
    if not all(isinstance(v, str) for v in fields) or not fields[0] or not fields[1]:
        return None
    return Marker(*fields)


def find_claim(resources, ref: str | os.PathLike[str]):
    """The ``derived`` ledger entry whose path matches ``ref``, if any."""
    for claim in resources:
        if claim.kind == KIND and same_path(claim.ref, ref):
            return claim
    return None


def prepare_directory(
    target: Path,
    *,
    worktree_id: str,
    checkout: str,
    resources,
    creator: str,
    now_iso: str,
) -> tuple[str, bool]:
    """Create ``target`` with a fresh marker, or re-prove an earlier one.

    Returns ``(nonce, created)``. Raises :class:`DerivedClaimError` when the
    path is outside the checkout, or exists without a marker proving that this
    worktree created it under a ledger entry it still records.
    """
    if not inside_checkout(target, checkout):
        raise DerivedClaimError(
            f"{target} is not inside worktree {worktree_id}'s checkout; not claimed"
        )
    if link := linked_ancestor(target, checkout):
        raise DerivedClaimError(
            f"{target} sits below a link or reparse point ({link}); not claimed"
        )
    if os.path.lexists(target):
        marker = read_marker(target) if target.is_dir() and not is_reparse(os.lstat(target)) else None
        existing = find_claim(resources, target)
        ledger_nonce = nonce_from_note(existing.note) if existing is not None else None
        if (
            marker is None
            or marker.worktree_id != worktree_id
            or ledger_nonce is None
            or marker.nonce != ledger_nonce
        ):
            raise DerivedClaimError(
                f"{target} already exists and was not created under a derived claim "
                f"by worktree {worktree_id}; refusing to adopt it (not claimed)"
            )
        return marker.nonce, False
    nonce = new_nonce()
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise DerivedClaimError(f"{target} appeared concurrently; not claimed") from exc
    try:
        write_marker(target, Marker(worktree_id, nonce, creator, now_iso))
    except OSError:
        discard_new_directory(target)
        raise
    return nonce, True


def discard_new_directory(target: Path) -> None:
    """Undo :func:`prepare_directory`'s creation when journaling failed."""
    try:
        (target / MARKER_NAME).unlink()
    except OSError:
        pass
    try:
        target.rmdir()
    except OSError:
        pass


def _tracked_files(checkout: str, target: Path) -> str | None:
    """Refusal reason when Git tracks anything under ``target`` (or cannot say)."""
    from . import git_ops

    rel = os.path.relpath(os.path.abspath(target), os.path.abspath(checkout))
    try:
        proc = git_ops.git(
            "--literal-pathspecs", "ls-files", "-z", "--", rel,
            cwd=checkout, check=False, timeout=60, isolated_repository=True,
        )
    except Exception as exc:  # noqa: BLE001 - any failure must refuse
        return f"cannot list Git-tracked files ({exc})"
    if proc.returncode != 0:
        return f"cannot list Git-tracked files ({(proc.stderr or '').strip() or proc.returncode})"
    tracked = [p for p in (proc.stdout or "").split("\0") if p]
    if tracked:
        return f"contains {len(tracked)} Git-tracked file(s), e.g. {tracked[0]}"
    return None


def _process_blocker(target: Path) -> str | None:
    """Refusal reason when a live process works in, or runs an image from,
    ``target``. Working-directory discovery fails closed when unavailable."""
    from . import procs

    if blocker := procs.checkout_retirement_blocker(str(target)):
        return blocker
    try:
        images = procs.processes_with_executable_under(os.path.realpath(target))
    except OSError as exc:
        return f"executable inspection unavailable: {exc}; reclaim deferred"
    if images:
        names = ", ".join(f"{p.get('name') or '?'}({p['pid']})" for p in images)
        return f"live process(es) run an executable from it: {names}"
    return None


def _ledger_refusal(record, claim) -> str | None:
    """Refusal reason when the ledger itself would not accept settling
    ``claim`` -- checked before deletion so the directory is never removed
    for a settle that is bound to be refused."""
    from . import tracking
    from .execution_spaces import ExecutionSpaceError, require_project_record_mutation

    try:
        require_project_record_mutation(record)
    except ExecutionSpaceError as exc:
        return f"this process may not mutate {record.worktree_id}'s ledger: {exc}"
    if getattr(record, "status", "") in {"finalizing", "orphaned"}:
        return f"owner worktree {record.worktree_id} is {record.status}"
    if reservation := tracking.claim_handoff_reservation(record, claim):
        return f"claim is reserved by offered handoff bundle {reservation}"
    return None


def reclaim_refusals(record, claim) -> list[str]:
    """Every reason ``claim``'s directory must NOT be deleted (empty = safe).

    Checks, in order: the entry is an open ``derived`` claim; its path is a
    real directory strictly inside this worktree's checkout; its marker names
    this worktree and the ledger entry's nonce; nothing inside is a link or
    reparse point; Git tracks nothing beneath it; and no live process uses it.
    """
    if claim.kind != KIND:
        return [f"claim {claim.ref} is a {claim.kind!r} claim, not {KIND!r}"]
    if not claim.is_live:
        return [f"claim {claim.ref} is {claim.state}, not open"]
    target = Path(claim.ref)
    checkout = getattr(record, "worktree_path", "") or ""
    if not inside_checkout(target, checkout):
        return [f"{target} is not inside worktree {record.worktree_id}'s checkout"]
    if link := linked_ancestor(target, checkout):
        return [f"{target} sits below a link or reparse point ({link}); never followed"]
    if ledger := _ledger_refusal(record, claim):
        return [ledger]
    try:
        root_info = os.lstat(target)
    except FileNotFoundError:
        return [f"{target} no longer exists (use `claims release` to drop the claim)"]
    except OSError as exc:
        return [f"cannot inspect {target}: {exc}"]
    if is_reparse(root_info):
        return [f"{target} is a link or reparse point; never followed"]
    if not stat.S_ISDIR(root_info.st_mode):
        return [f"{target} is not a directory"]
    marker = read_marker(target)
    ledger_nonce = nonce_from_note(claim.note)
    if marker is None:
        return [f"{target} has no readable {MARKER_NAME} marker"]
    if marker.worktree_id != record.worktree_id:
        return [f"marker names worktree {marker.worktree_id}, not {record.worktree_id}"]
    if ledger_nonce is None or marker.nonce != ledger_nonce:
        return ["marker nonce does not match the ledger entry"]
    refusals: list[str] = []
    if link := find_reparse_point(target):
        refusals.append(f"contains a link or reparse point: {link}")
    if tracked := _tracked_files(checkout, target):
        refusals.append(tracked)
    if blocker := _process_blocker(target):
        refusals.append(blocker)
    return refusals


def remove_directory(target: Path) -> tuple[bool, str]:
    from . import gc

    return gc.remove_tree(target)


def claim_directory(
    path: str | os.PathLike[str],
    *,
    creator: str,
    worktree: str | None = None,
    owner_ref: str | None = None,
) -> dict:
    """Producer API: create ``path`` and journal it as an open derived claim.

    Runs the same transaction as ``agent-worktrees claims add derived <path>``
    in-process and returns its JSON result (``created`` is False when the
    directory was already claimed by this worktree). Raises
    :class:`DerivedClaimError` with the refusal text when the claim was not
    recorded -- including a pre-existing directory this worktree did not
    create, which a producer must then leave unclaimed.
    """
    import argparse

    from . import derived_claims_cli, output

    args = argparse.Namespace(
        json=True, derived_creator=creator, release_worktree=worktree,
        claim_owner_ref=owner_ref,
    )
    with output.capture_json_output() as buffer:
        rc = derived_claims_cli.claims_add_derived(args, os.fspath(path))
    try:
        result = json.loads(buffer.getvalue() or "{}")
    except ValueError:
        result = {}
    if rc != 0:
        raise DerivedClaimError(str(result.get("error") or f"derived claim refused (exit {rc})"))
    return result
