"""Exact retained-good decisions with real trust, history and publication proofs."""

import copy
from contextlib import contextmanager
import os

import pytest

from fleet_manager import client_activation as activation, client_admission as admission
from fleet_manager import client_construction as construction, client_frontdoor as frontdoor
from fleet_manager import client_recovery as recovery
from fleet_manager.client_command_removal import unregister_client_frontdoor
from fleet_manager.client_deactivation import deactivate_client_runtime
from fleet_manager.client_runtime_removal import remove_client_runtime
from fleet_manager.planning import PlanError, canonical, digest
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate, completed_candidate
from test_client_admission import database_bytes, identity, independently_sign, retained, root_at, selection
from test_client_construction import admit, signed_wheel as signed_wheel, target  # noqa: F401
from test_client_frontdoor import command_directory, register, states


def higher_release(fixture, area, sequence=11):
    value = copy.deepcopy(fixture)
    value["manifest"]["id"] = "newer-client-" + str(sequence)
    payload = {
        **value["proof"]["payload"], "sequence": sequence,
        "release_digest": digest(value["manifest"]),
    }
    signed = independently_sign([payload], area)
    value["proof"] = {**value["proof"], **signed["proofs"][0]}
    value["policy"]["keys"][0]["public_key"] = signed["public_key"]
    return value


def decision_for(root, retained_active, completion, directory, *, withdrawn=False):
    namespace = root / construction.NAMESPACE
    admitted = admission.inspect_client_admission(root, identity())["admitted"]
    return {
        "schema": "fleet-manager.client-recovery-decision", "version": 1,
        "identity": identity(), "owner": identity()["selection_owner"],
        "decision": "explicit-retained-good", "revision": 1,
        "admitted_digest": digest(admitted),
        "expected_active": None if withdrawn else construction._read(namespace / activation.ACTIVE),
        "expected_withdrawal": digest(construction._read(namespace / recovery.WITHDRAWN)) if withdrawn else None,
        "retained_active": retained_active, "completion_digest": digest(completion),
        "target_digest": digest(target({"manifest": {
            "roles": {"client": completion["input"]["selection"]["components"]},
        }})),
        "command_directory": str(directory),
    }


@pytest.fixture
def prepared(tmp_path, primitive, signed_wheel, monkeypatch):
    root = root_at(tmp_path)
    directory = command_directory(tmp_path)
    old = signed_wheel
    with retained(old, tmp_path) as (staged, trusted):
        admit(root, old, staged, trusted)
        namespace, slot = completed_candidate(
            root, old, staged, trusted, primitive,
            prepare=lambda slot: construction._bootstrap(slot, construction.artifact_staging.diagnose_client()),
        )
        monkeypatch.setattr(construction, "_health", lambda *_args: {"unit_health_stub": True})
        monkeypatch.setattr(frontdoor, "_health", lambda *_args: None)
        activate(root, old, staged, trusted)
        register(root, old, staged, trusted, directory)
        prior = construction._read(namespace / activation.ACTIVE)
        completion = construction._read(slot / construction.COMPLETION)
        newer = higher_release(old, tmp_path)
        with retained(newer, tmp_path) as (new_stage, new_policy):
            admission.admit_client_release(
                root, identity(), selection(newer), new_stage, new_policy, clock=lambda: 150,
            )
            completed_candidate(root, newer, new_stage, new_policy, primitive)
            activate(root, newer, new_stage, new_policy)
        user = root / "user-config"
        user.write_bytes(b"retained config and schema-1 data")
        decision = decision_for(root, prior, completion, directory)
        yield root, old, staged, trusted, directory, namespace, slot, decision, newer, user


def recover(prepared, **options):
    root, fixture, staged, trusted, directory, _namespace, _slot, decision, *_rest = prepared
    return recovery.recover_client_release(
        root, identity(), selection(fixture), staged, command_directory=directory,
        current_recovery=options.pop("current_recovery", lambda: decision),
        current_policy=options.pop("current_policy", lambda: trusted),
        current_target=options.pop("current_target", lambda: target(fixture)),
        clock=options.pop("clock", lambda: 150), **options,
    )


