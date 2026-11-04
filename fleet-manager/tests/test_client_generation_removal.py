"""Generation retirement reuses native deletion without blocking a forward startup."""

import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from fleet_manager import client_construction as construction, client_generations as generations
from fleet_manager import client_runtime_removal as removal
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_admission import database_bytes, identity, retained
from test_client_construction import signed_wheel as signed_wheel  # noqa: F401
from test_client_distribution import FLAGS
from test_client_frontdoor import states
from test_client_generation_construction import construct_generation, direct_health
from test_client_generation_frontdoor import (
    frontdoor, higher_fixture, installation as installation, pointer, publish,  # noqa: F401
)
from test_client_generation_withdrawal import withdraw
from test_client_generations import FIRST, SECOND, state


def runtime(installation):
    first = installation[-1]
    root = Path(first["generation_root"])
    namespace = root / construction.NAMESPACE
    return root, namespace, namespace / "versions" / first["candidate"]


def remove(installation):
    return generations.remove_client_generation(
        installation[0], identity(), state(installation[0])[2][FIRST], clock=lambda: 151,
    )


@pytest.mark.timeout(90)
def test_actual_generation_removal_and_retry_preserve_lineage_and_external_proofs(
    primitive, installation, monkeypatch,
):
    root, _directory, _fixture, _staged, _trusted, _first = installation
    generation_root, namespace, slot = runtime(installation)
    user = root / "custom-config"
    user.write_bytes(b"retained configuration\n")
    before = state(root)
    preserved = states([
        user, generation_root / generations._OWNER, namespace / construction.OWNER,
        namespace / "client-inputs" / (slot.name + ".json"),
    ])

    def forbidden(*args, **kwargs):
        raise AssertionError("cleanup must not run health, admission or construction")

    monkeypatch.setattr(construction, "_health", forbidden)
    monkeypatch.setattr(generations, "_generation_root", forbidden)
    monkeypatch.setattr(generations, "_reserve_generation", forbidden)
    result = remove(installation)
    assert result["generation_retired"] and result["runtime_removed"] and not result["reused"]
    assert not result["installation_retired"] and result["mutations_performed"]
    assert not slot.exists() and state(root)[:3] == before[:3]
    saved = database_bytes(root)
    retried = remove(installation)
    assert retried["runtime_removed"] and retried["reused"] and not retried["mutations_performed"]
    assert database_bytes(root) == saved and states(preserved) == preserved
    assert not (namespace / construction.REMOVING).exists()
    assert len(list((namespace / removal.HISTORY).iterdir())) == 1


@pytest.mark.timeout(90)
def test_selected_generation_refuses_before_retirement_then_owned_withdrawal_allows_delete(
    primitive, installation,
):
    root = installation[0]
    published = publish(installation)
    before = database_bytes(root)
    with pytest.raises(PlanError, match="owned shared withdrawal"):
        remove(installation)
    assert database_bytes(root) == before and FIRST not in state(root, fences=True)[3]["retired"]
    withdraw(installation, published["publication"])
    assert remove(installation)["runtime_removed"] and not runtime(installation)[2].exists()
    assert pointer(root)[:2] == (None, None)


@pytest.mark.timeout(90)
def test_delayed_generation_delete_preserves_new_selected_runtime_and_commands(primitive, installation):
    root, directory, fixture, staged, trusted, _first = installation
    old = publish(installation)
    second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    new = publish(installation, SECOND)
    preserved = states(frontdoor.frontdoor._command_paths(directory))
    (root / frontdoor.HISTORY / old["publication"] / "copy-forward.jsonl").write_bytes(b"corrupt\n")
    assert remove(installation)["runtime_removed"]
    assert states(preserved) == preserved and pointer(root)[1] == new["publication"]
    assert state(root, fences=True)[3]["current_generation"] == SECOND
    direct_health(second, directory.parent)


@pytest.mark.timeout(90)
def test_generation_cleanup_accepts_a_newer_admitted_floor_without_old_release_authentication(
    primitive, installation,
):
    root, directory, fixture, _staged, _trusted, _first = installation
    area = directory.parent / "higher"
    area.mkdir(mode=0o700)
    higher = higher_fixture(fixture, area)
    with retained(higher, area) as (staged, trusted):
        second = construct_generation(root, higher, staged, trusted, SECOND, "forward-2")
        before = state(root)
        assert before[0]["sequence"] > before[2][FIRST]["selection"]["sequence"]
        assert remove(installation)["runtime_removed"]
        assert state(root)[:3] == before[:3]
        direct_health(second, directory.parent)


