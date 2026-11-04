"""Exact producer withdrawal cannot hold startup or recover over a successor."""

import json
import os
import subprocess
import sys

import pytest

from fleet_manager import client_generation_frontdoor as frontdoor, client_generations as generations
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_admission import database_bytes, identity
from test_client_construction import signed_wheel as signed_wheel  # noqa: F401
from test_client_distribution import FLAGS
from test_client_frontdoor import states
from test_client_generation_construction import construct_generation, direct_health
from test_client_generation_frontdoor import installation as installation, pointer, publish  # noqa: F401
from test_client_generations import FIRST, SECOND, reserve, state


def withdraw(installation, publication, generation=FIRST, **kwargs):
    root, directory, _fixture, _staged, _trusted, _first = installation
    return frontdoor.unregister_client_generation(
        root, identity(), state(root)[2][generation], publication=publication,
        command_directory=directory, **kwargs,
    )


@pytest.mark.timeout(90)
def test_exact_withdrawal_is_retryable_and_preserves_runtime_floor_and_configuration(
    primitive, installation,
):
    root, directory, _fixture, _staged, _trusted, first = installation
    published = publish(installation)
    assert published["publication"] == pointer(root)[1]
    before = state(root, fences=True)
    selected = pointer(root)
    user = root / "configuration"
    user.write_bytes(b"retained configuration\n")
    result = withdraw(installation, published["publication"])
    assert result["commands_absent"] and not result["superseded"] and result["mutations_performed"]
    assert not any(directory.glob("fleet-manager*"))
    assert pointer(root)[:2] == (None, None) and pointer(root)[2] == selected[2]
    after = state(root, fences=True)
    assert before[:3] == after[:3]
    assert after[3]["current_generation"] == FIRST and after[3]["selected_generation"] is None
    saved = database_bytes(root)
    retry = withdraw(installation, published["publication"])
    assert retry["commands_absent"] and not retry["mutations_performed"]
    assert database_bytes(root) == saved
    direct_health(first, directory.parent)
    assert user.read_bytes() == b"retained configuration\n"
    generations._retire_generation(root, identity(), state(root)[2][FIRST], clock=lambda: 151)
    assert FIRST in state(root, fences=True)[3]["retired"]
    assert withdraw(installation, published["publication"])["commands_absent"]


@pytest.mark.timeout(90)
def test_withdrawal_refuses_same_byte_foreign_object_before_any_removal(primitive, installation):
    root, directory, _fixture, _staged, _trusted, _first = installation
    published = publish(installation)
    command = frontdoor.frontdoor._command_paths(directory)[-1]
    replacement = directory / "foreign"
    replacement.write_bytes(command.read_bytes())
    replacement.chmod(command.stat().st_mode)
    os.replace(replacement, command)
    before = states(frontdoor.frontdoor._command_paths(directory))
    saved = database_bytes(root)
    with pytest.raises(PlanError, match="unattributed command"):
        withdraw(installation, published["publication"])
    assert states(frontdoor.frontdoor._command_paths(directory)) == before
    assert database_bytes(root) == saved and pointer(root)[0] == FIRST


