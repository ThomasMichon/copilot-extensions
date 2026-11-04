"""Real copy producers, SQLite acknowledgement and installed default commands."""

import os
import copy
import sqlite3

import pytest

from fleet_manager import (
    client_admission as admission,
    client_generation_frontdoor as frontdoor, client_generations as generations,
)
from fleet_manager.planning import PlanError, decode, digest
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_admission import database_bytes, identity, independently_sign, retained, selection
from test_client_construction import signed_wheel as signed_wheel, target  # noqa: F401
from test_client_construction_native import failed_doctor_fixture
from test_client_generation_construction import construct_generation, direct_health
from test_client_generations import FIRST, SECOND, lineage_at, reserve, state


@pytest.fixture
def installation(tmp_path_factory, signed_wheel):
    area = tmp_path_factory.mktemp("d")
    root = lineage_at(area)
    directory = area / "commands"
    directory.mkdir(mode=0o700)
    generations._initialize_lineage(root, identity())
    with retained(signed_wheel, area) as (staged, trusted):
        first = construct_generation(root, signed_wheel, staged, trusted)
        yield root, directory, signed_wheel, staged, trusted, first


def publish(installation, generation=FIRST, **kwargs):
    root, directory, fixture, staged, trusted, _first = installation
    return frontdoor.publish_client_generation(
        root, identity(), state(root)[2][generation], staged, command_directory=directory,
        current_policy=lambda: trusted, current_target=lambda: target(fixture),
        clock=lambda: 150, **kwargs,
    )


def pointer(root):
    with sqlite3.connect(root / admission.DATABASE) as connection:
        return connection.execute(
            "SELECT selected_generation, selected_publication, publication FROM lineage",
        ).fetchone()


@pytest.mark.timeout(90)
def test_real_default_commands_acknowledge_and_exact_retry_preserves_producer_objects(
    primitive, installation,
):
    root, directory, _fixture, _staged, _trusted, first = installation
    published = publish(installation)
    assert published["installed"] and published["started"]
    assert published["selected"] and published["registered"] and not published["reused"]
    assert published["health"] == first["health"]
    assert not published["path_modified"]
    publication = primitive._runtime_component("versioned_runtime_publication")
    objects = {path: publication.state_path(path) for path in frontdoor.frontdoor._command_paths(directory)}
    before = database_bytes(root)
    selected = pointer(root)
    assert selected[0] == FIRST and selected[1] is not None
    retried = publish(installation)
    assert retried["selected"] and retried["registered"] and retried["reused"]
    assert not retried["mutations_performed"]
    assert database_bytes(root) == before and pointer(root) == selected
    assert {path: publication.state_path(path) for path in objects} == objects
    direct_health(first, directory.parent)


@pytest.mark.timeout(90)
def test_same_release_forward_cutover_retains_both_direct_commands(primitive, installation):
    root, directory, fixture, staged, trusted, first = installation
    publish(installation)
    old_selected = pointer(root)
    user = root / "configuration"
    user.write_bytes(b"preserved configuration\n")
    second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    assert pointer(root) == old_selected
    switched = publish(installation, SECOND)
    assert switched["selected"] and switched["registered"] and not switched["reused"]
    assert pointer(root)[0] == SECOND and pointer(root)[1] != old_selected[1]
    direct_health(first, directory.parent)
    direct_health(second, directory.parent)
    assert user.read_bytes() == b"preserved configuration\n"
    assert state(root)[0]["selection"] == state(root)[2][FIRST]["selection"]


