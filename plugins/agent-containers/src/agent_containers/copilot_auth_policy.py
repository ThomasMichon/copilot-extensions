"""Explicit trusted inference policy, independent of repository credentials."""

HOST_COPILOT = "host-copilot"
LEGACY = "legacy"


def validate_auth_mode(value: object) -> str:
    if not isinstance(value, str) or value not in (HOST_COPILOT, LEGACY):
        raise RuntimeError("copilot_auth_mode must be 'host-copilot' or explicit 'legacy'")
    return value


def trusted_auth_mode(config, fleet) -> str | None:
    if fleet and getattr(fleet, "security_profile", "trusted") == "restricted":
        return None
    override = getattr(fleet, "copilot_auth_mode", None) if fleet else None
    return validate_auth_mode(
        override if override is not None else getattr(config, "copilot_auth_mode", HOST_COPILOT)
    )
