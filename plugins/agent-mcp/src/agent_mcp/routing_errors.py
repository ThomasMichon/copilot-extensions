"""Expected routed failures shared by auth, protocol, and CLI boundaries."""


class RoutingError(RuntimeError):
    """An expected configuration-contract or authentication failure."""

    def __init__(self, message: str, *, code: int = -32603,
                 data: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class CompatibilityError(RoutingError):
    """A target does not implement the configured common tool contract."""
