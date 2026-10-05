"""Remote-machine helpers for the ``resolve`` launch flow."""

from __future__ import annotations

import os

from remote_login_shell import is_posix_login_shell, wrap_login_shell

from . import config as cfg, output


def _core():
    from . import __main__ as core

    return core


def _emit_plan(*args, **kwargs):
    return _core()._emit_plan(*args, **kwargs)


# Picker env labels (engine: "Win" | "WSL" | "Linux") -> machines.yaml ssh
# environment names.
_ENV_LABEL_TO_NAME = {"win": "windows", "wsl": "wsl", "linux": "linux"}


def _load_remote_machines(
    config: cfg.Config,
) -> list[tuple[cfg.MachineEntry, list[cfg.SSHEnvironment]]]:
    """Load machines/environments reachable via SSH from the picker.

    Returns a list of (machine, ssh_environments) tuples. For remote
    machines, all SSH environments are included. For the local machine,
    only environments that differ from the current platform are included
    (e.g., WSL when running on Windows).

    Filters by ssh_ready=True, copilot=True, and non-empty environments.
    """
    repo = config.default_repo
    try:
        machines = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        return []

    # Don't offer cross-machine handoffs from inside an SSH session -- it would
    # be a double hop. Show only this host's worktrees.
    if _in_ssh_session():
        return []

    local_key = config.machine
    current_platform = cfg.detect_platform()
    result: list[tuple[cfg.MachineEntry, list[cfg.SSHEnvironment]]] = []

    for key, entry in machines.items():
        if not entry.ssh_ready or not entry.ssh_environments or not entry.copilot:
            continue

        if key == local_key:
            # Local machine: only include other-platform environments
            other_envs = [e for e in entry.ssh_environments if e.name != current_platform]
            if other_envs:
                result.append((entry, other_envs))
        else:
            result.append((entry, entry.ssh_environments))

    return result


def _try_machine_handoff(
    config: cfg.Config,
    machine_name: str,
) -> int | None:
    """Handle --machine flag for a remote machine.

    Returns an exit code if the remote plan was emitted, or None if the
    machine wasn't found (caller should error).
    """
    remote_targets = _load_remote_machines(config)
    entry_map = {entry.key: (entry, envs) for entry, envs in remote_targets}

    if machine_name not in entry_map:
        found = None
        for entry, envs in remote_targets:
            if entry.alias and entry.alias.lower() == machine_name.lower():
                found = (entry, envs)
                break
        if not found:
            output.err(f"Unknown or unreachable remote machine: {machine_name}")
            all_machines = _load_all_machine_keys(config)
            if all_machines:
                output.err("Available: " + ", ".join(all_machines))
            return 1
        entry, envs = found
    else:
        entry, envs = entry_map[machine_name]

    ssh_alias, shell = _resolve_ssh_target(entry)
    project = cfg.project_name()
    _emit_plan(
        {
            "action": "remote",
            "ssh_alias": ssh_alias,
            "remote_command": _wrap_remote_command(shell, project),
            "machine": entry.key,
            "display_name": entry.display_name,
        }
    )
    return 0


def _load_all_machine_keys(config: cfg.Config) -> list[str]:
    """Load all machine keys from machines.yaml for error messages."""
    repo = config.default_repo
    try:
        machines = cfg.load_machines_yaml(repo.anchor)
        return list(machines.keys())
    except (FileNotFoundError, ValueError):
        return []


def _new_picker_blocked_by_ssh() -> bool:
    """The Textual picker can't read the keyboard over Windows OpenSSH.

    Textual's Windows input driver reads key events via
    ``ReadConsoleInputW(GetStdHandle(STD_INPUT_HANDLE))`` (see
    ``textual/drivers/win32.py``); those records are not delivered through the
    Windows OpenSSH ConPTY input path, so the TUI renders but is completely
    unresponsive to the keyboard. Linux/WSL over SSH is unaffected (the Unix
    driver reads the pty directly via ``os.read``). So over SSH **on Windows**
    we fall back to the legacy ANSI picker, whose ``msvcrt`` input works over
    the ConPTY (it's what the fleet has used over SSH all along).
    """
    return _in_ssh_session() and cfg.detect_platform() == "windows"


def _in_ssh_session() -> bool:
    """True when this process was reached over SSH."""
    return bool(
        os.environ.get("SSH_CONNECTION")
        or os.environ.get("SSH_TTY")
        or os.environ.get("SSH_CLIENT")
    )


