"""Explicit vendor OAuth ceremony; never called by inventory or rendering."""
from __future__ import annotations

import argparse
import json
import queue
import re
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from agent_procutil import no_window_flags, spawn_sync_in_kill_on_close_job

from . import gh_account, picker_auth


def add_parser(sub) -> None:
    parser = sub.add_parser("authorize", help="Explicitly sign in/authorize CodeSpaces on this device")
    parser.add_argument("--account", default="", help="Required GitHub login, if already selected")
    parser.add_argument("--user-initiated", action="store_true", help="Confirm explicit operator activation")
    parser.set_defaults(func=command)


def command(args: argparse.Namespace) -> int:
    def emit(kind: str, message: str) -> None:
        key = "msg" if kind == "progress" else "message"
        print(json.dumps({"type": kind, key: message}), flush=True)

    if not args.user_initiated:
        emit("error", "Authorization requires an explicit user action.")
        return 1
    try:
        login = authorize(args.account, lambda message: emit("progress", message))
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        emit("error", str(exc))
        return 1
    except KeyboardInterrupt:
        emit("error", "Authorization cancelled.")
        return 1
    emit("done", f"Authorized {login}; refresh CodeSpaces.")
    return 0


def authorize(account: str, progress) -> str:
    """Run gh in an isolated config, adopt only a scope/identity-verified result."""
    if account and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", account):
        raise ValueError("Invalid GitHub account.")
    root = picker_auth.auth_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Pending credentials never become discoverable after an interrupted attempt.
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as directory:
        pending = Path(directory)
        env = picker_auth.config_env(pending)
        progress(
            f"Authorize GitHub account {account or '(choose in browser)'} on this device.\n"
            "Complete the vendor browser/device-code instructions below. Esc cancels."
        )
        _login(env, progress)
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"], env=env,
            capture_output=True, text=True, timeout=15, creationflags=no_window_flags(),
        )
        login = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login):
            raise RuntimeError("Authorization completed but the GitHub account could not be verified.")
        if account and login.casefold() != account.casefold():
            raise RuntimeError(f"Signed in as {login}, not {account}; credential was not adopted.")
        rc, text = picker_auth.status(env)
        if picker_auth.classify(rc, text, login)["status"] != "ready":
            raise RuntimeError("Authorization did not grant a verified codespace scope.")
        # The vendor owns hosts.yml and its secure-store credentials. Moving its
        # config preserves those semantics without touching global gh selection.
        destination = root / login.casefold()
        destination.mkdir(mode=0o700, exist_ok=True)
        for name in ("hosts.yml", "config.yml"):
            source = pending / name
            if source.exists():
                source.replace(destination / name)
        (destination / "accepted-login").write_text(login, encoding="utf-8")
    gh_account.clear_caches()
    return login


def _login(env: dict[str, str], progress) -> None:
    # Keep the ceremony device-code-only: a browser launched as a descendant
    # would otherwise share the cancellable vendor process's lifetime.
    browser = [sys.executable, "-c", "pass"]
    env = {**env, "GH_BROWSER": (
        subprocess.list2cmdline(browser) if os.name == "nt" else shlex.join(browser)
    )}
    proc, job = spawn_sync_in_kill_on_close_job(
        ["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https",
         "--web", "--scopes", "codespace"],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, creationflags=no_window_flags(),
        require_job=True, allow_breakaway=False,
    )
    output: queue.Queue[str] = queue.Queue()

    def read() -> None:
        for line in proc.stdout:
            output.put(line)

    threading.Thread(target=read, daemon=True).start()
    previous_handler = None
    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.signal(signal.SIGTERM, _interrupt)
    messages = []
    try:
        # --web remains noninteractive on a pipe; supplying Enter also supports
        # vendor versions that request it before opening the browser.
        proc.stdin.write("\n")
        proc.stdin.close()
        deadline = time.monotonic() + 900
        while proc.poll() is None or not output.empty():
            if time.monotonic() > deadline:
                raise RuntimeError("GitHub authorization timed out; try again.")
            try:
                line = output.get(timeout=0.1).strip()
            except queue.Empty:
                continue
            # Never relay token-shaped vendor output, even on a failure path.
            if re.search(r"\b(?:gh[pousr]_|github_pat_)\S+", line, re.IGNORECASE):
                line = "[credential output withheld]"
            messages.append(line)
            progress("\n".join(messages[-12:]))
        if proc.returncode:
            detail = messages[-1] if messages else f"exit {proc.returncode}"
            raise RuntimeError(f"GitHub authorization failed: {detail}")
    finally:
        if job is not None:
            job.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)


def _interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt
