"""Activate the resident daemon using a constructed Manager's own dependencies."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def run_self_install(rest: list[str], render) -> int:
    """Route Windows apply through the existing installation execution owner."""
    if "--apply" in rest and os.name == "nt":
        from .update_driver_cli import self_install_request

        return self_install_request(render)
    return render()


def activate_installed_slot(slot: Path) -> dict:
    """Activate the exact installed slot without resolving a mutable selection."""
    from .self_install import _runtime_installer

    return _runtime_installer()._activate_after_bootstrap_update(slot.parent.parent, slot)


def started_outcome(stdout: str, generation: str) -> dict | None:
    """Accept only the requested incoming generation's acknowledged startup."""
    try:
        outcome = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(outcome, dict) or outcome.get("runtime_generation") != generation:
        return None
    result = outcome.get("result")
    if (
        outcome.get("action") in ("cutover", "legacy-cutover")
        and isinstance(result, dict) and result.get("ok") is True
    ):
        return outcome
    route = outcome.get("route")
    if (
        outcome.get("action") == "noop" and outcome.get("reason") == "already-active-version"
        and isinstance(route, dict) and route.get("version") == generation
    ):
        return outcome
    return None


def main(argv: list[str] | None = None) -> int:
    from . import mux_daemon, mux_daemon_cutover
    from ._installation_context import paths_equal
    from .runtime_identity import running_slot

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--start-if-missing", action="store_true")
    args = parser.parse_args(argv)
    root, slot = args.root.resolve(), running_slot()
    if slot is None or not paths_equal(slot.parent, root / "versions") or not (slot / ".runtime-bootstrap").is_dir():
        raise RuntimeError("Bootstrap activation must execute from its constructed Manager slot")
    for attempt in range(2):
        result = mux_daemon_cutover.activate_after_update(root=root, slot=slot, version=slot.name)
        if (
            not isinstance(result, dict)
            or result.get("action") not in ("noop", "cutover", "legacy-cutover", "error", "refused")
        ):
            raise RuntimeError("Manager cutover returned an invalid activation result")
        outcome = result.get("result")
        if outcome is not None and (
            not isinstance(outcome, dict) or type(outcome.get("ok")) is not bool
        ):
            raise RuntimeError("Manager cutover returned an invalid activation outcome")
        if result["action"] in ("cutover", "legacy-cutover") and outcome is None:
            raise RuntimeError("Manager cutover returned an invalid activation outcome")
        missing = result["action"] == "noop" and result.get("reason") == "no-live-daemon"
        if not (args.start_if_missing and missing):
            break
        if attempt or not mux_daemon.ensure_daemon_running(root):
            raise RuntimeError("Constructed Manager daemon startup did not become ready")
    print(json.dumps({**result, "runtime_generation": slot.name}), flush=True)
    return int(
        result["action"] in ("error", "refused")
        or (isinstance(outcome, dict) and outcome.get("ok") is False)
    )


if __name__ == "__main__":
    raise SystemExit(main())
