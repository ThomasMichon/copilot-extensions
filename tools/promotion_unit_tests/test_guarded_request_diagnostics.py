"""Passive guarded-request diagnostics through the existing refusal boundary."""
from __future__ import annotations

import errno
import importlib
import json
import ssl
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest
from core_native_feed_errors import GuardedSelectionError, guarded_stop_reason, safe_guarded_request_reason
from installer_wheel_acquisition import AcquisitionLimits, WheelError, WheelFeed, _Budget, _Client

guard = importlib.import_module("core_native_feed_resolution")
PRIVATE = "https://example.invalid/private?credential=fixture-secret"


def _limits() -> AcquisitionLimits:
    return AcquisitionLimits(
        max_packages=4, max_requests=8, max_redirects=2, max_candidates_per_project=16,
        max_index_bytes=4096, max_total_metadata_bytes=8192, max_wheel_bytes=16384,
        max_closure_bytes=32768, max_temporary_bytes=65536, max_workers=2,
    )


class _PendingResolver:
    def __init__(self) -> None:
        self.killed = False
        self.waits: list[int] = []

    def poll(self) -> int | None:
        return -9 if self.killed else None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: int) -> int:
        self.waits.append(timeout)
        return -9


def _failure(
    monkeypatch: pytest.MonkeyPatch, stage: str, native: BaseException,
) -> tuple[WheelError, _Client]:
    limits, deadline = _limits(), time.monotonic() + 30
    budget = _Budget(limits, deadline)
    client = _Client(limits, deadline, ssl.create_default_context(), {}, budget)

    def refuse(_request: object, *, timeout: float) -> None:
        assert timeout > 0
        raise URLError(native)

    def selected_client(*_args: object) -> _Client:
        return client

    monkeypatch.setattr(client.opener, "open", refuse)
    monkeypatch.setattr(guard.acquisition, "_Client", selected_client)
    feed = WheelFeed("fixture", "https://feed.example/simple/")
    with pytest.raises(WheelError) as caught:
        if stage == "project-index":
            guard._fetch_project(feed, "fixture", limits, deadline, None, budget)
        elif stage == "wheel":
            server = SimpleNamespace(
                lock=threading.RLock(),
                files={"/wheel": ("fixture", "fixture-1.0-py3-none-any.whl", "a" * 64)},
                projects={"fixture": {
                    "entries": [], "base": "https://feed.example/", "origin": "https://feed.example",
                }},
                wheel_locks={}, cache={}, admitted={}, cutoffs={}, floor=None, origins=(),
                limits=limits, deadline=deadline, context=None, budget=budget, feed=feed,
            )

            def selected(*_args: object) -> tuple[str, str, None]:
                return "https://cdn.example/fixture.whl", "2020-01-01T00:00:00Z", None

            monkeypatch.setattr(guard.acquisition, "_selected", selected)
            guard._GuardedIndex.wheel(server, "/wheel")
        else:
            client.request(feed.index_url, feed, None, "application/json", index=True)
    assert type(caught.value) is WheelError
    assert type(caught.value.__context__) is URLError
    return caught.value, client


@pytest.mark.parametrize(
    ("stage", "kind", "request_count"),
    [("project-index", "tls", 1), ("wheel", "connect", 1), ("project-index", "reset", 2)],
    ids=["index-tls", "wheel-connect", "index-reset"],
)
def test_request_codes_survive_real_normalization_and_resolver_stop(
    monkeypatch: pytest.MonkeyPatch, stage: str, kind: str, request_count: int,
) -> None:
    native: OSError
    if kind == "tls":
        native = ssl.SSLCertVerificationError(1, PRIVATE)
        native.verify_code = 20
        cause, number, windows, tls = "SSLCertVerificationError", 1, "none", "20"
    elif kind == "connect":
        native = ConnectionRefusedError(10061, PRIVATE)
        setattr(native, "winerror", 10061)
        cause, number, windows, tls = "ConnectionRefusedError", 10061, "10061", "none"
    else:
        native = ConnectionResetError(errno.ECONNRESET, PRIVATE)
        cause, number, windows, tls = "ConnectionResetError", errno.ECONNRESET, "none", "none"
    failure, client = _failure(monkeypatch, stage, native)
    assert client.requests == request_count
    client.budget.fail(failure)
    process = _PendingResolver()

    def pending(*_args: object, **_kwargs: object) -> _PendingResolver:
        return process

    monkeypatch.setattr(guard.subprocess, "Popen", pending)
    with pytest.raises(GuardedSelectionError) as stopped:
        guard._run_uv(["unused"], {}, SimpleNamespace(budget=client.budget), client.deadline)
    reason = (
        f"guarded request [stage={stage},type={cause},errno={number},"
        f"winerror={windows},tls={tls}]"
    )
    assert str(stopped.value) == safe_guarded_request_reason(reason) == reason
    native.errno = 999
    assert str(stopped.value) == reason
    assert PRIVATE not in reason
    assert process.killed and process.waits == [10]


