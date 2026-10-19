"""Durable reciprocal proof for an interrupted terminal pair discharge.

The receipt is published under the sibling record lock *before* unlinking.
Only that exact terminal sibling document, with only pairing fields removed,
can resume a partial discharge. It never authorizes removal or guesses ownership.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import yaml

from . import tracking

_PAIR_FIELDS = ("pair_id", "pair_role", "pair_ref", "pair_kind")


def receipt_path(path: Path) -> Path:
    return path.with_suffix(".terminal-pair.json")


def _document(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("terminal pair record is not a mapping")
    return document


def _document_digest(document: dict) -> str:
    document = {name: value for name, value in document.items() if name not in _PAIR_FIELDS}
    pending, visited = [document], set()
    while pending:
        value = pending.pop()
        if not isinstance(value, (dict, list, tuple)) or id(value) in visited:
            continue
        visited.add(id(value))
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("terminal pair record requires string keys")
            pending.extend(value.values())
        else:
            pending.extend(value)
    return hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode()).hexdigest()


def _digest(path: Path) -> str:
    return _document_digest(_document(path))


def primary_identity(record):
    return {
        "machine": record.machine, "repo": record.repo,
        "worktree_id": record.worktree_id, "worktree_path": record.worktree_path,
        "branch": record.branch, "pair": {name: getattr(record, name) for name in _PAIR_FIELDS},
        "dispatch_attempt": asdict(record.dispatch_attempt) if record.dispatch_attempt else None,
    }


def _supported_pair(pair) -> bool:
    return (
        isinstance(pair, dict) and set(pair) == set(_PAIR_FIELDS)
        and all(isinstance(pair[name], str) and pair[name].strip() for name in _PAIR_FIELDS)
        and pair["pair_role"] in {"harness", "knowledge"}
        and pair["pair_kind"] == "worktree"
    )


def reciprocal_identity_valid(primary: dict, sibling: dict) -> bool:
    """One reciprocal identity predicate for conclusion and retained GC proof."""
    try:
        left, right = primary["pair"], sibling["pair"]
        if (not _supported_pair(left) or not _supported_pair(right)
                or left["pair_id"].strip() != right["pair_id"].strip()
                or {left["pair_role"], right["pair_role"]} != {"harness", "knowledge"}):
            return False
        for current, other in ((primary, sibling), (sibling, primary)):
            if (any(not isinstance(current[name], str) or not current[name].strip()
                    for name in ("machine", "repo", "worktree_id", "worktree_path", "branch"))
                    or Path(current["worktree_path"]).name != current["worktree_id"]
                    or current["branch"] != f"worktree/{current['worktree_id']}"):
                return False
            ref = tracking.parse_claim_ref(current["pair"]["pair_ref"])
            expected = tracking.format_claim_ref(other["machine"], other["repo"], other["worktree_id"])
            if ref is None or not ref.is_qualified or ref.canonical() != expected:
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def publish(record, sibling, sibling_path: Path, *, primary_path: Path | None = None) -> None:
    """Caller holds both records' locks and has saved the primed reciprocal sibling."""
    receipt = {
        "version": 1, "primary": primary_identity(record),
        "sibling_pair": {name: getattr(sibling, name) for name in _PAIR_FIELDS},
        "sibling_digest": _digest(sibling_path),
    }
    if primary_path is not None:
        receipt["primary_record_path"] = str(primary_path.resolve())
    tracking._atomic_write(receipt_path(sibling_path), json.dumps(receipt, sort_keys=True) + "\n")
    if primary_path is not None:
        primary_guard = {
            "version": 1, "receipt_role": "primary", "primary": receipt["primary"],
            "owner": record.owner, "sibling": primary_identity(sibling),
            "sibling_record_path": str(sibling_path.resolve()),
        }
        tracking._atomic_write(
            receipt_path(primary_path), json.dumps(primary_guard, sort_keys=True) + "\n",
        )


