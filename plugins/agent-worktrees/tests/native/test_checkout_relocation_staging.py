"""Native stage inheritance must preserve the admitted checkout access contract."""

import ctypes
import hashlib
import json
import os
import shutil
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

import pytest
from agent_procutil import _daemon_instance_owner as owner
from agent_procutil.owned_process import run_caller_owned

import agent_worktrees
from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import checkout_relocation_state as state
from agent_worktrees.git_repository_environment import repository_identity_env
from test_checkout_relocation import checkout as checkout

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows stage DACL inheritance")


def _dacl(path, *, administrator=False, deny_create=False):
    api, kernel = owner._apis()
    user = owner._current_sids(os.getpid())[0]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    api.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    api.SetFileSecurityW.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    denied = f"(D;;0x2;;;{user})" if deny_create else ""
    extra = "(A;OICI;FA;;;BA)" if administrator else ""
    assert api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"D:P{denied}(A;OICI;FA;;;{user}){extra}", 1, ctypes.byref(descriptor), None,
    )
    try:
        assert api.SetFileSecurityW(str(path), 4 | 0x80000000, descriptor)
    finally:
        kernel.LocalFree(descriptor)


@pytest.fixture
def admitted(checkout, monkeypatch):
    fixture = checkout
    for path in (
        fixture.source.parent, fixture.destination.parent,
        *relocation.transfer.walk(fixture.source),
    ):
        _dacl(path)

    def stopped(record, path):
        assert record.worktree_id == fixture.record.worktree_id
        assert Path(path).parent in {fixture.source.parent, fixture.destination.parent}
        assert not record.sessions and record.status == "completed"

    def forbidden(*_args, **_kwargs):
        pytest.fail("fixture must not query a native owner/backend")

    monkeypatch.setattr(relocation, "_stopped", stopped)
    monkeypatch.setattr(relocation.sessions, "verify_worktree_active", forbidden)
    return fixture


@pytest.mark.parametrize("administrator", [False, True], ids=["owner-only", "owner-admin"])
def test_native_stage_inherits_admitted_access_and_preserves_metadata(admitted, administrator, record_property):
    fixture = admitted
    if administrator:
        for path in (fixture.destination.parent, *relocation.transfer.walk(fixture.source)):
            _dacl(path, administrator=True)
    expected = relocation.transfer.manifest(fixture.source)
    record_property("python", sys.version)
    result = relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert result["phase"] == "complete"
    assert relocation.transfer.manifest(fixture.destination) == expected
    assert not fixture.source.exists()
    assert relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)["phase"] == "complete"


