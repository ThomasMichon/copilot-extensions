"""Attributable namespace-provider identity without ambient command discovery."""


class ProviderIdentity:
    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def management_command(self) -> list[str] | None:
        return list(self._command) if self._command else None
