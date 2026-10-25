"""Explicit same-window Companion navigation; never restart the CLI."""

from __future__ import annotations

import os
import hashlib
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from threading import Event
from pathlib import Path

from . import engine_client as ec
from .companion_client import cancellable_read_runner
from .mux_child_process import run as run_mux_child
from .mux_mapping_registry import get_mapping, _try_lock_file_once, _unlock_file
from .self_install import default_root

_PANE = re.compile(r"%\d+\Z")
_INCARNATION = re.compile(r"\$\d+:\d+\Z")


class PaneError(RuntimeError):
    """No verified same-window navigation target is available."""


def _call(mapping: dict, *args: str, cancelled=None) -> str:
    if cancelled is not None and cancelled.is_set():
        raise PaneError("View switch cancelled.")
    environment = dict(os.environ)
    arguments = list(args)
    if os.name == "nt":
        environment["PSMUX_SESSION"] = mapping["mux_session"]
        if "-t" in arguments:
            index = arguments.index("-t") + 1
            if index < len(arguments) and _PANE.fullmatch(arguments[index]):
                arguments[index] = mapping["mux_session"] + ":" + arguments[index]
    elif arguments[0] in {"show-options", "set-option"} and "-t" in arguments:
        index = arguments.index("-t") + 1
        if arguments[index] == "=" + mapping["mux_session"]:
            arguments[index] = mapping["session_incarnation"].split(":", 1)[0]
    try:
        result = run_mux_child(
            [mapping["mux_bin"], *arguments], capture_output=True, text=True,
            env=environment,
            timeout=5, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise PaneError(f"Mux navigation failed: {exc}") from exc
    if result.returncode:
        raise PaneError((result.stderr or "Mux navigation failed.").strip())
    return result.stdout.rstrip("\r\n")


@contextmanager
def _navigation_guard(mapping: dict, cli: str, cancelled=None):
    key = "\0".join(str(mapping.get(field, "")) for field in (
        "project", "worktree_id", "mux_session", "session_incarnation",
    )) + "\0" + cli
    folder = default_root() / "companion-panes"
    path = folder / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            deadline = time.monotonic() + 10
            while not _try_lock_file_once(handle):
                if cancelled is not None and cancelled.is_set():
                    raise PaneError("View switch cancelled.")
                if time.monotonic() >= deadline:
                    raise PaneError("Another view switch is still in progress.")
                time.sleep(0.05)
            try:
                yield
            finally:
                _unlock_file(handle)
    except OSError as exc:
        raise PaneError(f"View switch guard unavailable: {exc}") from exc


def _target(cwd: str, cli_pane: str | None, runner=None, cancelled=None) -> tuple[dict, str, str, list[dict]]:
    status = ec.current_worktree_status(path=cwd, runner=runner)
    if not isinstance(status, dict):
        raise PaneError("The current worktree status is unavailable.")
    project, worktree_id = status.get("repo"), status.get("id")
    if not project or not worktree_id:
        raise PaneError("The current worktree has no registered mux identity.")
    try:
        mapping = get_mapping(str(project), str(worktree_id))
    except (OSError, ValueError) as exc:
        raise PaneError(f"The registered mux identity is unavailable: {exc}") from exc
    if not mapping or not mapping.get("live"):
        raise PaneError("The current worktree has no live mux mapping.")
    if Path(str(mapping.get("worktree_path") or "")).resolve() != Path(
        str(status.get("path") or cwd)
    ).resolve():
        raise PaneError("The mux mapping belongs to a different worktree path.")
    if not _INCARNATION.fullmatch(str(mapping.get("session_incarnation", ""))):
        raise PaneError("The registered mux session is no longer current.")
    candidates = [
        pane["pane_id"] for pane in mapping.get("panes", [])
        if pane.get("role") == "cli" and pane.get("live") is True
        and _PANE.fullmatch(str(pane.get("pane_id", "")))
    ]
    if len(candidates) != 1 or (cli_pane is not None and candidates[0] != cli_pane):
        raise PaneError("The CLI pane identity is missing, ambiguous, or changed; reopen from the launcher.")
    cli = candidates[0]
    raw = _call(
        mapping, "list-panes", "-s", "-t", "=" + mapping["mux_session"], "-F",
        "#{pane_id}\t#{window_id}\t#{pane_dead}\t"
        "#{session_name}\t#{session_id}:#{session_created}\t#{pane_current_path}",
        cancelled=cancelled,
    )
    panes = []
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) != 6 or not _PANE.fullmatch(fields[0]):
            raise PaneError("Mux returned an invalid pane inventory.")
        panes.append(dict(zip(("id", "window", "dead", "session", "incarnation", "path"), fields)))
    matches = [pane for pane in panes if pane["id"] == cli and pane["dead"] == "0"]
    if len(matches) != 1:
        raise PaneError("The registered CLI pane is no longer live.")
    native = matches[0]
    if (
        native["session"] != mapping["mux_session"]
        or native["incarnation"] != mapping["session_incarnation"]
    ):
        raise PaneError("The registered mux session is no longer current.")
    # On Windows the session's directory can be home even though its CLI
    # pane is in the worktree. Attribute the exact registered pane instead.
    if not native["path"] or Path(native["path"]).resolve() != Path(mapping["worktree_path"]).resolve():
        raise PaneError("The registered CLI pane belongs to a different worktree path.")
    return mapping, cli, matches[0]["window"], panes


