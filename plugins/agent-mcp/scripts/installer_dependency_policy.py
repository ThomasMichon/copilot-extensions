"""Fail-closed seasoning for UV's actual configured resolver.

UV resolves its own configuration, including user and index settings. Its
development settings projection is intentionally treated as a versioned input:
an unknown representation refuses installation, never weakens the policy.
Captured settings may contain credentials and must never be printed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import types
import zipimport
from datetime import datetime, timezone
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

MIN_AGE_SECONDS = 604800
SETTINGS_TIMEOUT_SECONDS = 30
_HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_BUNDLED_TOML = None
_REMOTE_REFERENCE = re.compile(r"(?:https?|git(?:\+[^:]+)?|ssh|svn|hg|bzr)://", re.IGNORECASE)
_INDEX_OPTIONS = {"--index", "--index-url", "--default-index", "--extra-index-url", "-i"}
_FILE_OPTIONS = {
    "-r", "--requirements", "--requirement", "--constraint", "--constraints", "-c",
    "--override", "--overrides", "--build-constraint", "--build-constraints", "-b",
    "--excludes",
}
_VALUE_OPTIONS = _INDEX_OPTIONS | _FILE_OPTIONS | {
    "-e", "--editable", "--no-editable-package", "--extra", "--cert", "--group",
    "-t", "--target", "--prefix", "--no-binary", "--only-binary", "--python-version",
    "--python-platform", "--torch-backend", "-f", "--find-links", "--index-strategy",
    "--keyring-provider", "-P", "--upgrade-package", "--upgrade-group", "--resolution",
    "--prerelease", "--prerelease-package", "--fork-strategy", "--exclude-newer",
    "--exclude-newer-package", "--no-sources-package", "--reinstall-package", "--link-mode",
    "-C", "--config-setting", "--config-settings-package", "--no-build-isolation-package",
    "--cache-dir", "--refresh-package", "-p", "--python", "--color", "--allow-insecure-host",
    "--directory", "--project", "--config-file", "-o", "--output-file", "--format",
}


class DependencyPolicyError(RuntimeError):
    """An installation could not establish the seasoning policy."""


class _CompileMetadata(set):
    """Compile project roots are metadata inputs, never directory dependencies."""


def _captured_environment(env):
    result = {}
    for key, value in (os.environ if env is None else env).items():
        key = key.upper() if os.name == "nt" else key
        if key in result and result[key] != value:
            raise DependencyPolicyError("dependency installation environment has conflicting variable identities")
        result[key] = value
    return result


def _toml_parser():
    global _BUNDLED_TOML
    for name in ("tomllib", "tomli"):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            pass
    if _BUNDLED_TOML is not None:
        return _BUNDLED_TOML
    # Fresh Python 3.10 UV venvs have no pip/tomli, but their base stdlib
    # supplies ensurepip's parser. Import only that package, never wheel-wide pip.
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    bundled = _contained(stdlib / "ensurepip" / "_bundled", (stdlib,))
    for wheel in sorted(bundled.glob("pip-*.whl"), reverse=True):
        if wheel.is_symlink() or wheel.resolve().parent != bundled.resolve():
            continue
        namespace = "_installer_seasoning_bundled_tomli"
        package = types.ModuleType(namespace)
        package.__path__ = [str(wheel) + "/pip/_vendor/tomli"]
        try:
            importer = zipimport.zipimporter(package.__path__[0])
            code = importer.get_code("__init__")
            if code is None:
                continue
            sys.modules[namespace] = package
            exec(code, package.__dict__)
            _BUNDLED_TOML = package
            return package
        except (ImportError, OSError):
            for name in list(sys.modules):
                if name == namespace or name.startswith(namespace + "."):
                    del sys.modules[name]
    raise DependencyPolicyError("a proper TOML parser is unavailable in this bootstrap interpreter")


def _toml(path: Path) -> dict:
    parser = _toml_parser()
    try:
        return parser.loads(Path(_native_retained_path(path)).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DependencyPolicyError("first-party source TOML could not be read or parsed") from exc


def _identity(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise DependencyPolicyError("first-party source has no literal project identity")
    return re.sub(r"[-_.]+", "-", name).lower()


def _contained(path: Path, roots: tuple[Path, ...]) -> Path:
    lexical = Path(os.path.abspath(path))
    resolved = lexical.resolve()
    for root in roots:
        if lexical.is_relative_to(root) and resolved.is_relative_to(root):
            current = lexical
            while current != root:
                if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
                    break
                current = current.parent
            else:
                return resolved
    raise DependencyPolicyError("local source is outside attributable trusted source roots")


def _argument_pairs(arguments: list[str]):
    iterator = iter(arguments)
    positional = False
    for argument in iterator:
        if positional or not argument.startswith("-"):
            yield "", argument
        elif argument == "--":
            positional = True
        else:
            option, separator, value = argument.partition("=")
            if (not argument.startswith("--") and len(argument) > 2
                    and argument[:2] in _VALUE_OPTIONS):
                option, separator, value = argument[:2], "=", argument[2:]
            if option in _VALUE_OPTIONS:
                if not separator:
                    value = next(iterator, "")
                if not value or value.startswith("--"):
                    raise DependencyPolicyError("dependency installation option has no value")
                yield option, value
            else:
                yield option, value


def _check_requirement(
    requirement: str, base: Path, roots: tuple[Path, ...], seen: set[Path],
) -> Path | None:
    if _REMOTE_REFERENCE.search(requirement):
        raise DependencyPolicyError("direct remote dependencies lack release-age evidence")
    requirement = requirement.strip()
    expected = None
    path_input = re.sub(r"\[[^\]]*\]$", "", requirement)
    unnamed_source = (
        requirement.lower().startswith("file:")
        or Path(path_input).is_absolute()
        or (base / path_input).exists()
    )
    if "@" in requirement and not unnamed_source:
        name, requirement = requirement.split("@", 1)
        expected = _identity(name.strip().split("[", 1)[0])
        requirement = requirement.split(";", 1)[0].strip()
    if requirement.lower().startswith("file:"):
        parsed = urlsplit(requirement)
        if parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            raise DependencyPolicyError("local file URL lacks attributable source evidence")
        candidate = base / url2pathname(unquote(parsed.path))
    else:
        source = re.sub(r"\[[^\]]*\]$", "", requirement)
        candidate = base / source
        if not (expected or candidate.exists() or source in (".", "..")
                or re.search(r"[/\\]|^[A-Za-z]:|(?i:\.(whl|zip|tar\.gz|tgz))$", source)):
            return
    candidate = _contained(candidate, roots)
    if not candidate.is_dir():
        raise DependencyPolicyError("local artifacts cannot prove release age; only owned source directories are exempt")
    if isinstance(seen, _CompileMetadata):
        raise DependencyPolicyError("lock export cannot retain directory dependencies or local artifacts")
    _check_project(candidate, roots, seen, expected)
    return candidate


def _bundled_dbus_next(project, name, wheel):
    return (project == "copilot-extensions-worktree-manager" and name == "dbus-next"
            and wheel.name == "dbus_next-0.2.3-py3-none-any.whl" and wheel.is_file()
            and hashlib.sha256(wheel.read_bytes()).hexdigest()
            == "58948f9aff9db08316734c0be2a120f6dc502124d9642f55e90ac82ffb16a18b")


def _check_project(
    source: Path, roots: tuple[Path, ...], seen: set[Path], expected: str | None = None,
) -> None:
    try:
        _inspect_project(source, roots, seen, expected)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise DependencyPolicyError("source dependency metadata is unsupported") from exc


def _inspect_project(
    source: Path, roots: tuple[Path, ...], seen: set[Path], expected: str | None = None,
) -> None:
    source = _contained(source, roots)
    manifest = _contained(source / "pyproject.toml", roots)
    data = _toml(manifest)
    project = data.get("project", {})
    if not isinstance(project, dict):
        raise DependencyPolicyError("source project metadata is unsupported")
    identity = _identity(project.get("name"))
    if expected is not None and identity != _identity(expected):
        raise DependencyPolicyError("local source project identity does not match its dependency")
    if source in seen:
        return
    seen.add(source)
    if set(project.get("dynamic", [])) & {"dependencies", "optional-dependencies"}:
        raise DependencyPolicyError("dynamic local dependency sources cannot be attributed")
    uv = data.get("tool", {}).get("uv", {})
    if not isinstance(uv, dict):
        raise DependencyPolicyError("source UV metadata is unsupported")
    indexes = uv.get("index", [])
    if not isinstance(indexes, list) or any(not isinstance(entry, dict) for entry in indexes):
        raise DependencyPolicyError("source registry configuration is unsupported")
    if any(entry.get("exclude-newer") is not None for entry in indexes):
        raise DependencyPolicyError("index-specific exclude-newer cannot prove artifact upload age")
    mappings = uv.get("sources", {})
    if not isinstance(mappings, dict):
        raise DependencyPolicyError("local UV source graph is unsupported")
    for name, entries in mappings.items():
        for entry in entries if isinstance(entries, list) else [entries]:
            if not isinstance(entry, dict):
                raise DependencyPolicyError("local UV source entry is unsupported")
            if "path" in entry:
                if (set(entry) - {"path", "editable", "extra", "group", "marker"}
                        or not isinstance(entry["path"], str)):
                    raise DependencyPolicyError("local UV path source is unsupported")
                child = _contained(source / entry["path"], roots)
                if isinstance(seen, _CompileMetadata):
                    raise DependencyPolicyError("lock export cannot retain directory dependencies or local artifacts")
                if _bundled_dbus_next(identity, name, child):
                    continue
                if not child.is_dir():
                    raise DependencyPolicyError("local UV artifacts cannot prove release age")
                _check_project(child, roots, seen, _identity(name))
            elif set(entry) - {"index", "extra", "group", "marker"}:
                raise DependencyPolicyError("non-registry UV sources lack release-age evidence")
    sections = [
        data.get("build-system", {}).get("requires", []), project.get("dependencies", []),
        uv.get("dev-dependencies", []), uv.get("constraint-dependencies", []),
        uv.get("override-dependencies", []), uv.get("build-constraint-dependencies", []),
        *project.get("optional-dependencies", {}).values(),
        *data.get("dependency-groups", {}).values(),
    ]
    for entry in uv.get("dependency-metadata", []):
        if not isinstance(entry, dict):
            raise DependencyPolicyError("source dependency metadata is unsupported")
        sections.append(entry.get("requires-dist", []))
    for entries in uv.get("extra-build-dependencies", {}).values():
        if not isinstance(entries, list):
            raise DependencyPolicyError("extra build dependencies are unsupported")
        sections.append([
            entry.get("requirement") if isinstance(entry, dict) else entry for entry in entries
        ])
    for section in sections:
        if not isinstance(section, list):
            raise DependencyPolicyError("source dependency declarations are unsupported")
        for requirement in section:
            if isinstance(requirement, dict) and set(requirement) == {"include-group"}:
                continue
            if not isinstance(requirement, str):
                raise DependencyPolicyError("source dependency declaration is unsupported")
            _check_requirement(requirement, source, roots, seen)


def _anchor_roots() -> tuple[Path, ...]:
    """CLI-only attribution from the helper's owner, never installation inputs."""
    helper = Path(__file__).resolve()
    owner_payload = None
    repository = None
    if helper.parent.name == "scripts":
        payload = helper.parent.parent
        try:
            manifest = json.loads(_contained(payload / "plugin.json", (payload,)).read_text(encoding="utf-8"))
            project = _toml(_contained(payload / "pyproject.toml", (payload,)))["project"]
            if (_identity(manifest["name"]) == _identity(project["name"])
                    and manifest["version"] == project["version"]
                    and manifest.get("repository") == "https://github.com/ThomasMichon/copilot-extensions"):
                candidate = payload.parent.parent
                if (payload.parent.name == "plugins"
                        and (candidate / ".github" / "plugin" / "marketplace.json").is_file()):
                    repository, owner_payload = candidate, payload
                else:
                    return (payload,)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if repository is None:
            return ()
    # Canonical source graphs are roster-owned plugins plus their explicitly
    # named library references. Standalone projects need caller attestation.
    if repository is None:
        if helper.parent.name != "installer-engine" or helper.parent.parent.name != "libs":
            return ()
        repository = helper.parents[2]
    roster = repository / ".github" / "plugin" / "marketplace.json"
    if not roster.is_file():
        return ()
    try:
        data = json.loads(_contained(roster, (repository,)).read_text(encoding="utf-8"))
        if data.get("name") != "copilot-extensions":
            return ()
        roots = set()
        pending = []
        for entry in data["plugins"]:
            source = entry.get("source", "")
            if not isinstance(source, str) or not re.fullmatch(r"plugins/[A-Za-z0-9_-]+", source):
                continue
            path = _contained(repository / source, (repository,))
            manifest_path = _contained(path / "plugin.json", (repository,))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["name"] != entry["name"] or manifest["version"] != entry["version"]:
                raise DependencyPolicyError("canonical plugin roster identity is inconsistent")
            if (path / "pyproject.toml").is_file():
                project = _toml(_contained(path / "pyproject.toml", (repository,)))
                if _identity(project["project"]["name"]) != _identity(entry["name"]):
                    raise DependencyPolicyError("canonical project identity is inconsistent")
                roots.add(path)
                pending.append((path, path / "libs"))
        if owner_payload is not None and owner_payload not in roots:
            raise DependencyPolicyError("installer helper owner is not a roster-attributed project")
        while pending:
            source, library_root = pending.pop()
            mappings = _toml(_contained(source / "pyproject.toml", (repository,))).get("tool", {}).get("uv", {}).get("sources", {})
            for name, entries in mappings.items():
                for entry in entries if isinstance(entries, list) else [entries]:
                    if not isinstance(entry, dict) or "path" not in entry:
                        continue
                    child = _contained(source / entry["path"], (repository,))
                    if not (child.is_relative_to(repository / "libs")
                            or child.is_relative_to(library_root)):
                        raise DependencyPolicyError("canonical local graph lacks library attribution")
                    if _identity(_toml(_contained(child / "pyproject.toml", (repository,)))["project"]["name"]) != _identity(name):
                        raise DependencyPolicyError("canonical library identity is inconsistent")
                    if child not in roots:
                        roots.add(child)
                        child_root = repository / "libs" if child.is_relative_to(repository / "libs") else library_root
                        pending.append((child, child_root))
        return tuple(sorted(roots))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DependencyPolicyError("canonical source ownership could not be established") from exc


