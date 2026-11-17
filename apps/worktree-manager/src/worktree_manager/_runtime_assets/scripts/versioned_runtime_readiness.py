"""Read-only payload/source qualification of a selected runtime generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from versioned_runtime_source import manifest_fields, read_source_identity


def deployment_runtime_version(payload: Path, package: str, plugin: str, deployment: object) -> str:
    identity = read_source_identity(payload, package, plugin=plugin)
    fields = manifest_fields(identity)
    if not isinstance(deployment, dict) or deployment.get("runtime_source") != fields.get("runtime_source"):
        raise ValueError("Payload and deployment source identities differ")
    base = identity.runtime_version if identity else package
    selected = deployment.get("runtime_version", base)
    separator = "." if "+" in base else "+"
    if selected != base and (
        not isinstance(selected, str)
        or not re.fullmatch(re.escape(base + separator) + r"repair\.[0-9a-f]{32}", selected)
    ):
        raise ValueError("Deployment generation does not match the payload")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--package-version", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--deployment-manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        deployment = json.loads(args.deployment_manifest.read_text(encoding="utf-8-sig"))
        print(deployment_runtime_version(args.payload, args.package_version, args.plugin, deployment))
    except (OSError, ValueError) as error:
        print(f"runtime-readiness: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