def test_recovery_binds_selectors_commands_and_unchanged_admission(prepared, primitive, monkeypatch):
    root, old, staged, trusted, directory, namespace, slot, decision, _newer, user = prepared
    floor = database_bytes(root)
    owned = admission._owned
    acquisitions = []
    installed_health = construction._health
    health_calls = []

    @contextmanager
    def observed(path):
        acquisitions.append(path)
        with owned(path) as custody:
            yield custody

    monkeypatch.setattr(admission, "_owned", observed)

    def observed_health(*arguments):
        health_calls.append(True)
        return installed_health(*arguments)

    monkeypatch.setattr(construction, "_health", observed_health)
    result = recover(prepared)
    assert health_calls == [True]
    assert result["active"] and result["frontdoor_registered"] and not result["reused"]
    assert result["recovered_active"] == slot.name
    assert result["admitted"]["sequence"] == 11
    assert not result["admission_modified"] and not result["constructed"]
    active = construction._read(namespace / activation.ACTIVE)
    assert active["publication"] != decision["retained_active"]["publication"]
    assert construction._read(namespace / activation.HISTORY / active["publication"] / recovery.CONTEXT) == decision
    assert primitive.current_version(namespace) == slot.name
    commands = frontdoor._commands(directory, slot)
    before = states([*commands, *activation._targets(namespace), namespace / activation.ACTIVE])
    assert recover(prepared)["reused"]
    assert acquisitions == [root, root]
    assert health_calls == [True, True]
    assert states(before) == before and database_bytes(root) == floor
    assert user.read_bytes() == b"retained config and schema-1 data"
    for operation in (admission.admit_client_release, construction.construct_client_release):
        with pytest.raises(PlanError):
            if operation is admission.admit_client_release:
                operation(root, identity(), selection(old), staged, trusted, clock=lambda: 150)
            else:
                operation(root, identity(), selection(old), staged,
                          current_policy=lambda: trusted, current_target=lambda: target(old),
                          clock=lambda: 150)
    with pytest.raises(PlanError, match="current attributed"):
        register(root, old, staged, trusted, directory)
    assert register(root, old, staged, trusted, directory, current_recovery=lambda: decision)["reused"]
    assert health_calls == [True, True, True]
    with pytest.raises(PlanError):
        remove_client_runtime(root, identity(), candidate=slot.name)
    assert slot.is_dir() and database_bytes(root) == floor


def test_cli_requires_an_explicit_decision_before_any_state_write(tmp_path, capsys):
    from fleet_manager.__main__ import main

    root = tmp_path / "untouched-root"
    root.mkdir()
    identity_file = tmp_path / "identity.json"
    identity_file.write_bytes(canonical(identity()))
    arguments = ["client-state", "recover-retained", "--root", str(root),
                 "--identity", str(identity_file)]
    for name in ("selection", "release", "proof", "policy", "sources", "staging-parent",
                 "target", "command-directory"):
        arguments.extend(["--" + name, str(tmp_path / "not-created")])
    assert main(arguments) == 1
    output = capsys.readouterr()
    assert "--recovery-decision" in output.err and not output.out
    assert not list(root.iterdir()) and not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("as_string", [False, True])
