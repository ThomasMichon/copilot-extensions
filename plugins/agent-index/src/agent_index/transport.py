"""Project-aware, role-routed transport for the read subcommands.

The four read subcommands (``search``/``similar``/``clusters``/``status``) are
project-aware transport routers. A **client** machine runs no local indexer, so
its read commands are executed **on the designated indexer host over SSH** — not
via a port-forward or a relay. The dynamic zero-downtime service port never
leaves the host; the command runs *on* the host, where ``agent-index`` resolves
its own live local endpoint.

Routing (per invocation):

1. Resolve the backing **project** from the current directory (git top-level, or
   ``AGENT_INDEX_REPO``). The project selects which ``.agent-index/config.yaml``
   ``indexer:`` block governs (``machine``/``ssh``/``shell``).
2. Determine this machine's **role** for that project: ``host`` iff
   ``machine_id()`` equals the project's ``indexer.machine`` (canonical hostname).
   When no project/indexer is resolvable (e.g. the delegated command running in
   the host's home directory over SSH), fall back to the machine-global
   ``resolve_role()`` — which makes the host run locally and so **terminates the
   SSH recursion at the host**.
3. **Route:** ``host`` → run locally (return ``None`` so the caller dispatches the
   normal local command). ``client`` → run the *same* ``agent-index <argv>`` on
   the project's indexer over SSH, forwarding stdout. A client with **no**
   resolvable project/indexer is refused with a clear message (the transport is
   project-specific — there is no single global default).
"""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
import sys
from typing import Any

from . import config

# Read subcommands that route through the transport.
DELEGABLE = ("search", "similar", "clusters", "status")

# Default remote shell when a project's ``indexer.shell`` is unset. The mesh's
# indexer is Windows (pwsh); a Linux indexer must declare ``shell: bash``.
DEFAULT_SHELL = "pwsh"


def plan_route() -> tuple[str, dict | None]:
    """Return ``(role, indexer)`` for the current directory.

    ``role`` is ``"host"`` or ``"client"``; ``indexer`` is the resolved project's
    ``indexer:`` mapping, or ``None`` when no project/designation is resolvable.
    """
    root = config.repo_root()
    indexer = config.read_indexer(root) if root is not None else None
    if indexer is not None:
        designated = str(indexer.get("machine", "")).strip().lower()
        role = "host" if config.machine_id().strip().lower() == designated else "client"
    else:
        role = config.resolve_role()
    return role, indexer


def _q_pwsh(arg: str) -> str:
    """Single-quote *arg* for a pwsh command line ('' escapes a quote)."""
    return "'" + arg.replace("'", "''") + "'"


def _pwsh_remote(inner: str) -> str:
    """Wrap *inner* as a remote pwsh ``-EncodedCommand`` invocation.

    Uses base64 of UTF-16LE (mirrors agent-worktrees' ``_pwsh_remote``): a plain
    ``-Command '<inner>'`` is mangled when the remote sshd default shell is
    cmd.exe (the Windows dtssh hosts), which echoes the single-quoted text
    instead of running it. ``-EncodedCommand`` carries no shell-special
    characters, so it executes correctly regardless of the remote default shell.
    """
    enc = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
    return f"pwsh -NoProfile -WindowStyle Hidden -EncodedCommand {enc}"


def _build_inner(shell: str, argv: list[str]) -> str:
    """Build the remote command that runs ``agent-index <argv>`` on the host.

    Invokes the host's installed binstub by absolute path (resolvable over a
    non-interactive SSH logon, where ``~/.local/bin`` may not be on PATH).
    """
    if shell == "bash":
        quoted = " ".join(shlex.quote(a) for a in argv)
        return f'"$HOME/.local/bin/agent-index" {quoted}'
    # pwsh (default)
    quoted = " ".join(_q_pwsh(a) for a in argv)
    return f'& "$env:USERPROFILE\\.local\\bin\\agent-index.ps1" {quoted}'


def build_ssh_argv(indexer: dict, argv: list[str]) -> list[str]:
    """Build the local ``ssh`` argv that runs ``agent-index <argv>`` on the host."""
    alias = str(indexer["ssh"]).strip()
    shell = str(indexer.get("shell") or DEFAULT_SHELL).strip().lower()
    inner = _build_inner(shell, argv)
    if shell == "bash":
        escaped = inner.replace("'", "'\\''")
        return ["ssh", alias, f"bash -lc '{escaped}'"]
    return ["ssh", alias, _pwsh_remote(inner)]


def _forward_argv(sub: str, raw_argv: list[str]) -> list[str]:
    """The argv to run on the host: the original argv, forcing JSON for search."""
    argv = list(raw_argv)
    if sub == "search" and "--json" not in argv:
        argv.append("--json")
    return argv


def _emit_error(payload: dict[str, Any]) -> int:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 1


def maybe_delegate(sub: str, raw_argv: list[str]) -> int | None:
    """Route a read subcommand.

    Returns ``None`` when the command should run **locally**; otherwise delegates
    over SSH to the project's indexer and returns the remote exit code.

    Decision:

    * A positively-resolved indexer designation for the current project routes:
      this machine is the ``host`` (``machine_id() == indexer.machine``) → run
      local (``None``); otherwise → delegate ``agent-index <argv>`` to
      ``indexer.ssh``.
    * No usable indexer for the current directory: **refuse** only when truly
      bare — not inside any repo — on a ``client``-role machine (there is no
      project to pick a transport target, and a client has no local store).
      Everywhere else (inside a project without a designation, or a
      host/standalone box) run locally, preserving direct local dispatch. This
      also terminates the SSH recursion: the delegated command runs in the host's
      home dir (no project), where the host resolves to local.
    """
    if sub not in DELEGABLE:
        return None
    root = config.repo_root()
    indexer = config.read_indexer(root) if root is not None else None

    if indexer and indexer.get("ssh"):
        designated = str(indexer.get("machine", "")).strip().lower()
        if config.machine_id().strip().lower() == designated:
            return None  # this machine is the host for this project -> run local
        # client -> run the same command on the indexer host over SSH
        argv = build_ssh_argv(indexer, _forward_argv(sub, raw_argv))
        try:
            proc = subprocess.run(argv, check=False)  # noqa: S603 - argv built from config
        except OSError as exc:
            return _emit_error(
                {
                    "error": (
                        f"agent-index: SSH transport to indexer '{indexer.get('ssh')}' "
                        f"failed: {exc}"
                    ),
                    "hits": [],
                }
            )
        return proc.returncode

    if root is None and config.resolve_role() == "client":
        return _emit_error(
            {
                "error": (
                    "agent-index: no indexer transport resolvable here. This machine is a "
                    "client, so read commands run on the designated indexer over SSH — but "
                    "the current directory is not inside an adopted repo. Run inside a repo "
                    "with .agent-index/config.yaml indexer.ssh, or set AGENT_INDEX_REPO."
                ),
                "hits": [],
            }
        )
    return None
