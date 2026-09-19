"""Distinguish machine identity from execution environment identity."""
from __future__ import annotations
from ..agent_registry import AgentResolver

def _is_local_target(ssh_host: str | None, resolver: AgentResolver) -> bool:
    """Check if an SSH host alias resolves to the local machine AND environment.

    Returns True only when the SSH alias points to the same machine key
    AND the same platform (wsl/windows/linux) as the one we're running on.
    This avoids treating a Windows agent as "local" when running on WSL
    (or vice versa), even though they share the same physical machine.
    """
    if not ssh_host:
        return True

    import socket
    hostname = socket.gethostname().lower()
    host_lower = ssh_host.lower()

    from ..agent_registry import _detect_local_machine
    machine, platform = _detect_local_machine(resolver.machines)
    if not machine:
        # If we can't identify our own machine, only match exact hostname
        return host_lower == hostname

    # Check if the SSH alias matches any alias for the local machine's
    # environments — but only the environment matching our platform
    for env in machine.ssh_environments:
        if env.alias and env.alias.lower() == host_lower:
            # Alias matches — is it our platform?
            return env.name == platform

    # Direct hostname match only if we can't resolve via aliases
    if host_lower == hostname or host_lower == machine.key.lower():
        # Ambiguous — could be any environment. Only treat as local
        # if there's exactly one environment and it matches our platform.
        matching = [e for e in machine.ssh_environments if e.name == platform]
        return len(matching) == 1

    return False