@pytest.mark.timeout(90)
@pytest.mark.parametrize("metadata", ["remove-context.json", "remove-intent.json"])
@pytest.mark.parametrize("boundary", ["empty", "partial", "complete"])
def test_interrupted_withdrawal_metadata_does_not_poison_exact_retry(
    primitive, installation, monkeypatch, metadata, boundary,
):
    root, directory, _fixture, _staged, _trusted, first = installation
    published = publish(installation)
    workspace = root / frontdoor.HISTORY / published["publication"]
    producer = primitive._runtime_component("versioned_runtime_publication")
    owner, name = (
        (producer, "create_context") if metadata == "remove-context.json"
        else (frontdoor.construction, "_write_exclusive")
    )
    actual = getattr(owner, name)
    before = states(frontdoor.frontdoor._command_paths(directory))
    saved = database_bytes(root)

    def interrupted(path, *args, **kwargs):
        if path.name != metadata and not path.name.startswith("." + metadata + ".write-"):
            return actual(path, *args, **kwargs)
        if boundary == "complete":
            actual(path, *args, **kwargs)
        else:
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                stream.write(b"" if boundary == "empty" else b'{"unfinished":')
                stream.flush()
                os.fsync(stream.fileno())
        raise OSError("injected withdrawal metadata interruption")

    with monkeypatch.context() as injected:
        injected.setattr(owner, name, interrupted)
        with pytest.raises(PlanError):
            withdraw(installation, published["publication"])
    assert not (workspace / metadata).exists()
    assert states(frontdoor.frontdoor._command_paths(directory)) == before
    assert database_bytes(root) == saved and pointer(root)[1] == published["publication"]
    attempts = list(workspace.glob("." + metadata + ".write-*"))
    assert len(attempts) == 1
    retained = {path: (path.read_bytes(), path.stat().st_ino) for path in attempts}
    assert withdraw(installation, published["publication"])["commands_absent"]
    assert pointer(root)[:2] == (None, None)
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in attempts} == retained
    direct_health(first, directory.parent)


@pytest.mark.timeout(90)
@pytest.mark.parametrize("metadata", ["remove-context.json", "remove-intent.json"])
def test_withdrawal_metadata_publication_preserves_a_foreign_final_winner(
    primitive, installation, monkeypatch, metadata,
):
    root, directory, _fixture, _staged, _trusted, _first = installation
    published = publish(installation)
    workspace = root / frontdoor.HISTORY / published["publication"]
    producer = primitive._runtime_component("versioned_runtime_publication")
    actual = producer._publish_receipt
    before = states(frontdoor.frontdoor._command_paths(directory))
    saved = database_bytes(root)

    def competing(source, target):
        if target == workspace / metadata:
            target.write_bytes(b"foreign metadata winner")
            target.chmod(0o600)
        return actual(source, target)

    with monkeypatch.context() as injected:
        injected.setattr(producer, "_publish_receipt", competing)
        with pytest.raises(PlanError):
            withdraw(installation, published["publication"])
    assert (workspace / metadata).read_bytes() == b"foreign metadata winner"
    assert states(frontdoor.frontdoor._command_paths(directory)) == before
    assert database_bytes(root) == saved and pointer(root)[1] == published["publication"]


@pytest.mark.timeout(90)
@pytest.mark.parametrize("boundary", ["partial", "before-commit", "after-commit"])
def test_interrupted_withdrawal_retries_native_effects_and_selection_ack(
    primitive, installation, monkeypatch, boundary,
):
    root, directory, _fixture, _staged, _trusted, first = installation
    published = publish(installation)
    producer = primitive._runtime_component("versioned_runtime_publication")
    recover = producer._recover_locked
    commit = frontdoor.admission._commit

    def partial(proof_root, entries, proofs, required, receipt):
        recover(proof_root, entries, proofs, required[:1], receipt)
        raise OSError("injected after first actual owned unlink")

    def interrupted(connection):
        if boundary == "after-commit":
            commit(connection)
        raise OSError("injected withdrawal selection durability boundary")

    with monkeypatch.context() as injected:
        if boundary == "partial":
            injected.setattr(producer, "_recover_locked", partial)
        else:
            injected.setattr(frontdoor.admission, "_commit", interrupted)
        with pytest.raises(PlanError, match="withdrawal outcome is unknown"):
            withdraw(installation, published["publication"])
    workspace = root / frontdoor.HISTORY / published["publication"]
    assert (workspace / frontdoor.REMOVAL).is_file()
    assert (workspace / "copy-context.json").is_file()
    assert (workspace / "copy-forward.jsonl").is_file()
    assert pointer(root)[0] == (None if boundary == "after-commit" else FIRST)
    remaining = list(directory.glob("fleet-manager*"))
    assert bool(remaining) is (boundary == "partial" and len(published["commands"]) > 1)
    result = withdraw(installation, published["publication"])
    assert result["commands_absent"] and not result["superseded"]
    assert pointer(root)[:2] == (None, None) and not list(directory.glob("fleet-manager*"))
    direct_health(first, directory.parent)


