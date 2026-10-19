"""Immutable preparation seeds; each contract receives independent mutable state."""
from __future__ import annotations

import copy
import os
import shutil
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from _promotion_workflow_test_support import source_workflow

_SEEDS = pytest.StashKey[dict[Callable[[Path], Path], Path]]()
_SEED_GIT_CONFIG = (
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
    ("gc.autoDetach", "false"),
    ("maintenance.autoDetach", "false"),
)
SEED_GIT_OPTIONS = tuple(
    argument for key, value in _SEED_GIT_CONFIG for argument in ("-c", f"{key}={value}")
)


def copy_fixture_seed(
    config: pytest.Config,
    factory: pytest.TempPathFactory,
    prepare: Callable[[Path], Path],
    destination: Path,
) -> Path:
    seeds = config.stash.setdefault(_SEEDS, {})
    if prepare not in seeds:
        with pytest.MonkeyPatch.context() as environment:
            environment.setenv("GIT_CONFIG_NOSYSTEM", "1")
            environment.setenv("GIT_CONFIG_GLOBAL", os.devnull)
            count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
            for index, (key, value) in enumerate(_SEED_GIT_CONFIG, start=count):
                environment.setenv(f"GIT_CONFIG_KEY_{index}", key)
                environment.setenv(f"GIT_CONFIG_VALUE_{index}", value)
            environment.setenv("GIT_CONFIG_COUNT", str(count + len(_SEED_GIT_CONFIG)))
            seeds[prepare] = prepare(factory.mktemp("artifact-preparation-seed"))
    # Copy objects, refs, index and config too: hardlinks/shared writable metadata
    # would let adversarial mutations escape into the seed or another test.
    return Path(shutil.copytree(seeds[prepare], destination))


@pytest.fixture
def artifact_repo(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, pytestconfig: pytest.Config,
) -> Path:
    from test_promote_release import repo

    return copy_fixture_seed(
        pytestconfig, tmp_path_factory, repo.__wrapped__, tmp_path / "repo",
    )


@lru_cache(maxsize=32)
def _parsed_yaml(text: str | bytes) -> Any:
    return yaml.safe_load(text)


def fixture_yaml(text: str | bytes) -> Any:
    return copy.deepcopy(_parsed_yaml(text))


@lru_cache(maxsize=8)
def _source_document(
    root: Path, name: str, controller: bytes, document: bytes, lineage: bytes | None,
) -> dict[str, Any]:
    return source_workflow(root, name)


def source_workflow_snapshot(root: Path, name: str) -> dict[str, Any]:
    """Cache immutable source parsing, not mutable generated-checkout witnesses."""
    workflows = root / ".github/workflows"
    state = root / ".github/release-pipeline-state.json"
    lineage = state.read_bytes() if state.is_file() else None
    return copy.deepcopy(_source_document(
        root, name, (workflows / "validate-and-promote.yml").read_bytes(),
        (workflows / name).read_bytes(), lineage,
    ))
