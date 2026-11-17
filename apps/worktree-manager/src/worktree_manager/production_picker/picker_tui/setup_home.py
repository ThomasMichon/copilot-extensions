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
from .configuration_form import CommandResultScreen, ConfigurationDetailScreen

DIAGNOSTIC_TIMEOUT = 30.0

_GUIDES = {
    "core": ("Install core", (
        "Owner: Worktree Manager setup and the existing prerequisite/core installers.\n\n"
        "First review the read-only setup plan below. No installation runs from this guide.\n\n"
        "To apply, Quit and run worktree-manager setup --apply in your terminal. "
        "This can install prerequisites and the agent-worktrees core on this machine. "
        "Use the intended installation context and retain the command's actual result; "
        "follow any restart instructions before returning and selecting Reload.\n\n"
        "The real installer requires a copilot-extensions source checkout or the "
        "bootstrap's staging checkout. If neither is available, follow the Manager "
        "installation instructions rather than using Update to bootstrap a missing engine.\n\n"
        "In-app Apply is unavailable; the diagnostic deadline is not an installer lifetime.\n\n"
        "https://github.com/ThomasMichon/copilot-extensions/blob/main/worktree-manager/README.md"
    )),
    "machine": ("Add machine", (
        "Owner: the selected repository's machine topology and native SSH/profile workflow.\n\n"
        "There is no Add-machine writer in this setup screen. Machine display preferences "
        "cannot create a machine or grant SSH access.\n\n"
        "Next: select the repository that owns your topology. Review its machines.yaml "
        "declarations through that repository's worktree and publication flow. "
        "Use the SSH configuration reference for the supported mesh schema and the "
        "transport owner's instructions for profile emission.\n\n"
        "Topology editing, transport installation, credentials and reachability checks "
        "are separate explicit operations. Opening this guide performs none of them.\n\n"
        "https://github.com/ThomasMichon/copilot-extensions/blob/main/plugins/agent-ssh/docs/configuration.md"
    )),
    "repository": ("Adopt repo", (
        "Owner: Manager register and the engine's repository setup flow.\n\n"
        "Quit and run worktree-manager register --help for the supported arguments. "
        "Choose a credential-free HTTPS repository URL and an absolute "
        "--repository-target destination; optionally choose a full --repository-commit "
        "and a declared --context.\n\n"
        "Without --apply, register only prints the proposed declaration. "
        "It does not inspect or qualify the repository's setup code. "
        "Actual acquisition, registration and setup require --apply and retain the "
        "engine owner's account, trust and refusal checks. "
        "--trust-repository-setup is a separate explicit choice, never implied by this guide.\n\n"
        "Install core first if the engine is unavailable. In-app adoption is unavailable. "
        "Retain the owner command's actual result, then return and select Reload; "
        "this guide does not mark a repository ready.\n\n"
        "https://github.com/ThomasMichon/copilot-extensions/blob/main/docs/adoption.md"
    )),
    "plugins": ("Enable plugins", (
        "Owner: the selected repository's plugin settings and native plugin installation workflow.\n\n"
        "In-app plugin enablement is unavailable here. First choose the repository and "
        "qualify the marketplace/source and trust using its setup instructions. "
        "Review shared enabledPlugins and extraKnownMarketplaces changes through that "
        "repository's contribution flow; use private or machine-local preferences only "
        "where the owning workflow explicitly supports them.\n\n"
        "Payload installation, repository enablement, runtime installation and service "
        "activation are separate states. A loaded plugin does not establish a running service. "
        "Opening this guide does not edit settings, install a payload or start a service. "
        "After the owner workflow completes, follow its restart instructions and Reload.\n\n"
        "https://github.com/ThomasMichon/copilot-extensions/blob/main/docs/configuration.md\n"
        "https://github.com/ThomasMichon/copilot-extensions/blob/main/docs/installation.md"
    )),
}


class SetupGuideScreen(ConfigurationDetailScreen, inherit_bindings=False):
    BINDINGS = [("escape", "close", "Return")]

    def __init__(self, action):
        title, body = _GUIDES[action]
        super().__init__(
            title, {"body": body}, context="Setup / Doctor",
            return_label="Return to Setup / Doctor",
        )
        self.action = action

    def compose(self):
        yield from super().compose()
        if self.action == "core":
            yield Button("Review setup plan (read-only)", id="setup-guide-plan")

    def on_button_pressed(self, event):
        event.prevent_default()
        event.stop()
        self.dismiss("setup" if event.button.id == "setup-guide-plan" else None)


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
            for action, (title, _) in _GUIDES.items():
                yield Button(f"{title} (guide)", id=f"setup-guide-{action}")
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
        elif button and button.startswith("setup-guide-"):
            self.app.push_screen(
                SetupGuideScreen(button.removeprefix("setup-guide-")), self._guide_returned,
            )
        elif button and button.startswith("setup-project-"):
            self.app.reload_setup(self.state.projects[int(button.rsplit("-", 1)[1])])

    def _guide_returned(self, action):
        if action == "setup" and self.is_mounted:
            self._diagnose(action)

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
