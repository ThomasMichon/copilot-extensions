"""Read Manager-owned launch assets under its existing retention bootstrap."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys


def main() -> None:
    sys.argv = ["worktree-manager", "--version"]
    try:
        runpy.run_module("worktree_manager", run_name="__main__", alter_sys=True)
    except SystemExit as result:
        if result.code not in (None, 0):
            raise

    from worktree_manager.relocated_launch import _relocated_launch_script
    from worktree_manager.runtime_identity import running_application_slot

    slot, script = running_application_slot(), _relocated_launch_script()
    if slot is None or script is None:
        raise RuntimeError("Manager application has no owned launcher")
    directory = Path(script).resolve().parent
    for ancestor in directory.parents:
        if ancestor.samefile(slot):
            print(json.dumps({"launcher_directory": str(directory.relative_to(ancestor))}))
            return
    raise RuntimeError("Manager application launcher is outside its runtime slot")


if __name__ == "__main__":
    main()