def test_relative_command_directory_refuses_before_custody_health_or_effects(
    prepared, monkeypatch, as_string,
):
    root, old, staged, trusted, directory, namespace, _slot, decision, *_rest = prepared
    before = states([*activation._targets(namespace), namespace / activation.ACTIVE,
                     namespace / frontdoor.REGISTERED, *frontdoor._command_paths(directory)])
    floor = database_bytes(root)
    acquisitions, health_calls = [], []
    owned = admission._owned

    @contextmanager
    def observed(path):
        acquisitions.append(path)
        with owned(path) as custody:
            yield custody

    def observed_health(*_args):
        health_calls.append(True)
        return {"unit_health_stub": True}

    monkeypatch.chdir(directory.parent)
    monkeypatch.setattr(admission, "_owned", observed)
    monkeypatch.setattr(construction, "_health", observed_health)
    supplied = directory.name if as_string else directory.relative_to(directory.parent)
    with pytest.raises(PlanError, match="explicit absolute command directory"):
        recovery.recover_client_release(
            root, identity(), selection(old), staged, command_directory=supplied,
            current_recovery=lambda: decision, current_policy=lambda: trusted,
            current_target=lambda: target(old), clock=lambda: 150,
        )
    assert states(before) == before and database_bytes(root) == floor
    assert not acquisitions and not health_calls
    assert not (namespace / activation.PENDING).exists()
    assert not (namespace / frontdoor.PENDING).exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows disallows control characters in directory names")
def test_unregistered_control_character_command_path_refuses_before_health_or_selectors(
    prepared, tmp_path, monkeypatch,
):
    root, _old, _staged, _trusted, directory, namespace, _slot, decision, *_rest = prepared
    unregister_client_frontdoor(root, identity(), command_directory=directory)
    invalid_directory = command_directory(tmp_path, "invalid\ncommands")
    decision["command_directory"] = str(invalid_directory)
    arguments = list(prepared)
    arguments[4] = invalid_directory
    before = states([*activation._targets(namespace), namespace / activation.ACTIVE])
    floor = database_bytes(root)
    health_calls = []

    def observed_health(*_args):
        health_calls.append(True)
        return {"unit_health_stub": True}

    monkeypatch.setattr(construction, "_health", observed_health)
    with pytest.raises(PlanError, match="control characters"):
        recover(tuple(arguments))
    assert not health_calls and states(before) == before and database_bytes(root) == floor
    assert not list(invalid_directory.iterdir())
    assert not (namespace / activation.PENDING).exists()
    assert not (namespace / frontdoor.REGISTERED).exists()
    assert not (namespace / frontdoor.PENDING).exists()


@pytest.mark.parametrize("change", [
    "custody", "active", "candidate", "runtime_sha256", "target_digest",
    "admitted", "recovery_decision_digest", "health",
])
def test_shared_health_requires_the_exact_current_owned_activation(prepared, monkeypatch, change):
    root, old, staged, trusted, directory, namespace, _slot, decision, *_rest = prepared
    floor = database_bytes(root)
    with admission._owned(root) as custody:
        selected = activation._activate_client_runtime(
            root, identity(), selection(old), staged, current_policy=lambda: trusted,
            current_target=lambda: target(old), clock=lambda: 150, recovery=decision,
            current_recovery=lambda: decision, ownership=custody,
        )
        active = construction._read(namespace / activation.ACTIVE)
        selected_active = copy.deepcopy(active)
        other_custody = custody
        if change == "custody":
            other_custody = tuple(list(custody))
        elif change == "active":
            selected_active["publication"] = "f" * 32
        elif change in ("admitted", "health"):
            selected[change] = None
        else:
            selected[change] = "foreign"
        before = states([*activation._targets(namespace), *frontdoor._command_paths(directory)])

        def forbidden(*_args):
            raise AssertionError("invalid health handoff must not execute a client")

        monkeypatch.setattr(construction, "_health", forbidden)
        monkeypatch.setattr(frontdoor, "_health", forbidden)
        with pytest.raises(PlanError, match="different owned activation context"):
            frontdoor._register_client_frontdoor(
                root, identity(), selection(old), staged, command_directory=directory,
                current_policy=lambda: trusted, current_target=lambda: target(old),
                clock=lambda: 150, current_recovery=lambda: decision, ownership=custody,
                activation_health=(other_custody, selected, selected_active),
            )
    assert database_bytes(root) == floor and states(before) == before


