"""Narrow native-fixture commands; never address the ambient mux or whole server."""

from pathlib import Path
import os
import re
import subprocess
from uuid import uuid4

from agent_procutil import no_window_kwargs
from plugin_test_command_safety import SANDBOX_ENV, private_mux_args, validate_private_mux_args


class FixtureMux:
    def __init__(self, binary, root: Path, environment):
        self.binary = str(binary)
        self.root = root
        self.mux_args = private_mux_args(root)
        self.sandbox = Path(os.environ[SANDBOX_ENV])
        self.target = "headed-fixture-" + uuid4().hex
        nesting = {
            "TMUX", "TMUX_PANE", "PSMUX_PANE", "PSMUX_SESSION", "PSMUX_TARGET_SESSION",
            "PSMUX_SESSION_NAME", "PSMUX_DEFAULT_SESSION", "PSMUX_CLAUDE_TEAMMATE_MODE",
        }
        self.environment = {
            key: value for key, value in environment.items() if key.upper() not in nesting
        }

    def query(self, action, *arguments):
        validate_private_mux_args(self.mux_args, sandbox=self.sandbox, cwd=self.root)
        if re.fullmatch(r"headed-fixture-[0-9a-f]{32}", self.target) is None:
            raise ValueError("An explicit fixture mux namespace and target are required")
        allowed = {
            "new-session": ("-s",), "has-session": ("-t",),
            "display-message": ("-t",), "capture-pane": ("-t",), "kill-session": ("-t",),
        }
        selectors = allowed.get(action)
        if selectors is None:
            raise ValueError("Whole-server and non-fixture mux commands are forbidden")
        mux_arguments = arguments
        if action == "new-session":
            if "--" not in arguments or arguments[-1] == "--":
                raise ValueError("A raw pane command is required after the mux arguments")
            mux_arguments = arguments[:arguments.index("--")]
        selected = [
            mux_arguments[index + 1]
            for index, value in enumerate(mux_arguments[:-1]) if value in {"-t", "-s"}
        ]
        if selected != [self.target] or not any(option in mux_arguments for option in selectors):
            raise ValueError("The exact fixture target is required")
        if any(value in {"-L", "-S", ";", "kill-server", "server"} for value in mux_arguments):
            raise ValueError("Fixture mux routing cannot be overridden")
        return subprocess.run(
            [self.binary, *self.mux_args, action, *arguments],
            env=self.environment, cwd=self.root, capture_output=True,
            text=True, encoding="utf-8", timeout=5, **no_window_kwargs(),
        )