@pytest.mark.parametrize("kind", ["unknown", "malformed"])
def test_unknown_causes_and_invalid_codes_are_not_formatted(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    class UntrustedOSError(OSError):
        def __str__(self) -> str:
            pytest.fail("diagnostics must not format the underlying exception")

    native: OSError
    if kind == "unknown":
        native = UntrustedOSError(13, PRIVATE)
        expected = "unknown"
    else:
        native = OSError(5, PRIVATE)
        setattr(native, "errno", PRIVATE)
        setattr(native, "winerror", True)
        expected = "OSError"
    failure, _client = _failure(monkeypatch, "wheel", native)
    assert guarded_stop_reason(failure, deadline_expired=False) == (
        f"guarded request [stage=wheel,type={expected},errno=none,winerror=none,tls=none]"
    )


def test_unknown_owner_frame_preserves_legacy_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    failure, _client = _failure(monkeypatch, "unknown", TimeoutError(10060, PRIVATE))
    assert guarded_stop_reason(failure, deadline_expired=False) == "guarded feed request failed or timed out"


def test_reason_validation_refuses_unbounded_or_untrusted_fields() -> None:
    reason = (
        "guarded request [stage=project-index,type=SSLCertVerificationError,"
        "errno=-2147483648,winerror=4294967295,tls=4294967295]"
    )
    assert len(reason) <= 128
    assert safe_guarded_request_reason(reason) == reason
    for changed in (
        reason + "\n",
        reason.replace("project-index", "private-origin"),
        reason.replace("SSLCertVerificationError", "PrivateException"),
        reason.replace("-2147483648", "-2147483649"),
        reason.replace("4294967295", "4294967296"),
        reason.replace("4294967295", "True"),
        reason.replace("4294967295", "01"),
        reason.replace("SSLCertVerificationError", "unknown"),
    ):
        assert safe_guarded_request_reason(changed) is None
    assert safe_guarded_request_reason(None) is None


@pytest.mark.parametrize("mode", ["legacy", "request-codes", "late-index"])
def test_existing_worker_writer_and_parent_reader_preserve_refusals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    failure, _client = _failure(monkeypatch, "project-index", TimeoutError(10060, PRIVATE))
    reason = (
        "guarded feed request failed or timed out" if mode == "legacy"
        else guarded_stop_reason(failure, deadline_expired=False)
    )

    def refuse(*_args: object) -> None:
        if mode == "late-index":
            raise failure
        raise GuardedSelectionError(reason)

    control = tmp_path / "request.json"
    control.write_text(json.dumps({
        "version": 1, "operation": "verify" if mode == "late-index" else "resolve",
    }), encoding="utf-8")
    monkeypatch.setattr(guard, "_worker", refuse)
    monkeypatch.setattr(guard, "_verify_worker", refuse)
    guard._worker_main(str(control))
    receipt = capsys.readouterr().out
    expected = "guarded worker failed: WheelError" if mode == "late-index" else reason
    assert json.loads(receipt) == {"status": "refused", "reason": expected}

    def completed(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout=receipt)

    def open_snapshot(path: Path) -> object:
        return path.open("rb")

    monkeypatch.setattr(guard, "run_owned", completed)
    monkeypatch.setattr(guard.snapshots, "_open", open_snapshot)
    parent = tmp_path / "parent"
    parent.mkdir()
    with pytest.raises(GuardedSelectionError) as caught:
        guard._run_isolated(
            {"control_bytes": 0}, parent, {"worker.py": b"pass"},
            _limits(), time.monotonic() + 30, SimpleNamespace(),
        )
    assert str(caught.value) == expected
    assert "private" not in str(caught.value)
