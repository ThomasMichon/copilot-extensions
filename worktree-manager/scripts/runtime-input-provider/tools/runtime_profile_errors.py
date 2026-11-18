"""Shared refusal types usable before semantic admission tooling is present."""


class ProfileError(ValueError):
    """A malformed, incompatible, unapproved or ambiguous selection."""


class ProfileUnavailable(ProfileError):
    """No approved record matches the authority's requested selection."""
