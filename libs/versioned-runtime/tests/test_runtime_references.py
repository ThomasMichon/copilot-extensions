"""Durable lifecycle references remain deletion barriers after owner exit."""

from __future__ import annotations

import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime as runtime


def case(tmp_path):
    root = tmp_path / "runtime"
    for name in ("old", "new", "spare"):
        (root / "versions" / name).mkdir(parents=True)
    return root


def replace(root, versions, *, expected=(), owner="fixture/component"):
    runtime._leases.replace_runtime_references(root, owner, versions, expected=list(expected))


def test_references_survive_process_exit_and_protect_all_cleanup_paths(tmp_path):
    root = case(tmp_path)
    code = """
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
import versioned_runtime as runtime
runtime._leases.replace_runtime_references(
    pathlib.Path(sys.argv[2]), 'fixture/component', ['old', 'new'], expected=[],
)
os._exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(LIBRARY), str(root)],
        timeout=15, capture_output=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 23, result.stderr
    assert runtime.gc(root) == ["spare"]
    with pytest.raises(RuntimeError, match="selected or in use"):
        runtime.remove_owned_candidate(root, "new", remove=lambda candidate: candidate.rmdir())
    with runtime.construction_lease(root, "new"):
        with pytest.raises(RuntimeError, match="selected or in use"):
            runtime.discard_failed_candidate(root, "new")
    replace(root, ["old"], expected=["old", "new"])
    assert runtime.gc(root) == ["new"]
    replace(root, [], expected=["old"])
    assert runtime.gc(root) == ["old"]


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length paths")
@pytest.mark.parametrize("long_root", [False, True])
def test_native_leases_share_ownership_under_a_long_ordinary_root(tmp_path, long_root):
    base = str(tmp_path)
    if base.startswith("\\\\?\\UNC\\"):
        base = "\\\\" + base[8:]
    elif base.startswith("\\\\?\\"):
        base = base[4:]
    root = Path(base) / "runtime"
    while (
        len(str(root)) <= 270 if long_root
        else len(str(root / ".construction-leases" / ("0" * 64 + ".lock"))) <= 260
    ):
        root /= "d" * 32
    value = str(root)
    native_root = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    native_root.mkdir(parents=True)
    try:
        with runtime.construction_lease(root, "Candidate", timeout=1):
            code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import versioned_runtime as runtime
try:
    with runtime.construction_lease(Path(sys.argv[2]), 'candidate', timeout=0):
        raise SystemExit(9)
except runtime.ConstructionTimeout:
    print('blocked')
"""
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", code, str(LIBRARY), str(native_root)],
                capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "blocked"
        with runtime.construction_lease(native_root, "candidate", timeout=1):
            pass
        with runtime._leases.reference_transaction_custody(root, timeout=1):
            pass
        observed = []

        def open_lock(directory, name):
            observed.append(directory)
            return runtime._leases._windows_private_lease_file(root, directory, name)

        publication = runtime._runtime_component("versioned_runtime_publication")
        with publication.leases(root, ["fixture"], open_lock=open_lock):
            assert observed == [root / ".publication-leases"]
        slot = native_root / "versions" / "candidate"
        slot.mkdir(parents=True)
        with runtime.retention_lease(root, "candidate", require_installed=False):
            pass
        assert len(list((native_root / ".construction-leases").glob("*.lock"))) == 1
    finally:
        shutil.rmtree(native_root)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length paths")
@pytest.mark.parametrize("operation", ["discard", "gc"])
@pytest.mark.parametrize("extended_root", [False, True])
def test_owned_cleanup_removes_long_descendants(tmp_path, operation, extended_root):
    root = tmp_path / "runtime"
    if extended_root:
        root = Path("\\\\?\\" + str(root))
    slot = root / "versions" / "candidate"
    slot.mkdir(parents=True)
    value = str(slot.resolve())
    native_slot = Path(value if value.startswith("\\\\?\\") else "\\\\?\\" + value)
    deep = native_slot / ("a" * 120) / ("b" * 120)
    deep.mkdir(parents=True)
    (deep / "payload").write_bytes(b"fixture")
    assert len(str(deep)) > 260
    try:
        if operation == "discard":
            with runtime.construction_lease(root, "candidate"):
                assert runtime.discard_failed_candidate(root, "candidate")
        else:
            assert runtime.gc(root, min_age_days=0) == ["candidate"]
        assert not slot.exists()
    finally:
        if native_slot.exists():
            shutil.rmtree(native_slot)


