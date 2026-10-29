"""MCP retirement policy and actual native pinned-tree settlement."""
from __future__ import annotations

import importlib.util
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("reap_versions_under_test", SCRIPTS / "reap_versions.py")
rv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rv)


def _row(pid, parent, image, *, created=None):
    return {
        "pid": pid, "ppid": parent, "image": str(image), "argv": [str(image)],
        "command": str(image), "created": created if created is not None else pid,
    }


@pytest.fixture
def owners(tmp_path, monkeypatch):
    rows = {
        os.getpid(): _row(os.getpid(), 0, sys.executable, created=1),
        111: _row(111, os.getpid(), tmp_path / "versions/1.0/bin/python"),
        112: _row(112, 111, tmp_path / "upstream"),
        222: _row(222, os.getpid(), tmp_path / "versions/2.0/bin/python"),
    }
    state = {"rows": rows, "paused": [], "terminated": [], "resumed": []}

    class Pin:
        def __init__(self, pid, birth):
            self.pid, self.birth = pid, birth

        def __enter__(self):
            return self

        def __exit__(self, *_):
            if self.pid not in state["terminated"]:
                state["resumed"].append(self.pid)

        def exited(self):
            return self.pid in state["terminated"]

        def pause(self):
            state["paused"].append(self.pid)

        def terminate(self):
            state["terminated"].append(self.pid)

    monkeypatch.setattr(rv, "PinnedProcess", Pin)
    monkeypatch.setattr(rv, "birth_identity", lambda pid: f"linux:boot:ns:{rows[pid]['created']}")
    monkeypatch.setattr(rv, "_inspect", lambda root: {
        pid: row for pid, row in rows.items() if pid not in state["terminated"]
    })
    monkeypatch.setattr(rv, "current_version", lambda *a: "2.0")
    monkeypatch.setattr(rv, "list_versions", lambda root: ["1.0", "2.0"])
    return state


def test_reap_settles_original_tree_and_spares_current_and_parent(tmp_path, owners):
    reaped = rv.reap_stale(tmp_path)
    assert owners["paused"] == [111, 112]
    assert owners["terminated"] == [111, 112]
    assert owners["resumed"] == []
    assert reaped == [{"version": "1.0", "pid": 111, "terminated": True}]


def test_reap_exclusion_protects_descendants(tmp_path, owners):
    assert rv.reap_stale(tmp_path, exclude_pids={111}) == []
    assert owners["paused"] == []


def test_fresh_recovery_cannot_call_a_retained_old_owner_settled(tmp_path, owners):
    with pytest.raises(rv.RetirementRefused, match="protected from retirement"):
        rv.reap_stale(tmp_path, exclude_pids={111}, fresh=True)
    assert owners["paused"] == []


@pytest.mark.parametrize("kind", ["current", "excluded", "transport"])
def test_protected_child_refuses_before_pausing(tmp_path, owners, kind):
    rows = owners["rows"]
    options = {}
    if kind == "current":
        rows[222]["ppid"] = 111
    elif kind == "excluded":
        options["exclude_pids"] = {112}
    else:
        rows[112]["image"] = rows[112]["argv"][0] = "ssh.exe"
    with pytest.raises(rv.RetirementRefused, match="protected"):
        rv.reap_stale(tmp_path, **options)
    assert owners["paused"] == owners["terminated"] == []


