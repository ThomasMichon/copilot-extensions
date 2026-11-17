"""Slot-local stdlib bootstrap: retain a completed runtime before site imports."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import runpy
import stat
import sys
from pathlib import Path

GUI_BOOTSTRAP_VERSION = 1


def _gui_environment(runtime, root: Path, slot: Path) -> None:
    """Bind the physical Windows GUI host to this retained virtual environment."""
    python = runtime.slot_python(root, slot.name)
    if sys.platform != "win32" or python is None:
        raise RuntimeError("GUI retention bootstrap requires a Windows runtime")
    environment = python.parent.parent
    config = environment / "pyvenv.cfg"
    values = {}
    for line in _identity_bytes(config, 65536).decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip().lower()] = value.strip()
    home = Path(values.get("home", ""))
    if (
        not home.is_absolute()
        or values.get("include-system-site-packages", "").lower() != "false"
        or not Path(sys.executable).samefile(home / "pythonw.exe")
        or not Path(sys.base_prefix).samefile(home)
    ):
        raise RuntimeError("GUI host does not match the retained runtime base")
    # Task Scheduler cannot pass __PYVENV_LAUNCHER__. Bind before site.main(),
    # and keep subprocess selection inside the retained environment.
    sys.executable = str(python)
    sys.prefix = sys.exec_prefix = str(environment)


def _identity_bytes(path: Path, limit: int) -> bytes:
    def regular(info: os.stat_result) -> bool:
        return (
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, "st_file_attributes", 0) & 0x400
        )

    if not regular(path.lstat()):
        raise ValueError("Retained runtime identity requires regular metadata")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not regular(os.fstat(stream.fileno())):
            raise ValueError("Retained runtime identity requires regular metadata")
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Retained runtime identity metadata exceeds its bound")
    return content


def runtime_identity(slot: Path) -> str:
    """Bind a selected build to its existing generation and exact completion."""
    bootstrap = slot / ".runtime-bootstrap"
    if (
        slot.is_symlink() or bootstrap.is_symlink()
        or slot.resolve() != slot.parent.resolve() / slot.name
        or bootstrap.resolve() != slot.resolve() / bootstrap.name
    ):
        raise ValueError("Retained runtime identity requires a physical slot")
    generation = _identity_bytes(bootstrap / "runtime-generation", 64).decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", generation):
        raise ValueError("Retained runtime generation is malformed")
    completion = _identity_bytes(slot / ".install-complete.json", 65536)
    return generation + ":" + hashlib.sha256(completion).hexdigest()


def main() -> None:
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError("Runtime retention bootstrap requires -I -S -B")
    bootstrap = Path(__file__).resolve().parent
    slot = bootstrap.parent
    root = slot.parent.parent
    if slot.parent.name != "versions" or slot.name == "dev":
        raise RuntimeError("Runtime retention bootstrap requires an immutable numbered slot")
    arguments = sys.argv[1:]
    gui_runtime = arguments[:1] == ["--gui-runtime"]
    if gui_runtime:
        arguments = arguments[1:]
    expected_identity = None
    if arguments[:1] == ["--runtime-identity"]:
        if len(arguments) < 4 or not re.fullmatch(r"[0-9a-f]{32}:[0-9a-f]{64}", arguments[1]):
            raise ValueError("Runtime retention bootstrap requires a selected build identity")
        expected_identity = arguments[1]
        arguments = arguments[2:]
    if not arguments or (arguments[0] == "-m" and len(arguments) < 2):
        raise ValueError("Runtime retention bootstrap requires a module or absolute script")
    spec = importlib.util.spec_from_file_location(
        "_retained_versioned_runtime", bootstrap / "versioned_runtime.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Slot-local retention helper is unavailable")
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    marker = runtime.read_marker(root, slot.name)
    if marker is None or not runtime.is_complete(root, slot.name) or not marker.get("payload_hash"):
        raise RuntimeError("Runtime retention bootstrap requires attributable completion")
    with runtime.retention_lease(root, slot.name, payload_hash=marker["payload_hash"]):
        if expected_identity is not None and runtime_identity(slot) != expected_identity:
            raise RuntimeError("Retained runtime build identity changed")
        if gui_runtime:
            if expected_identity is None:
                raise RuntimeError("GUI retention bootstrap requires a selected build identity")
            _gui_environment(runtime, root, slot)
        import site

        site.main()
        python = runtime.slot_python(root, slot.name)
        if python is None or not Path(sys.prefix).samefile(python.parent.parent):
            raise RuntimeError("Retained runtime interpreter resolved outside its slot")
        if arguments[0] == "-m":
            module = arguments[1]
            sys.argv = arguments[1:]
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        else:
            path = arguments[0]
            if (
                any(character in path for character in "\0\r\n")
                or not Path(path).is_absolute() or not Path(path).is_file()
            ):
                raise ValueError("Runtime retention bootstrap requires an absolute script file")
            sys.argv = arguments
            runpy.run_path(path, run_name="__main__")


if __name__ == "__main__":
    main()
