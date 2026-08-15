"""Declarative namespace-provider discovery.

agent-bridge sources external agent *providers* (codespaces, containers, ...)
from a filesystem manifest registry instead of hardcoded imports / PATH probes.
Each provider plugin drops a small JSON manifest into
``~/.agent-bridge/providers.d/`` from its own sessionStart bootstrap hook; the
daemon scans that directory and registers a namespace resolver per manifest,
driving the provider's binstub over a process boundary.

Why a filesystem registry (mirrors the agent-worktrees *pivot* registry): the
daemon runs from its own isolated versioned venv and service PATH, where a
provider package is neither importable nor on ``PATH``. A manifest carries an
**absolute** command (resolved by the provider's own bootstrap hook, which *can*
find its binstub), so the daemon never depends on importing the provider or on
its ``PATH``. Providers self-register merely by dropping a manifest -- no
imperative "register" call, no TTL, always freshly enumerated on demand.

Robustness (also mirrors the pivot registry): a malformed or unreadable manifest
is skipped with a warning; discovery never raises, so a single bad drop-in can
never break daemon startup or agent enumeration.

Manifest schema (``~/.agent-bridge/providers.d/<name>.json``)::

    {
      "namespace": "codespace",          # required: the ``<prefix>:`` it serves
      "command": ["/abs/agent-codespaces"],  # required: absolute argv prefix
      "restricted": false,                # optional: venues lack cross-repo/inject
      "description": "GitHub Codespaces"  # optional: human label
    }

agent-bridge invokes ``<command...> namespace-list`` /
``<command...> namespace-resolve <name>`` (etc.) to source and resolve the
provider's agents on demand.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("agent-bridge")

#: Environment override for the provider-manifest directory (tests use it for
#: hermetic isolation; also an operator escape hatch).
PROVIDERS_DIR_ENV = "AGENT_BRIDGE_PROVIDERS_DIR"

#: Environment override for the agent-bridge config dir (shared with the rest of
#: the daemon; ``providers.d`` lives beneath it).
_CONFIG_DIR_ENV = "AGENT_BRIDGE_CONFIG_DIR"


def providers_dir() -> Path:
    """Resolve the ``providers.d`` directory (does not create it)."""
    override = os.environ.get(PROVIDERS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    config_dir = Path(
        os.environ.get(_CONFIG_DIR_ENV, "~/.agent-bridge")
    ).expanduser()
    return config_dir / "providers.d"


@dataclass(frozen=True)
class ProviderManifest:
    """A validated namespace-provider drop-in manifest."""

    namespace: str
    command: tuple[str, ...]
    restricted: bool = False
    description: str = ""
    source_path: str = ""


class ManifestError(ValueError):
    """A provider manifest was structurally invalid."""


def parse_manifest(data: object, *, source_path: str = "") -> ProviderManifest:
    """Build a :class:`ProviderManifest` from parsed JSON.

    Raises :class:`ManifestError` on any structural problem so the caller can
    skip a single bad manifest without aborting discovery.
    """
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a JSON object")

    ns = data.get("namespace")
    if not isinstance(ns, str) or not ns.strip():
        raise ManifestError("`namespace` is required and must be a non-empty string")
    ns = ns.strip().rstrip(":")

    cmd = data.get("command")
    if (
        not isinstance(cmd, list)
        or not cmd
        or not all(isinstance(x, str) and x for x in cmd)
    ):
        raise ManifestError("`command` must be a non-empty array of strings")

    desc = data.get("description", "")
    if not isinstance(desc, str):
        raise ManifestError("`description` must be a string when present")

    restricted = data.get("restricted", False)
    if not isinstance(restricted, bool):
        raise ManifestError("`restricted` must be a JSON boolean when present")

    return ProviderManifest(
        namespace=ns,
        command=tuple(cmd),
        restricted=restricted,
        description=desc,
        source_path=source_path,
    )


def discover_provider_manifests(
    directory: str | os.PathLike[str] | None = None,
) -> dict[str, ProviderManifest]:
    """Scan ``providers.d`` and return ``{namespace: manifest}``.

    Invalid manifests are skipped with a warning; a duplicate namespace keeps
    the first (lexicographic) manifest. Never raises.
    """
    directory = Path(directory) if directory is not None else providers_dir()
    manifests: dict[str, ProviderManifest] = {}
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError:
        return manifests

    for path in entries:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            manifest = parse_manifest(data, source_path=str(path))
        except (OSError, ValueError) as exc:
            log.warning("Skipping invalid provider manifest %s: %s", path, exc)
            continue
        if manifest.namespace in manifests:
            log.warning(
                "Duplicate provider namespace '%s' in %s -- keeping %s",
                manifest.namespace,
                path,
                manifests[manifest.namespace].source_path,
            )
            continue
        manifests[manifest.namespace] = manifest

    return manifests
