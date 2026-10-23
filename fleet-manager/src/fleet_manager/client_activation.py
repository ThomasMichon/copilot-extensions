"""Owned client runtime selectors, not frontdoor registration or fleet activation."""

from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import os
import re
import sqlite3
import subprocess
import time
import uuid

from . import artifact_staging, client_admission, client_construction as construction
from .planning import PlanError, digest, record

ACTIVE = "client-active.json"
PENDING = "client-activation-pending.json"
REMOVING = "client-activation-removing.json"
HISTORY = "client-activations"
SELECTORS = ("current-version", "last-known-good")
FIELDS = {"identity", "candidate", "runtime_sha256", "target_digest", "publication"}
MAX_ATTEMPTS = 16


def _targets(namespace):
    return [namespace / name for name in SELECTORS]


def _expected(namespace, candidate):
    value = hashlib.sha256((candidate + "\n").encode("ascii")).hexdigest()
    return [[str(path), value] for path in _targets(namespace)]


def _state(value, identity):
    record(value, "fleet-manager.client-active", FIELDS)
    if (
        set(value) != {"schema", "version", *FIELDS} or value["identity"] != identity
        or not isinstance(value["candidate"], str)
        or re.fullmatch(r"client-[1-9][0-9]*-[0-9a-f]{64}", value["candidate"]) is None
        or any(not isinstance(value[name], str) or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None
               for name in ("runtime_sha256", "target_digest"))
        or not isinstance(value["publication"], str)
        or re.fullmatch(r"[0-9a-f]{32}", value["publication"]) is None
    ):
        raise PlanError("client active receipt has an invalid installation or candidate binding")
    return value


def _active(namespace, identity, publication):
    _not_removing(namespace)
    if os.path.lexists(namespace / PENDING):
        construction._read(namespace / PENDING)
        raise PlanError("client selector outcome is unknown; owned publication recovery is required")
    if os.path.lexists(namespace / "current"):
        raise PlanError("client selector namespace contains a foreign runtime link")
    if not os.path.lexists(namespace / ACTIVE):
        if any(os.path.lexists(path) for path in _targets(namespace)):
            raise PlanError("client selectors have no attributable activation receipt")
        return None
    active = _state(construction._read(namespace / ACTIVE), identity)
    directory = namespace / HISTORY / active["publication"]
    artifact_staging._directory(directory)
    intent = construction._read(directory / "intent.json")
    record(intent, "fleet-manager.client-selector-intent", {"active", "previous", "before"})
    if intent["active"] != active:
        raise PlanError("client active receipt differs from its publication intent")
    _check_publication(namespace, active, publication)
    return active


def _not_removing(namespace):
    construction._not_removing(namespace)
    if os.path.lexists(namespace / REMOVING):
        raise PlanError("client selector removal is unsettled; owned deactivate retry is required")


def _proofs(namespace, active, publication, *, recovery_only=False):
    directory = namespace / HISTORY / active["publication"]
    artifact_staging._directory(directory)
    context = directory / "context.json"
    root, entries = publication.read_context(context)
    if root != namespace or set(entries) != set(_targets(namespace)):
        raise PlanError("client selector proof has a different root or target set")
    names = [
        "publication.jsonl" if index == 0 else f"publication.{index}.jsonl"
        for index in range(MAX_ATTEMPTS)
    ]
    found = {path.name for path in directory.iterdir() if path.name.startswith("publication")}
    if found != set(names[:len(found)]) or len(found) > MAX_ATTEMPTS:
        raise PlanError("client selector publication history is invalid or exceeds its bound")
    receipts = [directory / name for name in names[:len(found)]]
    proofs = publication._proofs(
        receipts, root, entries,
        _expected(namespace, active["candidate"]),
        recovery_only=recovery_only,
    ) if receipts else {}
    return directory, entries, proofs, len(receipts)


def _check_publication(namespace, active, publication):
    # Caller already owns the canonical, non-reentrant selector publication lease.
    _, _, proofs, _ = _proofs(namespace, active, publication)
    expected = dict(_expected(namespace, active["candidate"]))
    for target in _targets(namespace):
        current = publication.state_path(target, private=True)
        if current not in proofs.get(target, ()) or current.digest != expected[str(target)]:
            raise PlanError("client selector is not its producer-created publication object")