def test_update_is_owner_scoped_and_compare_bound(tmp_path):
    root = case(tmp_path)
    replace(root, ["old"])
    replace(root, ["new"], owner="other/component")
    before = (root / "runtime-references.json").read_bytes()
    with pytest.raises(RuntimeError, match="changed before publication"):
        replace(root, ["spare"], expected=["new"])
    assert (root / "runtime-references.json").read_bytes() == before
    replace(root, [], expected=["old"])
    assert runtime._leases.read_runtime_references(root) == {"other/component": ["new"]}
    assert set(runtime.gc(root)) == {"old", "spare"}


@pytest.mark.parametrize("versions", [["missing"], ["../old"], ["old."], [True], "old"])
def test_invalid_or_missing_generation_cannot_be_published(tmp_path, versions):
    root = case(tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        replace(root, versions)
    assert not (root / "runtime-references.json").exists()


@pytest.mark.parametrize("raw", [
    b"broken",
    b'{"schema":"copilot-extensions.runtime-references","version":1,"owners":{},"owners":{}}',
    b'{"schema":"copilot-extensions.runtime-references","version":true,"owners":{}}',
    b'{"schema":"copilot-extensions.runtime-references","version":1,"owners":{"a":["../new"]}}',
    b'{"schema":"copilot-extensions.runtime-references","version":1,"owners":{"a":[]}}',
    b"x" * 65537,
], ids=["broken", "duplicate", "wrong-version", "escape", "empty", "oversized"])
def test_unreadable_reference_evidence_refuses_deletion_and_overwrite(tmp_path, raw):
    root = case(tmp_path)
    path = root / "runtime-references.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        runtime.gc(root)
    with pytest.raises(ValueError):
        replace(root, ["old"])
    assert path.read_bytes() == raw
    assert set(runtime.list_versions(root)) == {"old", "new", "spare"}


def test_hardlinked_reference_evidence_is_not_authority(tmp_path):
    root = case(tmp_path)
    replace(root, ["old"])
    os.link(root / "runtime-references.json", root / "alias.json")
    with pytest.raises(ValueError, match="ordinary file"):
        runtime.gc(root)


def test_failed_reference_publication_preserves_previous_evidence(tmp_path, monkeypatch):
    root = case(tmp_path)
    replace(root, ["old"])
    before = json.loads((root / "runtime-references.json").read_bytes())
    original = Path.replace

    def fail(path, target):
        if Path(target).name == "runtime-references.json":
            raise OSError("injected reference publication failure")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="publication failure"):
        replace(root, ["new", "old"], expected=["old"])
    assert json.loads((root / "runtime-references.json").read_bytes()) == before
    assert not list(root.glob("runtime-references.json.*.tmp"))


