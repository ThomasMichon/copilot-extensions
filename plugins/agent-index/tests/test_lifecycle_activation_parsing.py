from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess

import pytest
import yaml


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "resolve-activation-role.py"
)
SPEC = importlib.util.spec_from_file_location("resolve_activation_role", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write(path: Path, value: object) -> Path:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def test_plural_canonical_yaml_activates_primary_and_secondary(tmp_path):
    path = _write(
        tmp_path / "config.yaml",
        {
            "indexers": [
                {"machine": "primary", "ssh": "primary"},
                {"machine": "secondary", "ssh": "secondary"},
            ],
            "corpus": {"sources": [{"name": "git:demo"}]},
        },
    )

    assert MODULE.resolve(path, "primary") == "host"
    assert MODULE.resolve(path, "secondary") == "host"
    assert MODULE.resolve(path, "client") == "client"


def test_singular_flow_yaml_activates_host(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "indexer: {machine: host, endpoint: 'http://127.0.0.1:8000'}\n",
        encoding="utf-8",
    )

    assert MODULE.resolve(path, "host") == "host"
    assert MODULE.resolve(path, "client") == "client"


def test_empty_or_missing_designation_is_unconfigured(tmp_path):
    empty = _write(tmp_path / "empty.yaml", {"indexers": []})
    unrelated = _write(
        tmp_path / "unrelated.yaml",
        {"worker": {"machine": "host"}},
    )

    assert MODULE.resolve(empty, "host") == "unconfigured"
    assert MODULE.resolve(unrelated, "host") == "unconfigured"
    assert MODULE.resolve(tmp_path / "missing.yaml", "host") == "unconfigured"


def test_fallback_parser_handles_canonical_and_inline_forms():
    canonical = yaml.safe_dump(
        {
            "indexers": [
                {"machine": "primary"},
                {"machine": "secondary"},
            ],
            "corpus": {"sources": []},
        },
        sort_keys=False,
    )

    assert MODULE._fallback_machines(canonical) == ["primary", "secondary"]
    assert MODULE._fallback_machines(
        "indexer: {machine: host, endpoint: http://127.0.0.1:8000}\n"
    ) == ["host"]


def test_posix_ensure_hook_resolves_helper_before_client_exit(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX hook execution is covered on Linux")
    if subprocess.run(["bash", "--version"], capture_output=True).returncode != 0:
        pytest.skip("bash is unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "indexer:\n  machine: another-host\n  ssh: another-host\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    runtime = home / ".agent-index"
    runtime.mkdir(parents=True)
    (runtime / "deploy-manifest.json").write_text("{}\n", encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(home),
        "AGENT_INDEX_MACHINE": "client-host",
    }

    result = subprocess.run(
        ["bash", SCRIPT.parent / "ensure-service.sh"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "unbound variable" not in result.stderr