@pytest.mark.timeout(90)
@pytest.mark.parametrize("damage", ["missing", "malformed"])
def test_explicit_incoming_targets_replace_without_predecessor_copy_receipts(
    primitive, installation, damage,
):
    root, directory, fixture, staged, trusted, first = installation
    old = publish(installation)
    receipt = root / frontdoor.HISTORY / old["publication"] / "copy-forward.jsonl"
    if damage == "missing":
        receipt.unlink()
    else:
        receipt.write_bytes(b"unfinished predecessor receipt")
    preserved = None if damage == "missing" else receipt.read_bytes()
    second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    reservation = state(root)[2][SECOND]
    targets = frontdoor.inspect_command_targets(
        root, identity(), reservation, command_directory=directory,
    )
    before = database_bytes(root)
    with pytest.raises(PlanError):
        publish(installation, SECOND)
    assert database_bytes(root) == before
    current = publish(installation, SECOND, replacement_targets=targets)
    assert current["selected"] and current["registered"] and pointer(root)[0] == SECOND
    assert (not receipt.exists()) if preserved is None else receipt.read_bytes() == preserved
    publication = primitive._runtime_component("versioned_runtime_publication")
    objects = {
        path: publication.state_path(path)
        for path in frontdoor.frontdoor._command_paths(directory)
    }
    before = database_bytes(root)
    retry = publish(installation, SECOND, replacement_targets=targets)
    assert retry["reused"] and not retry["mutations_performed"]
    assert database_bytes(root) == before
    assert {path: publication.state_path(path) for path in objects} == objects
    direct_health(first, directory.parent)
    direct_health(second, directory.parent)


@pytest.mark.timeout(90)
@pytest.mark.parametrize("existing", [False, True])
def test_explicit_targets_refuse_a_changed_object_before_incoming_publication(
    primitive, installation, existing,
):
    root, directory, _fixture, _staged, _trusted, _first = installation
    foreign = frontdoor.frontdoor._command_paths(directory)[0]
    content = b"unrelated incoming command"
    if existing:
        foreign.write_bytes(content)
    targets = frontdoor.inspect_command_targets(
        root, identity(), state(root)[2][FIRST], command_directory=directory,
    )
    replacement = directory / "replacement"
    replacement.write_bytes(content)
    os.replace(replacement, foreign)
    before = database_bytes(root)
    with pytest.raises(PlanError, match="targets changed"):
        publish(installation, replacement_targets=targets)
    assert foreign.read_bytes() == content
    assert database_bytes(root) == before
    assert pointer(root)[0] is None


@pytest.mark.timeout(90)
@pytest.mark.parametrize("field", ["identity", "reservation_digest"])
def test_explicit_targets_refuse_a_snapshot_from_a_different_request(
    primitive, installation, field,
):
    root, directory, _fixture, _staged, _trusted, _first = installation
    targets = frontdoor.inspect_command_targets(
        root, identity(), state(root)[2][FIRST], command_directory=directory,
    )
    if field == "identity":
        targets["identity"]["installation"] = "different-installation"
    else:
        targets[field] = "0" * 64
    before = database_bytes(root)
    with pytest.raises(PlanError, match="differ from this request"):
        publish(installation, replacement_targets=targets)
    assert database_bytes(root) == before
    assert pointer(root)[0] is None
    assert not any(os.path.lexists(path) for path in frontdoor.frontdoor._command_paths(directory))


@pytest.mark.timeout(90)
def test_explicit_retry_preserves_a_same_byte_foreign_replacement(primitive, installation):
    root, directory, _fixture, _staged, _trusted, _first = installation
    targets = frontdoor.inspect_command_targets(
        root, identity(), state(root)[2][FIRST], command_directory=directory,
    )
    publish(installation, replacement_targets=targets)
    command = frontdoor.frontdoor._command_paths(directory)[0]
    foreign = directory / "foreign"
    content = command.read_bytes()
    foreign.write_bytes(content)
    foreign.chmod(command.stat().st_mode)
    os.replace(foreign, command)
    object_identity = command.stat().st_ino
    before = database_bytes(root)
    with pytest.raises(PlanError, match="outside its recorded progress"):
        publish(installation, replacement_targets=targets)
    assert command.read_bytes() == content and command.stat().st_ino == object_identity
    assert database_bytes(root) == before and pointer(root)[0] == FIRST


