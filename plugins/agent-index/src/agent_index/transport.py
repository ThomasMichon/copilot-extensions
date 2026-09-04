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
   A project without an explicit indexer designation is ``unconfigured``. Only
   when no project is resolvable at all (e.g. the delegated command running in
   the host's home directory over SSH) does routing fall back to the explicit
   machine-global ``resolve_role()``.
3. **Route:** ``host`` → run locally (return ``None`` so the caller dispatches the
   normal local command). ``client`` → run the *same* ``agent-index <argv>`` on
   the project's indexer over SSH, forwarding stdout. A client with **no**
   resolvable project/indexer is refused with a clear message (the transport is
   project-specific — there is no single global default).
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import sys
from typing import Any

from . import config

# Read subcommands that route through the transport.
DELEGABLE = ("search", "similar", "clusters", "status")

# Default remote shell when a project's ``indexer.shell`` is unset. A Windows
# indexer uses pwsh; a Linux indexer must declare ``shell: bash``.
DEFAULT_SHELL = "pwsh"

# ``ssh`` exit code for a connection-level failure (host down, DNS/route error,
# auth refused) -- distinct from any exit code the remote command itself
# returns. The SSH failover across ordered indexers treats *only* this code (and
# an ``OSError`` launching ssh) as "try the next indexer"; a genuine remote
# command failure is authoritative and is returned as-is (mirrors the HTTP
# failover, which only falls over on an unreachable ``/health`` probe).
SSH_TRANSPORT_RC = 255

# Default per-hop TCP connect timeout (seconds) applied to every delegated ssh
# invocation. Bounds how long an unreachable indexer stalls before failover
# moves to the next; overridable via ``AGENT_INDEX_SSH_CONNECT_TIMEOUT_S``.
DEFAULT_SSH_CONNECT_TIMEOUT_S = 8
MAX_FORWARDED_CONFIG_BYTES = 4096


def _ssh_connect_timeout() -> int:
    """Per-hop ssh ``ConnectTimeout`` (seconds), from
    ``AGENT_INDEX_SSH_CONNECT_TIMEOUT_S``. Defensively falls back to the default
    on a missing/malformed/non-positive value so a stray env setting never
    breaks routing."""
    raw = os.environ.get("AGENT_INDEX_SSH_CONNECT_TIMEOUT_S")
    if not raw:
        return DEFAULT_SSH_CONNECT_TIMEOUT_S
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_SSH_CONNECT_TIMEOUT_S
    return val if val > 0 else DEFAULT_SSH_CONNECT_TIMEOUT_S


def plan_route() -> tuple[str, dict | None]:
    """Return ``(role, indexer)`` for the current directory.

    ``role`` is ``"host"``, ``"client"``, or ``"unconfigured"``; ``indexer`` is
    the resolved project's ``indexer:`` mapping, or ``None`` when no designation
    is resolvable.
    """
    root = config.repo_root()
    indexers = config.read_indexers(root)
    indexer = indexers[0] if indexers else None
    if indexers:
        me = config.machine_id().strip().lower()
        role = (
            "host"
            if any(str(item.get("machine", "")).strip().lower() == me for item in indexers)
            else "client"
        )
    elif root is not None:
        role = config.UNCONFIGURED_ROLE
    else:
        role = config.resolve_role()
    return role, indexer


def has_usable_client_transport() -> bool:
    """Whether the current repo explicitly configures SSH or HTTP routing."""
    if config.configured_endpoints():
        return True
    root = config.repo_root()
    return any(
        str(item.get("ssh") or item.get("endpoint") or "").strip()
        for item in config.read_indexers(root)
    )


def _q_pwsh(arg: str) -> str:
    """Single-quote *arg* for a pwsh command line ('' escapes a quote)."""
    return "'" + arg.replace("'", "''") + "'"


def _pwsh_remote(inner: str) -> str:
    """Wrap *inner* as a remote pwsh ``-EncodedCommand`` invocation.

    Uses base64 of UTF-16LE (mirrors agent-worktrees' ``_pwsh_remote``): a plain
    ``-Command '<inner>'`` is mangled when the remote sshd default shell is
    cmd.exe (a common Windows sshd configuration), which echoes the single-quoted
    text instead of running it. ``-EncodedCommand`` carries no shell-special
    characters, so it executes correctly regardless of the remote default shell.
    """
    enc = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
    return f"pwsh -NoProfile -WindowStyle Hidden -EncodedCommand {enc}"


def _forwarded_routing(indexer: dict) -> str:
    machine = str(indexer.get("machine") or "").strip()
    if not machine or len(machine) > 256 or any(
        ord(character) < 32 for character in machine
    ):
        raise ValueError(
            "agent-index SSH routing requires a safe indexer machine identity"
        )
    raw = json.dumps(
        {"indexers": [{"machine": machine}]},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(raw) > MAX_FORWARDED_CONFIG_BYTES:
        raise ValueError("agent-index forwarded routing configuration is too large")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _build_inner(
    shell: str, argv: list[str], indexer: dict | None = None
) -> str:
    """Build the remote command that runs ``agent-index <argv>`` on the host.

    Invokes the host's installed binstub by absolute path (resolvable over a
    non-interactive SSH logon, where ``~/.local/bin`` may not be on PATH).
    """
    indexer = dict(indexer or {"machine": "indexer"})
    routing = _forwarded_routing(indexer)
    machine = str(indexer["machine"])
    if shell == "bash":
        quoted = " ".join(shlex.quote(a) for a in argv)
        return (
            f"AGENT_INDEX_CONFIG_DATA_B64={shlex.quote(routing)} "
            f"AGENT_INDEX_MACHINE={shlex.quote(machine)} "
            f'"$HOME/.local/bin/agent-index" {quoted}'
        )
    # pwsh (default)
    quoted = " ".join(_q_pwsh(a) for a in argv)
    return (
        f"$env:AGENT_INDEX_CONFIG_DATA_B64={_q_pwsh(routing)}; "
        f"$env:AGENT_INDEX_MACHINE={_q_pwsh(machine)}; "
        f'& "$env:USERPROFILE\\.local\\bin\\agent-index.ps1" {quoted}'
    )


def build_ssh_argv(indexer: dict, argv: list[str]) -> list[str]:
    """Build the local ``ssh`` argv that runs ``agent-index <argv>`` on the host.

    Every hop carries ``BatchMode=yes`` (never block on an interactive auth
    prompt -- delegation runs unattended over key/agent auth) and a bounded
    ``ConnectTimeout`` so an unreachable indexer fails fast (exit 255) and the
    ordered failover moves on to the next indexer instead of stalling.
    """
    alias = str(indexer["ssh"]).strip()
    shell = str(indexer.get("shell") or DEFAULT_SHELL).strip().lower()
    inner = _build_inner(shell, argv, indexer)
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={_ssh_connect_timeout()}"]
    if shell == "bash":
        escaped = inner.replace("'", "'\\''")
        return ["ssh", *opts, alias, f"bash -lc '{escaped}'"]
    return ["ssh", *opts, alias, _pwsh_remote(inner)]


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
    over SSH to the project's designated indexer(s) and returns the remote exit
    code.

    Decision:

    * A positively-resolved indexer designation for the current project routes.
      This machine runs **locally** (``None``) when it is *any* of the designated
      indexers (``machine_id() == indexers[i].machine``) -- a primary or a
      secondary host serves from its own store, and this also terminates the SSH
      recursion at the host. Otherwise this is a **client**: delegate
      ``agent-index <argv>`` across the ordered ``indexers`` (primary first),
      trying the next only when a hop fails at the **SSH connection level**
      (exit 255 or an ``OSError`` launching ssh). A genuine remote command exit
      is authoritative and returned as-is -- a bad query must not silently retry
      on another indexer.
    * A repository without a usable indexer designation is unconfigured and
      read commands fail without probing or starting a local service.
    * A truly bare invocation falls back to explicit machine-global role
      configuration so a delegated command can terminate on its host.
    """
    if sub not in DELEGABLE:
        return None
    root = config.repo_root()
    indexers = config.read_indexers(root)

    if indexers:
        me = config.machine_id().strip().lower()
        if any(str(ix.get("machine", "")).strip().lower() == me for ix in indexers):
            return None  # this machine is a designated indexer -> run local
        repo_has_endpoints = any(
            str(ix.get("endpoint") or "").strip() for ix in indexers
        )
        if repo_has_endpoints and config.configured_endpoints():
            # A client-local endpoint (commonly an SSH forward on a
            # machine-specific port) overrides this repo's shared endpoint, but
            # never captures another repo's SSH-only designation.
            return None
        candidates = [ix for ix in indexers if str(ix.get("ssh") or "").strip()]
        if candidates:
            return _delegate_over_ssh(sub, raw_argv, candidates)
        if any(str(ix.get("endpoint") or "").strip() for ix in indexers):
            # Explicit HTTP routing is handled by client_url() in the local
            # command handler; no SSH hop is required.
            return None
        # designation without any usable transport -> report it as incomplete

    if root is not None:
        return _emit_error(
            {
                "error": (
                    "agent-index has no usable indexer transport for the current "
                    "repository. Run `agent-index setup` with an SSH alias or "
                    "endpoint before using the retrieval commands."
                ),
                "hits": [],
            }
        )

    role = config.resolve_role()
    if role != "host":
        if role == config.UNCONFIGURED_ROLE:
            message = (
                "agent-index is not configured on this machine. Run inside a "
                "repository with .agent-index/config.yaml, or run `agent-index "
                "setup` explicitly."
            )
        else:
            message = (
                "agent-index: no indexer transport resolvable here. This machine is a "
                "client, so read commands run on the designated indexer over SSH — but "
                "the current directory is not inside an adopted repo. Run inside a repo "
                "with .agent-index/config.yaml indexer.ssh, or set AGENT_INDEX_REPO."
            )
        return _emit_error(
            {
                "error": message,
                "hits": [],
            }
        )
    return None


def _delegate_over_ssh(sub: str, raw_argv: list[str], candidates: list[dict]) -> int:
    """Run the read subcommand on the first reachable indexer in *candidates*.

    Iterates the ordered indexers (primary first); a hop that fails at the SSH
    connection level (exit 255, or an ``OSError`` launching ssh) is recorded and
    skipped so a down primary or broken SSH hop transparently falls back to a
    secondary. The 255-as-transport-failure signal applies only when a fallback
    remains: with a single candidate its exit code is returned verbatim (a lone
    indexer's 255 is its authoritative result, preserving single-indexer
    back-compat). When every candidate fails to connect, one aggregated error is
    emitted."""
    fwd = _forward_argv(sub, raw_argv)
    multi = len(candidates) > 1
    failures: list[str] = []
    for ix in candidates:
        argv = build_ssh_argv(ix, fwd)
        try:
            proc = subprocess.run(argv, check=False)  # noqa: S603 - argv built from config
        except OSError as exc:
            failures.append(f"{ix.get('ssh')}: {exc}")
            continue
        if proc.returncode == SSH_TRANSPORT_RC and multi:
            failures.append(f"{ix.get('ssh')}: ssh connection failed (exit 255)")
            continue
        return proc.returncode
    return _emit_error(
        {
            "error": (
                "agent-index: SSH transport failed for every designated indexer "
                f"({'; '.join(failures)})."
            ),
            "hits": [],
        }
    )
