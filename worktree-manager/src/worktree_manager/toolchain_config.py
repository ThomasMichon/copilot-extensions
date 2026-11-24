"""Data-only adopter defaults for the standard bootstrap's native tools."""
from __future__ import annotations

import configparser
from contextlib import ExitStack
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

CONFIGURATION_ENV = "COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG"
FIELDS = {
    "python_index_url": ("UV_DEFAULT_INDEX", "PIP_INDEX_URL"),
    "python_install_mirror": ("UV_PYTHON_INSTALL_MIRROR",),
    "uv_install_mirror": ("UV_INSTALLER_GITHUB_BASE_URL",),
    "npm_registry": ("npm_config_registry",),
}
URL = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~:/+-]*)?")
MAX_SIZE = 16384


def _routing_environment(current: Mapping[str, str]) -> dict[str, str]:
    result = dict(current)
    version = current.get("COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST")
    if version not in (None, "", "1"):
        raise ValueError("Unsupported toolchain request context")
    if version == "1" and not current.get(CONFIGURATION_ENV):
        raise ValueError("Toolchain request context requires its selected configuration")
    if current.get("COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST") == "1" and os.name != "nt":
        for encoded, original in (
            ("COPILOT_EXTENSIONS_NPM_REGISTRY", "npm_config_registry"),
            ("COPILOT_EXTENSIONS_NPM_USERCONFIG", "npm_config_userconfig"),
            ("COPILOT_EXTENSIONS_NPM_GLOBALCONFIG", "npm_config_globalconfig"),
        ):
            result.pop(original, None)
            if encoded in current:
                result[original] = current[encoded]
    return result


