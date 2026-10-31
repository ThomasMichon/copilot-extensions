"""Dispatch a bounded mux metadata command beyond the pane's lifetime."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agent_procutil.daemon_instances import DaemonInstanceSpec, spawn_finite_worker

from .mux_daemon_process import direct_daemon_python, scrub_session_credentials, selected_root
from .self_install import default_root


def callback_command(arguments: list[str]) -> tuple[list[str], dict[str, str], Path]:
    if not arguments or arguments[0] not in ("register", "remove"):
        raise ValueError("mux callback requires register or remove")
    prefix = Path(sys.prefix).resolve()
    env = scrub_session_credentials(dict(os.environ))
    python, extra = direct_daemon_python(sys.executable)
    command = [python]
    root = selected_root(default_root())
    if prefix.parent.name == "versions":
        bootstrap = prefix / ".runtime-bootstrap" / "runtime_launcher.py"
        if not bootstrap.is_file() or not (prefix / ".install-complete.json").is_file():
            raise RuntimeError("mux callback requires a constructed Manager runtime")
        root = prefix.parent.parent
        command += ["-I", "-S", "-B", "-X", "utf8", str(bootstrap)]
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
            env.pop(name, None)
    command += ["-m", "worktree_manager", "mux-daemon", *arguments]
    env["WORKTREE_MANAGER_ROOT"] = str(root)
    env.update(extra)
    return command, env, root


def main(arguments: list[str] | None = None) -> int:
    try:
        command, env, root = callback_command(list(sys.argv[1:] if arguments is None else arguments))
        root.mkdir(parents=True, exist_ok=True)
        spawn_finite_worker(
            command, cwd=root, log_path=root / "mux-callback.log", env=env,
            console_children=True,
            instance=DaemonInstanceSpec(
                "worktree-manager-callback", root, Path(__file__).resolve(),
                lifecycle="finite-worker",
            ),
        )
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Manager mux callback failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
