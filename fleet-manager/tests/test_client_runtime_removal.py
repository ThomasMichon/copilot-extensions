"""Completed removal retains external ownership across partial native deletion."""

import copy
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import traceback

import pytest

from fleet_manager import client_construction as construction, client_activation as activation
from fleet_manager import client_runtime_removal as removal, client_frontdoor as frontdoor
from fleet_manager.client_deactivation import deactivate_client_runtime
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate, completed_candidate
from test_client_admission import database_bytes, identity, retained, root_at
from test_client_construction import admit, construct, signed_wheel as signed_wheel  # noqa: F401
from test_client_distribution import FLAGS
from test_client_frontdoor import states


@pytest.fixture
def owned(tmp_path, primitive, signed_wheel, monkeypatch):
    root = root_at(tmp_path)
    fixture = signed_wheel

    def prepare(slot):
        (slot / "a-first").write_bytes(b"first")
        (slot / "b-second").write_bytes(b"second")
        (slot / "nested").mkdir()
        (slot / "nested" / "child").write_bytes(b"nested")
        if os.name != "nt":
            (slot / "internal-link").symlink_to("nested", target_is_directory=True)

    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        namespace, slot = completed_candidate(root, fixture, staged, trusted, primitive, prepare=prepare)
        monkeypatch.setattr(construction, "_health", lambda *_args: {"unit_health_stub": True})
        yield root, fixture, staged, trusted, namespace, slot


def remove(owned, **kwargs):
    root, _fixture, _staged, _trusted, _namespace, slot = owned
    try:
        return removal.remove_client_runtime(
            root, kwargs.pop("identity", identity()), candidate=kwargs.pop("candidate", slot.name), **kwargs,
        )
    except PlanError as error:
        if error.__context__ is not None:
            print("CLIENT_REMOVAL_CAUSE " + repr(error.__context__), flush=True)
            traceback.print_exception(error.__context__)
        raise


def remaining(owned):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    if slot.exists() or os.name == "nt":
        return slot
    value = construction._read(namespace / construction.REMOVING)
    return namespace / removal.HISTORY / value["removal"] / "claims" / removal._claim_key([])


def test_owned_completed_removal_exact_noop_and_preservation(owned, monkeypatch):
    root, _fixture, _staged, _trusted, namespace, slot = owned
    custom = root / "custom-config"
    custom.write_bytes(b"settings")
    other = slot.with_name("other-retained-runtime")
    other.mkdir()
    (other / "rollback").write_bytes(b"retained rollback")
    preserved = states([custom, other / "rollback", namespace / construction.OWNER,
                        namespace / "client-inputs" / (slot.name + ".json")])
    floor = database_bytes(root)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("removal cannot execute the candidate or authenticate a release")

    monkeypatch.setattr(construction, "_health", forbidden)
    monkeypatch.setattr(construction, "_run", forbidden)
    for reused in (False, True):
        result = remove(owned)
        assert result["runtime_removed"] and result["reused"] is reused
        assert result["mutations_performed"] is not reused
        assert not slot.exists() and not (namespace / construction.REMOVING).exists()
        for flag in ("active", "frontdoor_registered", "installation_retired",
                     "path_modified", "live_readiness_verified"):
            assert result[flag] is False
        assert states(preserved) == preserved and database_bytes(root) == floor
    history = list((namespace / removal.HISTORY).iterdir())
    assert len(history) == 1
    assert construction._read(history[0] / "intent.json")["tree"][0]
    assert (history[0] / "outcome.json").is_file()


@pytest.mark.parametrize("name", [
    "current", *activation.SELECTORS, activation.ACTIVE, activation.PENDING, activation.REMOVING,
    frontdoor.REGISTERED, frontdoor.PENDING, frontdoor.REMOVING,
])
def test_every_unsettled_selection_or_registration_blocks_removal(owned, name):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    target = namespace / name
    target.write_bytes(b"present evidence, not permission to reclaim\n")
    before = states(path for path in slot.rglob("*") if path.is_file())
    with pytest.raises(PlanError):
        remove(owned)
    assert states(before) == before and target.is_file()
    assert not (namespace / construction.REMOVING).exists()


