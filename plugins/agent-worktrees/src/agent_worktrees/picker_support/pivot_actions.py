"""Mechanically extracted pivot action helpers from ``pivots.py``.

This sibling module exists only to keep ``pivots.py`` under its module-size
baseline. The worktree/config contribution types and argv-template helpers were
moved here verbatim, with no intended behavior change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class ManifestError(ValueError):
    """A pivot manifest was structurally invalid."""


def _as_argv(value: object, *, where: str) -> tuple[str, ...]:
    """Coerce a manifest ``list``/``run`` field into an argv tuple of strings."""
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ManifestError(f"{where} must be a non-empty array of strings")
    return tuple(value)


@dataclass(frozen=True)
class WorktreeAction:
    """A cross-plugin action contributed onto a *worktree row's* action menu."""

    key: str
    label: str
    run: tuple[str, ...]
    source: str
    confirm: bool = False
    description: str = ""
    when: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ConfigSection:
    """A cross-plugin section contributed under the ⚙ **Configuration** menu."""

    key: str
    label: str
    run: tuple[str, ...]
    source: str
    confirm: bool = False
    description: str = ""


def parse_worktree_actions(
    data: Mapping[str, object], *, name: str
) -> tuple[WorktreeAction, ...]:
    """Parse a manifest's optional ``worktree_actions`` array."""
    raw = data.get("worktree_actions", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ManifestError("`worktree_actions` must be an array when present")
    out: list[WorktreeAction] = []
    for i, a in enumerate(raw):
        if not isinstance(a, Mapping):
            raise ManifestError(f"`worktree_actions[{i}]` must be an object")
        label = a.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ManifestError(f"`worktree_actions[{i}].label` is required")
        run = _as_argv(a.get("run"), where=f"`worktree_actions[{i}].run`")
        key = a.get("key")
        when = a.get("when")
        if when is not None and not isinstance(when, Mapping):
            raise ManifestError(f"`worktree_actions[{i}].when` must be an object")
        out.append(
            WorktreeAction(
                key=str(key) if isinstance(key, str) and key else f"{name}{i}",
                label=label.strip(),
                run=run,
                source=name,
                confirm=bool(a.get("confirm", False)),
                description=str(a.get("description", "")),
                when=dict(when) if isinstance(when, Mapping) else None,
            )
        )
    return tuple(out)


def entry_matches(when: Mapping[str, object] | None, rec: Mapping[str, object]) -> bool:
    """True when ``rec`` satisfies a ``when`` gate."""
    if not when:
        return True
    for field_name, allowed in when.items():
        values = allowed if isinstance(allowed, (list, tuple)) else [allowed]
        allowed_str = {str(v).lower() for v in values}
        if str(rec.get(field_name)).lower() not in allowed_str:
            return False
    return True


def worktree_action_matches(
    action: WorktreeAction, rec: Mapping[str, object]
) -> bool:
    """True when ``action`` should appear for worktree record ``rec``."""
    return entry_matches(action.when, rec)


def parse_config_sections(
    data: Mapping[str, object], *, name: str
) -> tuple[ConfigSection, ...]:
    """Parse a manifest's optional ``config_sections`` array."""
    raw = data.get("config_sections", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ManifestError("`config_sections` must be an array when present")
    out: list[ConfigSection] = []
    for i, a in enumerate(raw):
        if not isinstance(a, Mapping):
            raise ManifestError(f"`config_sections[{i}]` must be an object")
        label = a.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ManifestError(f"`config_sections[{i}].label` is required")
        run = _as_argv(a.get("run"), where=f"`config_sections[{i}].run`")
        key = a.get("key")
        out.append(
            ConfigSection(
                key=str(key) if isinstance(key, str) and key else f"{name}{i}",
                label=label.strip(),
                run=run,
                source=name,
                confirm=bool(a.get("confirm", False)),
                description=str(a.get("description", "")),
            )
        )
    return tuple(out)


def format_template(template: Sequence[str], ctx: Mapping[str, object]) -> list[str]:
    """Substitute ``{token}`` placeholders in an argv template."""

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


def _encode_field_value(value: object) -> str:
    """Encode one collected field value for a ``--field name=value`` argument."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        import json as _json

        return _json.dumps([("" if v is None else str(v)) for v in value])
    return str(value)


def format_form_template(
    template: Sequence[str],
    ctx: Mapping[str, object],
    fields: Mapping[str, object],
) -> list[str]:
    """Substitute a form action's argv template in a single safe pass."""
    import string

    field_prefix = "field."

    class _FormFormatter(string.Formatter):
        def get_field(self, field_name, args, kwargs):  # type: ignore[override]
            if field_name.startswith(field_prefix):
                return (
                    _encode_field_value(fields.get(field_name[len(field_prefix):], "")),
                    field_name,
                )
            return (ctx.get(field_name, ""), field_name)

        def format_field(self, value, format_spec):  # type: ignore[override]
            return "" if value is None else str(value)

    fmt = _FormFormatter()
    out: list[str] = []
    for arg in template:
        if arg == "{fields}":
            for name, value in fields.items():
                out.append("--field")
                out.append(f"{name}={_encode_field_value(value)}")
            continue
        try:
            out.append(fmt.vformat(arg, (), {}))
        except (KeyError, IndexError, ValueError):
            out.append(arg)
    return out
