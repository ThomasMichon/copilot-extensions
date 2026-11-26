"""Run preparation inside the caller's existing contained process-tree owner."""

import importlib.util
import os
from pathlib import Path
import sys
from typing import Any


def main() -> None:
    name, uv, retained, mode, *remaining = sys.argv[1:]
    if len(remaining) > 1:
        raise ValueError("unexpected preparation worker arguments")
    deadline = float(remaining[0]) if remaining else None
    source = Path(__file__).with_name("run-plugin-tests.py")
    spec = importlib.util.spec_from_file_location("contained_preparation", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("test preparation runner is unavailable")
    runner: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.VENV_ROOT = Path(retained)
    from promotion_source_archive import composition_budget
    with composition_budget(deadline):
        runner._prepare_venv(
            name, uv, reinstall=mode == "reinstall", environment=dict(os.environ),
            **({"deadline": deadline} if deadline is not None else {}),
        )


if __name__ == "__main__":
    main()
