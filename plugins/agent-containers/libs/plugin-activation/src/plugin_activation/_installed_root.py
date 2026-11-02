"""Resolve a plugin source to its installed-payload root under ``~/.copilot``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dropin_registry import Finding
from plugin_resolve import split_source

from ._findings import _finding
from ._fs_candidates import _manifest_name, _regular_directory, _valid_source_part


@dataclass
class _Candidate:
    root: Path | None = None
    findings: list[Finding] = field(default_factory=list)
    indeterminate: bool = False


def _installed_root(copilot_home: Path, source: str) -> _Candidate:
    name, marketplace = split_source(source)
    if not _valid_source_part(name) or not _valid_source_part(marketplace):
        return _Candidate(
            findings=[
                _finding(copilot_home, "invalid-entry", target=source, owner=source)
            ]
        )
    path = copilot_home / "installed-plugins" / marketplace / name
    canonical, error = _regular_directory(path)
    if canonical is None:
        if error is None:
            return _Candidate()
        indeterminate = not error.startswith("path must")
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "entry-indeterminate" if indeterminate else "identity-mismatch",
                    status="indeterminate" if indeterminate else "inactive",
                    owner=source,
                    detail=error,
                )
            ],
            indeterminate=indeterminate,
        )
    try:
        installed_root = (copilot_home / "installed-plugins").resolve(strict=True)
        relative = canonical.relative_to(installed_root)
    except OSError as exc:
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "entry-indeterminate",
                    status="indeterminate",
                    target=str(canonical),
                    owner=source,
                    detail=str(exc),
                )
            ],
            indeterminate=True,
        )
    except ValueError as exc:
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail=str(exc),
                )
            ]
        )
    if relative.parts != (marketplace, name):
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail="installed payload path does not match marketplace/plugin",
                )
            ]
        )
    manifest_name, manifest_error, manifest_indeterminate = _manifest_name(canonical)
    if manifest_error is not None:
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "entry-indeterminate"
                    if manifest_indeterminate
                    else "identity-mismatch",
                    status="indeterminate" if manifest_indeterminate else "inactive",
                    target=str(canonical),
                    owner=source,
                    detail=manifest_error,
                )
            ],
            indeterminate=manifest_indeterminate,
        )
    if manifest_name != name:
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail="installed plugin manifest name differs from source",
                )
            ]
        )
    return _Candidate(root=canonical)
