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
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

#: Environment override for the manifest directory (used by tests for hermetic
#: isolation, and available as an operator escape hatch).
PIVOTS_DIR_ENV = "AGENT_WORKTREES_PIVOTS_DIR"

#: Environment override for the copilot marketplace plugin-install root. Its
#: ``<marketplace>/<plugin>/pivots/*.json`` files are the *durable source* used
#: by :func:`ensure_pivots` to restore the runtime pivots dir after a reset.
PLUGINS_ROOT_ENV = "AGENT_WORKTREES_PLUGINS_DIR"


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
    #: When set, this is an *internal* (picker-navigation) action handled by the
    #: picker itself (e.g. ``"jump-host"``) rather than an external CLI. ``run``
    #: then carries the verb's argument template instead of a command. See
    #: ``engine.PickerScreen._internal_pivot_action`` for the handler table.
    internal: str | None = None


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


@dataclass(frozen=True)
class Column:
    """One declarative column in a registered pivot's table view (D1).

    A pivot may declare an ordered ``columns`` list so the generic renderer shows
    a real table (id / state / age / …) instead of only the flat id/title/badge
    ``entry`` shape. ``key`` names the field in each entry dict; ``header`` is the
    column label (defaults to ``key``); ``width`` clips/pads the cell (``None``
    lets the renderer size it); ``align`` is ``l``/``r``/``c``; ``style`` is an
    optional Rich style hint the renderer may apply. Purely declarative -- an
    unknown ``key`` degrades to an empty cell, never an error.
    """

    key: str
    header: str
    width: int | None = None
    align: str = "l"
    style: str | None = None
    #: Optional named palette for **per-value** cell colouring (reusing the
    #: picker's own vocabulary, e.g. ``"state"`` -> the Worktrees state palette).
    #: The renderer maps the cell value through the palette; ``style`` is the
    #: fallback when the value isn't in the palette.
    palette: str | None = None


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
    #: D1 -- declarative table columns (empty => fall back to the id/title/badge
    #: ``entry`` render) and a summary/header-line template whose ``{token}``s are
    #: filled from the ``list`` payload's ``summary`` object (e.g. budget
    #: headroom). Both default off so an older manifest is unaffected.
    columns: tuple[Column, ...] = ()
    summary_template: str | None = None
    #: Data scope. ``"machine"`` (default) rides the machine sub-nav -- the pivot's
    #: ``list`` runs per selected machine (agent-dispatch, containers). ``"account"``
    #: (or ``"global"``) is a cross-machine shared resource (CodeSpaces): the list
    #: runs **once**, the machine sub-nav is ignored for scoping, and the header
    #: counts items, not "on <machine>".
    scope: str = "machine"
    #: Optional entry key to **group** a columns table by (``entry.group``): rows
    #: sharing a value are rendered under a ``── <value> ──`` section header (e.g.
    #: ``repo @ account``). ``None`` => a flat table.
    group_field: str | None = None

    @property
    def account_scoped(self) -> bool:
        """True when this pivot is a cross-machine (account/global) resource."""
        return self.scope in ("account", "global")

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
    group_field = _entry_str("group", None)

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
        a_key = a.get("key")
        key = str(a_key) if isinstance(a_key, str) and a_key else f"action{i}"
        # Two action shapes: an EXTERNAL CLI (`run` argv template, the default)
        # or an INTERNAL picker-navigation verb (`{"kind":"internal","verb":…}`).
        # An internal action's optional `args` become the ``run`` template the
        # picker's handler substitutes; no subprocess is ever spawned for it.
        if a.get("kind") == "internal":
            verb = a.get("verb")
            if not isinstance(verb, str) or not verb.strip():
                raise ManifestError(
                    f"`actions[{i}].verb` is required for an internal action"
                )
            args = a.get("args", [])
            if isinstance(args, Sequence) and not isinstance(args, (str, bytes)):
                run = tuple(str(x) for x in args)
            else:
                run = ()
            internal: str | None = verb.strip()
        else:
            run = _as_argv(a.get("run"), where=f"`actions[{i}].run`")
            internal = None
        actions.append(
            PivotAction(
                key=key,
                label=a_label,
                run=run,
                confirm=bool(a.get("confirm", False)),
                description=str(a.get("description", "")),
                internal=internal,
            )
        )

    columns = _parse_columns(data.get("columns"))

    summary = data.get("summary")
    if summary is None:
        summary_template: str | None = None
    elif isinstance(summary, str):
        summary_template = summary
    else:
        raise ManifestError("`summary` must be a string template when present")

    scope = data.get("scope", "machine")
    if not isinstance(scope, str) or scope not in ("machine", "account", "global"):
        raise ManifestError("`scope` must be one of machine/account/global")

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
        columns=columns,
        summary_template=summary_template,
        scope=scope,
        group_field=group_field,
    )


