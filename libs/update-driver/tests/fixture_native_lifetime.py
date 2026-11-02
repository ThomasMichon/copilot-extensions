"""Isolated production-lifetime qualification, never a live installation."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import agent_procutil as pu
from update_driver import borrowed_custody, ensure_worker


def publish_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def main():
    action, root_text, console, mode = sys.argv[1:]
    root = Path(root_text)
    if action == "daemon":
        from agent_procutil import _job_scopes

        publish_json(
            root / "daemon-ready", {"scopes": _job_scopes.scope_names(os.environ)},
        )
        deadline = time.monotonic() + 20
        while not (root / "daemon-continue").exists():
            if time.monotonic() >= deadline:
                return 93
            time.sleep(.02)
        (root / "daemon-completed").write_text("post-installer work", encoding="ascii")
        return 0
    if action == "product-daemon":
        from agent_procutil import _job_scopes
        from agent_procutil._daemon_instance_identity import _windows_birth

        with borrowed_custody(root=root) as custody, custody.execution_scope():
            ordinary = subprocess.Popen(
                [console, "-c", "import time; time.sleep(30)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **pu.no_window_kwargs(),
            )
            result = {
                "ordinary": ordinary.pid, "ordinary_birth": _windows_birth(ordinary._handle),
                "caller_scopes": _job_scopes.scope_names(os.environ),
            }
            try:
                daemon = pu.spawn_daemon(
                    [console, str(Path(__file__).resolve()), "daemon", str(root), console, mode],
                    cwd=root, log_path=root / "daemon.log", console_children=True,
                )
            except pu.DaemonSpawnError as error:
                result.update(admitted=False, cleanup_confirmed=error.cleanup_confirmed)
            else:
                result.update(
                    admitted=True, daemon=daemon.pid, daemon_birth=_windows_birth(daemon._handle),
                )
            publish_json(root / "product-result.json", result)
            deadline = time.monotonic() + 10
            while not (root / "product-continue").exists():
                if time.monotonic() >= deadline:
                    return 94
                time.sleep(.02)
            return 0
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
