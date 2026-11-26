"""Pure bounded native diagnostics; real-source and native contracts stay separate."""

import importlib.util
from io import StringIO
import json
import mmap
from pathlib import Path
import re
import struct
import subprocess
from types import ModuleType, SimpleNamespace
from typing import Any, NoReturn

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("fault", [
    "none", "partial", "short", "version", "count", "commit", "phase", "frequency",
    "reverse-clock", "private-bytes", "truncated",
])
def test_retained_rpc_numeric_decoder(fixture: ModuleType, fault: str) -> None:
    data = bytearray(4096)
    struct.pack_into("<8Q", data, 0, 0x5250435048415345, 1, 64, 2, 0, 0, 0, 0)
    struct.pack_into("<5Q", data, 64, 2, 100, 200, 1000, 1)
    struct.pack_into("<5Q", data, 104, 3, 110, 210, 1000, 2)
    for selected, offset, value in (
        ("version", 8, 2), ("count", 24, 65), ("commit", 136, 0),
        ("phase", 104, 999), ("frequency", 128, 0), ("reverse-clock", 112, 90),
        ("truncated", 32, 1),
    ):
        if fault == selected:
            struct.pack_into("<Q", data, offset, value)
    if fault == "partial":
        struct.pack_into("<Q", data, 144, 7)
    if fault == "private-bytes":
        data[64:80] = b"private-sentinel"
    result = fixture.decode_retained_phases(bytes(data[:-1] if fault == "short" else data))
    if fault in {"none", "partial", "truncated"}:
        assert result == {
            "status": "partial" if fault == "partial" else "complete",
            "records": [
                {"phase_id": 2, "qpc": 100, "ticks": 200, "frequency": 1000},
                {"phase_id": 3, "qpc": 110, "ticks": 210, "frequency": 1000},
            ],
            "truncated": fault == "truncated", "error": False,
        }
    else:
        assert result == {"status": "unavailable", "records": []}
    assert "private-sentinel" not in json.dumps(result)


