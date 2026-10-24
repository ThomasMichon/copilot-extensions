"""Remote-machine helpers for the ``resolve`` launch flow."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING

from machine_transport import default_shell_for_env_name as _default_shell_for_env_name
from machine_transport import find_machine_entry
from machine_transport import normalize_environment, resolve_machine_selection
from machine_transport import resolve_ssh_target as _resolve_ssh_target
from machine_transport import wrap_remote_command as _wrap_remote_command  # noqa: F401 -- compatibility re-export

from . import config as cfg
from . import machine_identity, output

if TYPE_CHECKING:
    from .resolve_cli import ResolveCommandState


def remote_selector_args(state: ResolveCommandState) -> list[str]:
    if getattr(state.args, "require_native_seed", False) and not (
        state.use_json and getattr(state.args, "seed", None)
        and not getattr(state.args, "dry_run", False)
    ):
        raise ValueError("Remote --require-native-seed requires structured JSON prompt admission.")
    if getattr(state.args, "owner_ref", None):
        raise ValueError("Remote --owner-ref is unsupported; create owned worktrees on the target.")
    if getattr(state.args, "copilot_args", None):
        raise ValueError("Remote Copilot arguments are unsupported; pass them on the target.")
    args = (
        ["--base"] if state.use_base else ["--new"] if state.use_new else
        ["--worktree-id", str(state.worktree_id)] if state.worktree_id else []
    )
    for name in ("bare_resume", "restore"):
        if getattr(state.args, name, False):
            args.append("--" + name.replace("_", "-"))
    if getattr(state.args, "target_no_mux", False) or (
        not state.use_json and getattr(state.args, "no_mux", False)
    ):
        args.append("--no-mux")
    for name in (
        "dry_run", "no_resume", "no_fast_forward", "recovery", "defer_projections",
        "bridge", "defer_new_seed", "stage_launch_seed",
    ):
        if getattr(state.args, "seed", None) and name in ("defer_new_seed", "stage_launch_seed"):
            continue  # Structured admission already guarantees queued seed staging.
        if getattr(state.args, name, False):
            args.append("--" + name.replace("_", "-"))
    for name in ("profile", "parent_session", "caller_worktree", "native_resume_session"):
        value = getattr(state.args, name, None)
        if value is not None:
            args.append("--" + name.replace("_", "-") + "=" + str(value))
    args.append("--require-agent-hosting")
    if getattr(state.args, "require_native_seed", False):
        args.append("--require-native-seed")
    if getattr(state.args, "launch_attempt_id", None) is not None:
        args.append("--launch-attempt-id=" + state.args.launch_attempt_id)
    return args


def _core():
    from . import __main__ as core

    return core


def _emit_plan(*args, **kwargs):
    return _core()._emit_plan(*args, **kwargs)


# Picker env labels (engine: "Win" | "WSL" | "Linux") -> machines.yaml ssh
# environment names.
_ENV_LABEL_TO_NAME = {"win": "windows", "wsl": "wsl", "linux": "linux"}


def _entry_for_environment(
    entries: dict[str, cfg.MachineEntry], name: str, environment: str | None = None,
) -> cfg.MachineEntry | None:
    return resolve_machine_selection(entries, name, environment, allow_physical_context=True).entry


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

    current_platform = cfg.detect_platform()
    result: list[tuple[cfg.MachineEntry, list[cfg.SSHEnvironment]]] = []

    for key, entry in machines.items():
        if not entry.ssh_ready or not entry.ssh_environments or not entry.copilot:
            continue

        # Canonicalize before comparing -- `key` and `config.machine` can be
        # different-but-equivalent spellings of the same machine (a
        # machines.yaml key vs. its alias, or a raw COMPUTERNAME vs. the
        # canonical alias set via the hostname-field decoupling). A naive
        # `key == local_key` string check then wrongly treats "this
        # machine" as a remote target.
        if machine_identity.is_local_machine(key, config):
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
    remote_args: list[str] | None = None,
) -> int | None:
    """Handle --machine flag for a remote machine.

    Returns an exit code if the remote plan was emitted, or None if the
    machine wasn't found (caller should error).
    """
    remote_targets = _load_remote_machines(config)
    entry_map = {
        entry.key: replace(entry, ssh_environments=envs)
        for entry, envs in remote_targets
    }
    try:
        entry = find_machine_entry(entry_map, machine_name, reject_ambiguous=True)
    except ValueError as exc:
        output.err(str(exc))
        return 1
    if entry is None:
        output.err(f"Unknown or unreachable remote machine: {machine_name}")
        all_machines = _load_all_machine_keys(config)
        if all_machines:
            output.err("Available: " + ", ".join(all_machines))
        return 1

    ssh_alias, shell = _resolve_ssh_target(entry)
    project = cfg.project_name()
    try:
        remote_command = _remote_launch_command(shell, project, remote_args)
    except ValueError as exc:
        output.err(str(exc))
        return 2
    _emit_plan(
        {
            "action": "remote",
            "ssh_alias": ssh_alias,
            "remote_command": remote_command,
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


def _remote_launch_command(shell: str, project: str, args: list[str] | None) -> str:
    from .remote_seed_launch import shell_command

    options = list(args or [])
    if "--require-agent-hosting" not in options:
        options.append("--require-agent-hosting")
    return shell_command(shell, [project, *options])


def _emit_remote_plan_for_env(
    config: cfg.Config,
    machine_display: str,
    env_label: str,
    remote_args: list[str] | None = None,
    *,
    seed: str | None = None,
) -> int | None:
    """Emit a remote SSH handoff plan for a specific machine and env."""
    repo = config.default_repo
    try:
        entries = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        return None

    try:
        normalize_environment(env_label)
    except ValueError:
        return None
    try:
        selection = resolve_machine_selection(
            entries, machine_display, env_label, allow_physical_context=True,
        )
        want, entry = selection.target.environment, selection.entry
    except ValueError as exc:
        return output._json_error(str(exc))
    if entry is None or not entry.ssh_environments:
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
    if seed:
        from .remote_seed_launch import prepare

        try:
            plan = prepare(config, ssh_alias, shell, remote_args or [], seed)
        except (ValueError, RuntimeError) as exc:
            return output._json_error(str(exc), exit_code=3)
        plan.update(machine=entry.key, display_name=f"{entry.display_name} {env_label}".strip())
        _emit_plan(plan)
        return 0
    try:
        remote_command = _remote_launch_command(shell, project, remote_args)
    except ValueError as exc:
        return output._json_error(str(exc), exit_code=2)
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


def _resolve_ssh_alias(entry: cfg.MachineEntry) -> str:
    """Pick the best SSH alias for a remote machine."""
    return _resolve_ssh_target(entry)[0]


def _machine_key_for_display(
    config: cfg.Config, name: str, environment: str | None = None,
) -> str:
    """Resolve a picker machine label (display name / key / alias / hostname) to its key."""
    repo = config.default_repo
    try:
        entries = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        return name
    entry = _entry_for_environment(entries, name, environment)
    return entry.key if entry is not None else name
