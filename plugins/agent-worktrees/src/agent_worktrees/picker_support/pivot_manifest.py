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
import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from dropin_registry import (
    Finding,
    ScanAuthority,
    ScanSnapshot,
    WarningTracker,
)
from plugin_activation import ActivationReport, resolve_active_plugins

from .. import registry_paths
from .pivot_actions import (
    ConfigSection,
    ManifestError,
    WorktreeAction,
    _as_argv,
    parse_config_sections,
    parse_worktree_actions,
)

#: Environment override for the manifest directory (used by tests for hermetic
#: isolation, and available as an operator escape hatch).
PIVOTS_DIR_ENV = "AGENT_WORKTREES_PIVOTS_DIR"

#: Environment override for the copilot marketplace plugin-install root. Its
#: ``<marketplace>/<plugin>/pivots/*.json`` files are the *durable source* used
#: by :func:`ensure_pivots` to restore the runtime pivots dir after a reset.
PLUGINS_ROOT_ENV = "AGENT_WORKTREES_PLUGINS_DIR"

REGISTRY_NAME = "pivots"
MANAGED_SCHEMA_VERSION = 2
_PLUGIN_SOURCE_RE = re.compile(r"^[^@/\\\s]+@[^@/\\\s]+$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_KNOWN_LEGACY_PIVOTS = {
    "agent-bridge.json": "agent-bridge@copilot-extensions",
    "agent-codespaces.json": "agent-codespaces@copilot-extensions",
    "agent-containers.json": "agent-containers@copilot-extensions",
    "agent-dispatch.json": "agent-dispatch@copilot-extensions",
}

log = logging.getLogger("agent-worktrees")


def _resolve_activation() -> ActivationReport:
    """Resolve legacy activation or stand down after validating cell context."""
    if os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip():
        registry_paths.registry_root()
        return ActivationReport(
            authority=ScanAuthority.COMPLETE,
            decisions={},
        )
    return resolve_active_plugins()


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
    #: D3 -- optional visibility gate (same shape as a ``WorktreeAction.when``):
    #: the verb only appears for a row whose entry matches every field (value or
    #: list of allowed values), e.g. ``{"disposition": "in-use"}`` shows *Release*
    #: only on an in-use CodeSpace. ``None`` => always shown. Matched by
    #: :func:`entry_matches` at sub-menu build time.
    when: Mapping[str, object] | None = None
    #: D4 -- opt into **progress reporting**. When True the action's stdout is the
    #: NDJSON progress envelope (``{"type":"progress","pct":..,"msg":..}`` lines,
    #: then ``{"type":"done"}`` / ``{"type":"error"}``); the picker renders it live
    #: in the modal ``ProgressScreen`` instead of blocking on a single sync call.
    #: Default off => the original synchronous run (result in the status line).
    #: Ignored for an ``internal`` action.
    progress: bool = False
    #: A5 (steering seam) -- a ``kind:"form"`` action. When set, activating the
    #: verb opens a **native elicitation modal** that reads a ``request-input``
    #: field spec out of the selected entry (``fields_from`` -- a dotted path such
    #: as ``card.request_input``), lays out a widget per field (text/textarea/
    #: choice), and on submit substitutes ``{field.<name>}`` tokens in ``run`` and
    #: executes it (the general steer transport, e.g. ``agent-dispatch steer
    #: submit``). ``None`` => not a form action. Shape:
    #: ``{"fields_from": str, "title_from": str|None, "body_from": str|None}``.
    #: Mutually exclusive with ``internal``/``card``.
    form: Mapping[str, object] | None = None
    #: A5 (steering seam) -- a ``kind:"card"`` action. When set, activating the
    #: verb opens a **read-only scrollable card-detail modal** rendering the
    #: entry's card (title/status/link/body pulled from dotted paths). No
    #: subprocess is run. ``None`` => not a card action. Shape:
    #: ``{"title_from", "status_from", "link_from", "body_from"}`` (each a dotted
    #: path string). Mutually exclusive with ``internal``/``form``.
    card: Mapping[str, object] | None = None


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
    #: D2 -- opt into ``--stream``-style NDJSON. When True the runtime runs the
    #: ``list`` command with a trailing ``--stream`` and consumes a line-delimited
    #: ``{"type":"begin|row|summary|delta|removed|done|error"}`` envelope, so a
    #: slow/large provider paints progressively. Falls back to the one-shot
    #: ``list`` when the CLI doesn't understand ``--stream`` or emits a plain
    #: array. Default off => the original one-shot contract is unchanged.
    stream: bool = False
    #: D2 -- with ``stream``, hold the channel open for **live** updates: the
    #: provider keeps emitting ``delta``/``removed`` frames (e.g. a periodic
    #: re-scan + diff) and the runtime applies them in place so an open pivot
    #: repaints without a re-fetch. Ignored unless ``stream`` is also set.
    subscribe: bool = False
    #: Optional cheap visibility gate evaluated by the host before adding the
    #: pivot. The path is relative to the resolved state root and must name a
    #: file. This keeps configured, plugin-owned views out of unconfigured
    #: users' tab rows without executing the provider.
    visible_when_state_root_file: str | None = None

    @property
    def account_scoped(self) -> bool:
        """True when this pivot is a cross-machine (account/global) resource."""
        return self.scope in ("account", "global")

    @property
    def kind(self) -> str:
        return "registered"