def _balanced(text: str, start: int, opening: str, closing: str) -> tuple[str, int]:
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
                return text[start + 1:offset], offset + 1
    raise DependencyPolicyError("UV settings projection is incomplete")


def cutoff_arguments(
    settings: str, *, now: datetime | None = None, _operation: str = "install",
) -> list[str]:
    """Honor a stronger global cutoff; reject package/index escape hatches."""
    return _configuration.cutoff_arguments(
        settings, now=now, operation=_operation, min_age=MIN_AGE_SECONDS,
        balanced=_balanced, remote_reference=_REMOTE_REFERENCE, error_type=DependencyPolicyError,
    )


def _source_inputs(arguments, environment, cwd, trusted_source_roots, *, operation="install"):
    if "--" in arguments:
        raise DependencyPolicyError("UV end-of-options markers cannot precede mandatory resolver policy")
    if any(arg.split("=", 1)[0] in ("--show-settings", "--dry-run") for arg in arguments):
        raise DependencyPolicyError("a settings-only invocation is not a dependency installation")
    if any(arg.split("=", 1)[0] in ("--no-deps", "--no-build-isolation", "--no-build-isolation-package")
           for arg in arguments):
        raise DependencyPolicyError("dependency or isolated-build resolution cannot bypass seasoning")
    # Registry/index URLs still use UV's artifact timestamps. Direct URL/Git
    # requirements do not; only local first-party source inputs are exempt.
    roots = tuple(Path(root).resolve() for root in trusted_source_roots)
    if any(not Path(root).is_absolute() for root in trusted_source_roots):
        raise DependencyPolicyError("trusted source roots must be absolute caller-attributed paths")
    if any(not root.is_dir() for root in roots):
        raise DependencyPolicyError("trusted source root is not an existing directory")
    base = Path(cwd or Path.cwd()).resolve()
    pairs = list(_argument_pairs(arguments))
    directory = environment.get("UV_WORKING_DIR")
    project_directory = environment.get("UV_PROJECT")
    for option, value in pairs:
        if option == "--directory":
            directory = value
        elif option == "--project":
            project_directory = value
    if directory:
        base = (base / directory).resolve()
    seen: set[Path] = _CompileMetadata() if operation == "compile" else set()
    files: set[Path] = set()
    editables: set[Path] = set()
    for option, value in pairs:
        if option in _INDEX_OPTIONS:
            if re.search(r"\s|@", value) and _REMOTE_REFERENCE.search(value):
                # Userinfo is allowed, but a named requirement is not an index.
                if not re.fullmatch(r"(?:https?|file)://\S+", value, re.IGNORECASE):
                    raise DependencyPolicyError("direct remote dependencies lack release-age evidence")
            continue
        if _REMOTE_REFERENCE.search(value):
            raise DependencyPolicyError("direct remote dependencies lack release-age evidence")
        if option in _FILE_OPTIONS:
            path = base / value
            if path.suffix == ".toml":
                if path.name != "pyproject.toml":
                    raise DependencyPolicyError("dependency TOML input lacks attributable project identity")
                _check_project(path.parent, roots, seen)
            else:
                _check_requirements_sources(path, set(), roots, base, seen, files, editables)
        elif not option and operation == "compile":
            _check_requirements_sources(base / value, set(), roots, base, seen, files, editables)
        elif option in ("", "-e", "--editable"):
            source = _check_requirement(value, base, roots, seen)
            if source is not None and option in ("-e", "--editable"):
                editables.add(source)
        elif option in ("-f", "--find-links"):
            raise DependencyPolicyError("flat-index dependencies cannot prove artifact upload age")
        elif option == "--group":
            project_path = base / (project_directory or ".")
            if ":" in value:
                project_path = base / value.rsplit(":", 1)[0]
            _check_project(project_path, roots, seen)
        elif "file:" in value.lower():
            raise DependencyPolicyError("local file reference in an unsupported option")
    if project_directory:
        manifest = base / project_directory / "pyproject.toml"
        if manifest.is_file():
            _check_project(manifest.parent, roots, seen)
    for path, is_project in _config_inputs(arguments, environment, base):
        data = _toml(path)
        if is_project:
            data = data["tool"]["uv"]
        if not isinstance(data, dict):
            raise DependencyPolicyError("configured UV metadata is unsupported")
        if data.get("sources"):
            if not is_project:
                raise DependencyPolicyError("local UV sources require attributable project metadata")
            _check_project(path.parent, roots, seen)
        sections = [data.get(key, []) for key in (
            "constraint-dependencies", "override-dependencies", "build-constraint-dependencies",
        )]
        for entry in data.get("dependency-metadata", []):
            if not isinstance(entry, dict):
                raise DependencyPolicyError("configured dependency metadata is unsupported")
            sections.append(entry.get("requires-dist", []))
        extra_build = data.get("extra-build-dependencies", {})
        if not isinstance(extra_build, dict):
            raise DependencyPolicyError("configured build dependencies are unsupported")
        for entries in extra_build.values():
            if not isinstance(entries, list):
                raise DependencyPolicyError("configured build dependencies are unsupported")
            sections.append([
                entry.get("requirement") if isinstance(entry, dict) else entry for entry in entries
            ])
        for section in sections:
            if not isinstance(section, list):
                raise DependencyPolicyError("configured dependency declarations are unsupported")
            for requirement in section:
                if not isinstance(requirement, str):
                    raise DependencyPolicyError("configured dependency declaration is unsupported")
                _check_requirement(requirement, base, roots, seen)
    for variable in ("UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE", "UV_EXCLUDE"):
        if environment.get(variable):
            for path in environment[variable].split():
                _check_requirements_sources(base / path, set(), roots, base, seen, files, editables)
    source_editables = (
        "--no-sources" not in dict(pairs) and "--no-editable" not in dict(pairs)
        and environment.get("UV_NO_SOURCES", "").lower() not in ("1", "true")
        and environment.get("UV_NO_EDITABLE", "").lower() not in ("1", "true")
    )
    for project in seen if source_editables else ():
        mappings = _toml(project / "pyproject.toml").get("tool", {}).get("uv", {}).get("sources", {})
        for entries in mappings.values():
            for entry in entries if isinstance(entries, list) else [entries]:
                if entry.get("editable") is True and "path" in entry:
                    editables.add(_contained(project / entry["path"], roots))
    return base, seen, files, editables


