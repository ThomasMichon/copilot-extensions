"""Resolve which CodeSpace-scoped plugins a CodeSpace needs, from the harness's
installed plugin arrangement.

A **harness-side** plugin declares the plugins that should be installed *into a
CodeSpace on its account* via a custom ``codespacePlugins`` array in its
``plugin.json``. That field is an *unrecognized* top-level manifest field, so
the core Copilot CLI ignores it; ``agent-codespaces`` is the consumer that reads
it. Each entry::

    {
      "source": "odsp-web-codespace@dev-tmichon",   // install source
      "enable": true,                                // enable after install
      "forWorkspaceRepo": "odsp-microsoft/odsp-web"  // optional scope filter
    }

``forWorkspaceRepo`` (string, list, or omitted) scopes an entry to CodeSpaces of
a given workspace repo; omitting it means the entry applies to *every* CodeSpace
this harness provisions. A harness plugin that uses ``codespacePlugins`` is
expected to declare a dependency on ``agent-codespaces`` (the honorer).

This module is the **discovery / resolution** half only: it sweeps the installed
harness plugins, collects and filters their declarations, and returns the
resolved, de-duplicated set (:func:`resolve_codespace_plugins`). Actually
installing / enabling the resolved plugins *inside* the CodeSpace — the
register-into-CodeSpace flow — is a separate concern that consumes this output.

Run ``python -m agent_codespaces.codespace_plugins <owner/repo>`` to preview
what a given CodeSpace would receive.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FIELD = "codespacePlugins"


# --------------------------------------------------------------------------
# Filesystem indirection (overridable in tests)
# --------------------------------------------------------------------------

def _home() -> Path:
    return Path.home()


def default_copilot_home() -> Path:
    """The ``~/.copilot`` directory that holds installed plugin payloads."""
    return _home() / ".copilot"


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON file, returning ``None`` on any error or absence."""
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# --------------------------------------------------------------------------
# Resolved spec
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CodespacePluginSpec:
    """One CodeSpace-scoped plugin the harness wants injected into a CodeSpace."""

    source: str
    enable: bool = True
    for_workspace_repo: tuple[str, ...] = field(default_factory=tuple)
    declared_by: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_global(self) -> bool:
        """True when the entry applies to every CodeSpace (no repo filter)."""
        return not self.for_workspace_repo

    @property
    def plugin_ref(self) -> str:
        """Best-effort ``name@marketplace`` (or the raw source if not that form)."""
        return self.source

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "enable": self.enable,
            "forWorkspaceRepo": list(self.for_workspace_repo),
            "declaredBy": list(self.declared_by),
        }


# --------------------------------------------------------------------------
# Installed-plugin discovery
# --------------------------------------------------------------------------