@pytest.mark.timeout(90)
def test_delayed_old_withdrawal_preserves_successor_without_reading_old_copy_proofs(
    primitive, installation,
):
    root, directory, fixture, staged, trusted, _first = installation
    old = publish(installation)
    construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    new = publish(installation, SECOND)
    commands = frontdoor.frontdoor._command_paths(directory)
    before = states(commands)
    saved = database_bytes(root)
    (root / frontdoor.HISTORY / old["publication"] / "copy-forward.jsonl").write_bytes(b"corrupt\n")
    result = withdraw(installation, old["publication"])
    assert result["superseded"] and not result["commands_absent"] and not result["mutations_performed"]
    assert pointer(root)[1] == new["publication"]
    assert states(commands) == before and database_bytes(root) == saved


@pytest.mark.timeout(90)
def test_republish_same_generation_after_removal_intent_uses_new_attempt(
    primitive, installation, monkeypatch,
):
    root, directory, _fixture, _staged, _trusted, _first = installation
    old = publish(installation)
    producer = primitive._runtime_component("versioned_runtime_publication")

    def interrupted(*args):
        raise OSError("injected before actual removal")

    with monkeypatch.context() as injected:
        injected.setattr(producer, "_recover_locked", interrupted)
        with pytest.raises(PlanError, match="withdrawal outcome is unknown"):
            withdraw(installation, old["publication"])
    new = publish(installation)
    assert not new["reused"] and new["publication"] != old["publication"]
    commands = frontdoor.frontdoor._command_paths(directory)
    before = states(commands)
    saved = database_bytes(root)
    assert withdraw(installation, old["publication"])["superseded"]
    assert states(commands) == before and database_bytes(root) == saved
    assert withdraw(installation, new["publication"])["commands_absent"]


@pytest.mark.timeout(90)
@pytest.mark.parametrize("corruption", ["intent", "foreign-context", "missing-context"])
def test_corrupt_removal_proof_refuses_before_any_command_effect(
    primitive, installation, monkeypatch, corruption,
):
    root, directory, _fixture, _staged, _trusted, _first = installation
    published = publish(installation)
    producer = primitive._runtime_component("versioned_runtime_publication")

    def interrupted(*args):
        raise OSError("injected before actual removal")

    with monkeypatch.context() as injected:
        injected.setattr(producer, "_recover_locked", interrupted)
        with pytest.raises(PlanError, match="withdrawal outcome is unknown"):
            withdraw(installation, published["publication"])
    workspace = root / frontdoor.HISTORY / published["publication"]
    context = workspace / "remove-context.json"
    if corruption == "intent":
        (workspace / frontdoor.REMOVAL).write_bytes(b"{}\n")
    elif corruption == "foreign-context":
        replacement = workspace / "foreign-context"
        replacement.write_bytes(context.read_bytes())
        replacement.chmod(context.stat().st_mode)
        os.replace(replacement, context)
    else:
        context.unlink()
    before = states(frontdoor.frontdoor._command_paths(directory))
    saved = database_bytes(root)
    with pytest.raises(PlanError):
        withdraw(installation, published["publication"])
    assert states(frontdoor.frontdoor._command_paths(directory)) == before
    assert database_bytes(root) == saved and pointer(root)[1] == published["publication"]


