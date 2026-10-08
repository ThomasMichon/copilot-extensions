"""Local-vs-SSH transport resolution and remote-command wrapping.

:func:`get_machine_transport` is the one-stop "should I run this locally or
over SSH, and if SSH, with which alias and shell" decision -- composing
:func:`machine_transport.identity.is_local_machine` (the canonicalized local
check) with :func:`resolve_ssh_target` (the per-entry SSH alias/shell
preference) and :func:`wrap_remote_command` (POSIX login-shell wrapping, via
``remote_login_shell``, with non-POSIX shells passed through untouched).

This does not try to cover every caller-specific remote-command shape (e.g.
a picker's own ``pwsh -NoProfile -WindowStyle Hidden -EncodedCommand``
construction, or an ACP bridge's breadcrumb-logged launch argv) -- those stay
local to their own consumer. What's shared here is the part that was
genuinely duplicated byte-for-byte: deciding local-vs-remote, picking the SSH
alias/shell for an entry, and the one documented POSIX-login-shell wrap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from remote_login_shell import is_posix_login_shell, wrap_login_shell

from .identity import is_local_machine
from .registry import MachineEntry, find_machine_entry

__all__ = [
    "TransportPlan",
    "default_shell_for_env_name",
    "get_machine_transport",
    "resolve_ssh_target",
    "wrap_remote_command",
]


def default_shell_for_env_name(env_name: str, shell: str) -> str:
    """The effective shell for an SSH environment: the configured ``shell``
    value when set, else ``"pwsh"`` for a ``windows`` environment and
    ``"bash"`` otherwise."""
    return shell or ("pwsh" if env_name == "windows" else "bash")


def resolve_ssh_target(entry: MachineEntry) -> tuple[str, str]:
    """Pick the best SSH ``(alias, shell)`` for a remote machine entry.

    Prefers the SSH environment matching ``entry.environment``'s own
    Windows-vs-POSIX hint; falls back to the first declared environment.
    Returns ``(entry.key, "")`` when the entry declares no SSH environments
    at all -- preserved as-is from this function's original behavior; a
    caller must treat an empty ``shell`` (or an empty ``alias``, if the
    registry was parsed with ``require_alias=False``) as "no usable SSH
    target" rather than dispatching to it blindly.
    """
    if not entry.ssh_environments:
        return entry.key, ""

    env_lower = entry.environment.lower()
    if "windows" in env_lower:
        for ssh_env in entry.ssh_environments:
            if ssh_env.name == "windows":
                return ssh_env.alias, default_shell_for_env_name("windows", ssh_env.shell)
    else:
        for ssh_env in entry.ssh_environments:
            if ssh_env.name in ("linux", "wsl"):
                return ssh_env.alias, default_shell_for_env_name(ssh_env.name, ssh_env.shell)

    first = entry.ssh_environments[0]
    return first.alias, default_shell_for_env_name(first.name, first.shell)


def wrap_remote_command(shell: str, command: str) -> str:
    """Wrap ``command`` so it runs correctly as a bare, non-interactive,
    non-login SSH command-exec (``ssh host "command"``) on the target shell.

    That invocation shape is non-login AND non-interactive for the remote
    shell: on a POSIX target, neither ``~/.profile`` (login-shell-only) nor a
    ``~/.bashrc``/``~/.zshrc`` entry placed after the interactive-shell guard
    ever runs for it -- so anything relying on a PATH addition made there
    (``uv``, ``copilot``, ``gh``, or any other tool installed to
    ``~/.local/bin``) is unreachable, even though it works fine from an
    actual interactive/login session.

    Forcing a login shell (``<shell> -lc``, using the CONFIGURED shell
    itself -- never a hardcoded ``bash`` substituted for a different
    configured one) makes the POSIX target source its own login startup
    files before running ``command``. Windows (``pwsh``) targets, and any
    other unrecognized/empty ``shell`` value, are left untouched -- never
    guessed at, since wrapping a non-POSIX target in a POSIX login shell
    would break it outright.
    """
    if is_posix_login_shell(shell):
        return wrap_login_shell(command, shell=shell)
    return command


@dataclass(frozen=True)
class TransportPlan:
    """Where/how to reach ``name``: in-process, or over SSH with a resolved
    alias and shell.

    ``resolved`` is ``False`` only when ``name`` could not be matched to any
    known machine at all (distinct from ``local=False`` with a ``None``
    ``ssh_alias``, which means a genuinely remote, but currently
    unreachable/alias-less, environment -- see :func:`resolve_ssh_target`).
    """

    local: bool
    resolved: bool
    machine_key: str | None = None
    display_name: str | None = None
    ssh_alias: str | None = None
    shell: str | None = None

    def wrap(self, command: str) -> str:
        """Wrap ``command`` for this plan's resolved shell. A no-op for a
        local plan, or a remote plan with no resolved shell."""
        if self.local or not self.shell:
            return command
        return wrap_remote_command(self.shell, command)


def get_machine_transport(
    name: str,
    *,
    config_machine: str,
    load_entries: Callable[[], dict[str, MachineEntry]],
    real_hostname: str | None = None,
) -> TransportPlan:
    """Resolve the transport plan for reaching ``name``.

    A direct ``config_machine`` match (the common, fast path) never touches
    the registry at all -- same degrade-safe ordering as
    :func:`is_local_machine`. Otherwise, the registry is loaded AT MOST ONCE
    (a lazily-cached snapshot, including a cached load failure) and reused
    for both the identity check and the subsequent alias/shell resolution:
    calling ``load_entries`` twice could otherwise observe two different
    snapshots (e.g. a live file that changed between reads), letting the
    identity check and the entry resolution disagree. A local result's
    ``machine_key`` is the matched registry entry's own canonical ``key``
    when one was found (not the raw, possibly differently-spelled ``name``/
    ``config_machine`` string) -- falling back to ``config_machine or name``
    only for a genuinely registry-free direct match.
    """
    config_machine = config_machine or ""
    if name and config_machine and name.lower() == config_machine.lower():
        return TransportPlan(local=True, resolved=True, machine_key=config_machine)

    snapshot: dict[str, dict[str, MachineEntry]] = {}
    failure: list[Exception] = []

    def _cached_entries() -> dict[str, MachineEntry]:
        if "value" not in snapshot and not failure:
            try:
                snapshot["value"] = load_entries()
            except (FileNotFoundError, ValueError, KeyError) as exc:
                failure.append(exc)
                raise
        if failure:
            raise failure[0]
        return snapshot["value"]

    local = is_local_machine(
        name, config_machine=config_machine, load_entries=_cached_entries,
        real_hostname=real_hostname,
    )
    try:
        entries = _cached_entries()
    except (FileNotFoundError, ValueError, KeyError):
        entries = {}
    entry = find_machine_entry(entries, name)

    if local:
        key = entry.key if entry is not None else (config_machine or name)
        return TransportPlan(local=True, resolved=True, machine_key=key)
    if entry is None:
        return TransportPlan(local=False, resolved=False)
    alias, shell = resolve_ssh_target(entry)
    return TransportPlan(
        local=False,
        resolved=True,
        machine_key=entry.key,
        display_name=entry.display_name,
        ssh_alias=alias or None,
        shell=shell or None,
    )
