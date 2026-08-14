"""User-level *source* override for the Worktree Manager self-updater.

The self-updater (:func:`worktree_manager.self_install.self_update`) normally
pulls the Worktree Manager payload from the canonical GitHub repo's ``main``
branch. This module lets a user durably point it somewhere else — a **fork** or a
**canary / different source branch** — via a small, human-editable, user-level
config file, **not** an environment variable.

The config lives at ``<root>/config.toml`` (``~/.worktree-manager/config.toml`` by
default) under a ``[source]`` table::

    [source]
    repo = "https://github.com/<fork>/copilot-extensions.git"
    ref  = "canary"

Resolution is simply **config file → built-in default** (there is no env knob for
the source). It is managed with the ``worktree-manager source`` command
(``set`` / ``reset`` / show).
"""

from __future__ import annotations

from pathlib import Path

try:  # tomllib is stdlib on 3.11+; tomli backports it for 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

#: Canonical defaults (the bootstrap one-liners fetch from the same place).
DEFAULT_REPO = "https://github.com/ThomasMichon/copilot-extensions.git"
DEFAULT_REF = "main"
CONFIG_NAME = "config.toml"


def config_path(root: Path | None = None) -> Path:
    """Path to the user-level config file under the install root."""
    from .self_install import default_root

    return (root or default_root()) / CONFIG_NAME


def _load(root: Path | None = None) -> dict:
    try:
        return tomllib.loads(config_path(root).read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def configured_source(root: Path | None = None) -> tuple[str | None, str | None]:
    """The explicitly-configured ``(repo, ref)`` overrides, ``None`` where unset."""
    src = _load(root).get("source")
    if not isinstance(src, dict):  # tolerate a malformed / non-table [source]
        return None, None
    repo = src.get("repo")
    ref = src.get("ref")
    repo = repo if isinstance(repo, str) and repo else None
    ref = ref if isinstance(ref, str) and ref else None
    return repo, ref


def resolved_repo(root: Path | None = None) -> str:
    """The effective git source: the configured repo, else the canonical default."""
    repo, _ = configured_source(root)
    return repo or DEFAULT_REPO


def resolved_ref(root: Path | None = None) -> str:
    """The effective branch/ref: the configured ref, else the canonical default."""
    _, ref = configured_source(root)
    return ref or DEFAULT_REF


def _escape(value: str) -> str:
    # Emit a valid TOML basic string: escape backslash/quote first, then the
    # control chars TOML represents with escapes (so a stray newline/tab in a
    # repo/ref can't corrupt the file).
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _render(repo: str | None, ref: str | None) -> str:
    lines = ["[source]"]
    if repo:
        lines.append(f'repo = "{_escape(repo)}"')
    if ref:
        lines.append(f'ref = "{_escape(ref)}"')
    return "\n".join(lines) + "\n"


def _write(repo: str | None, ref: str | None, root: Path | None = None) -> None:
    """Persist (or, when nothing is set, remove) the ``[source]`` config atomically."""
    p = config_path(root)
    if not (repo or ref):
        try:
            p.unlink()
        except OSError:
            pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(_render(repo, ref), encoding="utf-8")
    tmp.replace(p)  # atomic publish


def set_source(
    *, repo: str | None = None, ref: str | None = None, root: Path | None = None
) -> tuple[str | None, str | None]:
    """Set the repo and/or ref override, preserving whichever is not provided."""
    cur_repo, cur_ref = configured_source(root)
    _write(
        repo if repo is not None else cur_repo,
        ref if ref is not None else cur_ref,
        root,
    )
    return configured_source(root)


def reset_source(
    *, repo: bool = False, ref: bool = False, root: Path | None = None
) -> None:
    """Clear the repo and/or ref override (both when neither flag is given)."""
    cur_repo, cur_ref = configured_source(root)
    if not repo and not ref:
        repo = ref = True
    _write(None if repo else cur_repo, None if ref else cur_ref, root)