def test_reference_transaction_survives_owner_exit_and_blocks_gc_until_commit(tmp_path):
    root = case(tmp_path)
    replace(root, ["old"])
    code = """
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
import versioned_runtime as runtime
runtime._leases.begin_runtime_reference_transaction(pathlib.Path(sys.argv[2]), 'fixture/component')
os._exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(LIBRARY), str(root)],
        timeout=15, capture_output=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 23, result.stderr
    with pytest.raises(ValueError):
        runtime.gc(root, min_age_days=0)
    assert set(runtime.list_versions(root)) == {"old", "new", "spare"}

    ticket = runtime._leases.begin_runtime_reference_transaction(root, "fixture/component")
    assert ticket["owners"] == {"fixture/component": ["old"]}
    runtime._leases.commit_runtime_reference_transaction(
        root, "fixture/component", ticket["token"],
        {"fixture/component": ["old", "new"], "fixture/component/cohort": ["spare"]},
        expected={"fixture/component": ["old"], "fixture/component/cohort": []},
    )
    assert runtime._leases.read_runtime_references(root) == {
        "fixture/component": ["new", "old"], "fixture/component/cohort": ["spare"],
    }
    assert runtime.gc(root, min_age_days=0) == []


def test_reference_transaction_refuses_foreign_owner_and_stale_token(tmp_path):
    root = case(tmp_path)
    ticket = runtime._leases.begin_runtime_reference_transaction(root, "fixture/component")
    path = root / "runtime-references.json"
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        runtime._leases.begin_runtime_reference_transaction(root, "other/component")
    with pytest.raises(RuntimeError):
        runtime._leases.commit_runtime_reference_transaction(
            root, "fixture/component", "0" * 64,
            {"fixture/component": ["old"]}, expected={"fixture/component": []},
        )
    with pytest.raises(ValueError):
        runtime._leases.commit_runtime_reference_transaction(
            root, "fixture/component", ticket["token"],
            {"other/component": ["old"]}, expected={"other/component": []},
        )
    with pytest.raises(ValueError):
        replace(root, ["old"])
    assert path.read_bytes() == before
    assert runtime._leases.begin_runtime_reference_transaction(root, "fixture/component") == ticket


def test_reference_transaction_commit_is_atomic_and_retryable(tmp_path, monkeypatch):
    root = case(tmp_path)
    replace(root, ["old"])
    ticket = runtime._leases.begin_runtime_reference_transaction(root, "fixture/component")
    path = root / "runtime-references.json"
    before = path.read_bytes()
    original = Path.replace

    def fail(source, target):
        if Path(target) == path:
            raise OSError("injected reference publication failure")
        return original(source, target)

    updates = {"fixture/component": ["old", "new"], "fixture/component/cohort": ["spare"]}
    expected = {"fixture/component": ["old"], "fixture/component/cohort": []}
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="publication failure"):
            runtime._leases.commit_runtime_reference_transaction(
                root, "fixture/component", ticket["token"], updates, expected=expected,
            )
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        runtime.gc(root, min_age_days=0)
    assert set(runtime.list_versions(root)) == {"old", "new", "spare"}
    runtime._leases.commit_runtime_reference_transaction(
        root, "fixture/component", ticket["token"], updates, expected=expected,
    )
    assert runtime._leases.read_runtime_references(root) == {
        "fixture/component": ["new", "old"], "fixture/component/cohort": ["spare"],
    }


def test_reference_transaction_preserves_fence_on_compare_or_target_failure(tmp_path):
    root = case(tmp_path)
    replace(root, ["old"])
    ticket = runtime._leases.begin_runtime_reference_transaction(root, "fixture/component")
    path = root / "runtime-references.json"
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="changed before publication"):
        runtime._leases.commit_runtime_reference_transaction(
            root, "fixture/component", ticket["token"],
            {"fixture/component": ["new"]}, expected={"fixture/component": ["spare"]},
        )
    with pytest.raises(FileNotFoundError):
        runtime._leases.commit_runtime_reference_transaction(
            root, "fixture/component", ticket["token"],
            {"fixture/component": ["missing"]}, expected={"fixture/component": ["old"]},
        )
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        runtime.gc(root, min_age_days=0)


def test_reference_transactions_wait_for_other_owner_to_settle(tmp_path):
    root = case(tmp_path)
    ticket = runtime._leases.begin_runtime_reference_transaction(root, "fixture/first")
    started = threading.Event()

    def begin_other():
        started.set()
        return runtime._leases.begin_runtime_reference_transaction(
            root, "fixture/second", timeout=5,
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(begin_other)
        assert started.wait(2)
        assert not future.done()
        runtime._leases.commit_runtime_reference_transaction(
            root, "fixture/first", ticket["token"],
            {"fixture/first": ["old"]}, expected={"fixture/first": []},
        )
        other = future.result(timeout=5)
    runtime._leases.commit_runtime_reference_transaction(
        root, "fixture/second", other["token"],
        {"fixture/second": ["new"]}, expected={"fixture/second": []},
    )
    assert runtime._leases.read_runtime_references(root) == {
        "fixture/first": ["old"], "fixture/second": ["new"],
    }


def test_reference_transaction_custody_serializes_post_commit_work(tmp_path):
    root = case(tmp_path)
    started = threading.Event()

    def acquire():
        started.set()
        with runtime._leases.reference_transaction_custody(root, timeout=5):
            return True

    with ThreadPoolExecutor(max_workers=1) as pool:
        with runtime._leases.reference_transaction_custody(root):
            future = pool.submit(acquire)
            assert started.wait(2)
            assert not future.done()
        assert future.result(timeout=5) is True


def test_foreign_abandoned_transaction_recovers_all_slots_before_continuing(tmp_path):
    root = tmp_path / "runtime"
    names = [f"slot-{number:03d}" for number in range(68)]
    for name in names:
        (root / "versions" / name).mkdir(parents=True)
    runtime._leases.begin_runtime_reference_transaction(root, "fixture/first")

    with runtime._leases.reference_transaction_custody(root):
        ticket = runtime._leases.begin_runtime_reference_transaction(
            root, "fixture/second", recover_foreign=True,
        )
        prefix = runtime._leases.reference_recovery_prefix("fixture/first")
        recovered = {
            key: values for key, values in ticket["owners"].items() if key.startswith(prefix)
        }
        assert len(recovered) == 2
        assert set().union(*recovered.values()) == set(names)
        runtime._leases.commit_runtime_reference_transaction(
            root, "fixture/second", ticket["token"],
            {"fixture/second": [names[0]]}, expected={"fixture/second": []},
        )
    assert runtime.gc(root, min_age_days=0) == []


def test_fresh_root_fence_can_be_recovered_by_another_owner(tmp_path):
    root = tmp_path / "fresh"
    code = """
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
import versioned_runtime as runtime
runtime._leases.begin_runtime_reference_transaction(pathlib.Path(sys.argv[2]), 'fixture/first')
os._exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(LIBRARY), str(root)],
        timeout=15, capture_output=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 23, result.stderr
    assert (root / "versions").is_dir()
    with runtime._leases.reference_transaction_custody(root):
        ticket = runtime._leases.begin_runtime_reference_transaction(
            root, "fixture/second", recover_foreign=True,
        )
        assert ticket["owners"] == {}
        runtime._leases.commit_runtime_reference_transaction(
            root, "fixture/second", ticket["token"],
            {"fixture/second": []}, expected={"fixture/second": []},
        )
    assert runtime._leases.read_runtime_references(root) == {}