@pytest.mark.timeout(90)
@pytest.mark.parametrize("change", ["identity", "request"])
def test_cleanup_rejects_wrong_generation_authority_before_retirement(
    primitive, installation, change,
):
    root = installation[0]
    reservation = copy.deepcopy(state(root)[2][FIRST])
    owner = identity()
    if change == "identity":
        owner["installation"] = "foreign-installation"
    else:
        reservation["request"] = "different-request"
    before = database_bytes(root)
    with pytest.raises(PlanError):
        generations.remove_client_generation(root, owner, reservation, clock=lambda: 151)
    assert database_bytes(root) == before and runtime(installation)[2].is_dir()


@pytest.mark.timeout(90)
def test_pending_old_commands_refuse_even_without_selected_generation(
    primitive, installation, monkeypatch,
):
    root, directory, fixture, staged, trusted, first = installation
    actual = frontdoor.frontdoor._health

    def interrupted(*args, **kwargs):
        actual(*args, **kwargs)
        raise OSError("injected after actual default health")

    with monkeypatch.context() as injected:
        injected.setattr(frontdoor.frontdoor, "_health", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            publish(installation)
    assert pointer(root)[0] is None
    before = states(frontdoor.frontdoor._command_paths(directory))
    with pytest.raises(PlanError, match="owned shared withdrawal"):
        remove(installation)
    assert states(before) == before and runtime(installation)[2].is_dir()
    assert FIRST in state(root, fences=True)[3]["retired"]
    second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    direct_health(first, directory.parent)
    assert publish(installation, SECOND)["selected"]
    assert remove(installation)["runtime_removed"]
    direct_health(second, directory.parent)


@pytest.mark.timeout(90)
def test_new_pending_attempt_cannot_attribute_restored_old_baseline_to_successor(
    primitive, installation, monkeypatch,
):
    root, directory, fixture, staged, trusted, _first = installation
    health = frontdoor.frontdoor._health

    def unacknowledged(*args, **kwargs):
        health(*args, **kwargs)
        raise OSError("injected after actual old default health")

    with monkeypatch.context() as injected:
        injected.setattr(frontdoor.frontdoor, "_health", unacknowledged)
        with pytest.raises(PlanError, match="outcome is unknown"):
            publish(installation)
    assert pointer(root)[0] is None
    construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    publication = primitive._runtime_component("versioned_runtime_publication")
    actual = publication.CopyPublication.verify

    def interrupted(copy):
        actual(copy)
        if not copy.published:
            raise OSError("injected after native copy, before producer success")

    with monkeypatch.context() as injected:
        injected.setattr(publication.CopyPublication, "verify", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            publish(installation, SECOND)
    pending = json.loads(pointer(root)[2])
    assert pending["generation"] == SECOND
    _commands, proofs = frontdoor._proofs(root, pending, state(root)[2], publication)
    assert all(
        publication.state_path(path, private=True) in proofs[path]
        for path in frontdoor.frontdoor._command_paths(directory)
    )
    with pytest.raises(PlanError, match="unwithdrawn or foreign commands"):
        remove(installation)
    assert runtime(installation)[2].is_dir()
    assert withdraw(installation, pending["publication"], SECOND)["commands_absent"]


@pytest.mark.timeout(90)
def test_foreign_same_byte_successor_command_blocks_old_runtime_deletion(primitive, installation):
    root, directory, fixture, staged, trusted, _first = installation
    construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
    publish(installation, SECOND)
    command = frontdoor.frontdoor._command_paths(directory)[0]
    replacement = directory / "replacement"
    replacement.write_bytes(command.read_bytes())
    replacement.chmod(command.stat().st_mode)
    os.replace(replacement, command)
    before = states(frontdoor.frontdoor._command_paths(directory))
    with pytest.raises(PlanError, match="foreign commands"):
        remove(installation)
    assert states(before) == before and runtime(installation)[2].is_dir()
    assert not (runtime(installation)[1] / construction.REMOVING).exists()


@pytest.mark.timeout(90)
@pytest.mark.parametrize("change", ["root-proof", "physical-root", "owner", "input"])
def test_generation_cleanup_requires_exact_registered_root_and_input(
    primitive, installation, change,
):
    root = installation[0]
    generation_root, namespace, slot = runtime(installation)
    if change == "root-proof":
        with sqlite3.connect(root / generations.client_admission.DATABASE) as connection:
            connection.execute("UPDATE generations SET root = NULL WHERE generation = ?", (FIRST,))
    elif change == "physical-root":
        generation_root.rename(generation_root.with_name("retained-original"))
        generation_root.mkdir(mode=0o700)
    else:
        path = generation_root / generations._OWNER if change == "owner" else (
            namespace / "client-inputs" / (slot.name + ".json")
        )
        path.write_bytes(b"{}\n")
    with pytest.raises(PlanError):
        remove(installation)
    assert not (namespace / construction.REMOVING).exists()


@pytest.mark.timeout(90)
@pytest.mark.parametrize("custody", ["retention", "live-process"])
def test_generation_cleanup_retains_canonical_reference_protection(
    primitive, installation, monkeypatch, custody,
):
    _generation_root, namespace, slot = runtime(installation)
    if custody == "live-process":
        monkeypatch.setattr(primitive, "_versions_with_live_process", lambda _root: {slot.name})
        with pytest.raises(PlanError):
            remove(installation)
    else:
        with primitive.retention_lease(namespace, slot.name):
            with pytest.raises(PlanError):
                remove(installation)
    assert slot.is_dir() and not (namespace / construction.REMOVING).exists()


def interrupt_native(monkeypatch):
    if os.name == "nt":
        actual = removal._windows_node

        def interrupted(path, *args, **kwargs):
            result = actual(path, *args, **kwargs)
            if kwargs.get("remove") and result["kind"] == "file":
                raise OSError("injected after actual native file deletion")
            return result

        monkeypatch.setattr(removal, "_windows_node", interrupted)
    else:
        actual = removal._posix_claimed_node

        def interrupted(parent, name, publication, expected, claims, path, visited, **kwargs):
            result = actual(parent, name, publication, expected, claims, path, visited, **kwargs)
            if kwargs.get("remove") and expected["kind"] != "directory":
                raise OSError("injected after actual native file deletion")
            return result

        monkeypatch.setattr(removal, "_posix_claimed_node", interrupted)


@pytest.mark.timeout(90)
@pytest.mark.parametrize("corrupt", [None, "intent", "snapshot"])
def test_partial_generation_native_delete_retries_only_exact_retained_evidence(
    primitive, installation, monkeypatch, corrupt,
):
    _generation_root, namespace, slot = runtime(installation)
    with monkeypatch.context() as injected:
        interrupt_native(injected)
        with pytest.raises(PlanError, match="outcome is unknown"):
            remove(installation)
    intent = namespace / construction.REMOVING
    assert intent.is_file()
    if corrupt == "intent":
        intent.write_bytes(b"{}\n")
    elif corrupt == "snapshot":
        value = construction._read(intent)
        remaining = slot if os.name == "nt" else (
            namespace / removal.HISTORY / value["removal"] / "claims" / removal._claim_key([])
        )
        (remaining / "foreign").write_bytes(b"not owned by removal\n")
    if corrupt is not None:
        with pytest.raises(PlanError):
            remove(installation)
        assert intent.is_file()
    else:
        assert remove(installation)["runtime_removed"] and not slot.exists()
        assert remove(installation)["reused"] and not intent.exists()


_DELAYED_RUNTIME_REMOVAL = """
import json,os,sys
from pathlib import Path
from fleet_manager import client_generations,client_runtime_removal as removal
value=json.loads(sys.stdin.readline())
name="_tree" if os.name=="nt" else "_posix_claimed_tree"
actual=getattr(removal,name)
def delayed(*args,**kwargs):
    if kwargs.get("remove"):
        print("BEFORE_NATIVE_REMOVAL",flush=True)
        if sys.stdin.readline() != "\\n":
            raise RuntimeError("removal release barrier missing")
    return actual(*args,**kwargs)
setattr(removal,name,delayed)
result=client_generations.remove_client_generation(
    Path(value["root"]),value["identity"],value["reservation"],clock=lambda:151,
)
print(json.dumps(result),flush=True)
"""


@pytest.mark.timeout(90)
def test_independent_old_runtime_cleanup_allows_new_start_and_publication(primitive, installation):
    root, directory, fixture, staged, trusted, _first = installation
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", _DELAYED_RUNTIME_REMOVAL], cwd=directory.parent,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **FLAGS,
    )
    try:
        process.stdin.write(json.dumps({
            "root": str(root), "identity": identity(), "reservation": state(root)[2][FIRST],
        }) + "\n")
        process.stdin.flush()
        barrier = process.stdout.readline()
        assert barrier.strip() == "BEFORE_NATIVE_REMOVAL", (
            barrier, process.stderr.read() if process.poll() is not None else process.poll(),
        )
        second = construct_generation(root, fixture, staged, trusted, SECOND, "forward-2")
        direct_health(second, directory.parent)
        new = publish(installation, SECOND)
        preserved = states(frontdoor.frontdoor._command_paths(directory))
        assert process.poll() is None
        stdout, stderr = process.communicate("\n", timeout=45)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["runtime_removed"] and not runtime(installation)[2].exists()
        assert pointer(root)[1] == new["publication"] and states(preserved) == preserved
        direct_health(second, directory.parent)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
