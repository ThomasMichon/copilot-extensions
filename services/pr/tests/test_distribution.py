"""Normal wheel artifact and existing generic role declaration, not deployment proof."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import cast

import pytest

from fleet_manager.komodo_roles import KomodoRoleProvider
from fleet_manager.role_bindings import parse_bindings


def _snapshot(root: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    project = root / "services" / "pr"
    project.mkdir(parents=True)
    for name in (
        "pyproject.toml", "README.md", "MANIFEST.in", "LICENSE",
        "setup.py", "_build_runtime_resource.py", "packaged-assets.json",
    ):
        shutil.copy2(source / name, project / name)
    shutil.copytree(source / "container", project / "container")
    shutil.copytree(
        source / "src", project / "src",
        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"),
    )
    repository = source.parents[1]
    for relative in ("libs/versioned-runtime", "plugins/ai-attribution/scripts"):
        shutil.copytree(repository / relative, root / relative)
    return project


def _build(project: Path, output: Path, operation: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c",
         f"from setuptools.build_meta import {operation}; {operation}({str(output)!r})"],
        cwd=project, capture_output=True, text=True, timeout=30,
        creationflags=cast(int, getattr(subprocess, "CREATE_NO_WINDOW")) if os.name == "nt" else 0,
    )


def _consume(wheel: Path, outside: Path) -> None:
    outside.mkdir()
    (outside / "attribution_profile.py").write_text("raise RuntimeError('ambient substitute')\n")
    code = """
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
before = list(sys.path)
from agent_pull_requests_service._attribution import attribution_profile as profile
from agent_pull_requests_service._attribution import comment_preparation as comments
assert sys.path == before
assert profile.__file__.startswith(sys.argv[1])
assert comments.__file__.startswith(sys.argv[1])
assert 'attribution_profile' not in sys.modules
prepared = comments.prepare(
    'Review text.', profile.validate({'version': 1}),
    {'domain': 'forge', 'visibility': 'public', 'activity': 'comment', 'required': True},
    {'protocol': comments.SOURCE_PROTOCOL, 'privacy': 'public',
     'origin': {'codename': 'example-label'}, 'effects': 'none'},
    comment_id='11111111-1111-4111-8111-111111111111',
    write_id='22222222-2222-4222-8222-222222222222',
)
assert prepared['body'].startswith('[AI-assisted] Review text.')
assert prepared['body'].count(comments.PREFIX) == 1
assert prepared['effects'] == 'none'
"""
    child = subprocess.run(
        [sys.executable, "-I", "-c", code, str(wheel), str(outside)],
        cwd=outside, capture_output=True, text=True, timeout=10,
        creationflags=cast(int, getattr(subprocess, "CREATE_NO_WINDOW")) if os.name == "nt" else 0,
    )
    assert child.returncode == 0, child.stdout + child.stderr


def test_independent_wheel_has_no_source_checkout_or_marketplace_requirement(tmp_path: Path) -> None:
    project = _snapshot(tmp_path / "checkout")
    output = tmp_path / "artifacts"
    output.mkdir()
    copied_directories = [
        project / "src", *(path for path in (project / "src").rglob("*") if path.is_dir()),
    ]
    # Rehearse a read-only snapshot on the owned copy, never on the source.
    for directory in copied_directories:
        directory.chmod(directory.stat().st_mode & ~stat.S_IWUSR)
    # The backend must create egg-info beside the copied package.
    for directory in copied_directories:
        directory.chmod(directory.stat().st_mode | stat.S_IWUSR)
    for operation in ("build_wheel", "build_sdist"):
        built = _build(project, output, operation)
        assert built.returncode == 0, built.stdout + built.stderr
    wheel_path = next(output.glob("*.whl"))
    helpers = Path(__file__).resolve().parents[3] / "plugins" / "ai-attribution" / "scripts"
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
        metadata = Parser().parsestr(wheel.read(next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )).decode())
        assert metadata["Name"] == "agent-pull-requests-service"
        requirements = metadata.get_all("Requires-Dist")
        assert requirements is not None
        assert any(item.startswith("agent-pull-requests[fleet]") for item in requirements)
        assert not any(item.startswith("agent-worktrees") for item in requirements)
        assert not any("@" in item or "file:" in item for item in requirements)
        assert "agent_pull_requests_service/hosting.py" in names
        assert "agent_pull_requests_service/reports.py" in names
        for name in ("attribution_profile.py", "comment_preparation.py"):
            assert wheel.read(f"agent_pull_requests_service/_attribution/{name}") == (
                helpers / name
            ).read_bytes()
        assert not any(name.endswith("_versioned_runtime.py") for name in names)
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
        assert not any("copilot" in name or name.endswith(".pem") for name in names)
        entries = wheel.read(next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )).decode()
        assert "agent-pull-requests-service = agent_pull_requests_service.__main__:main" in entries
    with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
        names = archive.getnames()
        assert any(name.endswith("/container/Dockerfile") for name in names)
        assert any(name.endswith("/container/role-bindings.example.json") for name in names)
        archive.extractall(tmp_path / "sdist", filter="data")
    _consume(wheel_path, tmp_path / "outside-wheel")
    rebuilt = tmp_path / "rebuilt"
    rebuilt.mkdir()
    archived = next((tmp_path / "sdist").iterdir())
    built = _build(archived, rebuilt, "build_wheel")
    assert built.returncode == 0, built.stdout + built.stderr
    rebuilt_wheel = next(rebuilt.glob("*.whl"))
    with zipfile.ZipFile(rebuilt_wheel) as wheel:
        for name in ("attribution_profile.py", "comment_preparation.py"):
            assert wheel.read(f"agent_pull_requests_service/_attribution/{name}") == (
                helpers / name
            ).read_bytes()
    _consume(rebuilt_wheel, tmp_path / "outside-sdist")
    child = subprocess.run(
        [sys.executable, "-I", "-m", "agent_pull_requests_service", "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
        creationflags=cast(int, getattr(subprocess, "CREATE_NO_WINDOW")) if os.name == "nt" else 0,
    )
    assert child.returncode == 0
    assert all(command in child.stdout for command in (
        "provision", "serve", "ready", "list", "query", "subscribe", "inspect",
        "events", "acknowledge", "cancel", "follow",
    ))


@pytest.mark.parametrize("fault", ["missing", "mismatched"])
def test_helper_build_refuses_incomplete_or_substituted_canonical_source(
    tmp_path: Path, fault: str,
) -> None:
    project = _snapshot(tmp_path / "checkout")
    if fault == "missing":
        canonical = tmp_path / "checkout/plugins/ai-attribution/scripts/comment_preparation.py"
        canonical.rename(canonical.with_suffix(".missing"))
    else:
        (project / "src/agent_pull_requests_service/_attribution/comment_preparation.py").write_text(
            "raise RuntimeError('substituted helper')\n",
        )
    output = tmp_path / "artifacts"
    output.mkdir()
    built = _build(project, output, "build_wheel")
    assert built.returncode != 0
    assert ("missing" if fault == "missing" else "differs from the canonical") in built.stderr


def test_role_example_uses_existing_reference_only_container_service_contract() -> None:
    source = Path(__file__).resolve().parents[1]
    providers, bindings = parse_bindings(json.loads(
        (source / "container" / "role-bindings.example.json").read_text("utf-8"),
    ))
    resource = KomodoRoleProvider().project(providers[0], bindings[0])
    assert resource.artifact_ref == "approved-pr-image"
    assert resource.configuration_ref == "pr-service-config"
    assert bindings[0].implementation == "container.service"
    assert resource.target == "host-a"


def test_container_recipe_is_explicit_offline_nonroot_and_does_not_copy_host_state() -> None:
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