def iter_installed_manifests(
    copilot_home: Path | None = None,
) -> Iterator[tuple[str, Path, dict[str, Any]]]:
    """Yield ``(plugin_name, payload_dir, manifest)`` for every installed plugin.

    Walks ``<copilot_home>/installed-plugins/<marketplace>/<plugin>/plugin.json``
    and the ``_direct`` layout. ``plugin_name`` is the manifest ``name`` when
    present, else the directory name.
    """
    root = (copilot_home or default_copilot_home()) / "installed-plugins"
    if not root.is_dir():
        return
    for marketplace_dir in sorted(root.iterdir()):
        if not marketplace_dir.is_dir():
            continue
        for plugin_dir in sorted(marketplace_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest = _read_json(plugin_dir / "plugin.json")
            if manifest is None:
                continue
            name = str(manifest.get("name") or plugin_dir.name)
            yield name, plugin_dir, manifest


def plugin_names_from_enabled(ep: Any) -> set[str] | None:
    """Plugin names from an ``enabledPlugins`` map (keys ``"<name>@<marketplace>"``).

    Returns ``None`` when ``ep`` is not a dict, signalling "cannot determine --
    do not filter on enablement". Falsy entries are dropped.
    """
    if not isinstance(ep, dict):
        return None
    names: set[str] = set()
    for spec, val in ep.items():
        if not val or not isinstance(spec, str):
            continue
        names.add(spec.partition("@")[0])
    return names


def enabled_plugin_names(copilot_home: Path | None = None) -> set[str] | None:
    """Plugin names enabled in the harness *user* settings, or ``None`` if unknown.

    Reads ``<copilot_home>/settings.json`` ``enabledPlugins``. Returns ``None``
    when the settings file is absent or has no ``enabledPlugins`` map, signalling
    "cannot determine — do not filter on enablement".

    NOTE: this is the legacy *user-settings* source. The dispatch/register path
    now prefers the **repo-scoped** enablement (``config.repo_copilot_settings``
    → :func:`plugin_names_from_enabled`); this remains for back-compat and
    callers that pass an explicit ``enabled_names`` are not affected.
    """
    data = _read_json((copilot_home or default_copilot_home()) / "settings.json")
    if not isinstance(data, dict):
        return None
    return plugin_names_from_enabled(data.get("enabledPlugins"))


# --------------------------------------------------------------------------
# Manifest parsing + filtering
# --------------------------------------------------------------------------

def _as_repo_filters(value: Any) -> tuple[str, ...]:
    """Normalise a ``forWorkspaceRepo`` value to a tuple of filter strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if isinstance(v, str) and v.strip())
    return ()


def repo_matches(filters: tuple[str, ...], workspace_repo: str | None) -> bool:
    """Whether a repo filter set applies to ``workspace_repo``.

    An empty filter set means "global" (always applies). Otherwise the workspace
    repo must match at least one filter, case-insensitively, as an exact string
    or an ``fnmatch`` glob (e.g. ``"odsp-microsoft/*"``). An unknown workspace
    repo (``None``) matches only the global (empty-filter) case.
    """
    if not filters:
        return True
    if not workspace_repo:
        return False
    target = workspace_repo.strip().lower()
    for f in filters:
        pat = f.strip().lower()
        if target == pat or fnmatch.fnmatch(target, pat):
            return True
    return False


def plugin_name(source: str) -> str:
    """The plugin name from a source (``name@marketplace`` -> ``name``)."""
    return (source or "").strip().partition("@")[0].strip()


def is_harness_plugin(source: str) -> bool:
    """True for a ``<reponame>-harness[-*]`` plugin.

    Harness plugins are central-harness-only and must NEVER be injected into a
    CodeSpace. See the control-plane AGENTS.md "Plugin Naming & Propagation
    Convention". Enforced here so a mis-declared ``codespacePlugins`` entry can't
    leak a harness plugin onto a CodeSpace.
    """
    name = plugin_name(source)
    return name.endswith("-harness") or "-harness-" in name


def parse_codespace_plugins(
    manifest: dict[str, Any], declared_by: str
) -> list[CodespacePluginSpec]:
    """Parse a manifest's ``codespacePlugins`` array into specs (tolerant).

    Drops any ``*-harness*`` source: harness plugins are central-harness-only and
    must not be injected into a CodeSpace even if mis-declared.
    """
    raw = manifest.get(MANIFEST_FIELD)
    if not isinstance(raw, list):
        return []
    specs: list[CodespacePluginSpec] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if not isinstance(source, str) or not source.strip():
            continue
        if is_harness_plugin(source):
            continue
        enable = bool(entry.get("enable", True))
        filters = _as_repo_filters(entry.get("forWorkspaceRepo"))
        specs.append(
            CodespacePluginSpec(
                source=source.strip(),
                enable=enable,
                for_workspace_repo=filters,
                declared_by=(declared_by,),
            )
        )
    return specs


def parse_operator_plugins(
    entries: Any, declared_by: str = "codespaces.yaml"
) -> list[CodespacePluginSpec]:
    """Parse an operator's ``codespace_plugins`` list (from codespaces.yaml).

    Same entry shape + tolerance as a manifest's ``codespacePlugins`` array
    (harness-plugin sources are dropped defensively). ``declared_by`` marks the
    control-plane origin so a resolved spec's provenance is clear.
    """
    return parse_codespace_plugins({MANIFEST_FIELD: entries}, declared_by)


def _merge_spec(
    merged: dict[str, CodespacePluginSpec], spec: CodespacePluginSpec
) -> None:
    """Merge one spec into ``merged`` (dedup by source, OR-merge enable)."""
    existing = merged.get(spec.source)
    if existing is None:
        merged[spec.source] = spec
        return
    merged[spec.source] = CodespacePluginSpec(
        source=spec.source,
        enable=existing.enable or spec.enable,
        # Union of filters preserves the broadest applicable scope.
        for_workspace_repo=tuple(
            dict.fromkeys(existing.for_workspace_repo + spec.for_workspace_repo)
        ),
        declared_by=tuple(dict.fromkeys(existing.declared_by + spec.declared_by)),
    )


def resolve_codespace_plugins(
    workspace_repo: str | None,
    *,
    copilot_home: Path | None = None,
    only_enabled: bool = True,
    extra_specs: Iterable[CodespacePluginSpec] = (),
    enabled_names: set[str] | None = None,
) -> list[CodespacePluginSpec]:
    """Resolve the CodeSpace-scoped plugins to inject into ``workspace_repo``'s CodeSpace.

    Sweeps the installed harness plugins, collects their ``codespacePlugins``
    declarations, and keeps the entries whose ``forWorkspaceRepo`` filter applies
    to ``workspace_repo`` (global entries always apply). Entries are de-duplicated
    by ``source``: ``enable`` is OR-merged (any declarer asking to enable wins),
    and every declaring plugin is recorded in ``declared_by``.

    ``extra_specs`` are operator-declared specs (e.g. parsed from the control
    plane's ``codespaces.yaml`` ``codespace_plugins`` list via
    :func:`parse_operator_plugins`) merged in on equal footing with the swept
    ones -- the seam an operator uses to put generic plugins (agent-worktrees,
    efforts, ...) on every CodeSpace without editing a shared/repo plugin.json.
    They are NOT subject to the ``only_enabled`` harness-enablement filter (the
    operator declared them explicitly).

    When ``only_enabled`` is true and the harness's enabled-plugin set can be
    determined, declarations from plugins that are *not* enabled on the harness
    are ignored (a disabled harness plugin should not inject anything). If the
    enabled set cannot be determined, no enablement filtering is applied.
    ``enabled_names`` lets the caller supply that set from the **repo-scoped**
    config (``config.repo_copilot_settings``) instead of the user settings;
    when ``None`` (the default) the legacy user-settings
    :func:`enabled_plugin_names` is consulted.
    """
    home = copilot_home or default_copilot_home()
    if only_enabled:
        enabled = enabled_names if enabled_names is not None else enabled_plugin_names(home)
    else:
        enabled = None

    merged: dict[str, CodespacePluginSpec] = {}
    for name, _pdir, manifest in iter_installed_manifests(home):
        if enabled is not None and name not in enabled:
            continue
        for spec in parse_codespace_plugins(manifest, declared_by=name):
            if not repo_matches(spec.for_workspace_repo, workspace_repo):
                continue
            _merge_spec(merged, spec)
    for spec in extra_specs:
        if not repo_matches(spec.for_workspace_repo, workspace_repo):
            continue
        _merge_spec(merged, spec)
    return [merged[k] for k in sorted(merged)]


# --------------------------------------------------------------------------
# CLI preview (prototype: "what plugins would this CodeSpace need?")
# --------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="agent_codespaces.codespace_plugins",
        description="Preview the CodeSpace-scoped plugins the harness would "
        "inject into a CodeSpace for a given workspace repo.",
    )
    parser.add_argument(
        "workspace_repo",
        nargs="?",
        help="Target CodeSpace workspace repo (e.g. odsp-microsoft/odsp-web). "
        "Omit to see only the globally-scoped plugins.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include declarations from installed-but-not-enabled harness plugins.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    specs = resolve_codespace_plugins(
        args.workspace_repo, only_enabled=not args.all
    )
    if args.json:
        print(json.dumps([s.to_dict() for s in specs], indent=2))
        return 0
    if not specs:
        print(
            f"No CodeSpace-scoped plugins for "
            f"{args.workspace_repo or '(global-only)'}."
        )
        return 0
    print(f"CodeSpace-scoped plugins for {args.workspace_repo or '(global-only)'}:")
    for s in specs:
        scope = "global" if s.is_global else ",".join(s.for_workspace_repo)
        flag = "" if s.enable else " (install-only)"
        print(f"  • {s.source}{flag}  [{scope}]  ← {', '.join(s.declared_by)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
