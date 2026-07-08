"""Cross-plugin pivot registry for the Textual picker.

A pivot is a top-level view in the picker (Worktrees, Maintenance, Profiles).
This module lets *another* plugin -- installed in its own separate venv --
contribute an extra pivot without agent-worktrees importing its Python. Because
each plugin installs standalone (its own ``scripts/init.sh``), setuptools
entry-points do **not** cross venvs; a filesystem manifest registry does.

The contract:

* A contributing plugin's installer drops a JSON manifest into the shared
  runtime root at ``~/.agent-worktrees/pivots/<name>.json`` (overridable for
  tests via ``AGENT_WORKTREES_PIVOTS_DIR``).
* The manifest declares a display ``label``, a position hint (``after``), a
  ``list`` command (an argv template that prints a JSON array of entries to
  stdout), a field mapping so the generic renderer can pull id/title/worktree/
  badges out of each entry, and an ``actions`` set (each an argv template).
* The picker scans that directory at startup and renders a generic pivot per
  manifest -- no engine code per new pivot. Data flows only through the
  contributing plugin's CLI on ``PATH`` (never a cross-venv import), so the
  seam stays generic for future pivots (Bridges, Containers, ...).

Everything here is declarative and defensive: a missing directory, a malformed
manifest, or a CLI that never runs must never break the picker -- a bad or
absent pivot simply doesn't appear.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

#: Environment override for the manifest directory (used by tests for hermetic
#: isolation, and available as an operator escape hatch).
PIVOTS_DIR_ENV = "AGENT_WORKTREES_PIVOTS_DIR"


@dataclass(frozen=True)
class PivotAction:
    """One entry in a registered pivot's Enter sub-menu.

    ``run`` is an argv template: tokens like ``{id}`` / ``{machine}`` /
    ``{worktree}`` are substituted (see :func:`format_template`) from the
    selected entry and the current picker context at activation time.
    """

    key: str
    label: str
    run: tuple[str, ...]
    confirm: bool = False
    description: str = ""


@dataclass(frozen=True)
class RegisteredPivot:
    """A pivot contributed by another plugin via a filesystem manifest."""

    name: str
    label: str
    after: str
    list_cmd: tuple[str, ...]
    id_field: str
    title_field: str
    worktree_field: str | None
    badge_fields: tuple[str, ...]
    subtitle_field: str | None
    empty_hint: str
    actions: tuple[PivotAction, ...]
    source_path: str

    @property
    def kind(self) -> str:
        return "registered"


class ManifestError(ValueError):
    """A pivot manifest was structurally invalid."""


def _as_argv(value: object, *, where: str) -> tuple[str, ...]:
    """Coerce a manifest ``list``/``run`` field into an argv tuple of strings."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ManifestError(f"{where} must be a non-empty array of strings")
    argv = tuple(str(x) for x in value)
    if not argv:
        raise ManifestError(f"{where} must be a non-empty array of strings")
    return argv


def parse_manifest(data: Mapping[str, object], *, name: str, source_path: str) -> RegisteredPivot:
    """Build a :class:`RegisteredPivot` from a parsed manifest mapping.

    Raises :class:`ManifestError` on any structural problem so the caller can
    skip a single bad manifest without aborting discovery.
    """
    if not isinstance(data, Mapping):
        raise ManifestError("manifest root must be a JSON object")

    label = data.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ManifestError("`label` is required and must be a non-empty string")

    list_cmd = _as_argv(data.get("list"), where="`list`")

    after = data.get("after", "Worktrees")
    if not isinstance(after, str) or not after.strip():
        after = "Worktrees"

    entry = data.get("entry") or {}
    if not isinstance(entry, Mapping):
        raise ManifestError("`entry` must be an object when present")

    def _entry_str(key: str, default: str | None) -> str | None:
        val = entry.get(key, default)
        if val is None:
            return None
        if not isinstance(val, str):
            raise ManifestError(f"`entry.{key}` must be a string")
        return val

    id_field = _entry_str("id", "id") or "id"
    title_field = _entry_str("title", "title") or "title"
    worktree_field = _entry_str("worktree", "target_worktree")
    subtitle_field = _entry_str("subtitle", None)

    badges_raw = entry.get("badges", [])
    if isinstance(badges_raw, str):
        badge_fields: tuple[str, ...] = (badges_raw,)
    elif isinstance(badges_raw, Sequence):
        badge_fields = tuple(str(b) for b in badges_raw)
    else:
        raise ManifestError("`entry.badges` must be a string or array of strings")

    empty_hint = data.get("empty_hint", "No tasks.")
    if not isinstance(empty_hint, str):
        empty_hint = "No tasks."

    actions_raw = data.get("actions", [])
    if not isinstance(actions_raw, Sequence) or isinstance(actions_raw, (str, bytes)):
        raise ManifestError("`actions` must be an array when present")
    actions: list[PivotAction] = []
    for i, a in enumerate(actions_raw):
        if not isinstance(a, Mapping):
            raise ManifestError(f"`actions[{i}]` must be an object")
        a_label = a.get("label")
        if not isinstance(a_label, str) or not a_label.strip():
            raise ManifestError(f"`actions[{i}].label` is required")
        run = _as_argv(a.get("run"), where=f"`actions[{i}].run`")
        a_key = a.get("key")
        actions.append(
            PivotAction(
                key=str(a_key) if isinstance(a_key, str) and a_key else f"action{i}",
                label=a_label,
                run=run,
                confirm=bool(a.get("confirm", False)),
                description=str(a.get("description", "")),
            )
        )

    return RegisteredPivot(
        name=name,
        label=label.strip(),
        after=after.strip(),
        list_cmd=list_cmd,
        id_field=id_field,
        title_field=title_field,
        worktree_field=worktree_field,
        badge_fields=badge_fields,
        subtitle_field=subtitle_field,
        empty_hint=empty_hint,
        actions=tuple(actions),
        source_path=source_path,
    )


