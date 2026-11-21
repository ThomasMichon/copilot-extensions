"""Dependency command grammar, effective UV settings, and launch projection."""

from __future__ import annotations

import os
import json
import re
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

_Error = type[Exception]
_Balanced = Callable[[str, int, str, str], tuple[str, int]]
_Pairs = Callable[[list[str]], Iterable[tuple[str, str]]]


def uv_launch_path(
    uv: str, environment: Mapping[str, str], cwd: Path | None, *, error_type: _Error,
) -> str:
    """Resolve captured PATH entries against the actual OS launch directory."""
    base = Path.cwd() if cwd is None else cwd.absolute()
    if os.path.dirname(uv):
        return os.path.abspath(base / uv)
    for entry in os.get_exec_path(dict(environment)):
        selected = os.path.abspath(base / entry / uv)
        if os.path.isfile(selected) and os.access(selected, os.X_OK):
            return selected
    raise error_type("Cannot qualify UV_NO_SYSTEM_CONFIG support: selected UV is unavailable")


def merge_config(high: Mapping[str, object], low: Mapping[str, object]) -> dict[str, object]:
    """Apply UV's recursive table and ordered-list precedence without doing I/O."""
    result = dict(low)
    for key, value in high.items():
        previous = result.get(key)
        if isinstance(value, dict) and isinstance(previous, dict):
            result[key] = merge_config(cast(dict[str, object], value), cast(dict[str, object], previous))
        elif isinstance(value, list) and isinstance(previous, list):
            result[key] = [*cast(list[object], value), *cast(list[object], previous)]
        else:
            result[key] = value
    return result


def captured_environment(
    env: Mapping[str, str], *, windows: bool, error_type: _Error,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in env.items():
        key = key.upper() if windows else key
        if key in result and result[key] != value:
            raise error_type("dependency installation environment has conflicting variable identities")
        result[key] = value
    return result


def argument_pairs(
    arguments: Iterable[str], value_options: Collection[str], *, error_type: _Error,
) -> Iterator[tuple[str, str]]:
    iterator = iter(arguments)
    positional = False
    for argument in iterator:
        if positional or not argument.startswith("-"):
            yield "", argument
        elif argument == "--":
            positional = True
        else:
            option, separator, value = argument.partition("=")
            if (
                not argument.startswith("--")
                and len(argument) > 2
                and argument[:2] in value_options
            ):
                option, separator, value = argument[:2], "=", argument[2:]
            if option in value_options:
                if not separator:
                    value = next(iterator, "")
                if not value or value.startswith("--"):
                    raise error_type("dependency installation option has no value")
                yield option, value
            else:
                yield option, value


def balanced(
    text: str, start: int, opening: str, closing: str, *, error_type: _Error,
) -> tuple[str, int]:
    depth = 0
    quoted = escaped = False
    for offset in range(start, len(text)):
        char = text[offset]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1 : offset], offset + 1
    raise error_type("UV settings projection is incomplete")


def uv_command(
    uv: str,
    arguments: list[str],
    *,
    cwd: Path | None,
    directory: Path,
    operation: str,
    windows: bool,
    argument_pairs: _Pairs,
    value_options: Collection[str],
) -> tuple[list[str], Path | None]:
    if not windows or len(os.path.abspath(cwd or Path.cwd())) < 260:
        return [uv, "pip", operation, *arguments], cwd
    retained = []
    directory_supplied = False
    for option, value in argument_pairs(arguments):
        if option == "--directory":
            directory_supplied = True
            value = str(directory)
        if option and value:
            retained.extend(
                [option, value] if option in value_options else [option + "=" + value]
            )
        else:
            retained.append(option or value)
    if not directory_supplied:
        retained[:0] = ["--directory", str(directory)]
    return [uv, "pip", operation, *retained], Path(directory.anchor)


_STRUCTURED_CUTOFF = re.compile(
    r"ExcludeNewerValue\s*\{\s*timestamp:\s*([^,\s]+),\s*span:\s*"
    r"(?:None|Some\(\s*ExcludeNewerSpan\(\s*([^,\s]+),?\s*\),?\s*\)),?\s*\}"
)