_VALID_ALIGN = {"l", "r", "c"}


def _parse_columns(raw: object) -> tuple[Column, ...]:
    """Parse a manifest's optional ``columns`` array into :class:`Column`\\ s (D1).

    Absent => ``()`` (the renderer falls back to the id/title/badge ``entry``
    shape). Each column needs a string ``key``; ``header`` defaults to ``key``;
    ``width`` must be a positive int when present; ``align`` is ``l``/``r``/``c``
    (default ``l``); ``style`` is an optional string hint. A structural problem
    raises :class:`ManifestError` so the caller can skip the whole manifest.
    """
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ManifestError("`columns` must be an array when present")
    cols: list[Column] = []
    for i, c in enumerate(raw):
        if not isinstance(c, Mapping):
            raise ManifestError(f"`columns[{i}]` must be an object")
        key = c.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ManifestError(f"`columns[{i}].key` is required")
        header = c.get("header", key)
        if not isinstance(header, str):
            raise ManifestError(f"`columns[{i}].header` must be a string")
        width_raw = c.get("width")
        if width_raw is None:
            width: int | None = None
        elif isinstance(width_raw, bool) or not isinstance(width_raw, int) or width_raw <= 0:
            raise ManifestError(f"`columns[{i}].width` must be a positive integer")
        else:
            width = width_raw
        align = c.get("align", "l")
        if not isinstance(align, str) or align not in _VALID_ALIGN:
            raise ManifestError(f"`columns[{i}].align` must be one of l/r/c")
        style = c.get("style")
        if style is not None and not isinstance(style, str):
            raise ManifestError(f"`columns[{i}].style` must be a string")
        palette = c.get("palette")
        if palette is not None and not isinstance(palette, str):
            raise ManifestError(f"`columns[{i}].palette` must be a string")
        cols.append(
            Column(
                key=key.strip(),
                header=header,
                width=width,
                align=align,
                style=style,
                palette=palette,
            )
        )
    return tuple(cols)


def parse_list_payload(data: object) -> tuple[list[dict], dict]:
    """Normalize a registered pivot's ``list`` output into ``(rows, summary)`` (D1).

    Two accepted shapes, so the summary/header line (D1) is expressible without
    breaking the original bare-array contract:

    * a bare JSON **array** of entry objects -> ``(rows, {})`` (back-compat);
    * a JSON **object** ``{"entries": [...], "summary": {...}}`` -> rows from
      ``entries`` and the ``summary`` dict threaded to the header-line template.

    Defensive: non-dict rows are dropped; a non-list ``entries`` or non-dict
    ``summary`` degrades to empty rather than raising, so a malformed payload
    never breaks the picker.
    """
    if isinstance(data, Mapping):
        raw_rows = data.get("entries", [])
        raw_summary = data.get("summary", {})
    else:
        raw_rows = data
        raw_summary = {}
    rows = [r for r in raw_rows if isinstance(r, dict)] if isinstance(raw_rows, list) else []
    summary = dict(raw_summary) if isinstance(raw_summary, Mapping) else {}
    return rows, summary


def parse_worktree_actions(
    data: Mapping[str, object], *, name: str
) -> tuple["WorktreeAction", ...]:
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


def discover_worktree_actions(
    base: str | os.PathLike[str] | None = None,
) -> list["WorktreeAction"]:
    """Scan the manifest directory and return all contributed worktree actions,
    in stable (filename, declared) order. A manifest with **no** ``list`` pivot
    is still honored here (a layer may contribute only worktree actions). Any
    unreadable/malformed manifest is skipped -- never fatal."""
    directory = pivots_dir(base)
    try:
        if not directory.is_dir():
            return []
        files = sorted(
            p for p in directory.iterdir() if p.suffix == ".json" and p.is_file()
        )
    except OSError:
        return []
    out: list[WorktreeAction] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, Mapping):
                continue
            out.extend(parse_worktree_actions(data, name=path.stem))
        except (OSError, ValueError):
            continue
    return out


