"""Private UV settings-projection and configuration-retention primitives."""
from __future__ import annotations

import configparser
import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time as clock
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

_WINDOWS = os.name == "nt"


def remaining_timeout(deadline, *, error_type, cap=None):
    if deadline is None:
        return cap
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise error_type("retained UV inputs require a finite monotonic deadline")
    remaining = deadline - clock.monotonic()
    if remaining <= 0:
        raise error_type("retained UV input deadline exceeded")
    return remaining if cap is None else min(remaining, cap)


def copy_retained_file(source, destination, *, native_path, deadline, error_type):
    remaining_timeout(deadline, error_type=error_type)
    if deadline is None:
        return shutil.copy2(native_path(source), native_path(destination))
    with open(native_path(source), "rb") as incoming, open(native_path(destination), "wb") as output:
        while True:
            remaining_timeout(deadline, error_type=error_type)
            chunk = incoming.read(1 << 20)
            if not chunk:
                break
            output.write(chunk)
    shutil.copystat(native_path(source), native_path(destination))
    remaining_timeout(deadline, error_type=error_type)
    return destination


def prepare_uv_install(
    uv, arguments, *, environment, cwd, now, trusted_source_roots, projection,
    operation, deadline, native_target, compile_inputs, source_inputs, select_uv,
    cutoff, argument_pairs, balanced, error_type, settings_timeout, headless,
):
    remaining_timeout(deadline, error_type=error_type)
    if native_target and environment.get("UV_PYTHON_PLATFORM"):
        raise error_type("native source lock export cannot override its selected target")
    if operation in ("compile", "source-compile"):
        arguments = compile_inputs(arguments, environment, cwd, trusted_source_roots, operation)
    source_inputs(
        arguments, environment, cwd, trusted_source_roots,
        **({"operation": operation} if operation != "install" else {}),
    )
    remaining_timeout(deadline, error_type=error_type)
    uv = select_uv(uv)
    probe_arguments = [arg for arg in arguments if arg != "--quiet" and not re.fullmatch(r"-q+", arg)]
    native_operation = "compile" if operation == "source-compile" else operation
    try:
        result = subprocess.run(
            [uv, "pip", native_operation, "--show-settings", *probe_arguments],
            capture_output=True, text=True, env=environment, cwd=cwd,
            timeout=remaining_timeout(deadline, error_type=error_type, cap=settings_timeout),
            creationflags=headless,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise error_type("UV seasoning configuration probe failed or timed out") from exc
    remaining_timeout(deadline, error_type=error_type)
    if result.returncode:
        raise error_type("UV seasoning configuration probe failed")
    admitted = cutoff(result.stdout, now=now, _operation=native_operation)
    if native_operation == "compile":
        check_compile_projection(
            result.stdout, arguments, argument_pairs=argument_pairs,
            balanced=balanced, error_type=error_type,
            source_graph=operation == "source-compile", native_target=native_target,
        )
    elif native_target:
        raise error_type("native target binding requires a compile transaction")
    if projection is not None:
        projection.append(result.stdout)
    cleaned = []
    iterator = iter(arguments)
    for argument in iterator:
        if argument in ("--exclude-newer", "--exclude-newer-package"):
            next(iterator, None)
        elif (not argument.startswith(("--exclude-newer=", "--exclude-newer-package="))
              and argument not in ("--reinstall", "--force-reinstall")):
            cleaned.append(argument)
    return [*cleaned, *admitted]

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
def retain_closed_wheels(
    uv, snapshot, *, python, env, captured_environment, select_uv,
    transaction_type, error_type,
):
    """Retain the policy owner's fixed, closed wheel invocation projection."""
    from installer_wheel_snapshot import WheelSnapshot

    if not isinstance(snapshot, WheelSnapshot):
        raise error_type("closed wheel installation requires retained wheel custody")
    python = Path(python)
    if not python.is_absolute():
        raise error_type("closed wheel interpreter must be an absolute candidate path")
    uv = select_uv(uv)
    if not Path(uv).is_absolute():
        if os.path.dirname(uv):
            selected = uv
        else:
            import shutil

            selected = shutil.which(uv)
            if selected is None:
                raise error_type("closed wheel UV executable is unavailable")
        uv = select_uv(os.path.abspath(selected))
    environment = {
        key: value for key, value in captured_environment(env).items()
        if not key.upper().startswith(("UV_", "PIP_", "PYTHON"))
        and key.upper() not in ("VIRTUAL_ENV", "CONDA_PREFIX", "__PYVENV_LAUNCHER__")
    }
    with temporary_inputs(None) as temporary:
        root = private_retained_root(Path(temporary), error_type=error_type).resolve()
        requirements = root / "wheels.txt"
        requirements.write_text("".join(
            f"{name.split('-', 1)[0]} @ "
            f"{path.as_uri()}#sha256={snapshot.digests[name].removeprefix('sha256:')} "
            f"--hash={snapshot.digests[name]}\n"
            for name, path in sorted(snapshot.paths.items())
        ), encoding="utf-8")
        snapshot.verify()
        yield transaction_type(
            (uv, "pip", "install", "--no-config", "--no-cache", "--offline",
             "--no-python-downloads", "--no-index", "--no-deps", "--no-build",
             "--no-sources", "--require-hashes", "--link-mode", "copy",
             "--python", str(python), "--requirements", str(requirements)),
            environment, root, {}, {},
        )
        snapshot.verify()


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


@contextmanager
def retain_local_exclusions(retain, uv, arguments, *, excluded_local_packages, error_type, **options):
    if not isinstance(excluded_local_packages, tuple) or any(
        not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is None
        for name in excluded_local_packages
    ):
        raise error_type("excluded local packages must be an immutable tuple of names")
    names = tuple(re.sub(r"[-_.]+", "-", name).lower() for name in excluded_local_packages)
    if len(set(names)) != len(names):
        raise error_type("excluded local packages contain duplicate names")
    if not names:
        with retain(uv, arguments, **options) as transaction:
            yield transaction
        return
    with temporary_inputs(None) as temporary:
        constraint = (Path(temporary) / "excluded-local-packages.txt").resolve()
        constraint.write_text(
            "".join(f"{name}<0,>=0\n" for name in sorted(names)), encoding="utf-8",
        )
        with retain(
            uv, [*arguments, "--constraint", str(constraint)],
            _generated_constraints=(constraint,), **options,
        ) as transaction:
            yield transaction


def retained_source_layout(
    root, projects, files, resources, base, arguments, environment, editables, *,
    argument_pairs, file_options, toml, config_inputs, generated_constraints=(),
):
    context_project = dict(argument_pairs(arguments)).get("--project") or environment.get("UV_PROJECT")
    pairs = list(argument_pairs(arguments))
    operands = [value for option, value in pairs if not option]
    layout_files = files - set(generated_constraints)
    compact = (
        projects and operands and not layout_files and not editables and not context_project
        and not any(
            option in file_options | {"--group"}
            and (option == "--group" or (base / value).resolve() not in generated_constraints)
            for option, value in pairs
        )
        and not any(environment.get(key) for key in (
            "UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE", "UV_EXCLUDE",
        ))
        and all(Path(value).is_absolute() for value in operands)
        and not any(metadata_needs_caller_layout(toml(project / "pyproject.toml")) for project in projects)
        and not any(metadata_needs_caller_layout(toml(path))
                    for path, _ in config_inputs(arguments, environment, base))
    )
    # Generated absolute constraints must not widen the caller-relative source layout.
    paths = [*projects, *layout_files, *resources]
    if not compact:
        paths.append(base)
    if context_project:
        paths.append((base / context_project).resolve())
    anchors = {}
    for path in paths:
        anchor = path.anchor.lower()
        members = [str(member) for member in paths if member.anchor.lower() == anchor]
        common = Path(os.path.commonpath(members))
        anchors[anchor] = common.parent if common.is_file() else common

    def mirror(path):
        path = Path(path).resolve()
        if path in generated_constraints:
            return root / "generated" / path.name
        anchor = hashlib.sha256(path.anchor.encode()).hexdigest()[:12]
        return root / anchor / path.relative_to(anchors[path.anchor.lower()])

    return anchors, mirror


def retained_source_remapper(paths, mirror):
    replacements = {}
    for path in sorted(paths, key=lambda item: len(str(item)), reverse=True):
        retained_path = mirror(path)
        for before, after in (
            (path.as_uri(), retained_path.as_uri()),
            (str(path), str(retained_path)),
            (path.as_posix(), retained_path.as_posix()),
            (json.dumps(str(path))[1:-1], json.dumps(str(retained_path))[1:-1]),
        ):
            replacements[before] = after
    pattern = re.compile("|".join(re.escape(before) for before in replacements)) if replacements else None

    def remap(value):
        if isinstance(value, list):
            return [remap(item) for item in value]
        if isinstance(value, dict):
            return {key: remap(item) for key, item in value.items()}
        if not isinstance(value, str) or pattern is None:
            return value
        return pattern.sub(lambda match: replacements[match[0]], value)

    return remap


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


def retain_editable_metadata(project, *, error_type):
    configuration = Path(native_retained_path(project / "setup.cfg"))
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    try:
        if configuration.exists():
            config.read_string(configuration.read_text(encoding="utf-8"))
        if not config.has_section("egg_info"):
            config.add_section("egg_info")
        config.set("egg_info", "egg_base", ".")
        with configuration.open("w", encoding="utf-8") as stream:
            config.write(stream)
    except (OSError, configparser.Error) as error:
        raise error_type("cannot retain editable build metadata") from error


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


def cutoff_arguments(settings, *, now, operation, min_age, balanced, remote_reference, error_type):
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


def source_compile_packages(projects, *, toml, identity, error_type):
    names = []
    omissions = set()
    for path in projects:
        data = toml(path / "pyproject.toml")
        project = data["project"]
        names.append(identity(project["name"]))
        required = {
            identity(match[1])
            for requirement in project.get("dependencies", [])
            if isinstance(requirement, str) and ";" not in requirement and "@" not in requirement
            and (match := re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement))
        }
        for name, mapping in data.get("tool", {}).get("uv", {}).get("sources", {}).items():
            if isinstance(mapping, dict) and "path" in mapping:
                name = identity(name)
                if name not in required:
                    raise error_type("source lock export cannot omit a conditional or unselected local dependency")
                omissions.add(name)
    if not names or len(set(names)) != len(names):
        raise error_type("source lock export needs an unambiguous owned project graph")
    return tuple(sorted(omissions))


