"""Responsive, durable confirmation before a Picker releases launch effects."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static

from .launch_attempts import LaunchAttempts
from .production_picker.picker_tui.background import run_background
from .production_picker.picker_tui.inbox import Inbox
from .seed_history import SeedHistory

log = logging.getLogger(__name__)


@dataclass
class _Confirmation:
    intent: dict = field(repr=False)
    decision: dict = field(repr=False)
    attempt_id: str = field(default_factory=lambda: uuid4().hex)


def new_decision(dlg: dict, prompt: str, *, is_local: bool) -> dict:
    """Freeze the New dialog's selected options and execution target."""
    on = {option["label"] for option in dlg["opts"] if option["on"]}
    machine, environment = dlg.get("target", (None, None))
    return {
        "action": "new", "machine": machine, "env": environment, "is_local": is_local,
        "options": {
            "anchor": "Anchor repo" in on,
            "bare": "Bare" in on,
            "no_mux": "No Mux" in on,
            "ahp": "AHP" in on,
            "local_model": "Local model" in on,
            "seed_prompt": prompt,
        },
    }


def confirm_before_effect(
    owner,
    project: str,
    decision: dict,
    *,
    surface: str,
    saved: Callable[[dict], None],
    prompt: str | None = None,
    refusal: str | None = None,
    refusal_code: str = "unsupported",
    history: SeedHistory | None = None,
    enabled: bool = True,
) -> bool:
    """Show saving immediately, then invoke *saved* only after durable intent.

    A failed identical confirmation retains its identity for explicit retry.
    Closing the application or removing the original screen never replays it.
    """
    app = owner.app
    original_screen = app.screen
    if owner is not app and (not owner.is_mounted or owner.screen is not original_screen):
        return False
    if isinstance(original_screen, _SavingLaunch) or getattr(owner, "_launch_confirmation_pending", False):
        return False
    snapshot = deepcopy(decision)
    snapshot["project"] = project
    if not enabled:
        if refusal:
            owner.notify(refusal, severity="error")
            return False
        saved(snapshot)
        return True
    options = deepcopy(snapshot.get("options") or {})
    execution_prompt = options.pop("seed_prompt", "")
    options["title"] = snapshot.get("title")
    action = snapshot.get("journal_action") or snapshot["action"]
    if options.get("anchor"):
        action = "base"
    elif options.get("bare_resume"):
        action = "bare-resume"
    intent = {
        "action": action, "project": project, "surface": surface,
        "is_local": snapshot.get("is_local"),
        "machine": snapshot.get("machine"), "environment": snapshot.get("env"),
        "provider": snapshot.get("provider"), "venue_id": snapshot.get("venue"),
        "worktree_id": snapshot.get("worktree_id"),
        "prompt": execution_prompt if prompt is None else prompt,
        "options": options,
    }
    previous = getattr(owner, "_launch_confirmation", None)
    confirmation = (
        previous if previous is not None and previous.intent == intent
        and previous.decision == snapshot else _Confirmation(intent, snapshot)
    )
    owner._launch_confirmation = confirmation
    owner._launch_confirmation_pending = True

    def after(error: str | None) -> None:
        owner._launch_confirmation_pending = False
        if (
            not original_screen.is_mounted or app.screen is not original_screen
            or owner is not app and not owner.is_mounted
        ):
            return
        if error:
            owner.notify(error, severity="error")
            return
        owner._launch_confirmation = None
        if refusal:
            owner.notify(refusal, severity="error")
            return
        result = deepcopy(confirmation.decision)
        result["attempt_id"] = confirmation.attempt_id
        saved(result)

    app.push_screen(_SavingLaunch(
        confirmation, refusal=refusal, refusal_code=refusal_code, history=history,
    ), after)
    return True


class _SavingLaunch(ModalScreen[str | None]):
    CSS = """
    _SavingLaunch { align: center middle; background: $background 55%; }
    _SavingLaunch Static {
        width: 60; height: auto; padding: 1 2;
        border: round $accent; background: $surface;
    }
    """

    def __init__(
        self, confirmation: _Confirmation, *, refusal: str | None, refusal_code: str,
        history: SeedHistory | None,
    ) -> None:
        super().__init__()
        self._confirmation = confirmation
        self._refusal = refusal
        self._refusal_code = refusal_code
        self._history = history
        self._inbox = Inbox(self)
        self._threads: set[threading.Thread] = set()
        self._cancel = threading.Event()

    def compose(self) -> ComposeResult:
        yield Static("Saving launch intent...\nNo launch will begin until the save completes.")

    def on_mount(self) -> None:
        confirmation, refusal, history = self._confirmation, self._refusal, self._history
        refusal_code = self._refusal_code

        def work() -> str | None:
            try:
                store = LaunchAttempts()
                store.record_intent(confirmation.attempt_id, **confirmation.intent)
                if refusal:
                    store.append(
                        confirmation.attempt_id, owner="manager", phase="refused",
                        code=refusal_code,
                    )
                elif history is not None:
                    history.remember(confirmation.intent["prompt"])
            except Exception as exc:
                # Never route private input, chained storage errors or receipts
                # through the generic background worker's diagnostic logging.
                log.error("Launch confirmation failed (%s).", type(exc).__name__)
                return "Could not save launch confirmation. Your draft is unchanged; no launch started."
            return None

        run_background(
            inbox=self._inbox, bg_threads=self._threads, cancel=self._cancel,
            label="Saving launch intent", work=work, done=self.dismiss, quiet=True,
        )

    def on_inbox_updated(self) -> None:
        self._inbox.drain_apply()

    def on_unmount(self) -> None:
        self._cancel.set()
