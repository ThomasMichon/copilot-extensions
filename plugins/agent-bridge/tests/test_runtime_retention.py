"""Native-process candidate/rollback retention and cleanup admission."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "versioned_runtime.py"
spec = importlib.util.spec_from_file_location("retention_runtime", SOURCE)
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)


def child(root, body):
    script = (
        "import importlib.util, pathlib\n"
        f"s=importlib.util.spec_from_file_location('vr', {str(SOURCE)!r})\n"
        "vr=importlib.util.module_from_spec(s); s.loader.exec_module(vr)\n"
        f"root=pathlib.Path({str(root)!r})\n" + body
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.fixture
def processes():
    owned = []
    yield owned
    for process in owned:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def start_owner(root, processes, body):
    process = child(root, body + "\n print('ready', flush=True)\n input()\n")
    processes.append(process)
    assert process.stdout.readline().strip() == "ready"
    return process


def install(root, version="1.0", *, complete=False):
    slot = vr.slot(root, version)
    (slot / "payload").write_text("immutable")
    if complete:
        vr.mark_complete(root, version, payload_hash="same")
    return slot


def cleanup(root, kind):
    if kind == "gc":
        return vr.gc(root)
    if kind == "toss":
        return vr.toss_incomplete(root)
    return vr.slot(root, "1.0", clean_incomplete=True)


@pytest.mark.guard
@pytest.mark.parametrize("kind", ["gc", "toss", "slot"])
def test_retention_blocks_every_cleanup_until_owner_releases(tmp_path, processes, kind):
    slot = install(tmp_path)
    before = slot.stat().st_mtime_ns
    process = start_owner(
        tmp_path, processes, "with vr.retention_lease(root, '1.0'):",
    )
    if kind == "slot":
        with pytest.raises(RuntimeError, match="retention owner"):
            cleanup(tmp_path, kind)
    else:
        assert cleanup(tmp_path, kind) == []
    assert (slot / "payload").read_text() == "immutable"
    assert slot.stat().st_mtime_ns == before
    stdout, stderr = process.communicate(input="\n", timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    result = cleanup(tmp_path, kind)
    if kind == "slot":
        assert result.is_dir()
        assert not (slot / "payload").exists()
    else:
        assert result == ["1.0"]
        assert not slot.exists()


@pytest.mark.parametrize("kind", ["gc", "toss", "slot"])
def test_construction_blocks_cleanup_and_death_releases(tmp_path, processes, kind):
    slot = install(tmp_path)
    process = start_owner(
        tmp_path, processes, "with vr.construction_lease(root, '1.0'):",
    )
    if kind == "slot":
        with pytest.raises(RuntimeError, match="construction"):
            cleanup(tmp_path, kind)
    else:
        assert cleanup(tmp_path, kind) == []
    assert (slot / "payload").exists()
    process.kill()
    process.communicate(timeout=10)
    cleanup(tmp_path, kind)
    assert not (slot / "payload").exists()


def test_shared_reuse_holders_coexist_and_last_owner_gates_gc(tmp_path, processes):
    slot = install(tmp_path, complete=True)
    owners = [
        start_owner(tmp_path, processes,
                    "with vr.retention_lease(root, '1.0', payload_hash='same', timeout=0):")
        for _ in range(2)
    ]
    assert vr.gc(tmp_path) == []
    owners[0].communicate(input="\n", timeout=10)
    assert vr.gc(tmp_path) == []
    owners[1].kill()
    owners[1].communicate(timeout=10)
    assert vr.gc(tmp_path) == ["1.0"]
    assert not slot.exists()
    assert list((tmp_path / ".retention-leases").glob("*.lock"))


@pytest.mark.guard
def test_construction_handoff_has_no_unprotected_candidate(tmp_path, processes):
    process = child(
        tmp_path,
        "from contextlib import ExitStack\n"
        "with ExitStack() as pins:\n"
        " with vr.construction_lease(root, '1.0'):\n"
        "  slot=vr.slot(root, '1.0', clean_incomplete=True)\n"
        "  (slot/'payload').write_text('immutable')\n"
        "  vr.mark_complete(root, '1.0', payload_hash='same')\n"
        "  pins.enter_context(vr.retention_lease(root, '1.0', payload_hash='same'))\n"
        " print('ready', flush=True)\n"
        " input()\n",
    )
    processes.append(process)
    assert process.stdout.readline().strip() == "ready"
    with vr.construction_lease(tmp_path, "1.0", timeout=0):
        assert vr.check_admission(tmp_path, "1.0", payload_hash="same") == "reuse"
    assert vr.gc(tmp_path) == []
    process.communicate(input="\n", timeout=10)
    assert process.returncode == 0
    assert vr.gc(tmp_path) == ["1.0"]


def test_missing_candidate_refuses_after_cleanup_wins(tmp_path):
    install(tmp_path, complete=True)
    assert vr.gc(tmp_path) == ["1.0"]
    with pytest.raises(FileNotFoundError):
        with vr.retention_lease(tmp_path, "1.0", payload_hash="same"):
            pytest.fail("deleted candidate admitted")


@pytest.mark.parametrize("complete, expected", [(False, "construct"), (True, "content-conflict")])
def test_reuse_rechecks_content_under_retention(tmp_path, complete, expected):
    install(tmp_path, complete=complete)
    with pytest.raises(RuntimeError, match=expected):
        with vr.retention_lease(tmp_path, "1.0", payload_hash="different"):
            pytest.fail("mismatched candidate admitted")
    assert vr.gc(tmp_path) == ["1.0"]


def test_exception_and_cancellation_release_retention(tmp_path):
    install(tmp_path)
    for error in (RuntimeError, KeyboardInterrupt):
        with pytest.raises(error):
            with vr.retention_lease(tmp_path, "1.0"):
                raise error
        with vr._cleanup_lease(tmp_path, "1.0") as admitted:
            assert admitted


def test_retention_identity_mismatch_is_not_contention(tmp_path, monkeypatch):
    install(tmp_path)
    with vr.retention_lease(tmp_path, "1.0"):
        pass
    lock = next((tmp_path / ".retention-leases").glob("*.lock"))
    other = tmp_path / "other"
    other.write_text("different inode")
    real_lstat = vr.os.lstat
    with monkeypatch.context() as patch:
        patch.setattr(vr.os, "lstat", lambda path, *a, **kw:
                      other.stat() if Path(path) == lock else real_lstat(path, *a, **kw))
        with pytest.raises(OSError, match="retention lease path") as error:
            with vr.retention_lease(tmp_path, "1.0"):
                pass
        assert not isinstance(error.value, vr.RetentionTimeout)
    assert vr.gc(tmp_path) == ["1.0"]


def test_storage_errors_do_not_report_successful_gc(tmp_path):
    install(tmp_path)
    (tmp_path / ".retention-leases").write_text("not a directory")
    with pytest.raises(OSError):
        vr.gc(tmp_path)
    assert (vr.version_dir(tmp_path, "1.0") / "payload").exists()


def test_unreadable_selector_does_not_authorize_deletion(tmp_path, monkeypatch):
    install(tmp_path)
    read = Path.read_text

    def deny(path, *args, **kwargs):
        if path == tmp_path / vr.CURRENT_VERSION_FILE:
            raise PermissionError("selector inaccessible")
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny)
    with pytest.raises(PermissionError, match="inaccessible"):
        vr.gc(tmp_path)
    assert vr.version_dir(tmp_path, "1.0").is_dir()


def test_last_known_good_survives_both_cleanup_paths(tmp_path):
    install(tmp_path)
    (tmp_path / vr.LAST_KNOWN_GOOD_FILE).write_text("1.0\n")
    assert vr.gc(tmp_path) == []
    assert vr.toss_incomplete(tmp_path) == []
    with pytest.raises(RuntimeError, match="distinct-generation health repair"):
        vr.slot(tmp_path, "1.0", clean_incomplete=True)


def test_mutable_dev_cleanup_keeps_existing_stale_owner_exception(tmp_path, monkeypatch):
    slot = install(tmp_path, vr.DEV_VERSION)
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text(vr.DEV_VERSION + "\n")
    (tmp_path / vr.LAST_KNOWN_GOOD_FILE).write_text(vr.DEV_VERSION + "\n")
    monkeypatch.setattr(vr, "_reliable_process_enumeration", lambda: True)
    monkeypatch.setattr(vr, "_versions_with_live_process", lambda root: set())
    assert vr.slot(tmp_path, vr.DEV_VERSION, clean_incomplete=True).is_dir()
    assert not (slot / "payload").exists()
    assert not (tmp_path / vr.CURRENT_VERSION_FILE).exists()


@pytest.mark.parametrize("version", ["../escape", "", ".", "name.", "a\\b"])
def test_invalid_retention_identity_does_not_create_state(tmp_path, version):
    with pytest.raises(ValueError):
        with vr.retention_lease(tmp_path, version):
            pass
    assert not (tmp_path / ".retention-leases").exists()


def test_cleanup_rechecks_selectors_after_candidate_enumeration(tmp_path, monkeypatch):
    install(tmp_path)
    enumerate_candidates = vr._gc_candidate_versions

    def change_selection(root):
        candidates = enumerate_candidates(root)
        vr.activate(root, "1.0")
        return candidates

    monkeypatch.setattr(vr, "_gc_candidate_versions", change_selection)
    assert vr.gc(tmp_path) == []
    assert vr.current_version(tmp_path) == "1.0"


def test_cleanup_and_activation_share_deletion_guard(tmp_path, processes):
    install(tmp_path)
    process = start_owner(
        tmp_path, processes, "with vr._cleanup_lease(root, '1.0') as admitted:\n assert admitted",
    )
    with pytest.raises(vr.RetentionTimeout):
        with vr.retention_lease(tmp_path, "1.0", timeout=0.1):
            pass
    activation = child(tmp_path, "vr.activate(root, '1.0'); print('activated')")
    processes.append(activation)
    assert vr.gc(tmp_path) == []
    process.communicate(input="\n", timeout=10)
    stdout, stderr = activation.communicate(timeout=10)
    assert activation.returncode == 0, stderr
    assert stdout.strip() == "activated"
    assert vr.gc(tmp_path) == []


def test_candidate_and_rollback_pins_survive_promotion_and_drain(tmp_path):
    old = install(tmp_path, "1.0", complete=True)
    candidate = install(tmp_path, "2.0", complete=True)
    vr.activate(tmp_path, "1.0")
    with vr.retention_lease(tmp_path, "1.0"), vr.retention_lease(tmp_path, "2.0"):
        vr.activate(tmp_path, "2.0")
        assert vr.gc(tmp_path) == []
        vr.activate(tmp_path, "1.0")  # rollback retains the rejected candidate until settled
        assert vr.gc(tmp_path) == []
        assert old.is_dir() and candidate.is_dir()
    assert vr.gc(tmp_path) == ["2.0"]


def test_same_thread_construction_does_not_allow_gc_of_own_build(tmp_path):
    with vr.construction_lease(tmp_path, "1.0"):
        install(tmp_path)
        assert vr.gc(tmp_path) == []
        assert vr.toss_incomplete(tmp_path) == []
        assert vr.slot(tmp_path, "1.0", clean_incomplete=True).is_dir()


def test_selector_case_aliases_are_conservatively_protected(tmp_path):
    install(tmp_path, "VERSION")
    (tmp_path / vr.CURRENT_VERSION_FILE).write_text("version\n")
    assert vr.gc(tmp_path) == []
    assert vr.toss_incomplete(tmp_path) == []
    with pytest.raises(RuntimeError, match="distinct-generation health repair"):
        vr.slot(tmp_path, "VERSION", clean_incomplete=True)


def test_retention_case_aliases_gate_cleanup_on_every_platform(tmp_path, processes):
    install(tmp_path, "VERSION")
    process = start_owner(
        tmp_path, processes, "with vr.retention_lease(root, 'VERSION'):",
    )
    with vr._cleanup_lease(tmp_path, "version") as admitted:
        assert not admitted
    process.communicate(input="\n", timeout=10)
    with vr._cleanup_lease(tmp_path, "version") as admitted:
        assert admitted


def test_dev_claim_preserves_previous_rollback_identity(tmp_path):
    install(tmp_path, "1.0", complete=True)
    install(tmp_path, vr.DEV_VERSION, complete=True)
    vr.claim_dev(tmp_path, "test-owner", previous_version="1.0")
    vr.activate(tmp_path, vr.DEV_VERSION)
    assert vr.gc(tmp_path) == []
    with vr.retention_lease(tmp_path, "1.0", payload_hash="same"):
        vr.release_dev(tmp_path, "test-owner")
        assert vr.gc(tmp_path) == []
        vr.activate(tmp_path, "1.0")
    assert vr.gc(tmp_path) == [vr.DEV_VERSION]


def test_real_cli_gc_respects_then_reclaims_retained_candidate(tmp_path, processes):
    install(tmp_path, complete=True)
    process = start_owner(
        tmp_path, processes, "with vr.retention_lease(root, '1.0', payload_hash='same'):",
    )

    def cli_gc():
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--root", str(tmp_path), "--json", "gc"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    assert cli_gc() == {"removed": []}
    process.communicate(input="\n", timeout=10)
    assert cli_gc() == {"removed": ["1.0"]}


@pytest.mark.parametrize("operation", [
    pytest.param("gc", marks=pytest.mark.guard), "toss", "slot", "claim", "release",
])
def test_unreadable_dev_claim_refuses_cleanup_and_mutation(tmp_path, monkeypatch, operation):
    install(tmp_path)
    install(tmp_path, vr.DEV_VERSION)
    vr.claim_dev(tmp_path, "test-owner", previous_version="1.0")
    path = vr.dev_claim_path(tmp_path)
    before = path.read_bytes()
    load = vr._load_unique_json

    def denied(source):
        if source == path:
            raise PermissionError("claim inaccessible")
        return load(source)

    monkeypatch.setattr(vr, "_load_unique_json", denied)
    assert vr.read_dev_claim(tmp_path) is None  # compatibility status read only
    with pytest.raises(PermissionError, match="claim inaccessible"):
        if operation == "claim":
            vr.claim_dev(tmp_path, "different-owner", force=True)
        elif operation == "release":
            vr.release_dev(tmp_path, "test-owner", force=True)
        else:
            cleanup(tmp_path, operation)
    assert path.read_bytes() == before
    assert (vr.version_dir(tmp_path, "1.0") / "payload").exists()
    assert (vr.version_dir(tmp_path, vr.DEV_VERSION) / "payload").exists()


@pytest.mark.parametrize("claim", [
    "not json", "[]", '{"schema": "wrong", "owner": "owner"}',
    json.dumps({"schema": vr.DEV_CLAIM_SCHEMA, "owner": "owner", "previous_version": 123}),
])
def test_malformed_dev_claim_does_not_authorize_cleanup(tmp_path, claim):
    install(tmp_path)
    install(tmp_path, vr.DEV_VERSION)
    vr.dev_claim_path(tmp_path).write_text(claim)
    with pytest.raises(ValueError):
        vr.gc(tmp_path)
    with pytest.raises(ValueError):
        vr.toss_incomplete(tmp_path)
    assert vr.version_dir(tmp_path, "1.0").is_dir()
    assert vr.version_dir(tmp_path, vr.DEV_VERSION).is_dir()


@pytest.mark.parametrize("previous", ["../escape", "", "name.", 123, "a\\b", "a/b"])
def test_invalid_rollback_input_never_publishes_or_overwrites_claim(tmp_path, previous):
    fresh = tmp_path / "fresh"
    with pytest.raises(ValueError, match="rollback version"):
        vr.claim_dev(fresh, "test-owner", previous_version=previous)
    assert not vr.dev_claim_path(fresh).exists()
    assert not fresh.exists()
    vr.claim_dev(tmp_path, "original-owner", previous_version="1.0")
    before = vr.dev_claim_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="rollback version"):
        vr.claim_dev(tmp_path, "replacement-owner", previous_version=previous, force=True)
    assert vr.dev_claim_path(tmp_path).read_bytes() == before
    assert vr.release_dev(tmp_path, "original-owner")["previous_version"] == "1.0"


@pytest.mark.parametrize("owner", ["", " ", None, 123])
def test_invalid_owner_input_never_publishes_claim(tmp_path, owner):
    with pytest.raises(ValueError, match="owner"):
        vr.claim_dev(tmp_path, owner)
    assert not vr.dev_claim_path(tmp_path).exists()


def test_cli_invalid_rollback_input_refuses_without_wedging_claim(tmp_path, capsys):
    assert vr.main([
        "--root", str(tmp_path), "dev-claim", "--owner", "test-owner",
        "--previous-version", "../escape",
    ]) == 1
    assert "rollback version" in capsys.readouterr().err
    assert not vr.dev_claim_path(tmp_path).exists()
    vr.claim_dev(tmp_path, "test-owner", previous_version="1.0")
    assert vr.release_dev(tmp_path, "test-owner")["previous_version"] == "1.0"
