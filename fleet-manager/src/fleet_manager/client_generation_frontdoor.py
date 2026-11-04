"""Generation commands settled from native copy proofs, separately from runtime startup."""

from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

from . import artifact_staging, client_admission as admission
from . import client_construction as construction, client_frontdoor as frontdoor
from . import client_generations as generations
from .planning import PlanError, canonical, decode, digest, record

HISTORY = "client-generation-frontdoors"
REMOVAL = "remove-intent.json"


def inspect_command_targets(root, identity, reservation, *, command_directory):
    """Observe fixed incoming targets; never infer permission from old receipts."""
    identity = admission._identity(identity)
    reservation = generations._reservation(reservation, identity)
    try:
        observed = generations.inspect_client_generations(
            root, identity, generation=reservation["generation"],
        )
        if observed["reservations"].get(reservation["generation"]) != reservation:
            raise PlanError("command targets require the exact existing generation reservation")
        with admission._root(command_directory) as (directory, _anchor, unchanged):
            if directory.is_relative_to(Path(root)) or Path(root).is_relative_to(directory):
                raise PlanError("client commands require a separate explicit private directory")
            with artifact_staging._primitive() as primitive:
                publication = primitive._runtime_component("versioned_runtime_publication")
                targets = frontdoor._command_paths(directory)
                with publication.target_leases(Path(root), targets):
                    result = _command_targets(identity, reservation, directory, targets, publication)
                    unchanged()
                    return result
    except PlanError:
        raise
    except (OSError, sqlite3.Error, subprocess.SubprocessError, RuntimeError, ValueError):
        raise PlanError("client command targets could not be inspected without mutation") from None


def _command_targets(identity, reservation, directory, targets, publication):
    states = {}
    for path in targets:
        physical = publication.state_path(path, private=True)
        states[path.name] = None if physical is None else asdict(physical)
    return {
        "schema": "fleet-manager.client-command-targets", "version": 1,
        "identity": identity, "reservation_digest": digest(reservation),
        "directory": generations._location(directory), "targets": states,
        "mutations_performed": False,
    }


def _pointer(connection, reservations):
    return generations._publication(connection.execute(
        "SELECT publication FROM lineage WHERE singleton = 1",
    ).fetchone()[0], reservations)


def _attempt(root, value, reservations):
    workspace = root / HISTORY / value["publication"]
    artifact_staging._directory(workspace)
    intent = construction._read(workspace / "intent.json")
    if generations._publication(canonical(intent).decode("utf-8"), reservations) != value:
        raise PlanError("client generation command intent differs from its lineage binding")
    generation = value["generation"]
    reservation = reservations[generation]
    candidate = reservation["selection"]
    slot = (
        root / ("generation-" + generation) / construction.NAMESPACE / "versions"
        / f"client-{candidate['sequence']}-{candidate['release_digest']}"
    )
    return workspace, frontdoor._commands(Path(value["directory"]["path"]), slot)


def _proofs(root, value, reservations, publication):
    workspace, commands = _attempt(root, value, reservations)
    proof_root, entries = publication.read_context(workspace / "copy-context.json")
    if proof_root != root or set(entries) != set(commands):
        raise PlanError("client generation command proof has a different root or command family")
    receipts = [workspace / "copy-forward.jsonl"]
    recoveries = sorted(workspace.glob("copy-recovery.*.jsonl"))
    for path in recoveries:
        if path.name != f"copy-recovery.{len(receipts)}.jsonl":
            raise PlanError("client generation command recovery proofs are ambiguous")
        receipts.append(path)
    return commands, publication._proofs(
        receipts, root, entries, frontdoor._expected(commands), recovery_only=True,
    )


def _continued_targets(root, value, reservations, commands, publication):
    _commands, proofs = _proofs(root, value, reservations, publication)
    proof_root, entries = publication.read_context(
        root / HISTORY / value["publication"] / "copy-context.json",
    )
    if proof_root != root or set(entries) != set(commands):
        raise PlanError("incoming attempt context differs from its fixed command family")
    for path in commands:
        actual = publication.state_path(path, private=True)
        if actual != entries[path][1] and actual not in proofs.get(path, ()):
            raise PlanError("incoming attempt targets changed outside its recorded progress")


