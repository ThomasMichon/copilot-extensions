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

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, cast

from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
    WarningTracker,
    scan_directory,
)
from plugin_activation import ActivationReport, ActivePlugin, resolve_active_plugins

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
    state_root: Path | None = None,
) -> bool:
    required = pivot.visible_when_state_root_file
    if required is None:
        return True
    root = state_root if state_root is not None else _resolve_state_root_path()
    if root is None:
        return False
    return root.joinpath(*PurePosixPath(required).parts).is_file()


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


class TargetUnusableError(ValueError):
    """A manifest command exists but cannot be executed safely."""


def _activation_from_plugins_root(root: Path) -> ActivationReport:
    """Build a synthetic active report for ``ensure_pivots`` unit tests."""
    decisions: dict[str, EntryDecision[ActivePlugin]] = {}
    try:
        manifests = sorted(root.glob("*/*/pivots/*.json"))
    except OSError:
        manifests = []
    for manifest in manifests:
        try:
            plugin_root = manifest.parents[1].resolve(strict=True)
        except OSError:
            continue
        marketplace = manifest.parents[2].name
        plugin = manifest.parents[1].name
        source = f"{plugin}@{marketplace}"
        decisions[source] = EntryDecision.active(
            ActivePlugin(
                source=source,
                name=plugin,
                marketplace=marketplace,
                root=plugin_root,
                scopes=("global",),
            )
        )
    return ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions=decisions,
    )


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _payload_command(root: Path | None, command: str) -> Path | None:
    if root is None or Path(command).name != command:
        return None
    candidates = [root / "bin" / command]
    if os.name == "nt":
        candidates = [
            root / "bin" / f"{command}.cmd",
            *candidates,
        ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _resolve_command(command: Sequence[str], *, root: Path | None = None) -> list[str]:
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise ManifestError("command must be a non-empty array of strings")
    first = command[0]
    payload = _payload_command(root, first)
    candidate = Path(first).expanduser()
    has_path = candidate.is_absolute() or "/" in first or "\\" in first
    if payload is not None:
        resolved = str(payload)
    elif has_path:
        resolved = str(candidate if candidate.is_absolute() or root is None else root / candidate)
    else:
        resolved = shutil.which(first)
    if not resolved:
        raise FileNotFoundError(first)
    target = Path(resolved)
    canonical = target.resolve(strict=True)
    info = canonical.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        raise TargetUnusableError("command must be a regular non-reparse file")
    if os.name != "nt" and not os.access(canonical, os.X_OK):
        raise TargetUnusableError("command is not executable")
    if root is not None and (
        payload is not None or (has_path and not candidate.is_absolute())
    ):
        try:
            canonical.relative_to(root)
        except ValueError as exc:
            raise TargetUnusableError(
                "relative command escapes the identity-verified plugin root"
            ) from exc
    if os.name == "nt" and canonical.suffix.casefold() == ".ps1":
        raise TargetUnusableError(
            "PowerShell scripts must be invoked through an executable wrapper"
        )
    return [str(canonical), *command[1:]]


def _rewrite_manifest_commands(
    raw: Mapping[str, object],
    *,
    root: Path | None,
    require_targets: bool,
) -> dict[str, object]:
    """Return a copy whose external argv heads are canonical absolute paths."""
    data = deepcopy(dict(raw))

    def rewrite(container: dict[str, object], key: str) -> None:
        value = container.get(key)
        if value is None:
            return
        try:
            container[key] = _resolve_command(
                cast(Sequence[str], value),
                root=root,
            )
        except (FileNotFoundError, OSError, TargetUnusableError):
            if require_targets:
                raise

    if isinstance(data.get("list"), Sequence) and not isinstance(
        data.get("list"), (str, bytes)
    ):
        rewrite(data, "list")
    for collection in ("actions", "worktree_actions", "config_sections"):
        entries = data.get(collection)
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict) or "run" not in item:
                continue
            if collection == "actions" and item.get("kind") in {"internal", "card"}:
                continue
            rewrite(item, "run")
    return data