def pivots_dir(base: str | os.PathLike[str] | None = None) -> Path:
    """The manifest directory: an explicit ``base``, else the env override,
    else ``~/.agent-worktrees/pivots``."""
    if base is not None:
        return Path(base)
    env = os.environ.get(PIVOTS_DIR_ENV)
    if env:
        return Path(env)
    from .. import config

    return config.install_dir() / "pivots"


def discover_pivots(base: str | os.PathLike[str] | None = None) -> list[RegisteredPivot]:
    """Scan the manifest directory and return the valid registered pivots.

    Sorted by manifest filename for a stable tab order. A missing directory
    yields ``[]``; a malformed or unreadable manifest is skipped (never fatal),
    so the picker degrades gracefully when a contributor ships a bad file.
    """
    directory = pivots_dir(base)
    try:
        if not directory.is_dir():
            return []
        files = sorted(p for p in directory.iterdir() if p.suffix == ".json" and p.is_file())
    except OSError:
        return []

    out: list[RegisteredPivot] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(parse_manifest(data, name=path.stem, source_path=str(path)))
        except (OSError, ValueError):
            # A single bad manifest must not sink the others (or the picker).
            continue
    return out


def order_pivots(builtins: Sequence[str], registered: Sequence[RegisteredPivot]) -> list[dict]:
    """Weave registered pivots into the builtin order via their ``after`` hint.

    Returns a list of pivot descriptors (dicts) in final display order. Each is
    ``{"label", "kind", "pivot"}``; builtins carry ``pivot=None`` and a kind of
    their lowercased label, registered pivots carry ``kind="registered"`` and
    their :class:`RegisteredPivot`. A registered pivot whose ``after`` matches
    no builtin is appended at the end (still shown, never dropped).
    """
    descriptors: list[dict] = [
        {"label": b, "kind": b.strip().lower(), "pivot": None} for b in builtins
    ]
    for reg in registered:
        entry = {"label": reg.label, "kind": "registered", "pivot": reg}
        idx = next(
            (i for i, d in enumerate(descriptors) if d["label"].lower() == reg.after.lower()),
            None,
        )
        if idx is None:
            descriptors.append(entry)
        else:
            descriptors.insert(idx + 1, entry)
    return descriptors


def format_template(template: Sequence[str], ctx: Mapping[str, object]) -> list[str]:
    """Substitute ``{token}`` placeholders in an argv template.

    Only whole-token substitution is performed (``str.format_map`` with a
    default that leaves unknown tokens intact), so a literal argument without
    braces passes through untouched and an unknown placeholder degrades to
    empty rather than raising.
    """

    class _Default(dict):
        def __missing__(self, key: str) -> str:
            return ""

    safe = _Default({k: ("" if v is None else str(v)) for k, v in ctx.items()})
    out: list[str] = []
    for arg in template:
        try:
            out.append(arg.format_map(safe))
        except (KeyError, IndexError, ValueError):
            out.append(arg)
    return out


# Kept for symmetry with maintenance.py's module layout; the engine imports the
# functions above directly.
__all__ = [
    "ManifestError",
    "PivotAction",
    "RegisteredPivot",
    "discover_pivots",
    "format_template",
    "order_pivots",
    "parse_manifest",
    "pivots_dir",
]
