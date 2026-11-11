"""Frontend Host creation through canonical daemon lifetime admission."""

from __future__ import annotations

import os
from pathlib import Path

from agent_procutil import spawn_daemon
from agent_procutil.daemon_instances import DaemonInstanceSpec

from .launcher_scratch import observe_exit


def spawn_host(argv, *, cwd, environment, state_dir: Path, phase):
    """Return an admitted Host and its borrowed, bounded startup-output stream."""
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    try:
        with os.fdopen(write_fd, "wb") as output, open(os.devnull, "rb") as null:
            phase["spawn_attempted"] = True
            try:
                process = spawn_daemon(
                    argv, cwd=os.path.abspath(cwd or os.getcwd()),
                    env=environment, console_children=True,
                    log_path=state_dir / "host-launch.log",
                    instance=DaemonInstanceSpec(
                        "agent-bridge-session-host", state_dir, Path(__file__).resolve(),
                    ),
                    stdio_fds=(null.fileno(), output.fileno(), output.fileno()),
                )
            except BaseException as error:
                if (
                    getattr(error, "cleanup_confirmed", None) is True
                    and getattr(error, "admission_sent", None) is False
                ):
                    if getattr(error, "instance_receipt", None) is not None:
                        try:
                            observe_exit(phase, error)
                        except (OSError, ValueError, KeyError):
                            pass
                    elif getattr(error, "process_created", None) is False:
                        phase["creation_refused"] = True
                raise
        return process, stream
    except BaseException:
        stream.close()
        raise