@pytest.mark.parametrize("custody", ["retention", "construction", "live-process"])
def test_canonical_custody_blocks_before_a_removal_intent(owned, primitive, monkeypatch, custody):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    if custody == "live-process":
        monkeypatch.setattr(primitive, "_versions_with_live_process", lambda _root: {slot.name})
        with pytest.raises(PlanError):
            remove(owned)
    else:
        lease = getattr(primitive, custody + "_lease")
        with lease(namespace, slot.name):
            with pytest.raises(PlanError):
                remove(owned)
    assert (slot / "candidate-bytes").is_file()
    assert not (namespace / construction.REMOVING).exists()


@pytest.mark.parametrize("change", ["bytes", "completion", "incomplete", "input", "identity"])
def test_initial_foreign_or_incomplete_candidate_preserved(owned, change):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    options = {}
    if change == "bytes":
        (slot / "candidate-bytes").write_bytes(b"tampered")
    elif change == "completion":
        (slot / construction.COMPLETION).write_bytes(b"{}")
    elif change == "incomplete":
        (slot / ".install-complete.json").unlink()
    elif change == "input":
        (namespace / "client-inputs" / (slot.name + ".json")).write_bytes(b"{}")
    else:
        wrong = copy.deepcopy(identity())
        wrong["installation"] = "another-installation"
        options["identity"] = wrong
    before = states(path for path in slot.rglob("*") if path.is_file())
    with pytest.raises(PlanError):
        remove(owned, **options)
    assert states(before) == before and not (namespace / construction.REMOVING).exists()


def interrupt(owned, monkeypatch, *, crash=None):
    _root, _fixture, _staged, _trusted, _namespace, slot = owned
    if os.name == "nt":
        # Fail before opening the second file; the first deletion is already durable.
        native = removal._windows_node

        def fail(path, *args, **kwargs):
            if path == slot / "b-second" and kwargs.get("remove"):
                if crash:
                    crash()
                raise OSError("injected partial removal")
            return native(path, *args, **kwargs)

        monkeypatch.setattr(removal, "_windows_node", fail)
    else:
        native = removal._posix_claimed_node

        def fail(parent, name, publication, expected, claims, path, visited, **kwargs):
            if path == ["b-second"] and kwargs.get("remove"):
                if crash:
                    crash()
                raise OSError("injected partial removal")
            return native(parent, name, publication, expected, claims, path, visited, **kwargs)

        monkeypatch.setattr(removal, "_posix_claimed_node", fail)


def test_partial_native_delete_retries_and_blocks_republication(owned, primitive, monkeypatch):
    root, fixture, staged, trusted, namespace, slot = owned
    floor = database_bytes(root)
    with monkeypatch.context() as patch:
        interrupt(owned, patch)
        with pytest.raises(PlanError, match="outcome is unknown"):
            remove(owned)
    assert not (remaining(owned) / "a-first").exists() and (remaining(owned) / "b-second").exists()
    assert (namespace / construction.REMOVING).is_file()
    for operation in (
        lambda: construct(root, fixture, staged, trusted),
        lambda: activate(root, fixture, staged, trusted),
    ):
        with pytest.raises(PlanError, match="runtime removal is unsettled"):
            operation()
    assert remove(owned)["runtime_removed"] and not slot.exists()
    assert database_bytes(root) == floor
    assert primitive.gc(namespace) == []


@pytest.mark.parametrize("change", ["same-byte-file", "changed-file", "new-file", "directory", "root"])
def test_partial_retry_preserves_replacements_before_any_more_effects(owned, monkeypatch, change):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    with monkeypatch.context() as patch:
        interrupt(owned, patch)
        with pytest.raises(PlanError):
            remove(owned)
    slot = remaining(owned)
    if change == "same-byte-file":
        peer = slot / "replacement"
        peer.write_bytes((slot / "b-second").read_bytes())
        os.replace(peer, slot / "b-second")
    elif change == "changed-file":
        (slot / "b-second").write_bytes(b"changed")
    elif change == "new-file":
        (slot / "new-custom").write_bytes(b"preserved")
    elif change == "directory":
        (slot / "nested").rename(slot / "retained-original")
        (slot / "nested").mkdir()
        (slot / "nested" / "child").write_bytes(b"foreign")
    else:
        slot.rename(slot.with_name("retained-original"))
        slot.mkdir()
        (slot / "foreign").write_bytes(b"preserved")
    before = states(path for path in slot.rglob("*") if path.is_file())
    with pytest.raises(PlanError, match="preserves"):
        remove(owned)
    assert states(before) == before and (namespace / construction.REMOVING).is_file()