def _managed_manifest_data(
    template: Mapping[str, object],
    *,
    source: str,
    root: Path,
    template_name: str,
    require_targets: bool,
) -> dict[str, object]:
    data = _rewrite_manifest_commands(
        template,
        root=root,
        require_targets=require_targets,
    )
    data["schema_version"] = MANAGED_SCHEMA_VERSION
    data["plugin"] = source
    data["plugin_root"] = str(root)
    data["template"] = template_name
    return data


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _exclusive_create_text(target: Path, content: str) -> bool:
    """Atomically publish ``content`` only when ``target`` is still absent."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _materialize_active_pivots(
    destination: Path,
    activation: ActivationReport,
) -> list[str]:
    """Refresh managed entries for active roots without deleting any file."""
    if activation.authority is ScanAuthority.INDETERMINATE:
        return []
    candidates: dict[str, list[tuple[str, Path, dict[str, object]]]] = {}
    for source, active in sorted(activation.active.items()):
        seen_roots: set[Path] = set()
        for selected in active.live_roots:
            if selected.root in seen_roots:
                continue
            seen_roots.add(selected.root)
            pivot_dir = selected.root / "pivots"
            try:
                templates = sorted(pivot_dir.glob("*.json"))
            except OSError:
                continue
            for template_path in templates:
                try:
                    info = template_path.lstat()
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or stat.S_ISLNK(info.st_mode)
                        or _is_reparse(info)
                    ):
                        continue
                    raw = _read_json(template_path)
                    if not isinstance(raw, dict):
                        continue
                    candidates.setdefault(template_path.name, []).append(
                        (source, selected.root, raw)
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue

    changed: list[str] = []
    for name, owners in sorted(candidates.items()):
        sources = {source for source, _root, _template in owners}
        if len(sources) != 1:
            continue
        # live_roots is precedence-ordered, so a project-local directory wins
        # over an installed copy without weakening cross-plugin collision safety.
        source, root, template = owners[0]
        try:
            data = _managed_manifest_data(
                template,
                source=source,
                root=root,
                template_name=name,
                require_targets=False,
            )
        except ManifestError:
            continue
        content = json.dumps(
            data,
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        ) + "\n"
        template_fingerprint = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()[:12]
        base_target = destination / name
        target = base_target
        try:
            if (
                base_target.exists()
                and base_target.read_text(encoding="utf-8") == content
            ):
                continue
        except OSError:
            pass
        if base_target.exists():
            target = destination / (
                f"{base_target.stem}.{template_fingerprint}{base_target.suffix}"
            )

        if target.exists():
            try:
                if target.read_text(encoding="utf-8") == content:
                    continue
            except OSError:
                pass
            continue

        try:
            if not _exclusive_create_text(target, content):
                continue
            changed.append(target.name)
        except OSError:
            continue
    return changed


def _remedy(
    entry: Path,
    *,
    entry_class: str,
    owner: str | None = None,
) -> str:
    if entry_class == "managed-plugin" and owner:
        return (
            f"Re-enable or reinstall {owner}, then reopen the Picker to refresh "
            f"{entry}; agent-worktrees will not remove it."
        )
    if entry_class == "legacy-plugin":
        return (
            f"Re-enable or update {owner or 'the contributing plugin'} and reopen "
            f"the Picker so {entry} is rewritten with current attribution."
        )
    if entry_class == "operator":
        return (
            f"Fix the operator-owned manifest at {entry}; "
            "agent-worktrees will not remove it."
        )
    return (
        f"Fix or remove the unrecognized legacy manifest at {entry} if no "
        "longer intended; agent-worktrees will not remove it."
    )


def _finding(
    entry: Path,
    reason: str,
    *,
    status: str = "inactive",
    target: str | Path | None = None,
    entry_class: str,
    owner: str | None = None,
    detail: str | None = None,
) -> Finding:
    return Finding(
        registry=REGISTRY_NAME,
        entry=str(entry),
        status=status,
        reason=reason,
        target=str(target) if target is not None else None,
        owner=owner,
        remedy=_remedy(entry, entry_class=entry_class, owner=owner),
        detail=detail,
    )


def _parse_contribution(
    data: Mapping[str, object],
    *,
    path: Path,
    entry_class: str,
    owner: str | None,
) -> PivotContribution:
    pivot = (
        parse_manifest(data, name=path.stem, source_path=str(path))
        if "list" in data
        else None
    )
    worktree_actions = parse_worktree_actions(data, name=path.stem)
    config_sections = parse_config_sections(data, name=path.stem)
    if pivot is None and not worktree_actions and not config_sections:
        raise ManifestError(
            "manifest must contribute a list pivot, worktree action, or config section"
        )
    return PivotContribution(
        entry=path,
        entry_class=entry_class,
        owner=owner,
        pivot=pivot,
        worktree_actions=worktree_actions,
        config_sections=config_sections,
    )


def _activation_decision(
    entry: Path,
    *,
    source: str,
    stored_root: Path | None,
    entry_class: str,
    activation: ActivationReport,
) -> tuple[ActivePlugin | None, EntryDecision[PivotContribution] | None]:
    source_decision = activation.decisions.get(source)
    if (
        activation.authority is ScanAuthority.INDETERMINATE
        or (
            source_decision is not None
            and source_decision.status is EntryStatus.INDETERMINATE
        )
    ):
        return None, EntryDecision.indeterminate(
            _finding(
                entry,
                "entry-indeterminate",
                status="indeterminate",
                target=stored_root,
                entry_class=entry_class,
                owner=source,
                detail="plugin activation or root evidence is indeterminate",
            )
        )
    if source_decision is None or source_decision.status is EntryStatus.INACTIVE:
        reason = "not-enabled"
        detail = "plugin is not enabled globally or in an adopted project"
        if source_decision is not None and source_decision.findings:
            finding = source_decision.findings[0]
            reason = finding.reason
            detail = finding.detail or detail
        return None, EntryDecision.inactive(
            _finding(
                entry,
                reason,
                target=stored_root,
                entry_class=entry_class,
                owner=source,
                detail=detail,
            )
        )
    return cast(ActivePlugin, source_decision.value), None


def _classify_managed(
    entry: Path,
    data: dict[str, object],
    *,
    activation: ActivationReport,
) -> EntryDecision[PivotContribution]:
    source = data.get("plugin")
    raw_root = data.get("plugin_root")
    template_name = data.get("template")
    if (
        not isinstance(source, str)
        or not _PLUGIN_SOURCE_RE.fullmatch(source)
        or not isinstance(raw_root, str)
        or not raw_root.strip()
        or not isinstance(template_name, str)
        or Path(template_name).name != template_name
        or not template_name.endswith(".json")
    ):
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                entry_class="managed-plugin",
                detail=(
                    "managed schema requires plugin name@marketplace, plugin_root, "
                    "and template"
                ),
            )
        )
    stored_root = Path(raw_root).expanduser()
    if not stored_root.is_absolute():
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                target=stored_root,
                entry_class="managed-plugin",
                owner=source,
                detail="plugin_root must be absolute",
            )
        )
    active, verdict = _activation_decision(
        entry,
        source=source,
        stored_root=stored_root,
        entry_class="managed-plugin",
        activation=activation,
    )
    if verdict is not None:
        return verdict
    active = cast(ActivePlugin, active)
    try:
        canonical_root = stored_root.resolve(strict=True)
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=stored_root,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "entry-indeterminate",
                status="indeterminate",
                target=stored_root,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    live_roots = {selected.root for selected in active.live_roots}
    if canonical_root not in live_roots:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=canonical_root,
                entry_class="managed-plugin",
                owner=source,
                detail=(
                    "manifest root differs from authoritative live plugin roots "
                    + ", ".join(str(root) for root in sorted(live_roots))
                ),
            )
        )

    template_path = canonical_root / "pivots" / template_name
    try:
        template = _read_json(template_path)
        if not isinstance(template, dict):
            raise ManifestError("plugin pivot template must be a JSON object")
        expected = _managed_manifest_data(
            template,
            source=source,
            root=canonical_root,
            template_name=template_name,
            require_targets=True,
        )
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "missing-target",
                target=str(exc.filename or exc),
                entry_class="managed-plugin",
                owner=source,
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ManifestError) as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=template_path,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except TargetUnusableError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "target-unusable",
                target=template_path,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "target-unusable",
                status="indeterminate",
                target=template_path,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    if data != expected:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=template_path,
                entry_class="managed-plugin",
                owner=source,
                detail="runtime manifest differs from the current plugin template",
            )
        )
    try:
        contribution = _parse_contribution(
            expected,
            path=entry,
            entry_class="managed-plugin",
            owner=source,
        )
    except ManifestError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    if activation.decisions[source].status is EntryStatus.ACTIVE_WITH_ADVISORY:
        advisories = tuple(
            replace(
                finding,
                registry=REGISTRY_NAME,
                entry=str(entry),
                status="active-with-advisory",
                owner=source,
                remedy=_remedy(
                    entry,
                    entry_class="managed-plugin",
                    owner=source,
                ),
            )
            for finding in activation.decisions[source].findings
        )
        return EntryDecision.advisory(contribution, *advisories)
    return EntryDecision.active(contribution)


def _classify_legacy(
    entry: Path,
    data: dict[str, object],
    *,
    source: str,
    activation: ActivationReport,
) -> EntryDecision[PivotContribution]:
    active, verdict = _activation_decision(
        entry,
        source=source,
        stored_root=None,
        entry_class="legacy-plugin",
        activation=activation,
    )
    if verdict is not None:
        return verdict
    active = cast(ActivePlugin, active)
    template_path = active.root / "pivots" / entry.name
    matched_root: Path | None = None
    for selected in active.live_roots:
        candidate = selected.root / "pivots" / entry.name
        try:
            if _read_json(candidate) == data:
                matched_root = selected.root
                template_path = candidate
                break
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        except OSError as exc:
            return EntryDecision.indeterminate(
                _finding(
                    entry,
                    "target-unusable",
                    status="indeterminate",
                    target=candidate,
                    entry_class="legacy-plugin",
                    owner=source,
                    detail=str(exc),
                )
            )
    if matched_root is None:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=template_path,
                entry_class="legacy-plugin",
                owner=source,
                detail="legacy manifest differs from every active plugin template",
            )
        )
    try:
        resolved = _rewrite_manifest_commands(
            data,
            root=matched_root,
            require_targets=True,
        )
        contribution = _parse_contribution(
            resolved,
            path=entry,
            entry_class="legacy-plugin",
            owner=source,
        )
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "missing-target",
                target=str(exc.filename or exc),
                entry_class="legacy-plugin",
                owner=source,
            )
        )
    except TargetUnusableError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "target-unusable",
                target=template_path,
                entry_class="legacy-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "target-unusable",
                status="indeterminate",
                target=template_path,
                entry_class="legacy-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ManifestError) as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                target=template_path,
                entry_class="legacy-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    return EntryDecision.advisory(
        contribution,
        _finding(
            entry,
            "legacy-unattributed",
            status="active-with-advisory",
            target=template_path,
            entry_class="legacy-plugin",
            owner=source,
            detail="legacy manifest remains active during attribution migration",
        ),
    )


def _classify_unattributed(
    entry: Path,
    data: dict[str, object],
    *,
    entry_class: str,
    advisory: bool,
) -> EntryDecision[PivotContribution]:
    try:
        resolved = _rewrite_manifest_commands(
            data,
            root=None,
            require_targets=True,
        )
        contribution = _parse_contribution(
            resolved,
            path=entry,
            entry_class=entry_class,
            owner=None,
        )
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "missing-target",
                target=str(exc.filename or exc),
                entry_class=entry_class,
            )
        )
    except TargetUnusableError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "target-unusable",
                entry_class=entry_class,
                detail=str(exc),
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "entry-indeterminate",
                status="indeterminate",
                entry_class=entry_class,
                detail=str(exc),
            )
        )
    except ManifestError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                entry_class=entry_class,
                detail=str(exc),
            )
        )
    if not advisory:
        return EntryDecision.active(contribution)
    return EntryDecision.advisory(
        contribution,
        _finding(
            entry,
            "legacy-unattributed",
            status="active-with-advisory",
            entry_class=entry_class,
            detail="unattributed schema-v1 manifest remains active for compatibility",
        ),
    )


def _withdraw_confirmed_disappearances(
    snapshot: ScanSnapshot[PivotContribution],
) -> ScanSnapshot[PivotContribution]:
    if snapshot.authority is not ScanAuthority.COMPLETE:
        return snapshot
    decisions = dict(snapshot.decisions)
    findings = list(snapshot.findings)
    for key, decision in tuple(decisions.items()):
        if decision.status is not EntryStatus.INDETERMINATE:
            continue
        try:
            Path(key).lstat()
        except FileNotFoundError:
            del decisions[key]
            findings = [finding for finding in findings if finding.entry != key]
        except OSError:
            continue
    return replace(
        snapshot,
        decisions=decisions,
        findings=tuple(findings),
    )


def scan_pivot_registry(
    base: str | os.PathLike[str] | None = None,
    *,
    previous: Mapping[str, PivotContribution] | None = None,
    activation_report: ActivationReport | None = None,
    materialize: bool = True,
) -> PivotRegistryReport:
    """Scan, classify, reconcile, and de-duplicate Picker contributions."""
    directory = pivots_dir(base)
    activation = activation_report or resolve_active_plugins()
    if materialize:
        _materialize_active_pivots(directory, activation)
    entry_classes: dict[str, str] = {}

    def classify(entry: Path) -> EntryDecision[PivotContribution]:
        try:
            data = _read_json(entry)
        except UnicodeDecodeError as exc:
            entry_classes[str(entry)] = "unknown"
            return EntryDecision.inactive(
                _finding(
                    entry,
                    "invalid-entry",
                    entry_class="unknown",
                    detail=f"manifest is not valid UTF-8: {exc}",
                )
            )
        except json.JSONDecodeError as exc:
            entry_classes[str(entry)] = "unknown"
            return EntryDecision.inactive(
                _finding(
                    entry,
                    "invalid-entry",
                    entry_class="unknown",
                    detail=f"manifest is not valid JSON: {exc}",
                )
            )
        if not isinstance(data, dict):
            entry_classes[str(entry)] = "unknown"
            return EntryDecision.inactive(
                _finding(
                    entry,
                    "invalid-entry",
                    entry_class="unknown",
                    detail="manifest root must be a JSON object",
                )
            )
        schema = data.get("schema_version")
        if schema == MANAGED_SCHEMA_VERSION:
            entry_classes[str(entry)] = "managed-plugin"
            return _classify_managed(entry, data, activation=activation)
        if schema == 1:
            source = _KNOWN_LEGACY_PIVOTS.get(entry.name)
            if source:
                entry_classes[str(entry)] = "legacy-plugin"
                return _classify_legacy(
                    entry,
                    data,
                    source=source,
                    activation=activation,
                )
            entry_classes[str(entry)] = "unknown-legacy"
            return _classify_unattributed(
                entry,
                data,
                entry_class="unknown-legacy",
                advisory=True,
            )
        if schema is None:
            entry_classes[str(entry)] = "operator"
            return _classify_unattributed(
                entry,
                data,
                entry_class="operator",
                advisory=False,
            )
        entry_classes[str(entry)] = "unknown"
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                entry_class="unknown",
                detail=f"unsupported schema_version {schema!r}",
            )
        )

    snapshot = scan_directory(
        directory,
        classify,
        registry=REGISTRY_NAME,
        suffixes=(".json",),
    )
    snapshot = _withdraw_confirmed_disappearances(snapshot)
    if snapshot.findings:
        snapshot = replace(
            snapshot,
            findings=tuple(
                replace(
                    finding,
                    remedy=(
                        (
                            f"Restore readable access to {directory}, then run "
                            "`agent-worktrees doctor` again; current pivots are retained."
                        )
                        if finding.reason == "registry-indeterminate"
                        else _remedy(
                            Path(finding.entry),
                            entry_class=entry_classes.get(
                                finding.entry, "unknown"
                            ),
                            owner=finding.owner,
                        )
                    ),
                )
                if not finding.remedy
                else finding
                for finding in snapshot.findings
            ),
        )

    try:
        root_key = str(directory.expanduser().resolve(strict=False))
    except OSError:
        root_key = os.path.abspath(os.path.expanduser(str(directory)))
    prior = dict(previous) if previous is not None else dict(
        _LAST_KNOWN.get(root_key, {})
    )
    reconciled = snapshot.reconcile(prior)
    active_entries = dict(reconciled)

    decisions = dict(snapshot.decisions)
    findings = list(snapshot.findings)
    owners: dict[str, str] = {}
    precedence = {
        "operator": 0,
        "managed-plugin": 1,
        "legacy-plugin": 2,
        "unknown-legacy": 3,
    }
    ordered_entries = sorted(
        active_entries,
        key=lambda key: (
            precedence.get(active_entries[key].entry_class, 4),
            key,
        ),
    )
    for key in ordered_entries:
        contribution = active_entries[key]
        duplicate_of = next(
            (owners[identity] for identity in contribution.identities if identity in owners),
            None,
        )
        if duplicate_of is None:
            for identity in contribution.identities:
                owners[identity] = key
            continue
        active_entries.pop(key)
        current = decisions.get(key)
        if current is None or current.status is EntryStatus.INDETERMINATE:
            continue
        duplicate = _finding(
            Path(key),
            "duplicate",
            target=duplicate_of,
            entry_class=entry_classes.get(key, "unknown"),
            owner=contribution.owner,
            detail="a prior active entry already claims one of this manifest's identities",
        )
        decisions[key] = EntryDecision.inactive(duplicate)
        findings = [finding for finding in findings if finding.entry != key]
        findings.append(duplicate)
    snapshot = replace(
        snapshot,
        decisions=decisions,
        findings=tuple(findings),
    )
    if previous is None:
        _LAST_KNOWN[root_key] = reconciled
    return PivotRegistryReport(
        snapshot=snapshot,
        active_entries=active_entries,
        entry_classes=entry_classes,
    )


def warn_pivot_findings(report: PivotRegistryReport) -> None:
    """Emit bounded, fingerprint-deduplicated Picker registry warnings."""
    batch = _WARNING_TRACKER.select(report.findings)
    for finding in batch.emitted:
        target = f" target={finding.target}" if finding.target else ""
        log.warning(
            "%s entry=%s reason=%s%s; run `agent-worktrees doctor`",
            REGISTRY_NAME,
            finding.entry,
            finding.reason,
            target,
        )
    if batch.suppressed:
        log.warning(
            "%s: %d additional findings suppressed; run `agent-worktrees doctor`",
            REGISTRY_NAME,
            batch.suppressed,
        )


def ensure_pivots(
    base: str | os.PathLike[str] | None = None,
    plugins_root: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Materialize active plugin manifests as attributed runtime entries.

    Cached installed payloads are never authority by themselves. Only roots
    returned by :func:`resolve_active_plugins` may create or refresh a managed
    pivot entry. Existing operator/unknown entries are never overwritten.

    ``plugins_root`` remains a test-only compatibility input. When supplied,
    candidates are discovered from that synthetic tree and treated as active.
    """
    activation = (
        _activation_from_plugins_root(Path(plugins_root))
        if plugins_root is not None
        else resolve_active_plugins()
    )
    return _materialize_active_pivots(pivots_dir(base), activation)


def discover_pivots(base: str | os.PathLike[str] | None = None) -> list[RegisteredPivot]:
    """Return active pivots, with parser-only explicit-dir support."""
    candidates: list[RegisteredPivot] = []
    for path, data in _compat_manifest_documents(pivots_dir(base)):
        if "list" not in data:
            continue
        try:
            candidates.append(
                parse_manifest(data, name=path.stem, source_path=str(path))
            )
        except ManifestError:
            continue
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


# Kept for symmetry with maintenance.py's module layout; the engine imports the
# functions above directly.
__all__ = [
    "Column",
    "ConfigSection",
    "ManifestError",
    "PivotAction",
    "PivotContribution",
    "PivotRegistryReport",
    "RegisteredPivot",
    "WorktreeAction",
    "discover_config_sections",
    "discover_pivots",
    "discover_worktree_actions",
    "ensure_pivots",
    "entry_matches",
    "format_form_template",
    "format_template",
    "installed_plugins_dir",
    "order_pivots",
    "parse_config_sections",
    "parse_list_payload",
    "parse_manifest",
    "parse_worktree_actions",
    "pivots_dir",
    "resolve_path",
    "scan_pivot_registry",
    "warn_pivot_findings",
    "worktree_action_matches",
]
