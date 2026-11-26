"""Code-only primitive controls, not portfolio or native-cause acceptance."""

from __future__ import annotations

import ctypes as ct
import json
import os
import struct
import sys
import threading
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_procutil import no_window_kwargs

from hook_child_code_sample import (
    WindowsInstructionSample,
    aligned_context,
)
from hook_child_unwind import Amd64Unwinder, CodeSampleUnavailable
from hook_child_code_observer import CodeChildObserver, CodeSampleClaim
from hook_child_observer import ChildObserver, Sample, observed_subprocess


def sampler(**kernel: Any) -> WindowsInstructionSample:
    value = WindowsInstructionSample.__new__(WindowsInstructionSample)
    value.pid, value.birth, value.handle = 123, "windows:456", 789
    value.kernel = SimpleNamespace(**kernel)
    value.threads, value.suspended = [], []
    value.attempted, value.resumed, value.exited_before_resume = False, 0, 0
    value.started, value.memory_bytes = time.monotonic(), 0
    value.deadline = value.started + 1
    value.module_signatures, value.signatures_truncated = {}, False
    return value


@pytest.mark.guard
def test_context_alignment_flags_and_zeroed_storage() -> None:
    storage, address = aligned_context()
    assert address % 16 == 0
    offset = address - ct.addressof(storage)
    assert ct.c_uint32.from_address(address + 48).value == 0x100003
    assert bytes(storage)[offset + 120:offset + 1232] == bytes(1112)


@pytest.mark.guard
@pytest.mark.parametrize("mismatch", ["pid", "birth", "zero-birth", "exited", "architecture"])
def test_process_refusal_precedes_thread_enumeration(mismatch: str) -> None:
    def times(_handle: int, created: Any, *_rest: Any) -> bool:
        created._obj.dwLowDateTime = 0 if mismatch == "zero-birth" else (
            457 if mismatch == "birth" else 456
        )
        return True

    def architecture(_handle: int, machine: Any, native: Any) -> bool:
        machine._obj.value = 0
        native._obj.value = 0xAA64 if mismatch == "architecture" else 0x8664
        return True

    value = sampler(
        GetProcessTimes=times, GetProcessId=lambda _: 124 if mismatch == "pid" else 123,
        WaitForSingleObject=lambda *_: 0 if mismatch == "exited" else 258,
        IsWow64Process2=architecture,
    )
    if mismatch == "zero-birth":
        value.birth = "windows:0"
    with pytest.raises(CodeSampleUnavailable):
        value.sample()
    assert value.threads == value.suspended == []


@pytest.mark.guard
@pytest.mark.parametrize("mismatch", ["pid", "birth", "zero-birth", "exited"])
def test_thread_refusal_precedes_suspend(mismatch: str) -> None:
    suspended: list[int] = []

    def times(_handle: int, created: Any, *_rest: Any) -> bool:
        created._obj.dwLowDateTime = 0 if mismatch == "zero-birth" else (
            901 if mismatch == "birth" else 900
        )
        return True

    value = sampler(
        OpenThread=lambda *_: 11, GetThreadTimes=times,
        GetProcessIdOfThread=lambda _: 124 if mismatch == "pid" else 123,
        WaitForSingleObject=lambda *_: 0 if mismatch == "exited" else 258,
        SuspendThread=lambda handle: suspended.append(handle),
    )
    with pytest.raises(CodeSampleUnavailable, match="thread-identity"):
        value.thread_frame(22, 0 if mismatch == "zero-birth" else 900, time.monotonic() + 1)
    assert suspended == [] and value.threads == [11]