def _pending(namespace, active, publication):
    if os.path.lexists(namespace / "current"):
        raise PlanError("pending client selector namespace contains a foreign runtime link")
    directory, _, proofs, attempts = _proofs(namespace, active, publication)
    intent = construction._read(directory / "intent.json")
    record(intent, "fleet-manager.client-selector-intent", {"active", "previous", "before"})
    if intent["active"] != active or not isinstance(intent["before"], dict) or (
        set(intent["before"]) != set(SELECTORS)
    ):
        raise PlanError("pending client selector intent has a different binding")
    previous = intent["previous"]
    if previous is not None:
        _state(previous, active["identity"])
    existing = (construction._read(namespace / ACTIVE)
                if os.path.lexists(namespace / ACTIVE) else None)
    if existing not in (previous, active):
        raise PlanError("pending client selector receipt has a foreign active owner")
    for target in _targets(namespace):
        before = intent["before"][target.name]
        baseline = publication.PhysicalState.parse(before) if before is not None else None
        current = publication.state_path(target, private=True)
        if current != baseline and current not in proofs.get(target, ()):
            raise PlanError("pending client selector has an unproved or foreign publication object")
    return previous, directory, attempts


def _prepare(namespace, active, previous, publication):
    history = namespace / HISTORY
    if os.path.lexists(history):
        artifact_staging._directory(history)
    else:
        history.mkdir(mode=0o700)
        client_admission._sync_directory(namespace)
    directory = history / active["publication"]
    directory.mkdir(mode=0o700)
    before, bindings = {}, []
    for index, target in enumerate(_targets(namespace)):
        state = publication.state_path(target, private=True)
        before[target.name] = asdict(state) if state else None
        backup = ""
        if state is not None:
            data = target.read_bytes()
            if hashlib.sha256(data).hexdigest() != state.digest:
                raise PlanError("client selector changed while its backup was captured")
            saved = directory / f"before.{index}"
            with saved.open("xb") as stream:
                os.chmod(saved, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if publication.state_path(target, private=True) != state:
                raise PlanError("client selector changed while its backup was captured")
            backup = str(saved)
        bindings.append([str(target), backup, state.digest if state else ""])
    construction._write(directory / "intent.json", {
        "schema": "fleet-manager.client-selector-intent", "version": 1,
        "active": active, "previous": previous, "before": before,
    })
    publication.create_context(directory / "context.json", namespace, bindings)
    client_admission._sync_directory(directory)
    client_admission._sync_directory(history)
    return directory


def activate_client_runtime(root, identity, selection, staged, *,
                            current_policy, current_target, clock=lambda: int(time.time())):
    """Select an already constructed client under fresh policy, bytes and local health."""
    identity, selection, binding = construction._request(
        identity, selection, staged, current_policy, current_target,
    )
    evidence = artifact_staging.diagnose_client()
    publishing = False
    try:
        with (
            client_admission._owned(root) as (path, database_root, unchanged),
            client_admission._connection(
                path, database_root=database_root, readonly=False,
            ) as connection,
            artifact_staging._primitive() as primitive,
            ExitStack() as retained,
        ):
            connection.execute("BEGIN IMMEDIATE")

            def checkpoint():
                return construction._checkpoint(
                    connection, identity, selection, binding, staged,
                    current_policy, current_target, clock, unchanged,
                )

            checkpoint()
            namespace = path / construction.NAMESPACE
            artifact_staging._directory(namespace)
            construction._namespace(path, identity)
            construction._not_removing(namespace)
            version = f"client-{selection['sequence']}-{selection['release_digest']}"
            with primitive.construction_lease(namespace, version) as descriptor:
                completion = construction._completion(primitive, namespace, version, binding)
                retained.enter_context(primitive.retention_lease(
                    namespace, version, payload_hash=digest(completion),
                ))
                slot = primitive.version_dir(namespace, version)
                publication = primitive._runtime_component("versioned_runtime_publication")
                # The canonical selector lease spans validation, health and publication.
                with primitive._publication_lease(namespace):
                    _not_removing(namespace)
                    pending = None
                    if os.path.lexists(namespace / PENDING):
                        publishing = True
                        pending = _state(construction._read(namespace / PENDING), identity)
                        if (pending["candidate"], pending["runtime_sha256"]) != (
                            version, completion["runtime_sha256"],
                        ):
                            raise PlanError("pending client publication requires its exact admitted candidate")
                        previous, directory, attempts = _pending(namespace, pending, publication)
                    else:
                        previous = _active(namespace, identity, publication)
                    if previous:
                        retained.enter_context(primitive.retention_lease(
                            namespace, previous["candidate"],
                        ))
                    health = construction._health(slot, checkpoint, descriptor, evidence)
                    _, target, admitted = checkpoint()
                    construction._completion(primitive, namespace, version, binding)
                    if pending:
                        if construction._read(namespace / PENDING) != pending or (
                            _pending(namespace, pending, publication) != (previous, directory, attempts)
                        ):
                            raise PlanError("pending client selector changed before publication")
                    elif _active(namespace, identity, publication) != previous:
                        raise PlanError("client active receipt changed before publication")
                    reused = pending is None and previous is not None and previous["candidate"] == version
                    if reused and previous["runtime_sha256"] != completion["runtime_sha256"]:
                        raise PlanError("active client bytes differ from their activation receipt")
                    if not reused:
                        active = pending or {
                            "schema": "fleet-manager.client-active", "version": 1,
                            "identity": identity, "candidate": version,
                            "runtime_sha256": completion["runtime_sha256"],
                            "target_digest": digest(target), "publication": uuid.uuid4().hex,
                        }
                        if active["target_digest"] != digest(target):
                            raise PlanError("pending client publication requires its exact compatible target")

                        def publication_checkpoint():
                            _, current, admitted = checkpoint()
                            if digest(current) != active["target_digest"]:
                                raise PlanError("client target changed during selector publication")
                            return current, admitted

                        if pending is None:
                            directory = _prepare(namespace, active, previous, publication)
                            attempts = 0
                        if attempts >= MAX_ATTEMPTS:
                            raise PlanError("client selector publication exhausted its bounded retries")
                        publication_checkpoint()
                        receipt = directory / (
                            "publication.jsonl" if attempts == 0 else f"publication.{attempts}.jsonl"
                        )

                        def before_effects():
                            nonlocal publishing
                            publication_checkpoint()
                            client_admission._sync_directory(directory)
                            publishing = True
                            if pending is None:
                                construction._write(namespace / PENDING, active)

                        publication.publish_selectors(
                            namespace, version, receipt, None, before_effects=before_effects,
                        )
                        client_admission._sync_directory(namespace)
                        # Recheck before acknowledging the separate, non-atomic selector writes.
                        target, admitted = publication_checkpoint()
                        construction._completion(primitive, namespace, version, binding)
                        _check_publication(namespace, active, publication)
                        result = directory / "active-result.json"
                        if os.path.lexists(result):
                            if construction._read(result) != active:
                                raise PlanError("client active result differs from its pending intent")
                        else:
                            construction._write(result, active)
                        os.replace(result, namespace / ACTIVE)
                        client_admission._sync_directory(directory)
                        client_admission._sync_directory(namespace)
                        (namespace / PENDING).unlink()
                        client_admission._sync_directory(namespace)
                        publishing = False
                    return {
                        "schema": "fleet-manager.client-runtime-activation", "version": 1,
                        "identity": identity, "candidate": version, "admitted": admitted,
                        "runtime_sha256": completion["runtime_sha256"],
                        "target_digest": digest(target), "health": health,
                        "active": True, "reused": reused, "mutations_performed": not reused,
                        "frontdoor_registered": False, "live_readiness_verified": False,
                    }
    except PlanError:
        if publishing:
            raise PlanError(
                "client selector outcome is unknown; retained publication evidence requires owned recovery"
            ) from None
        raise
    except (OSError, sqlite3.Error, subprocess.SubprocessError, RuntimeError, ValueError):
        raise PlanError(
            "client selector outcome is unknown; retained publication evidence requires owned recovery"
            if publishing else "client runtime activation could not establish owned selectors"
        ) from None