def worktree_action_matches(
    action: "WorktreeAction", rec: Mapping[str, object]
) -> bool:
    """True when ``action`` should appear for worktree record ``rec``: its
    ``when`` is empty, or every ``when`` field matches the record (the record's
    value, stringified, is among the allowed value(s))."""
    when = action.when
    if not when:
        return True
    for field, allowed in when.items():
        values = allowed if isinstance(allowed, (list, tuple)) else [allowed]
        allowed_str = {str(v).lower() for v in values}
        if str(rec.get(field)).lower() not in allowed_str:
            return False
    return True


def parse_config_sections(
    data: Mapping[str, object], *, name: str
) -> tuple["ConfigSection", ...]:
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


def discover_config_sections(
    base: str | os.PathLike[str] | None = None,
) -> list["ConfigSection"]:
    """Scan the manifest directory and return all contributed config sections,
    in stable (filename, declared) order. A manifest with **no** ``list`` pivot
    is still honored here (a layer may contribute only config sections). Any
    unreadable/malformed manifest is skipped -- never fatal."""
    directory = pivots_dir(base)
    try:
        if not directory.is_dir():
            return []
        files = sorted(
            p for p in directory.iterdir() if p.suffix == ".json" and p.is_file()
        )
    except OSError:
        return []
    out: list[ConfigSection] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, Mapping):
                continue
            out.extend(parse_config_sections(data, name=path.stem))
        except (OSError, ValueError):
            continue
    return out


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


def installed_plugins_dir(base: str | os.PathLike[str] | None = None) -> Path:
    """The copilot marketplace plugin-install root.

    An explicit ``base``, else the :data:`PLUGINS_ROOT_ENV` override, else
    ``~/.copilot/installed-plugins``. The copilot CLI writes this tree when a
    plugin installs, and -- unlike ``~/.agent-worktrees/`` -- it *survives* an
    agent-worktrees runtime-root reset, so it is the durable source from which
    :func:`ensure_pivots` restores lost pivot manifests.
    """
    if base is not None:
        return Path(base)
    env = os.environ.get(PLUGINS_ROOT_ENV)
    if env:
        return Path(env)
    from .. import config

    return config._home() / ".copilot" / "installed-plugins"


def ensure_pivots(
    base: str | os.PathLike[str] | None = None,
    plugins_root: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Self-heal the runtime pivots dir from the marketplace install tree.

    Contributor plugins ship their pivot manifest inside their own package
    (``<plugin>/pivots/<name>.json``) and copy it into ``~/.agent-worktrees/
    pivots/`` only on *their own* install/update. Resetting the agent-worktrees
    runtime root therefore silently drops every contributed pivot (e.g. the
    ``Tasks`` pivot) until each plugin happens to be reinstalled (#2180).

    This restores them with no contributor involvement: it scans the copilot
    marketplace plugin-install root (:func:`installed_plugins_dir`) for
    ``<marketplace>/<plugin>/pivots/*.json`` and copies any manifest that is
    **missing** from the runtime pivots dir. It is idempotent (restore-only: an
    existing manifest -- including one a newer contributor install force-wrote --
    is never clobbered) and fully best-effort: every error is swallowed so the
    picker always opens.

    Returns the manifest filenames restored (for logging/tests); ``[]`` when
    nothing was missing or the source tree is absent.
    """
    dest = pivots_dir(base)
    source_root = installed_plugins_dir(plugins_root)
    restored: list[str] = []
    try:
        if not source_root.is_dir():
            return []
        # Layout: <marketplace>/<plugin>/pivots/<name>.json
        sources = sorted(source_root.glob("*/*/pivots/*.json"))
    except OSError:
        return []
    for src in sources:
        try:
            if not src.is_file():
                continue
            target = dest / src.name
            if target.exists():
                continue
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
            restored.append(src.name)
        except OSError:
            # A single unreadable/unwritable manifest must not sink the rest.
            continue
    return restored


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
    "Column",
    "ConfigSection",
    "ManifestError",
    "PivotAction",
    "RegisteredPivot",
    "WorktreeAction",
    "discover_config_sections",
    "discover_pivots",
    "discover_worktree_actions",
    "ensure_pivots",
    "format_template",
    "installed_plugins_dir",
    "order_pivots",
    "parse_config_sections",
    "parse_list_payload",
    "parse_manifest",
    "parse_worktree_actions",
    "pivots_dir",
    "worktree_action_matches",
]
