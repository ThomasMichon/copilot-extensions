"""Internal single-use, mux-hosted UI process owned by the Manager resident."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path


def publish(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def main(directory: Path, owner_pid: int, owner_start: str) -> int:
    from .warm_picker_contract import invocation_context, worker_environment

    environment = worker_environment(dict(os.environ))
    os.environ.clear()
    os.environ.update(environment)
    publish(directory / "startup.json", {"phase": "imports"})
    from zdd.diagnostics import _pid_alive, process_start_time

    from . import engine_client, resident_picker
    from .production_picker import runner
    from .production_picker.picker_tui import engine, data_local, data_ssh  # noqa: F401

    def owner_live():
        return _pid_alive(owner_pid) and str(process_start_time(owner_pid)) == owner_start

    def cancelled():
        return (directory / "cancel").exists() or not owner_live()

    def receive():
        publish(directory / "startup.json", {"phase": "pending-frame"})
        publish(directory / "ready.json", {
            "pid": os.getpid(), "start": process_start_time(os.getpid()),
            "context": invocation_context(manager_root=directory.parents[2]),
        })
        request = directory / "request.json"
        while not request.is_file():
            if cancelled():
                raise RuntimeError("warm UI owner is unavailable")
            time.sleep(0.02)
        deadline = time.monotonic() + 15.0
        while not (directory / "attached").is_file():
            if (
                time.monotonic() >= deadline or cancelled()
            ):
                raise RuntimeError("warm UI attachment is unavailable")
            time.sleep(0.02)
        payload = json.loads(request.read_text(encoding="utf-8"))
        context = invocation_context(manager_root=directory.parents[2])
        if context is None or payload.get("context") != context:
            raise ValueError("warm UI environment differs from admitted caller")
        for name, key in (
            ("launch_id", "AGENT_WORKTREES_LAUNCH_ID"),
            ("binstub_started", "AGENT_WORKTREES_BINSTUB_STARTED"),
        ):
            value = payload.get(name, "")
            if not isinstance(value, str) or len(value) > 512 or "\0" in value:
                raise ValueError("invalid warm UI launch identity")
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        os.chdir(payload["cwd"])
        os.environ[engine_client.ENGINE_ARGV_ENV] = json.dumps(payload["engine"])
        os.environ[resident_picker.BOOTSTRAP_ENV] = json.dumps(payload["handoff"])
        project, bootstrap = resident_picker.consume(
            ["--project", payload["handoff"]["bootstrap"]["project"]]
        )
        from .production_picker.picker_tui.frame_health import append_launch_event
        from .process_priority import raise_current_process_priority

        raise_current_process_priority()
        append_launch_event("warm_picker_ui_activated")
        return project, bootstrap

    try:
        decision = runner.run("", bootstrap_supplier=receive, lifetime_cancelled=cancelled)
        if cancelled():
            return 130
        publish(directory / "result.json", {"ok": True, "decision": decision})
        return 0
    except Exception as error:
        if cancelled():
            return 130
        publish(directory / "result.json", {"ok": False, "error": type(error).__name__})
        return 1
    finally:
        if not owner_live():
            try:
                for name in (
                    "startup.json", "startup.tmp", "ready.json", "ready.tmp",
                    "request.json", "request.tmp", "result.json", "result.tmp",
                    "attached", "consumed", "cancel",
                ):
                    (directory / name).unlink(missing_ok=True)
                directory.rmdir()
                if directory.parent.parent.name == "picker-sessions":
                    try:
                        directory.parent.rmdir()
                    except OSError:
                        pass  # Another UI from the same owner can still be exiting.
            except OSError:
                logging.getLogger(__name__).exception("Orphan warm UI files could not be retired")


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]))
