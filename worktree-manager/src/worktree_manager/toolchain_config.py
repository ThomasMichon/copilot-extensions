"""Data-only adopter defaults for the standard bootstrap's native tools."""
from __future__ import annotations

import configparser
import os
from pathlib import Path
import re
import tempfile
import tomllib
from collections.abc import Mapping

CONFIGURATION_ENV = "COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG"
FIELDS = {
    "python_index_url": ("UV_DEFAULT_INDEX", "PIP_INDEX_URL"),
    "python_install_mirror": ("UV_PYTHON_INSTALL_MIRROR",),
    "uv_install_mirror": ("UV_INSTALLER_GITHUB_BASE_URL",),
    "npm_registry": ("npm_config_registry",),
}
URL = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~:/+-]*)?")
MAX_SIZE = 16384


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
    if "python_index_url" in values:
        if current.get("PIP_CONFIG_FILE", "").lower() in {"nul", "/dev/null"}:
            raise ValueError("PIP_CONFIG_FILE disables native configuration")
        for key in (
            "UV_INDEX", "UV_EXTRA_INDEX_URL", "UV_FIND_LINKS", "UV_CONFIG_FILE",
            "UV_INSECURE_HOST", "UV_NO_INDEX", "UV_NO_CONFIG", "PIP_EXTRA_INDEX_URL",
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
        pip = [Path(env.get("PROGRAMDATA") or "C:\\ProgramData") / "pip" / "pip.ini",
               home / "pip" / "pip.ini", appdata / "pip" / "pip.ini"]
        uv = appdata / "uv" / "uv.toml"
    else:
        config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
        pip = [Path(p) / "pip" / "pip.conf" for p in env.get("XDG_CONFIG_DIRS", "/etc/xdg").split(":")]
        pip += [Path("/etc/pip.conf"), home / ".pip" / "pip.conf", config / "pip" / "pip.conf"]
        uv = config / "uv" / "uv.toml"
    if env.get("VIRTUAL_ENV"):
        pip.append(Path(env["VIRTUAL_ENV"]) / ("pip.ini" if windows else "pip.conf"))
    if env.get("PIP_CONFIG_FILE"):
        explicit = Path(env["PIP_CONFIG_FILE"])
        pip.append(explicit)
    target = (
        Path(env.get("APPDATA") or home / "AppData" / "Roaming") / "pip" / "pip.ini"
        if windows else Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "pip" / "pip.conf"
    )
    return pip, uv, target


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
    if mirror := values.get("python_install_mirror"):
        if "python-install-mirror" in uv_config:
            if not isinstance(uv_config["python-install-mirror"], str) or not _same(
                uv_config["python-install-mirror"], mirror,
            ):
                raise ValueError("Existing managed Python mirror conflicts with toolchain configuration")
        else:
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
            for section in parser.sections():
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
                if line.strip().lower() == "[global]":
                    newline = "\r\n" if "\r\n" in pip_content else "\n"
                    if not lines[i].endswith(("\r", "\n")):
                        lines[i] += newline
                    lines.insert(i + 1, f"index-url = {index}{newline}")
                    break
            else:
                lines += [f"\n[global]\nindex-url = {index}\n"]
            changes[pip_target] = "".join(lines)
        for name in ("extra-index-url", "find-links"):
            if name in uv_config:
                raise ValueError("Legacy or additional uv sources require reconciliation")
        legacy_index = uv_config.get("index-url")
        if legacy_index is not None and (
            not isinstance(legacy_index, str) or not _same(legacy_index, index)
        ):
            raise ValueError("Existing uv index conflicts with toolchain configuration")
        indexes = uv_config.get("index")
        if indexes is not None:
            if (
                legacy_index is not None or not isinstance(indexes, list) or len(indexes) != 1
                or not isinstance(indexes[0], dict) or indexes[0].get("default") is not True
                or not isinstance(indexes[0].get("url"), str) or not _same(indexes[0]["url"], index)
            ):
                raise ValueError("Existing uv indexes conflict with toolchain configuration")
        elif legacy_index is None:
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
    values = read_configuration(path)
    selected_home = home or Path(os.environ.get("USERPROFILE") or Path.home())
    updated = environment(values, os.environ)
    changes = plan_native_configuration(values, selected_home, os.environ, windows=os.name == "nt")
    if root is not None:
        retained = root / "toolchain.conf"
        content = "schema=1\n" + "".join(f"{key}={value}\n" for key, value in values.items())
        if retained.resolve() != path.resolve():
            changes[retained] = content
    for target, content in changes.items():
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
    os.environ.update(updated)


def configure_selected(*, root: Path, apply: bool) -> None:
    """Reconcile the selected input without executing adopter-provided code."""
    selected = os.environ.get(CONFIGURATION_ENV)
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
            values, Path(os.environ.get("USERPROFILE") or Path.home()), os.environ,
            windows=os.name == "nt",
        )
