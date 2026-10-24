"""Fixed broker adoption under the existing installation owner's live custody."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from . import core
from .participant import ENVIRONMENT_KEY, borrowed_custody, participant, participant_environment

if TYPE_CHECKING:
    from agent_procutil.windows_task_broker import Registration


def adopt(runtime_root: Path, slot: Path, *, root: Path | None = None) -> Registration:
    """Reconcile a qualified action without acquiring a competing mutation lock.

    The owning installer calls this after settling runtime selection, outside
    publication leases. First adoption belongs to explicit native/foreground
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
        broker.reconcile(desired, previous=previous if previous != desired else None)
        custody.assert_owned()
        return desired