@pytest.mark.timeout(90)
def test_interrupted_default_health_retains_real_pending_proofs_and_retry_acknowledges(
    primitive, installation, monkeypatch,
):
    root, directory, fixture, staged, trusted, first = installation
    publish(installation)
    old_selected = pointer(root)[:2]
    second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    actual = frontdoor.frontdoor._health

    def interrupted(*args, **kwargs):
        actual(*args, **kwargs)
        raise PlanError("injected after real default-command health")

    with monkeypatch.context() as injected:
        injected.setattr(frontdoor.frontdoor, "_health", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            publish(installation, SECOND)
    pending = pointer(root)
    assert pending[:2] == old_selected and pending[2] is not None
    command_objects = {
        path: path.stat().st_ino for path in frontdoor.frontdoor._command_paths(directory)
    }
    retried = publish(installation, SECOND)
    assert retried["reused"] and retried["mutations_performed"] and retried["selected"]
    assert pointer(root)[0] == SECOND and pointer(root)[2] == pending[2]
    assert {path: path.stat().st_ino for path in command_objects} == command_objects
    direct_health(first, directory.parent)
    direct_health(second, directory.parent)


@pytest.mark.timeout(90)
@pytest.mark.parametrize("after", [False, True])
@pytest.mark.parametrize("incoming", [False, True], ids=["implicit", "explicit"])
def test_acknowledgement_crash_never_invents_selection_or_republishes_same_attempt(
    primitive, installation, monkeypatch, after, incoming,
):
    root, directory, _fixture, _staged, _trusted, first = installation
    commit = admission._commit
    options = {
        "replacement_targets": frontdoor.inspect_command_targets(
            root, identity(), state(root)[2][FIRST], command_directory=directory,
        ),
    } if incoming else {}

    def interrupted(connection):
        row = connection.execute(
            "SELECT selected_publication FROM lineage",
        ).fetchone()
        if row[0] is not None:
            if after:
                commit(connection)
            raise OSError("injected selection durability boundary")
        commit(connection)

    with monkeypatch.context() as injected:
        injected.setattr(admission, "_commit", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            publish(installation, **options)
    pending = pointer(root)
    assert (pending[0] == FIRST) is after
    objects = {path: path.stat().st_ino for path in frontdoor.frontdoor._command_paths(directory)}
    result = publish(installation, **options)
    assert result["selected"] and result["reused"] and result["mutations_performed"] is not after
    assert pointer(root)[0] == FIRST and pointer(root)[2] == pending[2]
    assert {path: path.stat().st_ino for path in objects} == objects
    direct_health(first, directory.parent)


@pytest.mark.timeout(90)
def test_new_request_during_real_default_health_fences_ack_without_rollback(
    primitive, installation, monkeypatch,
):
    root, directory, fixture, staged, trusted, first = installation
    actual = frontdoor.frontdoor._health

    def superseded(*args, **kwargs):
        actual(*args, **kwargs)
        reserve(root, fixture, staged, trusted, generation=SECOND, request="forward-2")

    with monkeypatch.context() as injected:
        injected.setattr(frontdoor.frontdoor, "_health", superseded)
        with pytest.raises(PlanError, match="outcome is unknown"):
            publish(installation)
    assert pointer(root)[0] is None
    pending = pointer(root)[2]
    second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    with pytest.raises(PlanError, match="current shared publication request"):
        publish(installation)
    assert pointer(root)[2] == pending
    assert publish(installation, SECOND)["selected"]
    direct_health(first, directory.parent)
    direct_health(second, directory.parent)


@pytest.mark.timeout(90)
def test_same_byte_foreign_default_is_preserved_while_new_generation_starts(
    primitive, installation,
):
    root, directory, fixture, staged, trusted, first = installation
    publish(installation)
    publication = primitive._runtime_component("versioned_runtime_publication")
    command = frontdoor.frontdoor._command_paths(directory)[0]
    before = publication.state_path(command)
    replacement = directory / "foreign"
    replacement.write_bytes(command.read_bytes())
    replacement.chmod(command.stat().st_mode)
    os.replace(replacement, command)
    foreign = publication.state_path(command)
    assert foreign != before and foreign.digest == before.digest
    second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    selected = pointer(root)
    with pytest.raises(PlanError, match="foreign or same-byte"):
        publish(installation, SECOND)
    assert pointer(root) == selected and publication.state_path(command) == foreign
    direct_health(first, directory.parent)
    direct_health(second, directory.parent)


@pytest.mark.timeout(90)
def test_default_directory_is_lineage_bound_not_a_new_path_on_retry(primitive, installation):
    root, directory, fixture, staged, trusted, _first = installation
    publish(installation)
    construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    other = directory.parent / "different-default"
    other.mkdir(mode=0o700)
    selected = pointer(root)
    with pytest.raises(PlanError, match="different default command directory"):
        frontdoor.publish_client_generation(
            root, identity(), state(root)[2][SECOND], staged, command_directory=other,
            current_policy=lambda: trusted, current_target=lambda: target(fixture),
            clock=lambda: 150,
        )
    assert pointer(root) == selected
    assert {path.name for path in other.iterdir()} <= {".publication-leases"}
    assert not any(path.exists() for path in frontdoor.frontdoor._command_paths(other))


@pytest.mark.timeout(90)
@pytest.mark.parametrize("after", [False, True])
@pytest.mark.parametrize("incoming", [False, True], ids=["implicit", "explicit"])
def test_intent_durability_crash_precedes_every_shared_command_effect(
    primitive, installation, monkeypatch, after, incoming,
):
    root, directory, _fixture, _staged, _trusted, first = installation
    commit = admission._commit
    options = {
        "replacement_targets": frontdoor.inspect_command_targets(
            root, identity(), state(root)[2][FIRST], command_directory=directory,
        ),
    } if incoming else {}

    def interrupted(connection):
        row = connection.execute("SELECT publication FROM lineage").fetchone()
        if row[0] is not None:
            if after:
                commit(connection)
            raise OSError("injected intent durability boundary")
        commit(connection)

    with monkeypatch.context() as injected:
        injected.setattr(admission, "_commit", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            publish(installation, **options)
    interrupted_pointer = pointer(root)
    assert interrupted_pointer[:2] == (None, None)
    assert (interrupted_pointer[2] is not None) is after
    assert not any(path.exists() for path in frontdoor.frontdoor._command_paths(directory))
    if incoming and after:
        attempt = decode(interrupted_pointer[2].encode())["publication"]
        (root / frontdoor.HISTORY / attempt / "copy-forward.jsonl").write_bytes(b"lost own proof")
    assert publish(installation, **options)["selected"]
    assert set(state(root)[2]) == {FIRST}
    direct_health(first, directory.parent)


@pytest.mark.timeout(90)
@pytest.mark.parametrize("incoming", [False, True], ids=["implicit", "explicit"])
def test_copy_failure_recovery_objects_are_proved_and_retryable(
    primitive, installation, monkeypatch, incoming,
):
    root, directory, fixture, staged, trusted, first = installation
    generation, options, second = FIRST, {}, None
    baseline = {}
    if incoming:
        publish(installation)
        second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
        generation = SECOND
        options["replacement_targets"] = frontdoor.inspect_command_targets(
            root, identity(), state(root)[2][SECOND], command_directory=directory,
        )
        baseline = {path: path.read_bytes() for path in frontdoor.frontdoor._command_paths(directory)}
    publication = primitive._runtime_component("versioned_runtime_publication")
    original = publication.CopyPublication.verify

    def interrupted(copy):
        if not copy.published:
            original(copy)
            raise OSError("injected after physical copies before producer success")
        original(copy)

    with monkeypatch.context() as injected:
        injected.setattr(publication.CopyPublication, "verify", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            publish(installation, generation, **options)
    assert pointer(root)[0] == (FIRST if incoming else None)
    assert {
        path: path.read_bytes() for path in frontdoor.frontdoor._command_paths(directory) if path.exists()
    } == baseline
    assert list((root / frontdoor.HISTORY).glob("*/copy-recovery.*.jsonl"))
    assert publish(installation, generation, **options)["selected"]
    direct_health(first, directory.parent)
    if second is not None:
        direct_health(second, directory.parent)


def higher_fixture(fixture, area, *, failed=False):
    replacement = failed_doctor_fixture(fixture, area) if failed else copy.deepcopy(fixture)
    replacement["manifest"]["id"] += "-forward"
    payload = {
        **replacement["proof"]["payload"], "sequence": selection(fixture)["sequence"] + 1,
        "release_digest": digest(replacement["manifest"]),
    }
    signed = independently_sign([payload], area)
    replacement["proof"] = {**replacement["proof"], **signed["proofs"][0]}
    replacement["policy"]["keys"][0]["public_key"] = signed["public_key"]
    return replacement


@pytest.fixture(scope="module", params=[False, True], ids=["higher", "failed-start"])
def forward_ready(tmp_path_factory, signed_wheel, request):
    area = tmp_path_factory.mktemp("h")
    root = lineage_at(area)
    directory = area / "commands"
    directory.mkdir(mode=0o700)
    generations._initialize_lineage(root, identity())
    with retained(signed_wheel, area) as (staged, trusted):
        first = construct_generation(root, signed_wheel, staged, trusted)
        prepared = root, directory, signed_wheel, staged, trusted, first
        publish(prepared)
        yield prepared, request.param


@pytest.mark.timeout(90)
def test_higher_release_preparation_has_real_selected_default_commands(forward_ready):
    prepared, _failed = forward_ready
    assert pointer(prepared[0])[0] == FIRST
    assert all(path.exists() for path in frontdoor.frontdoor._command_paths(prepared[1]))


@pytest.mark.timeout(90)
def test_higher_release_preserves_old_selected_commands_until_new_start_succeeds(
    primitive, forward_ready,
):
    installation, failed = forward_ready
    root, directory, fixture, _staged, _trusted, first = installation
    before = pointer(root)
    publication = primitive._runtime_component("versioned_runtime_publication")
    objects = {path: publication.state_path(path) for path in frontdoor.frontdoor._command_paths(directory)}
    area = directory.parent / "higher"
    area.mkdir(mode=0o700)
    replacement = higher_fixture(fixture, area, failed=failed)
    with retained(replacement, area) as (stage, policy):
        if failed:
            with pytest.raises(PlanError, match="local diagnostics failed with exit status 23"):
                construct_generation(root, replacement, stage, policy, SECOND, "forward-2")
            assert pointer(root) == before
            assert {path: publication.state_path(path) for path in objects} == objects
        else:
            second = construct_generation(root, replacement, stage, policy, SECOND, "forward-2")
            assert pointer(root) == before
            current = root, directory, replacement, stage, policy, second
            assert publish(current, SECOND)["selected"]
            assert pointer(root)[0] == SECOND and pointer(root)[1] != before[1]
            direct_health(second, area)
        assert state(root)[0]["sequence"] == selection(replacement)["sequence"]
    direct_health(first, area)


@pytest.fixture(scope="module")
def corrupt_ready(tmp_path_factory, signed_wheel):
    area = tmp_path_factory.mktemp("c")
    root = lineage_at(area)
    directory = area / "commands"
    directory.mkdir(mode=0o700)
    generations._initialize_lineage(root, identity())
    with retained(signed_wheel, area) as (staged, trusted):
        first = construct_generation(root, signed_wheel, staged, trusted)
        prepared = root, directory, signed_wheel, staged, trusted, first
        publish(prepared)
        yield prepared


@pytest.mark.timeout(90)
def test_corrupt_predecessor_preparation_has_real_selected_commands(corrupt_ready):
    assert pointer(corrupt_ready[0])[0] == FIRST
    assert all(path.exists() for path in frontdoor.frontdoor._command_paths(corrupt_ready[1]))


@pytest.mark.timeout(90)
def test_corrupt_predecessor_publication_proof_does_not_block_independent_start(
    primitive, corrupt_ready,
):
    installation = corrupt_ready
    root, directory, fixture, staged, trusted, first = installation
    selected = pointer(root)
    value = decode(selected[2].encode("utf-8"))
    proof = root / frontdoor.HISTORY / value["publication"] / "copy-forward.jsonl"
    proof.write_bytes(b"corrupt predecessor proof\n")
    second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    with pytest.raises(PlanError, match="could not establish owned default commands"):
        publish(installation, SECOND)
    assert pointer(root) == selected
    direct_health(first, directory.parent)
    direct_health(second, directory.parent)
