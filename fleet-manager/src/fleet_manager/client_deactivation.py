"""Owned selector withdrawal, never runtime deletion or retained-good rollback."""

import os
import re
import sqlite3
import uuid

from . import artifact_staging, client_activation as activation, client_admission
from . import client_construction as construction, client_frontdoor as frontdoor
from .planning import PlanError, record

HISTORY = "client-deactivations"
FIELDS = {"identity", "removal", "active", "pending"}


def _saved(namespace):
    return {
        name: construction._read(namespace / filename) if os.path.lexists(namespace / filename) else None
        for name, filename in (("active", activation.ACTIVE), ("pending", activation.PENDING))
    }


def _unregistered(namespace):
    if any(os.path.lexists(namespace / name) for name in (
        frontdoor.REGISTERED, frontdoor.PENDING, frontdoor.REMOVING,
    )):
        raise PlanError("client selector withdrawal requires settled command unregistration first")


def _proofs(namespace, identity, saved, publication):
    values = [value for value in saved.values() if value is not None]
    if saved["pending"] is not None:
        pending = activation._state(saved["pending"], identity)
        intent = construction._read(namespace / activation.HISTORY / pending["publication"] / "intent.json")
        record(intent, "fleet-manager.client-selector-intent", {"active", "previous", "before"})
        if saved["active"] not in (intent["previous"], pending):
            raise PlanError("pending client deactivation has a different active owner")
        if intent["previous"] is not None:
            values.append(intent["previous"])
    proofs = {}
    for value in values:
        activation._state(value, identity)
        directory, _entries, states, _attempts = activation._proofs(
            namespace, value, publication,
            recovery_only=value == saved["pending"] and value != saved["active"],
        )
        intent = construction._read(directory / "intent.json")
        record(intent, "fleet-manager.client-selector-intent", {"active", "previous", "before"})
        if intent["active"] != value or not isinstance(intent["before"], dict) or (
            set(intent["before"]) != set(activation.SELECTORS)
        ):
            raise PlanError("client deactivation proof has a different activation intent")
        for target, created in states.items():
            proofs.setdefault(target, []).extend(created)
    return proofs


def _marker(namespace, identity):
    path = namespace / activation.REMOVING
    if not os.path.lexists(path):
        return None
    value = construction._read(path)
    record(value, "fleet-manager.client-deactivation-intent", FIELDS)
    if (
        set(value) != {"schema", "version", *FIELDS} or value["identity"] != identity
        or not isinstance(value["removal"], str)
        or re.fullmatch("[0-9a-f]{32}", value["removal"]) is None
        or value["active"] is None and value["pending"] is None
    ):
        raise PlanError("client deactivation has an invalid installation or intent binding")
    workspace = namespace / HISTORY / value["removal"]
    artifact_staging._directory(workspace)
    if construction._read(workspace / "intent.json") != value:
        raise PlanError("client deactivation differs from its retained intent")
    return value


def _unchanged(namespace, saved, *, settling):
    for name, value in _saved(namespace).items():
        if value != saved[name] and not (settling and value is None):
            raise PlanError("client activation changed during owned selector withdrawal")