def gc_preservation_reason(sibling_path: Path) -> str | None:
    """A receipt is collectible only after its exact primary unlink is durable."""
    path = receipt_path(sibling_path)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return "terminal-pair-receipt-unverified"
    try:
        if path.is_symlink():
            return "terminal-pair-receipt-unverified"
        receipt = json.loads(path.read_text(encoding="utf-8"))
        expected = receipt["primary"]
        if receipt.get("receipt_role") == "primary":
            document = _document(sibling_path)
            if any(name in document for name in _PAIR_FIELDS):
                return "terminal-pair-discharge-pending"
            current = tracking.load_record(sibling_path)
            identity = primary_identity(current)
            identity["pair"] = expected["pair"]
            other_name = receipt["sibling_record_path"]
            if (receipt["version"] != 1 or not _supported_pair(expected["pair"])
                    or not reciprocal_identity_valid(expected, receipt["sibling"])
                    or identity != expected or _document(sibling_path) != document
                    or current.kind not in tracking.MANAGED_KINDS
                    or current.status not in {"complete", "finalized"}
                    or current.resolved_origin != "delegate"
                    or not current.owner or current.owner != receipt["owner"]
                    or not isinstance(other_name, str) or not other_name.strip()):
                return "terminal-pair-primary-changed"
            other_path = Path(other_name)
            if (not other_path.is_absolute() or other_path.is_symlink()
                    or other_path.name != receipt["sibling"]["worktree_id"] + ".yaml"):
                return "terminal-pair-sibling-unverified"
            try:
                receipt_path(other_path).lstat()
            except FileNotFoundError:
                try:
                    other_path.lstat()
                except FileNotFoundError:
                    return None
            return "terminal-pair-sibling-gc-pending"
        if "receipt_role" in receipt:
            return "terminal-pair-receipt-unverified"
        primary_name = receipt.get("primary_record_path")
        if (receipt["version"] != 1 or not isinstance(expected, dict)
                or not _supported_pair(expected.get("pair"))
                or not _supported_pair(receipt.get("sibling_pair"))
                or not isinstance(primary_name, str) or not primary_name.strip()):
            return "terminal-pair-primary-unverified"
        primary_path = Path(primary_name)
        if (not primary_path.is_absolute() or primary_path.is_symlink()
                or primary_path.name != expected["worktree_id"] + ".yaml"):
            return "terminal-pair-primary-unverified"
        primary_document = _document(primary_path)
        if any(name in primary_document for name in _PAIR_FIELDS):
            return "terminal-pair-discharge-pending"
        sibling_document = _document(sibling_path)
        if (any(name in sibling_document for name in _PAIR_FIELDS)
                or receipt["sibling_digest"] != _document_digest(sibling_document)):
            return "terminal-pair-sibling-changed"
        primary = tracking.load_record(primary_path)
        sibling = tracking.load_record(sibling_path)
        if _document(primary_path) != primary_document or _document(sibling_path) != sibling_document:
            return "terminal-pair-record-changed"
        identity = primary_identity(primary)
        identity["pair"] = expected["pair"]
        sibling_identity = primary_identity(sibling)
        sibling_identity["pair"] = receipt["sibling_pair"]
        if (identity != expected or primary.kind not in tracking.MANAGED_KINDS
                or not reciprocal_identity_valid(expected, sibling_identity)
                or primary.status not in {"complete", "finalized"}
                or primary.resolved_origin != "delegate"
                or not primary.owner or primary.owner != sibling.owner):
            return "terminal-pair-primary-changed"
        return None
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return "terminal-pair-receipt-unverified"


def reciprocal_snapshot(record, sibling, sibling_path: Path):
    """Return retained reciprocal evidence only for an exact interrupted unlink."""
    if any(getattr(sibling, name) for name in _PAIR_FIELDS):
        return None
    if (sibling.kind not in tracking.MANAGED_KINDS or sibling.status != "complete"
            or sibling.owner != record.owner or sibling.resolved_origin != "delegate"):
        return None
    try:
        document = _document(sibling_path)
        if any(name in document for name in _PAIR_FIELDS):
            return None
        receipt = json.loads(receipt_path(sibling_path).read_text(encoding="utf-8"))
        if (receipt["version"] != 1 or receipt["primary"] != primary_identity(record)
                or receipt["sibling_digest"] != _document_digest(document)):
            return None
        pair = receipt["sibling_pair"]
        if not _supported_pair(pair):
            return None
        return replace(sibling, **pair)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return None