def test_retained_rpc_disabled_does_not_allocate(
    fixture: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> NoReturn:
        pytest.fail("Normal fixtures must not allocate a shared region")

    monkeypatch.setattr(mmap, "mmap", forbidden)
    with fixture.retained_rpc_phases(enabled=False, reporter=forbidden) as prefix:
        assert prefix == "" and fixture.retained_rpc_command(prefix, "original") == "original"


class RetainedRegion(bytearray):
    closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def retained_regions(
    fixture: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[RetainedRegion], StringIO]:
    regions: list[RetainedRegion] = []
    stderr = StringIO()

    def allocate(descriptor: int, length: int, *, tagname: str) -> RetainedRegion:
        assert descriptor == -1 and length == 4096
        assert tagname == "Local\\fixture-rpc-" + "a" * 32
        assert all(region.closed for region in regions)
        region = RetainedRegion(length)
        regions.append(region)
        return region

    monkeypatch.setattr(fixture, "mmap", SimpleNamespace(mmap=allocate))
    monkeypatch.setattr(fixture, "sys", SimpleNamespace(platform="win32", stderr=stderr))
    monkeypatch.setattr(fixture, "uuid", SimpleNamespace(
        uuid4=lambda: SimpleNamespace(hex="a" * 32),
    ))
    return regions, stderr


def test_retained_reporter_preserves_each_closed_region_across_capture_resets(
    fixture: ModuleType, retained_regions: tuple[list[RetainedRegion], StringIO],
) -> None:
    regions, stderr = retained_regions
    reports: list[dict[str, object]] = []

    def report(value: dict[str, object]) -> None:
        assert regions[-1].closed
        reports.append(value)

    for index in range(2):
        with fixture.retained_rpc_phases(enabled=True, reporter=report):
            struct.pack_into("<8Q", regions[-1], 0, 0x5250435048415345, 1, 64, 1, 0, 0, 0, 0)
            struct.pack_into("<5Q", regions[-1], 64, 2, 100 + index, 200, 1000, 1)
        stderr.seek(0)
        stderr.truncate()
    assert len(reports) == 2 and len(regions) == 2
    for index, value in enumerate(reports):
        assert value == {
            "status": "complete", "records": [
                {"phase_id": 2, "qpc": 100 + index, "ticks": 200, "frequency": 1000},
            ],
            "truncated": False, "error": False, "region_closed": True,
            "diagnostic_only": True,
        }


@pytest.mark.parametrize("raises", [False, True])
def test_retained_reporter_failure_keeps_original_outcome_and_stderr_fallback(
    fixture: ModuleType, retained_regions: tuple[list[RetainedRegion], StringIO],
    raises: bool,
) -> None:
    regions, stderr = retained_regions
    original = subprocess.TimeoutExpired(["synthetic-private-argument"], 10)

    def report(value: dict[str, object]) -> NoReturn:
        assert regions[-1].closed
        raise RuntimeError("synthetic-private-reporter-failure")

    def invoke() -> None:
        with fixture.retained_rpc_phases(enabled=True, reporter=report):
            if raises:
                raise original

    if raises:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            invoke()
        assert caught.value is original
    else:
        invoke()
    value = json.loads(stderr.getvalue().removeprefix("retained_rpc_phases="))
    assert value["region_closed"] is True and value["status"] == "complete"
    assert all(region.closed for region in regions)
    assert "synthetic-private" not in stderr.getvalue()


@pytest.fixture
def fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "promotion_native_rpc_fixture",
        ROOT / "plugins/agent-worktrees/tests/native_rpc_fixture.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("enabled", [False, True])
def test_policy_transport_capture_is_explicitly_opt_in(
    fixture: ModuleType, monkeypatch: pytest.MonkeyPatch, enabled: bool,
) -> None:
    calls: list[str] = []

    def instrument(source: str) -> str:
        calls.append(source)
        return "synthetic-instrumented-transport"

    monkeypatch.setattr(fixture, "instrument_native_transport", instrument)
    source = "synthetic-unmodified-transport"
    result = fixture.diagnostic_policy_transport(source, enabled=enabled)
    if enabled:
        assert calls == [source] and result.endswith("synthetic-instrumented-transport")
        assert fixture.PHASE_BUFFER_WRITER in result
        assert "GetCurrentProcess" not in result
    else:
        assert calls == [] and result is source


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize(
    "phase",
    [
        "rpc-encode-start",
        "rpc-socket-start",
        "rpc-connect-returned",
        "json-entry",
        "rpc-connect-call-returned",
        "rpc-connect-catch",
        "rpc-connect-unwrapped",
    ],
)
def test_buffered_rpc_clocks_roundtrip_without_cpu_relabeling(
    fixture: ModuleType,
    newline: bytes,
    phase: str,
) -> None:
    payload = f"12 {phase} qpc=123 frequency=1000"
    assert (
        fixture.rpc_clock_phase(
            b"NATIVE_WRITE_FIXTURE_PHASE " + payload.encode("ascii") + newline
        )
        == payload
    )


@pytest.mark.parametrize(
    "record",
    [
        b"12 command-entry qpc=123 frequency=1000",
        b"12 rpc-connected qpc=0 frequency=1000",
        b"12 rpc-connected qpc=123 frequency=0",
        b"12 rpc-connected qpc=123 frequency=1000 cpu_ticks=9",
        b"12 rpc-connected qpc=123456789012345678901 frequency=1000",
    ],
)
def test_buffered_rpc_clock_rejects_unavailable_or_unrecognized_data(
    fixture: ModuleType,
    record: bytes,
) -> None:
    assert (
        fixture.rpc_clock_phase(b"NATIVE_WRITE_FIXTURE_PHASE " + record + b"\n") is None
    )


@pytest.fixture
def reporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "promotion_native_failure_report",
        ROOT / "plugins/agent-worktrees/_native_failure_report_pytest.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_timeout_marker_reader_keeps_existing_evidence_without_payloads(
    reporter: ModuleType,
) -> None:
    stderr = (
        b"synthetic-private-stderr\nNATURAL_SHELL_BUILD 5.1.0\n"
        b"NATURAL_POLICY_START\nNATURAL_POLICY_COMPLETE-extra\n"
    )
    error = subprocess.TimeoutExpired(
        ["synthetic-command-argument"],
        10,
        output=b"synthetic-private-stdout",
        stderr=stderr,
    )
    markers = reporter._timeout_stderr_markers(error)
    assert markers == {
        "available": True,
        "truncated": False,
        "shell_build": True,
        "policy_start": True,
        "policy_complete": False,
    }
    assert "synthetic-private" not in json.dumps(markers)
    assert "synthetic-command" not in json.dumps(markers)
    assert error.stderr is stderr
    assert reporter._timeout_stderr_markers(AssertionError("synthetic")) is None