def _legacy_commands(origin, identity, directory, publication):
    legacy = origin["legacy"]
    if legacy is None:
        return None
    path = Path(legacy["location"]["path"])
    if generations._location(path) != legacy["location"]:
        raise PlanError("client legacy command origin changed")
    if list(artifact_staging._identity(admission._ordinary_file(
        path / admission.DATABASE, sqlite_state=True,
    ))) != legacy["database"]:
        raise PlanError("client legacy command database changed")
    namespace = path / construction.NAMESPACE
    if not os.path.lexists(namespace):
        return None
    artifact_staging._directory(namespace)
    if construction._read(namespace / construction.OWNER) != {
        "schema": "fleet-manager.client-runtime-owner", "version": 1, "identity": identity,
    }:
        raise PlanError("client legacy command namespace has a foreign owner")
    return frontdoor._receipt(namespace, identity, directory, publication)


def _owned_commands(root, previous, origin, identity, reservations, directory, commands, publication):
    """Only the DB-anchored producer chain can attribute an existing command object."""
    actual = {path: publication.state_path(path, private=True) for path in commands}
    unmatched = {path for path, value in actual.items() if value is not None}
    seen = set()
    value = previous
    legacy = _legacy_commands(origin, identity, directory, publication) if previous is None else None
    while value is not None:
        attempt = value["publication"]
        if attempt in seen or value["directory"] != generations._location(directory):
            raise PlanError("client generation command history is cyclic or has a different directory")
        seen.add(attempt)
        _commands, proofs = _proofs(root, value, reservations, publication)
        unmatched.difference_update({
            path for path in unmatched if actual[path] in proofs.get(path, ())
        })
        if not unmatched:
            return
        predecessor = value["previous"]
        value = None if predecessor is None else generations._publication(
            canonical(construction._read(root / HISTORY / predecessor / "intent.json")).decode("utf-8"),
            reservations,
        )
        if value is not None and value["publication"] != predecessor:
            raise PlanError("client generation command history has a different predecessor")
    if unmatched:
        if legacy is None:
            legacy = _legacy_commands(origin, identity, directory, publication)
        if legacy is not None:
            return
    if unmatched:
        raise PlanError("client command directory contains foreign or same-byte replacement objects")


@contextmanager
def _writer(root, identity, reservation, location, authenticate, manifest, staged,
            current_policy, current_target, clock, directory, directory_unchanged,
            generation_checkpoint):
    # This is entered only after canonical command-target custody is held.
    with admission._owned(root) as (path, database_root, unchanged):
        with admission._connection(
            path, database_root=database_root, readonly=False,
        ) as connection:
            admission._begin_write(connection, path)

            def checkpoint():
                unchanged()
                directory_unchanged()
                generation_checkpoint()
                admitted, origin, reservations, fences = generations._read(
                    connection, identity, location,
                )
                generations._current(admitted, reservations, reservation, fences, shared=True)
                policy = decode(canonical(current_policy()))
                evidence = authenticate(policy, clock=clock)
                if (
                    evidence["release_digest"] != reservation["selection"]["release_digest"]
                    or evidence["sequence"] != reservation["selection"]["sequence"]
                    or sorted(staged.artifacts) != reservation["selection"]["components"]
                    or digest(construction._compatible(decode(manifest), current_target(), identity))
                    != reservation["target_digest"]
                ):
                    raise PlanError("client generation publication policy or target changed")
                current = _pointer(connection, reservations)
                if current is not None and current["directory"] != generations._location(directory):
                    raise PlanError("client lineage already binds a different default command directory")
                unchanged()
                return admitted, origin, reservations, fences, current

            checkpoint()
            yield connection, checkpoint


def _acknowledge(connection, identity, location, reservation, value):
    connection.execute(
        "UPDATE lineage SET selected_generation = ?, selected_publication = ? WHERE singleton = 1",
        (reservation["generation"], value["publication"]),
    )
    admission._commit(connection)
    _admitted, _origin, reservations, fences = generations._read(connection, identity, location)
    if (
        fences["selected_generation"] != reservation["generation"]
        or _pointer(connection, reservations) != value
        or connection.execute(
            "SELECT selected_publication FROM lineage WHERE singleton = 1",
        ).fetchone()[0] != value["publication"]
    ):
        raise PlanError("client generation selection acknowledgement outcome is unknown")