def prepare_uv_install(
    uv: str, arguments: list[str], *, env: dict[str, str] | None = None,
    cwd: Path | None = None, now: datetime | None = None,
    trusted_source_roots: tuple[Path, ...] = (),
    _projection: list[str] | None = None,
    _operation: str = "install",
) -> list[str]:
    """Preflight only. Installations must execute a retained UV transaction."""
    env = _captured_environment(env)
    if _operation == "compile":
        arguments = _configuration.compile_arguments(
            arguments, argument_pairs=_argument_pairs, error_type=DependencyPolicyError,
        )
    _source_inputs(
        arguments, env, cwd, trusted_source_roots,
        **({"operation": _operation} if _operation != "install" else {}),
    )
    uv = _select_uv_executable(uv)
    probe_arguments = [
        argument for argument in arguments
        if argument != "--quiet" and not re.fullmatch(r"-q+", argument)
    ]
    command = [uv, "pip", _operation, "--show-settings", *probe_arguments]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, env=env, cwd=cwd,
            timeout=SETTINGS_TIMEOUT_SECONDS, creationflags=_HEADLESS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DependencyPolicyError("UV seasoning configuration probe failed or timed out") from exc
    if result.returncode != 0:
        # Do not include UV's debug projection or credential-bearing arguments.
        raise DependencyPolicyError("UV seasoning configuration probe failed")
    admitted = cutoff_arguments(result.stdout, now=now, _operation=_operation)
    if _operation == "compile":
        _configuration.check_compile_projection(
            result.stdout, arguments, argument_pairs=_argument_pairs,
            balanced=_balanced, error_type=DependencyPolicyError,
        )
    if _projection is not None:
        _projection.append(result.stdout)
    cleaned = []
    iterator = iter(arguments)
    for argument in iterator:
        if argument in ("--exclude-newer", "--exclude-newer-package"):
            next(iterator, None)
        elif (not argument.startswith(("--exclude-newer=", "--exclude-newer-package="))
              and argument not in ("--reinstall", "--force-reinstall")):
            cleaned.append(argument)
    return [*cleaned, *admitted]


