"""Hosted-runner smoke: fresh seasoned-public toolchain, shared across real builds."""
from __future__ import annotations

import json
import argparse
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import build_python_artifacts as builder
from build_toolchain_lock import resolve_toolchain_lock
from promotion_python_artifacts import (
    INDEX_NAME, SCHEMA, _run, bundle_wheels, candidate_projects, export_artifacts, validate_artifacts,
)
from standalone_python_artifacts import (
    COMPONENTS, artifact_label, build_standalone_artifacts, injected_resources,
    project_key, source_closure,
)


def nested_standalone_export(directory: Path) -> dict:
    """Exercise the actual nested promotion layout, not only direct builds."""
    source = Path(__file__).resolve().parent.parent
    fixture = directory / "nested-export-repository"
    fixture.mkdir()
    components = ("fleet-manager", "pr-service")
    inputs = {".gitattributes"}
    for component in components:
        project, dependencies, resources = source_closure(component, source, builder)
        inputs.update(
            path.relative_to(source).as_posix()
            for path in (project, *(path for _name, path in dependencies), *resources)
        )
    paths = _run(["git", "ls-files", "--", *sorted(inputs)], cwd=source).splitlines()
    for relative in paths:
        destination = fixture / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    _run(["git", "init", "-q"], cwd=fixture)
    _run(["git", "-c", "core.autocrlf=false", "add", "--all"], cwd=fixture)
    _run([
        "git", "-c", "user.name=Artifact Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Canonical standalone export fixture",
    ], cwd=fixture)
    commit = _run(["git", "rev-parse", "HEAD"], cwd=fixture).strip()
    tree = _run(["git", "rev-parse", "HEAD^{tree}"], cwd=fixture).strip()
    export = directory / "nested-export"
    with (
        patch("reuse_promotion_artifacts.reuse_artifacts", return_value=None),
        patch.dict(os.environ, {
            "GITHUB_WORKFLOW_SHA": commit, "GITHUB_SHA": commit, "GITHUB_REF": "refs/heads/main",
        }),
    ):
        index = export_artifacts(
            repo=fixture, commit=commit, content_tree=tree, dev_head=commit, main_before=commit,
            out_dir=export, source_policy="seasoned-public", reuse_repository="example/fixture",
        )
    validate_artifacts(
        export, content_tree=tree, dev_head=commit, main_before=commit,
        projects=candidate_projects(fixture), candidate_root=fixture,
        source_policy="seasoned-public",
    )
    assert {entry["component"] for entry in index["artifacts"]} == set(components)
    assert index["admission"] == {
        "signer_digest": commit, "source_digest": commit, "source_ref": "refs/heads/main",
    }
    assert len(_run(["git", "worktree", "list", "--porcelain"], cwd=fixture).split("worktree ")) == 2
    assert not _run(["git", "status", "--porcelain"], cwd=fixture).strip()
    return index


def main(*, standalone: bool = False) -> None:
    with tempfile.TemporaryDirectory(prefix="public-toolchain-smoke-") as root:
        directory = Path(root)
        toolchain = resolve_toolchain_lock(
            directory / "toolchain", source_policy="seasoned-public"
        )
        manifests = [
            builder.build_plugin_artifacts(
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
            for manifest in (item for item in manifests if "component" in item):
                component = manifest["component"]
                environment = directory / ("consumer-" + component)
                _run(["uv", "venv", "--python", sys.executable, str(environment)], cwd=directory)
                python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                wheels = [
                    str(directory / artifact_label(manifest) / wheel["filename"])
                    for wheel in manifest["wheels"]
                ]
                # Component sets are not an approved consolidated runtime closure.
                _run(
                    ["uv", "pip", "install", "--python", str(python), "--no-deps", "--no-index", *wheels],
                    cwd=directory,
                )
                versions = {
                    wheel["name"]: builder.parse_wheel_filename(Path(wheel["filename"]))["version"]
                    for wheel in manifest["wheels"]
                }
                resources = {
                    name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for name, path in injected_resources(component, builder.REPO).items()
                }
                primary = next(wheel["name"] for wheel in manifest["wheels"] if wheel["role"] == "standalone")
                _run([
                    str(python), "-I", "-c",
                    "import hashlib,importlib,json,pathlib,sys; "
                    "from importlib.metadata import distribution; "
                    "versions,resources=json.loads(sys.argv[1]),json.loads(sys.argv[2]); "
                    "root=pathlib.Path(sys.prefix).resolve(); "
                    "assert all(distribution(n).version==v and "
                    "pathlib.Path(distribution(n).locate_file('')).resolve().is_relative_to(root) "
                    "for n,v in versions.items()); "
                    "selected=distribution(sys.argv[3]); "
                    "assert all(hashlib.sha256(selected.locate_file(n).read_bytes()).hexdigest()==v "
                    "for n,v in resources.items()); "
                    "module={'fleet-manager':'fleet_manager','pr-service':'agent_pull_requests_service'}.get(sys.argv[4]); "
                    "assert module is None or importlib.import_module(module)",
                    json.dumps(versions), json.dumps(resources), primary, component,
                ], cwd=directory)
        nested = nested_standalone_export(directory) if standalone else None
        print(json.dumps({
            "plugins": [manifest["plugin"] for manifest in manifests if "plugin" in manifest],
            "projects": [project_key(manifest) for manifest in manifests],
            "standalone_installed_offline": standalone,
            "nested_export_artifacts": nested["artifacts"] if nested else [],
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