@pytest.mark.parametrize("change", [
    "no-decision", "owner", "installation", "floor", "active", "completion", "candidate",
    "target", "revoked-trust", "expired", "api", "data", "unknown", "tampered",
    "acknowledgement", "historical-proof", "unretained", "never-activated",
    "same-byte-selector", "same-byte-command",
])
def test_invalid_recovery_never_executes_or_replaces(prepared, monkeypatch, change):
    root, fixture, _staged, trusted, directory, namespace, slot, decision, _newer, _user = prepared
    options = {}
    if change == "no-decision":
        options["current_recovery"] = None
    elif change == "owner":
        decision["owner"] = "other-owner"
    elif change == "installation":
        decision["identity"]["installation"] = "other-installation"
    elif change == "floor":
        decision["admitted_digest"] = "0" * 64
    elif change == "active":
        decision["expected_active"]["publication"] = "0" * 32
    elif change == "completion":
        decision["completion_digest"] = "0" * 64
    elif change == "candidate":
        decision["retained_active"]["candidate"] = "client-9-" + "0" * 64
    elif change == "target":
        decision["target_digest"] = "0" * 64
    elif change == "revoked-trust":
        revoked = copy.deepcopy(trusted)
        revoked["keys"][0]["revoked"] = True
        options["current_policy"] = lambda: revoked
    elif change == "expired":
        options["clock"] = lambda: 10000
    elif change in ("api", "data", "unknown"):
        incompatible = target(fixture)
        incompatible["components"]["cli"][
            "api" if change == "api" else "data_schemas"
        ] = 65535 if change == "api" else ([65535] if change == "data" else [])
        decision["target_digest"] = digest(incompatible)
        options["current_target"] = lambda: incompatible
    elif change == "tampered":
        (slot / "candidate-bytes").write_bytes(b"changed candidate")
    elif change in ("acknowledgement", "historical-proof"):
        history = namespace / activation.HISTORY / decision["retained_active"]["publication"]
        (history / (recovery.ACKNOWLEDGED if change == "acknowledgement" else "publication.jsonl")).unlink()
    elif change == "unretained":
        slot.rename(namespace / "unretained-backup")
    elif change == "never-activated":
        decision["retained_active"]["publication"] = "f" * 32
    else:
        path = (namespace / "current-version" if change == "same-byte-selector"
                else next(iter(frontdoor._commands(directory, slot))))
        peer = path.with_name("foreign-peer")
        peer.write_bytes(path.read_bytes())
        os.replace(peer, path)
    floor = database_bytes(root)
    before = states([*activation._targets(namespace), *frontdoor._commands(directory, slot)])
    calls = []
    monkeypatch.setattr(construction, "_health", lambda *_args: calls.append(True))
    with pytest.raises(PlanError):
        recover(prepared, **options)
    assert not calls and database_bytes(root) == floor and states(before) == before
    if change == "unretained":
        assert not slot.exists() and (namespace / "unretained-backup").is_dir()


@pytest.mark.parametrize("error", [OSError, RuntimeError])
@pytest.mark.parametrize("phase", ["acquire", "release"])
def test_recovery_custody_failure_is_explicit_not_raw(prepared, monkeypatch, error, phase):
    root, _old, _staged, _trusted, directory, namespace, _slot, _decision, *_rest = prepared
    floor = database_bytes(root)
    before = states([*activation._targets(namespace), *frontdoor._command_paths(directory)])
    owned = admission._owned

    @contextmanager
    def failed(path):
        if phase == "acquire":
            raise error("injected custody acquisition failure")
        with owned(path) as custody:
            yield custody
        raise error("injected custody release failure")

    monkeypatch.setattr(admission, "_owned", failed)
    with pytest.raises(PlanError, match="owned installation custody" if phase == "acquire" else "outcome is unknown"):
        recover(prepared)
    assert database_bytes(root) == floor
    if phase == "acquire":
        assert states(before) == before
    else:
        assert (namespace / frontdoor.REGISTERED).is_file()
        assert not (namespace / frontdoor.PENDING).exists()