def publish_client_generation(root, identity, reservation, staged, *, command_directory,
                              current_policy, current_target, replacement_targets=None,
                              clock=lambda: int(time.time())):
    """Publish a healthy installed generation; never roll back after releasing lineage custody."""
    identity = admission._identity(identity)
    reservation = generations._reservation(reservation, identity)
    incoming_digest = None
    if replacement_targets is not None:
        replacement_targets = decode(canonical(replacement_targets))
        record(replacement_targets, "fleet-manager.client-command-targets", {
            "identity", "reservation_digest", "directory", "targets", "mutations_performed",
        })
        incoming_digest = digest(replacement_targets)
    construction._request(identity, reservation["selection"], staged, current_policy, current_target)
    intent_durable = False
    try:
        policy, target = construction._verified(
            identity, reservation["selection"], staged, current_policy, current_target, clock,
        )
        if digest(target) != reservation["target_digest"]:
            raise PlanError("client generation publication target differs from its reservation")
        generation_root, proof, binding = generations._generation_root(root, identity, reservation)
        with (
            generations._generation_custody(
                root, generation_root, proof, binding, identity, reservation, staged,
                current_policy, current_target, clock,
            ) as (_generation, generation_unchanged, generation_checkpoint),
            admission._root(command_directory) as (directory, _anchor, directory_unchanged),
            artifact_staging._primitive() as primitive,
            staged._retained_authentication(policy, clock=clock) as (manifest, authenticate),
            ExitStack() as retained,
        ):
            root = Path(root)
            if any(directory.is_relative_to(other) or other.is_relative_to(directory)
                   for other in (root, staged._root)):
                raise PlanError("client commands require a separate explicit private directory")
            namespace = generation_root / construction.NAMESPACE
            selection = reservation["selection"]
            candidate = f"client-{selection['sequence']}-{selection['release_digest']}"
            descriptor = retained.enter_context(primitive.construction_lease(namespace, candidate))
            completion = construction._completion(primitive, namespace, candidate, binding)
            retained.enter_context(primitive.retention_lease(
                namespace, candidate, payload_hash=digest(completion),
            ))
            slot = primitive.version_dir(namespace, candidate)
            evidence = artifact_staging.diagnose_client()
            health = construction._health(slot, generation_checkpoint, descriptor, evidence)
            commands = frontdoor._commands(directory, slot)
            publication = primitive._runtime_component("versioned_runtime_publication")
            location = binding["lineage"]

            def writer():
                def generation_fence():
                    generation_unchanged()
                    generations._owned_generation(generation_root, proof, binding)

                return _writer(
                    root, identity, reservation, location, authenticate, manifest, staged,
                    current_policy, current_target, clock, directory, directory_unchanged,
                    generation_fence,
                )

            def healthy():
                directory_unchanged()
                generation_checkpoint()
                construction._completion(primitive, namespace, candidate, binding)

            def same_incoming(value):
                return (
                    value is not None and value["generation"] == reservation["generation"]
                    and value.get("replacement_targets_digest") == incoming_digest
                    and not os.path.lexists(root / HISTORY / value["publication"] / REMOVAL)
                )

            def admit_targets(current, origin, reservations):
                if replacement_targets is None:
                    _owned_commands(
                        root, current, origin, identity, reservations, directory, commands, publication,
                    )
                elif replacement_targets == _command_targets(
                    identity, reservation, directory, commands, publication,
                ):
                    return
                elif same_incoming(current):
                    _continued_targets(root, current, reservations, commands, publication)
                else:
                    raise PlanError("explicit incoming command targets changed or differ from this request")

            # Reuse also takes targets first. No lineage lock is held across health.
            with publication.target_leases(root, list(commands)):
                with writer() as (_connection, checkpoint):
                    _admitted, _origin, reservations, fences, previous = checkpoint()
                    reuse = (
                        previous is not None
                        and previous["generation"] == reservation["generation"]
                        and (replacement_targets is None or same_incoming(previous))
                    )
                    if reuse and os.path.lexists(root / HISTORY / previous["publication"] / REMOVAL):
                        reuse = False
                    if reuse:
                        try:
                            _commands, proofs = _proofs(root, previous, reservations, publication)
                            actual = {
                                path: publication.state_path(path, private=True) for path in commands
                            }
                            reuse = all(
                                actual[path] is not None and actual[path] in proofs.get(path, ())
                                and actual[path].digest == hashlib.sha256(content).hexdigest()
                                for path, content in commands.items()
                            )
                        except (OSError, RuntimeError, ValueError):
                            if replacement_targets is None:
                                raise
                            reuse = False
                if reuse:
                    frontdoor._health(directory, slot, healthy, descriptor, evidence, health)
                    with writer() as (connection, checkpoint):
                        admitted, _origin, reservations, fences, current = checkpoint()
                        if current != previous:
                            raise PlanError("client generation command attempt changed before acknowledgement")
                        _commands, proofs = _proofs(root, current, reservations, publication)
                        for path, expected in frontdoor._expected(commands):
                            actual = publication.state_path(Path(path), private=True)
                            if actual not in proofs.get(Path(path), ()) or actual.digest != expected:
                                raise PlanError("client generation commands changed before acknowledgement")
                        acknowledged = connection.execute(
                            "SELECT selected_publication FROM lineage WHERE singleton = 1",
                        ).fetchone()[0] == previous["publication"]
                        if not acknowledged:
                            intent_durable = True
                            _acknowledge(connection, identity, location, reservation, current)
                    return _result(
                        reservation, current, directory, commands, admitted, health, reused=True,
                        mutations=not acknowledged,
                    )

            history = root / HISTORY
            if not os.path.lexists(history):
                history.mkdir(mode=0o700, exist_ok=True)
                admission._sync_directory(root)
            artifact_staging._directory(history)
            attempt = uuid.uuid4().hex
            workspace = history / attempt
            workspace.mkdir(mode=0o700)
            copies = []
            for index, (command, content) in enumerate(commands.items()):
                source = workspace / f"command.{index}"
                with source.open("xb") as stream:
                    os.chmod(source, 0o600)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                copies.append(publication.CopyBinding(
                    source, command, hashlib.sha256(content).hexdigest(), executable=os.name != "nt",
                ))
            value = None
            with ExitStack() as central:
                transaction = None

                def validate():
                    nonlocal transaction
                    if transaction is None:
                        transaction = central.enter_context(writer())
                    connection, checkpoint = transaction
                    _admitted, origin, reservations, _fences, current = checkpoint()
                    if value is None:
                        admit_targets(current, origin, reservations)
                    elif current != value:
                        raise PlanError("client generation publication intent changed during copy")

                def before_effects():
                    nonlocal value, intent_durable
                    connection, checkpoint = transaction
                    _admitted, origin, reservations, _fences, current = checkpoint()
                    if replacement_targets is not None:
                        admit_targets(current, origin, reservations)
                    value = {
                        "schema": "fleet-manager.client-generation-frontdoor", "version": 1,
                        "generation": reservation["generation"], "request_digest": digest(reservation),
                        "publication": attempt,
                        "previous": None if current is None else current["publication"],
                        "directory": generations._location(directory),
                        "target_digest": reservation["target_digest"],
                    }
                    if incoming_digest is not None:
                        value["replacement_targets_digest"] = incoming_digest
                    construction._write_exclusive(workspace / "intent.json", value)
                    admission._sync_directory(workspace)
                    admission._sync_directory(history)
                    connection.execute("UPDATE lineage SET publication = ? WHERE singleton = 1",
                                       (canonical(value).decode("utf-8"),))
                    intent_durable = True
                    admission._commit(connection)
                    admission._begin_write(connection, root)
                    validate()

                copy = publication.CopyPublication(
                    root, workspace, copies, validate, before_effects=before_effects,
                )
                try:
                    copy.publish()
                    admission._sync_directory(workspace)
                    admission._sync_directory(directory)
                    central.close()
                    frontdoor._health(directory, slot, healthy, descriptor, evidence, health)
                    copy.verify()
                    with writer() as (connection, checkpoint):
                        admitted, _origin, _reservations, _fences, current = checkpoint()
                        if current != value:
                            raise PlanError("client generation command attempt changed before acknowledgement")
                        copy.verify()
                        _acknowledge(connection, identity, location, reservation, current)
                    return _result(
                        reservation, current, directory, commands, admitted, health, reused=False, mutations=True,
                    )
                finally:
                    central.close()
                    # Keep the actual producer evidence, including failed/partial outcomes.
                    copy._release()
    except PlanError:
        if intent_durable:
            raise PlanError(
                "client generation command outcome is unknown; installed runtime and publication proofs retained"
            ) from None
        raise
    except (OSError, sqlite3.Error, subprocess.SubprocessError, RuntimeError, ValueError):
        raise PlanError(
            "client generation command outcome is unknown; installed runtime and publication proofs retained"
            if intent_durable else "client generation could not establish owned default commands"
        ) from None


