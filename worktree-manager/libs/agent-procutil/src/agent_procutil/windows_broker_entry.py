"""Task Scheduler's installed finite entrypoint, never a user provisioning verb."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    # -I ignores ambient PYTHONPATH; load only the action's installed package set.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agent_procutil.windows_task_broker import serve_main

    if sys.argv[1:] == ["--check-runtime"]:
        import zdd
        from zdd.private_windows_native import native

        if Path(zdd.__file__).resolve().parent.parent != Path(__file__).resolve().parent.parent:
            raise RuntimeError("broker transport is outside the action runtime")
        native().current_sid()
        sys.stdout.write("agent-procutil.windows-launch-broker.runtime-ready.v1\n")
        return 0
    return serve_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