def test_recovery_context_failure_precedes_pending_and_selector_effects(prepared, monkeypatch):
    root, _old, _staged, _trusted, directory, namespace, _slot, _decision, *_rest = prepared
    floor = database_bytes(root)
    before = states([*activation._targets(namespace), *frontdoor._command_paths(directory)])
    write = construction._write

    def interrupted(path, value):
        if path.name == recovery.CONTEXT:
            raise OSError("injected pre-intent recovery context failure")
        return write(path, value)

    monkeypatch.setattr(construction, "_write", interrupted)
    with pytest.raises(PlanError, match="could not establish owned selectors"):
        recover(prepared)
    assert not (namespace / activation.PENDING).exists()
    assert database_bytes(root) == floor and states(before) == before


def test_changed_decision_at_health_boundary_refuses_publication(prepared, monkeypatch):
    _root, _fixture, _staged, _trusted, _directory, namespace, _slot, decision, *_rest = prepared
    before = states(activation._targets(namespace))

    def changed(_slot, checkpoint, *_args):
        decision["revision"] += 1
        checkpoint()

    monkeypatch.setattr(construction, "_health", changed)
    with pytest.raises(PlanError, match="decision changed"):
        recover(prepared)
    assert states(before) == before and not (namespace / activation.PENDING).exists()


