"""Package a target's ordinary launch command without invoking either engine."""

from __future__ import annotations

import base64
import re
import shlex

from machine_transport import resolve_machine_selection, resolve_ssh_target, wrap_remote_command

from . import machine_roster


def shell_command(shell: str, argv: list[str]) -> str:
    shell = shell.lower()
    if shell in {"pwsh", "powershell", "pwsh.exe", "powershell.exe"}:
        script = "& " + " ".join("'" + part.replace("'", "''") + "'" for part in argv)
        encoded = base64.b64encode((script + "; exit $LASTEXITCODE").encode("utf-16le")).decode("ascii")
        return f"{shell} -NoProfile -EncodedCommand {encoded}"
    if shell not in {"bash", "sh", "zsh"}:
        raise ValueError("Remote launch requires a supported explicit shell")
    return wrap_remote_command(shell, shlex.join(argv))


def plan(
    project: str, machine: str, environment: str | None, *,
    worktree_id: str | None, new: bool, base: bool, bare_resume: bool,
    no_mux: bool, seed: str | None,
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", project):
        raise ValueError("Invalid source project")
    if (base or bare_resume) and seed:
        raise ValueError("A prompt requires New or ordinary Resume")
    entries = machine_roster.selected_entries(project)
    selected = resolve_machine_selection(entries, machine, environment, allow_physical_context=True)
    if selected.entry is None:
        raise ValueError(f"No configured SSH destination for {machine!r}")
    alias, shell = resolve_ssh_target(selected.entry)
    if not alias or alias.startswith("-") or not shell:
        raise ValueError(f"No usable SSH profile for {machine!r}")
    args = ["--new"] if new else ["--base"] if base else ["--worktree-id", worktree_id or ""]
    if bare_resume:
        args.append("--bare-resume")
    if no_mux:
        args.append("--no-mux")
    if seed:
        args += ["--stage-launch-seed", "--seed=" + seed]
    return {
        "action": "remote", "ssh_alias": alias,
        "remote_command": shell_command(shell, [project, *args]),
        "worktree_id": worktree_id, "remote_terminal_handoff_version": 1,
    }
