"""Presentation only: publication never grants acceptance or execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from threading import Event
from rich.text import Text
from . import agenda_transport, engine_client
from .agenda_contract import AgendaFeed, AgendaUnavailable


def agenda_text(frame: dict | None, error: str | None) -> Text:
    text = Text("Agenda (display only)\n", style="bold #d78700")
    if error:
        text.append(f"Unavailable: {error}", style="yellow")
        return text
    if frame is None:
        text.append("Unavailable: no current publication")
        return text
    if frame["availability"] != "observed":
        text.append(
            "Head changed; Refresh required. Previous agenda is not active."
            if frame["availability"] == "head-changed"
            else "Not published for the selected head."
        )
        return text
    observation = frame["observation"]
    snapshot = observation["snapshot"]
    text.append(
        f"Published revision {observation['revision']} at {observation['committed_at']}\n",
        style="dim",
    )
    for label, field in (
        ("Status", "status"), ("Root goal", "root"), ("Active stack", "stack"),
        ("Next action", "next_action"), ("Exit criteria", "exit_criteria"),
        ("Declared outcomes", "declared_outcomes"), ("Queued", "queued"),
        ("Parked", "parked"), ("Deferred", "deferred"), ("Due rechecks", "due_rechecks"),
        ("Next revisit", "next_revisit_at"), ("Notes", "notes"),
    ):
        value = snapshot[field]
        text.append(f"{label}: ", style="bold")
        text.append((value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)) + "\n")
    text.append("Publication is not acceptance, permission, or an action.", style="dim")
    return text


def subscribe(
    project: str, worktree: str, session: str, *, cancelled: Event, cwd: str,
    publish: Callable[[dict | None, str | None], None],
) -> None:
    """Keep reconnect admission inside one UI-owned, cancellable worker."""
    supported = False
    retired: set[str] = set()
    last: dict | None = None
    for attempt in range(4):
        if cancelled.is_set():
            return
        receiver = AgendaFeed(project, worktree, session)
        stream = agenda_transport.frames(
            project, worktree, session, cancelled=cancelled, cwd=cwd,
        )
        try:
            with closing(stream):
                for frame in stream:
                    if isinstance(frame.get("generation"), str) and frame["generation"] in retired:
                        raise ValueError("agenda feed returned a retired resident generation")
                    admitted = receiver.receive(frame)
                    if admitted is not None:
                        if last is not None:
                            if (
                                admitted["generation"] == last["generation"]
                                and admitted["sequence"] < last["sequence"]
                            ):
                                raise ValueError("agenda reconnect regressed its resident sequence")
                            old, new = last["observation"], admitted["observation"]
                            if old and new and old["source"]["store_id"] == new["source"]["store_id"]:
                                if (
                                    new["revision"] < old["revision"]
                                    or (new["revision"] == old["revision"] and new != old)
                                ):
                                    raise ValueError("agenda reconnect regressed or rewrote its store revision")
                        last = admitted
                        supported = True
                        if not cancelled.is_set():
                            publish(admitted, None)
                        if admitted["availability"] == "head-changed":
                            return
            if cancelled.is_set():
                return
            raise AgendaUnavailable("agenda feed ended without a terminal notice")
        except InterruptedError as exc:
            if not cancelled.is_set():
                publish(None, f"agenda read interrupted unexpectedly: {exc}")
            return
        except (AgendaUnavailable, engine_client.EngineError, ValueError, RecursionError) as exc:
            if cancelled.is_set():
                return
            if receiver.closed and receiver.current is not None:
                retired.add(receiver.current["generation"])
            retry = isinstance(exc, AgendaUnavailable) and exc.retry and supported and attempt < 3
            message = str(exc)
            if retry:
                message += f"; reconnecting in {2**attempt}s (new snapshot required)"
            publish(None, message)
            if not retry:
                return
        if cancelled.wait(2**attempt):
            return
