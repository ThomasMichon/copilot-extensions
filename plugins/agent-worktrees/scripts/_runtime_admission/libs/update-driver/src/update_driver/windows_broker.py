"""Fixed broker adoption under the existing installation owner's live custody."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from . import core
from .participant import ENVIRONMENT_KEY, borrowed_custody, participant, participant_environment

if TYPE_CHECKING:
    from agent_procutil.windows_task_broker import Registration


def adopt(runtime_root: Path, slot: Path, *, root: Path | None = None) -> Registration:
    """Reconcile a qualified action without acquiring a competing mutation lock.

    The owning installer calls this with a retained, qualified runtime, outside
    publication leases and before callbacks that may launch background work.
    Broker ownership is independent of the application's selected generation.
    First adoption belongs to explicit native/foreground
    installation custody, not ensure_worker or a foreground spawn fallback.
    """
    if os.name != "nt":
        raise core.StateError("Windows broker adoption is unsupported on this platform")
    if os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        raise core.StateError("contained Windows broker adoption refused")
    execution_root = core._root(root)
    custody = borrowed_custody(root=execution_root)
    if custody is None:
        inherited = participant_environment()
        if not inherited:
            raise core.StateError("Windows broker adoption requires live installation custody")
        custody = participant(root=execution_root, witness=inherited[ENVIRONMENT_KEY])
    with custody, custody.execution_scope():
        from agent_procutil.windows_broker_installation import registration, load
        from agent_procutil import windows_task_broker as broker

        custody.assert_owned()
        desired = registration(Path(runtime_root), Path(slot))
        broker.recover(desired)
        previous = load(Path(runtime_root)) if os.path.lexists(desired.config_path) else None
        custody.assert_owned()
        broker.reconcile(desired, previous=previous)
        custody.assert_owned()
        return desired


def remove(runtime_root: Path, *, root: Path | None = None) -> None:
    """Remove an adopted component before its owning installer deletes runtimes."""
    if os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        raise core.StateError("native Windows broker removal is unavailable")
    execution_root = core._root(root)
    custody = borrowed_custody(root=execution_root)
    if custody is None:
        inherited = participant_environment()
        if not inherited:
            raise core.StateError("Windows broker removal requires live installation custody")
        custody = participant(root=execution_root, witness=inherited[ENVIRONMENT_KEY])
    with custody, custody.execution_scope():
        from agent_procutil.windows_broker_installation import installation_identity, load
        from agent_procutil.windows_broker_retention import runtime_component
        from agent_procutil import windows_task_broker as broker

        runtime_root = Path(runtime_root)
        installation_identity(runtime_root)
        custody.assert_owned()
        config_path = runtime_root / "windows-launch-broker.json"
        if os.path.lexists(config_path.with_suffix(".pending.json")):
            raise core.StateError("Windows broker publication requires installer recovery before removal")
        if not os.path.lexists(config_path):
            return
        config = load(runtime_root)
        runtime = runtime_component(config, "versioned_runtime")
        generation = Path(config.entrypoint).relative_to(runtime_root).parts[1]
        with runtime.retention_lease(runtime_root, generation):
            custody.assert_owned()
            broker.uninstall(config)
            custody.assert_owned()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--slot", required=True, type=Path)
    parser.add_argument("--execution-root", required=True, type=Path)
    args = parser.parse_args(argv)
    adopt(args.runtime_root, args.slot, root=args.execution_root)
    print(json.dumps({"adopted": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