def test_pid_reuse_cannot_be_readmitted(tmp_path, owners, monkeypatch):
    monkeypatch.setattr(rv, "birth_identity", lambda pid: "linux:boot:ns:999")
    with pytest.raises(rv.RetirementRefused, match="identity changed"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == owners["terminated"] == []


def test_parent_pid_reuse_refuses_the_observed_tree(tmp_path, owners):
    owners["rows"][112]["created"] = 110
    with pytest.raises(rv.RetirementRefused, match="parent identity"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == []


def test_uninspectable_kernel_child_refuses_before_pausing(tmp_path, owners, monkeypatch):
    owners["rows"][111]["children"] = [999]
    original = rv.birth_identity
    monkeypatch.setattr(rv, "birth_identity", lambda pid: "linux:boot:ns:999" if pid == 999 else original(pid))
    with pytest.raises(rv.RetirementRefused, match="child cannot be inspected"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == []


def test_rooted_process_paths_cannot_traverse_a_foreign_version_directory(tmp_path, owners):
    directory = tmp_path / "versions"
    directory.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    try:
        (directory / "1.0").symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip("Native symlink creation unavailable")
    with pytest.raises(rv.inspection.UninstallRefused, match="Redirected"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == []


def test_exit_failure_resumes_remaining_originals(tmp_path, owners, monkeypatch):
    pin_type = rv.PinnedProcess

    def fail(_):
        raise rv.RetirementRefused("Process exit was not confirmed")

    monkeypatch.setattr(pin_type, "terminate", fail)
    with pytest.raises(rv.RetirementRefused, match="exit was not confirmed"):
        rv.reap_stale(tmp_path)
    assert owners["resumed"] == [112, 111]
    assert owners["terminated"] == []


def test_child_discovered_after_parent_pause_is_retired(tmp_path, owners, monkeypatch):
    original = rv._inspect
    calls = []

    def inspect(root):
        calls.append(True)
        if len(calls) == 2:
            owners["rows"][113] = _row(113, 112, tmp_path / "late upstream")
        return original(root)

    monkeypatch.setattr(rv, "_inspect", inspect)
    rv.reap_stale(tmp_path)
    assert owners["paused"] == owners["terminated"] == [111, 112, 113]


def test_protected_late_child_resumes_without_termination(tmp_path, owners, monkeypatch):
    original = rv._inspect

    def inspect(root):
        if owners["paused"]:
            owners["rows"][113] = _row(113, 112, "copilot.exe")
        return original(root)

    monkeypatch.setattr(rv, "_inspect", inspect)
    with pytest.raises(rv.RetirementRefused, match="protected"):
        rv.reap_stale(tmp_path)
    assert owners["terminated"] == []
    assert owners["resumed"] == [112, 111]


@pytest.mark.parametrize("name", [".venv", "venv", "tool"])
def test_fresh_recovery_settles_known_legacy_without_old_receipt(tmp_path, owners, name):
    owners["rows"][111]["argv"] = [str(tmp_path / name / "bin/python")]
    owners["rows"][111]["image"] = sys.executable
    assert rv.reap_stale(tmp_path) == []
    assert rv.reap_stale(tmp_path, fresh=True)[0]["version"] == "legacy"
    assert owners["terminated"] == [111, 112]


@pytest.mark.parametrize("declared", [False, True])
def test_fresh_process_inventory_follows_the_maintained_manifest(tmp_path, owners, monkeypatch, declared):
    original = rv.footprint.parse

    def parse(text, plugin):
        manifest = original(text, plugin)
        effect = {
            **manifest.effects["legacy-runtime-processes"],
            "entries": ["new-declared-code"] if declared else [],
        }
        return replace(manifest, effects={**manifest.effects, "legacy-runtime-processes": effect})

    monkeypatch.setattr(rv.footprint, "parse", parse)
    owners["rows"][111]["argv"] = [str(tmp_path / "new-declared-code/python")]
    owners["rows"][111]["image"] = sys.executable
    records = rv.reap_stale(tmp_path, fresh=True)
    assert bool(records) is declared
    assert owners["terminated"] == ([111, 112] if declared else [])


def test_explicit_no_reap_cannot_claim_old_owner_silence(tmp_path, owners):
    with pytest.raises(rv.RetirementRefused, match="explicitly disabled"):
        rv.reap_stale(tmp_path, fresh=True, check_only=True)
    assert owners["paused"] == []
    del owners["rows"][111]
    assert rv.reap_stale(tmp_path, fresh=True, check_only=True) == []


def test_missing_current_is_not_silently_reported_as_settled(tmp_path, owners, monkeypatch):
    monkeypatch.setattr(rv, "current_version", lambda *a: None)
    with pytest.raises(rv.RetirementRefused, match="installer-selected"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == []


def test_reap_matches_normalized_current(tmp_path, owners, monkeypatch):
    monkeypatch.setattr(rv, "current_version", lambda *a: "1-0")
    assert rv.reap_stale(tmp_path)[0]["pid"] == 222
    assert owners["terminated"] == [222]


def test_real_tree_exit_spares_original_parent(tmp_path):
    if os.name != "nt" and not sys.platform.startswith("linux"):
        pytest.skip("External identity-pinned retirement requires Windows or Linux")
    from agent_procutil._daemon_instance_identity import birth_identity, identity_state

    src = (
        "import os,subprocess,sys,time\n"
        "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
        "creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)\n"
        "print(c.pid,flush=True)\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-c", src, str(tmp_path)],
        stdout=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    child = None
    try:
        child = int(process.stdout.readline())
        child_birth = birth_identity(child)
        rows = rv._inspect(tmp_path)
        assert process.pid in rows and child in rows
        expected = rv._descendants(rows, {process.pid})
        auxiliaries = expected - {process.pid, child}
        assert all(Path(rows[pid]["image"]).name.casefold() == "conhost.exe" for pid in auxiliaries)
        births = {pid: rv._birth(rows[pid]) for pid in expected}
        retired = rv._retire(
            tmp_path, rows, {process.pid}, {os.getpid()}, check_only=False,
        )
        assert retired == expected
        assert process.wait(timeout=5) is not None
        assert all(identity_state(pid, birth) in {"dead", "reused"} for pid, birth in births.items())
        assert birth_identity(os.getpid())
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child is not None and "child_birth" in locals():
            from agent_procutil._daemon_instance_identity import stop_identity
            if identity_state(child, child_birth) == "matching":
                stop_identity(child, child_birth)
        process.stdout.close()
