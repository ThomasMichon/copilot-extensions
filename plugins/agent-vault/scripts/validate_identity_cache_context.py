"""Validate a captured cache pin after the existing payload gate resolves its cell."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("runtime-root", "installation-id", "source-home", "source-id", "cache-dir"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "src" / "agent_vault" / "identity_context.py"
    spec = importlib.util.spec_from_file_location("vault_payload_identity_context", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        selected = module.validate_payload_cache(
            os.environ.get("AGENT_VAULT_PINNED_CONTEXT", ""),
            os.environ.get("AGENT_VAULT_CACHE", ""), args.cache_dir,
            args.source_home, args.source_id, args.runtime_root, args.installation_id,
        )
    except (OSError, RuntimeError, ValueError):
        print("[agent-vault] invalid identity cache pin for the selected payload cell.", file=sys.stderr)
        return 126
    print(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
