"""Local, headless mux effects pinned to the authoritative session binding."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

from agent_procutil import no_window_kwargs

from .mux_question_forms import MAX_SCREEN, MuxQuestionError
from .session_lifecycle_cli import _agent_worktrees_launch_prefix


class LocalMux:
    """Resolve each observation from the selected local Worktrees owner."""

    def run(self, argv: list[str]) -> str:
        """Bound each captured subprocess and never invoke a shell."""
        try:
            result = subprocess.run(
                argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="strict", timeout=10, **no_window_kwargs(),
            )
        except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
            raise MuxQuestionError("mux command unavailable or outcome unverified") from exc
        if result.returncode or len(result.stdout) > MAX_SCREEN:
            raise MuxQuestionError("mux command failed or exceeded its output bound")
        return result.stdout

    def binding(self, session_id: str, registration: dict[str, Any]) -> dict[str, Any]:
        """Require one exact native lock/process/pane binding on this machine."""
        prefix = _agent_worktrees_launch_prefix(allow_path_fallback=False)
        if not prefix:
            raise MuxQuestionError("selected local session-binding owner is unavailable")
        try:
            value = json.loads(self.run([
                *prefix, "session-binding", "--session-id", session_id, "--json",
            ]))
        except (ValueError, TypeError) as exc:
            raise MuxQuestionError("invalid session-binding response") from exc
        if (
            not isinstance(value, dict) or value.get("found") is not True
            or value.get("session_id") != session_id
            or not registration.get("worktree_id")
            or value.get("worktree_id") != registration["worktree_id"]
            or not isinstance(value.get("mux_session"), str)
            or not re.fullmatch(r"%[0-9]+", str(value.get("pane_id", "")))
            or any(not value.get(key) for key in (
                "pane_pid", "pane_start_time", "copilot_pid", "copilot_start_time",
            ))
        ):
            raise MuxQuestionError("exact live session-to-pane binding is unavailable")
        binary = shutil.which("psmux" if os.name == "nt" else "tmux")
        if not binary:
            raise MuxQuestionError("supported local mux is unavailable")
        value["binary"] = binary
        identity = self.run([
            binary, "display-message", "-p", "-t", value["pane_id"],
            "#{session_name}|#{pane_id}|#{pane_pid}|#{session_id}:#{session_created}",
        ]).strip().split("|")
        if (
            len(identity) != 4 or identity[:3] != [
                value["mux_session"], value["pane_id"], str(value["pane_pid"]),
            ] or re.fullmatch(r"\$[0-9]+:[0-9]+", identity[3]) is None
        ):
            raise MuxQuestionError("mux pane identity changed or is ambiguous")
        return {key: value[key] for key in (
            "binary", "session_id", "worktree_id", "mux_session", "pane_id",
            "pane_pid", "pane_start_time", "copilot_pid", "copilot_start_time",
        )} | {"incarnation": identity[3]}

    def capture(self, binding: dict[str, Any]) -> str:
        """Capture the current viewport, never scrollback containing stale forms."""
        return self.run([
            binding["binary"], "capture-pane", "-p", "-t", binding["pane_id"],
        ])

    def key(self, binding: dict[str, Any], value: str, *, literal: bool = False) -> None:
        """Send a single validated navigation key or one literal answer line."""
        if not literal and value not in {"Up", "Down", "Enter"}:
            raise MuxQuestionError("unsupported mux answer key")
        args = ["send-keys", "-t", binding["pane_id"]]
        if literal:
            args.extend(["-l", "--"])
        self.run([binding["binary"], *args, value])
