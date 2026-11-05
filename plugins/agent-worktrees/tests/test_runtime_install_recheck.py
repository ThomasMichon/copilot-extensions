"""The construction owner must recheck held inputs before candidate publication."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PROGRAM = """
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import runtime_install_recheck as handover
marker, mode = Path(sys.argv[2]), sys.argv[3]
with handover.candidate_channel() as check:
    print("candidate progress", flush=True)
    check("selector" if mode == "wrong-order" else "helpers")
    if mode == "descendant":
        subprocess.run(
            [sys.executable, "-I", "-B", "-c", "print('descendant progress')"],
            stdin=subprocess.DEVNULL, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    if mode != "early":
        check("selector")
        marker.write_text("published", encoding="utf-8")
"""


@pytest.fixture
def handover(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import runtime_install_recheck

    return runtime_install_recheck


def spawn(marker: Path, mode: str, *, stdin=subprocess.PIPE):
    return subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", PROGRAM, str(SCRIPTS), str(marker), mode],
        stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


@pytest.mark.parametrize("denied", [None, "helpers", "selector"])
def test_parent_rechecks_each_publication_boundary(handover, tmp_path, denied):
    marker = tmp_path / "published"
    process = spawn(marker, "normal")
    calls = []

    def verify(phase):
        calls.append(phase)
        if phase == denied:
            raise ValueError("selected inputs changed")

    if denied is None:
        handover.supervise(process, verify)
        assert calls == ["helpers", "selector"] and marker.read_text() == "published"
    else:
        with pytest.raises(handover.HandoverError, match="verification refused"):
            handover.supervise(process, verify)
        assert calls == (["helpers"] if denied == "helpers" else ["helpers", "selector"])
        assert process.returncode is not None and not marker.exists()
    assert b"candidate progress" in process.stderr.read()
    process.stderr.close()


@pytest.mark.parametrize("outcome", [
    "owned", "local-only", "missing-owner", "missing-selection", "revoked", "foreign-source",
    "missing-admission", "changed-admission", "duplicate-admission", "missing-authentication",
])
def test_associated_source_profile_requires_its_live_owner(handover, outcome):
    identity = {"plugin": "agent-worktrees", "source_commit": "a" * 40}
    source = SimpleNamespace(
        document=lambda: identity, source_commit="a" * 40, assembly_hash="b" * 64,
    )
    profile = {
        "component": "agent-worktrees", "mode": "source",
        "release": {"identity": "source:" + source.source_commit, "sha256": "sha256:" + source.assembly_hash},
    }
    wheel = {"filename": "fixture-1.0-py3-none-any.whl", "sha256": "sha256:" + "c" * 64}
    lock = {
        "source": identity, "policy": {"policyId": "sha256:" + "d" * 64},
        "trigger": "release-index-unavailable", "files": [wheel],
        "source_authentication": {
            "source": identity, "owner_policy": "sha256:" + "d" * 64,
            "trigger": "release-index-unavailable", "mode": "source",
        },
        "acquisition": {
            "schema": "copilot-extensions.retained-wheel-feed-admission", "version": 1,
            "policy_id": "sha256:" + "e" * 64, "files": [dict(wheel)],
        },
    }
    if outcome == "foreign-source":
        lock["source_authentication"]["source"] = {"plugin": "foreign"}
    elif outcome == "missing-authentication":
        lock.pop("source_authentication")
    elif outcome == "missing-admission":
        lock.pop("acquisition")
    elif outcome == "changed-admission":
        lock["acquisition"]["files"][0]["sha256"] = "sha256:" + "f" * 64
    elif outcome == "duplicate-admission":
        lock["acquisition"]["files"].append(dict(wheel))
    elif outcome == "local-only":
        lock["files"] = []
        lock.pop("acquisition")
    binding = {"profile": profile, "source_lock": lock, "build_toolchain": {}}
    calls = []

    def verify():
        calls.append("verified")
        if outcome == "revoked":
            raise ValueError("association revoked")

    callback = None if outcome == "missing-owner" else verify
    selection = None if outcome == "missing-selection" else {}
    if outcome in ("owned", "local-only"):
        assert handover.wheel_profile(binding, source, callback, selection, ValueError) == profile
        assert calls == ["verified"]
    else:
        with pytest.raises(ValueError):
            handover.wheel_profile(binding, source, callback, selection, ValueError)


@pytest.mark.parametrize("mode", ["wrong-order", "early"])
def test_missing_or_unordered_boundary_never_reports_success(handover, tmp_path, mode):
    marker = tmp_path / "published"
    process = spawn(marker, mode)
    with pytest.raises(handover.HandoverError):
        handover.supervise(process, lambda _phase: None)
    assert process.returncode is not None and not marker.exists()
    process.stderr.close()


def test_parent_interruption_settles_the_waiting_candidate(handover, tmp_path):
    marker = tmp_path / "published"
    process = spawn(marker, "normal")

    def interrupted(_phase):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        handover.supervise(process, interrupted)
    assert process.returncode is not None and not marker.exists()
    process.stderr.close()


def test_descendant_output_cannot_enter_the_control_channel(handover, tmp_path):
    marker = tmp_path / "published"
    process = spawn(marker, "descendant")
    handover.supervise(process, lambda _phase: None)
    assert marker.read_text() == "published"
    assert b"descendant progress" in process.stderr.read()
    process.stderr.close()


def test_invalid_control_bytes_fail_closed(handover):
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\n')"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    with pytest.raises(handover.HandoverError):
        handover.supervise(
            process, lambda _phase: pytest.fail("invalid frame reached verification"),
        )
    assert process.returncode is not None
    process.stderr.close()


def test_missing_parent_cannot_authorize_candidate(tmp_path):
    marker = tmp_path / "published"
    process = spawn(marker, "normal", stdin=subprocess.DEVNULL)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode != 0 and not marker.exists()
    assert b"helpers\n" in stdout and b"publication" in stderr.lower()


@pytest.mark.parametrize("fault", [
    None, "identity", "descriptor", "project", "profile", "selection",
])
def test_parent_rechecks_selected_snapshot(handover, monkeypatch, tmp_path, fault):
    profile, selection = {"profile": "admitted"}, {"target": "selected"}
    selected = SimpleNamespace(
        identity="source", source_descriptor={"kind": "source"},
        project={"profile": "profile.json"},
    )
    current = SimpleNamespace(**vars(selected))
    if fault == "identity":
        current.identity = "different"
    elif fault == "descriptor":
        current.source_descriptor = {"kind": "different"}
    elif fault == "project":
        current.project = {"profile": "different.json"}
    source = ModuleType("install_source")
    source.select_project = lambda *_args: current
    source._member = lambda *_args: tmp_path / "profile.json"
    source._read = lambda _path: {"profile": "changed"} if fault == "profile" else profile
    source.native_selection = lambda *_args: (
        {"target": "changed"} if fault == "selection" else selection
    )
    monkeypatch.setitem(sys.modules, "install_source", source)
    wheels = SimpleNamespace(receipt=lambda: {"binding": profile})
    if fault is None:
        handover.verify_selected(tmp_path, selected, wheels, selection)
    else:
        with pytest.raises(handover.HandoverError):
            handover.verify_selected(tmp_path, selected, wheels, selection)