@pytest.mark.parametrize("unsafe", ["nonuniform-source", "different-parent", "denied-parent"])
def test_stage_fix_does_not_admit_unsafe_access(admitted, unsafe):
    fixture = admitted
    path = fixture.source / "tracked.txt" if unsafe == "nonuniform-source" else fixture.destination.parent
    _dacl(path, administrator=unsafe != "denied-parent", deny_create=unsafe == "denied-parent")
    before = fixture.record.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="uniform checkout access|confirmed source delete/write"):
        relocation._action_plan(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert fixture.record.yaml_path.read_bytes() == before
    assert not state.journal_path(fixture.record.yaml_path).exists()
    assert fixture.source.exists() and not fixture.destination.exists()
    assert not list(fixture.destination.parent.iterdir())


def test_stage_creation_collision_retains_original_and_unknown_stage(admitted, monkeypatch):
    fixture = admitted
    original = Path.mkdir

    def collision(path, *args, **kwargs):
        if ".relocating-" in path.name:
            original(path, *args, **kwargs)
            (path / "collision").write_bytes(b"do not replace")
            raise FileExistsError("fixture stage creation race")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as race:
        race.setattr(Path, "mkdir", collision)
        with pytest.raises(FileExistsError, match="fixture stage"):
            relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    journal = state.read(fixture.record.yaml_path)
    stage = Path(journal["stage"])
    assert journal["phase"] == "prepared"
    with pytest.raises(ValueError, match="stage creation outcome is uncertain"):
        relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    assert (stage / "collision").read_bytes() == b"do not replace"
    assert fixture.source.exists() and not fixture.destination.exists()


@pytest.mark.parametrize("appearance", ["after-copy", "during-digest"])
def test_new_owner_after_copy_refuses_before_destination_publication(admitted, monkeypatch, appearance):
    fixture = admitted
    copy = relocation.transfer.copy
    digest = relocation._digest
    finished_copy = False

    def newly_active(*_args):
        raise ValueError("new native owner")

    def copied(source, stage):
        nonlocal finished_copy
        copy(source, stage)
        finished_copy = True
        if appearance == "after-copy":
            monkeypatch.setattr(relocation, "_stopped", newly_active)

    def fingerprint(path):
        value = digest(path)
        if finished_copy and path == fixture.source and appearance == "during-digest":
            monkeypatch.setattr(relocation, "_stopped", newly_active)
        return value

    monkeypatch.setattr(relocation.transfer, "copy", copied)
    monkeypatch.setattr(relocation, "_digest", fingerprint)
    with pytest.raises(ValueError, match="new native owner"):
        relocation.move(fixture.record.worktree_id, str(fixture.destination), fixture.config)
    journal = state.read(fixture.record.yaml_path)
    assert journal["phase"] == "copied"
    assert fixture.source.exists() and Path(journal["stage"]).exists()
    assert not fixture.destination.exists()


@pytest.mark.timeout(180)
def test_receiving_changed_root_fresh_process_and_cli_consumers(tmp_path_factory, record_property):
    persistent = os.environ.get("RELOCATION_LIVE_STATE")
    if persistent:
        tmp_path = Path(persistent)
        tmp_path.mkdir()
        (tmp_path / "scratch-owner.json").write_text(json.dumps({
            "purpose": "Enrolled receiving fixture anchor and recovery state",
            "retention": "Retain on failure; remove only after exact Git and fixture cleanup",
        }), encoding="utf-8")
    else:
        tmp_path = tmp_path_factory.mktemp("b")
    package = Path(agent_worktrees.__file__).resolve().parent
    expected = {
        name: hashlib.sha256((package / name).read_bytes()).hexdigest()
        for name in (
            "checkout_relocation.py", "checkout_relocation_plan.py", "checkout_relocation_state.py",
            "checkout_transfer.py", "receiving_allocation.py", "receiving_creation_journal.py",
            "receiving_relocation.py",
        )
    }
    worker = Path(__file__).with_name("receiving_staging_fixture.py")
    home = tmp_path / "s"
    setup_env = repository_identity_env()
    setup_env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    canary = tmp_path / "canary"
    initialized = run_caller_owned(
        ["git", "init", "--bare", "--template=", "--quiet", str(canary)],
        cwd=tmp_path, env=setup_env, timeout=15, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert initialized.returncode == 0, initialized.stderr
    before_canary = {str(path): path.read_bytes() for path in canary.rglob("*") if path.is_file()}
    poison = tmp_path / "poison-config"
    poison.write_text("[core]\nrepositoryformatversion = 999\n", encoding="utf-8")
    inherited = dict(
        os.environ, GIT_DIR=str(canary), GIT_WORK_TREE=str(tmp_path / "unrelated"),
        GIT_INDEX_FILE=str(canary / "index"), GIT_CONFIG_GLOBAL=str(poison), GIT_CONFIG_SYSTEM=str(poison),
        GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.repositoryformatversion", GIT_CONFIG_VALUE_0="999",
    )
    env = repository_identity_env(inherited)
    env.update(
        AGENT_HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1",
    )
    for key in (
        "COPILOT_EXTENSIONS_CONTEXT", "PYTHONPATH", "COPILOT_PLUGIN_ROOT", "AGENT_RT_ROOT",
        "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_WORKTREES_STATUS_MONITOR",
        "COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID",
    ):
        env.pop(key, None)

    def consume(mode, cwd, *arguments):
        result = run_caller_owned(
            [sys.executable, "-I", "-B", str(worker), mode, str(home), str(package),
             json.dumps(expected), *arguments],
            cwd=cwd, env=env, timeout=60, max_stdout_bytes=1024 * 1024, max_stderr_bytes=256 * 1024,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        return result

    flow = json.loads(consume("flow", tmp_path).stdout.splitlines()[-1])
    continued = json.loads(consume("continue", tmp_path).stdout.splitlines()[-1])
    assert flow["passed"] and continued["passed"]
    assert continued["new_allocation_uses_new_root"] and continued["both_creator_obligations_closed"]
    for directory in (
        Path(flow["destination"]), Path(flow["old_root"]) / Path(flow["origin_ledger"]).stem,
    ):
        before = {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()}
        result = consume("lookup", directory, "--project", "sample", "get", "worktree-dir")
        assert Path(result.stdout.strip()) == directory
        assert "SOURCE_LOOKUP_PROOF=" in result.stderr
        assert {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()} == before
    assert {str(path): path.read_bytes() for path in canary.rglob("*") if path.is_file()} == before_canary
    for name, digest in expected.items():
        record_property(name + "_sha256", digest)
    record_property("consumer_python", sys.version)
    record_property("consumer_tier", "source_candidate_not_installed")
    if os.environ.get("RELOCATION_LIVE_ROOTS"):
        assert flow["actual_live_admission"]
        record_property("live_cross_volume", json.dumps({
            "flow": flow, "continued": continued, "fixture": str(tmp_path),
        }))
        evidence = os.environ.get("RELOCATION_LIVE_EVIDENCE")
        if evidence:
            Path(evidence).write_text(json.dumps({
                "flow": flow, "continued": continued, "fixture": str(tmp_path),
            }, indent=2), encoding="utf-8")
        paths = [
            Path(flow["destination"]), Path(continued["new_allocation"]["path"]),
            Path(flow["old_root"]) / Path(flow["origin_ledger"]).stem,
        ]
        for path in paths:
            assert path.parent in {Path(value) for value in json.loads(os.environ["RELOCATION_LIVE_ROOTS"])}
            removed = run_caller_owned(
                ["git", "-C", str(home / "anchor"), "worktree", "remove", "--force", str(path)],
                cwd=tmp_path, env=env, timeout=15, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
            )
            assert removed.returncode == 0, removed.stderr
        for root in json.loads(os.environ["RELOCATION_LIVE_ROOTS"]):
            root = Path(root)
            marker = root / ".finalize.lock.lock"
            if marker.exists():
                assert not marker.is_symlink() and marker.stat().st_size == 0
                assert list(root.iterdir()) == [marker]
                with relocation.tracking._RecordLock(root / ".finalize.lock.guard", timeout=1, require_sidecar=True):
                    pass
                marker.unlink()
            root.rmdir()
        remaining = run_caller_owned(
            ["git", "-C", str(home / "anchor"), "worktree", "list", "--porcelain"],
            cwd=tmp_path, env=env, timeout=15, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
        )
        assert remaining.returncode == 0 and remaining.stdout.count("worktree ") == 1
        assert persistent and (tmp_path / "scratch-owner.json").is_file()
        shutil.rmtree(tmp_path)


@pytest.mark.timeout(180)
def test_live_cross_volume_receiving_uses_existing_native_owners(
    tmp_path_factory, record_property, monkeypatch,
):
    enrollment = os.environ.get("RELOCATION_LIVE_ENROLLMENT")
    if not enrollment:
        pytest.skip("explicit private two-volume roots and existing native observation scope required")
    enrolled = json.loads(Path(enrollment).read_text(encoding="utf-8"))
    primary, second = enrolled["primary"], enrolled["second"]
    data, ports = enrolled["native_data_dir"], json.dumps(enrolled["native_ports"])
    roots = [Path(primary), Path(second)]
    assert all(root.is_absolute() and root.is_dir() and root.resolve() == root for root in roots)
    assert roots[0].stat().st_dev != roots[1].stat().st_dev
    identity = "receiving-" + uuid.uuid4().hex
    monkeypatch.setenv("RELOCATION_LIVE_ROOTS", json.dumps([str(root / identity) for root in roots]))
    monkeypatch.setenv("RELOCATION_LIVE_PORTS", ports)
    monkeypatch.setenv("PSMUX_DATA_DIR", data)
    monkeypatch.setenv("RELOCATION_LIVE_STATE", str(roots[1] / ("state-" + identity)))
    if enrolled.get("evidence"):
        monkeypatch.setenv("RELOCATION_LIVE_EVIDENCE", enrolled["evidence"])
    test_receiving_changed_root_fresh_process_and_cli_consumers(tmp_path_factory, record_property)
