"""Provider-owned activation without guest provisioning or worker launch."""

from __future__ import annotations

import argparse
import os
import subprocess
from urllib.parse import quote

from agent_procutil import no_window_flags

from . import launch_policy, lifecycle
from .allocation import verified_account_token


def add_start_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("start", help="Activate an existing CodeSpace and wait for Available")
    parser.add_argument("name", help="Exact CodeSpace name")
    parser.set_defaults(func=cmd_start)


def cmd_start(args: argparse.Namespace) -> int:
    refused = launch_policy.refused_exit_code(args.name)
    if refused is not None:
        return refused
    exists, state, account = lifecycle.get_codespace_status_with_account(args.name)
    if not exists:
        raise RuntimeError("The exact CodeSpace is not present in the owning account inventory")
    if state == "Available":
        return 0
    if account is None:
        try:
            identity = subprocess.run(
                ["gh", "api", "user", "--jq", ".login"], capture_output=True,
                text=True, timeout=30, creationflags=no_window_flags(),
                env=dict(os.environ, GH_HOST="github.com"),
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("Cannot resolve the authenticated CodeSpace owner") from error
        account = identity.stdout.strip()
        if identity.returncode or not account or any(char.isspace() for char in account):
            raise RuntimeError("Cannot resolve the authenticated CodeSpace owner")
        exists, state, _ = lifecycle.get_codespace_status_with_account(args.name, account=account)
        if not exists:
            raise RuntimeError("The exact CodeSpace is absent under its authenticated owner")
        if state == "Available":
            return 0
    if lifecycle.classify_state(state or "") == "failed":
        raise RuntimeError(f"CodeSpace cannot activate from state {state}")
    if state == "Shutdown":
        token = verified_account_token(account)
        if not token:
            raise RuntimeError("Cannot authenticate the CodeSpace owner; refusing ambient fallback")
        env = dict(os.environ, GH_TOKEN=token, GH_HOST="github.com")
        env.pop("GITHUB_TOKEN", None)
        try:
            done = subprocess.run(
                ["gh", "api", "--method", "POST", f"/user/codespaces/{quote(args.name, safe='')}/start"],
                capture_output=True, text=True, timeout=30, env=env, creationflags=no_window_flags(),
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"CodeSpace owner activation is unavailable: {error}") from error
        if done.returncode:
            raise RuntimeError(f"CodeSpace owner activation failed: {done.stderr.strip()}")
    if not lifecycle.wait_for_available(args.name, timeout=180, interval=2, account=account):
        raise RuntimeError("CodeSpace did not become Available after activation")
    return 0
