"""Pure source/protocol and fake-process controls, not native qualification."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

import native_qualification_receiver as receiver


def wire(**changes: Any) -> bytes:
    value = {
        "execution_scope": 7, "qualification": "functional-only",
        "desktop_qualified": False, "visible_controls_attempted": 0,
        "functional_positive": True, "original_job_drained": True,
        "controller_disposed": True, "publication_state": receiver.PROVISIONAL,
        "final_success_requires_post_publication_zero_exit": True,
        "monotonic_start": 10.0, "summary_sha256": "a" * 64, "index_sha256": "b" * 64,
    }
    value.update(changes)
    return b"FUNCTIONAL START: scope 7; functional only\n" + json.dumps(value).encode()


def test_receiver_accepts_actual_exit_and_post_capture_original_bound() -> None:
    result = receiver.terminal(
        wire(), boundary=receiver.Boundary(10.0, "start"), scope=7,
        native_exit=0, captured_at=189.99,
    )
    assert result["monotonic_start"] == 10.0 and result["desktop_qualified"] is False


@pytest.mark.parametrize("change", [
    {"execution_scope": True}, {"execution_scope": 8}, {"desktop_qualified": 0},
    {"visible_controls_attempted": False}, {"visible_controls_attempted": 1},
    {"functional_positive": False}, {"original_job_drained": False},
    {"controller_disposed": False}, {"publication_state": "complete"},
    {"final_success_requires_post_publication_zero_exit": False},
    {"monotonic_start": 11.0}, {"summary_sha256": "wrong"},
])
def test_receiver_refuses_uncorrelated_or_provisional_custody(change: dict[str, Any]) -> None:
    with pytest.raises(receiver.ReceiverError):
        receiver.terminal(
            wire(**change), boundary=receiver.Boundary(10.0, "start"),
            scope=7, native_exit=0, captured_at=20.0,
        )


@pytest.mark.parametrize("native_exit,captured_at", [
    (1, 20.0), (False, 20.0), (0, 190.0), (0, 9.0), (0, float("nan")),
])
def test_receiver_never_substitutes_tool_success_or_old_timestamp(native_exit: int, captured_at: float) -> None:
    with pytest.raises(receiver.ReceiverError):
        receiver.terminal(
            wire(), boundary=receiver.Boundary(10.0, "start"), scope=7,
            native_exit=native_exit, captured_at=captured_at,
        )


def test_receiver_refuses_duplicate_or_extra_terminal_publication() -> None:
    for output in (
        wire().replace(b'"execution_scope": 7', b'"execution_scope": 7, "execution_scope": 7'),
        wire() + b"\n{}", wire().replace(b"scope 7;", b"scope 8;"),
    ):
        with pytest.raises((receiver.ReceiverError, ValueError)):
            receiver.terminal(
                output, boundary=receiver.Boundary(10.0, "start"), scope=7,
                native_exit=0, captured_at=20.0,
            )


def test_receiver_bounds_actual_stream_bytes_without_unbounded_capture() -> None:
    stream = receiver.Stream()
    stream.read(io.BytesIO(b"x" * (receiver.STREAM_BYTES + 1)))
    assert len(stream.data) <= receiver.STREAM_BYTES
    assert stream.errors and "bounded" in stream.errors[0]


def test_receiver_final_index_is_exact_and_changed_or_orphaned_bytes_refuse(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "summary.json").write_bytes(b"summary\n")
    rows = [{"path": "summary.json", "bytes": 8,
             "sha256": hashlib.sha256(b"summary\n").hexdigest()}]
    index = {"preparation_manifest_sha256": "c" * 64, "execution_scope": 7,
             "artifact_count": 1, "artifacts": rows}
    index_data = json.dumps(index).encode()
    (root / "sha256.json").write_bytes(index_data)
    value = {"root": str(root), "execution_scope": 7,
             "summary_sha256": rows[0]["sha256"],
             "index_sha256": hashlib.sha256(index_data).hexdigest()}
    args = dict(parent=tmp_path, manifest_sha256="c" * 64,
                boundary=receiver.Boundary(10, "start"), clock=lambda: 20)
    receiver.verify_artifacts(value, **args)
    (root / "orphan").write_bytes(b"unindexed")
    with pytest.raises(receiver.ReceiverError, match="complete"):
        receiver.verify_artifacts(value, **args)
    (root / "orphan").unlink()
    (root / "summary.json").write_bytes(b"changed\n")
    with pytest.raises(receiver.ReceiverError, match="changed"):
        receiver.verify_artifacts(value, **args)


def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], str, Path]:
    launcher = tmp_path / "launch-active.ps1"
    launcher.write_bytes(b"reviewed launcher\n")
    script = tmp_path / "native_qualification_receiver.py"
    script.write_bytes(b"reviewed receiver\n")
    rows = [
        {"name": path.name, "bytes": len(path.read_bytes()),
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (launcher, script)
    ]
    data = json.dumps({"files": rows}).encode()
    (tmp_path / "prepared-manifest.json").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(receiver, "__file__", str(script))
    command = [
        "powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(launcher),
        "-ExecuteAcceptedScope", "-ExecutionScope", "7",
        "-ParentAcceptanceReference", "accepted", "-ExpectedPreparedManifestSha256", digest,
    ]
    return command, digest, launcher


def inert_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reader:
        def __init__(self, *, target: Callable[..., None], args: tuple[Any, ...], daemon: bool) -> None:
            assert daemon is True
            self.target, self.args = target, args

        def start(self) -> None:
            self.target(*self.args)

        def join(self, timeout: float) -> None:
            assert timeout >= 0

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(receiver, "threading", SimpleNamespace(Thread=Reader))


def test_receiver_checks_whole_prepared_closure_before_inserting_one_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, digest, launcher = prepared(tmp_path, monkeypatch)
    original = copy.deepcopy(command)
    argv = receiver._command(command, 7, digest, receiver.Boundary(10, "start"), lambda: 20)
    assert command == original
    assert argv[-4:] == ["-OriginalStartMonotonic", "10", "-OriginalStartUtc", "start"]
    launcher.write_bytes(b"poisoned\n")
    with pytest.raises(receiver.ReceiverError, match="changed"):
        receiver._command(command, 7, digest, receiver.Boundary(10, "start"), lambda: 20)


@pytest.mark.parametrize("change", ["scope", "digest", "command", "clock"])
def test_receiver_refuses_command_substitution_before_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    command, digest, _ = prepared(tmp_path, monkeypatch)
    if change == "scope":
        command[8] = "8"
    elif change == "digest":
        command[-1] = "d" * 64
    elif change == "command":
        command[4] = "-Command"
    else:
        command.extend(["-OriginalStartMonotonic", "11"])
    with pytest.raises(receiver.ReceiverError):
        receiver._command(command, 7, digest, receiver.Boundary(10, "start"), lambda: 20)


def test_receiver_settles_capture_and_handles_before_acceptance_without_new_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, digest, _ = prepared(tmp_path, monkeypatch)
    inert_readers(monkeypatch)
    events = []
    monkeypatch.setattr(receiver, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(receiver, "_require_existing_job", lambda: events.append("existing-job"))
    monkeypatch.setattr(receiver, "STREAM_BYTES", 65536)
    process = SimpleNamespace(
        stdout=io.BytesIO(wire()), stderr=io.BytesIO(),
        wait=lambda **kwargs: events.append("native-exit") or 0,
        _handle=SimpleNamespace(Close=lambda: events.append("handle-close")),
    )
    def spawn(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        events.append("spawn")
        assert kwargs["bufsize"] == 0
        assert kwargs["creationflags"] == 0x08000000
        assert argv[-4:] == ["-OriginalStartMonotonic", "10.0", "-OriginalStartUtc", argv[-1]]
        return process
    monkeypatch.setattr(receiver.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(receiver.subprocess, "Popen", spawn)
    def artifacts(*args: Any, **kwargs: Any) -> None:
        assert process.stdout.closed and process.stderr.closed
        assert events[-1] == "handle-close"
        events.append("artifact-check")
    monkeypatch.setattr(receiver, "verify_artifacts", artifacts)
    times = iter([10.0, *([20.0] * 100)])
    report = receiver.receive(command, scope=7, cwd=tmp_path, manifest_sha256=digest, clock=lambda: next(times))
    assert report["passed"] is True and report["native_exit"] == 0, report
    assert report["post_capture_monotonic"] == report["post_artifact_verification_monotonic"] == 20
    assert report["captured_streams"]["stdout"]["sha256"] == hashlib.sha256(wire()).hexdigest()
    assert report["captured_streams"]["stderr"]["complete"] is True
    assert report["preparation_sources_unchanged_after"] is True
    assert events == ["existing-job", "spawn", "native-exit", "handle-close", "artifact-check"]


def test_receiver_refuses_late_settlement_and_does_not_kill_or_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, digest, _ = prepared(tmp_path, monkeypatch)
    inert_readers(monkeypatch)
    monkeypatch.setattr(receiver, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(receiver, "_require_existing_job", lambda: None)
    now = [10.0]
    starts = []
    process = SimpleNamespace(
        stdout=io.BytesIO(wire()), stderr=io.BytesIO(),
        _handle=SimpleNamespace(Close=lambda: None),
    )
    def wait(**kwargs: Any) -> int:
        now[0] = 191.0
        return 0
    process.wait = wait
    def spawn(*args: Any, **kwargs: Any) -> SimpleNamespace:
        starts.append(True)
        return process
    monkeypatch.setattr(receiver.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(receiver.subprocess, "Popen", spawn)
    report = receiver.receive(command, scope=7, cwd=tmp_path, manifest_sha256=digest, clock=lambda: now[0])
    assert report["passed"] is False and report["errors"]
    assert starts == [True] and report["native_exit"] == 0


def test_receiver_rechecks_prepared_source_after_native_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command, digest, launcher = prepared(tmp_path, monkeypatch)
    inert_readers(monkeypatch)
    monkeypatch.setattr(receiver, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(receiver, "_require_existing_job", lambda: None)
    process = SimpleNamespace(
        stdout=io.BytesIO(wire()), stderr=io.BytesIO(),
        _handle=SimpleNamespace(Close=lambda: None),
    )
    def wait(**kwargs: Any) -> int:
        launcher.write_bytes(b"changed after native creation\n")
        return 0
    process.wait = wait
    monkeypatch.setattr(receiver.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(receiver.subprocess, "Popen", lambda *args, **kwargs: process)
    times = iter([10.0, *([20.0] * 100)])
    report = receiver.receive(command, scope=7, cwd=tmp_path, manifest_sha256=digest, clock=lambda: next(times))
    assert report["native_exit"] == 0 and report["passed"] is False
    assert any("source closure changed" in error for error in report["errors"])
