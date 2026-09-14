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
    """A cross-plugin action contributed onto a *worktree row's* action menu.

    Unlike :class:`PivotAction` (which rides a registered pivot's own entries),
    a worktree action augments the built-in **Worktrees** view: any installed
    layer can add a verb to a worktree's Enter sub-menu (e.g. a bridge's "Send
    message", a dispatcher's "Dispatch task here") without agent-worktrees
    importing its Python. ``run`` is an argv template substituted from the
    worktree's context (``{worktree}`` / ``{machine}`` / ``{env}`` / ``{repo}``
    / ``{id4}`` plus the record's fields). ``when`` optionally gates visibility
    to worktrees whose normalized record matches every field (value or list).
    """

    key: str
    label: str
    run: tuple[str, ...]
    source: str
    confirm: bool = False
    description: str = ""
    when: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ConfigSection:
    """A cross-plugin section contributed under the ⚙ **Configuration** menu.

    Where :class:`WorktreeAction` augments a *worktree row's* Enter sub-menu,
    a config section augments the right-aligned ⚙ Configuration menu (which
    hosts built-in Profiles): any installed layer can add a settings entry
    (e.g. an SSH layer an "SSH" home, an MCP layer an "MCP" home) without
    agent-worktrees importing its Python. Selecting the section runs ``run`` --
    an argv template substituted from picker context (``{machine}`` / ``{repo}``)
    -- so a contributed config tool opens on its own terms on ``PATH``. Config
    sections are global (not per-worktree), so there is no ``when`` gate.
    """

    key: str
    label: str
    run: tuple[str, ...]
    source: str
    confirm: bool = False
    description: str = ""


def parse_worktree_actions(
    data: Mapping[str, object], *, name: str
) -> tuple[WorktreeAction, ...]:
    """Parse a manifest's optional ``worktree_actions`` array (independent of
    whether the manifest also contributes a ``list`` pivot). A malformed entry
    raises :class:`ManifestError` so the caller can skip the whole manifest's
    worktree actions without aborting discovery."""
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
    """True when ``rec`` satisfies a ``when`` gate: empty/absent gate always
    matches, else every ``when`` field's value (or list of values) must include
    the record's stringified value (case-insensitive). Shared by the
    contributed-``WorktreeAction`` gate and the D3 registered-``PivotAction``
    gate so both speak the identical ``when`` language."""
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
    """True when ``action`` should appear for worktree record ``rec``: its
    ``when`` is empty, or every ``when`` field matches the record (the record's
    value, stringified, is among the allowed value(s)). Thin wrapper over
    :func:`entry_matches`."""
    return entry_matches(action.when, rec)


def parse_config_sections(
    data: Mapping[str, object], *, name: str
) -> tuple[ConfigSection, ...]:
    """Parse a manifest's optional ``config_sections`` array (independent of
    whether the manifest also contributes a ``list`` pivot or ``worktree_actions``).
    Each entry declares a ``label`` and a ``run`` argv template opened on Enter.
    A malformed entry raises :class:`ManifestError` so the caller can skip the
    whole manifest's config sections without aborting discovery."""
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


def _encode_field_value(value: object) -> str:
    """Encode one collected field value for a ``--field name=value`` argument.

    A **multichoice** answer is a list -> a JSON array string (so members that
    contain commas survive round-trip and the consumer can ``json.loads`` it); a
    single value -> its string form; ``None`` -> empty.
    """
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
    """Substitute a form action's argv template in a single safe pass.

    Token namespaces:

    * ``{field.<name>}`` -> the operator's submitted value for ``<name>`` (from
      ``fields``), inserted **literally** -- it is never re-scanned, so a value
      that itself contains braces (``use {task_id} here``) is inert and can't
      inject another token. A list value (multichoice) is JSON-encoded.
    * ``{fields}`` as a **standalone** argv element -> expands to the full set of
      ``--field <name>=<value>`` pairs for *every* collected field (the general
      "submit all my answers" form, so a card can ask arbitrary questions without
      the manifest naming each). Empty-valued fields are still emitted (so the
      worker sees the operator left them blank).
    * ``{<token>}`` -> the entry/context value (same source as
      :func:`format_template`), e.g. ``{task_id}``.

    Per-token substitution is one pass with a custom :class:`string.Formatter`
    whose ``get_field`` treats the whole brace token as a single key (so
    ``field.<name>`` is a key lookup, not attribute access). Unknown tokens
    degrade to empty; a per-arg formatting error leaves that arg unchanged,
    mirroring :func:`format_template`'s defensive contract.
    """
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
            # Expand to one `--field name=value` pair per collected field.
            for name, value in fields.items():
                out.append("--field")
                out.append(f"{name}={_encode_field_value(value)}")
            continue
        try:
            out.append(fmt.vformat(arg, (), {}))
        except (KeyError, IndexError, ValueError):
            out.append(arg)
    return out
