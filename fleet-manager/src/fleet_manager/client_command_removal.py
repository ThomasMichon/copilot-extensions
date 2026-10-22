"""Owned command withdrawal, not runtime rollback or installation retirement."""

import os
import re
import sqlite3
import uuid

from . import artifact_staging, client_admission, client_construction as construction
from . import client_frontdoor as frontdoor
from .planning import PlanError, record

HISTORY = "client-command-removals"
FIELDS = {"directory", "removal", "registered", "pending"}


def _saved(namespace):
    return {
        name: construction._read(namespace / filename) if os.path.lexists(namespace / filename) else None
        for name, filename in (("registered", frontdoor.REGISTERED), ("pending", frontdoor.PENDING))
    }


def _proofs(namespace, identity, directory, saved, publication):
    commands = None
    proofs = {}
    for name in ("registered", "pending"):
        value = saved[name]
        if value is None:
            continue
        workspace, family = frontdoor._binding(namespace, identity, directory, value)
        if commands is not None and set(commands) != set(family):
            raise PlanError("client command removal has conflicting command families")
        commands = list(family)
        context = workspace / "copy-context.json"
        forward = workspace / "copy-forward.jsonl"
        if not os.path.lexists(context) and not os.path.lexists(forward) and name == "pending":
            # A pending intent precedes the first native copy effect.
            continue
        root, entries = publication.read_context(context)
        if root != namespace or set(entries) != set(family):
            raise PlanError("client command removal proof has a different root or family")
        receipts = [forward]
        recovery = sorted(workspace.glob("copy-recovery.*.jsonl"))
        if len(recovery) > 32 or any(
            not re.fullmatch(r"copy-recovery\.[1-9][0-9]*\.jsonl", path.name) for path in recovery
        ):
            raise PlanError("client command recovery receipts have an invalid shape or count")
        receipts.extend(recovery)
        for command, states in publication._proofs(
            receipts, root, entries, frontdoor._expected(family),
            recovery_only=name == "pending",
        ).items():
            proofs.setdefault(command, []).extend(states)
    return commands, proofs


def _marker(namespace, identity, directory):
    marker = namespace / frontdoor.REMOVING
    if not os.path.lexists(marker):
        return None
    value = construction._read(marker)
    record(value, "fleet-manager.client-command-removal", FIELDS)
    if (
        set(value) != {"schema", "version", *FIELDS}
        or value["directory"] != str(directory)
        or not isinstance(value["removal"], str)
        or re.fullmatch("[0-9a-f]{32}", value["removal"]) is None
        or value["registered"] is None and value["pending"] is None
    ):
        raise PlanError("client command removal has an invalid directory or intent binding")
    for name in ("registered", "pending"):
        if value[name] is not None:
            frontdoor._binding(namespace, identity, directory, value[name])
    workspace = namespace / HISTORY / value["removal"]
    artifact_staging._directory(workspace)
    if construction._read(workspace / "intent.json") != value:
        raise PlanError("client command removal differs from its retained intent")
    return value


def _unchanged(namespace, saved, *, settling):
    current = _saved(namespace)
    for name in ("registered", "pending"):
        if current[name] != saved[name] and not (settling and current[name] is None):
            raise PlanError("client registration changed during owned command removal")


