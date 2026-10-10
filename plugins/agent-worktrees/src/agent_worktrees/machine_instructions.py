"""Pure rendering of machine identity and deployment metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import MachineEntry


def render_copilot_instructions(
    entry: MachineEntry, project: str = "", *, machine: str = "", platform: str,
) -> str:
    """Render the content of ``machine.instructions.md`` for a machine.

    Includes the supplied platform along with the deployment environment (SSH
    alias) so agents know their exact identity for service deployments. When
    *project* is provided, includes project and binstub metadata.
    """
    # Find the SSH alias matching the current platform
    deploy_env = ""
    for ssh_env in entry.ssh_environments:
        if ssh_env.name == platform:
            deploy_env = ssh_env.alias
            break

    lines = [
        f"Machine: {machine or entry.display_name}",
        f"Hostname: {entry.key}",
        f"Environment: {entry.environment}",
        f"Platform: {platform}",
    ]
    if deploy_env:
        lines.append(f"Deployment environment: {deploy_env}")
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