@pytest.mark.parametrize("candidate", ["../escape", "client-1-abc", "", "dev"])
def test_exact_candidate_required_before_any_effects(owned, candidate):
    with pytest.raises(PlanError, match="exact constructed candidate"):
        remove(owned, candidate=candidate)


def test_absent_without_a_completed_outcome_is_not_success(owned):
    with pytest.raises(PlanError):
        remove(owned, candidate="client-100-" + "a" * 64)


def test_reconstruction_invalidates_only_current_outcome_before_new_slot_creation(
    owned, primitive, tmp_path, monkeypatch,
):
    root, fixture, staged, trusted, namespace, slot = owned
    remove(owned)
    pointer = namespace / removal.OUTCOMES / (slot.name + ".json")
    history = states(path for path in (namespace / removal.HISTORY).rglob("*") if path.is_file())
    assert pointer.is_file()
    original = primitive.slot
    checked = []

    def new_generation(*args, **kwargs):
        assert not pointer.exists()
        checked.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(primitive, "slot", new_generation)
    rebuilt = construct(root, fixture, staged, trusted)
    assert checked and not rebuilt["reused"] and not pointer.exists()
    assert states(history) == history
    slot.rename(tmp_path / "unexplained-disappearance")
    with pytest.raises(PlanError):
        remove(owned)
    assert states(history) == history and not pointer.exists()


def test_canonical_removal_has_no_unowned_or_incomplete_delete_fallback(owned, primitive):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    with pytest.raises(ValueError, match="installation owner"):
        primitive.remove_owned_candidate(namespace, slot.name, remove=None)
    with pytest.raises(RuntimeError, match="did not settle"):
        primitive.remove_owned_candidate(namespace, slot.name, remove=lambda _slot: None)
    assert (slot / ".install-complete.json").is_file()
    assert not (namespace / construction.REMOVING).exists()


def test_paged_physical_proof_preserves_json_depth_and_collection_bounds(primitive):
    def directory(number):
        return {"kind": "directory", "identity": [1, number],
                "mode": stat.S_IFDIR | 0o700, "children": {}}

    tree = directory(1)
    parent = tree
    for index in range(20):
        child = directory(index + 2)
        parent["children"][f"level-{index}"] = child
        parent = child
    parent["children"] = {f"member-{index}": directory(index + 100) for index in range(200)}
    pages = removal._flatten(tree)
    assert len(pages) == 2 and all(len(page) <= 128 for page in pages)
    encoded = removal.canonical({"tree": pages})
    publication = primitive._runtime_component("versioned_runtime_publication")
    assert removal._inflate(construction.decode(encoded)["tree"], publication) == tree


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative symbolic link contract")
def test_partial_retry_never_follows_an_outside_replacement_link(owned, tmp_path, monkeypatch):
    _root, _fixture, _staged, _trusted, _namespace, slot = owned
    with monkeypatch.context() as patch:
        interrupt(owned, patch)
        with pytest.raises(PlanError):
            remove(owned)
    slot = remaining(owned)
    outside = tmp_path / "foreign"
    outside.mkdir()
    sentinel = outside / "child"
    sentinel.write_bytes(b"outside custom data")
    (slot / "nested").rename(slot / "retained-original")
    (slot / "nested").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PlanError, match="preserves"):
        remove(owned)
    assert sentinel.read_bytes() == b"outside custom data"


def test_owned_activation_withdrawal_then_exact_removal_preserves_activation_history(owned):
    root, fixture, staged, trusted, namespace, slot = owned
    activate(root, fixture, staged, trusted)
    history = states(path for path in (namespace / activation.HISTORY).rglob("*") if path.is_file())
    with pytest.raises(PlanError):
        remove(owned)
    deactivate_client_runtime(root, identity())
    assert remove(owned)["runtime_removed"]
    assert states(history) == history and not slot.exists()