def cutoff_value(value: str, now: datetime, *, error_type: _Error) -> datetime:
    value = value.strip().removesuffix(",").strip()
    structured = _STRUCTURED_CUTOFF.fullmatch(value)
    if structured:
        cutoff = cutoff_value(f"Absolute({structured[1]})", now, error_type=error_type)
        if structured[2] is not None:
            relative_cutoff = cutoff_value(
                f"Relative(ExcludeNewerSpan({structured[2]}))", now, error_type=error_type,
            )
            cutoff = min(cutoff, relative_cutoff)
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


def package_cutoffs(entries: str, *, balanced: _Balanced, error_type: _Error) -> list[tuple[str, str]]:
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


def cutoff_arguments(
    settings: str, *, now: datetime | None, operation: str, min_age: float,
    balanced: _Balanced, remote_reference: re.Pattern[str], error_type: _Error,
) -> list[str]:
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None:
        raise error_type("seasoning requires a timezone-aware clock")
    floor = now.astimezone(timezone.utc) - timedelta(seconds=min_age)
    projection = {"install": "PipInstallSettings", "compile": "PipCompileSettings"}.get(operation)
    if projection is None:
        raise error_type("unsupported UV dependency operation")
    projections = list(re.finditer(r"(?m)^" + projection + r" \{", settings))
    if len(projections) != 1:
        raise error_type("UV installation settings projection is unsupported")
    _, projection_end = balanced(settings, settings.index("{", projections[0].start()), "{", "}")
    if settings[projection_end:].strip():
        raise error_type("UV settings projection has unexpected trailing content")
    settings = settings[projections[0].start():projection_end]
    if (re.search(r"(?m)^\s*dependency_mode:\s*(?!Transitive\b)\S+", settings)
            or re.search(r"(?m)^\s*build_isolation:\s*(?!Isolate\b)\S+", settings)):
        raise error_type("dependency or isolated-build resolution cannot bypass seasoning")
    indexes = list(re.finditer(r"(?m)^\s*index_locations: IndexLocations \{", settings))
    if len(indexes) != 1:
        raise error_type("UV registry source configuration is unsupported")
    index_body, indexes_end = balanced(settings, settings.index("{", indexes[0].start()), "{", "}")
    flat = re.search(r"\bflat_index:\s*\[", index_body)
    if flat is None:
        raise error_type("UV flat-index configuration is unsupported")
    flat_body, _ = balanced(index_body, index_body.index("[", flat.start()), "[", "]")
    if flat_body.strip():
        raise error_type("flat-index dependencies cannot prove artifact upload age")
    non_registry = settings[:indexes[0].start()] + settings[indexes_end:]
    if remote_reference.search(non_registry):
        raise error_type("direct remote dependencies lack release-age evidence")
    matches = list(re.finditer(r"(?m)^\s*exclude_newer: ExcludeNewer \{", settings))
    if len(matches) != 1:
        raise error_type("UV does not expose an unambiguous seasoning configuration")
    match = matches[0]
    body, end = balanced(settings, settings.index("{", match.start()), "{", "}")
    # Per-index dates can synthesize age for artifacts with unknown timestamps.
    remaining = settings[:match.start()] + settings[end:]
    index_values = re.findall(r"(?m)^\s*exclude_newer:\s*([^\n]+)", remaining)
    if any(value.strip().rstrip(",") != "None" for value in index_values):
        raise error_type("index-specific exclude-newer cannot prove artifact upload age")
    global_match = re.match(r"\s*global:\s*(None|Some\()", body)
    if global_match is None:
        raise error_type("UV global cutoff settings are unsupported")
    position = global_match.end()
    if global_match[1] == "Some(":
        global_body, position = balanced(body, position - 1, "(", ")")
        floor = min(floor, cutoff_value(global_body, now, error_type=error_type))
    package_match = re.fullmatch(
        r",?\s*package:\s*ExcludeNewerPackage\(\s*\{(.*)\},?\s*\),?\s*",
        body[position:], re.DOTALL,
    )
    if package_match is None:
        raise error_type("UV package cutoff settings are unsupported")
    package_arguments = []
    for name, value in package_cutoffs(package_match[1], balanced=balanced, error_type=error_type):
        cutoff = cutoff_value(value, now, error_type=error_type)
        if cutoff > floor:
            raise error_type(f"package-specific cutoff weakens seasoning: {name}")
        package_arguments.extend(["--exclude-newer-package", f"{name}={cutoff.isoformat().replace('+00:00', 'Z')}"])
    result = ["--exclude-newer", floor.isoformat().replace("+00:00", "Z"), *package_arguments]
    return [*result, "--reinstall"] if operation == "install" else result


