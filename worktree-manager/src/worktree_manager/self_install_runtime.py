"""Read-only runtime resources and completion checks for Manager self-installation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from .runtime_identity import running_payload_dir, runtime_scripts  # noqa: F401 -- compatibility exports


def local_bin_hint() -> str:
    """Render the command directory resolved by the current installation policy."""
    from .self_install import local_bin
    return str(local_bin())


def load_runtime_installer(scripts: Path):
    script = scripts / "runtime_install.py"
    name = "_manager_runtime_install"
    existing = sys.modules.get(name)
    if existing is not None and Path(existing.__file__).resolve() == script.resolve():
        return existing
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError("Manager canonical installer adapter is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def slot_is_complete(slot: Path, runtime_installer, key_files) -> bool:
    """Check canonical completion, interpreter and retained payload availability.

    Payload assembly alone never establishes runtime readiness. Apply/reuse
    additionally runs the canonical installed-origin and CLI health probe under
    candidate custody; this read-only status check does not reconstruct a slot.
    """
    payload = slot / "payload"
    try:
        runtime = runtime_installer().runtime
        return (
            runtime.is_complete(slot.parent.parent, slot.name)
            and runtime.slot_python(slot.parent.parent, slot.name) is not None
            and (
                (slot / "application.json").is_file()
                or all((payload / rel).is_file() for rel in key_files)
            )
        )
    except (OSError, ValueError, ImportError, RuntimeError):
        return False


def slot_package_version(slot: Path, runtime_installer, payload_version) -> str | None:
    if (slot / "application.json").is_file():
        try:
            return runtime_installer().runtime.package_version(slot.name)
        except (OSError, ValueError, ImportError, RuntimeError):
            return None
    return payload_version(slot / "payload")