@pytest.mark.guard
@pytest.mark.parametrize("context_failure", [False, True])
def test_context_exception_always_resumes_and_scrubs(
    context_failure: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resumed: list[int] = []
    storage, address = aligned_context()
    monkeypatch.setattr("hook_child_code_sample.aligned_context", lambda: (storage, address))

    def times(_handle: int, created: Any, *_rest: Any) -> bool:
        created._obj.dwLowDateTime = 900
        return True

    def context(_handle: int, address: int) -> bool:
        ct.memset(address + 120, 0x7F, 128)
        if context_failure:
            raise RuntimeError("private-native-failure")
        return True

    class Region(ct.Structure):
        _fields_ = [("kind", ct.c_uint32), ("allocation_base", ct.c_void_p)]

        def __init__(self) -> None:
            super().__init__()
            self.kind, self.allocation_base = 0x20000, 1

    def resume(handle: int) -> int:
        resumed.append(handle)
        return 1

    value = sampler(
        OpenThread=lambda *_: 11, GetThreadTimes=times,
        GetProcessIdOfThread=lambda _: 123, WaitForSingleObject=lambda *_: 258,
        SuspendThread=lambda _: 0, GetThreadContext=context,
        ResumeThread=resume, VirtualQueryEx=lambda *_: ct.sizeof(Region),
    )
    monkeypatch.setattr(value, "verify_process", lambda: None)
    monkeypatch.setattr(value, "Region", Region, raising=False)

    def frame(_address: int, _registers: list[int]) -> dict[str, object]:
        raise CodeSampleUnavailable("unsupported-code-range")

    monkeypatch.setattr(value, "frames", frame)
    with pytest.raises(RuntimeError):
        value.thread_frame(22, 900, time.monotonic() + 1)
    assert resumed == [11] and value.suspended == []
    offset = address - ct.addressof(storage)
    assert bytes(storage)[offset:offset + 1232] == bytes(1232)


@pytest.mark.guard
@pytest.mark.parametrize("exited", [False, True])
def test_resume_failure_retains_exact_custody_unless_thread_termination_is_confirmed(
    exited: bool,
) -> None:
    value = sampler(
        ResumeThread=lambda _: 0xFFFFFFFF, WaitForSingleObject=lambda *_: 0 if exited else 258,
    )
    value.suspended, value.threads = [11], [11]
    if exited:
        value.resume(11)
        assert value.suspended == [] and value.threads == [11]
        assert value.resumed == 0 and value.exited_before_resume == 1
    else:
        with pytest.raises(CodeSampleUnavailable, match="resume-unconfirmed"):
            value.close()
        assert value.suspended == value.threads == [11]


@pytest.mark.guard
@pytest.mark.parametrize("reason", ["read-cap", "deadline"])
def test_read_budget_refuses_before_native_access(reason: str) -> None:
    value = sampler()
    value.memory_bytes = 65536 if reason == "read-cap" else 0
    value.deadline = 0 if reason == "deadline" else time.monotonic() + 1
    with pytest.raises(CodeSampleUnavailable, match="read-or-attempt-budget"):
        value.read_memory(4096, 8)


@pytest.mark.guard
def test_second_attempt_refuses_before_any_native_access() -> None:
    value = sampler()
    value.attempted = True
    with pytest.raises(CodeSampleUnavailable, match="attempt-already-used"):
        value.sample()


@pytest.mark.guard
@pytest.mark.parametrize("entries,expected", [
    ([(index, 1000 + index, 0) for index in range(16)] + [(99, 900, 0)],
     [99, *range(15)]),
    ([(index, 1000, 0) for index in reversed(range(17))], list(reversed(range(1, 17)))),
    ([(99, 0, 0)] + [(index, 1000 + index, 0) for index in range(16)], list(range(16))),
])
def test_code_selection_prioritizes_oldest_positive_birth_within_same_bounded_entries(
    entries: list[tuple[int, int, int]], expected: list[int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = sampler()
    original = list(entries)
    queries: list[int] = []
    enumerations: list[bool] = []
    monkeypatch.setattr(value, "verify_process", lambda: None)

    def enumerate_threads() -> list[tuple[int, int, int]]:
        enumerations.append(True)
        return entries

    def frame(tid: int, birth: int, deadline: float) -> dict[str, object]:
        assert birth > 0 and deadline == value.deadline
        queries.append(tid)
        return {"frames": []}

    monkeypatch.setattr(value, "thread_entries", enumerate_threads)
    monkeypatch.setattr(value, "thread_frame", frame)
    result = value.sample()
    assert enumerations == [True] and entries == original
    assert queries == expected and len(cast(list[dict[str, object]], result["threads"])) == 16
    assert result["threads_truncated"] is True
    assert result["thread_entries_enumerated"] == 17
    assert result["thread_selection"] == "oldest-positive-enumerated-birth-first"


@pytest.mark.guard
def test_output_cap_counts_the_serialized_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    value = sampler()
    monkeypatch.setattr(value, "verify_process", lambda: None)
    monkeypatch.setattr(value, "thread_entries", lambda: [(index, 1, 0) for index in range(17)])
    monkeypatch.setattr(value, "thread_frame", lambda *_: {
        "frames": [{"module": "a" * 128, "code_offset": 0x7FFFFFFFFFFFFFFF}] * 16,
    })
    result = value.sample()
    assert result["output_truncated"] is True and result["threads_truncated"] is True
    assert len(json.dumps(result, ensure_ascii=True).encode("ascii")) <= 30720


@pytest.mark.guard
@pytest.mark.parametrize("fault", ["guard", "allocation", "kind", "cross-range"])
def test_stack_read_range_refusal_precedes_memory_access(
    fault: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Region(ct.Structure):
        _fields_ = [
            ("base", ct.c_size_t), ("allocation_base", ct.c_size_t),
            ("size", ct.c_size_t), ("state", ct.c_uint32),
            ("protect", ct.c_uint32), ("kind", ct.c_uint32),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.base = self.allocation_base = 4096
            self.size, self.state, self.protect, self.kind = 4096, 0x1000, 4, 0x20000
            if fault == "guard":
                self.protect |= 0x100
            if fault == "allocation":
                self.allocation_base = 2048
            if fault == "kind":
                self.kind = 0x1000000

    value = sampler(VirtualQueryEx=lambda *_: ct.sizeof(Region))
    value.stack_allocation = 4096
    monkeypatch.setattr(value, "Region", Region, raising=False)
    with pytest.raises(CodeSampleUnavailable, match="unsupported-read-range"):
        value.read_memory(4096, 4097 if fault == "cross-range" else 8)
    assert value.memory_bytes == 0


@pytest.mark.guard
def test_worker_claim_is_atomic_and_shared_across_observer_instances(tmp_path: Path) -> None:
    path = tmp_path / "selected"
    first, second = CodeSampleClaim(path), CodeSampleClaim(path)
    barrier = threading.Barrier(2)
    outcomes: list[bool] = []

    def claim(value: CodeSampleClaim) -> None:
        barrier.wait(timeout=2)
        outcomes.append(value.take())

    threads = [threading.Thread(target=claim, args=(value,)) for value in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == [False, True] and path.is_dir()
    assert not CodeSampleClaim(path).take()


@pytest.mark.guard
@pytest.mark.parametrize("ineligible", ["no-deadline", "expired", "exited"])
def test_code_selection_refuses_ineligible_children_before_claiming(
    ineligible: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any) -> Any:
        pytest.fail("An ineligible child must not claim or query")

    process = cast(subprocess.Popen[Any], SimpleNamespace(poll=lambda: 0))
    deadline = None if ineligible == "no-deadline" else (
        0 if ineligible == "expired" else time.monotonic() + 1
    )
    observer = CodeChildObserver(process, deadline, claim=forbidden, factory=forbidden)
    monkeypatch.setattr(observer.stop, "wait", lambda _: False)
    observer.collect()
    result = observer.finish("returned")
    assert result["collector_settled"] is True
    code = cast(dict[str, Any], result["code_observation"])
    assert code["selection"] == "not-eligible" and code["samples"] == []


def unwind_fixture(
    codes: bytes, *, frame: int = 0, instructions: bytes = b"\x90" * 4,
    entry_code: bytes = b"",
) -> tuple[Amd64Unwinder, bytearray, list[int]]:
    image = bytearray(4096)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 60, 128)
    image[128:132] = b"PE\0\0"
    struct.pack_into("<H", image, 132, 0x8664)
    struct.pack_into("<H", image, 148, 240)
    struct.pack_into("<H", image, 152, 0x20B)
    struct.pack_into("<I", image, 208, len(image))
    struct.pack_into("<I", image, 260, 16)
    struct.pack_into("<II", image, 288, 512, 12)
    struct.pack_into("<III", image, 512, 1024, 1536, 768)
    image[768:772] = bytes([1, 32, len(codes) // 2, frame])
    image[772:772 + len(codes)] = codes
    image[1024:1024 + len(entry_code)] = entry_code
    image[1100:1100 + len(instructions)] = instructions
    stack = bytearray(1024)
    registers = [0] * 16
    registers[4] = 8192

    def read_image(base: int, address: int, size: int) -> bytes:
        assert base == 4096 and 4096 <= address < address + size <= 8192
        return bytes(image[address - base:address - base + size])

    def read_stack(address: int, size: int) -> bytes:
        assert 8192 <= address < address + size <= 8192 + len(stack)
        return bytes(stack[address - 8192:address - 8192 + size])

    return Amd64Unwinder(read_image, read_stack), stack, registers


@pytest.mark.guard
@pytest.mark.parametrize("offset,expected_rsp", [(0, 8200), (5, 8208), (76, 8240)])
def test_unwind_partial_prolog_and_reverse_nonvolatile_push(offset: int, expected_rsp: int) -> None:
    unwind, stack, registers = unwind_fixture(bytes([10, 0x32, 5, 0x30]))
    struct.pack_into("<Q", stack, 0, 10001)
    struct.pack_into("<Q", stack, 8, 10002)
    struct.pack_into("<Q", stack, 32, 10003)
    struct.pack_into("<Q", stack, 40, 10004)
    pc, restored = unwind.step(
        4096, 4096 + 1024 + offset, registers, instruction_frame=offset != 0,
        allow_leaf=False,
    )
    assert pc == {0: 10001, 5: 10002, 76: 10004}[offset]
    assert restored[4] == expected_rsp and registers[4] == 8192
    assert restored[3] == ({0: 0, 5: 10001, 76: 10003}[offset])


@pytest.mark.guard
@pytest.mark.parametrize("operation", [4, 5])
@pytest.mark.parametrize("offset", [9, 31])
def test_unwind_frame_register_partial_prolog_refuses_before_stack_reads(
    operation: int, offset: int,
) -> None:
    saved_offset = (4).to_bytes(2, "little") if operation == 4 else (32).to_bytes(4, "little")
    codes = bytes([14, 3, 9, 0x50 | operation]) + saved_offset + bytes([4, 0x42])
    entry = bytes.fromhex("48 83 ec 28 48 89 6c 24 20 48 8d 6c 24 20") + b"\x90" * 19
    unwind, stack, registers = unwind_fixture(codes, frame=0x25, entry_code=entry)
    registers[5] = 8384 if offset < 14 else 8224
    struct.pack_into("<Q", stack, 32, 8384)
    struct.pack_into("<Q", stack, 192, 0xBAD)
    struct.pack_into("<Q", stack, 40, 0x1234)
    reads: list[int] = []
    original = unwind.stack

    def read(address: int, size: int) -> bytes:
        reads.append(address)
        return original(address, size)

    unwind.stack = read
    with pytest.raises(CodeSampleUnavailable, match="partial-frame-register-prolog"):
        unwind.step(
            4096, 4096 + 1024 + offset, registers,
            instruction_frame=True, allow_leaf=False,
        )
    assert reads == [] and registers[4:6] == [8192, 8384 if offset < 14 else 8224]


@pytest.mark.guard
@pytest.mark.parametrize("operation", [4, 5])
def test_unwind_completed_frame_prolog_uses_fixed_base_with_dynamic_rsp(operation: int) -> None:
    saved_offset = (1).to_bytes(2, "little") if operation == 4 else (8).to_bytes(4, "little")
    codes = bytes([15, 0x30 | operation]) + saved_offset + bytes([10, 3, 5, 0x42, 1, 0x50])
    entry = bytes.fromhex("55 48 83 ec 28 48 8d 6c 24 20 48 89 5c 24 08") + b"\x90" * 18
    unwind, stack, registers = unwind_fixture(codes, frame=0x25, entry_code=entry)
    registers[3], registers[5] = 0xAAA, 8288
    struct.pack_into("<Q", stack, 72, 0x2222)
    struct.pack_into("<Q", stack, 104, 0x3333)
    struct.pack_into("<Q", stack, 112, 0x4444)
    pc, restored = unwind.step(
        4096, 4096 + 1024 + 32, registers, instruction_frame=True, allow_leaf=False,
    )
    assert pc == 0x4444 and restored[3:6] == [0x2222, 8312, 0x3333]
    assert registers[3:6] == [0xAAA, 8192, 8288]


@pytest.mark.guard
@pytest.mark.parametrize("code", [bytes([10, 6]), bytes([10, 10]), bytes([10, 1]), bytes([10, 0x40])])
def test_unwind_unknown_machine_frame_truncated_and_volatile_fail_closed(code: bytes) -> None:
    unwind, _stack, registers = unwind_fixture(code)
    with pytest.raises(CodeSampleUnavailable, match="unsupported-unwind-operation"):
        unwind.step(4096, 5196, registers, instruction_frame=True, allow_leaf=False)


@pytest.mark.guard
@pytest.mark.parametrize("instructions,adjustment", [
    (b"\xc3", 0), (b"\xf3\xc3", 0), (b"\x48\xc3", 0), (b"\x5b\xc3", 8),
    (b"\x48\x83\xc4\x20\x5b\xc3", 40), (b"\x48\x81\xc4\x20\0\0\0\x41\x5c\xc3", 40),
])
def test_unwind_actual_epilog_does_not_apply_prolog_twice(
    instructions: bytes, adjustment: int,
) -> None:
    unwind, stack, registers = unwind_fixture(bytes([10, 0x32, 5, 0x30]), instructions=instructions)
    struct.pack_into("<Q", stack, adjustment, 10001)
    pc, restored = unwind.step(4096, 5196, registers, instruction_frame=True, allow_leaf=False)
    assert pc == 10001 and restored[4] == 8192 + adjustment + 8


@pytest.mark.guard
@pytest.mark.parametrize("instructions", [b"\x48\x83\xc4\x20\x90", b"\xff\x25\0\0\0\0", b"\x5c\xc3"])
def test_unwind_partial_or_unsupported_epilog_never_falls_back_to_prolog(instructions: bytes) -> None:
    unwind, _stack, registers = unwind_fixture(bytes([10, 0x32]), instructions=instructions)
    with pytest.raises(CodeSampleUnavailable, match="unsupported-epilog"):
        unwind.step(4096, 5196, registers, instruction_frame=True, allow_leaf=False)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name != "nt", reason="Actual AMD64 context and owned PowerShell child")
def test_native_instruction_frame_is_code_only_and_all_handles_settle(tmp_path: Path) -> None:
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    observations: list[Sample] = []
    natives: list[WindowsInstructionSample] = []
    processes: list[subprocess.Popen[Any]] = []

    def factory(process: subprocess.Popen[Any], deadline: float | None) -> ChildObserver:
        processes.append(process)

        def capture(selected: subprocess.Popen[Any]) -> WindowsInstructionSample:
            assert selected is process and process.poll() is None
            native = WindowsInstructionSample(process, deadline=deadline)
            natives.append(native)
            return native

        return CodeChildObserver(
            process, deadline, claim=CodeSampleClaim(tmp_path / "selected").take, factory=capture,
        )

    proxy = observed_subprocess(observations.append, deadline_factory=factory)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        proxy.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Seconds 30"],
            cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
            **no_window_kwargs(),
        )
    assert caught.value.timeout == 10
    assert len(observations) == len(natives) == len(processes) == 1
    assert observations[0]["collector_settled"] is True and processes[0].poll() is not None
    assert natives[0].suspended == natives[0].threads == []
    assert not natives[0].snapshot and not natives[0].marker
    code = cast(dict[str, Any], observations[0]["code_observation"])
    assert code["selection"] == "selected" and len(code["samples"]) == 1
    result = code["samples"][0]
    post_exit = cast(list[dict[str, Any]], observations[0]["samples"])
    assert result["pid"] == post_exit[0]["pid"] == processes[0].pid
    assert result["birth_filetime"] == post_exit[0]["birth_filetime"]
    assert result["elapsed_ms"] >= 8000 and len(json.dumps(code).encode("ascii")) <= 32768
    rows = cast(list[dict[str, Any]], result["threads"])
    frames = [frame for row in rows for frame in row.get("frames", [])]
    assert frames, result
    assert any(len(row.get("frames", [])) > 1 for row in rows), result
    assert all(set(frame) == {"module", "code_offset"} for frame in frames)
    assert len(rows) <= 16 and len(frames) <= 256
    encoded = json.dumps(result)
    assert len(encoded.encode("utf-8")) <= 32768
    assert str(tmp_path) not in encoded and "Start-Sleep" not in encoded
    assert 0 < cast(int, result["target_memory_bytes_read"]) <= 65536
    signatures = cast(list[dict[str, Any]], result["module_signatures"])
    assert signatures and len(signatures) <= 32
    assert all(set(row) == {"module", "pe_timestamp", "image_size"} for row in signatures)
    print("native_code_sample_control=" + json.dumps({
        "threads": len(rows), "frames": len(frames),
        "read_bytes": result["target_memory_bytes_read"],
        "capture_elapsed_ms": result["capture_elapsed_ms"],
        "module_signatures": len(signatures),
        "threads_resumed": result["threads_resumed"],
        "threads_exited_before_resume": result["threads_exited_before_resume"],
        "stops": [row.get("stop", row.get("unavailable")) for row in rows],
        "handles_closed": True, "child_exited": True, "diagnostic_only": True,
    }, sort_keys=True))


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name != "nt", reason="Actual communicate endtime and kill/drain ordering")
def test_native_code_observer_uses_original_deadline_and_kills_before_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    rows: list[Sample] = []
    observers: list[CodeChildObserver] = []
    passed_deadlines: list[float | None] = []
    original_deadlines: list[float | None] = []
    exited_at_finish: list[bool] = []
    original_communicate = cast(Any, subprocess.Popen)._communicate

    def communicate(
        process: subprocess.Popen[Any], input: Any, endtime: float | None,
        orig_timeout: float | None,
    ) -> Any:
        passed_deadlines.append(endtime)
        return original_communicate(process, input, endtime, orig_timeout)

    monkeypatch.setattr(subprocess.Popen, "_communicate", communicate)

    class Native:
        def sample(self) -> Sample:
            entered.set()
            assert release.wait(3)
            return {"synthetic": True}

        def close(self) -> None:
            pass

    class Observer(CodeChildObserver):
        INITIAL_DELAY = 0.0

        def finish(self, outcome: str) -> Sample:
            exited_at_finish.append(self.process.poll() is not None)
            release.set()
            return super().finish(outcome)

    def factory(process: subprocess.Popen[Any], deadline: float | None) -> ChildObserver:
        original_deadlines.append(deadline)
        observer = Observer(process, deadline, claim=lambda: True, factory=lambda _: Native())
        observers.append(observer)
        return observer

    proxy = observed_subprocess(rows.append, deadline_factory=factory)
    try:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            proxy.run(
                [str(getattr(sys, "_base_executable", sys.executable)),
                 "-c", "import time; time.sleep(60)"],
                capture_output=True, timeout=.25, cwd=tmp_path, **no_window_kwargs(),
            )
        assert caught.value.timeout == .25 and entered.is_set()
        assert len(original_deadlines) == 1 and passed_deadlines[0] == original_deadlines[0]
        assert original_deadlines[0] is not None and exited_at_finish == [True]
        assert rows[0]["collector_settled"] is True
        code = cast(dict[str, Any], rows[0]["code_observation"])
        assert code["selection"] == "selected" and len(code["samples"]) == 1
    finally:
        release.set()
        for observer in observers:
            observer.stop.set()
            observer.worker.join(2)
    assert all(not observer.worker.is_alive() for observer in observers)
