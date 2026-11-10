"""Manager-owned navigation before an engine project is bound."""

from __future__ import annotations

import asyncio
import os
import subprocess

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Static

from ...first_run import diagnostic_argv
from .command_transport import child_process_env, command_result
from .configuration_form import CommandResultScreen

DIAGNOSTIC_TIMEOUT = 30.0


def _timeout_report(error):
    parts = [
        f"Diagnostic timed out after {error.timeout:g}s; no normal exit status was observed."
    ]
    for label, output in (("stdout", error.stdout), ("stderr", error.stderr)):
        if output:
            text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
            parts.append(f"{label} (partial):\n{text}")
    return "\n\n".join(parts)


class SetupHome(Widget):
    DEFAULT_CSS = """
    SetupHome { width: 100%; height: 100%; padding: 1 2; }
    SetupHome VerticalScroll { height: 1fr; }
    SetupHome Static { height: auto; margin-bottom: 1; }
    SetupHome Button { width: auto; margin-bottom: 1; }
    """
    BINDINGS = [("escape", "quit", "Quit"), ("q", "quit", "Quit"), ("r", "reload", "Reload")]

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._command_task = None
        self._cleanup_error = None

    def compose(self):
        yield Static("Worktree Manager / Setup / Doctor", markup=False)
        with VerticalScroll():
            yield Static(self.state.summary, id="setup-status", markup=False)
            for index, project in enumerate(self.state.projects or ()):
                yield Button(
                    f"Open {project}", id=f"setup-project-{index}",
                    disabled=not self.state.engine_available,
                )
            yield Static(
                "Review a plan before choosing an installation command. "
                "Repository, machine, plugin and knowledge configuration retain their existing owners.",
                markup=False,
            )
            yield Button("Review setup plan (read-only)", id="setup-plan")
            yield Button("Inspect Manager setup (read-only)", id="setup-doctor")
        yield Button("Reload", id="setup-reload")
        yield Button("Quit", id="setup-quit")

    def action_reload(self):
        if self._cleanup_error is not None:
            self.query_one("#setup-status", Static).update(
                f"Diagnostic cleanup is unconfirmed: {self._cleanup_error}\n"
                "No retry was started. Quit to return the retained failure to the caller."
            )
            for button in self.query(Button):
                button.disabled = button.id != "setup-quit"
            return
        self.app.reload_setup()

    def action_quit(self):
        self.app.exit()

    def on_button_pressed(self, event):
        event.stop()
        button = event.button.id
        if button == "setup-reload":
            self.action_reload()
        elif button == "setup-quit":
            self.action_quit()
        elif button in {"setup-plan", "setup-doctor"}:
            self._diagnose("setup" if button == "setup-plan" else "doctor")
        elif button and button.startswith("setup-project-"):
            self.app.reload_setup(self.state.projects[int(button.rsplit("-", 1)[1])])

    def _diagnose(self, action):
        result = CommandResultScreen(
            "Setup plan" if action == "setup" else "Manager diagnostics",
            context="Setup / Doctor", return_label="Return to Setup / Doctor",
        )
        self.app.push_screen(result, lambda _: self.action_reload())

        async def execute():
            try:
                argv = diagnostic_argv(action)
                environment = child_process_env()
                if action == "setup" and (context := os.environ.get("COPILOT_EXTENSIONS_CONTEXT")):
                    environment["COPILOT_EXTENSIONS_CONTEXT"] = context
                proc = await asyncio.to_thread(
                    run_caller_owned, argv, timeout=DIAGNOSTIC_TIMEOUT,
                    env=environment,
                    max_stdout_bytes=8 * 1024 * 1024, max_stderr_bytes=256 * 1024,
                )
                ok, message = command_result(proc, preserve_output=True)
                message = f"Exit status: {proc.returncode}\n\n{message}"
            except OwnedCleanupError as error:
                # Keep the exact native custody and return it through run_tui_picker.
                self._cleanup_error = self.app.binding_error = error
                ok, message = False, (
                    "Diagnostic cleanup is unconfirmed. No retry is offered; "
                    "return and Quit to surface the original failure."
                )
            except subprocess.TimeoutExpired as error:
                ok, message = False, _timeout_report(error)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                ok, message = False, str(error)
            if result.is_mounted:
                result.complete(ok, message)

        def start():
            self._command_task = asyncio.create_task(execute())

        result.call_after_refresh(start)

    async def on_unmount(self):
        if self._command_task is not None:
            await self._command_task
