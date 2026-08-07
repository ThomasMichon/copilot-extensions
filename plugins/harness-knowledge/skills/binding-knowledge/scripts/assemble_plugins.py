#!/usr/bin/env python3
"""Assemble the harness's personal-plugin overlay from the knowledge repo's ``.ai``.

Part of the citadel stateless-harness split (#955): a name-free harness must load
the OPERATOR's personal skills/agents, which live as ``.ai`` local-marketplace
plugins in the PRIVATE knowledge repo -- not in the shareable harness tree.
Copilot loads plugins from the LAUNCH repo's settings, so this renders a
machine-local, gitignored ``<harness>/.github/copilot/settings.local.json`` that
re-declares the knowledge repo's LOCAL (``.ai``) marketplace(s) with an ABSOLUTE
path into the knowledge checkout + the same ``enabledPlugins``. Copilot merges
``settings.local.json`` over the committed ``settings.json`` on launch (local tier
wins), so the personal plugins load while the harness tree stays name-free.

Reads BOTH the Copilot-native (``.github/copilot/settings.json``) and Claude
(``.claude/settings.json``) conventions, native preferred -- mirroring the
``plugin_resolve`` lib, but pure-stdlib (a skill script carries no deps). Only
LOCAL (``directory`` / ``local``) marketplaces are carried across: a remote
(github / git) marketplace the harness can declare itself or is globally
installed, and re-pointing it would be wrong. Idempotent and merge-safe:
unmanaged content already in ``settings.local.json`` is preserved, and stale
entries for the managed local marketplaces are refreshed to exactly mirror the
knowledge repo.

The rendered file MUST be gitignored in the harness tree (it is machine-local
and names the concrete knowledge checkout) -- keep ``.github/copilot/settings.local.json``
in the harness ``.gitignore``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Settings files in native-first order (Claude first, native last => native wins
# on a key conflict; ``.local`` overrides its base within a convention). Mirrors
# ``plugin_resolve.conventions.SETTINGS_RELS``.
_SETTINGS_RELS = (
    (".claude", "settings.json"),
    (".claude", "settings.local.json"),
    (".github", "copilot", "settings.json"),
    (".github", "copilot", "settings.local.json"),
)

# Local (on-disk) marketplace source spellings -- a directory on this machine.
_LOCAL_SOURCE_KINDS = frozenset({"directory", "local"})


def _load_json(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_repo_settings(repo_dir: str | Path) -> tuple[dict, dict]:
    """Merge a repo's ``enabledPlugins`` + ``extraKnownMarketplaces`` (native-first).

    Returns ``(enabled, marketplaces)`` folded across both conventions with
    last-file-wins (native over Claude, ``.local`` over base). Fail-safe -> empties.
    """
    base = Path(repo_dir)
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, dict] = {}
    for rel in _SETTINGS_RELS:
        data = _load_json(base.joinpath(*rel))
        if not data:
            continue
        en = data.get("enabledPlugins")
        if isinstance(en, dict):
            for k, v in en.items():
                if isinstance(k, str):
                    enabled[k] = bool(v)
        mk = data.get("extraKnownMarketplaces")
        if isinstance(mk, dict):
            for k, v in mk.items():
                if isinstance(k, str) and isinstance(v, dict):
                    marketplaces[k] = v
    return enabled, marketplaces


def _is_local_marketplace(defn: dict) -> bool:
    src = defn.get("source") if isinstance(defn, dict) else None
    return bool(isinstance(src, dict) and src.get("source") in _LOCAL_SOURCE_KINDS)


def _abs_dir_source(defn: dict, repo_dir: Path) -> dict:
    """Return a copy of a local-marketplace def with its path made ABSOLUTE.

    A relative ``path`` (e.g. the ``.ai`` standard's ``./.ai``) is resolved
    against ``repo_dir`` -- the knowledge checkout -- and emitted with forward
    slashes so the harness resolves it regardless of the launch cwd. An already-
    absolute path is normalized as-is.
    """
    src = dict(defn.get("source") or {})
    raw = str(src.get("path") or "").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = (repo_dir / p)
    src["path"] = p.resolve().as_posix()
    out = dict(defn)
    out["source"] = src
    return out


def assemble(harness_path: str | Path, knowledge_path: str | Path) -> dict:
    """Render the harness ``settings.local.json`` personal-plugin overlay.

    Reads the knowledge repo's LOCAL (``.ai``) marketplaces + their enabled
    plugins, re-declares them with an absolute path into the knowledge checkout,
    and merges the result into ``<harness>/.github/copilot/settings.local.json``
    (preserving unmanaged entries). Idempotent. Returns a summary dict.
    """
    harness = Path(harness_path)
    knowledge = Path(knowledge_path)

    k_enabled, k_marketplaces = read_repo_settings(knowledge)
    local_names = {
        name for name, defn in k_marketplaces.items() if _is_local_marketplace(defn)
    }

    # The managed marketplaces (absolute-path rewritten) + the knowledge's
    # currently-enabled plugins that belong to them.
    managed_marketplaces = {
        name: _abs_dir_source(k_marketplaces[name], knowledge)
        for name in sorted(local_names)
    }
    managed_enabled = {
        spec: True
        for spec, on in k_enabled.items()
        if on and "@" in spec and spec.rsplit("@", 1)[1] in local_names
    }

    # Merge into any existing (unmanaged) local settings, refreshing exactly the
    # managed marketplaces + their plugin entries so a dropped plugin doesn't
    # linger.
    out_path = harness / ".github" / "copilot" / "settings.local.json"
    existing = _load_json(out_path) or {}
    ext = dict(existing.get("extraKnownMarketplaces") or {})
    en = dict(existing.get("enabledPlugins") or {})

    # Refresh the managed marketplaces.
    ext.update(managed_marketplaces)
    # Clear stale enabledPlugins for the managed marketplaces, then re-add.
    en = {
        spec: v
        for spec, v in en.items()
        if not ("@" in spec and spec.rsplit("@", 1)[1] in local_names)
    }
    en.update(managed_enabled)

    result = dict(existing)
    if ext:
        result["extraKnownMarketplaces"] = ext
    if en:
        result["enabledPlugins"] = en

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {
        "settings_local": str(out_path),
        "marketplaces": sorted(local_names),
        "enabled_plugins": sorted(managed_enabled),
        "count": len(managed_enabled),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="assemble_plugins",
        description=(
            "Render the harness settings.local.json personal-plugin overlay from "
            "the knowledge repo's .ai local marketplace(s) (idempotent)."
        ),
    )
    p.add_argument("--harness-path", required=True,
                   help="Local checkout path of the stateless harness (where "
                        "settings.local.json is written).")
    p.add_argument("--knowledge-path", required=True,
                   help="Local checkout path of the knowledge repo (its .ai is "
                        "the personal-plugin source).")
    p.add_argument("--json", action="store_true", help="Emit the summary as JSON.")
    args = p.parse_args(argv)

    summary = assemble(args.harness_path, args.knowledge_path)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        n = summary["count"]
        print(f"Assembled {n} personal plugin(s) into {summary['settings_local']}")
        if summary["marketplaces"]:
            print(f"  marketplaces: {', '.join(summary['marketplaces'])}")
        for spec in summary["enabled_plugins"]:
            print(f"    + {spec}")
        print("Copilot merges settings.local.json over settings.json on launch, "
              "so these load in the harness. Keep it gitignored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
