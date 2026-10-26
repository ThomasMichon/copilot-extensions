"""Shared sync ordering and caller refusal; no native durability qualification."""

from __future__ import annotations

import copy
import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Iterator

import pytest

from process_telemetry import append, diagnostic_logs as logs

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
EVENT = {"ts": NOW.isoformat(), "operation_id": "synthetic", "stage": "before_effect"}


def _synthetic_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (
        ("O_DIRECTORY", 0x10000), ("O_NOFOLLOW", 0x20000), ("O_NONBLOCK", 0x40000),
    ):
        monkeypatch.setattr(append.os, name, getattr(append.os, name, value), raising=False)


@pytest.fixture
def syscalls(monkeypatch: pytest.MonkeyPatch) -> list:
    calls = []
    monkeypatch.setattr(append, "strict_write_capability", lambda: "synthetic-sync")
    _synthetic_flags(monkeypatch)
    def opened(path, flags, mode=0o777, *, dir_fd=None) -> int:
        calls.append(("open", path, flags, mode, dir_fd))
        return 10 if dir_fd is None else 11

    monkeypatch.setattr(append.os, "open", opened)
    monkeypatch.setattr(append.os, "fstat", lambda fd: SimpleNamespace(
        st_mode=stat.S_IFREG, st_nlink=1,
    ))
    monkeypatch.setattr(append.os, "write", lambda fd, data: (
        calls.append(("write", fd, data)) or len(data)
    ))
    monkeypatch.setattr(append.os, "fsync", lambda fd: calls.append(("sync", fd)), raising=False)
    monkeypatch.setattr(append.os, "close", lambda fd: calls.append(("close", fd)))
    return calls


def test_strict_append_orders_single_write_file_sync_and_directory_sync(syscalls: list) -> None:
    append.append_bytes_strict("/synthetic/existing/event.jsonl", b"{}\n")
    assert [call[0] for call in syscalls] == [
        "open", "open", "write", "sync", "close", "sync", "close",
    ]
    assert syscalls[1][1] == "event.jsonl" and syscalls[1][-1] == 10
    flags = syscalls[1][2]
    assert flags & os.O_APPEND and flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK
    assert syscalls[1][3] == 0o600
    assert [call[1] for call in syscalls if call[0] == "sync"] == [11, 10]


