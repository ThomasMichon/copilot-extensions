"""Synthetic release-closure decisions; no Git, process or network actors."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import materialize_main as materializer
import uv_editable_ref as editable


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "kind", ["missing", "editable", "escape", "conditional-route", "reentry"]
)
def test_materialized_closure_refuses_incomplete_sources(tmp_path, kind):
    consumer = tmp_path / "plugins" / "example"
    first = consumer / "libs" / "first"
    _write(first / "pyproject.toml", '[project]\nname = "first"\nversion = "1"\n')
    declarations = {
        "missing": 'first = { path = "libs/missing" }\n',
        "editable": 'first = { path = "libs/first", editable = true }\n',
        "escape": 'first = { path = "libs/first" }\n',
        "reentry": 'first = { path = "../other/../example/libs/first" }\n',
        "conditional-route": (
            'first = [{ path = "libs/missing/../first", marker = "sys_platform == \'win32\'" },'
            '{ path = "libs/first", marker = "sys_platform != \'win32\'" }]\n'
        ),
    }
    _write(consumer / "pyproject.toml", "[tool.uv.sources]\n" + declarations[kind])
    if kind == "reentry":
        (consumer.parent / "other").mkdir()
    if kind == "escape":
        _write(
            first / "pyproject.toml",
            '[tool.uv.sources]\nother = {path = "../../../../libs/other"}',
        )
        _write(
            tmp_path / "libs" / "other" / "pyproject.toml",
            '[project]\nname = "other"\n',
        )
    assert editable.materialized_local_source_problems(consumer)


@pytest.mark.parametrize("nested", [False, True])
def test_materialized_closure_refuses_retained_workspace_sources(tmp_path, nested):
    consumer = tmp_path / "plugins" / "example"
    package = consumer / "libs" / "nested" if nested else consumer
    if nested:
        _write(
            consumer / "pyproject.toml",
            '[tool.uv.sources]\nnested = {path = "libs/nested"}\n',
        )
    _write(
        package / "pyproject.toml", "[tool.uv.sources]\nshared = {workspace = true}\n"
    )
    assert any(
        "workspace mapping" in problem
        for problem in editable.materialized_local_source_problems(consumer)
    )


@pytest.mark.parametrize("kind", ["git", "url"])
def test_materialized_closure_refuses_file_uri_sources(tmp_path, kind):
    for nested in (False, True):
        consumer = tmp_path / str(nested) / "plugins" / "example"
        package = consumer / "libs" / "nested" if nested else consumer
        if nested:
            _write(
                consumer / "pyproject.toml",
                '[tool.uv.sources]\nnested = {path = "libs/nested"}\n',
            )
        _write(
            package / "pyproject.toml",
            f'[tool.uv.sources]\nshared = {{{kind} = "file:///unavailable/package"}}\n',
        )
        assert any(
            "file URI mapping" in problem
            for problem in editable.materialized_local_source_problems(consumer)
        )


def test_materializer_refuses_missing_standalone_manifest(tmp_path):
    (tmp_path / "worktree-manager").mkdir()
    (tmp_path / "plugins" / "skill-only").mkdir(parents=True)
    log = materializer.materialize(tmp_path, canonical_root=tmp_path)
    assert (
        len(log) == 1 and log[0].startswith("SKIP local source") and "missing" in log[0]
    )


@pytest.mark.parametrize("nested", [False, True])
def test_materialized_closure_audits_every_requirement_surface(tmp_path, nested):
    requirement = '"missing [extra] @ file:///unavailable/package"'
    surfaces = [
        '[project]\ndependencies = ["missing[] @ file:///unavailable/package"]\n',
        "[project.optional-dependencies]\nextra = [" + requirement + "]\n",
        "[build-system]\nrequires = [" + requirement + "]\n",
        '[dependency-groups]\ndev = [{include-group = "common"}, '
        + requirement
        + "]\n",
        *(
            "[tool.uv]\n" + key + " = [" + requirement + "]\n"
            for key in (
                "dev-dependencies",
                "constraint-dependencies",
                "override-dependencies",
                "build-constraint-dependencies",
            )
        ),
        "[[tool.uv.dependency-metadata]]\nrequires-dist = [" + requirement + "]\n",
        "[tool.uv.extra-build-dependencies]\nexample = [{requirement = "
        + requirement
        + ", match-runtime = true}]\n",
    ]
    consumer = tmp_path / "plugins" / "example"
    package = consumer / "libs" / "nested" if nested else consumer
    if nested:
        _write(
            consumer / "pyproject.toml",
            '[tool.uv.sources]\nnested = {path = "libs/nested"}\n',
        )
    for declaration in surfaces:
        _write(package / "pyproject.toml", declaration)
        problems = editable.materialized_local_source_problems(consumer)
        assert any("direct local requirement" in problem for problem in problems), (
            declaration
        )


def test_materialized_closure_accepts_siblings_archives_and_registry_requirements(
    tmp_path,
):
    consumer = tmp_path / "plugins" / "example"
    _write(
        consumer / "pyproject.toml",
        '[project]\ndependencies = ["remote @ https://example.invalid/package.whl"]\n'
        '[tool.uv.sources]\nfirst = {path = "libs/first"}\n'
        'archive = {path = "vendor/fixture.whl"}\n'
        'remote-git = {git = "https://example.invalid/repository.git"}\n'
        'remote-url = {url = "https://example.invalid/package.whl"}\n',
    )
    for name, dependency in (("first", "second"), ("second", "first")):
        _write(
            consumer / "libs" / name / "pyproject.toml",
            f'[tool.uv.sources]\n{dependency} = {{path = "../{dependency}"}}\n',
        )
    _write(consumer / "vendor" / "fixture.whl", "fixture")
    assert editable.materialized_local_source_problems(consumer) == []