@pytest.mark.parametrize("boundary", ["history-outcome", "outcome-pointer", "ack", "temporary"])
def test_deleted_root_and_interrupted_outcome_settlement_retry(owned, monkeypatch, boundary):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    original_write, unlink = removal._write, Path.unlink

    def fail_write(path, value):
        selected = (
            path.name == "outcome.json" if boundary in ("history-outcome", "temporary")
            else path.parent.name == removal.OUTCOMES
        )
        if selected:
            if boundary == "temporary":
                temporary = path.with_name("." + path.name + ".write-abandoned")
                temporary.write_bytes(removal.canonical(value))
                temporary.chmod(0o600)
            raise OSError("injected durable outcome interruption")
        return original_write(path, value)

    def fail_ack(path, *args, **kwargs):
        if path == namespace / construction.REMOVING:
            raise OSError("injected removal acknowledgement interruption")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if boundary == "ack":
            patch.setattr(Path, "unlink", fail_ack)
        else:
            patch.setattr(removal, "_write", fail_write)
        with pytest.raises(PlanError, match="outcome is unknown"):
            remove(owned)
    assert not slot.exists() and (namespace / construction.REMOVING).is_file()
    assert remove(owned)["runtime_removed"]
    assert remove(owned)["reused"]


@pytest.mark.parametrize("partial", [b"", b'{"schema":'])
def test_empty_or_truncated_native_outcome_write_does_not_poison_retry(owned, monkeypatch, partial):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    original_open, original_write = os.open, os.write
    writing = set()

    def open_attempt(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if Path(path).name.startswith(".outcome.json.write-"):
            writing.add(descriptor)
        return descriptor

    def write_partial(descriptor, content):
        if descriptor in writing:
            if partial:
                original_write(descriptor, partial)
            raise OSError("injected incomplete outcome write")
        return original_write(descriptor, content)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", open_attempt)
        patch.setattr(os, "write", write_partial)
        with pytest.raises(PlanError, match="outcome is unknown"):
            remove(owned)
    assert not slot.exists() and (namespace / construction.REMOVING).is_file()
    abandoned = list((namespace / removal.HISTORY).glob("*/.outcome.json.write-*"))
    assert len(abandoned) == 1 and abandoned[0].read_bytes() == partial
    assert remove(owned)["runtime_removed"]
    assert abandoned[0].read_bytes() == partial and remove(owned)["reused"]


def test_unpublished_initial_marker_preserves_old_attempt_and_allows_a_new_owned_attempt(owned, monkeypatch):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    original = os.replace

    def interrupt_marker(source, target, *args, **kwargs):
        if target == namespace / construction.REMOVING:
            raise OSError("injected failure before initial marker rename")
        return original(source, target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupt_marker)
        with pytest.raises(PlanError):
            remove(owned)
    abandoned = list(namespace.glob("." + construction.REMOVING + ".write-*"))
    assert len(abandoned) == 1 and slot.is_dir()
    assert not (namespace / construction.REMOVING).exists()
    before = abandoned[0].read_bytes()
    assert remove(owned)["runtime_removed"]
    assert abandoned[0].read_bytes() == before
    assert len(list((namespace / removal.HISTORY).iterdir())) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX claim-before-validation deletion contract")
@pytest.mark.parametrize("kind", ["file", "link", "directory"])
def test_replacement_during_final_validation_survives_in_the_original_namespace(
    owned, primitive, monkeypatch, kind,
):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    publication = primitive._runtime_component("versioned_runtime_publication")
    logical = {"file": "b-second", "link": "internal-link", "directory": "nested"}[kind]
    key = removal._claim_key([logical])
    injected = []

    def replace_original():
        if injected:
            return
        original = remaining(owned) / logical
        if kind == "directory":
            original.mkdir()
            sentinel = original / "foreign"
        else:
            sentinel = original
        sentinel.write_bytes(b"replacement must survive")
        injected.append(sentinel)

    state_fd, readlink, open_entry = publication.state_fd, os.readlink, os.open

    def hash_then_replace(descriptor, **kwargs):
        result = state_fd(descriptor, **kwargs)
        path = Path(readlink(f"/proc/self/fd/{descriptor}"))
        if path.name == key and path.parent.name == "claims":
            replace_original()
        return result

    def readlink_then_replace(name, *args, **kwargs):
        result = readlink(name, *args, **kwargs)
        if name == key and "dir_fd" in kwargs:
            replace_original()
        return result

    def open_then_replace(name, *args, **kwargs):
        descriptor = open_entry(name, *args, **kwargs)
        if name == key and "dir_fd" in kwargs:
            replace_original()
        return descriptor

    with monkeypatch.context() as patch:
        if kind == "file":
            patch.setattr(publication, "state_fd", hash_then_replace)
        elif kind == "link":
            patch.setattr(os, "readlink", readlink_then_replace)
        else:
            patch.setattr(os, "open", open_then_replace)
        with pytest.raises(PlanError):
            remove(owned)
    assert len(injected) == 1 and injected[0].read_bytes() == b"replacement must survive"
    assert (namespace / construction.REMOVING).is_file()
    with pytest.raises(PlanError):
        remove(owned)
    assert injected[0].read_bytes() == b"replacement must survive"


def test_corrupt_retained_tree_is_not_resnapshot_permission(owned, monkeypatch):
    _root, _fixture, _staged, _trusted, namespace, slot = owned
    with monkeypatch.context() as patch:
        interrupt(owned, patch)
        with pytest.raises(PlanError):
            remove(owned)
    value = construction._read(namespace / construction.REMOVING)
    item = next(item for page in value["tree"] for item in page if item["path"] == ["b-second"])
    item["entry"] = None
    for path in (
        namespace / construction.REMOVING,
        namespace / removal.HISTORY / value["removal"] / "intent.json",
    ):
        construction._write(path, value)
    before = states(path for path in remaining(owned).rglob("*") if path.is_file())
    with pytest.raises(PlanError, match="physical evidence"):
        remove(owned)
    assert states(before) == before


@pytest.mark.timeout(45)
def test_process_death_during_tree_deletion_retries_under_a_distinct_installed_owner(owned, tmp_path):
    root, _fixture, _staged, _trusted, namespace, slot = owned
    program = """
import json,os,sys
from pathlib import Path
from fleet_manager import client_runtime_removal as removal
request=json.loads(sys.stdin.readline())
if request["interrupt"]:
    if os.name=="nt":
        native=removal._windows_node
        def die(path,*args,**kwargs):
            if path.name=="b-second" and kwargs.get("remove"):
                os._exit(47)
            return native(path,*args,**kwargs)
        removal._windows_node=die
    else:
        native=removal._posix_claimed_node
        def die(parent,name,publication,expected,claims,path,visited,**kwargs):
            if path==["b-second"] and kwargs.get("remove"):
                os._exit(47)
            return native(parent,name,publication,expected,claims,path,visited,**kwargs)
        removal._posix_claimed_node=die
print(json.dumps(removal.remove_client_runtime(
    Path(request["root"]),request["identity"],candidate=request["candidate"],
)),flush=True)
"""
    floor = database_bytes(root)
    for interrupted in (True, False):
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program], cwd=tmp_path,
            input=json.dumps({"root": str(root), "identity": identity(), "candidate": slot.name,
                              "interrupt": interrupted}) + "\n",
            capture_output=True, text=True, timeout=20, **FLAGS,
        )
        if interrupted:
            assert result.returncode == 47, result.stderr
            assert (namespace / construction.REMOVING).is_file()
            assert not (remaining(owned) / "a-first").exists()
            assert (remaining(owned) / "b-second").exists()
        else:
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["runtime_removed"] and not slot.exists()
    assert database_bytes(root) == floor


