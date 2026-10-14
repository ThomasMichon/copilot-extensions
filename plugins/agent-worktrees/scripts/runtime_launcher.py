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
        if Path(sys.prefix).resolve() != slot:
            raise RuntimeError("Retained runtime interpreter resolved outside its slot")
        if len(sys.argv) < 3 or sys.argv[1] != "-m":
            raise ValueError("Runtime retention bootstrap requires a module invocation")
        module = sys.argv[2]
        sys.argv = sys.argv[2:]
        runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
