#!/usr/bin/env python3
"""Refresh complete current producer hashes without changing footprint semantics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import install_footprint_contract as guard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--convert-v2", action="store_true",
                        help="Explicitly convert the supported v2 effective contract to v3")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    provider = guard._load(repo / "libs/installer-engine/installer_engine_footprint.py", "_footprint_refresh")
    for plugin in guard.ADOPTERS:
        payload = repo / "plugins" / plugin
        path = payload / "scripts/uninstall-profile.json"
        record = provider.decode(provider._manifest_text(path))
        manifest = provider.parse(record, plugin)
        if manifest.serialization_version == 2:
            if not args.convert_v2:
                parser.error("v2 conversion requires the explicit --convert-v2 option")
            record = {
                "version": 3, "schema": provider.SCHEMA, "plugin": plugin,
                "revision": manifest.revision,
                "contract": {key: getattr(manifest, key) for key in ("profile", "effects", "recognizers")},
                "producers": manifest.producers, "change": None,
            }
        record["producers"] = {
            scope: provider.producer_digest(payload, scope) for scope in manifest.producers
        }
        provider.require_transition(
            provider.decode(provider._manifest_text(path)), record, plugin,
        )
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"{plugin}: refreshed source digests; contract revision {manifest.revision} unchanged")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Footprint refresh refused: {error}", file=sys.stderr)
        sys.exit(1)
