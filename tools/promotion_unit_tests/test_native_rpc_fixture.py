"""Pure numeric phase clocks; real-source and native contracts stay separate."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


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


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize(
    "phase",
    [
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
