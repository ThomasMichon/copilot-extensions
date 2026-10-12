"""Hosted-runner smoke: fresh seasoned-public toolchain, shared across real builds."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from build_python_artifacts import build_plugin_artifacts
from build_toolchain_lock import resolve_toolchain_lock


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="public-toolchain-smoke-") as root:
        directory = Path(root)
        toolchain = resolve_toolchain_lock(
            directory / "toolchain", source_policy="seasoned-public"
        )
        manifests = [
            build_plugin_artifacts(
                plugin, out_dir=directory / plugin, toolchain=toolchain
            )
            for plugin in ("agent-mcp", "agent-ssh")
        ]
        reused = resolve_toolchain_lock(
            directory / "toolchain", source_policy="seasoned-public"
        )
        assert reused.venv_python == toolchain.venv_python
        assert reused.lock_id == toolchain.lock_id
        for manifest in manifests:
            assert manifest["schema_version"] == 3
            assert manifest["build_toolchain"] == toolchain.record
            assert manifest["build_toolchain"]["provenance"]["source_policy"] == "seasoned-public"
            assert all(wheel["role"] in ("plugin", "vendored-lib") for wheel in manifest["wheels"])
        print(json.dumps({
            "plugins": [manifest["plugin"] for manifest in manifests],
            "toolchain": toolchain.record,
            "wheel_counts": [len(manifest["wheels"]) for manifest in manifests],
        }, sort_keys=True))


if __name__ == "__main__":
    main()
