#!/usr/bin/env python3
"""Multi-machine SSH data source for the Worktree Picker TUI.

Drop-in replacement for ``data_local`` exposing the same surface the engine
needs (``LOCAL`` / ``LOCAL_LABEL`` / ``machines()`` / ``bucket`` /
``for_machine`` / ``load()``) plus ``make_loader()`` for the engine's live
mode. The roster comes from the canonical ``machines.yaml`` registry (via
``config.load_machines_yaml``), so display names, env labels, SSH aliases and
shells never drift from config.

A :class:`LiveLoader` runs ``<project> list --json --classify --mux-details``
on a background daemon thread per machine: the local machine in-process (reusing
``data_local.load``, no subprocess), every reachable remote over its multi-machine system
SSH alias. The picker shows the connect spinner while a machine loads and
resolves it to ``ready`` (data) or ``failed`` (unreachable / errored).

This module only *reads* worktree listings -- it never creates, opens, cleans,
or syncs anything.

Graceful degradation: a remote running an agent-worktrees older than dev59 does
not recognize ``--classify``. When the list command fails with an
"unrecognized arguments" error mentioning ``--classify``, the loader retries
without it, so older remotes still load (their rows just lack canonical state).
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import logging
import os
import shlex
import signal
import socket
import subprocess
import threading

from agent_procutil import no_window_flags

from .. import config as cfg
from . import data_local, derive, provider_sources, roster, source_identity

# Shared display surface so the engine treats this exactly like ``data_local``.
# ``LOCAL`` is resolved from the actual local source below (so it carries the
# machine's ``machines.yaml`` display name, matching the tab descriptors) with
# ``data_local.LOCAL`` as the fallback when the registry is unavailable.
LOCAL_LABEL = data_local.LOCAL_LABEL
bucket = derive.bucket
for_machine = derive.for_machine
for_source = derive.for_source
# Profiles-matrix axes are config-bound from machines.yaml (same roster).
host_cols = roster.host_cols
target_envs = roster.target_envs
# Repo name + default branch for the top bar -- project config, not hardcoded
# (shared with data_local; both resolve the same active-project config).
REPO = data_local.REPO
BRANCH = data_local.BRANCH

# machines.yaml environment name -> the picker's short env label (and C_ENV key).
_ENV_LABEL = {"windows": "Win", "wsl": "WSL", "linux": "Linux"}

# Base list args shared by every source. ``--include-other-platforms`` is added
# for Windows targets so a Windows machine's WSL worktrees come back too.
_LIST_ARGS = "list --json --classify --mux-details"
_LIST_ARGS_WIN = _LIST_ARGS + " --include-other-platforms"
SOURCE_KIND_MACHINE_SSH = source_identity.MACHINE_SSH_KIND
SOURCE_KIND_PROVIDER_EXEC = source_identity.PROVIDER_EXEC_KIND
machine_source_id = source_identity.machine_ssh_id
_LOG = logging.getLogger(__name__)


class Source:
    """One machine/environment the picker loads worktrees from.

    ``machine``/``env`` are the display labels (``machines.yaml`` display name +
    short env label) and must match this module's ``machines()`` descriptors so
    the engine's per-tab filtering and "this host" detection line up. Existing
    local and remote machine sources share the ``machine-ssh`` kind; ``local``
    records whether enumeration runs in-process rather than over SSH.
    """

    def __init__(self, machine, env, argv, *, local=False, ready=True,
                 use_classify=True, timeout=20, alias="", shell="bash",
                 source_kind=SOURCE_KIND_MACHINE_SSH, source_id=None,
                 source_label=None, provider="", target_id="", instance_id="",
                 venue=None, capabilities=None, resolve_argv=None,
                 connect_argv=None):
        self.machine = machine        # display_name from machines.yaml
        self.env = env                # Win | WSL | Linux
        self.argv = argv              # subprocess argv (None for the local src)
        self.local = local
        self.ready = ready
        self.use_classify = use_classify
        self.timeout = timeout
        self.alias = alias            # SSH alias (remote sources only)
        self.shell = shell            # pwsh | bash (for remote command wrapping)
        self.source_kind = source_kind
        self.source_id = source_identity.resolve_id(
            source_kind, source_id, machine=machine, env=env
        )
        self.source_label = source_label or f"{machine} / {env}"
        self.provider = provider
        self.target_id = target_id
        self.instance_id = instance_id
        self.venue = dict(venue or {})
        self.capabilities = dict(capabilities or {})
        self.resolve_argv = list(resolve_argv or [])
        self.connect_argv = list(connect_argv or [])

    @property
    def key(self):
        """Legacy machine/environment key used by Picker-facing APIs."""
        return (self.machine, self.env)

    @property
    def cache_key(self):
        """Canonical source identity used for loader-owned state."""
        return self.source_id


def _unique_sources(sources):
    """Keep the first source for each canonical ID without crashing the Picker."""
    unique = []
    seen = set()
    for source in sources:
        if source.source_id in seen:
            _LOG.warning("ignoring duplicate Picker source id %s", source.source_id)
            continue
        seen.add(source.source_id)
        unique.append(source)
    return unique


def _local_identity() -> tuple[str, str]:
    """(hostname-key, platform-name) for the machine this picker runs on."""
    return socket.gethostname().split(".")[0].lower(), cfg.detect_platform()


def _project() -> str:
    try:
        return cfg.project_name()
    except (RuntimeError, ValueError):
        return "agent-worktrees"


def _list_args(shell: str, *, classify: bool, reconcile: bool = False) -> str:
    win = shell == "pwsh"
    args = _LIST_ARGS_WIN if win else _LIST_ARGS
    if not classify:
        args = args.replace(" --classify", "")
    if reconcile:
        # Reconcile this machine's own PR states against the provider during the
        # list (and persist the correction), so a remote tab stops showing a
        # merged-elsewhere worktree as having an open PR (#2102).
        args = args + " --reconcile-prs"
    return args


def _pwsh_remote(cmd: str) -> str:
    """Remote pwsh invocation that survives the remote sshd's default shell.

    Uses ``-EncodedCommand`` (base64 of UTF-16LE) instead of ``-Command '<cmd>'``.
    A plain single-quoted ``-Command`` is mangled when the remote sshd's default
    shell is **cmd.exe** (e.g. a dtssh Windows host, where DefaultShell is unset):
    cmd.exe passes the quotes through and pwsh evaluates the single-quoted text as
    a *string literal* -- echoing it instead of running it, so the picker gets a
    non-JSON line back and the machine shows as failed. EncodedCommand carries no
    shell-special characters, so it executes correctly regardless of the remote
    default shell (cmd.exe or pwsh).

    ``-WindowStyle Hidden`` matches agent-bridge's ``_build_remote_cmd`` and hides
    *this* pwsh's own console. NOTE (dotfiles#403): on the dtssh Windows hosts this
    flag is **parity only -- it does NOT stop the headed-window storm.** Those hosts
    run ``sshd`` as the user in the *interactive* session (session 1), so the sshd
    **exec child** -- the ``DefaultShell`` itself, i.e. the ``cmd.exe /c "..."`` or
    ``pwsh -c "..."`` wrapper that runs *this* command string -- AllocConsoles a
    *visible* console on the desktop. Verified live: with either DefaultShell, and
    even wrapping in ``conhost --headless``, non-PTY inbound execs still pop a
    window; only a PTY avoids it (but a PTY corrupts the JSON via ConPTY
    line-wrap + VT noise, so the picker can't use one). The window owner is the
    *outer* exec child, which no flag on this inner pwsh can hide. A real quash is
    a dtssh/host-level change (a hidden-console DefaultShell launcher, a hidden
    desktop for the sshd, etc.), tracked in the agent-ssh effort -- not here. This
    flag stays for consistency with agent-bridge and is harmless."""
    enc = base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
    return f"pwsh -NoProfile -WindowStyle Hidden -EncodedCommand {enc}"


def _drop_classify_arg(argv: list[str]) -> list[str]:
    """Return *argv* with ``--classify`` removed from the remote command.

    The classify-unsupported retry (see :func:`_fetch`) must strip ``--classify``
    from an already-built remote argv. That is a plain substring in the
    ``bash -lc '<cmd>'`` form, but for the Windows ``pwsh -NoProfile
    -EncodedCommand <base64>`` form the flag lives *inside* the base64 blob, so a
    naive ``str.replace`` is a no-op -- the retry would resend the identical
    command and fail again on an older remote. For the encoded form we decode,
    drop the flag, and re-encode so the fallback actually takes effect."""
    marker = "-EncodedCommand "
    out: list[str] = []
    for a in argv:
        if marker in a:
            head, b64 = a.rsplit(marker, 1)
            try:
                decoded = base64.b64decode(b64).decode("utf-16-le")
                decoded = decoded.replace(" --classify", "")
                b64 = base64.b64encode(
                    decoded.encode("utf-16-le")).decode("ascii")
                a = head + marker + b64
            except Exception:
                a = a.replace(" --classify", "")
        else:
            a = a.replace(" --classify", "")
        out.append(a)
    return out


# picker-cache-first-paint (dotfiles#948): the remote fast phase can request a
# **cache-only** listing (``list --json --cache-only``) that builds rows from the
# remote's tracking-record cache with no events.jsonl / process / git scan --
# symmetric with the local cache-only first paint. Opt-in per box (default off)
# during fleet rollout, since a remote whose runtime predates ``--cache-only``
# would reject it; when enabled the fetch falls back gracefully (drops the flag)
# for such a remote. Flip on once a box's remotes all carry the flag.
_CACHE_ONLY_GATE = os.environ.get("AGENT_WORKTREES_PICKER_CACHE_ONLY") == "1"


def _edit_remote_cmd(argv: list[str], transform) -> list[str]:
    """Apply ``transform`` to the remote command string in *argv*, encoding-aware.

    Mirrors :func:`_drop_classify_arg`'s handling of both the plain
    ``bash -lc '<cmd>'`` form and the Windows ``pwsh -EncodedCommand <base64>``
    form (where the command lives inside a UTF-16LE base64 blob), so a flag can be
    added/removed regardless of the remote shell."""
    marker = "-EncodedCommand "
    out: list[str] = []
    for a in argv:
        if marker in a:
            head, b64 = a.rsplit(marker, 1)
            try:
                decoded = base64.b64decode(b64).decode("utf-16-le")
                decoded = transform(decoded)
                b64 = base64.b64encode(
                    decoded.encode("utf-16-le")).decode("ascii")
                a = head + marker + b64
            except Exception:
                a = transform(a)
        else:
            a = transform(a)
        out.append(a)
    return out


def _add_cache_only_arg(argv: list[str]) -> list[str]:
    """Add ``--cache-only`` to a remote list argv (idempotent). Inserted after
    ``--json`` (always present in the picker's list command)."""
    def _t(s: str) -> str:
        if "--cache-only" in s:
            return s
        return s.replace(" --json", " --json --cache-only", 1)
    return _edit_remote_cmd(argv, _t)


def _drop_cache_only_arg(argv: list[str]) -> list[str]:
    """Remove ``--cache-only`` from a remote list argv (the old-remote fallback)."""
    return _edit_remote_cmd(argv, lambda s: s.replace(" --cache-only", ""))


def _is_cache_only_unsupported(stderr: str) -> bool:
    s = (stderr or "").lower()
    return "unrecognized arguments" in s and "--cache-only" in s


# Every picker load fans out to each ready machine over SSH via a dtssh
# ProxyCommand. Against an offline/unreachable peer, a bare ``ssh`` blocks
# indefinitely in ProxyCommand setup; if the picker is then gone, the child is an
# immortal orphan (own process group, no window, nobody to reap it) --
# dotfiles#1702. Harden every remote argv so the ssh child self-terminates at the
# OS level, independent of the picker being alive:
#   * BatchMode=yes         -- never block on a credential/host-key prompt.
#   * ConnectTimeout        -- bound the connect (modern OpenSSH honors this
#                              through a ProxyCommand), so a dead peer errors in
#                              seconds instead of hanging forever.
#   * ServerAlive*          -- bound a session that wedges after the handshake.
_SSH_HARDENING = (
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=8",
    "-o", "ServerAliveCountMax=3",
)


def _ssh_argv(alias: str, remote: str) -> list[str]:
    """``ssh`` argv with hardening options so an unreachable host fails fast.

    Options go between ``ssh`` and the alias, so ``argv[0] == "ssh"`` and the
    remote command stays ``argv[-1]`` (the encoding-aware editors and every
    caller that reads the last element are unaffected).
    """
    return ["ssh", *_SSH_HARDENING, alias, remote]


def _argv_for(shell: str, alias: str, project: str, *, classify: bool,
              reconcile: bool = False):
    """Remote list argv for a machine/env: pwsh on Windows, bash elsewhere."""
    cmd = f"{project} {_list_args(shell, classify=classify, reconcile=reconcile)}"
    if shell == "pwsh":
        return _ssh_argv(alias, _pwsh_remote(cmd))
    return _ssh_argv(alias, f"bash -lc '{cmd}'")


def _build_sources():
    """Derive machine/env sources from ``machines.yaml`` (the canonical roster).

    Skips ``copilot: false`` machines entirely. The local machine's matching env
    always becomes the in-process local source -- **it never needs an SSH
    profile of its own** (the picker runs there): even a machine with no SSH
    environment, or one whose ``ssh.ready`` is false, still gets a working local
    tab. Every *other* env is contacted over SSH only when it actually has an
    SSH profile (a non-empty alias) and the machine is ``ssh.ready``; an env
    with no alias is rendered as a disabled tab and never connected to, and a
    ``ssh.ready: false`` machine's remote envs stay disabled tabs.
    """
    config = cfg.load_config()
    repo = config.default_repo
    try:
        entries = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        entries = {}

    project = _project()
    local_key, local_plat = _local_identity()
    local_elabel = _ENV_LABEL.get(local_plat, local_plat.title() or "?")
    config_machine = (config.machine or "").lower()

    out: list[Source] = []
    for key, m in entries.items():
        if not m.copilot:
            continue
        is_local_machine = (
            key.lower() == local_key
            or (getattr(m, "hostname", "") or "").lower() == local_key
            or key.lower() == config_machine
            or (m.alias and m.alias.lower() == config_machine)
        )
        local_env_added = False
        for ssh_env in m.ssh_environments:
            ename = (ssh_env.name or "").lower()
            elabel = _ENV_LABEL.get(ename, ename.title() or "?")
            shell = ssh_env.shell or ("pwsh" if ename == "windows" else "bash")
            alias = ssh_env.alias or ""
            is_local = is_local_machine and ename == local_plat
            if is_local:
                # Local env: in-process, no SSH profile required.
                out.append(Source(m.key, elabel, None, local=True,
                                  ready=True))
                local_env_added = True
            elif not alias:
                # No SSH profile for this env -- never try to connect to it;
                # surface it as a disabled tab.
                out.append(Source(m.key, elabel, None, ready=False,
                                  alias="", shell=shell))
            elif m.ssh_ready:
                argv = _argv_for(shell, alias, project, classify=True)
                out.append(Source(m.key, elabel, argv, ready=True,
                                  alias=alias, shell=shell))
            else:
                out.append(Source(m.key, elabel, None, ready=False,
                                  alias=alias, shell=shell))
        # Bypass: the current machine always gets a local source, even when it
        # has no SSH environment of its own in machines.yaml (or none matched
        # the running platform). The picker runs *here*, so it never needs to
        # SSH to itself.
        if is_local_machine and not local_env_added:
            out.append(Source(m.key, local_elabel, None, local=True,
                              ready=True))

    # Defensive fail-safe: guarantee a local source even when this machine is
    # entirely absent from machines.yaml (a freshly-provisioned box whose
    # self-entry hasn't reached the anchor yet, or a stale anchor checkout).
    # Without a local source the engine has no "this host" tab and the picker
    # crashes. The picker runs *here*, so a hostname-based local tab (from
    # ``data_local.LOCAL``) is always the correct, safe fallback.
    if not any(s.local for s in out):
        out.append(Source(data_local.LOCAL[0], data_local.LOCAL[1], None,
                          local=True, ready=True))
    for registered in provider_sources.load(project):
        ready = bool(
            registered.venue.get("ready")
            and registered.venue.get("posture_verified")
        )
        source = Source(
            "", "", None,
            ready=ready,
            alias=registered.alias,
            shell=registered.shell,
            source_kind=registered.kind,
            source_id=registered.source_id,
            source_label=registered.label,
            provider=registered.provider,
            target_id=registered.target_id,
            instance_id=registered.instance_id,
            venue=registered.venue,
            capabilities=registered.capabilities,
            resolve_argv=registered.resolve_argv,
            connect_argv=registered.connect_argv,
        )
        if ready:
            source.argv = _provider_remote_argv(
                source,
                f"{project} {_list_args(source.shell, classify=True)}",
                expected_instance_id=source.instance_id,
                expected_assignment=source.venue.get("assignment"),
            )
        out.append(source)
    return _unique_sources(out)


def _wrap_remote(shell: str, alias: str, inner: str):
    """SSH argv that runs *inner* under the right login shell on *alias*."""
    if shell == "pwsh":
        return _ssh_argv(alias, _pwsh_remote(inner))
    return _ssh_argv(alias, f"bash -lc {shlex.quote(inner)}")


def _remote_arg(shell: str, value: object) -> str:
    """Quote one argument for the remote login shell."""
    text = str(value)
    if shell == "pwsh":
        return "'" + text.replace("'", "''") + "'"
    return shlex.quote(text)


def _provider_remote_argv(
    source: Source,
    inner: str,
    *,
    expected_instance_id: str,
    expected_assignment: dict,
):
    """Build SSH argv whose ProxyCommand verifies the displayed provider row."""
    if not source.connect_argv:
        raise ValueError("provider source omitted connection command")
    assignment_json = json.dumps(
        expected_assignment,
        sort_keys=True,
        separators=(",", ":"),
    )
    connect_argv = [
        *source.connect_argv,
        "--expected-target-id",
        source.target_id,
        "--expected-instance-id",
        expected_instance_id,
        "--expected-assignment",
        assignment_json,
    ]
    proxy_command = (
        subprocess.list2cmdline(connect_argv)
        if os.name == "nt"
        else shlex.join(connect_argv)
    ).replace("%", "%%")
    remote = (
        _pwsh_remote(inner)
        if source.shell == "pwsh"
        else f"bash -lc {shlex.quote(inner)}"
    )
    return [
        "ssh",
        *_SSH_HARDENING,
        "-o",
        f"ProxyCommand={proxy_command}",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"UserKnownHostsFile={'NUL' if os.name == 'nt' else '/dev/null'}",
        "-o",
        "GlobalKnownHostsFile=none",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        "-o",
        "ControlPersist=no",
        "-o",
        "PubkeyAuthentication=no",
        "-o",
        "PasswordAuthentication=no",
        source.alias,
        remote,
    ]


def _provider_transport_fingerprint(source) -> str:
    payload = {
        "alias": source.alias,
        "shell": source.shell,
        "resolve": list(source.resolve_argv),
        "connect": list(source.connect_argv),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stream_argv(source):
    """SSH argv that runs the remote ``list`` in NDJSON **streaming** mode.

    Same classify + mux + platform list flags as :func:`_argv_for` plus
    ``--stream``: the producer emits a ``begin`` frame, fast (unclassified)
    rows, then classified rows, then a ``done`` frame -- one JSON object per
    flushed line -- so the whole two-phase load happens over a single SSH
    connection with progressive fill."""
    project = _project()
    cmd = f"{project} {_list_args(source.shell, classify=True)} --stream"
    return _wrap_remote(source.shell, source.alias, cmd)


def _parse_ndjson_line(raw: str):
    """Parse one NDJSON line to a dict, tolerating banner noise / blank lines.

    Login shells / pwsh can emit a line of noise before the JSON, so locate the
    first ``{`` and decode from there. Returns ``None`` when the line carries no
    JSON object (blank, banner, partial)."""
    if not raw:
        return None
    s = raw.strip()
    i = s.find("{")
    if i < 0:
        return None
    try:
        return json.loads(s[i:])
    except Exception:
        return None


def _is_stream_unsupported(stderr: str) -> bool:
    """True when a remote's argparse rejected ``--stream`` (older
    agent-worktrees), so the caller falls back to the non-streaming path."""
    s = (stderr or "").lower()
    return "unrecognized arguments" in s and "--stream" in s


def _stream_enabled() -> bool:
    """Whether the Picker attempts single-connection NDJSON streaming before the
    two-phase load.

    Off by default during the fleet-rollout window: a remote that lacks the
    ``--stream`` producer costs a wasted connect + argparse probe (~seconds)
    before the two-phase fallback, which would regress first paint. So streaming
    is **opt-in** until the producer is deployed mesh-wide -- enable with
    ``AGENT_WORKTREES_PICKER_STREAM=1`` (any of 1/true/yes/on). Once every
    machine's runtime carries the producer, this can flip to on by default."""
    return os.environ.get(
        "AGENT_WORKTREES_PICKER_STREAM", "").strip().lower() in (
        "1", "true", "yes", "on")


def _find_source(machine=None, env=None, *, source_id=None):
    for source in _build_sources():
        if source_id is not None:
            if source.source_id == source_id:
                return source
        elif (machine or env) and source.machine == machine and source.env == env:
            return source
    return None


def remote_op_argv(machine, env, op, worktree_id, *, source_id=None,
                   include_unused=False, include_conversations=False,
                   force=False):
    """Build the SSH argv to run one maintenance op on a remote machine/env.

    ``op`` is ``"cleanup"``, ``"sync"``, ``"restart"``, ``"reclaim"``,
    ``"remux"``, or ``"finalize"``.
    Returns the ssh argv, or ``None`` for the local host or an unknown /
    not-ready target (the caller runs local ops in-process). The remote runs
    the project binstub's JSON per-worktree CLI.
    """
    project = _project()
    s = _find_source(machine, env, source_id=source_id)
    if s is not None:
        if s.source_kind != SOURCE_KIND_MACHINE_SSH:
            return None
        if (
            s.local
            or not s.ready
            or not s.alias
            or not s.capabilities.get(op, s.source_kind == SOURCE_KIND_MACHINE_SSH)
        ):
            return None
        if op == "cleanup":
            flags = " --clean --json"
            if force:
                flags += " --force"
            if include_unused:
                flags += " --include-unused"
            if include_conversations:
                flags += " --include-conversations"
            inner = (
                f"{project} cleanup --worktree-id "
                f"{_remote_arg(s.shell, worktree_id)}{flags}"
            )
        elif op == "restart":
                # ``restart`` takes the worktree id positionally (not
                # --worktree-id); the remote graceful double-Ctrl-C / mux
                # kill-session runs there and reports a single JSON object.
            inner = (
                f"{project} restart {_remote_arg(s.shell, worktree_id)} --json"
            )
        elif op == "reclaim":
                # ``reclaim`` reaps the bound Copilot process(es) for the
                # worktree's session (bare orphans only), reported as JSON.
            inner = (
                f"{project} reclaim --worktree-id "
                f"{_remote_arg(s.shell, worktree_id)} "
                f"--bare-only --yes --json"
            )
        elif op == "remux":
            inner = (
                f"{project} remux --worktree-id "
                f"{_remote_arg(s.shell, worktree_id)} --yes --json"
            )
        elif op == "finalize":
            inner = (
                f"{project} finalize --worktree-id "
                f"{_remote_arg(s.shell, worktree_id)} --json"
            )
        else:  # sync
            inner = (
                f"{project} sync --worktree-id "
                f"{_remote_arg(s.shell, worktree_id)} --json"
            )
        return _wrap_remote(s.shell, s.alias, inner)
    return None


def recent_messages_argv(
    machine,
    env,
    worktree_id,
    *,
    source_id=None,
    expected_instance_id=None,
    expected_assignment=None,
    expected_transport_fingerprint=None,
    limit=3,
):
    """Build the SSH argv to fetch a remote worktree's recent session messages.

    Runs ``<project> recent-messages --worktree <id> --limit N --json`` on the
    remote host. Returns the ssh argv, or ``None`` for the local host or an
    unknown / not-ready target (the caller loads local worktrees in-process).
    """
    project = _project()
    s = _find_source(machine, env, source_id=source_id)
    if s is not None:
        if s.source_kind == SOURCE_KIND_PROVIDER_EXEC:
            if (
                not expected_transport_fingerprint
                or _provider_transport_fingerprint(s)
                != expected_transport_fingerprint
            ):
                return None
            try:
                _resolve_provider_source(s, _run)
            except (OSError, RuntimeError, ValueError):
                return None
            if (
                not expected_instance_id
                or s.instance_id != expected_instance_id
                or not isinstance(expected_assignment, dict)
                or s.venue.get("assignment") != expected_assignment
            ):
                return None
        if (
            s.local
            or not s.ready
            or not s.alias
            or not s.capabilities.get(
                "messages", s.source_kind == SOURCE_KIND_MACHINE_SSH
            )
        ):
            return None
        inner = (
            f"{project} recent-messages --worktree "
            f"{_remote_arg(s.shell, worktree_id)} "
            f"--limit {int(limit)} --json"
        )
        if s.source_kind == SOURCE_KIND_PROVIDER_EXEC:
            return _provider_remote_argv(
                s,
                inner,
                expected_instance_id=expected_instance_id,
                expected_assignment=expected_assignment,
            )
        return _wrap_remote(s.shell, s.alias, inner)
    return None


def list_sessions_argv(
    machine,
    env,
    worktree_id,
    *,
    source_id=None,
    expected_instance_id=None,
    expected_assignment=None,
    expected_transport_fingerprint=None,
):
    """Build the SSH argv to list a remote worktree's Copilot sessions.

    Runs ``<project> list-sessions --worktree <id> --json`` on the remote host
    (the enriched registry: each entry carries id, name/title, is_head, state).
    Returns the ssh argv, or ``None`` for the local host or an unknown /
    not-ready target (the caller loads local worktrees in-process).
    """
    project = _project()
    s = _find_source(machine, env, source_id=source_id)
    if s is not None:
        if s.source_kind == SOURCE_KIND_PROVIDER_EXEC:
            if (
                not expected_transport_fingerprint
                or _provider_transport_fingerprint(s)
                != expected_transport_fingerprint
            ):
                return None
            try:
                _resolve_provider_source(s, _run)
            except (OSError, RuntimeError, ValueError):
                return None
            if (
                not expected_instance_id
                or s.instance_id != expected_instance_id
                or not isinstance(expected_assignment, dict)
                or s.venue.get("assignment") != expected_assignment
            ):
                return None
        if (
            s.local
            or not s.ready
            or not s.alias
            or not s.capabilities.get(
                "sessions", s.source_kind == SOURCE_KIND_MACHINE_SSH
            )
        ):
            return None
        inner = (
            f"{project} list-sessions --worktree "
            f"{_remote_arg(s.shell, worktree_id)} --json"
        )
        if s.source_kind == SOURCE_KIND_PROVIDER_EXEC:
            return _provider_remote_argv(
                s,
                inner,
                expected_instance_id=expected_instance_id,
                expected_assignment=expected_assignment,
            )
        return _wrap_remote(s.shell, s.alias, inner)
    return None


def profiles_argv(machine, env, *, action, set_json=None, no_mirror=False):
    """SSH argv to run ``profiles get|apply`` on a remote host/env.

    Returns the ssh argv, or ``None`` for the local host or an unknown /
    not-ready target (the caller runs the local op in-process). ``set_json`` is
    the column payload for ``apply``.
    """
    project = _project()
    for s in _build_sources():
        if s.machine == machine and s.env == env:
            if s.source_kind != SOURCE_KIND_MACHINE_SSH:
                return None
            if s.local or not s.ready or not s.alias:
                return None
            if action == "get":
                inner = f"{project} profiles get --json"
            else:  # apply
                flags = " --no-mirror" if no_mirror else ""
                payload = (set_json or "[]").replace("'", "'\\''")
                inner = (f"{project} profiles apply --json{flags} "
                         f"--set '{payload}'")
            return _wrap_remote(s.shell, s.alias, inner)
    return None


def source_snapshot():
    """Capture one source-registry/roster view for a Picker setup pass."""
    return tuple(_build_sources())


def machines(sources=None):
    """Ordered machine-tab descriptors: (label, machine, env, reachable).

    ``reachable`` is true only for the local source (always) and for a remote
    env that both has an SSH profile (a non-empty alias) and belongs to an
    ``ssh.ready`` machine: those are attempted (spinner -> ✓/✗). An env with no
    SSH profile, or one on a ``ssh.ready: false`` machine, renders as a disabled
    tab and is never contacted.
    """
    return [
        (f"{s.machine} {s.env}", s.machine, s.env, s.ready)
        for s in (sources if sources is not None else _build_sources())
        if s.source_kind == SOURCE_KIND_MACHINE_SSH
    ]


def source_tabs(sources=None):
    """Ordered Picker tab descriptors for physical and provider sources."""
    return [
        {
            "label": (
                f"{source.machine} {source.env}"
                if source.source_kind == SOURCE_KIND_MACHINE_SSH
                else source.source_label
            ),
            "machine": source.machine,
            "env": source.env,
            "ready": source.ready,
            "source_kind": source.source_kind,
            "source_id": source.source_id,
            "source_label": source.source_label,
            "provider": source.provider,
            "target_id": source.target_id,
            "instance_id": source.instance_id,
            "venue": dict(source.venue),
            "capabilities": dict(source.capabilities),
        }
        for source in (sources if sources is not None else _build_sources())
    ]


def machine_key_map() -> dict[str, str]:
    """``display_name -> registry key`` from ``machines.yaml``.

    A machine's registry key is its canonical identity (lowercase; it doubles as
    the SSH-alias base) -- the value ``agent-worktrees get machine`` returns and
    that other multi-machine system tools (agent-dispatch, agent-bridge) match against. The
    picker's tab labels carry the *display* name, so a registered pivot that
    scopes its CLI ``{machine}`` needs this translation to hand over the identity,
    not the label. Best-effort: an unreadable roster yields ``{}`` (the caller
    then falls back to the display name). Uncached -- the engine caches the
    result for a session; keeping this pure keeps it trivially testable.
    """
    mapping: dict[str, str] = {}
    try:
        config = cfg.load_config()
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except (FileNotFoundError, ValueError, AttributeError):
        return mapping
    for key, m in entries.items():
        display = getattr(m, "display_name", None)
        if display:
            mapping[display] = key
    return mapping


def machine_key(display_name: str | None) -> str | None:
    """The registry key (canonical identity) for a machine's ``display_name``,
    or the display name itself when the roster can't resolve it."""
    if not display_name:
        return display_name
    return machine_key_map().get(display_name, display_name)


def load_profile_column(machine, env):
    """Read a host's terminal-profile column (local in-process / remote SSH)."""
    from . import profiles_io
    return profiles_io.load_column(machine, env)


def apply_profile_column(machine, env, sels, *, mirror=True):
    """Persist a host's terminal-profile column. Returns ``(ok, detail)``."""
    from . import profiles_io
    return profiles_io.apply_column(machine, env, sels, mirror=mirror)


def reconcile_prs() -> int:
    """Reconcile the LOCAL machine's stale PR states against the provider (#1423).

    Delegates to :func:`data_local.reconcile_prs`. Each *remote* machine's PRs are
    reconciled on their own owning machine by :meth:`LiveLoader.reconcile_remote_prs`
    (a bounded, after-first-paint ``list --reconcile-prs`` over SSH, #2102).
    """
    return data_local.reconcile_prs()


def reconcile_bound_live() -> int:
    """Reconcile the LOCAL machine's cached bound-Copilot liveness (#4057/#1416).

    Delegates to :func:`data_local.reconcile_bound_live`. The bound-Copilot scan
    is inherently machine-local (it reads this host's session-state), so -- like
    :func:`reconcile_prs` -- each remote machine reconciles its own worktrees when
    its picker runs; this only ever touches the local machine's records.
    """
    return data_local.reconcile_bound_live()


def _reconcile_argv(source: "Source"):
    """The remote list argv for *source* with PR reconcile enabled (#2102).

    Mirrors ``source.argv`` but adds ``--reconcile-prs`` so the remote reconciles
    (and persists) its own PR states while listing. Honors the source's current
    ``use_classify`` so a remote that already fell back off ``--classify`` isn't
    handed it again.
    """
    return _argv_for(source.shell, source.alias, _project(),
                     classify=source.use_classify, reconcile=True)


def _resolve_local() -> tuple[str, str]:
    """(machine, env) of this host, using the registry display name when known.

    Falls back to ``data_local.LOCAL`` (hostname-based) if the local machine is
    not represented in ``machines.yaml`` -- or if config context is not yet
    resolvable at import time (e.g. before ``main()`` establishes the active
    project, as when the test suite imports this module).
    """
    try:
        for s in _build_sources():
            if s.local:
                return s.key
    except Exception:
        # Import-time config I/O must never hard-crash the import. In a real
        # picker run this module is imported after main() has established the
        # active project, so the try succeeds; this guard only covers early /
        # context-free imports (e.g. the test suite) where a hostname-based
        # local is the correct, safe fallback.
        pass
    return data_local.LOCAL


LOCAL = _resolve_local()


def load(machine: str | None = None, env: str | None = None):
    """Synchronous local-only load (live mode streams via :class:`LiveLoader`).

    Provided so this source stays swap-compatible with ``data_local`` for the
    non-live code path; returns just this host's worktrees.
    """
    return data_local.load(LOCAL[0], LOCAL[1])


def make_loader(sources=None):
    """Build the background per-machine loader the engine drives in live mode."""
    return LiveLoader(sources if sources is not None else _build_sources())


def _extract_json(text: str):
    """Parse the first JSON object out of command output.

    Login shells / pwsh can emit banner noise before the JSON, so locate the
    first ``{`` and let the decoder consume just that object.
    """
    i = text.find("{")
    if i < 0:
        raise RuntimeError("no JSON in output")
    obj, _end = json.JSONDecoder().raw_decode(text[i:])
    return obj


def _is_classify_unsupported(stderr: str) -> bool:
    s = (stderr or "").lower()
    return "unrecognized arguments" in s and "--classify" in s


# On Windows, keep ssh children off the picker's console. A child ``ssh.exe``
# otherwise opens the shared CONIN$/CONOUT$ directly -- writing its errors over
# the TUI and, worse, calling SetConsoleMode which clears the
# ENABLE_VIRTUAL_TERMINAL_INPUT bit Textual set on that console. After that,
# arrow-key escape sequences decode as NUL (ctrl+@) and Up/Down stop working
# until the picker is relaunched, while single-byte keys ([, ], Tab, Enter,
# Esc) keep working. This bites hardest when the ssh *fails* (e.g. an
# unreachable remote), because the failing child mangles the console mode and
# never restores it. CREATE_NO_WINDOW gives the child its own (absent) console
# so it can't touch ours. stdin=DEVNULL additionally stops ssh from reading the
# operator's keystrokes.
_CREATE_NO_WINDOW = no_window_flags()


def _run(argv, timeout):
    kwargs = dict(
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        # DEVNULL gives ssh an empty stdin (instant EOF) so a background ssh
        # child can't read the operator's keystrokes out from under the TUI.
        stdin=subprocess.DEVNULL,
    )
    if os.name == "nt" and _CREATE_NO_WINDOW:
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.run(argv, **kwargs)


def _kill_proc_tree(proc):
    """Best-effort terminate a prefetch child *and* its process group.

    Killing the local ``ssh`` also drops the channel, so the remote
    ``agent-worktrees list`` it was driving dies with it -- which is the whole
    point: don't leave a heavy git-classification churning on the machine the
    operator is about to hand off into.
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()
        else:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass


# --- SSH resolution diagnostics --------------------------------------------
# Every picker load fans out to each ready machine over SSH. When a remote
# fails to resolve, the reason (nonzero exit + stderr, a connect/list timeout,
# unparseable output, or a source that was skipped because it is not-ready /
# has no SSH alias) is otherwise invisible -- the tab just shows "failed" with
# no operator-facing detail. We append a structured, per-load record to
# ``~/.agent-worktrees/logs/picker-ssh.log`` so a failed enumeration is
# diagnosable after the fact. Logging is strictly best-effort: every helper
# swallows its own errors and never affects the picker.

_SSH_LOG_MAX_BYTES = 1_000_000  # rotate the single log once it passes ~1 MB

# Phase-2 (``--classify``) budget for a remote source. The fast phase-1 listing
# already paints the machine's rows well within ``Source.timeout``, so the
# expensive per-worktree git classification runs as a non-blocking follow-up on
# a more generous budget -- a slow box (e.g. dev6's large worktree set) can
# finish classifying without the whole enumeration hinging on beating the short
# interactive timeout.
_CLASSIFY_TIMEOUT = 60


class _RemoteFetchError(RuntimeError):
    """A remote ``list`` fetch failed; carries diagnostic detail for logging.

    ``str(self)`` stays a concise one-liner (what the picker records as the
    tab's ``_error``), while ``returncode`` / ``stderr`` / ``argv`` carry the
    full context the SSH log prints.
    """

    def __init__(self, message, *, returncode=None, stderr="", argv=None):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr or ""
        self.argv = argv


def _ssh_log_path():
    """``~/.agent-worktrees/logs/picker-ssh.log`` (created lazily), or None."""
    try:
        d = cfg.install_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d / "picker-ssh.log"
    except Exception:
        return None


def _ssh_log(line: str) -> None:
    """Append one timestamped line to the picker SSH log. Never raises."""
    try:
        path = _ssh_log_path()
        if path is None:
            return
        try:
            if path.exists() and path.stat().st_size > _SSH_LOG_MAX_BYTES:
                # Keep the most-recent half so the file stays bounded without
                # discarding the passes an operator is most likely to inspect.
                tail = path.read_bytes()[-(_SSH_LOG_MAX_BYTES // 2):]
                path.write_bytes(b"# (rotated -- older lines dropped)\n" + tail)
        except Exception:
            pass
        ts = _dt.datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} pid={os.getpid()} {line}\n")
    except Exception:
        pass


def _remote_cmd_str(argv) -> str:
    """Readable remote command from a list argv (decodes ``-EncodedCommand``).

    The Windows remote form is ``pwsh -NoProfile -EncodedCommand <base64>``;
    decode the blob back to its ``<project> list ...`` text so the log shows the
    actual command that was run, not an opaque base64 string.
    """
    try:
        if not argv:
            return ""
        parts = list(argv)
        marker = "-EncodedCommand "
        for i, a in enumerate(parts):
            if isinstance(a, str) and marker in a:
                head, b64 = a.rsplit(marker, 1)
                try:
                    dec = base64.b64decode(b64).decode("utf-16-le")
                    parts[i] = f"{head}(decoded) {dec}"
                except Exception:
                    pass
        return " ".join(str(p) for p in parts)
    except Exception:
        return str(argv)


def _resolve_provider_source(source: Source, runner) -> bool:
    """Refresh provider venue metadata and report instance replacement."""
    if source.source_kind != SOURCE_KIND_PROVIDER_EXEC:
        return False
    proc = runner(source.resolve_argv, min(source.timeout, 10))
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"provider resolve exit {proc.returncode}")
    payload = _extract_json(proc.stdout)
    venue = payload.get("venue")
    if not isinstance(venue, dict):
        raise RuntimeError("provider resolve omitted venue metadata")
    if (
        venue.get("provider") != source.provider
        or venue.get("target_id") != source.target_id
    ):
        raise RuntimeError("provider resolve returned a different target identity")
    instance_id = venue.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        raise RuntimeError("provider resolve omitted instance identity")
    if venue.get("ready") is not True:
        raise RuntimeError("provider target is not ready")
    if venue.get("posture_verified") is not True:
        raise RuntimeError("provider target trust posture is not verified")
    if venue.get("assignment") != source.venue.get("assignment"):
        raise RuntimeError("provider target lease assignment changed")
    changed = bool(source.instance_id and source.instance_id != instance_id)
    source.instance_id = instance_id
    source.venue = dict(venue)
    source.argv = _provider_remote_argv(
        source,
        f"{_project()} {_list_args(source.shell, classify=source.use_classify)}",
        expected_instance_id=instance_id,
        expected_assignment=venue["assignment"],
    )
    return changed


def _fetch(source: Source, runner=None, *, classify: bool = True, argv=None,
           timeout=None):
    """Run one source's list command and return normalized worktree records.

    Local sources load in-process. Remotes run over SSH, retrying without
    ``--classify`` when the remote agent-worktrees is too old to recognize it.

    ``runner`` runs the subprocess (default :func:`_run`); :class:`LiveLoader`
    passes its own tracked-and-killable runner so a picker exit can cancel any
    in-flight prefetch. ``argv`` overrides the source's default list argv
    (used by the fast phase-1 pass and the PR-reconcile pass, #2102) without
    persisting to ``source.argv``. ``timeout`` overrides ``source.timeout`` for
    this call (used by the remote phase-2 classify pass, which runs on the more
    generous :data:`_CLASSIFY_TIMEOUT` budget).

    ``classify`` only affects the **local** source: ``False`` skips the expensive
    per-worktree git classification for a fast provisional listing (the loader's
    phase-1 fast pass). Remote sources classify via their argv (the
    ``--classify`` flag), so this flag is a no-op for them -- their phase-1
    fast pass is driven by an ``argv`` override instead.
    """
    runner = runner or _run
    if source.local:
        return data_local.load(
            source.machine,
            source.env,
            classify=classify,
            source_kind=source.source_kind,
            source_id=source.source_id,
            source_label=source.source_label,
            runner=runner,
        )

    eff_timeout = source.timeout if timeout is None else timeout
    use_argv = argv if argv is not None else source.argv
    proc = runner(use_argv, eff_timeout)
    if proc.returncode != 0 and _is_cache_only_unsupported(proc.stderr):
        # Older remote without --cache-only (picker-cache-first-paint,
        # dotfiles#948): drop the flag and retry. The remote falls back to its
        # normal (non-cache) fast listing, so the tab still resolves.
        use_argv = _drop_cache_only_arg(use_argv)
        proc = runner(use_argv, eff_timeout)
    if proc.returncode != 0 and _is_classify_unsupported(proc.stderr):
        # Older remote: drop --classify and retry (rows will lack canonical
        # state but still load). Encoding-aware so the pwsh/-EncodedCommand
        # form is stripped too, not just the plain bash form.
        retry = _drop_classify_arg(use_argv)
        use_argv = retry
        # Persist the fallback only for the source's own default argv, not for a
        # transient override (e.g. the reconcile pass).
        if argv is None:
            source.argv = retry
            source.use_classify = False
        proc = runner(retry, eff_timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = err[-1] if err else f"exit {proc.returncode}"
        raise _RemoteFetchError(
            msg, returncode=proc.returncode,
            stderr=(proc.stderr or proc.stdout or ""), argv=use_argv,
        )
    try:
        data = _extract_json(proc.stdout)
    except Exception as exc:
        raise _RemoteFetchError(
            f"unparseable output: {exc}", returncode=proc.returncode,
            stderr=(proc.stdout or "")[:2000], argv=use_argv,
        ) from exc
    return [derive.norm(
                w, source.machine, source.env,
                source_kind=source.source_kind,
                source_id=source.source_id,
                source_label=source.source_label,
                source_metadata={
                    "provider": source.provider,
                    "target_id": source.target_id,
                    "instance_id": source.instance_id,
                    "venue": dict(source.venue),
                    "transport_fingerprint": (
                        _provider_transport_fingerprint(source)
                        if source.source_kind == SOURCE_KIND_PROVIDER_EXEC
                        else ""
                    ),
                },
                source_capabilities=source.capabilities,
            )
            for w in data.get("worktrees", [])]


class LiveLoader:
    """Background, per-machine loader feeding the picker's spinner -> resolve.

    On :meth:`start`, spawns one daemon thread per *ready* source. Each thread
    runs its list command and records either ``ready`` (with normalized
    worktrees) or ``failed`` (on any error/timeout). The UI polls :meth:`state`
    and :meth:`records` from its render tick -- a failed remote never crashes or
    hangs the UI. Not-ready sources are seeded ``failed`` and never contacted.
    """

    def __init__(self, sources=None):
        all_sources = _unique_sources(
            list(sources if sources is not None else _build_sources())
        )
        self._all_sources = all_sources
        self._sources = [s for s in all_sources if s.ready]
        self._lock = threading.Lock()
        self._state = {}     # source_id -> loading|ready|failed
        self._records = {}   # source_id -> [normalized record, ...]
        self._error = {}     # source_id -> str (last error)
        # In-flight prefetch ssh children, tracked so a picker exit can kill
        # them (otherwise a quick selection orphans them -- they reparent to
        # init and keep churning git-classification on the target machine,
        # starving the Copilot session we just launched there).
        self._procs = []
        self._procs_lock = threading.Lock()
        self._cancelled = threading.Event()
        self._source_locks = {
            source.cache_key: threading.Lock() for source in all_sources
        }
        # Keys with a silent background refresh in flight (#1421) -- a per-source
        # guard so a slow machine isn't re-hit every poll interval.
        self._refreshing: set = set()
        # Remote sources whose PRs have already been reconciled this session
        # (#2102) -- the remote-over-SSH reconcile runs once per source, as each
        # becomes ready, not on every poll.
        self._pr_reconciled_keys: set = set()
        # Per-source generation: bumped by reload() so an in-flight silent
        # repoll that started earlier never commits stale rows over a newer
        # intentional reload (#1421, ordering fix).
        self._gen: dict = {}
        for s in all_sources:
            self._state[s.cache_key] = "loading" if s.ready else "failed"
            self._records[s.cache_key] = []
            self._gen[s.cache_key] = 0

    def start(self):
        derive.NOW = _dt.datetime.now()
        self._log_load_header()
        # Every source -- local included -- loads on its own daemon thread so
        # the picker paints and accepts keys the instant it mounts; rows stream
        # in (local + remote alike) via the engine's render tick as each source
        # resolves. Local was once run synchronously here on the assumption it's
        # "fast" (#1432), but a real machine's git-classification of many
        # worktrees can take multiple seconds -- long enough to freeze the whole
        # TUI (no paint, no arrow keys) until it finished. Threading it keeps
        # interaction immediate and the local tab simply shows the connect
        # spinner until its records arrive, exactly like the remotes.
        for s in self._sources:
            threading.Thread(
                target=self._load_one, args=(s, self._gen.get(s.cache_key, 0)),
                name=f"load-{s.machine}-{s.env}", daemon=True,
            ).start()

    def reload(self, machine, env):
        """Re-fetch one source now (e.g. after a Maintenance op changed it).

        Every source -- local included -- re-threads so a post-maintenance
        refresh never blocks the UI (the local git-classification can take
        seconds); the tab shows the connect spinner until its fresh records
        arrive. Unknown / not-ready sources are a no-op. Returns True when a
        matching source was found (#1421, live re-render).
        """
        return self.reload_source(self._cache_key(machine, env))

    def reload_source(self, source_id):
        """Re-fetch one source by its canonical identity."""
        for s in self._sources:
            if s.cache_key == source_id:
                with self._lock:
                    self._state[s.cache_key] = "loading"
                    # Invalidate any in-flight silent repoll of this source so
                    # its (older) rows can't land after this reload (#1421).
                    self._gen[s.cache_key] = self._gen.get(s.cache_key, 0) + 1
                    gen = self._gen[s.cache_key]
                threading.Thread(
                    target=self._load_one, args=(s, gen),
                    name=f"reload-{s.machine}-{s.env}", daemon=True,
                ).start()
                return True
        return False

    def repoll_silent(self, keys=None):
        """Background-refresh currently-ready sources *in place* (#1421).

        Unlike :meth:`reload`, this never flips a source back to ``loading`` --
        no spinner, no transient empty list. Each ready source re-fetches on a
        daemon thread and swaps its records only on success; a failed refresh
        silently keeps the last-good rows. A per-source in-flight guard means a
        slow machine is skipped (not re-hit) on the next poll. ``keys`` bounds
        the pass to a set of ``(machine, env)`` -- the picker passes only the
        machines currently in view so a specific-machine tab never fans out to
        the whole fleet. No-op once the loader is cancelled (picker teardown).

        Returns the number of sources a refresh was actually started for.
        """
        if self._cancelled.is_set():
            return 0
        started = 0
        with self._lock:
            for s in self._sources:
                if (
                    keys is not None
                    and s.key not in keys
                    and s.source_id not in keys
                ):
                    continue
                if self._state.get(s.cache_key) != "ready":
                    continue
                if s.cache_key in self._refreshing:
                    continue
                self._refreshing.add(s.cache_key)
                gen = self._gen.get(s.cache_key, 0)
                started += 1
                threading.Thread(
                    target=self._refresh_one, args=(s, gen),
                    name=f"repoll-{s.machine}-{s.env}", daemon=True,
                ).start()
        return started

    def _refresh_one(self, source: Source, gen: int):
        """Silent re-fetch for :meth:`repoll_silent`: swap records on success,
        keep last-good on failure, and always clear the in-flight guard.

        ``gen`` is the source's generation captured when the refresh started;
        the fetched rows are committed only if the generation is unchanged --
        i.e. no :meth:`reload` superseded this refresh while it ran (#1421)."""
        with self._source_locks[source.cache_key]:
            self._refresh_one_serial(source, gen)

    def _refresh_one_serial(self, source: Source, gen: int):
        """Serialized implementation of :meth:`_refresh_one`."""
        try:
            if self._cancelled.is_set():
                return
            changed = False
            if source.source_kind == SOURCE_KIND_PROVIDER_EXEC:
                try:
                    changed = _resolve_provider_source(source, self._spawn)
                except Exception as exc:
                    with self._lock:
                        self._records[source.cache_key] = []
                        self._state[source.cache_key] = "failed"
                        self._error[source.cache_key] = (
                            str(exc).strip() or type(exc).__name__
                        )
                    return
            if changed:
                with self._lock:
                    self._records[source.cache_key] = []
                    self._state[source.cache_key] = "loading"
            try:
                recs = _fetch(source, runner=self._spawn)
            except Exception as exc:
                if source.source_kind == SOURCE_KIND_PROVIDER_EXEC:
                    with self._lock:
                        if changed:
                            self._state[source.cache_key] = "failed"
                            self._records[source.cache_key] = []
                        if (
                            changed
                            or self._gen.get(source.cache_key, 0) == gen
                        ):
                            self._error[source.cache_key] = (
                                str(exc).strip() or type(exc).__name__
                            )
                return
            with self._lock:
                # Commit only when still ready, not cancelled, and not
                # superseded by a newer reload() (generation unchanged).
                if (
                    not self._cancelled.is_set()
                    and (
                        changed
                        or self._state.get(source.cache_key) == "ready"
                    )
                    and self._gen.get(source.cache_key, 0) == gen
                ):
                    self._records[source.cache_key] = recs
                    self._state[source.cache_key] = "ready"
        finally:
            with self._lock:
                self._refreshing.discard(source.cache_key)

    def reconcile_remote_prs(self, keys=None):
        """Reconcile each ready REMOTE machine's own PR state, on that machine (#2102).

        For every ready remote source (local PRs reconcile via the engine's
        #1423 path), run ``list --reconcile-prs`` over SSH once -- the remote
        corrects and persists its stale PR states -- and swap the corrected rows
        in place, exactly like :meth:`repoll_silent` (no spinner, generation-
        checked, best-effort). Runs at most once per source per session, as each
        becomes ready, so a remote tab stops showing a merged-elsewhere worktree
        as having an open PR without ever blocking the first paint. ``keys``
        bounds the pass to the machines currently in view. No-op once cancelled.

        Returns the number of sources a reconcile was actually started for.
        """
        if self._cancelled.is_set():
            return 0
        started = 0
        with self._lock:
            for s in self._sources:
                if s.local:
                    continue
                if s.source_kind != SOURCE_KIND_MACHINE_SSH:
                    continue
                if (
                    keys is not None
                    and s.key not in keys
                    and s.source_id not in keys
                ):
                    continue
                if self._state.get(s.cache_key) != "ready":
                    continue
                if (s.cache_key in self._pr_reconciled_keys
                        or s.cache_key in self._refreshing):
                    continue
                self._pr_reconciled_keys.add(s.cache_key)
                self._refreshing.add(s.cache_key)
                gen = self._gen.get(s.cache_key, 0)
                started += 1
                threading.Thread(
                    target=self._reconcile_one, args=(s, gen),
                    name=f"reconcile-{s.machine}-{s.env}", daemon=True,
                ).start()
        return started

    def _reconcile_one(self, source: Source, gen: int):
        """Silent remote PR-reconcile for :meth:`reconcile_remote_prs`: fetch with
        ``--reconcile-prs`` and swap records on success, keep last-good on
        failure, and always clear the in-flight guard.

        ``gen`` guards against committing over a newer :meth:`reload` (#1421).
        A failed reconcile leaves ``_pr_reconciled_keys`` set (best-effort, one
        attempt) so a persistently-unreachable remote isn't re-hit every poll.
        """
        try:
            if self._cancelled.is_set():
                return
            recs = _fetch(source, runner=self._spawn, argv=_reconcile_argv(source))
        except Exception:
            return  # keep last-good records; no state flip
        else:
            with self._lock:
                if (not self._cancelled.is_set()
                        and self._state.get(source.cache_key) == "ready"
                        and self._gen.get(source.cache_key, 0) == gen):
                    self._records[source.cache_key] = recs
        finally:
            with self._lock:
                self._refreshing.discard(source.cache_key)

    def cancel(self):
        """Stop loading and kill any in-flight prefetch ssh children.

        Idempotent; called from the picker's teardown (Textual ``on_unmount``)
        so a launch decision never leaves orphaned ``ssh ... list`` processes
        behind. Safe to call when nothing is in flight.
        """
        self._cancelled.set()
        with self._procs_lock:
            procs = list(self._procs)
        for p in procs:
            _kill_proc_tree(p)

    def _spawn(self, argv, timeout):
        """Tracked, killable runner for prefetch subprocesses.

        Mirrors :func:`_run` but registers the live :class:`subprocess.Popen`
        so :meth:`cancel` can terminate it (and, on POSIX, its whole process
        group). Returns a :class:`subprocess.CompletedProcess` so ``_fetch`` is
        agnostic to which runner produced it.
        """
        if self._cancelled.is_set():
            raise RuntimeError("cancelled")
        kwargs = dict(
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # Never inherit the console stdin: an ``ssh`` child would otherwise
            # read the terminal's keyboard input out from under Textual's input
            # reader, freezing the picker's keys until the load fan-out exits.
            stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONSAFEPATH": "1",
            },
        )
        if os.name == "posix":
            kwargs["start_new_session"] = True   # own group -> killpg on cancel
        else:
            # CREATE_NEW_PROCESS_GROUP: killable as a group on cancel.
            # CREATE_NO_WINDOW: keep the ssh child off our console so a failing
            # ssh can't clear the console's VT-input mode and break arrow keys.
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)  # headless-guard: allow: own process group (kill-as-group on cancel) + no-window
                | _CREATE_NO_WINDOW
            )
        proc = subprocess.Popen(argv, **kwargs)
        with self._procs_lock:
            self._procs.append(proc)
        # Close the cancel/spawn race: if cancel() ran between the top-of-method
        # check and the Popen above, it never saw this child. Re-check now that
        # it's registered and kill it so a late spawn can't orphan an ssh child.
        if self._cancelled.is_set():
            _kill_proc_tree(proc)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_proc_tree(proc)
            out, err = proc.communicate()
        finally:
            with self._procs_lock:
                if proc in self._procs:
                    self._procs.remove(proc)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)

    def _spawn_stream(self, argv):
        """Spawn a tracked, killable **streaming** child: returns the live
        :class:`subprocess.Popen` (NOT communicated) so the caller can read
        stdout line-by-line as the remote flushes NDJSON. Registered in
        ``self._procs`` exactly like :meth:`_spawn`, so :meth:`cancel` (picker
        teardown) tears it down and no ``ssh ... list --stream`` is orphaned."""
        if self._cancelled.is_set():
            raise RuntimeError("cancelled")
        kwargs = dict(
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1,   # line-buffered: surface rows as they arrive
        )
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)  # headless-guard: allow: own process group (kill-as-group on cancel) + no-window
                | _CREATE_NO_WINDOW
            )
        proc = subprocess.Popen(argv, **kwargs)
        with self._procs_lock:
            self._procs.append(proc)
        # Close the cancel/spawn race (see _spawn).
        if self._cancelled.is_set():
            _kill_proc_tree(proc)
        return proc

    def _untrack(self, proc):
        with self._procs_lock:
            if proc in self._procs:
                self._procs.remove(proc)

    def _load_one(self, source: Source, gen: int | None = None):
        if gen is None:
            gen = self._gen.get(source.cache_key, 0)
        with self._source_locks[source.cache_key]:
            self._load_one_serial(source, gen)

    def _load_one_serial(self, source: Source, gen: int):
        if source.local:
            # Local tab: fast-then-fill so rows paint immediately (see below).
            self._load_local_two_phase(source)
            return
        try:
            changed = _resolve_provider_source(source, self._spawn)
        except Exception as exc:
            with self._lock:
                self._records[source.cache_key] = []
                self._state[source.cache_key] = "failed"
                self._error[source.cache_key] = (
                    str(exc).strip() or type(exc).__name__
                )
            return
        if changed:
            with self._lock:
                self._records[source.cache_key] = []
        if source.source_kind == SOURCE_KIND_PROVIDER_EXEC:
            try:
                recs = _fetch(source, runner=self._spawn)
            except Exception as exc:
                with self._lock:
                    if self._gen.get(source.cache_key, 0) != gen:
                        return
                    has_last_good = (
                        not changed
                        and bool(self._records.get(source.cache_key))
                    )
                    self._state[source.cache_key] = (
                        "ready" if has_last_good else "failed"
                    )
                    self._error[source.cache_key] = (
                        str(exc).strip() or type(exc).__name__
                    )
                return
            with self._lock:
                if (
                    not self._cancelled.is_set()
                    and self._gen.get(source.cache_key, 0) == gen
                ):
                    self._records[source.cache_key] = recs
                    self._state[source.cache_key] = "ready"
                    self._error[source.cache_key] = ""
            return
        # Remote tab: try single-connection NDJSON streaming -- fast rows paint
        # as they arrive, then each upgrades in place as its classified row
        # streams in. Opt-in (see _stream_enabled) during the rollout window;
        # falls back to the two-phase path when disabled or when the remote is
        # too old to know --stream.
        if _stream_enabled() and self._load_remote_stream(source, gen):
            return
        self._load_remote_two_phase(source)

    def _load_remote_stream(self, source: Source, gen: int) -> bool:
        """Single-connection NDJSON streaming load.

        Paints fast rows as they arrive (flipping the source to ``ready`` on the
        first one) and upgrades each in place when its classified row streams in
        -- collapsing the two-phase load's two SSH round-trips into one. An
        overall :data:`_CLASSIFY_TIMEOUT` watchdog kills a stalled stream; rows
        already received are kept (the tab stays resolved), mirroring the
        two-phase classify-timeout behavior.

        Returns True when the remote spoke the streaming protocol (resolved
        fully, partially, or errored -- all handled/logged here). Returns False
        **only** when the remote is too old to know ``--stream`` (argparse
        'unrecognized arguments'), so the caller falls back to two-phase."""
        argv = _stream_argv(source)
        deadline = max(source.timeout, _CLASSIFY_TIMEOUT)
        t0 = _dt.datetime.now()
        by_id: dict = {}
        order: list = []
        ready = False
        done = False
        err = ""
        try:
            proc = self._spawn_stream(argv)
        except Exception as exc:
            self._log_fetch_failure(source, exc, t0, phase="stream")
            with self._lock:
                self._state[source.cache_key] = "failed"
                self._error[source.cache_key] = str(exc).strip() or type(exc).__name__
            return True
        timer = threading.Timer(deadline, lambda: _kill_proc_tree(proc))
        timer.daemon = True
        timer.start()
        try:
            for raw in proc.stdout:
                if self._cancelled.is_set():
                    break
                obj = _parse_ndjson_line(raw)
                if obj is None:
                    continue
                typ = obj.get("type")
                if typ == "worktree":
                    wt = obj.get("wt") or {}
                    rid = wt.get("id")
                    if not rid:
                        continue
                    rec = derive.norm(
                        wt,
                        source.machine,
                        source.env,
                        source_kind=source.source_kind,
                        source_id=source.source_id,
                        source_label=source.source_label,
                        source_metadata={
                            "provider": source.provider,
                            "target_id": source.target_id,
                            "instance_id": source.instance_id,
                            "venue": dict(source.venue),
                        },
                        source_capabilities=source.capabilities,
                    )
                    with self._lock:
                        if self._cancelled.is_set():
                            break
                        if self._gen.get(source.cache_key, 0) != gen:
                            break   # superseded by a reload()
                        if rid not in by_id:
                            order.append(rid)
                        by_id[rid] = rec
                        self._records[source.cache_key] = [by_id[i] for i in order]
                        if not ready:
                            self._state[source.cache_key] = "ready"
                            ready = True
                elif typ == "done":
                    done = True
        finally:
            timer.cancel()
            try:
                _, err = proc.communicate(timeout=5)
            except Exception:
                _kill_proc_tree(proc)
                try:
                    _, err = proc.communicate(timeout=5)
                except Exception:
                    err = ""
            self._untrack(proc)
        elapsed = (_dt.datetime.now() - t0).total_seconds()
        rc = proc.returncode
        if ready or done:
            # Fully or partially resolved (or an empty remote) -- keep the rows.
            with self._lock:
                if (not self._cancelled.is_set()
                        and self._gen.get(source.cache_key, 0) == gen):
                    self._records[source.cache_key] = [by_id[i] for i in order]
                    self._state[source.cache_key] = "ready"
            note = "streamed" if done else "streamed (partial)"
            _ssh_log(f"  OK      {source.machine}/{source.env} {note} "
                     f"{len(order)} worktree(s) in {elapsed:.1f}s")
            return True
        # No rows: an old remote falls back to two-phase; anything else is a
        # real failure the streaming path owns.
        if _is_stream_unsupported(err):
            _ssh_log(f"  ~       {source.machine}/{source.env} --stream "
                     f"unsupported; falling back to two-phase")
            return False
        exc = _RemoteFetchError(
            (err.strip().splitlines()[-1] if err.strip() else f"exit {rc}"),
            returncode=rc, stderr=err, argv=argv)
        self._log_fetch_failure(source, exc, t0, phase="stream", timeout=deadline)
        with self._lock:
            self._state[source.cache_key] = "failed"
            self._error[source.cache_key] = str(exc).strip() or type(exc).__name__
        return True

    def _load_remote_two_phase(self, source: Source):
        """Fast-then-classify for a remote tab (mirrors the local two-phase).

        Phase 1 runs the remote ``list`` **without** ``--classify`` -- a cheap
        tracking/sessions/mux listing that returns well within ``Source.timeout``
        -- so the machine's rows paint reliably instead of the enumeration
        hinging on beating the timeout with per-worktree git classification.
        Phase 2 re-runs *with* ``--classify`` on the more generous
        :data:`_CLASSIFY_TIMEOUT` budget and swaps the authoritative state in on
        success; a timeout or error there keeps the honest phase-1 rows (the tab
        stays resolved, just without canonical git state) rather than failing
        the machine.

        A remote already known not to support ``--classify`` (an older
        agent-worktrees, ``use_classify`` cleared by a prior fetch) has nothing
        to classify, so it loads in a single pass -- there is no second phase."""
        gen = self._gen.get(source.cache_key, 0)
        fast_argv = _drop_classify_arg(source.argv)
        two_phase = source.use_classify and fast_argv != source.argv
        # picker-cache-first-paint (dotfiles#948): when enabled, the fast phase
        # goes cache-only (no remote events.jsonl/process scan) -- but only for a
        # real two-phase remote (an old no-classify remote almost certainly lacks
        # --cache-only too, and the classify phase 2 warms its cache anyway).
        if _CACHE_ONLY_GATE and two_phase:
            fast_argv = _add_cache_only_arg(fast_argv)
        # Phase 1: fast, no classify (or the only pass for a no-classify remote).
        t0 = _dt.datetime.now()
        try:
            first = _fetch(source, runner=self._spawn,
                           argv=(fast_argv if two_phase else None))
        except Exception as exc:  # any failure -> failed state
            self._log_fetch_failure(source, exc, t0,
                                    phase="fast" if two_phase else "load")
            with self._lock:
                self._state[source.cache_key] = "failed"
                self._error[source.cache_key] = str(exc).strip() or type(exc).__name__
            return
        elapsed = (_dt.datetime.now() - t0).total_seconds()
        label = "fast, no classify" if two_phase else "single pass"
        _ssh_log(f"  OK      {source.machine}/{source.env} resolved "
                 f"{len(first)} worktree(s) ({label}) in {elapsed:.1f}s")
        with self._lock:
            if self._cancelled.is_set():
                return
            self._records[source.cache_key] = first
            self._state[source.cache_key] = "ready"
        if not two_phase or self._cancelled.is_set():
            return
        # Phase 2: authoritative git classification on the longer classify budget
        # (non-blocking -- the rows are already painted); swapped in on success.
        t1 = _dt.datetime.now()
        classify_timeout = max(source.timeout, _CLASSIFY_TIMEOUT)
        try:
            full = _fetch(source, runner=self._spawn, timeout=classify_timeout)
        except Exception as exc:
            self._log_fetch_failure(source, exc, t1, phase="classify",
                                    timeout=classify_timeout)
            return  # keep the honest phase-1 rows; the tab stays resolved
        elapsed2 = (_dt.datetime.now() - t1).total_seconds()
        _ssh_log(f"  OK      {source.machine}/{source.env} classified "
                 f"{len(full)} worktree(s) in {elapsed2:.1f}s")
        with self._lock:
            if (not self._cancelled.is_set()
                    and self._state.get(source.cache_key) == "ready"
                    and self._gen.get(source.cache_key, 0) == gen):
                self._records[source.cache_key] = full

    def _load_local_two_phase(self, source: Source):
        """Fast-then-fill for the local tab.

        Phase 1 (``classify=False``) is cheap -- tracking + sessions + mux, no
        per-worktree git -- so the local rows paint at once (via ``derive``'s
        classification-absent heuristic: status + turns + PR) instead of blocking
        on git classification of every worktree, which can take seconds or stall.
        Phase 2 (``classify=True``) runs the full git classification and swaps
        the authoritative ``state`` in. A generation guard keeps a concurrent
        :meth:`reload` from being clobbered by this pass's phase 2 (#1421)."""
        gen = self._gen.get(source.cache_key, 0)
        t0 = _dt.datetime.now()
        try:
            fast = _fetch(source, classify=False)
        except Exception as exc:
            self._log_fetch_failure(source, exc, t0, phase="local")
            with self._lock:
                self._state[source.cache_key] = "failed"
                self._error[source.cache_key] = str(exc).strip() or type(exc).__name__
            return
        with self._lock:
            if self._cancelled.is_set():
                return
            self._records[source.cache_key] = fast
            self._state[source.cache_key] = "ready"
        # Phase 2: authoritative git classification, swapped in on success.
        try:
            full = _fetch(source, classify=True)
        except Exception:
            return  # keep the honest phase-1 heuristic rows
        with self._lock:
            if (not self._cancelled.is_set()
                    and self._state.get(source.cache_key) == "ready"
                    and self._gen.get(source.cache_key, 0) == gen):
                self._records[source.cache_key] = full

    def _log_load_header(self):
        """Enumerate every source this load pass will (or won't) resolve.

        Written once per :meth:`start` so the SSH log opens each pass with the
        full roster: which envs load locally, which remotes are contacted over
        SSH (with alias + timeout), and which are skipped and *why* (no SSH
        alias, or the machine is not ``ssh.ready`` in ``machines.yaml``) -- the
        skipped ones are never contacted, so this is the only place their
        non-resolution is recorded."""
        try:
            srcs = getattr(self, "_all_sources", None) or self._sources
            local = [s for s in srcs if s.local]
            remote = [s for s in srcs if s.ready and not s.local]
            skipped = [s for s in srcs if not s.ready and not s.local]
            _ssh_log(
                f"=== picker load pass: {len(remote)} remote to resolve, "
                f"{len(local)} local, {len(skipped)} skipped ===")
            for s in local:
                _ssh_log(f"  LOCAL   {s.machine}/{s.env} (in-process)")
            for s in remote:
                _ssh_log(f"  RESOLVE {s.machine}/{s.env} "
                         f"alias={s.alias} timeout={s.timeout}s")
            for s in skipped:
                why = ("no SSH alias/profile" if not s.alias
                       else "machine not ssh.ready in machines.yaml")
                _ssh_log(f"  SKIP    {s.machine}/{s.env} ({why})")
        except Exception:
            pass

    def _log_fetch_failure(self, source, exc, t0, *, phase="load", timeout=None):
        """Record why one remote source failed to resolve: elapsed, timeout vs
        error, exit code, stderr tail, and the decoded remote command.

        ``timeout`` overrides ``source.timeout`` for the elapsed-vs-budget
        labeling (the phase-2 classify pass runs on a longer budget)."""
        try:
            budget = source.timeout if timeout is None else timeout
            elapsed = (_dt.datetime.now() - t0).total_seconds()
            timed_out = isinstance(exc, subprocess.TimeoutExpired) or (
                elapsed >= budget * 0.9)
            kind = "TIMEOUT" if timed_out else "FAILED"
            detail = str(exc).strip() or type(exc).__name__
            _ssh_log(
                f"  {kind:7s} {source.machine}/{source.env} [{phase}] "
                f"after {elapsed:.1f}s (timeout={budget}s): {detail}")
            rc = getattr(exc, "returncode", None)
            if rc is not None:
                _ssh_log(f"            exit={rc} alias={source.alias}")
            stderr = getattr(exc, "stderr", "") or ""
            for ln in stderr.strip().splitlines()[-8:]:
                _ssh_log(f"            stderr| {ln}")
            argv = getattr(exc, "argv", None) or source.argv
            _ssh_log(f"            cmd: {_remote_cmd_str(argv)}")
        except Exception:
            pass

    def state(self, machine, env):
        """Compatibility lookup for a machine/environment source."""
        return self.state_for_source(self._cache_key(machine, env))

    def state_for_source(self, source_id):
        with self._lock:
            return self._state.get(source_id, "loading")

    def _cache_key(self, machine, env):
        return machine_source_id(machine, env)

    def records(self):
        """Flat list of every normalized worktree, keeping last-good rows in
        place across a refresh so the list never blanks.

        A source's rows are served whenever it HAS rows -- ``ready``, or
        ``loading``/``failed`` while it still holds its last-good records (a
        ``reload()`` after an action, or a transient re-fetch failure). Only a
        source that has never resolved (initial connect spinner, empty records)
        contributes nothing. This makes every refresh path update **in place**:
        the fresh rows swap in atomically when they arrive instead of the
        machine's rows vanishing to a blank while it re-fetches (dotfiles#948
        follow-up). ``repoll_silent`` already kept rows during its silent
        re-fetch; this extends the same courtesy to ``reload()`` (which flips a
        source to ``loading``) and to a post-action refresh."""
        with self._lock:
            out = []
            for key, recs in self._records.items():
                if recs or self._state.get(key) == "ready":
                    out.extend(recs)
            return out

    def records_for_source(self, source_id):
        """Return a copy of one canonical source's current rows."""
        with self._lock:
            return list(self._records.get(source_id, []))

    def counts(self):
        """(ready, loading, failed) machine counts for the status note."""
        with self._lock:
            vals = list(self._state.values())
        return (
            sum(1 for v in vals if v == "ready"),
            sum(1 for v in vals if v == "loading"),
            sum(1 for v in vals if v == "failed"),
        )

    def error(self, machine, env):
        """Compatibility lookup for a machine/environment source."""
        return self.error_for_source(self._cache_key(machine, env))

    def error_for_source(self, source_id):
        with self._lock:
            return self._error.get(source_id)
