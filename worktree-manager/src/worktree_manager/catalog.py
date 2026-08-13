"""Declarative plugin-knowledge model (Phase 1, effort ``installer-configurator``).

The Worktree Manager's **installer-owned** knowledge of the plugins: what each one
needs (prereqs), the config it manages, and the "what to do" to make it ready.
The knowledge is DATA (``data/plugins.toml``, shipped inside this payload), read
here into small typed records.

**Dependency-free boundary (DQ2).** This module imports **no plugin code** and
requires **no plugin** to publish anything installer-specific: the catalog stands
alone. Where a plugin *already* publishes generic metadata for its own reasons —
the marketplace entry, or ``scripts/service.yaml`` prereqs — the Worktree Manager
*opportunistically reconciles* against it (:func:`reconcile`) when a repo checkout
happens to be present, and simply skips that check when it is not. Reading
pre-existing metadata creates no dependency edge: the plugin neither knows about
nor produces anything for the Worktree Manager, and behaves identically with the
Worktree Manager absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

#: Allowed plugin kinds, ordered by how much setup they need.
KINDS = ("core", "service", "library", "knowledge")


@dataclass(frozen=True)
class Prereq:
    """A single prerequisite for a plugin (or the baseline)."""

    name: str
    min: str | None = None
    notes: str | None = None
    optional: bool = False
    #: "published" = the plugin declares it in its own service.yaml; "installer"
    #: = installer-side knowledge the catalog adds. ``None`` for baseline prereqs.
    source: str | None = None
    #: Baseline only: how Phase 2 provisions it ("system" | "script").
    provision: str | None = None


@dataclass(frozen=True)
class ConfigItem:
    """A config artifact the plugin manages (informational, for the doctor)."""

    path: str
    description: str = ""


@dataclass(frozen=True)
class Step:
    """One "what to do" step to make a plugin ready."""

    id: str
    what: str
    #: The concrete command Phase 2 would run, when the step is an install action.
    #: ``None`` for steps with no build (library / knowledge plugins).
    runs: str | None = None


@dataclass(frozen=True)
class Plugin:
    """The installer's declarative model of one plugin."""

    name: str
    kind: str
    summary: str = ""
    prereqs: tuple[Prereq, ...] = ()
    config: tuple[ConfigItem, ...] = ()
    steps: tuple[Step, ...] = ()
    depends_on: tuple[str, ...] = ()

    @property
    def published_prereqs(self) -> tuple[Prereq, ...]:
        """Prereqs the plugin itself declares (source == "published")."""
        return tuple(p for p in self.prereqs if p.source == "published")


@dataclass(frozen=True)
class Catalog:
    """The whole installer-owned plugin-knowledge model."""

    schema_version: int
    baseline_prereqs: tuple[Prereq, ...]
    plugins: tuple[Plugin, ...]

    def get(self, name: str) -> Plugin | None:
        for p in self.plugins:
            if p.name == name:
                return p
        return None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.plugins)


def _prereq(d: dict) -> Prereq:
    return Prereq(
        name=d["name"],
        min=d.get("min"),
        notes=d.get("notes"),
        optional=bool(d.get("optional", False)),
        source=d.get("source"),
        provision=d.get("provision"),
    )


def _plugin(d: dict) -> Plugin:
    kind = d["kind"]
    if kind not in KINDS:
        raise ValueError(f"plugin {d.get('name')!r}: unknown kind {kind!r}")
    return Plugin(
        name=d["name"],
        kind=kind,
        summary=d.get("summary", ""),
        prereqs=tuple(_prereq(p) for p in d.get("prereqs", [])),
        config=tuple(ConfigItem(path=c["path"], description=c.get("description", ""))
                     for c in d.get("config", [])),
        steps=tuple(Step(id=s["id"], what=s["what"], runs=s.get("runs"))
                    for s in d.get("steps", [])),
        depends_on=tuple(d.get("depends_on", [])),
    )


def _default_catalog_path() -> Path:
    return Path(str(files("worktree_manager") / "data" / "plugins.toml"))


def load_catalog(path: str | Path | None = None) -> Catalog:
    """Load the installer-owned catalog. No plugin code is imported."""
    p = Path(path) if path is not None else _default_catalog_path()
    with p.open("rb") as fh:
        raw = tomllib.load(fh)
    baseline = tuple(_prereq(x) for x in raw.get("baseline", {}).get("prereqs", []))
    plugins = tuple(_plugin(x) for x in raw.get("plugins", []))
    return Catalog(
        schema_version=int(raw.get("schema_version", 0)),
        baseline_prereqs=baseline,
        plugins=plugins,
    )


def find_repo_root(start: str | Path | None = None) -> Path | None:
    """Walk up from ``start`` (default: this file) to a copilot-extensions checkout.

    Returns the repo root if one is found (it carries ``.github/plugin/
    marketplace.json`` and a ``plugins/`` tree), else ``None`` — the Worktree Manager
    may run with no checkout present, in which case discovery falls back to the
    remote marketplace (see :mod:`discovery`).
    """
    here = Path(start) if start is not None else Path(__file__).resolve()
    for d in (here, *here.parents):
        if (d / ".github" / "plugin" / "marketplace.json").is_file() and (d / "plugins").is_dir():
            return d
    return None


# service.yaml is small + regular; parse just its `prereqs:` block without adding
# a YAML dependency. Reads generic metadata the plugin already publishes.
_PREREQ_BLOCK = re.compile(r"^prereqs:\s*$(.*?)(?=^\S|\Z)", re.MULTILINE | re.DOTALL)
_PREREQ_NAME = re.compile(r"^\s*-\s*name:\s*(.+?)\s*$", re.MULTILINE)


def published_prereq_names(repo_root: Path, plugin_name: str) -> list[str]:
    """Prereq names a plugin declares in its own ``scripts/service.yaml``.

    Empty when the plugin publishes no service.yaml (most don't) — this is
    opportunistic enrichment, never a requirement.
    """
    svc = repo_root / "plugins" / plugin_name / "scripts" / "service.yaml"
    if not svc.is_file():
        return []
    text = svc.read_text("utf-8")
    block = _PREREQ_BLOCK.search(text)
    if not block:
        return []
    return [m.group(1).strip() for m in _PREREQ_NAME.finditer(block.group(1))]


def all_prereqs(catalog: Catalog, include_optional: bool = True) -> list[Prereq]:
    """De-duplicated union of baseline + per-plugin prereqs (by name)."""
    seen: dict[str, Prereq] = {}
    for pr in (*catalog.baseline_prereqs,
               *(p for plug in catalog.plugins for p in plug.prereqs)):
        if not include_optional and pr.optional:
            continue
        # First occurrence wins, but a required prereq upgrades an optional one.
        cur = seen.get(pr.name)
        if cur is None or (cur.optional and not pr.optional):
            seen[pr.name] = pr
    return [seen[k] for k in sorted(seen)]
