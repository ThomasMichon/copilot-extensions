"""Reusable, named worker identities for declarative recipe loops.

A repository-issue-loop (or other declarative recipe) declaration can select a
worker identity by name instead of inlining its acting theme, focus, and rules
as ad hoc prompt prose. An identity is authored once, in the same
frontmatter-plus-markdown shape as an in-session sub-agent definition
(``*.agent.md``), and is independently revisable: sharpening its rules
improves every declaration that selects it.

Resolution order for a named identity (first hit wins):

1. A repo-local override: the canonical
   ``<repo>/.copilot-extensions/agent-dispatch/identities/<name>.identity.md``,
   then the legacy ``<repo>/.agent-dispatch/identities/<name>.identity.md``,
   with an explicit marketplace overlay under
   ``<repo>/.copilot-extensions/agent-dispatch/marketplaces/<marketplace-id>/identities/``.
   This lets an adopting repository carry its own private identity without
   touching this package.
2. The packaged built-in identities shipped inside this package:
   ``agent_dispatch/identities/<name>.identity.md`` -- included as package
   data so they resolve the same way from an editable checkout and an
   installed wheel/venv.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .registrar import RegistrarError
from . import repo_config

_FRONTMATTER = "---"
_REPO_LOCAL_SUBDIR = repo_config.CANONICAL_REPO_CONFIG_DIR / "identities"
_LEGACY_REPO_LOCAL_SUBDIR = repo_config.LEGACY_REPO_CONFIG_DIR / "identities"
# Package data: src/agent_dispatch/worker_identities.py -> src/agent_dispatch/identities.
# Must live *inside* the agent_dispatch package (not a plugin-root sibling of
# src/) so it is actually included in the built wheel and resolves identically
# from an editable checkout and an installed venv.
_BUILTIN_DIR = Path(__file__).resolve().parent / "identities"


@dataclass(frozen=True)
class WorkerIdentity:
    """One reusable, named worker identity.

    ``rules`` is the identity's full markdown body -- its structural theme,
    focus, and behavioral rules -- rendered verbatim into a task prompt in
    place of a declaration's inlined ``worker_guidance`` prose.
    """

    name: str
    description: str
    rules: str
    source_path: str


def _parse_identity_file(path: Path) -> WorkerIdentity:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(_FRONTMATTER):
        raise RegistrarError(
            f"worker identity {str(path)!r}: expected a '---' frontmatter header"
        )
    _, _, rest = text.partition(_FRONTMATTER)
    frontmatter_raw, sep, body = rest.partition(_FRONTMATTER)
    if not sep:
        raise RegistrarError(
            f"worker identity {str(path)!r}: unterminated frontmatter"
        )
    try:
        frontmatter = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as exc:
        raise RegistrarError(
            f"worker identity {str(path)!r}: invalid frontmatter YAML: {exc}"
        ) from exc
    if not isinstance(frontmatter, dict):
        raise RegistrarError(
            f"worker identity {str(path)!r}: frontmatter must be a mapping"
        )
    name = frontmatter.get("name")
    description = frontmatter.get("description", "")
    if not isinstance(name, str) or not name:
        raise RegistrarError(
            f"worker identity {str(path)!r}: frontmatter 'name' must be a "
            "non-empty string"
        )
    if not isinstance(description, str):
        raise RegistrarError(
            f"worker identity {str(path)!r}: frontmatter 'description' must "
            "be a string"
        )
    rules = body.strip()
    if not rules:
        raise RegistrarError(
            f"worker identity {str(path)!r}: body (the rules) must not be empty"
        )
    return WorkerIdentity(
        name=name,
        description=description.strip(),
        rules=rules,
        source_path=str(path),
    )


def _candidate_paths(name: str, *, cwd: Path | None = None) -> list[Path]:
    filename = f"{name}.identity.md"
    base = cwd if cwd is not None else Path.cwd()
    overlay = repo_config.overlay_repo_surface_dir(base, "identities")
    paths: list[Path] = []
    if overlay is not None:
        paths.append(overlay / filename)
    paths.extend(
        [
            base / _REPO_LOCAL_SUBDIR / filename,
            base / _LEGACY_REPO_LOCAL_SUBDIR / filename,
            _BUILTIN_DIR / filename,
        ]
    )
    return paths


def load_worker_identity(name: str, *, cwd: Path | None = None) -> WorkerIdentity:
    """Resolve and parse a named worker identity.

    Raises ``RegistrarError`` if ``name`` does not resolve to any candidate
    path, or if the resolved file is malformed.
    """
    if not name or not isinstance(name, str):
        raise RegistrarError("worker_identity: expected a non-empty string")
    for candidate in _candidate_paths(name, cwd=cwd):
        if candidate.is_file():
            return _parse_identity_file(candidate)
    raise RegistrarError(
        f"worker_identity {name!r}: no identity file found (looked for "
        f"{[str(p) for p in _candidate_paths(name, cwd=cwd)]})"
    )