def _focus(mapping: dict, pane: str, cancelled=None) -> None:
    _call(mapping, "select-pane", "-t", pane, cancelled=cancelled)
    zoom = _call(mapping, "display-message", "-p", "-t", pane, "#{window_zoomed_flag}", cancelled=cancelled)
    if zoom == "0":
        _call(mapping, "resize-pane", "-Z", "-t", pane, cancelled=cancelled)
    elif zoom != "1":
        raise PaneError("Mux did not confirm the selected view's zoom state.")


def switch(
    action: str, cwd: str | None = None, *, cli_pane: str | None = None, runner=None,
    cancelled=None, session_incarnation: str | None = None,
) -> str:
    """Return the selected pane ID, refusing unregistered or stale targets."""
    if action not in {"show", "return"}:
        raise PaneError("Unknown Companion pane action.")
    target_path = str(Path(cwd or os.getcwd()).resolve())
    if runner is None:
        runner = cancellable_read_runner(cancelled or Event(), cwd=target_path)
    original, cli, _window, _panes = _target(target_path, cli_pane, runner, cancelled)
    if session_incarnation is not None and original.get("session_incarnation") != session_incarnation:
        raise PaneError("The observer's original CLI session has ended.")
    with _navigation_guard(original, cli, cancelled):
        mapping, current_cli, window, panes = _target(target_path, cli, runner, cancelled)
        if current_cli != cli or any(
            mapping.get(field) != original.get(field)
            for field in ("mux_session", "session_incarnation", "mux_bin")
        ):
            raise PaneError("The CLI identity changed during the view switch.")
        return _switch_verified(action, target_path, mapping, cli, window, panes, cancelled)


def _switch_verified(action, target_path, mapping, cli, window, panes, cancelled):
    if action == "return":
        _focus(mapping, cli, cancelled)
        return cli
    option = "@aw_companion_" + cli[1:]
    options = _call(mapping, "show-options", "-t", "=" + mapping["mux_session"], cancelled=cancelled)
    values = [
        line.split(None, 1)[1].strip('"')
        for line in options.splitlines()
        if line.startswith(option + " ")
    ]
    if len(values) > 1 or (values and not _PANE.fullmatch(values[0])):
        raise PaneError("The Summary pane identity is invalid or ambiguous.")
    observers = [pane for pane in panes if values and pane["id"] == values[0] and pane["dead"] == "0"]
    if observers and (observers[0]["window"] != window or observers[0]["id"] == cli):
        raise PaneError("The Summary pane no longer belongs to the CLI window.")
    if observers:
        selected = observers[0]["id"]
    else:
        from .companion_runtime import companion_shell_command

        try:
            shell_command = companion_shell_command([
                "-m", "worktree_manager.mux_companion",
                "--cli-pane", cli, "--cwd", target_path,
                "--project", mapping["project"], "--worktree-id", mapping["worktree_id"],
                "--session-incarnation", mapping["session_incarnation"],
            ])
        except (RuntimeError, ValueError) as exc:
            raise PaneError(str(exc)) from exc
        selected = _call(
            mapping, "split-window", "-d", "-P", "-F", "#{pane_id}",
            "-t", cli, "-c", target_path, shell_command,
            cancelled=cancelled,
        )
        if not _PANE.fullmatch(selected) or selected in {pane["id"] for pane in panes}:
            raise PaneError("Mux did not confirm a new Summary pane.")
        try:
            _call(mapping, "set-option", "-t", "=" + mapping["mux_session"], option, selected, cancelled=cancelled)
        except PaneError:
            try:
                _call(mapping, "kill-pane", "-t", selected)
            except PaneError:
                pass
            raise
    _focus(mapping, selected, cancelled)
    return selected


def run(action: str, cwd: str | None = None) -> int:
    try:
        switch(action, cwd)
    except (PaneError, ec.EngineError) as exc:
        print(f"Companion: {exc}", file=sys.stderr)
        return 1
    return 0
