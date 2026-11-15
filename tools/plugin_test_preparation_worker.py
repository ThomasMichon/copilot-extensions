"""Run preparation inside the caller's existing contained process-tree owner."""

import importlib.util
import os
from pathlib import Path
import sys


def main():
    name, uv, retained, mode = sys.argv[1:]
    source = Path(__file__).with_name("run-plugin-tests.py")
    spec = importlib.util.spec_from_file_location("contained_preparation", source)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.VENV_ROOT = Path(retained)
    runner._prepare_venv(name, uv, reinstall=mode == "reinstall", environment=dict(os.environ))


if __name__ == "__main__":
    main()
