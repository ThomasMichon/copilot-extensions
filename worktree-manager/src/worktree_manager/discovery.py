"""Dynamic plugin discovery (Phase 1 refinement — dynamic membership overlay).

The Worktree Manager learns *which* plugins exist by reading the marketplace, not
from a hand-frozen list: a nearby copilot-extensions checkout when one is present,
otherwise the **remote published** marketplace ref (the same ref the bootstrap
fetches). This keeps the membership self-updating and needs zero maintenance — the
installer never goes stale when a plugin is added or removed.

This is pure *awareness*, not a dependency: it reads metadata the plugins already
publish for their own reasons (the marketplace entry, and — from a checkout —
whether a plugin ships a ``scripts/service.yaml`` / ``pyproject.toml``). No plugin
code is imported and nothing here requires a plugin to know the Worktree Manager
exists.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .catalog import find_repo_root

#: Where the published marketplace lives when there is no local checkout. ``ref``
#: is filled from ``WORKTREE_MANAGER_REF`` (set by the bootstrap) or defaults to main.
RAW_MARKETPLACE_URL = (
    "https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/"
    "{ref}/.github/plugin/marketplace.json"
)
DEFAULT_REF = "main"


@dataclass(frozen=True)
class Discovered:
    """One plugin found in the marketplace."""

    name: str
    description: str = ""
    #: "checkout" | "remote" — where this entry was discovered.
    origin: str = "checkout"
    #: Checkout-only signals used to infer a kind for uncatalogued plugins.
    has_service_yaml: bool = False
    has_pyproject: bool = False


@dataclass(frozen=True)
class DiscoverySource:
    """The result of a discovery pass: where it came from + what it found."""

    #: "checkout" | "remote" | "none"
    kind: str
    #: A human-readable pointer (path or URL) to the source.
    detail: str
    plugins: tuple[Discovered, ...] = ()


def _from_checkout(root: Path) -> DiscoverySource:
    market_path = root / ".github" / "plugin" / "marketplace.json"
    market = json.loads(market_path.read_text("utf-8"))
    items = []
    for p in market.get("plugins", []):
        name = p["name"]
        pdir = root / "plugins" / name
        items.append(Discovered(
            name=name,
            description=p.get("description", ""),
            origin="checkout",
            has_service_yaml=(pdir / "scripts" / "service.yaml").is_file(),
            has_pyproject=(pdir / "pyproject.toml").is_file(),
        ))
    return DiscoverySource("checkout", str(market_path), tuple(items))


def _from_remote(ref: str, timeout: float) -> DiscoverySource:
    url = RAW_MARKETPLACE_URL.format(ref=ref)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (trusted host)
            market = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return DiscoverySource("none", url, ())
    items = tuple(
        Discovered(name=p["name"], description=p.get("description", ""), origin="remote")
        for p in market.get("plugins", [])
    )
    return DiscoverySource("remote", url, items)


def discover(
    repo_root: str | Path | None = None,
    *,
    ref: str | None = None,
    allow_remote: bool = True,
    timeout: float = 5.0,
) -> DiscoverySource:
    """Discover the plugin membership.

    Prefers a local checkout (richer: per-plugin file signals, offline). Falls
    back to the remote published marketplace ref when no checkout is found and
    ``allow_remote`` is set. Returns a ``"none"`` source (empty) if neither is
    available — callers fall back to the authored catalog so the app still runs.
    """
    root = Path(repo_root) if repo_root is not None else find_repo_root()
    if root is not None:
        return _from_checkout(root)
    if allow_remote:
        resolved_ref = ref or os.environ.get("WORKTREE_MANAGER_REF") or DEFAULT_REF
        return _from_remote(resolved_ref, timeout)
    return DiscoverySource("none", "", ())