def _result(reservation, value, directory, commands, admitted, health, *, reused, mutations):
    return {
        "schema": "fleet-manager.client-generation-publication", "version": 1,
        "generation": reservation["generation"], "request": reservation["request"],
        "publication": value["publication"],
        "command_directory": str(directory), "commands": [str(path) for path in commands],
        "admitted": admitted, "health": health, "installed": True, "started": True,
        "selected": True, "registered": True, "reused": reused,
        "mutations_performed": mutations, "path_modified": False,
        "predecessor_cleanup": "not-attempted", "live_readiness_verified": False,
    }


def _find_attempt(root, current, attempt, reservations):
    seen = set()
    value = current
    while value is not None:
        if value["publication"] in seen:
            raise PlanError("client generation command history is cyclic")
        seen.add(value["publication"])
        _attempt(root, value, reservations)
        if value["publication"] == attempt:
            return value
        predecessor = value["previous"]
        value = None if predecessor is None else generations._publication(
            canonical(construction._read(root / HISTORY / predecessor / "intent.json")).decode("utf-8"),
            reservations,
        )
        if value is not None and value["publication"] != predecessor:
            raise PlanError("client generation command history has a different predecessor")
    raise PlanError("client withdrawal requires an anchored publication attempt")


def _removal_context(root, workspace, value, commands, publication):
    intent = {
        "schema": "fleet-manager.client-generation-command-removal", "version": 1,
        "publication": value,
    }
    marker = workspace / REMOVAL
    if os.path.lexists(marker):
        saved = construction._read(marker)
        record(saved, intent["schema"], {"publication"})
        if saved != intent:
            raise PlanError("client command removal differs from its exact publication")
    context = workspace / "remove-context.json"
    if not os.path.lexists(context):
        if os.path.lexists(marker):
            raise PlanError("client command removal lost its retained context")
        temporary = context.with_name("." + context.name + ".write-" + uuid.uuid4().hex)
        publication.create_context(temporary, root, [[str(path), "", ""] for path in commands])
        publication._publish_receipt(temporary, context)
        admission._sync_directory(workspace)
    proof_root, entries = publication.read_context(context)
    if (
        proof_root != root or set(entries) != set(commands)
        or any(backup is not None or before is not None for backup, before in entries.values())
    ):
        raise PlanError("client generation removal must only withdraw its exact command family")
    if not os.path.lexists(marker):
        temporary = marker.with_name("." + marker.name + ".write-" + uuid.uuid4().hex)
        construction._write_exclusive(temporary, intent)
        publication._publish_receipt(temporary, marker)
    admission._sync_directory(workspace)
    return entries


