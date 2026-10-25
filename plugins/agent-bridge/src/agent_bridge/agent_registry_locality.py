"""Transport-locality matching for the registry's selected execution space."""

from __future__ import annotations


class _LocalTransportMixin:
    """Local-transport policy composed into AgentResolver."""

    def is_local_transport(
        self, ssh_host: str | None, *, execution_space_key: str | None = None,
    ) -> bool:
        """Match a transport to the selected execution space, not its physical host."""
        machine, platform = self._local_machine, self._local_platform
        if (machine and machine.execution_platform) or any(
            entry.execution_platform and machine is None
            for entry in self.machines.values()
        ):
            return bool(
                machine and execution_space_key == machine.key
            )
        if execution_space_key and (not machine or execution_space_key != machine.key):
            return False
        if not ssh_host:
            return True
        import socket

        hostname = socket.gethostname().lower()
        host_lower = ssh_host.lower()
        if not machine:
            return host_lower == hostname
        if execution_space_key is None:
            named = next(
                (entry for key, entry in self.machines.items() if key.casefold() == host_lower),
                None,
            )
            if named is not None:
                if named.key != machine.key:
                    return False
            if host_lower in self._ambiguous_ssh_aliases:
                return False
            elif host_lower in self._alias_index and self._alias_index[host_lower][0].key != machine.key:
                return False
        for env in machine.ssh_environments:
            if env.alias and env.alias.lower() == host_lower:
                return env.name == platform
        if host_lower == hostname or host_lower == machine.key.lower():
            return len([env for env in machine.ssh_environments if env.name == platform]) == 1
        return False
