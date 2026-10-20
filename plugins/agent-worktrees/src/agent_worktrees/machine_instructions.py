"""Pure rendering of machine identity and deployment metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

from machine_transport import entry_physical_host, machine_name

if TYPE_CHECKING:
    from .config import MachineEntry


def render_unresolved_context(
    machine: str, *, project: str, platform: str, hostname: str,
) -> str:
    """Render execution facts without claiming a resolved physical machine."""
    return "\n".join((
        "Machine: unresolved", f"Hostname: {hostname}",
        f"Execution identity: {machine}", f"Environment: {platform}",
        f"Platform: {platform}", f"Project: {project}", f"Binstub: {project}",
    ))


def render_copilot_instructions(
    entry: MachineEntry, project: str = "", *, machine: str = "", platform: str,
    hostname: str = "",
) -> str:
    """Render the content of ``machine.instructions.md`` for a machine.

    Physical machine, observed hostname, execution environment, compact identity
    and SSH transport remain distinct facts. When *project* is provided,
    includes project and binstub metadata.
    """
    # Find the SSH alias matching the current platform
    deploy_env = ""
    for ssh_env in entry.ssh_environments:
        if ssh_env.name == platform:
            deploy_env = ssh_env.alias
            break

    lines = [
        f"Machine: {entry_physical_host(entry)}",
        f"Hostname: {hostname or entry.hostname or entry.key}",
        f"Machine name: {entry_physical_host(entry)}",
        f"Execution identity: {machine or machine_name(entry)}",
        f"Alias/friendly name: {entry.display_name}",
        f"Environment: {platform}",
        f"Platform: {platform}",
    ]
    if entry.environment:
        lines.append(f"System environment: {entry.environment}")
    if deploy_env:
        lines.append(f"SSH alias: {deploy_env}")
    if entry.role:
        lines.append(f"Role: {entry.role}")
    if entry.description:
        lines.append(f"Description: {entry.description}")
    if entry.capabilities:
        lines.append(f"Capabilities: {', '.join(entry.capabilities)}")
    if project:
        lines.append(f"Project: {project}")
        lines.append(f"Binstub: {project}")
    return "\n".join(lines) + "\n"
