"""Retaining a package resource must not truncate its admitted parent project."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "libs" / "installer-engine" / (
    "installer_dependency_policy.py"
)
SPEC = importlib.util.spec_from_file_location("dependency_resource_overlap_policy", PATH)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


@pytest.mark.parametrize("editable", [False, True])
def test_resource_descendant_preserves_the_complete_admitted_project(
    tmp_path, monkeypatch, editable,
):
    source = tmp_path / "project"
    package = source / "src" / "fixture"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 'source'\n", encoding="utf-8")
    metadata = (
        '[build-system]\nrequires=[]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="fixture"\nversion="1.0"\ndependencies=[]\n'
        '[tool.setuptools.packages.find]\nwhere=["src"]\n'
    )
    (source / "pyproject.toml").write_text(metadata, encoding="utf-8")
    (source / "README.md").write_text("Complete source\n", encoding="utf-8")
    settings = """PipInstallSettings { settings: PipSettings {
      index_locations: IndexLocations { indexes: [], flat_index: [], },
      exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
    }}"""
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, settings, ""),
    )
    arguments = (["-e"] if editable else []) + [str(source)]
    # The projection-only UV probe is synthetic; all source copies and hashes are real.
    with policy.retain_uv_install(
        "fixture-uv", arguments, cwd=tmp_path, trusted_source_roots=(tmp_path,),
        retained_resource_roots=(package,),
    ) as transaction:
        index = transaction.argv.index("-e") + 1 if editable else 3
        retained = Path(transaction.argv[index])
        assert (retained / "pyproject.toml").is_file()
        assert (retained / "README.md").read_text("utf-8") == "Complete source\n"
        assert (retained / "src" / "fixture" / "__init__.py").read_text("utf-8") == (
            "VALUE = 'source'\n"
        )
        assert (source / "pyproject.toml").read_text("utf-8") == metadata
