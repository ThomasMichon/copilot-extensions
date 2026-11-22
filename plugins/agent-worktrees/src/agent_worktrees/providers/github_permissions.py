"""Normalize GitHub's authenticated repository permission observation."""

_GH_PERMISSION_PRIORITY = ("admin", "maintain", "push", "triage", "pull")
_GH_PERMISSION_TOKEN = {
    "admin": "admin", "maintain": "maintain", "push": "write",
    "triage": "triage", "pull": "read",
}


def _github_viewer_permission(permissions: object) -> str:
    """Missing/malformed evidence is unknown, never confident denial or access."""
    if not isinstance(permissions, dict):
        return ""
    for bit in _GH_PERMISSION_PRIORITY:
        if permissions.get(bit) is True:
            return _GH_PERMISSION_TOKEN[bit]
    return ""
