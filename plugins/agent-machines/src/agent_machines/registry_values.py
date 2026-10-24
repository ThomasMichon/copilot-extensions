"""Parse and compare data returned by Windows reg.exe."""

from __future__ import annotations

import re


_REG_VALUE_RE = re.compile(r"\s(REG_[A-Z_]+)\s+(.*)$")


def parse_reg_query(stdout: str, name: str) -> tuple[str | None, str | None]:
    """Extract ``(data, REG_type)`` for ``name`` (``""`` = the default value)."""
    target = name.lower() if name else "(default)"
    for line in stdout.splitlines():
        m = _REG_VALUE_RE.search(line)
        if not m:
            continue
        vname = line[: m.start()].strip()
        if vname.lower() == target:
            return m.group(2).strip(), m.group(1)
    return None, None


def registry_values_equal(observed: str | None, desired: str, reg_type: str) -> bool:
    """Compare unsigned numeric data by value and all other data as exact text."""
    if reg_type not in ("REG_DWORD", "REG_QWORD"):
        return observed == desired
    if observed is None:
        return False
    bits = 32 if reg_type == "REG_DWORD" else 64
    values = []
    for text in (observed, desired):
        if re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", text) is None:
            return False
        value = int(text, 16 if text.lower().startswith("0x") else 10)
        if not 0 <= value < (1 << bits):
            return False
        values.append(value)
    return values[0] == values[1]
