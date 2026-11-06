"""Bounded installed-application readiness without serving publication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def probe(root: Path) -> dict:
    """Start the actual passive mux server and prove that work stays closed."""
    import worktree_manager
    from .mux_daemon import KIND, MuxDaemonRuntime, registry_path
    from .mux_daemon_cutover import ControlClient
    from .runtime_identity import running_slot

    root = root.resolve()
    slot = running_slot()
    if (
        slot is None or slot.parent != root / "versions"
        or not Path(worktree_manager.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    ):
        raise RuntimeError("Manager staging readiness must run from its installed slot")
    runtime = MuxDaemonRuntime(registry_path(root), root=root, passive=True)
    try:
        runtime.start()
        if runtime.port is None:
            raise RuntimeError("Manager staging server has no listening endpoint")
        client = ControlClient(f"http://127.0.0.1:{runtime.port}", root=root)
        health = client.health()
        if (
            health.get("status") != "ready"
            or health.get("runtime_generation") != slot.name
            or health.get("active") is not False
        ):
            raise RuntimeError("Manager staging server did not prove passive generation readiness")
        withheld = client._request(KIND)
        if withheld.get("applied") is not False:
            raise RuntimeError("Manager staging server admitted application work")
    finally:
        runtime.shutdown()
    return {
        "schema": "copilot-extensions.manager-stage-readiness", "version": 1,
        "generation": slot.name, "ready": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(probe(args.root)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
