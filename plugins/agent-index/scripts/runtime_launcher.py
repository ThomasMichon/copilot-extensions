"""Slot-local stdlib bootstrap: retain a completed runtime before site imports."""

from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path


def main() -> None:
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise RuntimeError("Runtime retention bootstrap requires -I -S -B")
    bootstrap = Path(__file__).resolve().parent
    slot = bootstrap.parent
    root = slot.parent.parent
    if slot.parent.name != "versions" or slot.name == "dev":
        raise RuntimeError("Runtime retention bootstrap requires an immutable numbered slot")
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
        import site

        site.main()
        python = runtime.slot_python(root, slot.name)
        if python is None or not Path(sys.prefix).samefile(python.parent.parent):
            raise RuntimeError("Retained runtime interpreter resolved outside its slot")
        if len(sys.argv) >= 3 and sys.argv[1] == "-m":
            module = sys.argv[2]
            sys.argv = sys.argv[2:]
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        elif len(sys.argv) >= 2:
            path = sys.argv[1]
            if (
                any(character in path for character in "\0\r\n")
                or not Path(path).is_absolute() or not Path(path).is_file()
            ):
                raise ValueError("Runtime retention bootstrap requires an absolute script file")
            sys.argv = sys.argv[1:]
            runpy.run_path(path, run_name="__main__")
        else:
            raise ValueError("Runtime retention bootstrap requires a module or absolute script")


if __name__ == "__main__":
    main()