def deactivate_client_runtime(root, identity):
    """Withdraw only proved selectors after command removal; retain all candidate bytes."""
    identity = client_admission._identity(identity)
    removing = False
    try:
        with (
            client_admission._owned(root) as (path, database_root, unchanged),
            client_admission._connection(path, database_root=database_root, readonly=False) as connection,
            artifact_staging._primitive() as primitive,
        ):
            connection.execute("BEGIN IMMEDIATE")
            admitted = client_admission._read_state(connection, identity)
            namespace = path / construction.NAMESPACE
            artifact_staging._directory(namespace)
            construction._namespace(path, identity)
            publication = primitive._runtime_component("versioned_runtime_publication")
            # Both selector targets share this canonical, non-reentrant lease.
            with publication.target_leases(namespace, activation._targets(namespace)):
                marker = _marker(namespace, identity)
                saved = {name: marker[name] for name in ("active", "pending")} if marker else _saved(namespace)
                _unregistered(namespace)
                if os.path.lexists(namespace / "current"):
                    raise PlanError("client deactivation preserves a foreign runtime link")
                proofs = _proofs(namespace, identity, saved, publication)
                targets = activation._targets(namespace)
                unchanged()
                _unchanged(namespace, saved, settling=marker is not None)
                present = []
                for target in targets:
                    state = publication.state_path(target, private=True)
                    if state is not None:
                        if state not in proofs.get(target, ()):
                            raise PlanError("client deactivation preserves an unattributed selector")
                        present.append(target)
                if marker is None and any(saved.values()):
                    history = namespace / HISTORY
                    if not os.path.lexists(history):
                        history.mkdir(mode=0o700)
                        client_admission._sync_directory(namespace)
                    artifact_staging._directory(history)
                    marker = {
                        "schema": "fleet-manager.client-deactivation-intent", "version": 1,
                        "identity": identity, "removal": uuid.uuid4().hex, **saved,
                    }
                    workspace = history / marker["removal"]
                    workspace.mkdir(mode=0o700)
                    construction._write(workspace / "intent.json", marker)
                    publication.create_context(
                        workspace / "remove-context.json", namespace,
                        [[str(target), "", ""] for target in targets],
                    )
                    client_admission._sync_directory(workspace)
                    client_admission._sync_directory(history)
                    removing = True
                    source = workspace / "removing.json"
                    construction._write(source, marker)
                    os.replace(source, namespace / activation.REMOVING)
                    client_admission._sync_directory(namespace)
                if marker is not None:
                    removing = True
                    workspace = namespace / HISTORY / marker["removal"]
                    root_binding, entries = publication.read_context(workspace / "remove-context.json")
                    if (
                        root_binding != namespace or set(entries) != set(targets)
                        or any(backup is not None or before is not None for backup, before in entries.values())
                    ):
                        raise PlanError("client deactivation context must only withdraw its exact selectors")
                    if present:
                        output = workspace / ("remove-" + uuid.uuid4().hex + ".jsonl")
                        publication._recover_locked(
                            namespace, entries, proofs, present,
                            publication.Receipt(output, namespace, present, "outer-recovery"),
                        )
                    client_admission._sync_directory(namespace)
                    client_admission._sync_directory(workspace)
                    unchanged()
                    _unregistered(namespace)
                    _unchanged(namespace, saved, settling=True)
                    if _marker(namespace, identity) != marker:
                        raise PlanError("client deactivation intent changed before settlement")
                    if any(publication.state_path(target) is not None for target in targets):
                        raise PlanError("client deactivation did not settle absent selectors")
                    for filename in (activation.ACTIVE, activation.PENDING):
                        if os.path.lexists(namespace / filename):
                            (namespace / filename).unlink()
                            client_admission._sync_directory(namespace)
                    from .client_recovery import WITHDRAWN

                    construction._write(workspace / "settled.json", marker)
                    construction._write(namespace / WITHDRAWN, marker)
                    (namespace / activation.REMOVING).unlink()
                    client_admission._sync_directory(namespace)
                    removing = False
                return {
                    "schema": "fleet-manager.client-runtime-deactivation", "version": 1,
                    "identity": identity, "admitted": admitted,
                    "active": False, "selectors_absent": True, "frontdoor_registered": False,
                    "pending_activation_withdrawn": saved["pending"] is not None,
                    "mutations_performed": marker is not None, "reused": marker is None,
                    "runtime_removed": False, "installation_retired": False,
                    "path_modified": False, "live_readiness_verified": False,
                }
    except PlanError:
        if removing:
            raise PlanError("client deactivation outcome is unknown; retained intent requires owned retry") from None
        raise
    except (OSError, sqlite3.Error, RuntimeError, ValueError):
        raise PlanError(
            "client deactivation outcome is unknown; retained intent requires owned retry"
            if removing else "client selectors could not establish owned withdrawal"
        ) from None
