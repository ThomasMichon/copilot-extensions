"""Read-only main-package observations, not build or service attestation."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import re
import stat
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

PLUGINS = ("worktrees", "bridge", "dispatch")
SOURCE = Path("/opt/lab/source")
RUNTIME = Path("/lab/state/runtime")
MAX_FILES = 4096
MAX_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class RuntimeOrigin:
    plugin: str
    executable: str
    interpreter: str
    prefix: str
    package: str
    version: str
    source_package: str
    python_files: int
    python_bytes: int


def _read(path: Path) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
        raise ValueError(f"runtime origin requires a bounded regular file: {path}")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES or not os.path.samestat(info, path.lstat()):
        raise ValueError(f"runtime origin file changed or exceeded its limit: {path}")
    return data


def _python_files(root: Path) -> dict[str, bytes]:
    pending = [root]
    files: dict[str, bytes] = {}
    entries = total = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for entry in children:
                entries += 1
                if entries > MAX_FILES:
                    raise ValueError("main-package inventory exceeds its entry limit")
                if entry.is_symlink():
                    raise ValueError("main-package inventory contains a redirected path")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.name.endswith((".pyc", ".pyo")):
                    raise ValueError("main-package inventory contains cached bytecode; no import")
                elif entry.name.endswith(".py"):
                    data = _read(Path(entry.path))
                    total += len(data)
                    if total > MAX_BYTES:
                        raise ValueError("main-package Python source exceeds its byte limit")
                    files[str(Path(entry.path).relative_to(root))] = data
    if "__init__.py" not in files:
        raise ValueError("main-package inventory has no initializer")
    return files


def observe(plugin: str, *, runtime: Path = RUNTIME, source: Path = SOURCE) -> RuntimeOrigin:
    """Compare only this interpreter's main package with selected source bytes.

    Requires immutable selected source and no competing runtime writer. This
    observation does not authenticate the interpreter, dependencies or image.
    """
    if plugin not in PLUGINS:
        raise ValueError("unsupported fixed reproducer runtime")
    prefix = runtime / plugin
    executable = prefix / "bin/python3"
    if (
        Path(sys.prefix) != prefix or prefix.resolve(strict=True) != prefix
        or Path(sys.executable) != executable or sys.prefix == sys.base_prefix
    ):
        raise ValueError("probe is not executing the selected isolated runtime")
    distribution_name = "agent-" + plugin
    module_name = "agent_" + plugin
    if any(name == module_name or name.startswith(module_name + ".") for name in sys.modules):
        raise ValueError("main package was imported before source observation")
    project = source / "plugins" / distribution_name
    source_package = project / "src" / module_name
    if source.resolve(strict=True) != source or source_package.resolve(strict=True) != source_package:
        raise ValueError("selected source package is redirected")
    project_data = tomllib.loads(_read(project / "pyproject.toml").decode("utf-8"))
    declared = project_data["project"]
    if declared["name"] != distribution_name:
        raise ValueError("selected source distribution name differs")
    match = re.fullmatch(
        r"((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))(?:-dev(0|[1-9][0-9]*))?",
        declared["version"],
    )
    if match is None:
        raise ValueError("selected source version is not a canonical release/dev version")
    expected_version = match[1] + (".dev" + match[2] if match[2] is not None else "")
    distribution = importlib.metadata.distribution(distribution_name)
    if distribution.version != expected_version:
        raise ValueError("installed distribution version differs from selected source")
    direct_text = distribution.read_text("direct_url.json")
    if direct_text is None:
        raise ValueError("installed distribution has no source-origin metadata")
    direct = json.loads(direct_text)
    directory = direct.get("dir_info") if isinstance(direct, dict) else None
    noneditable = directory == {} or (
        isinstance(directory, dict) and set(directory) == {"editable"}
        and directory["editable"] is False
    )
    if (
        not isinstance(direct, dict) or direct.get("url") != project.as_uri()
        or not noneditable
        or set(direct) != {"url", "dir_info"}
    ):
        raise ValueError("installed distribution is not the selected noneditable local project")
    site = Path(distribution.locate_file("")).resolve(strict=True)
    if not site.is_relative_to(prefix):
        raise ValueError("installed distribution is outside the selected runtime")
    expected_package = site / module_name
    if expected_package.resolve(strict=True) != expected_package:
        raise ValueError("installed main package is redirected")
    original = _python_files(source_package)
    installed = _python_files(expected_package)
    if installed != original:
        raise ValueError("installed main-package Python bytes differ from selected source")
    # Import only the verified top-level package, never a CLI/service entry point.
    module = importlib.import_module(module_name)
    if module.__file__ is None or Path(module.__file__).resolve(strict=True) != expected_package / "__init__.py":
        raise ValueError("executing main package differs from the selected distribution")
    return RuntimeOrigin(
        plugin, str(executable), str(executable.resolve(strict=True)), str(prefix),
        str(expected_package), distribution.version, str(source_package),
        len(installed), sum(map(len, installed.values())),
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise ValueError("one fixed reproducer runtime name is required")
    print(json.dumps(asdict(observe(sys.argv[1])), sort_keys=True))


if __name__ == "__main__":
    main()
