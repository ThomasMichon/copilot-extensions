"""Restricted AMD64 PE unwind interpreter with caller-owned bounded reads."""

from __future__ import annotations

import struct
from collections.abc import Callable


class CodeSampleUnavailable(RuntimeError):
    pass


ReadImage = Callable[[int, int, int], bytes]
ReadStack = Callable[[int, int], bytes]
NONVOLATILE = frozenset({3, 5, 6, 7, 12, 13, 14, 15})


class Amd64Unwinder:
    def __init__(self, image: ReadImage, stack: ReadStack) -> None:
        self.image, self.stack = image, stack
        self.signatures: dict[int, tuple[int, int]] = {}

    def function(self, base: int, pc: int) -> tuple[int, int, int] | None:
        dos = self.image(base, base, 64)
        if dos[:2] != b"MZ":
            raise CodeSampleUnavailable("unsupported-image")
        nt_offset = struct.unpack_from("<I", dos, 60)[0]
        if not 64 <= nt_offset <= 1024 * 1024 or nt_offset % 4:
            raise CodeSampleUnavailable("unsupported-image")
        nt = self.image(base, base + nt_offset, 168)
        if (
            nt[:4] != b"PE\0\0" or struct.unpack_from("<H", nt, 4)[0] != 0x8664
            or struct.unpack_from("<H", nt, 20)[0] < 144
            or struct.unpack_from("<H", nt, 24)[0] != 0x20B
            or struct.unpack_from("<I", nt, 132)[0] < 4
        ):
            raise CodeSampleUnavailable("unsupported-image")
        image_size = struct.unpack_from("<I", nt, 80)[0]
        if not 0 <= pc - base < image_size:
            raise CodeSampleUnavailable("unsupported-image-range")
        self.signatures[base] = (int(struct.unpack_from("<I", nt, 8)[0]), image_size)
        table, size = struct.unpack_from("<II", nt, 160)
        if not table or table % 4 or size % 12:
            raise CodeSampleUnavailable("unwind-table-unavailable")
        if not 12 <= size <= 12 * 1024 * 1024 or table + size > image_size:
            raise CodeSampleUnavailable("unsupported-unwind-table")
        low, high = 0, size // 12
        while low < high:
            middle = (low + high) // 2
            entry = struct.unpack("<III", self.image(base, base + table + middle * 12, 12))
            begin, end, unwind = entry
            if not 0 <= begin < end <= image_size or not 0 < unwind < image_size or unwind % 4:
                raise CodeSampleUnavailable("unsupported-unwind-entry")
            if pc - base < begin:
                high = middle
            elif pc - base >= end:
                low = middle + 1
            else:
                return entry
        return None

    def step(
        self, base: int, pc: int, registers: list[int], *, instruction_frame: bool,
        allow_leaf: bool,
    ) -> tuple[int, list[int]]:
        registers = list(registers)
        entry = self.function(base, pc)
        if entry is None:
            if not allow_leaf:
                raise CodeSampleUnavailable("dynamic-or-leaf-unavailable")
            return self.pop_return(registers)
        begin, end, unwind = entry
        header = self.image(base, base + unwind, 4)
        version, flags = header[0] & 7, header[0] >> 3
        if version != 1 or flags & ~3:
            raise CodeSampleUnavailable("unsupported-unwind-version-or-chain")
        count, frame = header[2], header[3] & 15
        if frame and frame not in NONVOLATILE or not frame and header[3] >> 4:
            raise CodeSampleUnavailable("unsupported-frame-register")
        prolog_offset = pc - base - begin
        if frame and prolog_offset < header[1]:
            raise CodeSampleUnavailable("partial-frame-register-prolog")
        if instruction_frame:
            epilog = self.epilog(base, pc, base + end, registers, frame)
            if epilog is not None:
                return epilog
        codes = self.image(base, base + unwind + 4, count * 2) if count else b""
        fixed_rsp = registers[frame] - (header[3] >> 4) * 16 if frame else registers[4]
        index, previous_offset = 0, 256
        while index < count:
            offset, operation = codes[index * 2:index * 2 + 2]
            op, info = operation & 15, operation >> 4
            slots = {0: 1, 1: 2 if info == 0 else 3, 2: 1, 3: 1, 4: 2, 5: 3, 8: 2, 9: 3}.get(op)
            if (
                slots is None or index + slots > count or not 0 < offset <= previous_offset
                or offset > header[1] or op == 1 and info not in (0, 1)
                or op in (0, 4, 5) and info not in NONVOLATILE
                or op == 3 and (not frame or info)
                or op in (8, 9) and info < 6
            ):
                raise CodeSampleUnavailable("unsupported-unwind-operation")
            previous_offset = offset
            extra = codes[(index + 1) * 2:(index + slots) * 2]
            amount = int.from_bytes(extra, "little")
            allocation = amount * 8 if info == 0 else amount
            if op == 1 and (not 136 <= allocation <= 0xFFFFFFF8 or allocation % 8):
                raise CodeSampleUnavailable("unsupported-stack-allocation")
            if offset <= prolog_offset:
                if op == 0:
                    registers[info] = self.stack_word(registers[4])
                    registers[4] += 8
                elif op == 1:
                    registers[4] += amount * 8 if info == 0 else amount
                elif op == 2:
                    registers[4] += info * 8 + 8
                elif op == 3:
                    registers[4] = fixed_rsp
                elif op in (4, 5):
                    registers[info] = self.stack_word(fixed_rsp + (amount * 8 if op == 4 else amount))
                # XMM saves do not affect integer-only unwinding.
            index += slots
        return self.pop_return(registers)

    def epilog(
        self, base: int, pc: int, end: int, registers: list[int], frame: int,
    ) -> tuple[int, list[int]] | None:
        registers = list(registers)

        def code(size: int) -> bytes:
            if pc + size > end:
                raise CodeSampleUnavailable("epilog-range-unavailable")
            return self.image(base, pc, size)

        for index in range(12):
            first = code(1)[0]
            prefix = 0x40 <= first <= 0x4F
            opcode = code(2)[1] if prefix else first
            if opcode == 0xC3:
                return self.pop_return(registers)
            if first == 0xF3 and code(2)[1] == 0xC3:
                return self.pop_return(registers)
            if 0x58 <= opcode <= 0x5F:
                register = opcode - 0x58 + (8 if prefix and first & 1 else 0)
                if register not in NONVOLATILE:
                    raise CodeSampleUnavailable("unsupported-epilog-register")
                registers[register] = self.stack_word(registers[4])
                registers[4] += 8
                pc += 2 if prefix else 1
                continue
            if index == 0 and first == 0x48 and opcode in (0x81, 0x83):
                length = 7 if opcode == 0x81 else 4
                raw = code(length)
                if raw[2] == 0xC4:
                    amount = int.from_bytes(raw[3:], "little", signed=True)
                    if amount <= 0 or amount % 8:
                        raise CodeSampleUnavailable("unsupported-epilog-allocation")
                    registers[4] += amount
                    pc += length
                    continue
            if index == 0 and first in (0x48, 0x49) and opcode == 0x8D:
                modrm = code(3)[2]
                mode, destination = modrm >> 6, (modrm >> 3) & 7
                source = (modrm & 7) + (8 if first & 1 else 0)
                if (
                    destination == 4 and source == frame and mode in (1, 2)
                    and modrm & 7 != 4
                ):
                    length = 4 if mode == 1 else 7
                    displacement = int.from_bytes(code(length)[3:], "little", signed=True)
                    registers[4] = registers[source] + displacement
                    pc += length
                    continue
                if destination == 4:
                    raise CodeSampleUnavailable("unsupported-epilog")
            if (
                index or opcode in (0xC2, 0xE9, 0xEB, 0xFF)
                or first in (0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65, 0x66, 0x67, 0xF0, 0xF2, 0xF3)
            ):
                raise CodeSampleUnavailable("unsupported-epilog")
            return None
        raise CodeSampleUnavailable("epilog-operation-cap")

    def stack_word(self, address: int) -> int:
        if address <= 0 or address % 8:
            raise CodeSampleUnavailable("unsupported-stack-alignment")
        return int(struct.unpack("<Q", self.stack(address, 8))[0])

    def pop_return(self, registers: list[int]) -> tuple[int, list[int]]:
        pc = self.stack_word(registers[4])
        registers[4] += 8
        return pc, registers
