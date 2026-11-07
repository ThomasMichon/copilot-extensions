"""Narrow native-fixture commands; never address the ambient mux or whole server."""

from pathlib import Path
import re
import subprocess
from uuid import uuid4

from agent_procutil import no_window_kwargs


class FixtureMux:
    def __init__(self, binary, root: Path, environment):
        self.binary = str(binary)
        self.root = root
        self.namespace = "driver-test-" + uuid4().hex
        self.target = "headed-fixture-" + uuid4().hex
        nesting = {
            "TMUX", "TMUX_PANE", "PSMUX_SESSION", "PSMUX_TARGET_SESSION",
            "PSMUX_SESSION_NAME", "PSMUX_DEFAULT_SESSION", "PSMUX_CLAUDE_TEAMMATE_MODE",
        }
        self.environment = {
            key: value for key, value in environment.items() if key.upper() not in nesting
        }

    def query(self, action, *arguments):
        if (
            re.fullmatch(r"driver-test-[0-9a-f]{32}", self.namespace) is None
            or re.fullmatch(r"headed-fixture-[0-9a-f]{32}", self.target) is None
        ):
            raise ValueError("An explicit fixture mux namespace and target are required")
        allowed = {
            "new-session": ("-s",), "has-session": ("-t",),
            "display-message": ("-t",), "capture-pane": ("-t",), "kill-session": ("-t",),
        }
        selectors = allowed.get(action)
        if selectors is None:
            raise ValueError("Whole-server and non-fixture mux commands are forbidden")
        selected = [
            arguments[index + 1]
            for index, value in enumerate(arguments[:-1]) if value in {"-t", "-s"}
        ]
        if selected != [self.target] or not any(option in arguments for option in selectors):
            raise ValueError("The exact fixture target is required")
        if any(value in {"-L", "-S", ";", "kill-server", "server"} for value in arguments):
            raise ValueError("Fixture mux routing cannot be overridden")
        return subprocess.run(
            [self.binary, "-L", self.namespace, action, *arguments],
            env=self.environment, cwd=self.root, capture_output=True,
            text=True, encoding="utf-8", timeout=5, **no_window_kwargs(),
        )
