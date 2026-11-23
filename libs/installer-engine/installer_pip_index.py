"""Read pip's global index setting without launching an ambient pip executable."""
from __future__ import annotations

import argparse
import configparser
import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import sys
import types
from typing import Any


class ProviderQualificationError(ValueError):
    """A fixed, safe provider diagnostic, never configuration or subprocess output."""


def _index(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("Invalid pip index configuration")
    return value or None


def _global_files(environment: Mapping[str, str], platform: str) -> list[Path]:
    if platform == "win32":
        return [_windows_folder(environment, "PROGRAMDATA", 35) / "pip/pip.ini"]
    variable, default = (
        ("XDG_DATA_DIRS", "/Library/Application Support")
        if platform == "darwin" else ("XDG_CONFIG_DIRS", "/etc/xdg")
    )
    files = [
        Path(directory) / "pip" / "pip.conf"
        for directory in (environment.get(variable) or default).split(os.pathsep)
        if directory
    ]
    return files if platform == "darwin" else [*files, Path("/etc/pip.conf")]


def _user_file(environment: Mapping[str, str], home: Path, platform: str) -> Path:
    if platform == "win32":
        return _windows_folder(environment, "APPDATA", 26) / "pip/pip.ini"
    if platform == "darwin":
        native = Path(environment.get("XDG_DATA_HOME") or home / "Library/Application Support") / "pip"
        return (native if native.is_dir() else home / ".config/pip") / "pip.conf"
    return Path(environment.get("XDG_CONFIG_HOME") or home / ".config") / "pip/pip.conf"


def _windows_folder(environment: Mapping[str, str], variable: str, folder_id: int) -> Path:
    if environment.get(variable):
        return Path(environment[variable])
    if sys.platform != "win32":
        raise OSError("Windows package configuration directory is unavailable")
    import ctypes

    folder = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, folder_id, None, 0, folder) != 0:
        raise OSError("Cannot resolve Windows package configuration directory")
    if not folder.value:
        raise OSError("Windows package configuration directory is unavailable")
    return Path(folder.value)


def _dependency_policy() -> types.ModuleType:
    # -I omits the script directory; reuse only the adjacent canonical policy owner.
    path = Path(__file__).with_name("installer_dependency_policy.py")
    policy = types.ModuleType("installer_pip_index_policy")
    policy.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), policy.__dict__)
    return policy


def _uv_default(environment: Mapping[str, str], base: Path, uv: str = "") -> bool:
    policy = _dependency_policy()
    policy._configuration.require_system_config_provider(
        policy._select_uv_executable(uv), environment, cwd=base,
        error_type=ProviderQualificationError,
    )
    base = policy._configuration.working_directory(
        [], environment, base, argument_pairs=policy._argument_pairs,
    )
    config_inputs: Callable[[list[str], dict[str, str], Path], list[tuple[Path, bool]]] = (
        policy._config_inputs
    )
    read_toml: Callable[[Path], dict[str, Any]] = policy._toml
    names = {
        name for value in environment.get("UV_INDEX", "").split()
        for name, separator, _url in [value.partition("=")]
        if separator and name and "://" not in name
    }
    for path, is_project in config_inputs([], dict(environment), base):
        data = read_toml(path)
        if is_project:
            tool = data.get("tool", {})
            if not isinstance(tool, dict) or not isinstance(tool.get("uv"), dict):
                raise ValueError("Invalid uv project configuration")
            data = tool["uv"]
        for section in (data.get("pip", {}), data):
            if not isinstance(section, dict):
                raise ValueError("Invalid uv index configuration")
            if "index-url" in section:
                return True
            indexes = section.get("index", [])
            if not isinstance(indexes, list):
                raise ValueError("Invalid uv index configuration")
            for index in indexes:
                if not isinstance(index, dict):
                    raise ValueError("Invalid uv index configuration")
                name = index.get("name")
                if isinstance(name, str):
                    if name in names:
                        continue
                    names.add(name)
                if index.get("default") is True:
                    return True
    return False


def configured_index(
    environment: Mapping[str, str], *, home: Path, prefix: Path,
    global_files: Sequence[Path] | None = None, platform: str | None = None,
) -> str | None:
    """Resolve pip's global index with its platform file and environment precedence."""
    if environment.get("UV_INDEX_URL") or environment.get("UV_DEFAULT_INDEX"):
        return None
    if environment.get("PIP_INDEX_URL"):
        return _index(environment["PIP_INDEX_URL"])
    explicit = environment.get("PIP_CONFIG_FILE")
    if explicit == os.devnull:
        return None
    platform = sys.platform if platform is None else platform
    files = list(_global_files(environment, platform) if global_files is None else global_files)
    if not explicit or not Path(explicit).exists():
        files.extend((
            home / "pip/pip.ini" if platform == "win32" else home / ".pip/pip.conf",
            _user_file(environment, home, platform),
        ))
    files.append(prefix / ("pip.ini" if platform == "win32" else "pip.conf"))
    if explicit:
        files.append(Path(explicit))
    values: dict[str, str] = {}
    for path in files:
        config = configparser.RawConfigParser()
        try:
            with path.open(encoding="utf-8") as stream:
                config.read_file(stream)
        except FileNotFoundError:
            continue
        if config.has_section("global"):
            values.update(
                (name.lower().replace("_", "-").removeprefix("--"), value)
                for name, value in config.items("global")
            )
    return _index(values.get("index-url"))


def main(arguments: Sequence[str] = ()) -> int:
    """Print the selected index privately to the invoking installer's capture pipe."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-prefix", default="")
    parser.add_argument("--uv", default="")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(list(arguments))
    try:
        if os.environ.get("UV_INDEX_URL") or os.environ.get("UV_DEFAULT_INDEX") or _uv_default(
            os.environ, Path.cwd(), options.uv,
        ):
            return 0
        prefix = Path(options.site_prefix) if options.site_prefix else Path(sys.prefix)
        index = configured_index(os.environ, home=Path.home(), prefix=prefix)
    except ProviderQualificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, UnicodeError, configparser.Error, ValueError, RuntimeError, ImportError):
        print("Cannot read package index configuration.", file=sys.stderr)
        return 1
    if index:
        print(json.dumps(index) if options.json else index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
