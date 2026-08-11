"""Effective plugin model = dynamic membership (discovery) + authored overlay.

The Configurator's *effective* knowledge of the plugins is composed here: the
**membership** comes from :mod:`discovery` (the marketplace — checkout or remote),
and each discovered plugin is enriched by the installer-owned **catalog**
(``data/plugins.toml``) when an authored entry exists. A discovered plugin with
**no** authored entry is not dropped — it gets **inferred defaults** (kind from
its published file signals, prereqs from its ``service.yaml``, a generic step) so
the installer degrades gracefully and never breaks on a newly-added plugin.

Because membership is discovered, there is no frozen list to police: the catalog
is a pure *knowledge overlay*. :func:`coverage` reports which discovered plugins
lack authored knowledge (a soft signal) and which authored entries are no longer
in the marketplace (a hard phantom/renamed error).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog import (
    Catalog,
    Plugin,
    Prereq,
    Step,
    find_repo_root,
    load_catalog,
    published_prereq_names,
)
from .discovery import Discovered, DiscoverySource, discover


@dataclass(frozen=True)
class EffectivePlugin:
    """A plugin as the installer effectively sees it."""

    plugin: Plugin
    #: True = enriched from the authored catalog; False = inferred from discovery.
    authored: bool
    #: Discovery origin: "checkout" | "remote" | "catalog" (offline fallback).
    origin: str


@dataclass(frozen=True)
class Model:
    """The composed, effective plugin model."""

    source: DiscoverySource
    plugins: tuple[EffectivePlugin, ...]

    def get(self, name: str) -> EffectivePlugin | None:
        for ep in self.plugins:
            if ep.plugin.name == name:
                return ep
        return None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(ep.plugin.name for ep in self.plugins)


@dataclass(frozen=True)
class Coverage:
    """How well the authored catalog covers the discovered membership."""

    source_kind: str
    #: Discovered but not authored — running on inferred defaults (soft signal).
    uncovered: tuple[str, ...] = ()
    #: Authored but not discovered in the marketplace (phantom/renamed — hard).
    phantom: tuple[str, ...] = ()
    #: (plugin, prereq) a plugin publishes in service.yaml but the catalog does
    #: not carry as source == "published" (hard; checkout only).
    published_prereq_gaps: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        # Uncovered is expected/allowed (inference handles it); only phantoms and
        # published-prereq drift are real errors.
        return not (self.phantom or self.published_prereq_gaps)


def _infer_kind(d: Discovered) -> str:
    if d.origin == "checkout":
        if d.has_service_yaml:
            return "service"
        if d.has_pyproject:
            return "library"
        return "knowledge"
    # Remote: no per-plugin file listing, so default to the no-build kind.
    return "knowledge"


def _infer_step(kind: str) -> Step:
    if kind in ("core", "service"):
        return Step(id="install",
                    what="Run the plugin's installer (inferred — no authored catalog entry yet).")
    if kind == "library":
        return Step(id="provision-with-core",
                    what="Provisioned with the core runtime (inferred — no authored catalog entry yet).")
    return Step(id="enable",
                what="Skills/instructions only — active once enabled (inferred — no authored catalog entry yet).")


def _inferred_plugin(d: Discovered, repo_root: Path | None) -> Plugin:
    kind = _infer_kind(d)
    prereqs: tuple[Prereq, ...] = ()
    if repo_root is not None:
        prereqs = tuple(Prereq(name=n, source="published")
                        for n in published_prereq_names(repo_root, d.name))
    return Plugin(name=d.name, kind=kind, summary=d.description,
                  prereqs=prereqs, steps=(_infer_step(kind),))


def build_model(
    catalog: Catalog | None = None,
    *,
    repo_root: str | Path | None = None,
    ref: str | None = None,
    allow_remote: bool = True,
) -> Model:
    """Compose the effective model from discovered membership + authored overlay."""
    cat = catalog or load_catalog()
    src = discover(repo_root=repo_root, ref=ref, allow_remote=allow_remote)
    root = Path(repo_root) if repo_root is not None else find_repo_root()

    if not src.plugins:
        # Offline and no checkout: fall back to the authored catalog so the app
        # still works (membership just can't be confirmed against reality).
        eff = tuple(EffectivePlugin(plugin=p, authored=True, origin="catalog")
                    for p in cat.plugins)
        return Model(source=src, plugins=eff)

    eff = []
    for d in src.plugins:
        authored = cat.get(d.name)
        if authored is not None:
            eff.append(EffectivePlugin(plugin=authored, authored=True, origin=d.origin))
        else:
            eff.append(EffectivePlugin(plugin=_inferred_plugin(d, root),
                                       authored=False, origin=d.origin))
    return Model(source=src, plugins=tuple(eff))


def coverage(
    catalog: Catalog | None = None,
    *,
    repo_root: str | Path | None = None,
    ref: str | None = None,
    allow_remote: bool = True,
) -> Coverage:
    """Report catalog coverage of the discovered membership."""
    cat = catalog or load_catalog()
    model = build_model(cat, repo_root=repo_root, ref=ref, allow_remote=allow_remote)
    root = Path(repo_root) if repo_root is not None else find_repo_root()

    discovered = set(model.names)
    uncovered = tuple(ep.plugin.name for ep in model.plugins if not ep.authored)
    # Catalog entries that discovery did not confirm (only meaningful when
    # discovery actually returned a membership list).
    phantom = tuple(sorted(n for n in cat.names
                           if model.source.plugins and n not in discovered))

    gaps: list[tuple[str, str]] = []
    if root is not None:
        for p in cat.plugins:
            have = {pr.name for pr in p.published_prereqs}
            for name in published_prereq_names(root, p.name):
                if name not in have:
                    gaps.append((p.name, name))

    return Coverage(
        source_kind=model.source.kind,
        uncovered=uncovered,
        phantom=phantom,
        published_prereq_gaps=tuple(gaps),
    )


def effective_prereqs(model: Model, baseline: tuple[Prereq, ...],
                      include_optional: bool = True) -> list[Prereq]:
    """De-duplicated union of baseline + every effective plugin's prereqs."""
    seen: dict[str, Prereq] = {}
    for pr in (*baseline, *(p for ep in model.plugins for p in ep.plugin.prereqs)):
        if not include_optional and pr.optional:
            continue
        cur = seen.get(pr.name)
        if cur is None or (cur.optional and not pr.optional):
            seen[pr.name] = pr
    return [seen[k] for k in sorted(seen)]
