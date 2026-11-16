"""Static managed-Python inputs are validated before publication, not at launch."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "_bootstrap_declaration_generator", Path(__file__).resolve().parents[1] / "generate.py",
)
assert _spec and _spec.loader
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)


def _declare(tmp_path: Path, version: object, requirement: str = ">=3.11,<3.13") -> Path:
    scripts = tmp_path / "plugin/scripts"
    scripts.mkdir(parents=True)
    for name in ("install.sh", "install.ps1", "resolve-runtime.sh", "resolve-runtime.ps1"):
        (scripts / name).write_text("# fixture\n", encoding="utf-8")
    manifest = scripts.parent / "payload-invocation.json"
    data = {
        "schema": "copilot-extensions.payload-invocation", "version": 1,
        "command": "agent-example", "module": "agent_example", "runtimeRoot": ".agent-example",
        "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION", "purpose": "Example runtime",
        "bootstrapPython": version,
    }
    manifest.write_text(json.dumps(data), encoding="utf-8")
    (manifest.parent / "pyproject.toml").write_text(
        '[project]\nname = "agent-example"\nversion = "1"\n'
        f"requires-python = {json.dumps(requirement)}\n",
        encoding="utf-8",
    )
    return manifest


def test_static_bootstrap_declaration_does_not_change_launchers(tmp_path: Path) -> None:
    manifest = _declare(tmp_path, "3.11.11")
    outputs = generator.expected_files(manifest)
    declaration = manifest.parent / "scripts/bootstrap-python-version.txt"
    assert outputs.pop(declaration) == "cpython@3.11.11\n"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["bootstrapPython"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert generator.expected_files(manifest) == outputs


@pytest.mark.parametrize("version", [
    None, True, "3.11", "pypy@3.11.11", "3.13.1t", "3.11.11\n", "../python", "--system",
])
def test_invalid_bootstrap_requests_refuse_before_writes(tmp_path: Path, version: object) -> None:
    manifest = _declare(tmp_path, version)
    with pytest.raises(ValueError, match="exact stable CPython"):
        generator.process_manifest(manifest, check=False)
    assert not (manifest.parent / "bin").exists()
    assert not (manifest.parent / "scripts/bootstrap-python-version.txt").exists()


@pytest.mark.parametrize("requirement", ["", "not-a-specifier", ">=3.12", ">=3.11,!=3.11.11"])
def test_incompatible_project_refuses_before_writes(tmp_path: Path, requirement: str) -> None:
    manifest = _declare(tmp_path, "3.11.11", requirement)
    with pytest.raises(ValueError, match="invalid bootstrap Python project"):
        generator.process_manifest(manifest, check=False)
    assert not (manifest.parent / "bin").exists()


@pytest.mark.parametrize("metadata", [None, "[project]\nname='example'\n", "[project\n", "project='invalid'\n"])
def test_missing_or_malformed_project_refuses(tmp_path: Path, metadata: str | None) -> None:
    manifest = _declare(tmp_path, "3.11.11")
    project = manifest.parent / "pyproject.toml"
    if metadata is None:
        project.unlink()
    else:
        project.write_text(metadata, encoding="utf-8")
    with pytest.raises(ValueError, match="bootstrapPython|invalid bootstrap Python project"):
        generator.expected_files(manifest)
