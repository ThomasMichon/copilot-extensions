#!/usr/bin/env python3
"""Provision the reviewed test-wrapper CLI in a disposable CI directory."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess


def provision(root: Path, project: Path) -> Path:
    helper = root / "worktree-manager/scripts/picker-snapshot/install.py"
    spec = importlib.util.spec_from_file_location("test_devcontainer_npm_admission", helper)
    if spec is None or spec.loader is None:
        raise RuntimeError("locked npm admission helper is unavailable")
    admission = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admission)
    source = root / "tools/devcontainer-cli"
    project.mkdir()
    retained = {name: (source / name).read_bytes() for name in ("package.json", "package-lock.json")}
    expected_version = json.loads(retained["package.json"])["dependencies"]["@devcontainers/cli"]
    for name, body in retained.items():
        (project / name).write_bytes(body)
    command = admission.npm_command()
    replacement = subprocess.run(
        [*command, "config", "get", "replace-registry-host"], cwd=source,
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout.strip()
    registry = subprocess.run(
        [*command, "config", "get", "registry"], cwd=source,
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout.strip()
    if not registry:
        raise RuntimeError("configured npm registry is unavailable")
    admitted = admission.audit_lock(
        json.loads(retained["package-lock.json"]),
        lambda name, version: admission.read_metadata(command, source, name, version),
        replace_default_registry=replacement == "npmjs",
    )
    subprocess.run(
        [*command, "ci", "--ignore-scripts", "--strict-peer-deps", "--no-audit", "--fund=false"],
        cwd=project, env={**os.environ, "npm_config_registry": registry}, check=True, timeout=120,
    )
    if any((project / name).read_bytes() != body for name, body in retained.items()):
        raise RuntimeError("Dev Containers manifests changed after dependency admission")
    binary = project / "node_modules/.bin/devcontainer"
    version = subprocess.run(
        [str(binary), "--version"], check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    if version != expected_version:
        raise RuntimeError("installed Dev Containers CLI does not match its reviewed version")
    print(f"Admitted {admitted} seasoned locked dependency; Dev Containers CLI {version} is ready.")
    return binary


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    binary = provision(root, Path(os.environ["RUNNER_TEMP"]) / "periodic-devcontainer-cli")
    with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as output:
        output.write(str(binary.parent) + "\n")


if __name__ == "__main__":
    main()
