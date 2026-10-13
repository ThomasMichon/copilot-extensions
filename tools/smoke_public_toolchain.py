"""Hosted-runner smoke: fresh seasoned-public toolchain, shared across real builds."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from build_python_artifacts import build_plugin_artifacts
from build_toolchain_lock import resolve_toolchain_lock
from promotion_python_artifacts import INDEX_NAME, SCHEMA, bundle_wheels, validate_artifacts


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
        export = directory / "export"
        export.mkdir()
        entries = [
            bundle_wheels(manifest, directory / manifest["plugin"], export)
            for manifest in manifests
        ]
        identity = "a" * 40
        (export / INDEX_NAME).write_text(json.dumps({
            "schema": SCHEMA, "schema_version": 1,
            "dev_head": identity, "main_before": identity, "content_tree": identity,
            "artifacts": entries,
        }), encoding="utf-8")
        validate_artifacts(
            export, dev_head=identity, main_before=identity, content_tree=identity,
            projects={manifest["plugin"]: manifest["version"] for manifest in manifests},
            candidate_root=Path(__file__).resolve().parent.parent,
        )
        print(json.dumps({
            "plugins": [manifest["plugin"] for manifest in manifests],
            "toolchain": toolchain.record,
            "wheel_counts": [len(manifest["wheels"]) for manifest in manifests],
        }, sort_keys=True))


if __name__ == "__main__":
    main()
