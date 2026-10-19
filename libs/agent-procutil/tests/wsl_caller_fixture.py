"""Native WSL fixture: abrupt caller death must not depend on Python finally."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def main() -> None:
    config = json.loads(Path(sys.argv[1]).read_text())
    sys.path.insert(0, config["source"])
    from agent_procutil.wsl import run_windows_owned

    def interrupt() -> None:
        gate = Path(config["gate"])
        while not gate.exists():
            time.sleep(.01)
        os.kill(os.getpid(), signal.SIGINT if config["case"] == "sigint" else signal.SIGKILL)

    threading.Thread(target=interrupt, daemon=True).start()
    try:
        result = run_windows_owned(
            config["argv"], windows_python=config["pythonw"],
            timeout=4 if config["case"] in {"timeout", "stdin-stall"} else 30,
            input_text="x" * 524288 if config["case"] == "stdin-stall" else None,
        )
    except subprocess.TimeoutExpired:
        print(json.dumps({"outcome": config["case"]}), flush=True)
    except KeyboardInterrupt:
        print(json.dumps({"outcome": "sigint"}), flush=True)
    else:
        raise RuntimeError(f"Fixture command unexpectedly completed: {result.returncode}")


if __name__ == "__main__":
    main()