def source_compile_arguments(
    arguments, environment, cwd, roots, operation, *,
    source_inputs, toml, identity, argument_pairs, error_type,
):
    packages = ()
    if operation == "source-compile":
        _, projects, _, _ = source_inputs(
            arguments, environment, cwd, roots, operation=operation,
        )
        packages = source_compile_packages(projects, toml=toml, identity=identity, error_type=error_type)
    return compile_arguments(
        arguments, argument_pairs=argument_pairs, error_type=error_type, source_packages=packages,
        source_graph=operation == "source-compile",
    )


def compile_arguments(arguments, *, argument_pairs, error_type, source_packages=(), source_graph=False):
    """Bounded requirements export, never an implicit incremental lock update."""
    values = {
        "--constraints", "-c", "--overrides", "--excludes", "--build-constraints", "-b",
        "--extra", "--cert", "-o", "--output-file", "--format", "--only-binary",
        "--python-platform", "--python-version", "--index", "--default-index",
        "--index-url", "-i", "--extra-index-url", "--index-strategy", "--keyring-provider",
        "--resolution", "--prerelease", "--fork-strategy", "--exclude-newer",
        "--exclude-newer-package", "--cache-dir", "-p", "--python", "--color",
        "--directory", "--project", "--config-file",
    }
    flags = {
        "--all-extras", "--generate-hashes", "--no-build", "--no-index", "--universal",
        "--upgrade", "-U", "--no-sources", "--no-cache", "-n", "--refresh",
        "--no-python-downloads", "--no-managed-python", "--offline", "--system-certs",
        "--no-progress", "--quiet", "-q", "--verbose", "-v", "--no-header",
        "--no-annotate",
    }
    result = []
    omissions = []
    output_format = "requirements.txt"
    for option, value in argument_pairs(arguments):
        if not option:
            if value == "-" or not value:
                raise error_type("lock export requires retained file inputs, not standard input")
            result.append(value)
        elif option == "--no-emit-package":
            omissions.append(value)
        elif option == "--format":
            if value not in (("requirements.txt", "pylock.toml") if source_graph else ("requirements.txt",)):
                raise error_type("lock export format is unsupported for this transaction")
            output_format = value
        elif option in ("--generate-hashes", "--upgrade", "-U", "--no-header", "--no-annotate") and value:
            raise error_type("lock export boolean options cannot carry values")
        elif option in ("--format", "--generate-hashes", "--upgrade", "-U", "--no-header", "--no-annotate"):
            continue
        elif option in values:
            if option in ("-o", "--output-file") and value == "-":
                raise error_type("lock export output must be a file path or implicit standard output")
            result.extend([option, value])
        elif option in flags and not value:
            result.append(option)
        else:
            raise error_type("unsupported lock export option")
    if omissions and tuple(omissions) != source_packages:
        raise error_type("lock export omissions must match the retained source graph")
    return [*result, "--format", output_format, "--generate-hashes", "--upgrade", "--no-header", "--no-annotate",
            *[argument for name in source_packages for argument in ("--no-emit-package", name)]]