def _check_requirements_sources(
    path: Path, seen: set[Path], roots: tuple[Path, ...], base: Path,
    projects: set[Path] | None = None, files: set[Path] | None = None,
    editables: set[Path] | None = None,
) -> None:
    projects = set() if projects is None else projects
    if path.suffix == ".toml":
        if path.name != "pyproject.toml":
            raise DependencyPolicyError("dependency TOML input lacks attributable project identity")
        _check_project(path.parent, roots, projects)
        return
    path = path.resolve()
    if path in seen:
        raise DependencyPolicyError("cyclic requirement includes cannot prove dependency sources")
    seen = seen | {path}
    if files is not None:
        files.add(path)
    try:
        text = re.sub(r"\\\r?\n", " ", path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DependencyPolicyError("dependency sources file could not be read") from exc
    for line in text.splitlines():
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line or line.startswith("#"):
            continue
        include = re.fullmatch(r"(?:-r|--requirements?|--constraints?|-c)\s+(.+)", line)
        if include:
            _check_requirements_sources(
                path.parent / include[1], seen, roots, base, projects, files, editables,
            )
        elif line.startswith(("--index-url ", "--extra-index-url ", "--index-url=", "--extra-index-url=")):
            continue
        elif line.startswith(("-e ", "--editable ")):
            source = _check_requirement(line.split(maxsplit=1)[1], base, roots, projects)
            if source is not None and editables is not None:
                editables.add(source)
        elif line.startswith("-"):
            raise DependencyPolicyError("unsupported dependency sources option")
        else:
            _check_requirement(re.sub(r"\s+--hash=\S+", "", line), base, roots, projects)


_configuration_path = Path(__file__).with_name("installer_dependency_config.py")
_configuration = types.ModuleType(__name__ + "_configuration")
_configuration.__file__ = str(_configuration_path)
exec(compile(_configuration_path.read_bytes(), str(_configuration_path), "exec"), _configuration.__dict__)
RetainedInputCleanupError = _configuration.RetainedInputCleanupError
_report_cleanup_error = _configuration.report_cleanup_error
_native_retained_path = _configuration.native_retained_path
_temporary_inputs = _configuration.temporary_inputs
tempfile = _configuration.tempfile
_write_toml = partial(_configuration.write_toml, error_type=DependencyPolicyError)
_private_retained_root = partial(_configuration.private_retained_root, error_type=DependencyPolicyError)
_merge_config = _configuration.merge_config
_select_uv_executable = partial(_configuration.select_uv_executable, error_type=DependencyPolicyError)
_metadata_needs_caller_layout = _configuration.metadata_needs_caller_layout
_cutoff_value = partial(_configuration.cutoff_value, error_type=DependencyPolicyError)
_package_cutoffs = partial(_configuration.package_cutoffs, balanced=_balanced, error_type=DependencyPolicyError)
_resolver_configuration = partial(
    _configuration.resolver_configuration, balanced=_balanced, error_type=DependencyPolicyError,
)


def _config_inputs(arguments, environment, base):
    return _configuration.config_inputs(
        arguments, environment, base, argument_pairs=_argument_pairs,
        toml=_toml, error_type=DependencyPolicyError,
    )


def _copy_retained_file(source, destination):
    return shutil.copy2(_native_retained_path(source), _native_retained_path(destination))


class RetainedUVInstall(NamedTuple):
    """Admitted command and its single, context-owned retained input graph."""

    argv: tuple[str, ...]
    environment: dict[str, str]
    cwd: Path
    sources: dict[Path, Path]
    source_hashes: dict[Path, str]


@contextmanager
def retain_uv_install(
    uv: str, arguments: list[str], *, env: dict[str, str] | None = None,
    cwd: Path | None = None, now: datetime | None = None,
    trusted_source_roots: tuple[Path, ...] = (),
    retained_resource_roots: tuple[Path, ...] = (),
    _operation: str = "install",
):
    """Own the admitted graph until the caller's execution and health settle."""
    now = datetime.now(timezone.utc) if now is None else now
    environment = _captured_environment(env)
    if _operation == "compile":
        arguments = _configuration.compile_arguments(
            arguments, argument_pairs=_argument_pairs, error_type=DependencyPolicyError,
        )
    base, projects, files, editables = _source_inputs(
        arguments, environment, cwd, trusted_source_roots,
        **({"operation": _operation} if _operation != "install" else {}),
    )
    uv = _select_uv_executable(uv)
    resources = []
    for resource in retained_resource_roots:
        if not Path(resource).is_absolute():
            raise DependencyPolicyError("retained build resources must be absolute caller-attributed paths")
        source = _contained(Path(resource), tuple(Path(path).resolve() for path in trusted_source_roots))
        if not source.is_dir():
            raise DependencyPolicyError("retained build resource root is not a directory")
        for child in source.rglob("*"):
            if child.is_symlink() or (
                getattr(child.lstat(), "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise DependencyPolicyError("retained build resources must not be linked")
        resources.append(source)
    for variable in (
        "UV_CACHE_DIR", "UV_CREDENTIALS_DIR", "UV_TARGET", "UV_PREFIX",
        "VIRTUAL_ENV", "CONDA_PREFIX", "SSL_CERT_FILE",
    ):
        if environment.get(variable):
            environment[variable] = os.path.abspath(base / environment[variable])
    if environment.get("SSL_CERT_DIR"):
        environment["SSL_CERT_DIR"] = os.pathsep.join(
            os.path.abspath(base / directory) for directory in environment["SSL_CERT_DIR"].split(os.pathsep)
        )
    if environment.get("UV_PYTHON"):
        value = environment["UV_PYTHON"]
        if Path(value).is_absolute() or (base / value).exists() or re.search(r"[/\\]", value):
            environment["UV_PYTHON"] = os.path.abspath(base / value)
    normalized = []
    for option, value in _argument_pairs(arguments):
        if option in ("--target", "-t", "--prefix", "--cache-dir", "--cert", "-o", "--output-file"):
            value = os.path.abspath(base / value)
        elif option in ("-p", "--python"):
            if Path(value).is_absolute() or (base / value).exists() or re.search(r"[/\\]", value):
                value = os.path.abspath(base / value)
        if option and value:
            normalized.extend([option, value] if option in _VALUE_OPTIONS else [option + "=" + value])
        else:
            normalized.append(option or value)
    arguments = normalized
    original_projection = []
    original = prepare_uv_install(
        uv, arguments, env=environment, cwd=cwd, now=now,
        trusted_source_roots=trusted_source_roots, _projection=original_projection,
        **({"_operation": _operation} if _operation != "install" else {}),
    )
    if editables and re.search(r'"editable_mode"\s*:', _resolver_configuration(original_projection[0], now=now)):
        raise DependencyPolicyError("retained editable metadata owns the setuptools editable mode")
    temporary_parent = None
    if os.name == "nt" and editables:
        drives = {path.drive.lower() for path in editables}
        if len(drives) != 1:
            raise DependencyPolicyError("editable input retention requires one source volume")
        # Keep relative setuptools paths short and outside every copied input.
        volume_inputs = (
            path for path in (*projects, *resources) if path.drive.lower() in drives
        )
        shallowest = min(volume_inputs, key=lambda path: (len(path.parts), str(path)))
        temporary_parent = shallowest.parent
        if temporary_parent == shallowest:
            raise DependencyPolicyError("editable retention needs temporary space outside copied inputs")
    with _temporary_inputs(temporary_parent) as temporary:
        root = _private_retained_root(Path(temporary)).resolve()
        if any(root.is_relative_to(path) for path in (*projects, *resources)):
            raise DependencyPolicyError("retained snapshot must be outside copied inputs")
        context_project = dict(_argument_pairs(arguments)).get("--project") or environment.get("UV_PROJECT")
        pairs = list(_argument_pairs(arguments))
        operands = [value for option, value in pairs if not option]
        compact_sources = (
            projects and operands and not files and not editables and not context_project
            and not any(option in _FILE_OPTIONS | {"--group"} for option, _ in pairs)
            and not any(environment.get(key) for key in (
                "UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE", "UV_EXCLUDE",
            ))
            and all(Path(value).is_absolute() for value in operands)
            and not any(_metadata_needs_caller_layout(_toml(project / "pyproject.toml")) for project in projects)
            and not any(_metadata_needs_caller_layout(_toml(path))
                        for path, _ in _config_inputs(arguments, environment, base))
        )
        # An unrelated caller cwd must not force absolute build inputs to retain
        # their entire checkout/staging ancestry. Relative-input layouts stay intact.
        paths = [*projects, *files, *resources]
        if not compact_sources:
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
            anchor = hashlib.sha256(path.anchor.encode()).hexdigest()[:12]
            return root / anchor / path.relative_to(anchors[path.anchor.lower()])

        def remap(value):
            if isinstance(value, list):
                return [remap(item) for item in value]
            if isinstance(value, dict):
                return {key: remap(item) for key, item in value.items()}
            if not isinstance(value, str):
                return value
            replacements = {}
            for path in sorted(projects | files, key=lambda item: len(str(item)), reverse=True):
                for before, after in (
                    (path.as_uri(), mirror(path).as_uri()),
                    (str(path), str(mirror(path))),
                    (path.as_posix(), mirror(path).as_posix()),
                    (json.dumps(str(path))[1:-1], json.dumps(str(mirror(path)))[1:-1]),
                ):
                    replacements[before] = after
            if not replacements:
                return value
            return re.sub(
                "|".join(re.escape(before) for before in replacements),
                lambda match: replacements[match[0]], value,
            )

        for project in sorted(projects, key=lambda path: len(path.parts)):
            destination = mirror(project)
            if not Path(_native_retained_path(destination)).is_dir():
                shutil.copytree(
                    _native_retained_path(project), _native_retained_path(destination),
                    copy_function=_copy_retained_file,
                    ignore=shutil.ignore_patterns(
                        ".git", ".venv", "__pycache__", "*.egg-info",
                        ".pytest_cache", ".ruff_cache", "node_modules", "build", "dist",
                    ),
                )
        for resource in resources:
            shutil.copytree(_native_retained_path(resource), _native_retained_path(mirror(resource)), dirs_exist_ok=True,
                            copy_function=_copy_retained_file)
        source_hashes = {}
        for project in projects:
            destination = Path(_native_retained_path(mirror(project)))
            for path in destination.rglob("*"):
                if path.is_file():
                    original_path = project / path.relative_to(destination)
                    retained_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                    if hashlib.sha256(Path(_native_retained_path(original_path)).read_bytes()).hexdigest() != retained_hash:
                        raise DependencyPolicyError("source content changed while retaining UV inputs")
                    source_hashes[original_path] = retained_hash
        for project in projects:
            manifest = mirror(project) / "pyproject.toml"
            original_data = _toml(manifest)
            data = remap(original_data)
            if data != original_data:
                _write_toml(Path(_native_retained_path(manifest)), data)
        for project in editables:
            manifest = mirror(project) / "pyproject.toml"
            data = _toml(manifest)
            setuptools = data.get("tool", {}).get("setuptools", {})
            packages = setuptools.get("packages", {}) if isinstance(setuptools, dict) else {}
            find = packages.get("find", {}) if isinstance(packages, dict) else {}
            if (data.get("build-system", {}).get("build-backend") != "setuptools.build_meta"
                    or data.get("project", {}).get("dynamic")
                    or not isinstance(setuptools, dict) or not isinstance(find, dict)
                    or find.get("where") != ["src"]
                    or setuptools.get("package-dir") not in (None, {"": "src"})):
                raise DependencyPolicyError("editable retention requires static setuptools src-layout metadata")
            source = _contained(project / "src", tuple(Path(path).resolve() for path in trusted_source_roots))
            # Only live Python code is referenced outside the retained metadata.
            # compat emits a .pth to this original src, not the temporary project.
            relative = Path(os.path.relpath(source, mirror(project))).as_posix()
            find["where"] = [relative]
            setuptools["package-dir"] = {"": relative}
            _write_toml(Path(_native_retained_path(manifest)), data)
        for path in files:
            destination = mirror(path)
            Path(_native_retained_path(destination.parent)).mkdir(parents=True, exist_ok=True)
            text = Path(_native_retained_path(path)).read_text(encoding="utf-8")
            if _operation == "compile":
                text = _configuration.compile_requirement_includes(text, path, mirror, remap)
            else:
                text = remap(text)
            Path(_native_retained_path(destination)).write_text(
                text, encoding="utf-8")
        retained_config = {}
        for path, is_project in reversed(_config_inputs(arguments, environment, base)):
            data = _toml(path)
            if is_project:
                data = {
                    key: value for key, value in data["tool"]["uv"].items()
                    if key not in ("sources", "workspace")
                }
            retained_config = _merge_config(data, retained_config)
        config = root / "uv.toml"
        _write_toml(config, remap(retained_config))
        retained_environment = dict(environment)
        retained_environment["UV_CONFIG_FILE"] = str(config)
        active = (
            environment.get("VIRTUAL_ENV") and (Path(environment["VIRTUAL_ENV"]) / "pyvenv.cfg").is_file()
        ) or (
            environment.get("CONDA_PREFIX") and (Path(environment["CONDA_PREFIX"]) / "conda-meta").is_dir()
        )
        if not active:
            for directory in (base, *base.parents):
                if (directory / ".venv" / "pyvenv.cfg").is_file():
                    retained_environment["VIRTUAL_ENV"] = str(directory / ".venv")
                    break
        for variable in ("UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE", "UV_EXCLUDE"):
            if variable in retained_environment:
                retained_environment[variable] = " ".join(
                    str(mirror(base / path)) for path in environment[variable].split()
                )
        base_anchor = anchors.get(base.anchor.lower())
        retained_base = (
            mirror(base) if base_anchor is not None and base.is_relative_to(base_anchor)
            else root / "cwd"
        )
        Path(_native_retained_path(retained_base)).mkdir(parents=True, exist_ok=True)
        retained_environment.pop("UV_WORKING_DIR", None)
        retained_environment.pop("UV_PROJECT", None)
        retained = []
        for option, value in _argument_pairs(original):
            if option in ("--directory", "--config-file"):
                continue
            if (_operation == "compile" and not option) or option in _FILE_OPTIONS or option == "--project":
                value = str(mirror(base / value))
            elif option in ("-p", "--python", "--target", "-t", "--prefix", "--cache-dir", "--cert"):
                if (base / value).exists() or Path(value).is_absolute():
                    value = os.path.abspath(base / value)
            elif option not in ("-o", "--output-file"):
                value = remap(value)
            if option and value:
                retained.extend([option, value] if option in _VALUE_OPTIONS else [option + "=" + value])
            else:
                retained.append(option or value)
        # --config-file is exclusive: no mutable ambient file can be rediscovered.
        retained.extend(["--config-file", str(config)])
        if context_project and "--project" not in dict(_argument_pairs(original)):
            retained.extend(["--project", str(mirror(base / context_project))])
        retained_roots = tuple(mirror(path) for path in projects)
        for path in retained_roots:
            Path(_native_retained_path(path)).mkdir(parents=True, exist_ok=True)
        retained_projection = []
        admitted = prepare_uv_install(
            uv, retained, env=retained_environment, cwd=retained_base, now=now,
            trusted_source_roots=retained_roots, _projection=retained_projection,
            **({"_operation": _operation} if _operation != "install" else {}),
        )
        original_configuration = _resolver_configuration(
            original_projection[0], remap, now=now, freeze_package_cutoffs=True,
        )
        retained_configuration = _resolver_configuration(retained_projection[0], now=now)
        if original_configuration != retained_configuration:
            raise DependencyPolicyError("retained UV configuration differs from the configured resolver")
        if editables:
            admitted.extend(["--config-setting", "editable_mode=compat"])
        yield RetainedUVInstall(
            (uv, "pip", _operation, *admitted), retained_environment, retained_base,
            {path: mirror(path) for path in projects}, source_hashes,
        )


def run_uv_install(
    uv: str, arguments: list[str], *, env: dict[str, str] | None = None,
    cwd: Path | None = None, now: datetime | None = None,
    trusted_source_roots: tuple[Path, ...] = (),
    retained_resource_roots: tuple[Path, ...] = (), _operation: str = "install", **run_options,
) -> subprocess.CompletedProcess:
    """Audit and execute the same private source, requirements and configuration."""
    result = None
    try:
        with retain_uv_install(
            uv, arguments, env=env, cwd=cwd, now=now,
            trusted_source_roots=trusted_source_roots,
            retained_resource_roots=retained_resource_roots,
            **({"_operation": _operation} if _operation != "install" else {}),
        ) as transaction:
            run_options.setdefault("creationflags", _HEADLESS)
            result = subprocess.run(
                list(transaction.argv), env=transaction.environment,
                cwd=transaction.cwd, **run_options,
            )
            return result
    except RetainedInputCleanupError as exc:
        if result is None or result.returncode == 0:
            raise
        _report_cleanup_error(exc)
        return result


def prepare_uv_compile(uv: str, arguments: list[str], **options) -> list[str]:
    """Preflight a hashed requirements export; execute only through retention."""
    return prepare_uv_install(uv, arguments, _operation="compile", **options)


def retain_uv_compile(uv: str, arguments: list[str], **options):
    """Retain the admitted compile graph for the lifetime of its execution."""
    return retain_uv_install(uv, arguments, _operation="compile", **options)


def run_uv_compile(uv: str, arguments: list[str], **options) -> subprocess.CompletedProcess:
    """Execute a retained, seasoned, hashed requirements export."""
    return run_uv_install(uv, arguments, _operation="compile", **options)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", required=True)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--trusted-source-root", action="append", type=Path, default=[])
    sources.add_argument(
        "--registry-only", action="store_true", help="Deny local projects without discovering product roots",
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    try:
        roots = () if args.registry_only else (tuple(args.trusted_source_root) or _anchor_roots())
        return run_uv_install(
            args.uv, arguments, trusted_source_roots=roots,
            stdout=sys.stdout, stderr=sys.stderr,
        ).returncode
    except RetainedInputCleanupError as exc:
        _report_cleanup_error(exc)
        return 1
    except (DependencyPolicyError, OSError) as exc:
        print(f"dependency-seasoning: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