@dataclass(frozen=True)
class PivotContribution:
    """All Picker surfaces contributed by one registry entry."""

    entry: Path
    entry_class: str
    owner: str | None
    pivot: RegisteredPivot | None
    worktree_actions: tuple[WorktreeAction, ...]
    config_sections: tuple[ConfigSection, ...]

    @property
    def identities(self) -> tuple[str, ...]:
        identities: list[str] = []
        if self.pivot is not None:
            identities.append(f"pivot:{self.pivot.label.casefold()}")
        identities.extend(
            f"worktree-action:{action.key.casefold()}"
            for action in self.worktree_actions
        )
        identities.extend(
            f"config-section:{section.key.casefold()}"
            for section in self.config_sections
        )
        return tuple(identities)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "entry": str(self.entry),
            "class": self.entry_class,
            "pivot": self.pivot.label if self.pivot else None,
            "worktree_actions": [action.key for action in self.worktree_actions],
            "config_sections": [section.key for section in self.config_sections],
        }
        if self.owner:
            result["owner"] = self.owner
        return result


@dataclass(frozen=True)
class PivotRegistryReport:
    """The one pivot registry result consumed by the Picker and doctor."""

    snapshot: ScanSnapshot[PivotContribution]
    active_entries: dict[str, PivotContribution]
    entry_classes: dict[str, str] = field(default_factory=dict)

    @property
    def authority(self) -> ScanAuthority:
        return self.snapshot.authority

    @property
    def findings(self) -> tuple[Finding, ...]:
        return self.snapshot.findings

    @property
    def contributions(self) -> list[PivotContribution]:
        return [self.active_entries[key] for key in sorted(self.active_entries)]

    @property
    def pivots(self) -> list[RegisteredPivot]:
        candidates = [
            contribution.pivot
            for contribution in self.contributions
            if contribution.pivot is not None
        ]
        state_root = (
            _resolve_state_root_path()
            if any(pivot.visible_when_state_root_file for pivot in candidates)
            else None
        )
        return [
            pivot
            for pivot in candidates
            if _pivot_is_visible(pivot, state_root=state_root)
        ]

    @property
    def worktree_actions(self) -> list[WorktreeAction]:
        return [
            action
            for contribution in self.contributions
            for action in contribution.worktree_actions
        ]

    @property
    def config_sections(self) -> list[ConfigSection]:
        return [
            section
            for contribution in self.contributions
            for section in contribution.config_sections
        ]

    def to_dict(self) -> dict[str, Any]:
        entries: list[dict[str, str]] = []
        for entry, decision in sorted(self.snapshot.decisions.items()):
            item = {
                "entry": entry,
                "status": decision.status.value,
                "class": self.entry_classes.get(entry, "unknown"),
            }
            if decision.value is not None and decision.value.owner:
                item["owner"] = decision.value.owner
            entries.append(item)
        return {
            "registry": REGISTRY_NAME,
            "authority": self.authority.value,
            "active_entries": [
                contribution.to_dict() for contribution in self.contributions
            ],
            "entries": entries,
            "findings": [finding.to_dict() for finding in self.findings],
        }


