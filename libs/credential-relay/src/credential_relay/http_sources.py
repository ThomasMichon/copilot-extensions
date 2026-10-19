"""Named HTTP credential sources; registration is not caller authorization."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .sources import CredentialSource

log = logging.getLogger(__name__)
HTTP_SOURCE_REFERENCE = r"^(?:builtin|[^@/\\\s]+@[^@/\\\s]+)/[A-Za-z0-9][A-Za-z0-9._-]*$"


@dataclass(frozen=True)
class HttpCredentialSource:
    source: CredentialSource
    scoped: bool = False


class HttpSourceRegistry:
    """Refuse ambiguous source identities without affecting unrelated entries."""

    def __init__(self) -> None:
        self._sources: dict[str, HttpCredentialSource] = {}
        self._ambiguous: set[str] = set()

    def register(self, reference: str, source: CredentialSource, *, scoped: bool = False) -> None:
        if not re.fullmatch(HTTP_SOURCE_REFERENCE, reference):
            raise ValueError("HTTP source requires a provider-qualified reference")
        if reference in self._sources or reference in self._ambiguous:
            self._sources.pop(reference, None)
            self._ambiguous.add(reference)
            log.warning("Ambiguous HTTP credential source reference: %s", reference)
            return
        self._sources[reference] = HttpCredentialSource(source, scoped)

    def get(self, reference: str) -> HttpCredentialSource:
        if reference in self._ambiguous or reference not in self._sources:
            raise PermissionError("HTTP credential source unavailable or ambiguous")
        return self._sources[reference]

    def include(self, other: HttpSourceRegistry) -> None:
        for reference, entry in other._sources.items():
            self.register(reference, entry.source, scoped=entry.scoped)
        for reference in other._ambiguous:
            self._sources.pop(reference, None)
            self._ambiguous.add(reference)