def test_recovery_does_not_recreate_a_missing_fenced_inventory(tmp_path):
    root = case(tmp_path)
    runtime._leases.begin_runtime_reference_transaction(root, "fixture/first")
    shutil.rmtree(root / "versions")
    with runtime._leases.reference_transaction_custody(root), pytest.raises(FileNotFoundError):
        runtime._leases.begin_runtime_reference_transaction(
            root, "fixture/second", recover_foreign=True,
        )
    assert not (root / "versions").exists()
    with pytest.raises(ValueError):
        runtime._leases.read_runtime_references(root)


def test_fresh_fence_refuses_a_non_directory_inventory(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "versions").write_text("foreign", encoding="utf-8")
    with pytest.raises(ValueError, match="ordinary directory"):
        runtime._leases.begin_runtime_reference_transaction(root, "fixture/first")
    assert not (root / "runtime-references.json").exists()


def test_foreign_recovery_failure_keeps_fence_and_existing_references(tmp_path, monkeypatch):
    root = case(tmp_path)
    replace(root, ["old"], owner="unrelated/component")
    runtime._leases.begin_runtime_reference_transaction(root, "fixture/first")
    path = root / "runtime-references.json"
    before = path.read_bytes()
    original = Path.replace

    def fail(source, target):
        if Path(target) == path:
            raise OSError("injected recovery failure")
        return original(source, target)

    with runtime._leases.reference_transaction_custody(root), monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="recovery failure"):
            runtime._leases.begin_runtime_reference_transaction(
                root, "fixture/second", recover_foreign=True,
            )
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        runtime.gc(root, min_age_days=0)

    with runtime._leases.reference_transaction_custody(root):
        ticket = runtime._leases.begin_runtime_reference_transaction(
            root, "fixture/second", recover_foreign=True,
        )
        assert ticket["owners"]["unrelated/component"] == ["old"]
        runtime._leases.commit_runtime_reference_transaction(
            root, "fixture/second", ticket["token"],
            {"fixture/second": ["new"]}, expected={"fixture/second": []},
        )
    assert runtime.gc(root, min_age_days=0) == []


def test_foreign_recovery_refuses_an_ambiguous_inventory(tmp_path):
    root = case(tmp_path)
    (root / "versions" / "not-a-directory").write_text("fixture", encoding="utf-8")
    runtime._leases.begin_runtime_reference_transaction(root, "fixture/first")
    with runtime._leases.reference_transaction_custody(root), pytest.raises(ValueError):
        runtime._leases.begin_runtime_reference_transaction(
            root, "fixture/second", recover_foreign=True,
        )
    with pytest.raises(ValueError):
        runtime._leases.read_runtime_references(root)


