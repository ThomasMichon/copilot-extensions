"""Claim-kind ``.d/`` drop-in registry (picker-venue-pivots effort).

Lets any installed plugin **contribute** a claimable kind (and the priority
it should rank at in ``claims_rank``'s shared pecking order) without editing
that module's own built-in table -- mirroring the Worktree Picker's own
cross-plugin pivot-contribution pattern (a plugin drops
``<plugin_root>/pivots/<name>.json``; the picker discovers and materializes
it). The contract here is deliberately lighter than that pivot registry:

- A contributing plugin drops one file per declared kind at
  ``<plugin_root>/claim-kinds/<kind>.json``:
  ``{"kind": "bug", "priority": 1}`` (an optional ``"label"`` may name a
  display prefix `format_claim` should use instead of the bare kind name;
  unset falls back to the kind itself, exactly like `claims_rank`'s own
  built-in ``_LABEL_PREFIX`` for "pr"/"bug"/"issue").
- No identity verification, legacy-migration, or single-flattened-runtime-
  directory materialization step (unlike the pivot registry): a claim-kind
  declaration carries no executable command to spoof, so that machinery's
  entire reason to exist doesn't apply here. This registry scans the
  installed-plugins tree directly, every call -- cheap (a handful of tiny
  JSON files), and it keeps this module honest as a thin discovery/merge
  layer rather than a second copy of the pivot registry's own complexity.
- A malformed/unreadable drop-in is silently skipped (never raises) --
  exactly `dropin_registry`'s own philosophy: a bad or absent entry must
  never break every other consumer's ranking.

``claims_rank`` itself never imports this module (stays pure/I/O-free --
see its own docstring); a caller wires the two together explicitly:

    from agent_worktrees import claims_rank, claim_kinds_registry
    order = claim_kinds_registry.effective_pecking_order()
    summary = claims_rank.summarize_claims(claims, pecking_order=order)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NamedTuple

from dropin_registry import EntryDecision, Finding, scan_directory

from . import config as cfg
from .claims_rank import DEFAULT_PECKING_ORDER

#: Same env-var name the Worktree Picker's own pivot registry uses for the
#: installed-plugins root (`pivot_manifest.PLUGINS_ROOT_ENV`) -- not
#: imported (agent-worktrees does not depend on worktree-manager), but kept
#: identical so one override env var configures both registries in a test
#: or sandboxed environment.
PLUGINS_ROOT_ENV = "AGENT_WORKTREES_PLUGINS_DIR"
CLAIM_KINDS_SUBDIR = "claim-kinds"
_REGISTRY_NAME = "claim-kinds"


class ClaimKindContribution(NamedTuple):
    kind: str
    priority: int
    label: str | None
    source: str


def installed_plugins_dir(base: str | os.PathLike[str] | None = None) -> Path:
    """The copilot marketplace plugin-install root -- an explicit ``base``,
    else :data:`PLUGINS_ROOT_ENV`, else ``~/.copilot/installed-plugins``."""
    if base is not None:
        return Path(base)
    env = os.environ.get(PLUGINS_ROOT_ENV)
    if env:
        return Path(env)
    return cfg._home() / ".copilot" / "installed-plugins"


def _classify(path: Path) -> EntryDecision[ClaimKindContribution]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return EntryDecision.inactive(
            Finding(
                registry=_REGISTRY_NAME,
                entry=str(path),
                status="inactive",
                reason="unreadable",
                detail=str(exc),
            )
        )
    try:
        raw = json.loads(raw_text)
    except ValueError as exc:
        return EntryDecision.inactive(
            Finding(
                registry=_REGISTRY_NAME,
                entry=str(path),
                status="inactive",
                reason="invalid-json",
                detail=str(exc),
            )
        )
    if not isinstance(raw, dict):
        return EntryDecision.inactive(
            Finding(
                registry=_REGISTRY_NAME,
                entry=str(path),
                status="inactive",
                reason="not-a-json-object",
            )
        )
    kind = raw.get("kind")
    priority = raw.get("priority")
    label = raw.get("label")
    if not isinstance(kind, str) or not kind.strip():
        return EntryDecision.inactive(
            Finding(
                registry=_REGISTRY_NAME,
                entry=str(path),
                status="inactive",
                reason="missing-or-invalid-kind",
            )
        )
    if isinstance(priority, bool) or not isinstance(priority, int):
        return EntryDecision.inactive(
            Finding(
                registry=_REGISTRY_NAME,
                entry=str(path),
                status="inactive",
                reason="missing-or-invalid-priority",
            )
        )
    if label is not None and not isinstance(label, str):
        label = None
    return EntryDecision.active(
        ClaimKindContribution(
            kind=kind.strip(), priority=priority, label=label, source=str(path)
        )
    )


def discover_claim_kind_contributions(
    plugins_root: str | os.PathLike[str] | None = None,
) -> list[ClaimKindContribution]:
    """Every valid ``claim-kinds/*.json`` drop-in across every installed
    plugin, in deterministic (sorted plugin path, then sorted filename)
    order. Never raises; an absent/unreadable plugins root or claim-kinds
    subdirectory simply yields no contributions from that plugin."""
    root = installed_plugins_dir(plugins_root)
    contributions: list[ClaimKindContribution] = []
    if not root.is_dir():
        return contributions
    try:
        plugin_dirs = sorted(p for p in root.glob("*/*") if p.is_dir())
    except OSError:
        return contributions
    for plugin_dir in plugin_dirs:
        claim_kinds_dir = plugin_dir / CLAIM_KINDS_SUBDIR
        snapshot = scan_directory(
            claim_kinds_dir,
            _classify,
            registry=_REGISTRY_NAME,
            suffixes={".json"},
        )
        for decision in snapshot.decisions.values():
            if decision.value is not None:
                contributions.append(decision.value)
    return contributions


def effective_pecking_order(
    plugins_root: str | os.PathLike[str] | None = None,
) -> dict[str, int]:
    """:data:`claims_rank.DEFAULT_PECKING_ORDER`, with every installed
    plugin's own ``claim-kinds/*.json`` contribution merged on top -- a
    plugin may override an EXISTING kind's priority or declare a brand-new
    one. Deterministic: contributions are applied in the same sorted
    (plugin path, filename) order `discover_claim_kind_contributions`
    returns, so the last one applied for a given kind wins on a genuine
    conflict between two plugins -- documented, tunable behavior, not a
    guarantee that conflicts are meaningfully resolved for you.
    """
    merged = dict(DEFAULT_PECKING_ORDER)
    for contribution in discover_claim_kind_contributions(plugins_root):
        merged[contribution.kind] = contribution.priority
    return merged


def effective_label_overrides(
    plugins_root: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """``{kind: label}`` for every contribution that declared a ``label`` --
    pass directly as `claims_rank.format_claim`/`summarize_claims`'s own
    ``label_overrides=`` parameter. Kept separate from
    `effective_pecking_order` so a caller can consume priorities without
    also needing label overrides (or vice versa).
    """
    labels: dict[str, str] = {}
    for contribution in discover_claim_kind_contributions(plugins_root):
        if contribution.label:
            labels[contribution.kind] = contribution.label
    return labels
