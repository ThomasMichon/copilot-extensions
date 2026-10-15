"""Private UV settings-projection and configuration-retention primitives."""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, time, timedelta, timezone

_STRUCTURED_CUTOFF = re.compile(
    r"ExcludeNewerValue\s*\{\s*timestamp:\s*([^,\s]+),\s*span:\s*"
    r"(?:None|Some\(\s*ExcludeNewerSpan\(\s*([^,\s]+),?\s*\),?\s*\)),?\s*\}"
)


def cutoff_value(value: str, now: datetime, *, error_type) -> datetime:
    value = value.strip().removesuffix(",").strip()
    structured = _STRUCTURED_CUTOFF.fullmatch(value)
    if structured:
        cutoff = cutoff_value(f"Absolute({structured[1]})", now, error_type=error_type)
        if structured[2] is not None:
            relative = cutoff_value(
                f"Relative(ExcludeNewerSpan({structured[2]}))", now, error_type=error_type,
            )
            cutoff = min(cutoff, relative)
        return cutoff
    absolute = re.fullmatch(r"Absolute\(\s*([^,\s]+),?\s*\)", value)
    if absolute:
        timestamp = re.sub(
            r"\.(\d{1,9})(?=Z$|[+-]\d{2}:\d{2}$)",
            lambda match: "." + match[1][:6].ljust(6, "0"), absolute[1],
        )
        try:
            result = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise error_type("UV returned an invalid cutoff timestamp") from exc
        if result.tzinfo is None:
            raise error_type("UV returned a cutoff without a timezone")
        return result.astimezone(timezone.utc)
    relative = re.fullmatch(
        r"Relative\(\s*ExcludeNewerSpan\(\s*([^,\s]+),?\s*\),?\s*\)",
        value,
    )
    if relative:
        span = relative[1]
        parts = re.findall(r"(\d+)(w|d|h|m|s)", span)
        if not parts or "".join(number + unit for number, unit in parts) != span:
            raise error_type("unsupported UV relative cutoff; use an RFC3339 timestamp")
        seconds = sum(int(number) * {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
                      for number, unit in parts)
        try:
            return now - timedelta(seconds=seconds)
        except OverflowError as exc:
            raise error_type("UV relative cutoff exceeds the supported range") from exc
    raise error_type("unsupported UV cutoff representation")


def package_cutoffs(entries, *, balanced, error_type):
    result = []
    entries = entries.strip()
    while entries:
        entry = re.match(r'PackageName\(\s*"([a-z0-9._-]+)",?\s*\):\s*Enabled\(', entries)
        if entry is None:
            raise error_type("disabled or unsupported package-specific seasoning")
        value, stop = balanced(entries, entry.end() - 1, "(", ")")
        result.append((entry[1], value))
        entries = entries[stop:].lstrip(", \r\n\t")
    return result


def _toml_value(value, error_type):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item, error_type) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(
            json.dumps(key) + " = " + _toml_value(item, error_type) for key, item in value.items()
        ) + "}"
    raise error_type("dependency input TOML value cannot be retained")


def write_toml(path, data, *, error_type):
    text = "\n".join(
        json.dumps(key) + " = " + _toml_value(value, error_type) for key, value in data.items()
    )
    # The private snapshot can contain credential-bearing index settings.
    if path.exists():
        os.chmod(path, 0o600)
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(text + "\n")


