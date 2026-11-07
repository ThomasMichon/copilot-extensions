"""Read Manager-owned launch assets under its existing retention bootstrap."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> int:
    from worktree_manager.__main__ import main as manager_main

    sys.argv = ["worktree-manager", "--version"]
    code = manager_main(["--version"])
    if code != 0:
        return code

    from worktree_manager.relocated_launch import _relocated_launch_script
    from worktree_manager.runtime_identity import running_application_slot

    slot, script = running_application_slot(), _relocated_launch_script()
    if slot is None or script is None:
        raise RuntimeError("Manager application has no owned launcher")
    directory = Path(script).resolve().parent
    for ancestor in directory.parents:
        if ancestor.samefile(slot):
            print(json.dumps({"launcher_directory": str(directory.relative_to(ancestor))}))
            return 0
    raise RuntimeError("Manager application launcher is outside its runtime slot")


if __name__ == "__main__":
    from worktree_manager._shutdown_exit import run_and_exit

    # Apply Manager's finalization workaround only after the resource report.
    run_and_exit(main)