@contextmanager
def _withdrawal_writer(path, identity, reservation, unchanged, directory_unchanged):
    with (
        admission._owned(path) as (_path, database_root, owned_unchanged),
        admission._connection(path, database_root=database_root, readonly=False) as connection,
    ):
        admission._begin_write(connection, path)
        location = generations._location(path)
        admitted, _origin, reservations, _fences = generations._read(connection, identity, location)
        if reservations.get(reservation["generation"]) != reservation:
            raise PlanError("client withdrawal requires the exact reserved generation")
        unchanged()
        owned_unchanged()
        directory_unchanged()
        yield connection, location, admitted, reservations, _pointer(connection, reservations)


def unregister_client_generation(root, identity, reservation, *, publication, command_directory):
    """Withdraw one exact producer attempt; never remove or restore a successor's commands."""
    identity = admission._identity(identity)
    reservation = generations._reservation(reservation, identity)
    generations._generation(publication)
    removing = False
    try:
        with (
            admission._root(root) as (path, _database_root, unchanged),
            admission._root(command_directory) as (directory, _anchor, directory_unchanged),
            artifact_staging._primitive() as primitive,
        ):
            if directory.is_relative_to(path) or path.is_relative_to(directory):
                raise PlanError("client commands require a separate explicit private directory")
            producer = primitive._runtime_component("versioned_runtime_publication")
            commands = frontdoor._command_paths(directory)
            def writer():
                return _withdrawal_writer(
                    path, identity, reservation, unchanged, directory_unchanged,
                )

            with producer.target_leases(path, commands):
                with writer() as snapshot:
                    admitted, reservations, current = snapshot[2:]
                value = _find_attempt(path, current, publication, reservations)
                if (
                    value["generation"] != reservation["generation"]
                    or value["directory"] != generations._location(directory)
                ):
                    raise PlanError("client withdrawal differs from its exact generation or directory")
                if current != value:
                    return _withdrawal_result(
                        reservation, publication, directory, commands, admitted,
                        superseded=True, mutations=False,
                    )
                workspace, family = _attempt(path, value, reservations)
                _commands, proofs = _proofs(path, value, reservations, producer)
                present = []
                for command in commands:
                    actual = producer.state_path(command, private=True)
                    if actual is not None:
                        if actual not in proofs.get(command, ()):
                            raise PlanError("client generation withdrawal preserves an unattributed command")
                        present.append(command)
                entries = _removal_context(path, workspace, value, family, producer)
                removing = True
                if present:
                    output = workspace / ("remove-" + uuid.uuid4().hex + ".jsonl")
                    producer._recover_locked(
                        path, entries, proofs, present,
                        producer.Receipt(output, path, present, "outer-recovery"),
                    )
                admission._sync_directory(directory)
                admission._sync_directory(workspace)
                unchanged()
                directory_unchanged()
                if any(producer.state_path(command) is not None for command in commands):
                    raise PlanError("client generation withdrawal did not settle absent commands")
                with writer() as (connection, location, admitted, _reservations, current):
                    if current != value:
                        raise PlanError("client command attempt changed during withdrawal")
                    if any(producer.state_path(command) is not None for command in commands):
                        raise PlanError("client commands changed before withdrawal acknowledgement")
                    selected = connection.execute(
                        "SELECT selected_publication FROM lineage WHERE singleton = 1",
                    ).fetchone()[0]
                    if selected is not None:
                        connection.execute(
                            "UPDATE lineage SET selected_generation = NULL, selected_publication = NULL "
                            "WHERE singleton = 1",
                        )
                        admission._commit(connection)
                    _admitted, _origin, _reservations, fences = generations._read(connection, identity, location)
                    if fences["selected_generation"] is not None:
                        raise PlanError("client generation withdrawal acknowledgement outcome is unknown")
                removing = False
                return _withdrawal_result(
                    reservation, publication, directory, commands, admitted,
                    superseded=False, mutations=bool(present) or selected is not None,
                )
    except PlanError:
        if removing:
            raise PlanError("client generation withdrawal outcome is unknown; retained intent requires owned retry") from None
        raise
    except (OSError, sqlite3.Error, RuntimeError, ValueError):
        raise PlanError(
            "client generation withdrawal outcome is unknown; retained intent requires owned retry"
            if removing else "client generation could not establish owned command withdrawal"
        ) from None


def _withdrawal_result(reservation, publication, directory, commands, admitted, *, superseded, mutations):
    return {
        "schema": "fleet-manager.client-generation-unregistration", "version": 1,
        "generation": reservation["generation"], "request": reservation["request"],
        "publication": publication, "command_directory": str(directory),
        "commands": [str(path) for path in commands], "admitted": admitted,
        "superseded": superseded, "commands_absent": not superseded,
        "mutations_performed": mutations, "runtime_selectors_modified": False,
        "runtime_removed": False, "installation_retired": False, "path_modified": False,
        "live_readiness_verified": False,
    }