def check_compile_projection(
    projection, arguments, *, argument_pairs, balanced, error_type, source_graph=False, native_target=False,
):
    """Output policy is effective configuration, not merely a CLI intention."""
    if dict(argument_pairs(arguments)).get("--format") == "pylock.toml" and (
        not source_graph or not re.search(r"(?m)^\s*format:\s*Some\(\s*PylockToml,\s*\),\s*$", projection)
    ):
        raise error_type("UV source wheel-lock format differs from the selected output")
    for field, expected in (
        ("generate_hashes", "true"), ("no_header", "true"), ("no_annotate", "true"),
        ("emit_index_url", "false"), ("emit_find_links", "false"), ("emit_index_annotation", "false"),
    ):
        if not re.search(r"(?m)^\s*" + field + r":\s*" + expected + r",\s*$", projection):
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


def check_requirements_sources(
    path, seen, roots, base, projects, files, editables, *,
    check_project, check_requirement, error_type,
):
    projects = set() if projects is None else projects
    if path.suffix == ".toml":
        if path.name != "pyproject.toml":
            raise error_type("dependency TOML input lacks attributable project identity")
        check_project(path.parent, roots, projects)
        return
    path = path.resolve()
    if path in seen:
        raise error_type("cyclic requirement includes cannot prove dependency sources")
    seen = seen | {path}
    if files is not None:
        files.add(path)
    try:
        text = re.sub(r"\\\r?\n", " ", path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise error_type("dependency sources file could not be read") from exc
    for line in text.splitlines():
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line or line.startswith("#"):
            continue
        include = re.fullmatch(r"(?:-r|--requirements?|--constraints?|-c)\s+(.+)", line)
        if include:
            check_requirements_sources(
                path.parent / include[1], seen, roots, base, projects, files, editables,
                check_project=check_project, check_requirement=check_requirement, error_type=error_type,
            )
        elif line.startswith(("--index-url ", "--extra-index-url ", "--index-url=", "--extra-index-url=")):
            continue
        elif line.startswith(("-e ", "--editable ")):
            source = check_requirement(line.split(maxsplit=1)[1], base, roots, projects)
            if source is not None and editables is not None:
                editables.add(source)
        elif line.startswith("-"):
            raise error_type("unsupported dependency sources option")
        else:
            check_requirement(re.sub(r"\s+--hash=\S+", "", line), base, roots, projects)


def compile_requirement_includes(text, source, mirror, remap=None):
    """Retain include targets by identity, not by lexical path replacement."""
    lines = []
    for line in re.sub(r"\\\r?\n", " ", text).splitlines():
        requirement = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        include = re.fullmatch(r"(-r|--requirements?|--constraints?|-c)\s+(.+)", requirement)
        if include:
            line = include[1] + " " + mirror(source.parent / include[2]).as_posix()
        elif remap is not None:
            line = remap(line)
        lines.append(line)
    return "\n".join(lines) + "\n"


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
