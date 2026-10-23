"""Private UV settings-projection and configuration-retention primitives."""
from __future__ import annotations

import errno
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

_WINDOWS = os.name == "nt"

# native-executable:begin
def physical_executable(path: str) -> str | None:
    """Read link targets before inspecting descendants, including the final file.

    RedirectionGuard can refuse traversal of a junction even when its target is
    usable. Like versioned-runtime's legacy link handling, inspect the entry
    with lstat and readlink instead of resolving it with a traversing stat.
    """
    requested = Path(path)
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    remaining = list(requested.parts)
    current = remaining.pop(0)
    links = 0
    if not remaining:
        return None
    while remaining:
        component = remaining.pop(0)
        if component == "..":
            current = os.path.dirname(current)
            if not remaining:
                return None
            continue
        candidate = os.path.join(current, component)
        try:
            info = os.lstat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            return None
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_reparse_tag", 0) & 0x20000000
        ):
            links += 1
            if links > 40:
                raise OSError(errno.ELOOP, "Interpreter link cycle or excessive indirection", path)
            target = os.readlink(candidate)
            if not os.path.isabs(target):
                target = os.path.join(current, target)
            remaining = list(Path(target).parts) + remaining
            current = remaining.pop(0)
            if not remaining:
                return None
            continue
        if remaining and not stat.S_ISDIR(info.st_mode):
            return None
        if not remaining and not stat.S_ISREG(info.st_mode):
            return None
        current = candidate
    return current
# native-executable:end


def select_uv_executable(uv, *, error_type):
    if not _WINDOWS:
        return uv
    if str(uv).lower() in ("uv", "uv.exe"):
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SearchPathW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_void_p,
        ]
        kernel.SearchPathW.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.SearchPathW(None, str(uv), ".exe", len(buffer), buffer, None)
        if not length:
            raise error_type(f"Native Windows UV lookup failed (error {ctypes.get_last_error()})")
        if length >= len(buffer):
            raise error_type("Selected UV executable exceeds the native path limit")
        uv = buffer.value
    if not Path(uv).is_absolute() or Path(uv).suffix.lower() != ".exe":
        return uv
    selected = physical_executable(uv)
    if selected is None:
        raise error_type("Selected UV executable does not identify an available regular file")
    return selected


_STRUCTURED_CUTOFF = re.compile(
    r"ExcludeNewerValue\s*\{\s*timestamp:\s*([^,\s]+),\s*span:\s*"
    r"(?:None|Some\(\s*ExcludeNewerSpan\(\s*([^,\s]+),?\s*\),?\s*\)),?\s*\}"
)


class RetainedInputCleanupError(RuntimeError):
    """Installation inputs could not be removed after execution settled."""


def report_cleanup_error(error):
    print(f"retained-input-cleanup: {error}", file=sys.stderr)


def native_retained_path(path):
    value = os.path.abspath(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


@contextmanager
def temporary_inputs(parent):
    prefix = "uv-" if parent is not None else "seasoned-uv-"
    temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=parent)
    failed = False
    try:
        yield temporary.name
    except BaseException:
        failed = True
        raise
    finally:
        temporary.name = native_retained_path(temporary.name)
        try:
            temporary.cleanup()
        except OSError as exc:
            if failed:
                report_cleanup_error(exc)
            else:
                raise RetainedInputCleanupError(str(exc)) from exc


def config_inputs(arguments, environment, base, *, argument_pairs, toml, error_type):
    pairs = dict(argument_pairs(arguments))
    if "--no-config" in pairs or environment.get("UV_NO_CONFIG", "").lower() in ("1", "true"):
        return []
    explicit = pairs.get("--config-file") or environment.get("UV_CONFIG_FILE")
    if explicit:
        return [(base / explicit, False)]
    candidates = []
    project = base / (pairs.get("--project") or environment.get("UV_PROJECT") or ".")
    if "--isolated" not in pairs and environment.get("UV_ISOLATED", "").lower() not in ("1", "true"):
        for directory in (project, *project.parents):
            uv = directory / "uv.toml"
            manifest = directory / "pyproject.toml"
            if uv.is_file():
                candidates.append((uv, False))
                break
            if manifest.is_file():
                tool = toml(manifest).get("tool", {})
                if not isinstance(tool, dict):
                    raise error_type("configured project metadata is unsupported")
                if "uv" in tool:
                    if not isinstance(tool["uv"], dict):
                        raise error_type("configured UV metadata is unsupported")
                    candidates.append((manifest, True))
                    break
    if os.name == "nt":
        if environment.get("APPDATA"):
            candidates.append((Path(environment["APPDATA"]) / "uv" / "uv.toml", False))
        if environment.get("SYSTEMDRIVE"):
            candidates.append((
                Path(environment["SYSTEMDRIVE"] + "\\") / "ProgramData" / "uv" / "uv.toml", False,
            ))
    else:
        user = Path(environment.get("XDG_CONFIG_HOME") or (
            Path(environment.get("HOME") or Path.home()) / ".config"
        )) / "uv" / "uv.toml"
        candidates.append((user, False))
        system = Path("/etc/uv/uv.toml")
        for directory in (environment.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":"):
            if not directory:
                break
            candidate = base / directory / "uv" / "uv.toml"
            if candidate.is_file():
                system = candidate
                break
        candidates.append((system, False))
    return [(path, project) for path, project in candidates if path.is_file()]


def metadata_needs_caller_layout(value):
    if isinstance(value, dict):
        # Source-bearing metadata can contain relative references or traversals
        # outside and back into a source root. Keep its established layout.
        if any(value.get(key) for key in (
            "sources", "dependency-groups", "dev-dependencies", "constraint-dependencies",
            "override-dependencies", "build-constraint-dependencies",
            "dependency-metadata", "extra-build-dependencies",
        )):
            return True
        return any(metadata_needs_caller_layout(item) for item in value.values())
    if isinstance(value, list):
        return any(metadata_needs_caller_layout(item) for item in value)
    return isinstance(value, str) and (
        "file:" in value.lower() or re.search(r"(?:^|[\s/\\])\.\.(?:[/\\]|$)", value) is not None
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
