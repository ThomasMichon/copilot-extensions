"""Fixed installed-runtime identity, shared by adoption and read-only launch."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .windows_task_broker import Registration


def installation_identity(root: Path) -> str:
    """Bind identity to an exact durable root, not an ambient owner hint."""
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("broker installation requires an absolute unlinked root")
    digest = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()
    from .windows_broker_cell import is_cell

    return ("cell/" if is_cell(root) else "runtime/") + digest


def registration(root: Path, slot: Path) -> Registration:
    """Describe only a complete immutable canonical Windows action generation."""
    from .windows_broker_retention import runtime_component
    from .windows_task_broker import Registration, _metadata, _refuse, native
    from . import windowless_python, windowless_python_env

    if os.name != "nt":
        raise _refuse("Windows prerequisite unavailable")
    root, slot = Path(root), Path(slot)
    identity = installation_identity(root)
    if (
        not slot.is_absolute() or slot.resolve() != slot
        or slot.parent != root / "versions"
    ):
        raise _refuse("broker action requires an immutable runtime generation")
    from .windows_broker_cell import validate as validate_cell

    validate_cell(root, slot, require_active=True)
    config = Registration(
        identity, str(root), str(slot / "Scripts" / "pythonw.exe"),
        str(slot / "Lib" / "site-packages" / "agent_procutil" / "windows_broker_entry.py"),
        native().current_sid(),
    )
    config.validate_files()
    runtime = runtime_component(config, "versioned_runtime")
    if (
        slot.name.casefold() == runtime.DEV_VERSION or runtime.is_dev_generation(slot.name)
        or runtime.validate_marker(_metadata(slot / ".install-complete.json"), slot.name) is None
    ):
        raise _refuse("broker action requires a complete immutable runtime generation")
    launcher = slot / "Scripts" / "python.exe"
    venv = slot / "pyvenv.cfg"
    info = venv.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not launcher.is_file()
    ):
        raise _refuse("broker runtime interpreter binding refused")
    pythonw = windowless_python(launcher, require_windowless=True)
    if re.match(r"^\\\\\?\\[A-Za-z]:\\", pythonw):
        dos_path = pythonw[4:]
        if not Path(dos_path).samefile(pythonw):
            raise _refuse("broker interpreter spelling changed native identity")
        pythonw = dos_path
    if windowless_python_env(launcher) != {"__PYVENV_LAUNCHER__": str(launcher)}:
        raise _refuse("broker runtime requires its direct base interpreter")
    # The entrypoint explicitly imports only its installed package directory.
    # Do not schedule the venv redirector or persist a process environment.
    config = replace(config, pythonw=pythonw)
    config.validate_files()
    return config


def load(root: Path) -> Registration:
    """Read adoption by canonical installation identity without repairing it."""
    from .windows_task_broker import load as load_registration

    identity = installation_identity(root)
    return load_registration(Path(root) / "windows-launch-broker.json", identity)


def load_current() -> Registration:
    """Read the adoption owned by this installed module, never ambient hints."""
    from .windows_task_broker import _refuse

    module = Path(__file__)
    suffix = Path("Lib") / "site-packages" / "agent_procutil" / module.name
    if (
        module.name != "windows_broker_installation.py"
        or not module.is_absolute()
        or len(module.parents) < 6
        or Path(*module.parts[-4:]) != suffix
        or module.parents[4].name != "versions"
        or module.resolve() != module
    ):
        raise _refuse("installed broker context required")
    # -S may leave sys.prefix at the base interpreter. The imported module is
    # the installation binding; its adopted action may retain an older slot.
    from .windows_broker_cell import validate as validate_cell

    validate_cell(module.parents[5], module.parents[3], require_active=True)
    return load(module.parents[5])