def test_registered_write_helper_keeps_numeric_context_without_payloads(
    reporter: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Poison:
        def __repr__(self) -> str:
            raise AssertionError("must not represent payloads")

    path = ROOT / "plugins/agent-worktrees/tests/test_managed_session.py"
    error = AssertionError("synthetic-private-failure")
    namespace: dict[str, object] = {"error": error, "private": Poison()}
    # Keep an owned synthetic traceback without importing the native fixture.
    exec(compile("""
def original():
    def qualify_registered_file_local_write():
        started = 10.0
        started_ms = 10000
        observed = [private]
        launch = {"cpu_ticks": 1250, "private": private}
        raise error
    qualify_registered_file_local_write()
""", str(path), "exec"), namespace)
    original = namespace["original"]
    assert callable(original)
    call = pytest.CallInfo.from_call(original, "call")
    monkeypatch.setattr(reporter, "time", SimpleNamespace(
        monotonic=lambda: 20.0, time_ns=lambda: 20_001_000_000,
    ))
    body = reporter.failure_context(SimpleNamespace(function=original, path=path), call)
    assert call.excinfo is not None and call.excinfo.value is error
    assert body["contexts"] == [
        {"role": "test"},
        {
            "role": "registered-write-helper", "started": 10.0, "started_ms": 10_000,
            "observed_count": 1, "launch": {"cpu_ticks": 1250},
            "elapsed_at_report_ms": 10_000.0, "utc_elapsed_at_report_ms": 10_001.0,
            "utc_minus_monotonic_elapsed_ms": 1.0,
        },
    ]
    assert "private" not in json.dumps(body)


@pytest.mark.parametrize(
    ("stderr", "available", "truncated", "started"),
    [
        (None, False, False, False),
        ("NATURAL_POLICY_START\n" + "x" * 16384, True, True, True),
        (
            b"x" * (16384 - len(b"NATURAL_POLICY_START") - 1)
            + b"\nNATURAL_POLICY_START-not-a-marker\n",
            True,
            True,
            False,
        ),
    ],
    ids=["unavailable", "bounded-text", "split-marker"],
)
def test_timeout_marker_reader_bounds_retained_lines(
    reporter: ModuleType,
    stderr: str | bytes | None,
    available: bool,
    truncated: bool,
    started: bool,
) -> None:
    markers = reporter._timeout_stderr_markers(
        subprocess.TimeoutExpired(["synthetic"], 10, stderr=stderr)
    )
    assert markers == {
        "available": available,
        "truncated": truncated,
        "shell_build": False,
        "policy_start": started,
        "policy_complete": False,
    }


def test_completed_marker_reader_keeps_existing_evidence_without_payloads(
    reporter: ModuleType,
) -> None:
    stderr = (
        b"synthetic-private-stderr\nNATURAL_SHELL_BUILD 5.1.0\n"
        b"NATURAL_POLICY_START\nNATURAL_POLICY_COMPLETE-extra\n"
    )
    result: subprocess.CompletedProcess[object] = subprocess.CompletedProcess(
        ["synthetic-command-argument"], 0,
        stdout=b"synthetic-private-stdout", stderr=stderr,
    )
    markers = reporter._completed_stderr_markers(result)
    assert markers == {
        "available": True,
        "truncated": False,
        "shell_build": True,
        "policy_start": True,
        "policy_complete": False,
    }
    assert "synthetic-private" not in json.dumps(markers)
    assert "synthetic-command" not in json.dumps(markers)
    assert result.stderr is stderr

    class Poison:
        @property
        def stderr(self) -> bytes:
            raise AssertionError("must not inspect arbitrary holders")

    assert reporter._completed_stderr_markers(Poison()) is None
    result.stderr = Poison()
    assert reporter._completed_stderr_markers(result)["available"] is False


@pytest.mark.parametrize(
    ("stderr", "available", "truncated", "started"),
    [
        (None, False, False, False),
        ("NATURAL_POLICY_START\n" + "x" * 16384, True, True, True),
        (
            b"x" * (16384 - len(b"NATURAL_POLICY_START") - 1)
            + b"\nNATURAL_POLICY_START-not-a-marker\n",
            True,
            True,
            False,
        ),
    ],
    ids=["unavailable", "bounded-text", "split-marker"],
)
def test_completed_marker_reader_bounds_retained_lines(
    reporter: ModuleType,
    stderr: str | bytes | None,
    available: bool,
    truncated: bool,
    started: bool,
) -> None:
    markers = reporter._completed_stderr_markers(
        subprocess.CompletedProcess(["synthetic"], 0, stderr=stderr)
    )
    assert markers == {
        "available": available,
        "truncated": truncated,
        "shell_build": False,
        "policy_start": started,
        "policy_complete": False,
    }


@pytest.fixture
def bootstrap_source() -> str:
    return (ROOT / "plugins/agent-worktrees/scripts/pre-tool-hook.ps1").read_text(
        encoding="utf-8",
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_bootstrap_phase_emitter_preserves_original_script(
    fixture: ModuleType, bootstrap_source: str, newline: str,
) -> None:
    source = bootstrap_source.replace("\n", newline)
    generated = fixture.diagnostic_policy_bootstrap(source, enabled=True)
    markers = re.findall(r"^    \[void\]\(Add-NativeWriteFixturePhase '([^']+)'\)", generated, re.M)
    assert markers == [
        "pretool-script-entry", "pretool-directory-complete", "pretool-stdin-complete",
        "pretool-admission-loaded", "pretool-membership-start", "pretool-membership-complete",
        "pretool-event-json-complete", "pretool-native-proof-complete", "pretool-posttool-loaded",
    ]
    stripped = re.sub(
        r"^    \[void\]\(Add-NativeWriteFixturePhase 'pretool-[^']+'\)\r?\n",
        "", generated, flags=re.M,
    )
    assert stripped == source
    assert "\n" not in generated.replace(newline, "")
    with pytest.raises(AssertionError):
        fixture.diagnostic_policy_bootstrap(generated, enabled=True)


def test_bootstrap_phase_emitter_is_explicitly_disabled(fixture: ModuleType) -> None:
    source = "unmodified source without any diagnostic anchors"
    assert fixture.diagnostic_policy_bootstrap(source, enabled=False) is source


@pytest.mark.parametrize("fault", ["missing", "duplicate"])
def test_bootstrap_phase_emitter_refuses_unknown_source(
    fixture: ModuleType, bootstrap_source: str, fault: str,
) -> None:
    source = "" if fault == "missing" else bootstrap_source * 2
    with pytest.raises(AssertionError):
        fixture.diagnostic_policy_bootstrap(source, enabled=True)


def test_bootstrap_clock_decoder_preserves_historical_ids(fixture: ModuleType) -> None:
    data = bytearray(4096)
    struct.pack_into("<8Q", data, 0, 0x5250435048415345, 1, 64, 2, 0, 0, 0, 0)
    struct.pack_into("<5Q", data, 64, 18, 100, 200, 1000, 1)
    struct.pack_into("<5Q", data, 104, 36, 110, 210, 1000, 2)
    result = fixture.decode_retained_phases(bytes(data))
    assert result["status"] == "complete" and not result["error"] and not result["truncated"]
    assert [
        fixture._BUFFERED_PHASES[row["phase_id"]] for row in result["records"]
    ] == ["json-entry", "pretool-posttool-loaded"]
    assert fixture.rpc_clock_phase(
        b"NATIVE_WRITE_FIXTURE_PHASE 210 pretool-posttool-loaded qpc=110 frequency=1000\n",
    ) == "210 pretool-posttool-loaded qpc=110 frequency=1000"


def test_bootstrap_command_initializes_and_clears_the_same_buffer(fixture: ModuleType) -> None:
    prefix, command = "existing-region-and-writer\n", "Invoke-OriginalCommand"
    legacy = (
        prefix + command
        + "\n} finally { if ($null -ne $global:nativeWriteRpcShared) { $global:nativeWriteRpcShared.Dispose() }; "
        "$global:nativeWriteRpcShared = $null; $fixturePhaseRegion.Dispose() }\n"
    )
    assert fixture.retained_rpc_command(prefix, command) == legacy
    assert fixture.retained_rpc_command(prefix, command, bootstrap=False) == legacy
    generated = fixture.retained_rpc_command(prefix, command, bootstrap=True)
    assert generated.startswith(prefix)
    assert generated.index("Shared = $global:nativeWriteRpcShared") < generated.index(command)
    assert generated.index("Add-NativeWriteFixturePhase 'bootstrap-capture-ready'") < generated.index(command)
    assert (
        generated.index(command)
        < generated.index("$global:nativeWriteRpcCapture = $null")
        < generated.index("$global:nativeWriteRpcShared.Dispose()")
    )
    assert generated.count("Add-NativeWriteFixturePhase '") == 1
    assert "[Diagnostics.Process]" not in generated and "Start-Sleep" not in generated


def test_bootstrap_command_requires_the_existing_region(fixture: ModuleType) -> None:
    assert fixture.retained_rpc_command("", "original", bootstrap=False) == "original"
    with pytest.raises(ValueError, match="region"):
        fixture.retained_rpc_command("", "original", bootstrap=True)


@pytest.mark.parametrize(("path", "allowed"), [
    ("plugins/agent-worktrees/scripts/pre-tool-hook.ps1", True),
    ("plugins/agent-worktrees/scripts/post-tool-hook.ps1", False),
], ids=["bootstrap-source", "neighbor-unadmitted"])
def test_bootstrap_source_admission_is_explicit(path: str, allowed: bool) -> None:
    from promotion_unit_contract import UnitEvidenceError, relative_file

    if allowed:
        assert relative_file(path) == path
    else:
        with pytest.raises(UnitEvidenceError, match="explicit build-data"):
            relative_file(path)
