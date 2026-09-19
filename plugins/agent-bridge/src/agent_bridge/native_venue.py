"""Provider-qualified native venue identity, with legacy CodeSpace records."""

from __future__ import annotations

from .native_store import NativeError, identifier


def venue(spec: dict) -> tuple[str, str]:
    target = spec.get("target")
    if target is None:
        return "codespace", identifier(spec.get("codespace"))
    if not isinstance(target, str) or target.count(":") != 1:
        raise NativeError("invalid_target", "Expected codespace:NAME or container:NAME", 400)
    namespace, name = target.split(":")
    if namespace not in {"codespace", "container"}:
        raise NativeError("invalid_target", "Native venue provider is unsupported", 400)
    identifier(name)
    if "codespace" in spec or "container" in spec:
        raise NativeError("invalid_target", "Specify exactly one native venue", 400)
    return namespace, name


def key(spec: dict) -> str:
    namespace, name = venue(spec)
    return name if namespace == "codespace" else f"{namespace}:{name}"


def record_venue(row: dict) -> tuple[str, str]:
    spec = row["data"].get("spec")
    if spec and ("target" in spec or "codespace" in spec):
        return venue(spec)
    raw = row["codespace"]
    return tuple(raw.split(":", 1)) if raw.startswith("container:") else ("codespace", raw)