@pytest.mark.timeout(45)
def test_installed_owner_without_optional_dependencies_can_remove_runtime(owned, wheel, tmp_path):
    root, _fixture, _staged, _trusted, _namespace, slot = owned
    package = tmp_path / "dependency-free-owner"
    uv = shutil.which("uv")
    assert uv, "no-dependency owner installation requires the runner's uv"
    installed = subprocess.run(
        [uv, "pip", "install", "--python", sys.executable, "--target", str(package),
         "--no-index", "--no-deps", "--link-mode", "copy", str(wheel[0])],
        capture_output=True, text=True, timeout=20, **FLAGS,
    )
    assert installed.returncode == 0, installed.stderr
    inputs = tmp_path / "identity.json"
    inputs.write_text(json.dumps(identity()), encoding="utf-8")
    program = """
import importlib.util,sys
sys.path.insert(0,sys.argv.pop(1))
assert importlib.util.find_spec("cryptography") is None
assert importlib.util.find_spec("pip") is None
from importlib.metadata import distribution
entry=next(value for value in distribution("fleet-manager").entry_points
           if value.group=="console_scripts" and value.name=="fleet-manager")
raise SystemExit(entry.load()())
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program, str(package),
         "client-state", "remove-runtime", "--root", str(root), "--identity", str(inputs),
         "--candidate", slot.name],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, **FLAGS,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime_removed"] and not slot.exists()
