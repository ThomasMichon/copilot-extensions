"""Classify pinned Main advancement without granting artifact or execution reuse."""
from __future__ import annotations

import json
from pathlib import Path

from promotion_control import (
    MANIFEST_PATH, ControlPreservationError, _SHA, _blob, _git, _paths, _read_state, _tree,
)

STATE_PATH = ".github/release-pipeline-state.json"


def _controls(repo: Path, entries: dict) -> set[str]:
    aliases = [path for path in entries if path.casefold() == MANIFEST_PATH.casefold()]
    if aliases and aliases != [MANIFEST_PATH]:
        raise ControlPreservationError("case-equivalent main control manifest")
    if MANIFEST_PATH not in entries:
        return set()
    paths = _paths(_blob(repo, entries[MANIFEST_PATH], limit=64 << 10))
    for path in paths:
        parts = path.split("/")
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            if any(
                other[:len(prefix)].casefold() == prefix.casefold()
                and (len(other) == len(prefix) or other[len(prefix)] == "/")
                and other[:len(prefix)] != prefix
                for other in entries
            ):
                raise ControlPreservationError("case-equivalent main control path")
        if path not in entries:
            raise ControlPreservationError("missing declared main control file")
        _blob(repo, entries[path], limit=1 << 20)
    return set(paths)


def _operator_state(state: dict | None) -> tuple[str | None, bool]:
    if state is None:
        return None, False
    if (
        type(state.get("paused")) is not bool
        or not isinstance(state.get("pause_reason"), (str, type(None)))
    ):
        raise ControlPreservationError("invalid operator pause state")
    remaining = {
        name: value for name, value in state.items() if name not in ("paused", "pause_reason")
    }
    try:
        return json.dumps(remaining, sort_keys=True, allow_nan=False), state["paused"]
    except (TypeError, ValueError) as exc:
        raise ControlPreservationError("invalid non-operator pipeline state") from exc


def inspect_main_transition(*, repo: Path, before: str, after: str) -> dict:
    """Only trusted, ancestor-bound exact controls may be recomposed by a new run.

    This is classification, not qualification, package equivalence or permission
    for an old controller to execute new authority.
    """
    if any(not isinstance(head, str) or not _SHA.fullmatch(head) for head in (before, after)):
        raise ControlPreservationError("main transition requires pinned commit identities")
    for head in (before, after):
        if _git(repo, ["cat-file", "-t", head]).strip() != b"commit":
            raise ControlPreservationError("main transition input is not a commit")
    if _git(repo, ["merge-base", before, after]).strip().decode("ascii") != before:
        raise ControlPreservationError("main transition does not descend from its original base")
    old, new = _tree(repo, before), _tree(repo, after)
    old_paths, new_paths = _controls(repo, old), _controls(repo, new)
    if old_paths and not new_paths:
        raise ControlPreservationError("control ownership removal requires a new plan")
    old_state, _old_paused = _operator_state(_read_state(repo, old))
    new_state, paused = _operator_state(_read_state(repo, new))
    if old_state != new_state:
        raise ControlPreservationError("promoter lineage or unclassified pipeline state advanced")
    changed = sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
    unknown = set(changed) - old_paths - new_paths - {STATE_PATH}
    if unknown:
        raise ControlPreservationError(
            "main payload or unclassified governance advanced: " + ", ".join(sorted(unknown))
        )
    return {
        "before": before, "after": after, "kind": "control-only" if changed else "unchanged",
        "changed": changed, "paused": paused,
        "requires_fresh_execution": bool(changed),
    }