def unregister_client_frontdoor(root, identity, *, command_directory):
    """Withdraw proved commands, including a pending attempt; preserve all runtime state."""
    identity = client_admission._identity(identity)
    removing = False
    try:
        with (
            client_admission._owned(root) as (path, database_root, unchanged),
            client_admission._root(command_directory) as (directory, _anchor, directory_unchanged),
            client_admission._connection(path, database_root=database_root, readonly=False) as connection,
            artifact_staging._primitive() as primitive,
        ):
            if directory.is_relative_to(path) or path.is_relative_to(directory):
                raise PlanError("client commands require a separate explicit private directory")
            connection.execute("BEGIN IMMEDIATE")
            admitted = client_admission._read_state(connection, identity)
            namespace = path / construction.NAMESPACE
            artifact_staging._directory(namespace)
            construction._namespace(path, identity)
            publication = primitive._runtime_component("versioned_runtime_publication")
            marker = _marker(namespace, identity, directory)
            saved = {name: marker[name] for name in ("registered", "pending")} if marker else _saved(namespace)
            with primitive._publication_lease(namespace):
                commands, proofs = _proofs(namespace, identity, directory, saved, publication)
                if commands is None:
                    commands = frontdoor._command_paths(directory)
                with publication.target_leases(namespace, list(commands)):
                    unchanged()
                    directory_unchanged()
                    _unchanged(namespace, saved, settling=marker is not None)
                    present = []
                    for command in commands:
                        current = publication.state_path(command, private=True)
                        if current is not None:
                            if current not in proofs.get(command, ()):
                                raise PlanError("client command removal preserves an unattributed command")
                            present.append(command)
                    if marker is None and any(saved.values()):
                        history = namespace / HISTORY
                        if not os.path.lexists(history):
                            history.mkdir(mode=0o700)
                            client_admission._sync_directory(namespace)
                        artifact_staging._directory(history)
                        marker = {
                            "schema": "fleet-manager.client-command-removal", "version": 1,
                            "directory": str(directory), "removal": uuid.uuid4().hex, **saved,
                        }
                        workspace = history / marker["removal"]
                        workspace.mkdir(mode=0o700)
                        construction._write(workspace / "intent.json", marker)
                        publication.create_context(
                            workspace / "remove-context.json", namespace,
                            [[str(command), "", ""] for command in commands],
                        )
                        client_admission._sync_directory(workspace)
                        client_admission._sync_directory(history)
                        removing = True
                        marker_source = workspace / "removing.json"
                        construction._write(marker_source, marker)
                        os.replace(marker_source, namespace / frontdoor.REMOVING)
                        client_admission._sync_directory(namespace)
                    if marker is not None:
                        removing = True
                        workspace = namespace / HISTORY / marker["removal"]
                        root_binding, entries = publication.read_context(workspace / "remove-context.json")
                        if (
                            root_binding != namespace or set(entries) != set(commands)
                            or any(backup is not None or before is not None for backup, before in entries.values())
                        ):
                            raise PlanError("client removal context must only withdraw its exact command family")
                        if present:
                            output = workspace / ("remove-" + uuid.uuid4().hex + ".jsonl")
                            publication._recover_locked(
                                namespace, entries, proofs, present,
                                publication.Receipt(output, namespace, present, "outer-recovery"),
                            )
                        client_admission._sync_directory(directory)
                        client_admission._sync_directory(workspace)
                        unchanged()
                        directory_unchanged()
                        _unchanged(namespace, saved, settling=True)
                        if _marker(namespace, identity, directory) != marker:
                            raise PlanError("client removal intent changed before settlement")
                        if any(publication.state_path(command) is not None for command in commands):
                            raise PlanError("client command removal did not settle an absent command family")
                        for filename in (frontdoor.REGISTERED, frontdoor.PENDING):
                            if os.path.lexists(namespace / filename):
                                (namespace / filename).unlink()
                                client_admission._sync_directory(namespace)
                        (namespace / frontdoor.REMOVING).unlink()
                        client_admission._sync_directory(namespace)
                        removing = False
                    return {
                        "schema": "fleet-manager.client-command-unregistration", "version": 1,
                        "identity": identity, "admitted": admitted,
                        "command_directory": str(directory), "commands": [str(command) for command in commands],
                        "frontdoor_registered": False, "commands_absent": True,
                        "pending_registration_withdrawn": saved["pending"] is not None,
                        "mutations_performed": marker is not None, "reused": marker is None,
                        "runtime_selectors_modified": False, "runtime_removed": False,
                        "installation_retired": False, "path_modified": False,
                        "live_readiness_verified": False,
                    }
    except PlanError:
        if removing:
            raise PlanError("client command removal outcome is unknown; retained intent requires owned retry") from None
        raise
    except (OSError, sqlite3.Error, RuntimeError, ValueError):
        raise PlanError(
            "client command removal outcome is unknown; retained intent requires owned retry"
            if removing else "client commands could not establish owned removal"
        ) from None