def read_configuration(path: Path) -> dict[str, str]:
    """Read the same bounded, deliberately non-executable grammar as the seed."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Toolchain configuration must be an ordinary file")
    with path.open("rb") as stream:
        data = stream.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise ValueError("Toolchain configuration exceeds 16384 bytes")
    if any(byte < 32 and byte not in (9, 10, 13) for byte in data) or 127 in data:
        raise ValueError("Control characters in toolchain configuration")
    values: dict[str, str] = {}
    for line in data.decode("ascii").split("\n"):
        line = line.removesuffix("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([a-z_]+)=([^\s]+)", line)
        if match is None:
            raise ValueError("Malformed toolchain configuration")
        key, value = match.groups()
        if key in values or key not in {"schema", *FIELDS}:
            raise ValueError("Unknown or duplicate toolchain configuration key")
        if (key == "schema" and value != "1") or (
            key != "schema" and (len(value) > 2048 or URL.fullmatch(value) is None)
        ):
            raise ValueError("Unsupported toolchain schema or source URL")
        values[key] = value
    if values.pop("schema", None) != "1" or not values:
        raise ValueError("Toolchain configuration requires schema=1 and a source")
    return values


def _same(actual: str, expected: str) -> bool:
    return actual.rstrip("/") == expected.rstrip("/")


def environment(values: Mapping[str, str], current: Mapping[str, str]) -> dict[str, str]:
    """Preflight selectors before returning the additive child environment."""
    result = dict(current)
    if "python_index_url" in values or "python_install_mirror" in values:
        for key in ("UV_CONFIG_FILE", "UV_NO_CONFIG"):
            if current.get(key):
                raise ValueError(f"{key} requires explicit source reconciliation")
    if "python_index_url" in values:
        if current.get("PIP_CONFIG_FILE", "").lower() in {"nul", "/dev/null"}:
            raise ValueError("PIP_CONFIG_FILE disables native configuration")
        for key in (
            "UV_INDEX", "UV_EXTRA_INDEX_URL", "UV_FIND_LINKS",
            "UV_INSECURE_HOST", "UV_NO_INDEX", "PIP_EXTRA_INDEX_URL",
            "PIP_FIND_LINKS", "PIP_TRUSTED_HOST", "PIP_NO_INDEX",
        ):
            if current.get(key):
                raise ValueError(f"{key} requires explicit source reconciliation")
    if "uv_install_mirror" in values:
        for key in ("UV_DOWNLOAD_URL", "INSTALLER_DOWNLOAD_URL", "UV_INSTALLER_GHE_BASE_URL"):
            if current.get(key):
                raise ValueError(f"{key} requires explicit source reconciliation")
    if "npm_registry" in values:
        for key in ("USERCONFIG", "GLOBALCONFIG"):
            upper = current.get("NPM_CONFIG_" + key)
            lower = current.get("npm_config_" + key.lower())
            if upper and lower and Path(upper).absolute() != Path(lower).absolute():
                raise ValueError(f"npm {key} selections conflict")
    for field, variables in FIELDS.items():
        if field not in values:
            continue
        aliases = (*variables, "UV_INDEX_URL") if field == "python_index_url" else variables
        if field == "npm_registry":
            aliases = (*aliases, "NPM_CONFIG_REGISTRY")
        for name in aliases:
            if current.get(name) and not _same(current[name], values[field]):
                raise ValueError(f"{name} conflicts with toolchain configuration")
        for name in variables:
            result[name] = values[field]
    for name in (
        "PIP_CONFIG_FILE", "NPM_CONFIG_USERCONFIG", "npm_config_userconfig",
        "NPM_CONFIG_GLOBALCONFIG", "npm_config_globalconfig",
    ):
        if current.get(name):
            result[name] = str(Path(current[name]).absolute())
    return result


def _native_paths(home: Path, env: Mapping[str, str], windows: bool) -> tuple[list[Path], Path, Path]:
    if windows:
        appdata = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
        pip = [Path(env.get("PROGRAMDATA") or "C:\\ProgramData") / "pip" / "pip.ini"]
        target = appdata / "pip" / "pip.ini"
        user = [home / "pip" / "pip.ini", target]
        uv = appdata / "uv" / "uv.toml"
    else:
        config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
        if sys.platform == "darwin":
            native = home / "Library" / "Application Support" / "pip"
            target = (native if native.is_dir() else home / ".config" / "pip") / "pip.conf"
            pip = [Path("/Library/Application Support/pip/pip.conf")]
        else:
            target = config / "pip" / "pip.conf"
            pip = [Path(p) / "pip" / "pip.conf" for p in (env.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":") if p]
            pip.append(Path("/etc/pip.conf"))
        user = [home / ".pip" / "pip.conf", target]
        uv = config / "uv" / "uv.toml"
    if not env.get("PIP_CONFIG_FILE"):
        pip += user
    if env.get("VIRTUAL_ENV"):
        pip.append(Path(env["VIRTUAL_ENV"]) / ("pip.ini" if windows else "pip.conf"))
    if env.get("PIP_CONFIG_FILE"):
        explicit = Path(env["PIP_CONFIG_FILE"])
        pip.append(explicit)
        target = explicit
    return pip, uv, target


def _system_uv_path(env: Mapping[str, str], *, windows: bool) -> Path | None:
    if windows:
        candidates = [Path(env.get("PROGRAMDATA") or "C:\\ProgramData") / "uv" / "uv.toml"]
    else:
        candidates = [
            Path(directory) / "uv" / "uv.toml"
            for directory in (env.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":") if directory
        ] + [Path("/etc/uv/uv.toml")]
    return next((path for path in candidates if path.exists()), None)


def _check_uv_sources(config: dict[str, object], values: Mapping[str, str]) -> bool:
    mirror = values.get("python_install_mirror")
    if mirror and "python-install-mirror" in config and (
        not isinstance(config["python-install-mirror"], str)
        or not _same(config["python-install-mirror"], mirror)
    ):
        raise ValueError("Existing managed Python mirror conflicts with toolchain configuration")
    index = values.get("python_index_url")
    if not index:
        return False
    pip = config.get("pip", {})
    if not isinstance(pip, dict):
        raise ValueError("Unsupported uv pip configuration")
    for table in (config, pip):
        for name in ("extra-index-url", "find-links", "no-index", "no-default-index", "allow-insecure-host"):
            if name in table:
                raise ValueError("Additional uv source selectors require reconciliation")
        if "index-url" in table and (
            not isinstance(table["index-url"], str) or not _same(table["index-url"], index)
        ):
            raise ValueError("Existing uv index conflicts with toolchain configuration")
    if "index" in pip:
        raise ValueError("Unsupported uv pip index configuration")
    indexes = config.get("index")
    legacy = config.get("index-url")
    if indexes is not None and (
        legacy is not None or not isinstance(indexes, list) or len(indexes) != 1
        or not isinstance(indexes[0], dict) or indexes[0].get("default") is not True
        or indexes[0].get("explicit", False) is not False
        or not isinstance(indexes[0].get("url"), str) or not _same(indexes[0]["url"], index)
    ):
        raise ValueError("Existing uv indexes conflict with toolchain configuration")
    return indexes is not None or legacy is not None


def _text(path: Path) -> str:
    if not path.exists():
        return ""
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Native tool configuration is not a bounded ordinary file")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return stream.read()


def plan_native_configuration(
    values: Mapping[str, str], home: Path, env: Mapping[str, str], *, windows: bool,
) -> dict[Path, str]:
    """Preflight every affected file; preserve unrelated bytes when appending defaults."""
    environment(values, env)
    changes: dict[Path, str] = {}
    paths, uv_path, pip_target = _native_paths(home, env, windows)
    uv_content = _text(uv_path) if any(
        name in values for name in ("python_index_url", "python_install_mirror")
    ) else ""
    uv_config = tomllib.loads(uv_content)
    uv_configs = [uv_config]
    if any(name in values for name in ("python_index_url", "python_install_mirror")):
        system = _system_uv_path(env, windows=windows)
        if system is not None and system != uv_path:
            uv_configs.append(tomllib.loads(_text(system)))
    has_index = [_check_uv_sources(config, values) for config in uv_configs]
    if sum(has_index) > 1:
        raise ValueError("Multiple uv default index selections require reconciliation")
    if mirror := values.get("python_install_mirror"):
        if not any("python-install-mirror" in config for config in uv_configs):
            uv_content = f'python-install-mirror = "{mirror}"\n' + uv_content
            changes[uv_path] = uv_content
    if index := values.get("python_index_url"):
        if env.get("PIP_CONFIG_FILE") and env["PIP_CONFIG_FILE"].lower() != os.devnull.lower():
            if not Path(env["PIP_CONFIG_FILE"]).is_file():
                raise ValueError("Explicit pip configuration is unavailable")
        for path in dict.fromkeys(paths):
            content = _text(path)
            parser = configparser.RawConfigParser()
            parser.read_string(content)
            for section in (parser.default_section, *parser.sections()):
                for name, value in parser.items(section):
                    name = name.replace("_", "-")
                    if name in {"extra-index-url", "find-links", "trusted-host", "no-index"}:
                        raise ValueError("Additional pip source selectors require reconciliation")
                    if name == "index-url" and not _same(value, index):
                        raise ValueError("Existing pip index conflicts with toolchain configuration")
        pip_content = _text(pip_target)
        parser = configparser.RawConfigParser()
        parser.read_string(pip_content)
        if not parser.has_option("global", "index-url") and not parser.has_option("global", "index_url"):
            lines = pip_content.splitlines(keepends=True)
            for i, line in enumerate(lines):
                header = parser.SECTCRE.match(line.strip())
                if header is not None and header.group("header") == "global":
                    newline = "\r\n" if "\r\n" in pip_content else "\n"
                    indentation = ""
                    for following in lines[i + 1:]:
                        stripped = following.lstrip()
                        if stripped.strip() and not stripped.startswith(("#", ";")):
                            indentation = following[:len(following) - len(stripped)]
                            break
                    if not lines[i].endswith(("\r", "\n")):
                        lines[i] += newline
                    lines.insert(i + 1, f"{indentation}index-url = {index}{newline}")
                    break
            else:
                lines += [f"\n[global]\nindex-url = {index}\n"]
            changes[pip_target] = "".join(lines)
        if not any(has_index):
            changes[uv_path] = uv_content + f'\n\n[[index]]\nurl = "{index}"\ndefault = true\n'
    if registry := values.get("npm_registry"):
        user = Path(env.get("NPM_CONFIG_USERCONFIG") or env.get("npm_config_userconfig") or home / ".npmrc")
        paths = [user]
        if global_config := env.get("NPM_CONFIG_GLOBALCONFIG") or env.get("npm_config_globalconfig"):
            paths.append(Path(global_config))
        for path in paths:
            for line in _text(path).splitlines():
                if not line.strip() or line.lstrip().startswith(("#", ";")):
                    continue
                name, separator, value = line.partition("=")
                if name.strip().lower() == "registry":
                    if not separator or not _same(value.strip(), registry):
                        raise ValueError("Existing npm registry conflicts with toolchain configuration")
                elif name.strip().lower().endswith(":registry"):
                    raise ValueError("Scoped npm registries require explicit reconciliation")
        content = _text(user)
        if not re.search(r"(?im)^\s*registry\s*=", content):
            changes[user] = content + f"\nregistry={registry}\n"
    return changes


def apply_configuration(path: Path, *, home: Path | None = None, root: Path | None = None) -> None:
    """Apply selected defaults before the standard installer starts package work."""
    from .config_write_lock import config_write_lock

    values = read_configuration(path)
    current = _routing_environment(os.environ)
    selected_home = home or Path(current.get("USERPROFILE") or Path.home())
    updated = environment(values, current)
    _paths, uv, pip = _native_paths(selected_home, current, os.name == "nt")
    targets: set[Path] = set()
    if "python_index_url" in values:
        targets.add(pip)
    if any(field in values for field in ("python_index_url", "python_install_mirror")):
        targets.add(uv)
    if "npm_registry" in values:
        targets.add(Path(current.get("NPM_CONFIG_USERCONFIG") or current.get("npm_config_userconfig")
                         or selected_home / ".npmrc"))
    retained = root / "toolchain.conf" if root is not None else None
    content = "schema=1\n" + "".join(f"{key}={value}\n" for key, value in values.items())
    if retained is not None:
        if retained.resolve() != path.resolve():
            targets.add(retained)
        updated[CONFIGURATION_ENV] = str(retained.absolute())
    with ExitStack() as locks:
        for target in sorted({target.absolute() for target in targets}, key=str):
            locks.enter_context(config_write_lock(target))
        changes = plan_native_configuration(values, selected_home, current, windows=os.name == "nt")
        if retained is not None and retained.resolve() != path.resolve():
            changes[retained] = content
        for target, content in changes.items():
            _write_configuration(target, content)
    if current.get("COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST") == "1" and os.name != "nt":
        for name in ("npm_config_registry", "npm_config_userconfig", "npm_config_globalconfig"):
            os.environ.pop(name, None)
    os.environ.update(updated)


def _write_configuration(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=target.parent, delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(content)
        if target.exists():
            os.chmod(temporary, target.stat().st_mode)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


GIT_LONGPATHS = "core.longpaths"
GitRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run_git(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments), capture_output=True, text=True, timeout=30, check=False,
        stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def ensure_git_longpaths(
    *, windows: bool | None = None, git: str | None = None, run: GitRunner | None = None,
    report: Callable[[str], None] | None = None,
) -> str:
    """Set ``git config --global core.longpaths true`` on Windows when unset.

    Git for Windows refuses checkout paths of 260 characters or more unless
    ``core.longpaths`` is true; the OS ``LongPathsEnabled`` value does not
    change that, and managed worktrees add a folder prefix to every path. An
    explicit global or system value (including included files), even
    ``false``, is never changed; ``false`` is reported. Returns
    ``not-windows``, ``no-git``, ``set``, ``present``, ``explicit-false`` or
    ``failed``. Never raises.
    """
    if not (os.name == "nt" if windows is None else windows):
        return "not-windows"
    executable = git or shutil.which("git")
    if not executable:
        return "no-git"
    runner = run or _run_git
    emit = report or (lambda message: print(message, file=sys.stderr))
    try:
        value = ""
        for scope in ("--global", "--system"):
            current = runner([executable, "config", scope, "--includes", "--get", GIT_LONGPATHS])
            if current.returncode not in (0, 1):
                emit(
                    f"Could not read {scope[2:]} Git {GIT_LONGPATHS} "
                    f"(exit {current.returncode}): {current.stderr.strip()}"
                )
                return "failed"
            value = current.stdout.strip() if current.returncode == 0 else ""
            if value:
                break
        if value:
            if value.lower() in ("false", "no", "off", "0"):
                emit(
                    f"Git {GIT_LONGPATHS} is explicitly '{value}' in the {scope[2:]} configuration; "
                    "left unchanged. Long repository paths in managed worktrees can fail with "
                    "'Filename too long'."
                )
                return "explicit-false"
            return "present"
        stored = runner([executable, "config", "--global", GIT_LONGPATHS, "true"])
    except (OSError, subprocess.SubprocessError):
        return "failed"
    if stored.returncode != 0:
        emit(f"Could not set global Git {GIT_LONGPATHS}=true: {stored.stderr.strip()}")
        return "failed"
    emit(f"Enabled global Git {GIT_LONGPATHS}=true for long Windows paths.")
    return "set"


def configure_selected(*, root: Path, apply: bool) -> None:
    """Reconcile the selected input without executing adopter-provided code."""
    if apply:
        ensure_git_longpaths()
    current = _routing_environment(os.environ)
    selected = current.get(CONFIGURATION_ENV)
    if not selected:
        candidate = Path.cwd() / ".copilot-extensions" / "toolchain.conf"
        selected = str(candidate if candidate.exists() else root / "toolchain.conf")
        if not Path(selected).exists():
            return
    path = Path(selected)
    if not path.is_absolute():
        raise ValueError("Toolchain configuration must be absolute")
    if apply:
        apply_configuration(path, root=root)
    else:
        values = read_configuration(path)
        plan_native_configuration(
            values, Path(current.get("USERPROFILE") or Path.home()), current,
            windows=os.name == "nt",
        )


def configure_setup(*, root: Path, apply: bool, configuration_only: bool) -> int:
    """Apply the shared setup configuration stage without runtime dependencies."""
    try:
        configure_selected(root=root, apply=apply)
    except (OSError, ValueError, UnicodeError, configparser.Error) as error:
        # Parser exceptions can contain native file contents.
        reason = f": {error}" if type(error) is ValueError else ""
        print(f"  ! toolchain configuration refused: {type(error).__name__}{reason}")
        return 2
    if configuration_only:
        mode = "applied" if apply else "preflight complete (plan only)"
        print(f"  toolchain configuration: {mode}; no prerequisite/core/repository setup requested.")
    return 0