@pytest.mark.timeout(90)
def test_exact_pending_withdrawal_does_not_restore_previous_selected_commands(
    primitive, installation, monkeypatch,
):
    root, directory, fixture, staged, trusted, _first = installation
    old = publish(installation)
    second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    actual = frontdoor.frontdoor._health

    def interrupted(*args, **kwargs):
        actual(*args, **kwargs)
        raise OSError("injected after actual default health")

    with monkeypatch.context() as injected:
        injected.setattr(frontdoor.frontdoor, "_health", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            publish(installation, SECOND)
    pending = json.loads(pointer(root)[2])["publication"]
    assert pointer(root)[1] == old["publication"]
    before = states(frontdoor.frontdoor._command_paths(directory))
    assert withdraw(installation, old["publication"])["superseded"]
    assert states(frontdoor.frontdoor._command_paths(directory)) == before
    assert withdraw(installation, pending, SECOND)["commands_absent"]
    assert pointer(root)[:2] == (None, None) and not list(directory.glob("fleet-manager*"))
    direct_health(second, directory.parent)


@pytest.mark.timeout(90)
def test_withdrawal_requires_exact_attempt_reservation_and_physical_directory(
    primitive, installation,
):
    root, directory, fixture, staged, trusted, _first = installation
    published = publish(installation)
    reserve(root, fixture, staged, trusted, generation=SECOND, request="forward-2")
    saved = database_bytes(root)
    before = states(frontdoor.frontdoor._command_paths(directory))
    with pytest.raises(PlanError, match="anchored publication"):
        withdraw(installation, "f" * 32)
    with pytest.raises(PlanError, match="exact generation or directory"):
        withdraw(installation, published["publication"], SECOND)
    other = directory.parent / "other"
    other.mkdir(mode=0o700)
    with pytest.raises(PlanError, match="exact generation or directory"):
        frontdoor.unregister_client_generation(
            root, identity(), state(root)[2][FIRST], publication=published["publication"],
            command_directory=other,
        )
    assert database_bytes(root) == saved and states(frontdoor.frontdoor._command_paths(directory)) == before


_DELAYED_REMOVAL = """
import json,sys
from contextlib import contextmanager
from pathlib import Path
from fleet_manager import artifact_staging,client_generation_frontdoor
value=json.loads(sys.stdin.readline())
actual_primitive=artifact_staging._primitive
@contextmanager
def intercepted():
    with actual_primitive() as primitive:
        producer=primitive._runtime_component("versioned_runtime_publication")
        actual=producer._recover_locked
        def delayed(*args,**kwargs):
            print("BEFORE_NATIVE_REMOVAL",flush=True)
            if sys.stdin.readline() != "\\n":
                raise RuntimeError("removal release barrier missing")
            return actual(*args,**kwargs)
        producer._recover_locked=delayed
        yield primitive
artifact_staging._primitive=intercepted
result=client_generation_frontdoor.unregister_client_generation(
    Path(value["root"]),value["identity"],value["reservation"],
    publication=value["publication"],command_directory=Path(value["directory"]),
)
print(json.dumps(result),flush=True)
"""


@pytest.mark.timeout(90)
def test_independent_removal_does_not_hold_lineage_writer_during_native_cleanup(
    primitive, installation,
):
    root, directory, fixture, staged, trusted, _first = installation
    old = publish(installation)
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", _DELAYED_REMOVAL], cwd=directory.parent,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **FLAGS,
    )
    try:
        process.stdin.write(json.dumps({
            "root": str(root), "identity": identity(), "reservation": state(root)[2][FIRST],
            "publication": old["publication"], "directory": str(directory),
        }) + "\n")
        process.stdin.flush()
        barrier = process.stdout.readline()
        assert barrier.strip() == "BEFORE_NATIVE_REMOVAL", (
            barrier, process.stderr.read() if process.poll() is not None else process.poll(),
        )
        second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
        assert process.poll() is None and pointer(root)[0] == FIRST
        direct_health(second, directory.parent)
        stdout, stderr = process.communicate("\n", timeout=45)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["commands_absent"] and pointer(root)[:2] == (None, None)
        new = publish(installation, SECOND)
        before = states(frontdoor.frontdoor._command_paths(directory))
        assert withdraw(installation, old["publication"])["superseded"]
        assert pointer(root)[1] == new["publication"]
        assert states(frontdoor.frontdoor._command_paths(directory)) == before
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
