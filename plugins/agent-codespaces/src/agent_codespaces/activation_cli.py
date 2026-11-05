"""Provider-owned activation without guest provisioning or worker launch."""

from __future__ import annotations

import argparse
import os
import subprocess
from urllib.parse import quote

from agent_procutil import no_window_flags

from . import gh_account, launch_policy, lifecycle


def add_start_parser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("start", help="Activate an existing CodeSpace and wait for Available")
    parser.add_argument("name", help="Exact CodeSpace name")
    parser.set_defaults(func=cmd_start)


def cmd_start(args: argparse.Namespace) -> int:
    refused = launch_policy.refused_exit_code(args.name)
    if refused is not None:
        return refused
    selected = next((cs for cs in lifecycle.list_codespaces() if cs.name == args.name), None)
    if selected is None:
        raise RuntimeError("The exact CodeSpace is not present in the owning account inventory")
    if selected.state == "Available":
        return 0
    if lifecycle.classify_state(selected.state) == "failed":
        raise RuntimeError(f"CodeSpace cannot activate from state {selected.state}")
    if selected.state == "Shutdown":
        env = None
        if selected.account:
            token = gh_account.token_for_account(selected.account)
            if not token:
                raise RuntimeError("Cannot authenticate the CodeSpace owner; refusing ambient fallback")
            env = dict(os.environ, GH_TOKEN=token)
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
    if not lifecycle.wait_for_available(args.name, timeout=180, interval=2):
        raise RuntimeError("CodeSpace did not become Available after activation")
    return 0
