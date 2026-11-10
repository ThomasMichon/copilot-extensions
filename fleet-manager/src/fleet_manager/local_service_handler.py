"""OS-admitted local requests, deliberately distinct from Gateway caller context."""

from dataclasses import dataclass, field

from .gateway_service_handler import RequestLifetime


@dataclass(frozen=True)
class LocalCallerContext:
    """Produced only by a selected local transport's OS peer-identity adapter."""

    namespace: str
    principal: str
    expires_at: int

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in (
            self.namespace, self.principal,
        )) or type(self.expires_at) is not int or self.expires_at < 1:
            raise ValueError("invalid admitted local context")


@dataclass(frozen=True)
class LocalServiceRequest:
    context: LocalCallerContext
    method: str
    target: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    lifetime: RequestLifetime = field(default_factory=RequestLifetime, repr=False, compare=False)
