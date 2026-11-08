"""Reconcile an existing Manager mux owner to the selected runtime."""

from __future__ import annotations

import argparse
import json
import math
import stat
import sys
import time
from pathlib import Path

from zdd import routing

from . import mux_daemon_cutover, mux_serving_observation

SCHEMA = "worktree-manager.mux-serving-reconciliation"
VERSION = 1


def _ordinary(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise ValueError("Manager selection is redirected or unavailable")


def require_selected_generation(root: Path) -> Path:
    from agent_procutil._daemon_instance_identity import runtime_slot_paths

    from .self_install import _runtime_installer

    try:
        root = Path(root)
        if not root.is_absolute():
            raise ValueError("Manager root must be absolute")
        _ordinary(root, directory=True)
        facts = runtime_slot_paths(sys.prefix)
        if facts is None:
            raise ValueError("Manager runtime is not a physical slot")
        generation, slot, owner = facts
        slot, owner = Path(slot), Path(owner)
        if not owner.samefile(root):
            raise ValueError("Manager runtime belongs to another installation")
        for directory in (root / "versions", slot, slot / ".runtime-bootstrap"):
            _ordinary(directory, directory=True)
        selector = root / "current-version"
        _ordinary(selector, directory=False)
        if selector.read_text(encoding="utf-8").strip() != generation:
            raise ValueError("Manager runtime is not selected")
        if not Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError("Manager reconcile module is outside its runtime")
        runtime = _runtime_installer().runtime
        python = runtime.slot_python(root, generation)
        if (
            runtime.current_version(root) != generation or not runtime.is_complete(root, generation)
            or python is None or not Path(sys.executable).samefile(python)
        ):
            raise ValueError("Manager runtime is not a complete selected generation")
        return slot.resolve(strict=True)
    except ValueError:
        raise
    except (AttributeError, ImportError, OSError, RuntimeError) as error:
        raise ValueError("Manager selection is unavailable") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 600:
        parser.error("--timeout must be between 0 and 600 seconds")
    stage = "selection"
    try:
        root = args.root
        slot = require_selected_generation(root)

        def verify_selected():
            if not require_selected_generation(root).samefile(slot):
                raise ValueError("Manager selection changed")

        stage = "observation"
        observed = mux_serving_observation.observe(root, timeout=args.timeout)
        if observed["state"] == "inactive":
            stage = "selection"
            verify_selected()
            stage = "observation"
            if mux_serving_observation.observe(root, timeout=args.timeout) != observed:
                raise RuntimeError("Manager inactive state changed")
            stage = "selection"
            verify_selected()
        else:
            table = routing.read_table(mux_daemon_cutover.routing_dir(root), strict=True)
            raw = table.get("active") if isinstance(table, dict) else None
            endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
            if endpoint is None or observed != {"state": "ready", "generation": endpoint.version}:
                raise RuntimeError("Manager incumbent changed")
            stage = "reconcile"
            outcome = mux_daemon_cutover.activate_after_update(
                root=root, slot=slot, version=slot.name,
                health_timeout=args.timeout, drain_timeout=args.timeout,
                expected_active=raw, verify_selected=verify_selected, lock_timeout=args.timeout,
            )
            if (
                not isinstance(outcome, dict) or outcome.get("action") not in {"noop", "cutover"}
                or (outcome["action"] == "noop" and outcome.get("reason") != "already-active-version")
                or (outcome["action"] == "cutover" and (
                    not isinstance(outcome.get("result"), dict) or outcome["result"].get("ok") is not True
                ))
            ):
                raise RuntimeError("Manager reconcile did not complete")
            stage = "observation"
            deadline = time.monotonic() + args.timeout
            while True:
                verify_selected()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Manager serving readiness timed out")
                try:
                    observed = mux_serving_observation.observe(root, timeout=remaining)
                except mux_serving_observation.ServingObservationError:
                    observed = {"state": "unknown", "generation": None}
                if observed == {"state": "ready", "generation": slot.name}:
                    break
                if outcome["action"] == "noop":
                    raise RuntimeError("Manager serving state changed")
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            stage = "selection"
            verify_selected()
        result = {"schema": SCHEMA, "version": VERSION, "ok": True, **observed}
    except Exception as error:
        if callable(getattr(error, "retry_cleanup", None)):
            raise
        result = {
            "schema": SCHEMA, "version": VERSION, "ok": False, "state": "unknown",
            "reason": stage + "-unavailable",
        }
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
