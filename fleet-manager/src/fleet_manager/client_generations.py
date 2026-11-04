"""Independent forward-generation construction; shared publication remains separate."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import uuid

from . import artifact_staging, client_admission, client_construction, client_runtime_removal
from .planning import PlanError, canonical, decode, digest, identifier, integer, record
from .release_selection import ReleaseSelection

_SCHEMA_VERSION = 2
_TABLES = {
    **client_admission._TABLES,
    "lineage": "CREATE TABLE lineage (singleton INTEGER PRIMARY KEY CHECK "
               "(singleton = 1), origin TEXT NOT NULL, current_generation TEXT, "
               "selected_generation TEXT, selected_publication TEXT, publication TEXT)",
    "generations": "CREATE TABLE generations (generation TEXT PRIMARY KEY, "
                   "request TEXT NOT NULL UNIQUE, receipt TEXT NOT NULL, root TEXT, retired TEXT)",
}
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_OWNER = "client-generation.json"
_INSPECTION_PAGE_SIZE = 64


def _publication(raw, reservations):
    if raw is None:
        return None
    value = decode(raw.encode("utf-8"))
    record(value, "fleet-manager.client-generation-frontdoor", {
        "generation", "request_digest", "publication", "previous", "directory", "target_digest",
    })
    if set(value) != {
        "schema", "version", "generation", "request_digest", "publication", "previous",
        "directory", "target_digest",
    }:
        raise PlanError("client generation publication has unsupported fields")
    _generation(value["publication"])
    _generation(value["generation"])
    if value["previous"] is not None:
        _generation(value["previous"])
        if value["previous"] == value["publication"]:
            raise PlanError("client generation publication refers to itself")
    _origin_location(value["directory"])
    reservation = reservations.get(value["generation"])
    if (
        reservation is None or value["request_digest"] != digest(reservation)
        or value["target_digest"] != reservation["target_digest"]
        or canonical(value).decode("utf-8") != raw
    ):
        raise PlanError("client generation publication differs from its reserved request")
    return value


def _location(root):
    root = Path(root)
    if not root.is_absolute():
        raise PlanError("client lineage requires an absolute private root")
    return {"path": str(root), "object": list(artifact_staging._directory(root))}


def _physical_object(value):
    if not isinstance(value, list) or len(value) != 2:
        raise PlanError("client lineage physical object is invalid")
    for part in value:
        integer(part, "client lineage physical object", maximum=2**128 - 1)


def _origin_location(value):
    if (
        not isinstance(value, dict) or set(value) != {"path", "object"}
        or not isinstance(value["path"], str) or not Path(value["path"]).is_absolute()
        or ".." in Path(value["path"]).parts
    ):
        raise PlanError("client lineage origin location is invalid")
    _physical_object(value["object"])


def _generation(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise PlanError("client generation must be an explicit 32-character lowercase hex identifier")
    return value


def _reservation(value, identity):
    record(value, "fleet-manager.client-generation-reservation",
           {"generation", "request", "selection", "target_digest", "reserved_at"})
    _generation(value["generation"])
    identifier(value["request"], "forward installation request")
    client_admission._selection(value["selection"], identity)
    integer(value["reserved_at"], "generation reservation time")
    if not isinstance(value["target_digest"], str) or not _DIGEST.fullmatch(value["target_digest"]):
        raise PlanError("client generation target digest is invalid")
    return value


def _root_proof(raw, generation):
    proof = decode(raw.encode("utf-8"))
    if (
        not isinstance(proof, dict) or set(proof) != {"temporary", "object"}
        or not isinstance(proof["temporary"], str)
        or re.fullmatch(r"\.generation-" + generation + r"-[0-9a-f]{32}",
                        proof["temporary"]) is None
        or canonical(proof).decode("utf-8") != raw
    ):
        raise PlanError("client generation root intent is invalid")
    _physical_object(proof["object"])
    return proof


def _read(connection, identity, location):
    admitted = client_admission._read_state(
        connection, identity, expected_tables=_TABLES, schema_version=_SCHEMA_VERSION,
    )
    rows = connection.execute(
        "SELECT singleton, origin, current_generation, selected_generation, "
        "selected_publication, publication FROM lineage"
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != 1:
        raise PlanError("client lineage origin is missing or ambiguous")
    origin = decode(rows[0][1].encode("utf-8"))
    record(origin, "fleet-manager.client-lineage-origin", {"location", "legacy"})
    _origin_location(origin["location"])
    if origin["location"] != location or canonical(origin).decode("utf-8") != rows[0][1]:
        raise PlanError("client lineage physical root differs from its origin")
    legacy = origin["legacy"]
    if legacy is not None:
        if not isinstance(legacy, dict) or set(legacy) != {"location", "database", "admitted"}:
            raise PlanError("client lineage legacy origin is invalid")
        _origin_location(legacy["location"])
        _physical_object(legacy["database"])
        if legacy["admitted"] is not None:
            imported = client_admission._receipt(legacy["admitted"], identity)
            if not admitted or (
                admitted["sequence"] < imported["sequence"]
                or (
                    admitted["sequence"] == imported["sequence"]
                    and admitted["selection_digest"] != imported["selection_digest"]
                )
            ):
                raise PlanError("client lineage lost its imported admitted floor")
    reservations, roots, retired = {}, {}, {}
    for generation, request, raw, root, retirement in connection.execute(
        "SELECT generation, request, receipt, root, retired FROM generations"
    ):
        receipt = _reservation(decode(raw.encode("utf-8")), identity)
        if (
            receipt["generation"] != generation or receipt["request"] != request
            or canonical(receipt).decode("utf-8") != raw or not admitted
            or receipt["selection"]["sequence"] > admitted["sequence"]
            or (
                receipt["selection"]["sequence"] == admitted["sequence"]
                and ReleaseSelection.from_dict(receipt["selection"]).selection_digest
                != admitted["selection_digest"]
            )
        ):
            raise PlanError("client generation reservation differs from the durable lineage")
        if root is not None:
            _root_proof(root, generation)
            roots[generation] = root
        if retirement is not None:
            tombstone = decode(retirement.encode("utf-8"))
            record(tombstone, "fleet-manager.client-generation-retirement",
                   {"generation", "request_digest", "retired_at"})
            integer(tombstone["retired_at"], "generation retirement time")
            if (
                tombstone["generation"] != generation
                or tombstone["request_digest"] != digest(receipt)
                or canonical(tombstone).decode("utf-8") != retirement
            ):
                raise PlanError("client generation retirement differs from its reserved request")
            retired[generation] = tombstone
        reservations[generation] = receipt
    current, selected = rows[0][2:4]
    publication = _publication(rows[0][5], reservations)
    selected_publication = rows[0][4]
    if selected_publication is not None:
        _generation(selected_publication)
    if (
        (current is None) != (not reservations)
        or (current is not None and current not in reservations)
        or (selected is not None and (selected not in roots or selected in retired))
        or (selected is None) != (selected_publication is None)
        or (selected is not None and publication is None)
        or (
            selected is not None and publication is not None
            and (
                (selected_publication == publication["publication"]
                 and selected != publication["generation"])
                or (publication["previous"] is None
                    and selected_publication != publication["publication"])
            )
        )
    ):
        raise PlanError("client lineage generation fences are invalid")
    if current is not None and ReleaseSelection.from_dict(
        reservations[current]["selection"],
    ).selection_digest != admitted["selection_digest"]:
        raise PlanError("client current request differs from the durable admitted selection")
    return admitted, origin, reservations, {
        "current_generation": current, "selected_generation": selected, "retired": retired,
    }


def _legacy_snapshot(root, identity):
    with client_admission._root(root) as (path, database_root, unchanged):
        client_admission._lease_shape(path)
        database = client_admission._ordinary_file(
            path / client_admission.DATABASE, sqlite_state=True,
        )
        with client_admission._connection(
            path, database_root=database_root, readonly=True,
        ) as connection:
            connection.execute("BEGIN")
            admitted = client_admission._read_state(connection, identity)
            result = {
                "location": _location(path),
                "database": list(artifact_staging._identity(database)),
                "admitted": admitted,
            }
            unchanged()
            if artifact_staging._identity(client_admission._ordinary_file(
                path / client_admission.DATABASE, sqlite_state=True,
            )) != artifact_staging._identity(database):
                raise PlanError("client lineage legacy database changed during import")
            return result


def _initialize_lineage(root, identity, *, legacy_root=None):
    """Publish origin and an exact legacy floor together, without predecessor custody."""
    identity = client_admission._identity(identity)
    root = Path(root)
    legacy_root = None if legacy_root is None else Path(legacy_root)
    try:
        with client_admission._root(root):
            location = _location(root)
            if legacy_root is not None and (
                not legacy_root.is_absolute() or root == legacy_root
                or root in legacy_root.parents or legacy_root in root.parents
            ):
                raise PlanError("client lineage and legacy roots must be separate namespaces")
            legacy = None
            if os.path.lexists(root / client_admission.DATABASE):
                with client_admission._root(root) as (path, database_root, unchanged):
                    with client_admission._connection(
                        path, database_root=database_root, readonly=True,
                    ) as connection:
                        connection.execute("BEGIN")
                        _, origin, _, _ = _read(connection, identity, location)
                        legacy = origin["legacy"]
                        if (None if legacy is None else legacy["location"]["path"]) != (
                            None if legacy_root is None else str(legacy_root)
                        ):
                            raise PlanError("client lineage retry names a different legacy origin")
                        unchanged()
            elif legacy_root is not None:
                legacy = _legacy_snapshot(legacy_root, identity)
            origin = {
                "schema": "fleet-manager.client-lineage-origin", "version": 1,
                "location": location, "legacy": legacy,
            }

            def initialize(connection):
                connection.execute("INSERT INTO lineage VALUES (1, ?, NULL, NULL, NULL, NULL)",
                                   (canonical(origin).decode("utf-8"),))
                if legacy is not None and legacy["admitted"] is not None:
                    receipt = legacy["admitted"]
                    connection.execute("INSERT INTO admission VALUES (1, ?, ?)", (
                        receipt["sequence"], canonical(receipt).decode("utf-8"),
                    ))

            def validate(connection, receipt):
                _, actual, _, _ = _read(connection, identity, location)
                if actual != origin:
                    raise PlanError("client lineage initialization origin conflicts")

            return client_admission._initialize(
                root, identity, tables=_TABLES, schema_version=_SCHEMA_VERSION,
                initialize_state=initialize, validate_state=validate,
            )
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise PlanError("client lineage could not establish its durable origin") from None


def inspect_client_generations(root, identity, *, generation=None, after=None):
    """Report durable requests without recovering state, executing runtimes or inferring health."""
    identity = client_admission._identity(identity)
    if generation is not None and after is not None:
        raise PlanError("client inspection requires an exact generation or a page cursor, not both")
    for value in (generation, after):
        if value is not None:
            _generation(value)
    try:
        with client_admission._root(root) as (path, database_root, unchanged):
            with client_admission._connection(
                path, database_root=database_root, readonly=True,
            ) as connection:
                connection.execute("BEGIN")
                admitted, origin, reservations, fences = _read(connection, identity, _location(path))
                selected_publication, raw_publication = connection.execute(
                    "SELECT selected_publication, publication FROM lineage WHERE singleton = 1",
                ).fetchone()
                publication = _publication(raw_publication, reservations)
                if generation is not None:
                    if generation not in reservations:
                        raise PlanError("client inspection generation reservation is absent")
                    names = [generation]
                else:
                    names = [
                        name for name in sorted(reservations)
                        if after is None or name > after
                    ]
                page = names[:_INSPECTION_PAGE_SIZE]
                unchanged()
                return {
                    "schema": "fleet-manager.client-generation-inspection", "version": 1,
                    "identity": identity, "origin": origin, "admitted": admitted,
                    "reservations": {name: reservations[name] for name in page},
                    **fences, "retired": {
                        name: fences["retired"][name] for name in page if name in fences["retired"]
                    },
                    "reservation_count": len(reservations),
                    "next_after": page[-1] if len(names) > len(page) else None,
                    "publication": publication,
                    "selected_publication": selected_publication,
                    "mutations_performed": False,
                }
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise PlanError("client lineage could not be inspected without mutation") from None


def _reserve_generation(root, identity, selection, staged, *, generation, request,
                        current_policy, current_target, clock=lambda: int(time.time())):
    """Reserve exact intent and admission atomically; do not create or execute runtime files."""
    identity, selection, _ = client_construction._request(
        identity, selection, staged, current_policy, current_target,
    )
    _generation(generation)
    identifier(request, "forward installation request")
    try:
        policy, target = client_construction._verified(
            identity, selection, staged, current_policy, current_target, clock,
        )
        with client_admission._root(root) as (path, _, unchanged):
            client_admission._ordinary_file(path / client_admission.DATABASE, sqlite_state=True)
            unchanged()
        with (
            staged._retained_authentication(policy, clock=clock) as (manifest, authenticate),
            client_admission._owned(root) as (path, database_root, unchanged),
        ):
            location = _location(path)
            with client_admission._connection(
                path, database_root=database_root, readonly=False,
            ) as connection:
                connection.execute("BEGIN IMMEDIATE")
                previous, _, reservations, fences = _read(connection, identity, location)
                current = client_construction._compatible(
                    decode(manifest), current_target(), identity,
                )
                if current != target:
                    raise PlanError("client generation target changed during reservation")
                policy = decode(canonical(current_policy()))
                evidence = authenticate(policy, clock=clock)
                verified = client_admission._admission_receipt(
                    selection, evidence, previous,
                )
                reservation = {
                    "schema": "fleet-manager.client-generation-reservation", "version": 1,
                    "generation": generation, "request": request, "selection": selection,
                    "target_digest": digest(target), "reserved_at": verified["admitted_at"],
                }
                existing = reservations.get(generation)
                if existing:
                    comparable = {**reservation, "reserved_at": existing["reserved_at"]}
                    if existing != comparable:
                        raise PlanError("client generation retry differs from its exact reserved request")
                    _current(previous, reservations, existing, fences)
                    unchanged()
                    return existing
                if any(value["request"] == request for value in reservations.values()):
                    raise PlanError("client forward request already reserved a different generation")
                if not previous or selection["sequence"] > previous["sequence"]:
                    connection.execute(
                        "INSERT INTO admission VALUES (1, ?, ?) ON CONFLICT(singleton) DO UPDATE "
                        "SET sequence = excluded.sequence, receipt = excluded.receipt",
                        (selection["sequence"], canonical(verified).decode("utf-8")),
                    )
                connection.execute("INSERT INTO generations VALUES (?, ?, ?, NULL, NULL)", (
                    generation, request, canonical(reservation).decode("utf-8"),
                ))
                connection.execute(
                    "UPDATE lineage SET current_generation = ? WHERE singleton = 1", (generation,),
                )
                unchanged()
                client_admission._commit(connection)
                _, _, committed, fences = _read(connection, identity, location)
                if (
                    committed.get(generation) != reservation
                    or fences["current_generation"] != generation
                ):
                    raise PlanError("client generation reservation outcome is unknown")
                unchanged()
                return reservation
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise PlanError("client generation could not establish its durable reservation") from None


def _binding(identity, reservation, location):
    return {
        "schema": "fleet-manager.client-construction-input", "version": 1,
        "identity": identity, "selection": reservation["selection"],
        "selection_digest": ReleaseSelection.from_dict(reservation["selection"]).selection_digest,
        "generation": reservation["generation"], "request_digest": digest(reservation),
        "lineage": location, "target_digest": reservation["target_digest"],
    }


def _current(admitted, reservations, reservation, fences, *, shared=False):
    if (
        reservations.get(reservation["generation"]) != reservation or not admitted
        or admitted["selection_digest"] != ReleaseSelection.from_dict(
            reservation["selection"],
        ).selection_digest
    ):
        raise PlanError("client generation no longer has the exact durably admitted request")
    if reservation["generation"] in fences["retired"]:
        raise PlanError("client generation has been retired")
    if shared and fences["current_generation"] != reservation["generation"]:
        raise PlanError("client generation is not the current shared publication request")


def _retire_generation(root, identity, reservation, *, clock=lambda: int(time.time())):
    """Fence future lifecycle work only; do not withdraw commands or delete runtime files."""
    identity = client_admission._identity(identity)
    reservation = _reservation(reservation, identity)
    generation = reservation["generation"]
    try:
        with client_admission._owned(root) as (path, database_root, unchanged):
            location = _location(path)
            with client_admission._connection(
                path, database_root=database_root, readonly=False,
            ) as connection:
                connection.execute("BEGIN IMMEDIATE")
                _, _, reservations, fences = _read(connection, identity, location)
                if reservations.get(generation) != reservation:
                    raise PlanError("client retirement requires the exact reserved generation")
                if generation in fences["retired"]:
                    unchanged()
                    return fences["retired"][generation]
                if fences["selected_generation"] == generation:
                    raise PlanError("client selected generation requires owned shared withdrawal first")
                retired_at = clock()
                integer(retired_at, "generation retirement time")
                tombstone = {
                    "schema": "fleet-manager.client-generation-retirement", "version": 1,
                    "generation": generation, "request_digest": digest(reservation),
                    "retired_at": retired_at,
                }
                connection.execute("UPDATE generations SET retired = ? WHERE generation = ?", (
                    canonical(tombstone).decode("utf-8"), generation,
                ))
                unchanged()
                client_admission._commit(connection)
                _, _, _, committed = _read(connection, identity, location)
                if committed["retired"].get(generation) != tombstone:
                    raise PlanError("client generation retirement outcome is unknown")
                unchanged()
                return tombstone
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise PlanError("client generation could not establish its durable retirement fence") from None


def _owned_generation(path, proof, binding, *, unpublished=False):
    if list(artifact_staging._directory(path)) != proof["object"]:
        raise PlanError("client generation root is not its original physical object")
    if client_construction._read(path / _OWNER) != binding:
        raise PlanError("client generation root has a different request owner")
    if unpublished and {child.name for child in path.iterdir()} != {_OWNER}:
        raise PlanError("unpublished client generation root contains foreign state")


def _cleanup_snapshot(root, identity, reservation, location, retirement, *, expected_proof=None):
    from . import client_generation_frontdoor as frontdoor

    with client_admission._root(root) as (path, database_root, unchanged):
        with client_admission._connection(
            path, database_root=database_root, readonly=True,
        ) as connection:
            connection.execute("BEGIN")
            _, _, reservations, fences = _read(connection, identity, location)
            generation = reservation["generation"]
            if (
                reservations.get(generation) != reservation
                or fences["retired"].get(generation) != retirement
                or fences["selected_generation"] == generation
            ):
                raise PlanError("client generation cleanup lost its exact retirement fence")
            raw = connection.execute(
                "SELECT root FROM generations WHERE generation = ?", (generation,),
            ).fetchone()[0]
            if raw is None:
                raise PlanError("client generation cleanup requires its registered physical root")
            proof = _root_proof(raw, generation)
            if expected_proof is not None and proof != expected_proof:
                raise PlanError("client generation cleanup physical root intent changed")
            pointer = frontdoor._pointer(connection, reservations)
            unchanged()
            return proof, reservations, pointer


def _cleanup_commands(root, generation, snapshot):
    from . import client_generation_frontdoor as frontdoor

    _, _, pointer = snapshot()
    if pointer is None:
        return
    with (
        client_admission._root(Path(pointer["directory"]["path"])) as (
            directory, _anchor, directory_unchanged,
        ),
        artifact_staging._primitive() as primitive,
    ):
        if _location(directory) != pointer["directory"]:
            raise PlanError("client generation cleanup command directory changed")
        publication = primitive._runtime_component("versioned_runtime_publication")
        commands = frontdoor.frontdoor._command_paths(directory)
        with publication.target_leases(root, commands):
            _, reservations, current = snapshot()
            if current != pointer:
                raise PlanError("client generation cleanup command attempt changed")
            actual = {path: publication.state_path(path, private=True) for path in commands}
            if any(value is not None for value in actual.values()):
                if pointer["generation"] == generation:
                    raise PlanError("client generation cleanup requires owned shared withdrawal first")
                expected, proofs = frontdoor._proofs(root, pointer, reservations, publication)
                for path, value in actual.items():
                    if value is not None and (
                        value not in proofs.get(path, ())
                        or value.digest != hashlib.sha256(expected[path]).hexdigest()
                    ):
                        raise PlanError("client generation cleanup preserves unwithdrawn or foreign commands")
            directory_unchanged()


@contextmanager
def _cleanup_custody(root, identity, reservation, location, retirement):
    proof, _, _ = _cleanup_snapshot(root, identity, reservation, location, retirement)
    binding = _binding(identity, reservation, location)
    generation = reservation["generation"]
    with client_admission._owned(root / ("generation-" + generation)) as (path, _, unchanged):
        def snapshot():
            unchanged()
            _owned_generation(path, proof, binding)
            return _cleanup_snapshot(
                root, identity, reservation, location, retirement, expected_proof=proof,
            )

        def checkpoint():
            _cleanup_commands(root, generation, snapshot)
            snapshot()

        checkpoint()
        yield path, checkpoint


def remove_client_generation(root, identity, reservation, *, clock=lambda: int(time.time())):
    """Retire one exact generation and remove its unreferenced runtime, not the lineage."""
    identity = client_admission._identity(identity)
    reservation = _reservation(reservation, identity)
    retirement = _retire_generation(root, identity, reservation, clock=clock)
    with client_admission._root(root) as (path, _anchor, unchanged):
        location = _location(path)
        binding = _binding(identity, reservation, location)
        selection = reservation["selection"]
        candidate = f"client-{selection['sequence']}-{selection['release_digest']}"

        def validate_binding(namespace, actual_identity, actual_candidate, value):
            if actual_identity != identity or actual_candidate != candidate or value != binding:
                raise PlanError("client generation cleanup has a foreign construction binding")
            client_construction._completed_intent(namespace, candidate, binding)

        result = client_runtime_removal._remove_client_runtime(
            identity, candidate=candidate,
            custody=lambda: _cleanup_custody(path, identity, reservation, location, retirement),
            validate_binding=validate_binding,
        )
        unchanged()
        return {
            **result, "schema": "fleet-manager.client-generation-removal",
            "generation": reservation["generation"], "generation_retired": True,
        }


def _generation_root(root, identity, reservation):
    """Persist physical intent before a canonical no-replace directory publication."""
    with client_admission._owned(root) as (path, database_root, unchanged):
        location = _location(path)
        binding = _binding(identity, reservation, location)
        generation = reservation["generation"]
        destination = path / ("generation-" + generation)
        with client_admission._connection(
            path, database_root=database_root, readonly=False,
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            admitted, _, reservations, fences = _read(connection, identity, location)
            _current(admitted, reservations, reservation, fences)
            raw = connection.execute(
                "SELECT root FROM generations WHERE generation = ?", (generation,),
            ).fetchone()[0]
            if raw is None:
                if os.path.lexists(destination):
                    raise PlanError("client generation destination has no owned physical intent")
                temporary = path / (".generation-" + generation + "-" + uuid.uuid4().hex)
                temporary.mkdir(mode=0o700)
                client_construction._write_exclusive(temporary / _OWNER, binding)
                client_admission._sync_directory(temporary)
                client_admission._sync_directory(path)
                proof = {"temporary": temporary.name,
                         "object": list(artifact_staging._directory(temporary))}
                connection.execute("UPDATE generations SET root = ? WHERE generation = ?", (
                    canonical(proof).decode("utf-8"), generation,
                ))
                unchanged()
                client_admission._commit(connection)
                stored = connection.execute(
                    "SELECT root FROM generations WHERE generation = ?", (generation,),
                ).fetchone()[0]
                if _root_proof(stored, generation) != proof:
                    raise PlanError("client generation root intent outcome is unknown")
            else:
                proof = _root_proof(raw, generation)
            temporary = path / proof["temporary"]
            if os.path.lexists(destination):
                _owned_generation(destination, proof, binding)
                if os.path.lexists(temporary):
                    raise PlanError("published client generation retains an ambiguous staging object")
            else:
                _owned_generation(temporary, proof, binding, unpublished=True)
                unchanged()
                with artifact_staging._primitive() as primitive:
                    publication = primitive._runtime_component("versioned_runtime_publication")
                    publication._publish_receipt(temporary, destination)
                client_admission._sync_directory(path)
                _owned_generation(destination, proof, binding)
            unchanged()
            return destination, proof, binding


@contextmanager
def _generation_custody(root, generation_root, proof, binding, identity, reservation, staged,
                        current_policy, current_target, clock):
    with client_admission._owned(generation_root) as (path, _, unchanged):
        def checkpoint():
            unchanged()
            _owned_generation(path, proof, binding)
            with client_admission._root(root) as (lineage, database_root, lineage_unchanged):
                with client_admission._connection(
                    lineage, database_root=database_root, readonly=True,
                ) as connection:
                    connection.execute("BEGIN")
                    admitted, _, reservations, fences = _read(connection, identity, binding["lineage"])
                    _current(admitted, reservations, reservation, fences)
                    stored = connection.execute(
                        "SELECT root FROM generations WHERE generation = ?",
                        (reservation["generation"],),
                    ).fetchone()[0]
                    if _root_proof(stored, reservation["generation"]) != proof:
                        raise PlanError("client generation physical intent changed")
                    lineage_unchanged()
            policy, target = client_construction._verified(
                identity, reservation["selection"], staged, current_policy, current_target, clock,
            )
            if digest(target) != reservation["target_digest"]:
                raise PlanError("client generation target differs from its reserved request")
            unchanged()
            return policy, target, admitted

        checkpoint()
        yield path, unchanged, checkpoint


def construct_client_generation(root, identity, selection, staged, *, generation, request,
                                current_policy, current_target, clock=lambda: int(time.time())):
    """Start the supplied generation independently; do not select it or register shared commands."""
    identity = client_admission._identity(identity)
    reservation = _reserve_generation(
        root, identity, selection, staged, generation=generation, request=request,
        current_policy=current_policy, current_target=current_target, clock=clock,
    )
    try:
        generation_root, proof, binding = _generation_root(root, identity, reservation)
        constructed = client_construction._construct_client_release(
            identity, reservation["selection"], staged, binding=binding, clock=clock,
            custody=lambda: _generation_custody(
                root, generation_root, proof, binding, identity, reservation, staged,
                current_policy, current_target, clock,
            ),
        )
        namespace = generation_root / client_construction.NAMESPACE
        with artifact_staging._primitive() as primitive:
            slot = primitive.version_dir(namespace, constructed["candidate"])
        command = slot / ("Scripts/fleet-manager.exe" if os.name == "nt" else "bin/fleet-manager")
        return {
            **constructed, "schema": "fleet-manager.client-generation-construction",
            "generation": generation, "request": request, "generation_root": str(generation_root),
            "reservation": reservation,
            "direct_command": str(command), "installed": True, "started": True,
            "selected": False, "registered": False, "predecessor_cleanup": "not-attempted",
        }
    except PlanError:
        raise
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise PlanError("client generation could not establish an independently healthy runtime") from None
