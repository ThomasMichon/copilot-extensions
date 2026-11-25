#!/usr/bin/env python3
"""Report agenda placement or explicitly preserve and retire a legacy source."""

from __future__ import annotations

import argparse
import hashlib
from itertools import islice
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from agenda import AgendaError
import agenda_export as export
import agenda_history as history
import agenda_managed as managed
import agenda_rescue as rescue
import agenda_store as store

CAPABILITY = "agenda-placement-doctor-v1"
SCHEMA = "efforts.agenda-placement-doctor"
RETIRED = "retired-source"


def inspect_external(source_home: Path, *, worktree: str, store_id: str) -> dict[str, Any]:
    """Validate an exact owner-published store without initialization or repair."""
    source = store.safe_path(source_home, directory=True)
    result: dict[str, Any] = {"schema": SCHEMA, "version": 1, "source_home": str(source),
              "state": "external-missing", "repairable": False, "store_id": store_id}
    if not source.exists() or not (source / store.DATABASE).exists():
        return result
    try:
        rescue._members(source)
        with store.transaction(source, readonly=True, store_version=2) as db:
            export._size(db)
            snapshot = export._inspect(db, source, worktree, store_id, None, export._Budget())
        result.update(state="external-correct", revision=snapshot["revision"])
    except (AgendaError, OSError, sqlite3.Error, ValueError, KeyError) as exc:
        result.update(state="external-corrupt", error=str(exc))
    return result


def inspect(source_home: Path, *, worktree: str, _container: bool = True) -> dict[str, Any]:
    """Observe one exact legacy source, or its bounded direct-lane container."""
    source = store.safe_path(source_home, directory=True)
    result: dict[str, Any] = {"schema": SCHEMA, "version": 1, "source_home": str(source),
              "state": "absent", "repairable": False}
    if not source.exists():
        return result
    if _container and source.name == ".effort-agenda" and not (source / store.DATABASE).exists():
        members = list(islice(source.iterdir(), 33))
        if len(members) > 32 or any(not member.is_dir() for member in members):
            raise AgendaError("legacy agenda container has unknown members or more than 32 lanes")
        result.update(
            state="legacy-local", repairable=False,
            lanes=[inspect(member, worktree=worktree, _container=False)
                   for member in sorted(members)],
        )
        return result
    rescue._members(source)
    with store.transaction(source, readonly=True, relocation=True) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == store.RELOCATED_VERSION:
            request = rescue._record(db)["request"]
            if request["worktree"] != worktree:
                raise AgendaError("fenced source belongs to another worktree")
            result.update(state="interrupted", store_id=request["store_id"],
                          revision=request["revision"], operation_id=request["operation_id"],
                          export_home=request["export_home"])
        elif version == 2:
            state = store._state(db, source)
            history._header(db, worktree, state["store_id"])
            result.update(state="legacy-local", repairable=True,
                          store_id=state["store_id"], revision=state["revision"])
        else:
            raise AgendaError("unsupported agenda format; retain the source")
    return result