def test_foreign_recovery_does_not_retry_forever_after_ownership_changes(tmp_path, monkeypatch):
    root = case(tmp_path)
    runtime._leases.begin_runtime_reference_transaction(root, "fixture/first")
    attempts = []

    def unchanged(_root):
        attempts.append(None)
        if len(attempts) > 2:
            raise AssertionError("recovery retried without a bound")

    monkeypatch.setattr(runtime._leases, "_recover_runtime_reference_transaction", unchanged)
    with runtime._leases.reference_transaction_custody(root), pytest.raises(RuntimeError):
        runtime._leases.begin_runtime_reference_transaction(
            root, "fixture/second", timeout=0, recover_foreign=True,
        )
    assert len(attempts) == 1


def test_reference_transaction_commits_multiple_bounded_groups_before_owner_exit(tmp_path):
    root = tmp_path / "runtime"
    names = [f"slot-{number:03d}" for number in range(68)]
    for name in names:
        (root / "versions" / name).mkdir(parents=True)
    code = """
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
import versioned_runtime as runtime
root = pathlib.Path(sys.argv[2])
ticket = runtime._leases.begin_runtime_reference_transaction(root, 'fixture/component')
names = [f'slot-{number:03d}' for number in range(68)]
runtime._leases.commit_runtime_reference_transaction(
    root, 'fixture/component', ticket['token'],
    {'fixture/component/first': names[:64], 'fixture/component/second': names[64:]},
    expected={'fixture/component/first': [], 'fixture/component/second': []},
)
os._exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(LIBRARY), str(root)],
        timeout=30, capture_output=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 23, result.stderr
    owners = runtime._leases.read_runtime_references(root)
    assert set().union(*owners.values()) == set(names)
    assert runtime.gc(root, min_age_days=0) == []


def test_publication_holds_retention_before_selector_custody(tmp_path, monkeypatch):
    root = case(tmp_path)
    publish = runtime._leases._publication_lease
    observed = []

    def check(root, **kwargs):
        with runtime._cleanup_lease(root, "new") as admitted:
            observed.append(admitted)
        return publish(root, **kwargs)

    monkeypatch.setattr(runtime._leases, "_publication_lease", check)
    replace(root, ["new"])
    assert observed == [False]


@pytest.mark.parametrize("exists", [False, True])
def test_predecessor_reference_pin_does_not_require_an_installed_directory(tmp_path, exists):
    root = tmp_path / "runtime"
    slot = root / "versions" / "1.2.3"
    slot.parent.mkdir(parents=True)
    if exists:
        slot.write_bytes(b"preserve invalid predecessor")
    with pytest.raises(FileNotFoundError, match="ordinary runtime slot not installed"):
        with runtime.retention_lease(root, "1.2.3"):
            pytest.fail("ordinary retention admitted an invalid runtime")

    with runtime.retention_lease(root, "1.2.3", require_installed=False):
        with runtime._cleanup_lease(root, "1.2.3") as admitted:
            assert not admitted
        if exists:
            assert slot.read_bytes() == b"preserve invalid predecessor"
        else:
            assert not slot.exists()

    with pytest.raises(FileNotFoundError, match="ordinary runtime slot not installed"):
        with runtime.retention_lease(root, "1.2.3"):
            pytest.fail("reference preservation changed ordinary runtime admission")


def test_predecessor_pin_cannot_admit_an_invalid_completed_candidate(tmp_path):
    root = tmp_path / "runtime"
    slot = root / "versions" / "1.2.3"
    slot.parent.mkdir(parents=True)
    slot.write_bytes(b"preserve invalid predecessor")

    with pytest.raises(RuntimeError, match="runtime retention admission refused"):
        with runtime.retention_lease(
            root, "1.2.3", payload_hash="a" * 64, require_installed=False,
        ):
            pytest.fail("predecessor preservation qualified a candidate")
    assert slot.read_bytes() == b"preserve invalid predecessor"


@pytest.mark.parametrize("selector", ["current-version", "last-known-good"])
def test_undecodable_old_selector_does_not_veto_ready_publication(tmp_path, selector):
    root = case(tmp_path)
    slot = runtime.slot(root, "2.0")
    (slot / "payload").write_text("incoming")
    runtime.mark_complete(root, "2.0", payload_hash="same")
    (root / selector).write_bytes(b"\xff\xfe")
    old_payload = root / "versions" / "old" / "preserve"
    old_payload.write_bytes(b"old state")

    runtime.activate(root, "2.0", link_free=True)

    assert runtime.current_version(root) == "2.0"
    assert runtime.read_last_known_good(root) == "2.0"
    assert old_payload.read_bytes() == b"old state"