_LAST_KNOWN: dict[str, dict[str, PivotContribution]] = {}
_WARNING_TRACKER = WarningTracker()


def _opt_path(value: object, *, where: str) -> str | None:
    """Validate an optional dotted-path field (A5 form/card ``*_from``).

    ``None`` / absent => ``None``; a non-empty string is returned stripped; any
    other type raises :class:`ManifestError` so a malformed manifest is skipped.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where} must be a non-empty string when present")
    return value.strip()


def resolve_path(rec: Mapping[str, object] | None, dotted: str | None) -> object:
    """Resolve a dotted path (e.g. ``card.request_input``) against an entry rec.

    Walks nested mappings key-by-key; a missing key or a non-mapping mid-walk
    yields ``None`` rather than raising, so a form/card action degrades to an
    empty field spec / blank card body instead of breaking the picker. A path
    with no dot is a plain top-level lookup.
    """
    if not dotted or rec is None:
        return None
    cur: object = rec
    for part in dotted.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
    return cur


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
        # Action shapes, by ``kind``:
        #   * default / EXTERNAL CLI -- a `run` argv template (a subprocess);
        #   * `internal` -- a picker-navigation verb (`{"kind":"internal","verb":…}`)
        #     whose optional `args` become the ``run`` template the picker's handler
        #     substitutes; no subprocess is ever spawned for it;
        #   * `form` -- a native elicitation modal (A5): reads a request-input field
        #     spec from `fields_from` (a dotted entry path) and, on submit,
        #     substitutes `{field.<name>}` into `run` and runs it;
        #   * `card` -- a read-only scrollable card-detail modal (A5); no `run`.
        kind = a.get("kind")
        form: Mapping[str, object] | None = None
        card: Mapping[str, object] | None = None
        if kind == "internal":
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
        elif kind == "form":
            fields_from = a.get("fields_from")
            if not isinstance(fields_from, str) or not fields_from.strip():
                raise ManifestError(
                    f"`actions[{i}].fields_from` is required for a form action"
                )
            run = _as_argv(a.get("run"), where=f"`actions[{i}].run`")
            internal = None
            form = {
                "fields_from": fields_from.strip(),
                "title_from": _opt_path(
                    a.get("title_from"),
                    where=f"`actions[{i}].title_from`",
                ),
                "body_from": _opt_path(
                    a.get("body_from"),
                    where=f"`actions[{i}].body_from`",
                ),
            }
        elif kind == "card":
            run = ()
            internal = None
            card = {
                "title_from": _opt_path(
                    a.get("title_from"),
                    where=f"`actions[{i}].title_from`",
                )
                or "card.title",
                "status_from": _opt_path(
                    a.get("status_from"),
                    where=f"`actions[{i}].status_from`",
                )
                or "card.status",
                "link_from": _opt_path(
                    a.get("link_from"),
                    where=f"`actions[{i}].link_from`",
                )
                or "card.link",
                "body_from": _opt_path(
                    a.get("body_from"),
                    where=f"`actions[{i}].body_from`",
                )
                or "card.body",
            }
        else:
            run = _as_argv(a.get("run"), where=f"`actions[{i}].run`")
            internal = None
        a_when = a.get("when")
        if a_when is not None and not isinstance(a_when, Mapping):
            raise ManifestError(f"`actions[{i}].when` must be an object when present")
        a_progress = a.get("progress", False)
        if not isinstance(a_progress, bool):
            raise ManifestError(f"`actions[{i}].progress` must be a boolean when present")
        actions.append(
            PivotAction(
                key=key,
                label=a_label,
                run=run,
                confirm=bool(a.get("confirm", False)),
                description=str(a.get("description", "")),
                internal=internal,
                when=dict(a_when) if isinstance(a_when, Mapping) else None,
                progress=a_progress,
                form=form,
                card=card,
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

    stream = data.get("stream", False)
    if not isinstance(stream, bool):
        raise ManifestError("`stream` must be a boolean when present")
    subscribe = data.get("subscribe", False)
    if not isinstance(subscribe, bool):
        raise ManifestError("`subscribe` must be a boolean when present")
    visible_when = data.get("visible_when")
    if visible_when is None:
        state_root_file = None
    else:
        if not isinstance(visible_when, Mapping):
            raise ManifestError("`visible_when` must be an object when present")
        unsupported = set(visible_when) - {"state_root_file"}
        if unsupported:
            raise ManifestError(
                f"`visible_when` has unsupported keys: {', '.join(sorted(unsupported))}"
            )
        raw_state_root_file = visible_when.get("state_root_file")
        if not isinstance(raw_state_root_file, str) or not raw_state_root_file.strip():
            raise ManifestError(
                "`visible_when.state_root_file` must be a non-empty relative path"
            )
        normalized = raw_state_root_file.strip().replace("\\", "/")
        relative = PurePosixPath(normalized)
        if (
            relative.is_absolute()
            or re.match(r"^[A-Za-z]:", normalized)
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ManifestError(
                "`visible_when.state_root_file` must stay within the state root"
            )
        state_root_file = relative.as_posix()

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
        stream=stream,
        subscribe=subscribe,
        visible_when_state_root_file=state_root_file,
    )


def _resolve_state_root_path() -> Path | None:
    try:
        from agent_worktrees import config as config_module
        from agent_worktrees.state_root import resolve_state_root

        resolved = resolve_state_root(config_module.load_config())
        return Path(resolved.path).resolve() if resolved.path else None
    except (KeyError, OSError, RuntimeError, ValueError):
        return None


def _pivot_is_visible(
    pivot: RegisteredPivot,
    *,
    state_root: Path | None,
) -> bool:
    required = pivot.visible_when_state_root_file
    if required is None:
        return True
    if state_root is None:
        return False
    return state_root.joinpath(*PurePosixPath(required).parts).is_file()


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


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _compat_manifest_documents(
    directory: Path,
) -> list[tuple[Path, Mapping[str, object]]]:
    """Read explicit-directory manifests for parser-focused compatibility APIs."""
    try:
        if not directory.is_dir():
            return []
        files = sorted(
            path
            for path in directory.iterdir()
            if path.suffix == ".json" and path.is_file()
        )
    except OSError:
        return []
    documents: list[tuple[Path, Mapping[str, object]]] = []
    for path in files:
        try:
            data = _read_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, Mapping):
            documents.append((path, data))
    return documents


def discover_worktree_actions(
    base: str | os.PathLike[str] | None = None,
) -> list[WorktreeAction]:
    """Return active worktree actions.

    An explicit directory keeps the historical parser-only helper behavior for
    schema/unit callers that intentionally use synthetic command names.
    """
    out: list[WorktreeAction] = []
    for path, data in _compat_manifest_documents(pivots_dir(base)):
        try:
            out.extend(parse_worktree_actions(data, name=path.stem))
        except ManifestError:
            continue
    return out


def discover_config_sections(
    base: str | os.PathLike[str] | None = None,
) -> list[ConfigSection]:
    """Return active config sections, with parser-only explicit-dir support."""
    out: list[ConfigSection] = []
    for path, data in _compat_manifest_documents(pivots_dir(base)):
        try:
            out.extend(parse_config_sections(data, name=path.stem))
        except ManifestError:
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

