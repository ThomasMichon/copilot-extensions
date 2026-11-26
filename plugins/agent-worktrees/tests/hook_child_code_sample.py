"""Opt-in source diagnostic primitive; never installed or enabled implicitly."""

from __future__ import annotations

import ctypes as ct
import json
import re
import struct
import subprocess
import time
from typing import Any, cast

from hook_child_observer import Sample, WindowsChildSample, windows_error
from hook_child_unwind import Amd64Unwinder, CodeSampleUnavailable


def aligned_context() -> tuple[Any, int]:
    # AMD64 CONTEXT is 1232 bytes and requires 16-byte alignment.
    storage = ct.create_string_buffer(1232 + 15)
    address = (ct.addressof(storage) + 15) & ~15
    ct.c_uint32.from_address(address + 48).value = 0x100003
    return storage, address


class WindowsInstructionSample(WindowsChildSample):
    """Capture bounded code frames; unsupported unwinding is explicit."""

    ATTEMPT_SECONDS = .250
    MAX_READ_BYTES = 65536
    MAX_FRAMES = 16

    def __init__(self, process: subprocess.Popen[Any], *, deadline: float | None = None) -> None:
        from ctypes import wintypes as wt

        self.started = time.monotonic()
        self.deadline = self.started + self.ATTEMPT_SECONDS
        if deadline is not None:
            self.deadline = min(self.deadline, deadline)
        super().__init__(process)
        if ct.sizeof(ct.c_void_p) != 8:
            raise CodeSampleUnavailable("unsupported-collector-architecture")

        class Region(ct.Structure):
            _fields_ = [
                ("base", ct.c_void_p), ("allocation_base", ct.c_void_p),
                ("allocation_protect", wt.DWORD), ("partition", wt.WORD),
                ("size", ct.c_size_t), ("state", wt.DWORD),
                ("protect", wt.DWORD), ("kind", wt.DWORD),
            ]

        self.Region = Region
        signatures: dict[str, tuple[list[Any], Any]] = {
            "GetProcessId": ([wt.HANDLE], wt.DWORD),
            "IsWow64Process2": ([wt.HANDLE, ct.POINTER(wt.WORD), ct.POINTER(wt.WORD)], wt.BOOL),
            "SuspendThread": ([wt.HANDLE], wt.DWORD),
            "ResumeThread": ([wt.HANDLE], wt.DWORD),
            "GetThreadContext": ([wt.HANDLE, ct.c_void_p], wt.BOOL),
            "VirtualQueryEx": ([wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t], ct.c_size_t),
            "K32GetModuleBaseNameW": ([wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD], wt.DWORD),
            "ReadProcessMemory": (
                [wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t)],
                wt.BOOL,
            ),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.suspended: list[int] = []
        self.memory_bytes = 0
        self.stack_allocation = 0
        self.attempted = False
        self.resumed = 0
        self.exited_before_resume = 0
        self.module_signatures: dict[tuple[str, int, int], Sample] = {}
        self.signatures_truncated = False

    def verify_process(self) -> None:
        from ctypes import wintypes as wt

        times = [wt.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(self.handle, *(ct.byref(value) for value in times)):
            raise windows_error()
        birth = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        machine, native = wt.WORD(), wt.WORD()
        if (
            birth <= 0 or self.kernel.GetProcessId(self.handle) != self.pid
            or self.birth != f"windows:{birth}"
            or self.kernel.WaitForSingleObject(self.handle, 0) != 258
        ):
            raise CodeSampleUnavailable("process-identity-unavailable")
        if not self.kernel.IsWow64Process2(self.handle, ct.byref(machine), ct.byref(native)):
            raise windows_error()
        if machine.value != 0 or native.value != 0x8664:
            raise CodeSampleUnavailable("unsupported-target-architecture")

    def code_frame(self, address: int) -> Sample:
        region = self.Region()
        if self.kernel.VirtualQueryEx(
            self.handle, address, ct.byref(region), ct.sizeof(region),
        ) != ct.sizeof(region):
            raise CodeSampleUnavailable("code-range-unavailable")
        if (
            region.state != 0x1000 or region.kind != 0x1000000
            or region.protect & (0x100 | 0x01)
            or region.protect & 0xFF not in (0x10, 0x20, 0x40, 0x80)
            or not region.allocation_base or not region.base
            or not region.allocation_base <= region.base <= address < region.base + region.size
        ):
            raise CodeSampleUnavailable("unsupported-code-range")
        name = ct.create_unicode_buffer(129)
        count = self.kernel.K32GetModuleBaseNameW(
            self.handle, region.allocation_base, name, len(name),
        )
        if not 0 < count < len(name) - 1 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name.value):
            raise CodeSampleUnavailable("module-name-unavailable")
        return {"module": name.value, "code_offset": address - region.allocation_base}

    def read_memory(self, address: int, size: int, *, image_base: int | None = None) -> bytes:
        if (
            not 0 < size <= self.MAX_READ_BYTES - self.memory_bytes
            or time.monotonic() >= self.deadline
        ):
            raise CodeSampleUnavailable("read-or-attempt-budget")
        region = self.Region()
        if self.kernel.VirtualQueryEx(
            self.handle, address, ct.byref(region), ct.sizeof(region),
        ) != ct.sizeof(region):
            raise CodeSampleUnavailable("read-range-unavailable")
        if (
            region.state != 0x1000 or region.protect & (0x100 | 0x01)
            or region.protect & 0xFF not in (0x02, 0x04, 0x08, 0x20, 0x40, 0x80)
            or not region.base or not region.base <= address < address + size <= region.base + region.size
            or image_base is not None
            and (region.kind != 0x1000000 or region.allocation_base != image_base)
            or image_base is None
            and (region.kind != 0x20000 or region.allocation_base != self.stack_allocation)
        ):
            raise CodeSampleUnavailable("unsupported-read-range")
        buffer = ct.create_string_buffer(size)
        received = ct.c_size_t()
        self.memory_bytes += size
        try:
            if not self.kernel.ReadProcessMemory(
                self.handle, address, buffer, size, ct.byref(received),
            ) or received.value != size:
                raise CodeSampleUnavailable("read-unavailable")
            return buffer.raw
        finally:
            ct.memset(buffer, 0, size)

    def thread_frame(self, tid: int, birth: int, deadline: float) -> Sample:
        from ctypes import wintypes as wt

        thread = self.kernel.OpenThread(0x100000 | 0x800 | 0x2 | 0x8, False, tid)
        if not thread:
            raise windows_error()
        self.threads.append(thread)
        times = [wt.FILETIME() for _ in range(4)]
        if not self.kernel.GetThreadTimes(thread, *(ct.byref(value) for value in times)):
            raise windows_error()
        if (
            birth <= 0 or self.kernel.GetProcessIdOfThread(thread) != self.pid
            or (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime != birth
            or self.kernel.WaitForSingleObject(thread, 0) != 258
        ):
            raise CodeSampleUnavailable("thread-identity-unavailable")
        self.verify_process()
        storage, address = aligned_context()
        if time.monotonic() >= deadline:
            raise CodeSampleUnavailable("attempt-budget")
        if self.kernel.SuspendThread(thread) == 0xFFFFFFFF:
            raise windows_error()
        self.suspended.append(thread)
        try:
            if not self.kernel.GetThreadContext(thread, address):
                raise windows_error()
            offset = address - ct.addressof(storage)
            instruction = struct.unpack_from("<Q", storage, offset + 248)[0]
            registers = list(struct.unpack_from("<16Q", storage, offset + 120))
            if time.monotonic() >= deadline:
                raise CodeSampleUnavailable("attempt-budget")
            region = self.Region()
            if self.kernel.VirtualQueryEx(
                self.handle, registers[4], ct.byref(region), ct.sizeof(region),
            ) != ct.sizeof(region) or region.kind != 0x20000:
                raise CodeSampleUnavailable("stack-range-unavailable")
            self.stack_allocation = region.allocation_base
            return self.frames(instruction, registers)
        finally:
            try:
                self.resume(thread)
            finally:
                ct.memset(address, 0, 1232)

    def frames(self, instruction: int, registers: list[int]) -> Sample:
        unwinder = Amd64Unwinder(
            lambda base, address, size: self.read_memory(address, size, image_base=base),
            lambda address, size: self.read_memory(address, size),
        )
        frames: list[Sample] = []
        stop = "frame-cap"
        for index in range(self.MAX_FRAMES):
            try:
                if time.monotonic() >= self.deadline:
                    raise CodeSampleUnavailable("attempt-budget")
                frame = self.code_frame(instruction)
                frames.append(frame)
                base = instruction - cast(int, frame["code_offset"])
                try:
                    pc, updated = unwinder.step(
                        base, instruction if index == 0 else instruction - 1, registers,
                        instruction_frame=index == 0,
                        allow_leaf=str(frame["module"]).lower() in {
                            "ntdll.dll", "kernelbase.dll", "kernel32.dll",
                        },
                    )
                finally:
                    signature = unwinder.signatures.get(base)
                    if signature is not None:
                        key = (str(frame["module"]), *signature)
                        if key in self.module_signatures or len(self.module_signatures) < 32:
                            self.module_signatures[key] = {
                                "module": key[0], "pe_timestamp": key[1], "image_size": key[2],
                            }
                        else:
                            self.signatures_truncated = True
                if not pc:
                    stop = "stack-end"
                    break
                if updated[4] <= registers[4]:
                    raise CodeSampleUnavailable("stack-progress-unavailable")
                instruction, registers = pc, updated
            except CodeSampleUnavailable as error:
                stop = str(error)
                break
        return {"frames": frames, "stop": stop}

    def resume(self, thread: int) -> None:
        if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
            if self.kernel.WaitForSingleObject(thread, 0) != 0:
                raise CodeSampleUnavailable("resume-unconfirmed")
            self.exited_before_resume += 1
        else:
            self.resumed += 1
        self.suspended.remove(thread)

    def sample(self) -> Sample:
        if self.attempted:
            raise CodeSampleUnavailable("attempt-already-used")
        self.attempted = True
        deadline = self.deadline
        self.memory_bytes = 0
        if time.monotonic() >= deadline:
            raise CodeSampleUnavailable("attempt-budget")
        self.verify_process()
        if time.monotonic() >= deadline:
            raise CodeSampleUnavailable("attempt-budget")
        entries = self.thread_entries()
        rows: list[Sample] = []
        for tid, birth, _ in entries[:self.MAX_THREADS]:
            if time.monotonic() >= deadline:
                break
            try:
                rows.append(self.thread_frame(tid, birth, deadline))
            except CodeSampleUnavailable as error:
                rows.append({"unavailable": str(error)})
                if self.suspended:
                    raise
            except OSError:
                rows.append({"unavailable": "native-call"})
        captured = time.monotonic()
        result: Sample = {
            "pid": self.pid, "birth_filetime": int(self.birth.removeprefix("windows:")),
            "threads": rows, "threads_truncated": len(entries) > self.MAX_THREADS,
            "attempt_expired": captured >= deadline,
            "capture_elapsed_ms": round((captured - self.started) * 1000, 3),
            "unwind": "restricted-amd64-v1", "target_memory_bytes_read": self.memory_bytes,
            "threads_resumed": self.resumed, "threads_exited_before_resume": self.exited_before_resume,
            "module_signatures": list(self.module_signatures.values()),
            "module_signatures_truncated": self.signatures_truncated,
        }
        while len(json.dumps(result, ensure_ascii=True).encode("ascii")) > 30720:
            rows.pop()
            result["output_truncated"] = True
        return result

    def close(self) -> None:
        for thread in list(self.suspended):
            self.resume(thread)
        self.stack_allocation = 0
        super().close()
