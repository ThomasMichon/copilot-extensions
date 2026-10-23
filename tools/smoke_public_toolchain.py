"""Hosted-runner smoke: fresh seasoned-public toolchain, shared across real builds."""
from __future__ import annotations

import json
import argparse
import os
import sys
import tempfile
from pathlib import Path

from build_python_artifacts import build_plugin_artifacts
from build_toolchain_lock import resolve_toolchain_lock
from promotion_python_artifacts import INDEX_NAME, SCHEMA, _run, bundle_wheels, validate_artifacts
from standalone_python_artifacts import COMPONENTS, artifact_label, build_standalone_artifacts, project_key


def main(*, standalone: bool = False) -> None:
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
        if standalone:
            manifests.extend(
                build_standalone_artifacts(
                    component, out_dir=directory / f"standalone-{component}", toolchain=toolchain,
                ) for component in COMPONENTS
            )
        reused = resolve_toolchain_lock(
            directory / "toolchain", source_policy="seasoned-public"
        )
        assert reused.venv_python == toolchain.venv_python
        assert reused.lock_id == toolchain.lock_id
        for manifest in manifests:
            assert manifest["schema_version"] == (4 if "component" in manifest else 3)
            assert manifest["build_toolchain"] == toolchain.record
            assert manifest["build_toolchain"]["provenance"]["source_policy"] == "seasoned-public"
            primary = "standalone" if "component" in manifest else "plugin"
            assert all(wheel["role"] in (primary, "vendored-lib") for wheel in manifest["wheels"])
        export = directory / "export"
        export.mkdir()
        entries = [
            bundle_wheels(manifest, directory / artifact_label(manifest), export)
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
            projects={project_key(manifest): manifest["version"] for manifest in manifests},
            candidate_root=Path(__file__).resolve().parent.parent,
        )
        if standalone:
            environment = directory / "consumer"
            _run(["uv", "venv", "--python", sys.executable, str(environment)], cwd=directory)
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            wheels = [
                str(directory / artifact_label(manifest) / wheel["filename"])
                for manifest in manifests if "component" in manifest for wheel in manifest["wheels"]
            ]
            # Only our built wheels, offline and dependency-free: not runtime closure admission.
            _run(
                ["uv", "pip", "install", "--python", str(python), "--no-deps", "--no-index", *wheels],
                cwd=directory,
            )
            _run([
                str(python), "-I", "-c",
                "import fleet_manager, agent_pull_requests_service; "
                "from importlib.resources import files; "
                "from importlib.metadata import version; "
                "assert version('fleet-manager'); assert version('agent-pull-requests-service'); "
                "root=files('fleet_manager'); "
                "names=['_versioned_runtime.py','versioned_runtime_leases.py',"
                "'versioned_runtime_construction.py','versioned_runtime_publication.py',"
                "'versioned_runtime_source.py','_runtime_launcher.py','_installer_process.py',"
                "'_construction_procutil.py']; "
                "assert all(root.joinpath(name).is_file() for name in names)",
            ], cwd=directory)
        print(json.dumps({
            "plugins": [manifest["plugin"] for manifest in manifests if "plugin" in manifest],
            "projects": [project_key(manifest) for manifest in manifests],
            "standalone_installed_offline": standalone,
            "artifacts": [
                {"project": project_key(manifest), "version": manifest["version"],
                 "artifact_id": manifest["artifact_id"], "payload_hash": manifest["payload_hash"]}
                for manifest in manifests
            ],
            "toolchain": toolchain.record,
            "wheel_counts": [len(manifest["wheels"]) for manifest in manifests],
        }, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--contained-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.contained_worker:
        main(standalone=args.standalone)
    else:
        from plugin_test_containment import Limits, TestSandbox, isolated_environment, run_contained
        with TestSandbox(prefix="artifact-smoke-") as temporary:
            root = Path(temporary)
            command = [sys.executable, str(Path(__file__).resolve()), "--contained-worker"]
            if args.standalone:
                command.append("--standalone")
            raise SystemExit(run_contained(
                command, cwd=Path(__file__).resolve().parent.parent,
                env=isolated_environment(os.environ, root), sandbox=root,
                limits=Limits(wall_seconds=180),
            ))
