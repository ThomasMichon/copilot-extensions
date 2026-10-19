"""Strict, data-only configuration for the built-in publication checks."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath


def parse_string_tuple(value: object) -> tuple[str, ...]:
    """Normalize publication labels/body-section lists without changing legacy coercion."""
    if isinstance(value, (list, tuple)):
        return tuple(s for s in (str(x).strip() for x in value) if s)
    if value:
        text = str(value).strip()
        return (text,) if text else ()
    return ()


@dataclass(frozen=True)
class PublicationChecksConfig:
    self_review: bool = True
    scope_report: bool = True
    review_lenses_path: str = ""
    allowed_paths: tuple[str, ...] | None = None
    guidance: str = ""


def relative_pattern(value: object, *, glob: bool = True) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("publication check paths must be nonempty bounded strings")
    path = PurePosixPath(value)
    if (
        "\\" in value or any(c in value for c in "\x00\r\n")
        or path.is_absolute() or PureWindowsPath(value).drive
        or any(part in ("", ".", "..") for part in value.rstrip("/").split("/"))
        or (not glob and any(c in value for c in "*?[]"))
    ):
        raise ValueError("publication check paths must be portable repository-relative paths")
    return value


def parse_publication_checks(raw: object) -> PublicationChecksConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {
        "features", "review_lenses_path", "allowed_paths", "guidance",
    }:
        raise ValueError("pr.publication_checks must contain only built-in configuration fields")
    features = raw.get("features", {})
    if not isinstance(features, dict) or set(features) - {"self_review", "scope_report"}:
        raise ValueError("pr.publication_checks.features has unknown check names")
    for value in features.values():
        if type(value) is not bool:
            raise ValueError("pr.publication_checks.features values must be booleans")
    lens_path = raw.get("review_lenses_path", "")
    if lens_path != "":
        lens_path = relative_pattern(lens_path, glob=False)
    paths = raw.get("allowed_paths")
    if paths is not None:
        if not isinstance(paths, list) or len(paths) > 1000:
            raise ValueError("pr.publication_checks.allowed_paths must be null or a bounded list")
        paths = tuple(relative_pattern(value) for value in paths)
        if len(set(paths)) != len(paths):
            raise ValueError("pr.publication_checks.allowed_paths must be unique")
        if not features.get("scope_report", True):
            raise ValueError("allowed_paths requires the built-in scope_report check")
    guidance = raw.get("guidance", "")
    if not isinstance(guidance, str) or len(guidance) > 16384:
        raise ValueError("pr.publication_checks.guidance must be a bounded string")
    return PublicationChecksConfig(
        self_review=features.get("self_review", True),
        scope_report=features.get("scope_report", True),
        review_lenses_path=lens_path,
        allowed_paths=paths,
        guidance=guidance,
    )
