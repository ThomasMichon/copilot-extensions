"""Normal wheel artifact and existing generic role declaration, not deployment proof."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

from fleet_manager.komodo_roles import KomodoRoleProvider
from fleet_manager.role_bindings import parse_bindings


def test_independent_wheel_has_no_source_checkout_or_marketplace_requirement(tmp_path):
    source = Path(__file__).resolve().parents[1]
    project, output = tmp_path / "project", tmp_path / "artifacts"
    project.mkdir()
    output.mkdir()
    for name in ("pyproject.toml", "README.md", "MANIFEST.in"):
        shutil.copy2(source / name, project / name)
    shutil.copytree(source / "container", project / "container")
    shutil.copytree(
        source / "src", project / "src",
        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"),
    )
    built = subprocess.run(
        [sys.executable, "-I", "-c",
         "from setuptools.build_meta import build_wheel,build_sdist; "
         f"build_wheel({str(output)!r});build_sdist({str(output)!r})"],
        cwd=project, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    with zipfile.ZipFile(next(output.glob("*.whl"))) as wheel:
        names = wheel.namelist()
        metadata = Parser().parsestr(wheel.read(next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )).decode())
        assert metadata["Name"] == "agent-pull-requests-service"
        requirements = metadata.get_all("Requires-Dist")
        assert any(item.startswith("agent-pull-requests[fleet]") for item in requirements)
        assert not any("@" in item or "file:" in item for item in requirements)
        assert "agent_pull_requests_service/hosting.py" in names
        assert not any("copilot" in name or name.endswith(".pem") for name in names)
        entries = wheel.read(next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )).decode()
        assert "agent-pull-requests-service = agent_pull_requests_service.__main__:main" in entries
    with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
        names = archive.getnames()
        assert any(name.endswith("/container/Dockerfile") for name in names)
        assert any(name.endswith("/container/role-bindings.example.json") for name in names)
    child = subprocess.run(
        [sys.executable, "-I", "-m", "agent_pull_requests_service", "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert child.returncode == 0 and "{provision,serve,ready}" in child.stdout


def test_role_example_uses_existing_reference_only_container_service_contract():
    source = Path(__file__).resolve().parents[1]
    providers, bindings = parse_bindings(json.loads(
        (source / "container" / "role-bindings.example.json").read_text("utf-8"),
    ))
    resource = KomodoRoleProvider().project(providers[0], bindings[0])
    assert resource.artifact_ref == "approved-pr-image"
    assert resource.configuration_ref == "pr-service-config"
    assert bindings[0].implementation == "container.service"
    assert resource.target == "host-a"


def test_container_recipe_is_explicit_offline_nonroot_and_does_not_copy_host_state():
    recipe = (Path(__file__).resolve().parents[1] / "container" / "Dockerfile").read_text("utf-8")
    assert recipe.startswith("ARG PYTHON_BASE\nFROM ${PYTHON_BASE}")
    assert "@sha256:" in recipe
    assert "--no-index" in recipe and "--require-hashes" in recipe
    assert "--only-binary=:all:" in recipe
    assert "USER 65532:65532" in recipe
    assert "STOPSIGNAL SIGTERM" in recipe and "HEALTHCHECK" in recipe
    copies = [line for line in recipe.splitlines() if line.startswith("COPY ")]
    assert copies == [
        "COPY wheels/ /tmp/pr-wheels/", "COPY requirements.lock /tmp/pr-requirements.lock",
    ]
