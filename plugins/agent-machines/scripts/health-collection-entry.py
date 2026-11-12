"""Payload-owned compatibility entry, executed by the selected runtime with -I."""

from __future__ import annotations

import importlib.util
import json
import runpy
import stat
import sys
import sysconfig
from pathlib import Path


def supported(arguments: list[str], purelib: Path) -> bool:
    """Read support from the executing slot, without importing provider code."""
    if arguments[:2] == ["health", "prepare-job"]:
        operation = "prepare-job"
    elif arguments[:2] == ["health", "job"]:
        operation = "installed-job"
    elif arguments[:2] == ["health", "run"]:
        operation = "apply" if "--apply" in arguments[2:] else "preview"
    else:
        return False
    package = purelib / "agent_machines"
    path = package / "health-collection-v1.json"
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or path.is_symlink()
            or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > 4096
        ):
            return False
        with path.open("rb") as stream:
            declaration = json.loads(stream.read(4097))
        if (
            type(declaration) is not dict
            or declaration.get("schema") != "agent-machines.health.collection"
            or type(declaration.get("version")) is not int or declaration["version"] != 1
            or type(declaration.get("operations")) is not list
            or not all(type(item) is str for item in declaration["operations"])
            or operation not in declaration["operations"]
        ):
            return False
        spec = importlib.util.find_spec("agent_machines")
        return spec is not None and spec.origin is not None and (
            Path(spec.origin).resolve() == (package / "__init__.py").resolve()
        )
    except (OSError, ValueError, ImportError, RecursionError, RuntimeError):
        return False


def main() -> int:
    arguments = sys.argv[1:]
    if not supported(arguments, Path(sysconfig.get_path("purelib"))):
        print(json.dumps({
            "ok": False, "error": "collection-contract-unsupported", "applied": False,
            "recovery": "Use the installation owner to select a collection-capable runtime.",
        }), file=sys.stderr)
        return 2
    sys.argv = ["agent-machines", *arguments]
    runpy.run_module("agent_machines", run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
