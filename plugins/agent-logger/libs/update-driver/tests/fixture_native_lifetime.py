"""Isolated production-lifetime qualification, never a live installation."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import agent_procutil as pu
from update_driver import borrowed_custody, ensure_worker


def main():
    action, root_text, console, mode = sys.argv[1:]
    root = Path(root_text)
    if action == "submit":
        # Explicit exception owned only by the opt-in production-lifetime lane.
        # Ordinary contained tests and the paired control retain suppression.
        if mode == "production":
            os.environ.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
        else:
            os.environ["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
        control = subprocess.Popen(
            [console, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **pu.no_window_kwargs(),
        )
        worker = ensure_worker(
            (console, "-m", "update_driver", "worker", "--root", str(root)), root=root,
        )
        (root / "submitter.json").write_text(
            json.dumps({"worker": worker.pid, "control": control.pid}), encoding="utf-8",
        )
        return 0
    if action == "product":
        with borrowed_custody(root=root) as custody:
            (root / "started").write_text(str(os.getpid()), encoding="ascii")
            deadline = time.monotonic() + 20
            while not (root / "continue").exists():
                if time.monotonic() >= deadline:
                    return 91
                time.sleep(.02)
            custody.assert_owned()
            result = subprocess.run(
                [console, "-c", "print('post-Job console child', flush=True)"],
                check=False, timeout=5, stdout=sys.stdout, stderr=sys.stderr,
                **pu.no_window_kwargs(),
            )
            print("post-Job installation stage", flush=True)
            return 7 if result.returncode == 0 else 92
    raise RuntimeError("unknown synthetic fixture action")


if __name__ == "__main__":
    raise SystemExit(main())