def private_retained_root(parent, *, error_type):
    root = parent / "inputs"
    if os.name != "nt":
        root.mkdir(mode=0o700)
        return root
    import ctypes
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID),
            ("inherit", wintypes.BOOL),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID]
    convert.restype = wintypes.BOOL
    create = kernel.CreateDirectoryW
    create.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
    create.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    descriptor = wintypes.LPVOID()
    # Protected, owner-only DACL propagates to both files and directories.
    if not convert("D:P(A;OICI;FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise error_type("private dependency-input permissions could not be constructed")
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        if not create(str(root), ctypes.byref(attributes)):
            raise error_type("private dependency-input directory could not be created")
    finally:
        kernel.LocalFree(descriptor)
    return root


def merge_config(high, low):
    result = dict(low)
    for key, value in high.items():
        previous = result.get(key)
        if isinstance(value, dict) and isinstance(previous, dict):
            result[key] = merge_config(value, previous)
        elif isinstance(value, list) and isinstance(previous, list):
            result[key] = [*value, *previous]
        else:
            result[key] = value
    return result


def resolver_configuration(
    projection, remapper=None, *, balanced, error_type, now=None, freeze_package_cutoffs=False,
):
    now = datetime.now(timezone.utc) if now is None else now
    match = re.search(r"\bsettings: PipSettings \{", projection)
    if match is None:
        raise error_type("UV resolver configuration cannot be compared")
    body, _ = balanced(projection, projection.index("{", match.start()), "{", "}")
    cutoff = re.search(r"(?m)^\s*exclude_newer: ExcludeNewer \{", body)
    if cutoff is None:
        raise error_type("UV resolver cutoff cannot be compared")
    cutoff_body, end = balanced(body, body.index("{", cutoff.start()), "{", "}")
    package = re.search(r"\bpackage:\s*ExcludeNewerPackage\(", cutoff_body)
    if package is None:
        raise error_type("UV package cutoffs cannot be compared")
    package_body, _ = balanced(cutoff_body, package.end() - 1, "(", ")")
    package_entries = re.fullmatch(r"\s*\{(.*)\},?\s*", package_body, re.DOTALL)
    if package_entries is None:
        raise error_type("UV package cutoff settings are unsupported")
    keys = []
    for name, value in package_cutoffs(package_entries[1], balanced=balanced, error_type=error_type):
        timestamp = cutoff_value(value, now, error_type=error_type)
        span = re.search(r"\bExcludeNewerSpan\(\s*([^,\s]+)", value)
        keys.append((name, timestamp.isoformat(), span[1] if span and not freeze_package_cutoffs else None))
    body = body[:cutoff.start()] + "package_cutoffs:" + repr(sorted(keys)) + "\n" + body[end:]
    if remapper is not None:
        for field in ("dependency_metadata", "extra_build_dependencies", "sources"):
            match = re.search(r"(?m)^\s*" + field + r":\s*[^\n({\[]*([({\[])", body)
            if match:
                start = match.start(1)
                opening = body[start]
                _, end = balanced(body, start, opening, {"(": ")", "{": "}", "[": "]"}[opening])
                body = body[:start] + remapper(body[start:end]) + body[end:]
    # Execution always reinstalls the full closure, which also subsumes an
    # admitted package-specific refresh. Neither changes dependency resolution.
    reinstall = re.search(r"(?m)^\s*reinstall: Packages\(", body)
    if reinstall:
        _, end = balanced(body, reinstall.end() - 1, "(", ")")
        if body[end:end + 1] == ",":
            end += 1
        body = body[:reinstall.start()] + body[end:]
    body = re.sub(r"(?m)^\s*reinstall: (?:None|All),", "", body)
    # Discovery provenance changes to explicit-file provenance; index identity,
    # order, authentication and every resolver option must remain identical.
    while origin := re.search(r"(?m)^\s*origin: (?:None,|Some\()", body):
        end = origin.end()
        if body[end - 1] == "(":
            _, end = balanced(body, end - 1, "(", ")")
            if body[end:end + 1] == ",":
                end += 1
        body = body[:origin.start()] + body[end:]
    for field in (
        "constraints_from_workspace", "overrides_from_workspace",
        "excludes_from_workspace", "build_constraints_from_workspace",
    ):
        match = re.search(r"\b" + field + r":\s*\[", projection)
        if match:
            value, _ = balanced(projection, projection.index("[", match.start()), "[", "]")
            if remapper is not None:
                value = remapper(value)
            body += field + ":[" + value + "]"
    global_settings = re.search(r"(?m)^GlobalSettings \{", projection)
    if global_settings:
        value, _ = balanced(projection, projection.index("{", global_settings.start()), "{", "}")
        body += value
    return "\n".join(line.strip() for line in body.splitlines() if line.strip())