def test_synthetic_protocol_supplies_all_missing_native_flags(
    syscalls: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"):
        monkeypatch.delattr(append.os, name)
    _synthetic_flags(monkeypatch)
    append.append_bytes_strict("/synthetic/existing/event.jsonl", b"{}\n")
    assert [call[1] for call in syscalls if call[0] == "sync"] == [11, 10]


@pytest.mark.parametrize("boundary", ["file_open", "short", "write", "file_sync", "directory_sync"])
def test_strict_append_failure_propagates_without_retry(
    syscalls: list, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    if boundary == "file_open":
        opened = append.os.open

        def fail_open(path, flags, mode=0o777, *, dir_fd=None) -> int:
            if dir_fd is not None:
                raise OSError("synthetic open failure")
            return opened(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(append.os, "open", fail_open)
    elif boundary in ("short", "write"):
        def fail_write(fd: int, data: bytes) -> int:
            syscalls.append(("write", fd, data))
            if boundary == "write":
                raise OSError("synthetic write failure")
            return len(data) - 1

        monkeypatch.setattr(append.os, "write", fail_write)
    else:
        def fail_sync(fd: int) -> None:
            syscalls.append(("sync", fd))
            if fd == (11 if boundary == "file_sync" else 10):
                raise OSError("synthetic sync failure")

        monkeypatch.setattr(append.os, "fsync", fail_sync)
    with pytest.raises(OSError):
        append.append_bytes_strict("/synthetic/existing/event.jsonl", b"{}\n")
    assert len([call for call in syscalls if call[0] == "write"]) <= 1
    assert [call[1] for call in syscalls if call[0] == "close"] == (
        [10] if boundary == "file_open" else [11, 10]
    )


@pytest.mark.parametrize("failed_fd", [10, 11])
def test_strict_close_failure_refuses_after_releasing_other_descriptor(
    syscalls: list, monkeypatch: pytest.MonkeyPatch, failed_fd: int,
) -> None:
    def fail_close(fd: int) -> None:
        syscalls.append(("close", fd))
        if fd == failed_fd:
            raise OSError("synthetic close failure")

    monkeypatch.setattr(append.os, "close", fail_close)
    with pytest.raises(OSError, match="close failure"):
        append.append_bytes_strict("/synthetic/existing/event.jsonl", b"{}\n")
    assert [call[1] for call in syscalls if call[0] == "close"] == [11, 10]


@pytest.mark.parametrize("mode,links", [(stat.S_IFIFO, 1), (stat.S_IFREG, 2)])
def test_strict_append_refuses_nonordinary_or_linked_files(
    syscalls: list, monkeypatch: pytest.MonkeyPatch, mode: int, links: int,
) -> None:
    monkeypatch.setattr(append.os, "fstat", lambda fd: SimpleNamespace(
        st_mode=mode, st_nlink=links,
    ))
    with pytest.raises(OSError, match="plain file"):
        append.append_bytes_strict("/synthetic/existing/event.jsonl", b"{}\n")
    assert not any(call[0] == "write" for call in syscalls)


@pytest.mark.parametrize("data", [b"", b"x" * 65537, "not-bytes"])
def test_strict_append_bounds_before_open(syscalls: list, data: bytes) -> None:
    with pytest.raises(ValueError):
        append.append_bytes_strict("/synthetic/existing/event.jsonl", data)
    assert not syscalls


def test_strict_append_exact_encoded_limit(syscalls: list) -> None:
    append.append_bytes_strict("/synthetic/existing/event.jsonl", b"x" * 65536)
    assert len(next(call[2] for call in syscalls if call[0] == "write")) == 65536


def test_capability_is_platform_not_package_or_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(append.sys, "platform", "win32")
    assert append.strict_write_capability() is None
    monkeypatch.setattr(append.sys, "platform", "linux")
    _synthetic_flags(monkeypatch)
    monkeypatch.setattr(append.os, "fsync", lambda fd: None, raising=False)
    assert append.strict_write_capability() == "linux-file-directory-fsync-v1"
    monkeypatch.delattr(append.os, "fsync")
    assert append.strict_write_capability() is None
    monkeypatch.setattr(append.os, "fsync", lambda fd: None, raising=False)
    monkeypatch.delattr(append.os, "O_NONBLOCK")
    assert append.strict_write_capability() is None


def test_unsupported_append_does_not_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(append, "strict_write_capability", lambda: None)
    monkeypatch.setattr(append.os, "open", lambda *args, **kwargs: pytest.fail("opened"))
    with pytest.raises(NotImplementedError):
        append.append_bytes_strict("/synthetic/event.jsonl", b"{}\n")


@pytest.fixture
def writer(tmp_path, monkeypatch: pytest.MonkeyPatch) -> tuple[logs.DiagnosticLog, list]:
    calls = []

    @contextmanager
    def custody(path) -> Iterator[None]:
        calls.append("acquire")
        try:
            yield
        finally:
            calls.append("release")

    result = logs.DiagnosticLog(
        "synthetic", tmp_path, service="synthetic", custody=custody,
        initial_maintenance=False, clock=lambda: NOW,
    )
    monkeypatch.setattr(logs, "strict_write_capability", lambda: "synthetic-sync")
    monkeypatch.setattr(result, "_rollover", lambda now: calls.append("rollover"))
    monkeypatch.setattr(logs, "append_bytes_strict", lambda path, data: calls.append(
        ("strict_append", json.loads(data))
    ))
    return result, calls


def test_caller_effect_occurs_only_after_sync_and_custody_release(writer: tuple) -> None:
    store, calls = writer
    store.write_event_strict(EVENT)
    calls.append("backend")
    assert calls == ["acquire", "rollover", ("strict_append", EVENT), "release", "backend"]


@pytest.mark.parametrize("boundary", ["custody", "rollover", "append"])
def test_strict_failure_prevents_caller_effect_and_reports_degradation(
    writer: tuple, monkeypatch: pytest.MonkeyPatch, capsys, boundary: str,
) -> None:
    store, calls = writer

    def fail(*args) -> None:
        raise OSError("synthetic storage failure")

    if boundary == "custody":
        monkeypatch.setattr(store, "_custody_factory", fail)
    elif boundary == "rollover":
        monkeypatch.setattr(store, "_rollover", fail)
    else:
        monkeypatch.setattr(logs, "append_bytes_strict", fail)
    with pytest.raises(OSError):
        store.write_event_strict(EVENT)
        calls.append("backend")
    assert "backend" not in calls
    assert store.last_error == "OSError" and "diagnostics degraded" in capsys.readouterr().err


@pytest.mark.parametrize("invalid", ["expired", "missing", "naive", "large", "multiline",
                                   "carriage_return", "invalid_utf8", "nan", "changed", "array", "absent_directory",
                                   "unsupported"])
def test_strict_refusal_happens_before_custody(
    writer: tuple, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    store, calls = writer
    event = dict(EVENT)
    if invalid == "expired":
        event["ts"] = (NOW - timedelta(days=30, microseconds=1)).isoformat()
    elif invalid == "missing":
        event.pop("ts")
    elif invalid == "naive":
        event["ts"] = NOW.replace(tzinfo=None).isoformat()
    elif invalid == "absent_directory":
        store.directory = store.directory / "absent"
    elif invalid == "unsupported":
        monkeypatch.setattr(logs, "strict_write_capability", lambda: None)
    else:
        encoded = {
            "large": b"x" * 65537, "multiline": b"{}\n{}\n",
            "carriage_return": b"{}\r{}\n",
            "invalid_utf8": b"\xff\n", "nan": b'{"ts":NaN}\n',
            "changed": b'{"ts":"2026-10-26T00:00:00+00:00"}\n', "array": b"[]\n",
        }[invalid]
        store.encoder = lambda event: encoded
    with pytest.raises((ValueError, FileNotFoundError, NotImplementedError)):
        store.write_event_strict(event)
        calls.append("backend")
    assert not calls


def test_cutoff_equality_is_written_and_ordinary_failure_stays_fail_open(
    writer: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, calls = writer
    event = dict(EVENT, ts=(NOW - timedelta(days=30)).isoformat())
    store.write_event_strict(event)
    assert ("strict_append", event) in calls

    def fail(*args) -> None:
        raise OSError("synthetic ordinary failure")

    monkeypatch.setattr(logs, "append_bytes", fail)
    store.write_event(EVENT)
    calls.append("ordinary_backend")
    assert calls[-1] == "ordinary_backend" and store.last_error == "OSError"


def test_strict_custody_never_creates_directory(writer: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _ = writer
    monkeypatch.setattr(type(store.directory), "mkdir", lambda *args, **kwargs: (
        pytest.fail("strict write attempted automatic directory creation")
    ))
    store.write_event_strict(EVENT)


def test_strict_json_exact_byte_limit_and_multibyte_oversize(writer: tuple) -> None:
    store, calls = writer
    event = dict(EVENT, text="")
    overhead = len(store.encoder(event))
    event["text"] = "x" * (65536 - overhead)
    assert len(store.encoder(event)) == 65536
    store.write_event_strict(event)
    calls.clear()
    event["text"] += "\u00e9"
    assert len(store.encoder(event)) == 65538
    with pytest.raises(ValueError):
        store.write_event_strict(event)
    assert not calls


def test_ordinary_expired_record_still_skips_without_strict_io(writer: tuple) -> None:
    store, calls = writer
    store.write_event(dict(EVENT, ts=(NOW - timedelta(days=31)).isoformat()))
    assert not calls and store.last_error is None


@pytest.mark.parametrize("value,encoded", [
    (1.0, "1e400"), (-1.0, "-1e400"),
    ({"nested": [1.0]}, '{"nested":[1e400]}'),
    ({"nested": [-1.0]}, '{"nested":[-1e400]}'),
])
def test_strict_numeric_overflow_refuses_before_custody(
    writer: tuple, value: object, encoded: str,
) -> None:
    store, calls = writer
    event = dict(EVENT, value=value)
    store.encoder = lambda event: (
        json.dumps(EVENT)[:-1] + ',"value":' + encoded + "}\n"
    ).encode()
    with pytest.raises(ValueError, match="not finite"):
        store.write_event_strict(event)
    assert not calls


@pytest.mark.parametrize("value,replacement", [
    (1, True), (True, 1), (0, False), (False, 0),
    (1.0, 1), (1, 1.0), ({"nested": [1]}, {"nested": [True]}),
    ([{"nested": False}], [{"nested": 0}]),
    ((1, 2), [1, 2]),
])
def test_strict_type_substitution_refuses_at_every_depth(
    writer: tuple, value: object, replacement: object,
) -> None:
    store, calls = writer
    event = dict(EVENT, value=value)
    store.encoder = lambda event: (
        json.dumps(dict(event, value=replacement)) + "\n"
    ).encode()
    with pytest.raises(ValueError, match="changed the record fields|primitive JSON fields"):
        store.write_event_strict(event)
    assert not calls


@pytest.mark.parametrize("fragment", [
    '"value":1,"value":1', '"value":{"nested":1,"nested":1}',
])
def test_strict_duplicate_fields_are_not_ambiguous_preservation(
    writer: tuple, fragment: str,
) -> None:
    store, calls = writer
    event = dict(EVENT, value=1 if "nested" not in fragment else {"nested": 1})
    store.encoder = lambda event: (json.dumps(EVENT)[:-1] + "," + fragment + "}\n").encode()
    with pytest.raises(ValueError, match="duplicate fields"):
        store.write_event_strict(event)
    assert not calls


def test_strict_nested_finite_json_fields_preserve_types(writer: tuple) -> None:
    store, calls = writer
    event = dict(EVENT, value=[None, False, 0, 1.0, "\u00e9", {"items": [True, -2.5]}])
    store.write_event_strict(event)
    assert ("strict_append", event) in calls


@pytest.mark.parametrize("change", ["remove_operation", "expire_timestamp", "nested_field", "nested_list"])
def test_strict_encoder_mutation_cannot_change_validation_baseline(
    writer: tuple, change: str,
) -> None:
    store, calls = writer
    event = dict(EVENT, nested={"operation_id": "nested", "items": [{"attempt": 1}]})
    baseline = copy.deepcopy(event)

    def mutating_encoder(record: dict) -> bytes:
        assert record is not event and record["nested"] is not event["nested"]
        if change == "remove_operation":
            record.pop("operation_id")
        elif change == "expire_timestamp":
            record["ts"] = (NOW - timedelta(days=31)).isoformat()
        elif change == "nested_field":
            record["nested"].pop("operation_id")
        else:
            record["nested"]["items"][0]["attempt"] = True
        return (json.dumps(record) + "\n").encode()

    store.encoder = mutating_encoder
    with pytest.raises(ValueError, match="changed the record fields"):
        store.write_event_strict(event)
        calls.append("backend")
    assert not calls and event == baseline


def test_strict_decoder_also_receives_an_independent_snapshot(writer: tuple) -> None:
    store, calls = writer
    event = dict(EVENT, nested={"operation_id": "nested"})
    baseline = copy.deepcopy(event)

    def mutating_decoder(record: dict) -> datetime:
        result = datetime.fromisoformat(record["ts"])
        record.pop("operation_id")
        record["nested"].clear()
        return result

    store.timestamp_decoder = mutating_decoder
    store.write_event_strict(event)
    assert ("strict_append", baseline) in calls and event == baseline


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"),
                                   {"nested": [float("inf")]}])
def test_nonfinite_input_refuses_before_encoder(writer: tuple, value: object) -> None:
    store, calls = writer
    store.encoder = lambda event: pytest.fail("encoded nonfinite input")
    with pytest.raises(ValueError, match="finite primitive JSON fields"):
        store.write_event_strict(dict(EVENT, value=value))
    assert not calls


def test_unsupported_input_never_invokes_a_custom_deepcopy_hook(writer: tuple) -> None:
    store, calls = writer

    class Unsupported:
        def __deepcopy__(self, memo: dict) -> object:
            pytest.fail("invoked an unsupported object hook")

    with pytest.raises(ValueError, match="primitive JSON fields"):
        store.write_event_strict(dict(EVENT, value=Unsupported()))
    assert not calls
