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


def _receipt_path(path: Path) -> Path:
    return path.with_suffix(".terminal-pair.json")


def _document(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("terminal pair record is not a mapping")
    return document


def _document_digest(document: dict) -> str:
    document = {name: value for name, value in document.items() if name not in _PAIR_FIELDS}
    return hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode()).hexdigest()


def _digest(path: Path) -> str:
    return _document_digest(_document(path))


def _primary_identity(record):
    return {
        "machine": record.machine, "repo": record.repo,
        "worktree_id": record.worktree_id, "worktree_path": record.worktree_path,
        "branch": record.branch, "pair": {name: getattr(record, name) for name in _PAIR_FIELDS},
        "dispatch_attempt": asdict(record.dispatch_attempt) if record.dispatch_attempt else None,
    }


def publish(record, sibling, sibling_path: Path) -> None:
    """Caller holds both records' locks and has saved the primed reciprocal sibling."""
    receipt = {
        "version": 1, "primary": _primary_identity(record),
        "sibling_pair": {name: getattr(sibling, name) for name in _PAIR_FIELDS},
        "sibling_digest": _digest(sibling_path),
    }
    tracking._atomic_write(_receipt_path(sibling_path), json.dumps(receipt, sort_keys=True) + "\n")


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
        receipt = json.loads(_receipt_path(sibling_path).read_text(encoding="utf-8"))
        if (receipt["version"] != 1 or receipt["primary"] != _primary_identity(record)
                or receipt["sibling_digest"] != _document_digest(document)):
            return None
        pair = receipt["sibling_pair"]
        if (not isinstance(pair, dict) or set(pair) != set(_PAIR_FIELDS)
                or any(not isinstance(pair[name], str) or not pair[name].strip() for name in _PAIR_FIELDS)
                or pair["pair_role"] not in {"harness", "knowledge"}
                or pair["pair_kind"] != "worktree"):
            return None
        return replace(sibling, **pair)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return None