@pytest.mark.parametrize("boundary", ["current-version", "last-known-good", activation.ACTIVE])
def test_interrupted_selector_recovery_uses_only_exact_bounded_context(prepared, monkeypatch, boundary):
    root, _fixture, _staged, _trusted, _directory, namespace, _slot, _decision, *_rest = prepared
    floor = database_bytes(root)
    original = os.replace

    def interrupted(source, destination):
        if destination == namespace / boundary:
            raise OSError("injected retained recovery interruption")
        return original(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            recover(prepared)
    assert (namespace / activation.PENDING).exists()
    result = recover(prepared)
    assert result["frontdoor_registered"] and not result["reused"]
    assert database_bytes(root) == floor and not (namespace / activation.PENDING).exists()


def test_settled_withdrawal_is_an_exact_generation_not_absence(prepared):
    root, _fixture, _staged, _trusted, directory, namespace, _slot, decision, *_rest = prepared
    unregister_client_frontdoor(root, identity(), command_directory=directory)
    deactivate_client_runtime(root, identity())
    decision["expected_active"] = None
    decision["expected_withdrawal"] = digest(construction._read(namespace / recovery.WITHDRAWN))
    assert recover(prepared)["frontdoor_registered"]
    unregister_client_frontdoor(root, identity(), command_directory=directory)
    deactivate_client_runtime(root, identity())
    with pytest.raises(PlanError, match="different settled withdrawal"):
        recover(prepared)


@pytest.mark.parametrize("change", ["later-floor", "later-active", "decision", "target"])
def test_completed_recovery_does_not_make_a_stale_command_authority(prepared, tmp_path, change):
    root, old, staged, trusted, directory, namespace, _slot, decision, newer, _user = prepared
    recover(prepared)
    if change == "later-floor":
        newer = higher_release(old, tmp_path, sequence=12)
        with retained(newer, tmp_path) as (new_stage, policy):
            admission.admit_client_release(
                root, identity(), selection(newer), new_stage, policy, clock=lambda: 150,
            )
    elif change == "later-active":
        with retained(newer, tmp_path) as (new_stage, policy):
            activate(root, newer, new_stage, policy)
    elif change == "decision":
        decision["revision"] += 1
    else:
        decision["target_digest"] = "0" * 64
    before = states([*activation._targets(namespace), *frontdoor._command_paths(directory)])
    floor = database_bytes(root)
    with pytest.raises(PlanError):
        register(root, old, staged, trusted, directory, current_recovery=lambda: decision)
    with pytest.raises(PlanError):
        recover(prepared)
    assert database_bytes(root) == floor and states(before) == before


def test_interrupted_commands_require_proved_withdrawal_then_same_decision_retry(prepared, monkeypatch):
    root, _old, _staged, _trusted, directory, namespace, _slot, decision, *_rest = prepared
    floor = database_bytes(root)
    original = os.replace

    def interrupted(source, destination):
        if destination == namespace / frontdoor.REGISTERED:
            raise OSError("injected command acknowledgement interruption")
        return original(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupted)
        with pytest.raises(PlanError, match="selectors recovered; command outcome is not settled"):
            recover(prepared)
    active = construction._read(namespace / activation.ACTIVE)
    assert recovery._context(namespace, active, decision)
    assert (namespace / frontdoor.PENDING).is_file()
    with pytest.raises(PlanError, match="outcome is unknown"):
        recover(prepared)
    assert unregister_client_frontdoor(root, identity(), command_directory=directory)["commands_absent"]
    assert recover(prepared)["frontdoor_registered"] and database_bytes(root) == floor


def test_command_retry_budget_cannot_be_reset_by_withdrawal(prepared):
    root, _old, _staged, _trusted, directory, namespace, _slot, decision, *_rest = prepared
    recover(prepared)
    active = construction._read(namespace / activation.ACTIVE)
    workspace = namespace / activation.HISTORY / active["publication"]
    for index in range(1, activation.MAX_ATTEMPTS):
        construction._write_exclusive(workspace / f"recovery-command.{index}.json", decision)
    unregister_client_frontdoor(root, identity(), command_directory=directory)
    with pytest.raises(PlanError, match="attempts are invalid or exhausted"):
        recover(prepared)
    assert not any(path.exists() for path in frontdoor._command_paths(directory))


@pytest.mark.parametrize("boundary", ["empty", "partial", "replace"])
def test_interrupted_command_attempt_staging_preserves_same_decision_retry(prepared, monkeypatch, boundary):
    root, _old, _staged, _trusted, directory, namespace, _slot, decision, *_rest = prepared
    floor = database_bytes(root)
    commands = states(frontdoor._command_paths(directory))
    write, replace = construction._write_exclusive, os.replace

    def interrupted_write(path, value):
        if "recovery-command." in path.name and boundary != "replace":
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                if boundary == "partial":
                    os.write(descriptor, canonical(value)[:17])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            raise OSError("injected command attempt staging write failure")
        return write(path, value)

    def interrupted_replace(source, destination):
        if destination.name.startswith("recovery-command."):
            raise OSError("injected command attempt staging replacement failure")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(construction, "_write_exclusive", interrupted_write)
        if boundary == "replace":
            patch.setattr(os, "replace", interrupted_replace)
        with pytest.raises(PlanError):
            recover(prepared)
    assert states(commands) == commands and not (namespace / frontdoor.PENDING).exists()
    active = construction._read(namespace / activation.ACTIVE)
    history = namespace / activation.HISTORY / active["publication"]
    abandoned = states(path for path in history.iterdir() if "recovery-command." in path.name)
    assert abandoned
    assert recover(prepared)["frontdoor_registered"]
    assert all(path.name.startswith(".recovery-command.") for path in abandoned)
    assert states(abandoned) == abandoned and database_bytes(root) == floor
    assert construction._read(history / "recovery-command.0.json") == decision
    assert not (history / "recovery-command.1.json").exists()


def test_malformed_committed_command_attempt_is_not_repaired(prepared):
    root, _old, _staged, _trusted, directory, namespace, _slot, _decision, *_rest = prepared
    floor = database_bytes(root)
    recover(prepared)
    active = construction._read(namespace / activation.ACTIVE)
    marker = namespace / activation.HISTORY / active["publication"] / "recovery-command.0.json"
    marker.write_bytes(b'{"schema":')
    unregister_client_frontdoor(root, identity(), command_directory=directory)
    with pytest.raises(PlanError, match="invalid bounded JSON"):
        recover(prepared)
    assert marker.read_bytes() == b'{"schema":' and database_bytes(root) == floor
    assert not any(path.exists() for path in frontdoor._command_paths(directory))
