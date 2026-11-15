"""Canonical cache/journal names for the backend's standard field aliases."""

from __future__ import annotations

ATTRIBUTES = {
    "password": "Password",
    "username": "UserName",
    "url": "URL",
    "title": "Title",
    "notes": "Notes",
}
_ALIASES = {attribute: name for name, attribute in ATTRIBUTES.items()}


def canonical_field(field: str) -> str:
    return _ALIASES.get(field, field)


def attribute_name(field: str) -> str:
    return ATTRIBUTES.get(field, field)