def repair(
    source_home: Path, export_home: Path, *, provider_python: str, project: str,
    worktree: str, creation_nonce: str, lane: str, store_id: str,
    expected_revision: int, operation_id: str,
) -> dict[str, Any]:
    """Preserve complete history and retire only this operation's fenced source."""
    source = store.safe_path(source_home, directory=True)
    saved = store.safe_path(export_home, directory=True)
    retired = store.safe_path(saved.with_name(saved.name + "." + RETIRED), directory=True)
    if source == saved or source in saved.parents or saved in source.parents:
        raise AgendaError("source and recovery export must be disjoint")
    selected = source if source.exists() else retired
    if source.exists() and retired.exists():
        raise AgendaError("both original and retired sources exist; preserve and reconcile")
    rescue._members(selected)
    if not (saved / export.MANIFEST).exists():
        if saved.is_dir() and any(saved.iterdir()):
            observed = inspect(selected, worktree=worktree, _container=False)
            if (
                observed["state"] == "legacy-local"
                and observed["store_id"] == store_id
                and observed["revision"] == expected_revision
            ):
                raise AgendaError(
                    "initial export is incomplete; source is not fenced. Retain the partial "
                    "export and select a different empty export directory before retrying"
                )
            raise AgendaError(
                "published recovery export is unavailable; retain all paths and restore "
                "the exact verified export before retrying fenced-source recovery"
            )
        export.export_store(source, saved, worktree=worktree, store_id=store_id,
                            expected_revision=expected_revision)
    arguments: dict[str, Any] = dict(
        provider_python=provider_python, project=project, worktree=worktree,
        creation_nonce=creation_nonce, lane=lane, store_id=store_id,
        expected_revision=expected_revision, operation_id=operation_id,
    )
    if selected == source:
        placement = rescue.relocate(source, saved, **arguments)["placement"]
    else:
        placement = managed.allocate(provider_python, project, worktree, creation_nonce, lane, store_id)
    target = store.safe_path(Path(placement["state_home"]), directory=True)
    with export._verified_export(
        saved, worktree=worktree, store_id=store_id, expected_revision=expected_revision,
    ) as (_manifest, raw, snapshot, budget):
        with store.transaction(selected, readonly=True, relocation=True) as db:
            record = rescue._record(db)
            request = record["request"]
            expected = {
                "operation_id": operation_id, "source_home": str(source),
                "export_home": str(saved), "worktree": worktree, "store_id": store_id,
                "revision": expected_revision, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "placement": rescue._placement_identity(placement),
            }
            if any(request.get(key) != value for key, value in expected.items()) or (
                rescue._identity(selected) != request["source_directory"]
                or rescue._identity(selected / store.DATABASE) != request["source_database"]
            ):
                raise AgendaError("retirement recovery identity changed; preserve and reconcile")
            if export._table_hashes(db, budget) != _manifest["tables"]:
                raise AgendaError("retained source differs from the verified complete export")
        if rescue._identity(target) != request["destination_directory"] or (
            rescue._identity(target / store.DATABASE) != record["destination_database"]
        ):
            raise AgendaError("retirement destination identity changed")
        with store.transaction(target, readonly=True, store_version=2) as db:
            revision = rescue._prefix(db, snapshot, target, request, budget)
        rescue._members(selected)
        if selected == source:
            # Keep the fenced original with its export. No database or history
            # is deleted. A cross-volume rename refuses without removing it.
            if retired.exists():
                raise AgendaError("retirement destination appeared; preserve and reconcile")
            source.rename(retired)
        parent = source.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        parent = store.safe_path(parent, directory=True)
        with export._pinned_directory(parent) as parent_fd:
            export._sync_directory(parent, parent_fd)
        with export._pinned_directory(saved.parent) as saved_fd:
            export._sync_directory(saved.parent, saved_fd)
    return {"schema": SCHEMA, "version": 1, "state": "external-correct",
            "persisted": True, "source_retired": True, "retired_home": str(retired),
            "state_home": str(target), "store_id": store_id, "revision": revision,
            "export_retained": True}


def main(argv: list[str] | None = None) -> int:
    """Dispatch an explicit report or identity-bound repair."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=("info", "inspect", "inspect-external", "repair"))
    parser.add_argument("--source-home", type=Path)
    parser.add_argument("--export-home", type=Path)
    for name in ("provider-python", "project", "worktree", "creation-nonce", "lane",
                 "store-id", "operation-id"):
        parser.add_argument(f"--{name}")
    parser.add_argument("--expected-revision", type=int)
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        if command == "info":
            result = {"schema": SCHEMA, "version": 1, "capabilities": [CAPABILITY]}
        elif command in ("inspect", "inspect-external"):
            if not args["source_home"] or not args["worktree"]:
                raise AgendaError("inspection requires source-home and worktree")
            if command == "inspect-external":
                if not args["store_id"]:
                    raise AgendaError("external inspection requires the published store ID")
                result = inspect_external(
                    args["source_home"], worktree=args["worktree"], store_id=args["store_id"],
                )
            else:
                result = inspect(args["source_home"], worktree=args["worktree"])
        else:
            if any(value is None for value in args.values()):
                raise AgendaError("repair requires all explicit identities and a recovery export home")
            result = repair(**args)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True))
        return 0
    except (AgendaError, OSError, sqlite3.Error, ValueError, KeyError) as exc:
        print(f"agenda doctor: {exc}; retain source, export and destination", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
