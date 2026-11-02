"""Pure rendering of machine identity and deployment metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

from machine_transport import LocalExecutionContext, entry_physical_host, machine_name

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
    return render_local_context(
        LocalExecutionContext(
            hostname or entry.hostname or entry.key, entry_physical_host(entry),
            platform, machine or machine_name(entry),
        ),
        entry=entry, project=project,
    )


def render_local_context(
    context: LocalExecutionContext, *, entry: MachineEntry | None = None, project: str = "",
) -> str:
    """Render shared local facts, adding only selected optional customization."""
    lines = [
        f"Machine: {context.machine}",
        f"Hostname: {context.hostname}",
        f"Machine name: {context.machine}",
        f"Execution identity: {context.execution_machine}",
        f"Environment: {context.environment}",
        f"Platform: {context.environment}",
    ]
    if entry is not None:
        lines.insert(4, f"Alias/friendly name: {entry.display_name}")
        lines.extend(_customization_lines(entry, context.environment))
    if project:
        lines.extend((f"Project: {project}", f"Binstub: {project}"))
    return "\n".join(lines) + "\n"


def _customization_lines(entry: MachineEntry, platform: str) -> list[str]:
    deploy_env = ""
    for ssh_env in entry.ssh_environments:
        if ssh_env.name == platform:
            deploy_env = ssh_env.alias
            break

    lines = []
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
    return lines