def _emit_remote_plan_for_env(
    config: cfg.Config,
    machine_display: str,
    env_label: str,
    remote_args: list[str] | None = None,
) -> int | None:
    """Emit a remote SSH handoff plan for a specific machine and env."""
    repo = config.default_repo
    try:
        entries = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        return None

    key = _machine_key_for_display(config, machine_display)
    entry = entries.get(key)
    if entry is None:
        nl = (machine_display or "").lower()
        for candidate_key, candidate in entries.items():
            if (
                candidate_key.lower() == nl
                or candidate.display_name.lower() == nl
                or (candidate.alias and candidate.alias.lower() == nl)
                or (candidate.hostname and candidate.hostname.lower() == nl)
            ):
                entry, key = candidate, candidate_key
                break
    if entry is None or not entry.ssh_environments:
        return None

    requested_environment = bool((env_label or "").strip())
    want = _ENV_LABEL_TO_NAME.get((env_label or "").lower())
    if requested_environment and not want:
        return None
    ssh_alias = ""
    shell = ""
    if want:
        for ssh_env in entry.ssh_environments:
            if ssh_env.name == want:
                ssh_alias = ssh_env.alias
                shell = _default_shell_for_env_name(want, ssh_env.shell)
                break
        if not ssh_alias:
            return None
    if not ssh_alias:
        ssh_alias, shell = _resolve_ssh_target(entry)

    project = cfg.project_name()
    remote_command = " ".join([project, *remote_args]) if remote_args else project
    remote_command = _wrap_remote_command(shell, remote_command)
    display = f"{entry.display_name} {env_label}".strip()
    _emit_plan(
        {
            "action": "remote",
            "ssh_alias": ssh_alias,
            "remote_command": remote_command,
            "machine": entry.key,
            "display_name": display,
        }
    )
    return 0


def _default_shell_for_env_name(env_name: str, shell: str) -> str:
    """The effective shell for an SSH environment: the configured ``shell``
    (per ``machines.yaml``'s own documented default), or -- when unset --
    ``"pwsh"`` for a ``windows`` environment, ``"bash"`` otherwise (matching
    ``plugins/agent-bridge/docs/machine-config.md``'s own ``shell`` default).
    """
    return shell or ("pwsh" if env_name == "windows" else "bash")


def _resolve_ssh_target(entry: cfg.MachineEntry) -> tuple[str, str]:
    """Pick the best SSH ``(alias, shell)`` for a remote machine. ``shell`` is
    the matching ``machines.yaml`` ``ssh.environments[].shell`` value (e.g.
    ``"bash"``/``"pwsh"``), defaulting per :func:`_default_shell_for_env_name`
    when the config leaves ``shell`` unset."""
    if not entry.ssh_environments:
        return entry.key, ""

    env_lower = entry.environment.lower()
    if "windows" in env_lower:
        for ssh_env in entry.ssh_environments:
            if ssh_env.name == "windows":
                return ssh_env.alias, _default_shell_for_env_name("windows", ssh_env.shell)
    else:
        for ssh_env in entry.ssh_environments:
            if ssh_env.name in ("linux", "wsl"):
                return ssh_env.alias, _default_shell_for_env_name(ssh_env.name, ssh_env.shell)

    first = entry.ssh_environments[0]
    return first.alias, _default_shell_for_env_name(first.name, first.shell)


def _resolve_ssh_alias(entry: cfg.MachineEntry) -> str:
    """Pick the best SSH alias for a remote machine."""
    return _resolve_ssh_target(entry)[0]


def _wrap_remote_command(shell: str, command: str) -> str:
    """Wrap ``command`` so it runs correctly as a bare, non-interactive,
    non-login SSH command-exec (``ssh host "command"``) on the target shell.

    That invocation shape is non-login AND non-interactive for the remote
    shell: on a POSIX target, neither ``~/.profile`` (login-shell-only) nor a
    ``~/.bashrc``/``~/.zshrc`` entry placed after the interactive-shell guard
    ever runs for it -- so anything relying on a PATH addition made there
    (``uv``, ``copilot``, ``gh``, or any other tool installed to
    ``~/.local/bin``) is unreachable, even though it works fine from an
    actual interactive/login session. Hit live resuming a worktree over SSH
    to a machine with no ``~/.bashrc`` at all (only a login-only
    ``~/.profile``): the remote Worktree Manager's own ``--version`` health
    check couldn't find ``uv`` under the bare PATH, which cascaded into the
    direct-launch fallback not finding ``copilot``/``gh`` either.

    Forcing a login shell (``<shell> -lc``, using the CONFIGURED shell
    itself -- never a hardcoded ``bash`` substituted for a different
    configured one) makes the POSIX target source its own login-shell
    startup files before running ``command``, matching what an interactive
    session already gets. Windows (``pwsh``) targets are left untouched --
    this exact non-login-shell PATH gap is POSIX-specific; PowerShell's own
    profile-loading rules differ and are out of scope here. Only wraps for
    an EXPLICIT, documented POSIX shell (:func:`remote_login_shell.is_posix_login_shell`)
    -- never guesses for an unrecognized/empty ``shell`` value, since wrapping
    a non-POSIX target in a POSIX login shell would break it outright (see
    :func:`_resolve_ssh_target`, which already defaults an unset ``shell``
    sensibly before calling this)."""
    if is_posix_login_shell(shell):
        return wrap_login_shell(command, shell=shell)
    return command


def _machine_key_for_display(config: cfg.Config, name: str) -> str:
    """Resolve a picker machine label (display name / key / alias / hostname) to its key."""
    repo = config.default_repo
    try:
        entries = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        return name
    nl = name.lower()
    for key, entry in entries.items():
        if (
            key.lower() == nl
            or (entry.alias and entry.alias.lower() == nl)
            or entry.display_name.lower() == nl
            or (entry.hostname and entry.hostname.lower() == nl)
        ):
            return key
    return name
