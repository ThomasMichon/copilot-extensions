"""Explicit local snapshot transactions; never native configuration or cloud I/O."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

from . import preferences as codec
from .killswitch_mutation import mutation_lock


def _plain_path(path: Path, *, directory=False, missing_parents=False) -> Path:
    if ".." in path.parts:
        raise codec.PreferenceError("parent-traversal")
    path = Path(os.path.abspath(path))
    for parent in path.parents:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if missing_parents:
                continue
            raise
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise codec.PreferenceError("linked-directory")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if directory:
            raise codec.PreferenceError("state-directory-unavailable")
    else:
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid or getattr(info, "st_file_attributes", 0) & 0x400:
            raise codec.PreferenceError("path-not-regular")
    return path


def _distinct(paths: list[Path]) -> None:
    for index, path in enumerate(paths):
        for other in paths[:index]:
            if path == other or path.exists() and other.exists() and path.samefile(other):
                raise codec.PreferenceError("overlapping-paths")


def _write(path: Path, value: dict) -> None:
    import json

    path = _plain_path(path)
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(raw) > codec.MAX_BYTES:
        raise codec.PreferenceError("document-too-large")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".tmp.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _plain_path(path)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _admitted(candidate: dict | None, policy: dict) -> bool:
    if candidate is None:
        return True
    domains = set(candidate["domains"])
    for fragment in candidate.get("machines", {}).values():
        domains.update(fragment)
    return domains <= set(policy["domains"])


def _state(state: Path):
    state = _plain_path(state, directory=True)
    policy_path = _plain_path(state / "policy.json")
    policy = codec.read(policy_path, validator=codec.validate_policy)
    lock = _plain_path(state / "exchange.lock")
    accepted = _plain_path(state / "accepted.json")
    publication = _plain_path(state / "publication.json")
    paths = [policy_path, lock, accepted, publication]
    _distinct(paths)
    return policy, paths


def _plan(
    operation: str, *, state: Path, source: Path, output: Path | None = None,
    machine: str = "", harness: str = "", local: Path | None = None,
    conflict: bool = False, full_snapshot: bool = False,
) -> tuple[dict, dict | None, dict | None, Path]:
    policy, protected = _state(state)
    source = _plain_path(source, missing_parents=True)
    selected = protected[2] if operation == "accept" else protected[3]
    paths = [*protected, source]
    if output is not None:
        output = _plain_path(output)
        paths.append(output)
    if local is not None:
        local = _plain_path(local)
        paths.append(local)
    _distinct(paths)
    candidate = codec.read(source, optional=True)
    accepted = codec.read(selected, optional=True)
    overrides = codec.read(local, local=True) if local is not None else None
    publisher = policy["publisher"]
    projection = codec.preview(
        candidate, accepted, publisher=publisher,
        machine=machine if operation == "accept" else policy["participant"],
        harness=harness if operation == "accept" else "publication",
        local=overrides, conflict=conflict, full_snapshot=full_snapshot,
    )
    status = projection["status"]
    eligible = status in {"unaccepted", "pending", "unchanged", "full-snapshot"}
    if not _admitted(candidate, policy):
        status, eligible = "domain-not-permitted", False
    remote = None
    if operation == "publish":
        if output is None:
            raise codec.PreferenceError("output-required")
        remote = codec.read(output, optional=True)
        if policy["participant"] != publisher:
            status, eligible = "not-publisher", False
        elif remote is not None:
            if remote["publisher"] != publisher:
                status, eligible = "output-publisher-mismatch", False
            elif candidate is not None and remote["revision"] > candidate["revision"]:
                status, eligible = "newer-output", False
            elif candidate is not None and remote["revision"] == candidate["revision"]:
                if codec.digest(remote) != codec.digest(candidate):
                    status, eligible = "output-revision-conflict", False
            elif accepted is not None and remote["revision"] == accepted["revision"]:
                if codec.digest(remote) != codec.digest(accepted):
                    status, eligible = "output-revision-conflict", False
            elif accepted is not None and remote["revision"] > accepted["revision"]:
                status, eligible = "untracked-output", False
            elif accepted is None:
                status, eligible = "untracked-output", False
    inputs = {
        "operation": operation, "paths": [str(path) for path in paths],
        "policy": policy, "candidate": candidate, "accepted": accepted,
        "output": remote, "local": overrides, "machine": machine, "harness": harness,
        "conflict": conflict, "full_snapshot": full_snapshot,
    }
    result = {
        "schema": "agent-machines.preferences.exchange", "version": 1,
        "operation": operation, "status": status, "eligible": eligible,
        "preview_token": codec.digest(inputs),
        "received_revision": candidate["revision"] if candidate else None,
        "accepted_revision": accepted["revision"] if accepted else None,
        "policy": policy, "applied": False, "local_published": False,
        "remote_receipt": "not-observed",
        "configuration_applied": False, "binding_changed": False,
    }
    if operation == "accept":
        if not eligible and projection["candidate_eligible"]:
            projection = codec.preview(
                None, accepted, publisher=publisher, machine=machine, harness=harness, local=overrides,
            )
        result["projection"] = projection
    return result, candidate, accepted, selected


def exchange(operation: str, *, apply=False, expected="", **arguments) -> dict:
    """Recheck the complete preview under a local-only, bounded native writer lock."""
    if operation not in {"accept", "publish"}:
        raise codec.PreferenceError("unknown-operation")
    if apply and re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise codec.PreferenceError("preview-token-required")
    result, candidate, accepted, selected = _plan(operation, **arguments)
    if not apply:
        return result
    with mutation_lock(selected.parent / "exchange"):
        result, candidate, accepted, selected = _plan(operation, **arguments)
        if result["preview_token"] != expected:
            raise codec.PreferenceError("preview-changed")
        if not result["eligible"]:
            raise codec.PreferenceError(result["status"])
        if accepted != candidate:
            _write(selected, candidate)
        if operation == "publish":
            # Record intent before output: a failed replacement can retry this revision,
            # but a missing/stale carrier can never lower the publisher's local watermark.
            try:
                _write(Path(arguments["output"]), candidate)
            except OSError as error:
                raise codec.PreferenceError("publication-pending") from error
            result["local_published"] = True
        result["applied"] = True
        return result