def check_compile_projection(
    projection: str, arguments: list[str], *, argument_pairs: _Pairs, balanced: _Balanced,
    error_type: _Error, source_graph: bool = False, native_target: bool = False,
) -> None:
    """Output policy is effective configuration, not merely a CLI intention."""
    if dict(argument_pairs(arguments)).get("--format") == "pylock.toml" and not re.search(
        r"(?m)^\s*format:\s*Some\(\s*PylockToml,\s*\),\s*$", projection,
    ):
        raise error_type("UV wheel-lock format differs from the selected output")
    for field, expected_value in (
        ("generate_hashes", "true"), ("no_header", "true"), ("no_annotate", "true"),
        ("emit_index_url", "false"), ("emit_find_links", "false"), ("emit_index_annotation", "false"),
    ):
        if not re.search(r"(?m)^\s*" + field + r":\s*" + expected_value + r",\s*$", projection):
            raise error_type("UV lock export output configuration is unsupported")
    omissions = re.search(r"(?m)^\s*no_emit_package:\s*\[", projection)
    if omissions is None:
        raise error_type("UV lock export output omissions are unsupported")
    body, _ = balanced(projection, omissions.end() - 1, "[", "]")
    names = []
    while body.strip():
        match = re.match(r'\s*PackageName\(\s*("(?:[^"\\]|\\.)*")\s*,?\s*\)\s*,\s*', body)
        if match is None:
            raise error_type("UV lock export output omissions are unsupported")
        try:
            names.append(json.loads(match[1]))
        except ValueError as exc:
            raise error_type("UV lock export output omissions are unsupported") from exc
        body = body[match.end():]
    expected = [value for option, value in argument_pairs(arguments) if option == "--no-emit-package"]
    if names != expected:
        raise error_type("UV lock export output omissions differ from the retained source graph")
    if source_graph and not re.search(r"(?m)^\s*sources:\s*None,\s*$", projection):
        raise error_type("source lock export cannot disable its retained source mappings")
    if native_target and (
        not re.search(r"(?m)^\s*python_platform:\s*None,\s*$", projection)
        or not re.search(r"(?m)^\s*universal:\s*false,\s*$", projection)
    ):
        raise error_type("native source lock export cannot override its selected target")
    outputs = [value for option, value in argument_pairs(arguments) if option in ("-o", "--output-file")]
    output = re.search(r"(?m)^\s*output_file:\s*(None,|Some\()", projection)
    if output is None:
        raise error_type("UV lock export output configuration is unsupported")
    selected = None
    if output[1] == "Some(":
        body, _ = balanced(projection, output.end() - 1, "(", ")")
        try:
            selected = json.loads(body.strip().rstrip(","))
        except ValueError as exc:
            raise error_type("UV lock export output path is unsupported") from exc
    if selected != (outputs[-1] if outputs else None):
        raise error_type("UV lock export output must be selected explicitly by the caller")


def resolver_configuration(
    projection: str, remapper: Callable[[str], str] | None = None, *, balanced: _Balanced,
    error_type: _Error, now: datetime | None = None, freeze_package_cutoffs: bool = False,
) -> str:
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
