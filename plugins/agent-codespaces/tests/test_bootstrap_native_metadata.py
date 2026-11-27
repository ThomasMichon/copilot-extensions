"""Native drift metadata with poisoned interpreters and an inert driver."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

import pytest

from .managed_command_fixture import packaged_command, snapshot

__all__ = ["packaged_command"]

pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX metadata; PowerShell already reads JSON natively"),
]
PLUGIN = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash", path="/bin:/usr/bin") if sys.platform == "darwin" else shutil.which("bash")


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@dataclass
class MetadataFixture:
    layout: str
    home: Path
    payload: Path
    project: Path
    env: dict[str, str]
    calls: Path
    poison: Path
    queries: Path

    @property
    def manifest(self) -> Path:
        return self.home / ".agent-codespaces/deploy-manifest.json"

    def write_version(self, version: object) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(json.dumps({
            "source": {"version": version, "path": str(self.payload)},
        }), encoding="utf-8")

    def run(self) -> tuple[subprocess.CompletedProcess[str], float]:
        assert BASH is not None
        before = snapshot(self.home)
        started = time.monotonic()
        result = subprocess.run(
            [BASH, str(self.payload / "scripts/bootstrap-check.sh")],
            env=self.env, cwd=self.project, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        elapsed = time.monotonic() - started
        assert result.stdout == "{}", result
        assert not self.poison.exists(), "an ambient Python/UV or selected runtime was executed"
        assert snapshot(self.home) == before
        return result, elapsed

    def query_count(self) -> int:
        return len(self.queries.read_text().splitlines()) if self.queries.exists() else 0

    def assert_handoff(self, action: str) -> None:
        assert self.calls.read_text(encoding="utf-8").splitlines() == [
            str(self.home / ".copilot-extensions/update-driver"), str(self.payload),
            str(self.manifest.parent), str(self.project), "--installer-action", action,
        ]


@pytest.fixture(params=["raw", "materialized"])
def bootstrap_metadata(
    tmp_path: Path, request: pytest.FixtureRequest, packaged_command: Path,
) -> MetadataFixture:
    if BASH is None:
        pytest.skip("Bash unavailable")
    source = PLUGIN if request.param == "raw" else packaged_command
    home = tmp_path / "home caf\u00e9"
    payload = home / ".copilot/installed-plugins/fixture-market/agent-codespaces"
    scripts = payload / "scripts"
    (scripts / "installation-context").mkdir(parents=True)
    project = tmp_path / "project with spaces"
    project.mkdir()
    shutil.copy2(source / "scripts/bootstrap-check.sh", scripts / "bootstrap-check.sh")
    shutil.copy2(
        source / "scripts/installation-context/json-query.awk",
        scripts / "installation-context/json-query.awk",
    )
    (payload / "plugin.json").write_text('{"name":"agent-codespaces"}\n', encoding="utf-8")
    (payload / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    calls, poison, queries = (tmp_path / name for name in ("handoff", "ambient", "queries"))
    driver = payload / "libs/update-driver/scripts/first-use.sh"
    _executable(
        driver,
        "#!/usr/bin/env bash\n"
        "# Installation driver lifecycle contract: vault-stamp-v1\n"
        'printf "%s\\n" "$@" >"$CODESPACES_METADATA_CALLS"\n'
        'printf "launched:123 log:%s/first-use-00000000-0000-0000-0000-000000000000.log\\n" "$1"\n',
    )
    driver_query = driver.parent.parent / "src/update_driver/json-query.awk"
    driver_query.parent.mkdir(parents=True)
    shutil.copy2(PLUGIN.parents[1] / "libs/update-driver/src/update_driver/json-query.awk", driver_query)
    tools = tmp_path / "native tools"
    tools.mkdir()
    for name in ("dirname", "grep", "sed", "tr", "uname", "basename"):
        address = shutil.which(name)
        assert address is not None, name
        (tools / name).symlink_to(address)
    (tools / "bash").symlink_to(BASH)
    awk = shutil.which("awk")
    assert awk is not None
    _executable(
        tools / "awk",
        '#!/bin/sh\nprintf "query\\n" >>"$CODESPACES_METADATA_QUERIES"\n'
        f'exec {shlex.quote(awk)} "$@"\n',
    )
    for name in ("python", "python3", "py", "uv", "uvx"):
        _executable(
            tools / name,
            '#!/bin/sh\nprintf "ambient\\n" >>"$CODESPACES_METADATA_POISON"\nexit 97\n',
        )
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith((
            "COPILOT_", "AGENT_", "PYTHON", "UV_", "CONDA", "VIRTUAL_ENV",
            "BOOTSTRAP_KILLSWITCH_", "CODESPACES_", "UPDATE_DRIVER_",
        ))
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), PATH=str(tools),
        CODESPACES_METADATA_CALLS=str(calls), CODESPACES_METADATA_POISON=str(poison),
        CODESPACES_METADATA_QUERIES=str(queries),
        PYTHONHOME=str(tools), PYTHONPATH=str(tools),
        VIRTUAL_ENV=str(tools), CONDA_PREFIX=str(tools),
        UV_PYTHON=str(tools / "python"), UV_OFFLINE="1",
    )
    fixture = MetadataFixture(str(request.param), home, payload, project, env, calls, poison, queries)
    fixture.write_version("1.0.0")
    return fixture


@pytest.mark.parametrize("selection", ["legacy-venv", "current-marker"])
def test_metadata_current_needs_no_python(
    bootstrap_metadata: MetadataFixture, selection: str,
    record_property: Callable[[str, object], None],
) -> None:
    fixture = bootstrap_metadata
    if selection == "legacy-venv":
        (fixture.manifest.parent / ".venv").mkdir()
    else:
        (fixture.manifest.parent / "current-version").write_text("1.0.0\n", encoding="utf-8")
        _executable(
            fixture.manifest.parent / "versions/1.0.0/bin/python",
            '#!/bin/sh\nprintf "runtime\\n" >>"$CODESPACES_METADATA_POISON"\nexit 97\n',
        )
    samples = []
    for _ in range(2):
        result, elapsed = fixture.run()
        assert result.returncode == 0 and result.stderr == "", result
        samples.append(elapsed)
    assert not fixture.calls.exists()
    assert fixture.query_count() == 2
    record_property("codespaces_native_metadata", json.dumps({
        "layout": fixture.layout, "selection": selection, "samples_seconds": samples,
        "query_calls": fixture.query_count(), "python_calls": 0, "uv_calls": 0,
        "driver_calls": 0, "instrumentation": "native command counting shim",
    }))


@pytest.mark.parametrize("phase", ["cold", "stale", "missing-current"])
def test_metadata_preserves_existing_action(bootstrap_metadata: MetadataFixture, phase: str) -> None:
    fixture = bootstrap_metadata
    if phase == "cold":
        fixture.manifest.unlink()
    elif phase == "stale":
        fixture.write_version("0.9.0")
        (fixture.manifest.parent / ".venv").mkdir()
    result, _ = fixture.run()
    if sys.platform == "linux":
        assert result.returncode == 0, result
        fixture.assert_handoff("stamp" if phase == "cold" else "provision")
        assert fixture.query_count() == (2 if phase == "cold" else 3)
    else:
        assert result.returncode == 3 and "qualified Linux Bash" in result.stderr, result
        assert not fixture.calls.exists()
        assert fixture.query_count() == (0 if phase == "cold" else 1)


@pytest.mark.parametrize("condition", ["context", "no-self", "pinned", "witness"])
def test_metadata_retains_existing_holds(bootstrap_metadata: MetadataFixture, condition: str) -> None:
    fixture = bootstrap_metadata
    fixture.write_version("0.9.0")
    if condition == "context":
        fixture.env["COPILOT_EXTENSIONS_CONTEXT"] = str(fixture.home / "context.json")
        fixture.manifest.write_text("{", encoding="utf-8")
    elif condition == "no-self":
        fixture.env["AGENT_CODESPACES_NO_SELFPROVISION"] = "1"
    elif condition == "pinned":
        fixture.env["AGENT_CODESPACES_PINNED_CONTEXT"] = ""
    else:
        fixture.env["UPDATE_DRIVER_WITNESS"] = ""
    result, _ = fixture.run()
    assert result.returncode == (3 if condition == "witness" else 0), result
    assert not fixture.calls.exists()
    assert fixture.query_count() == (0 if condition == "context" else 1)


@pytest.mark.parametrize("invalid", [
    "syntax", "empty", "nonstring", "missing-field", "duplicate", "case-ambiguous",
    "invalid-utf8", "escaped-nul", "literal-nul", "missing-reader",
])
def test_metadata_invalid_never_submits(bootstrap_metadata: MetadataFixture, invalid: str) -> None:
    fixture = bootstrap_metadata
    if invalid == "syntax":
        fixture.manifest.write_text("{", encoding="utf-8")
    elif invalid == "empty":
        fixture.write_version("")
    elif invalid == "nonstring":
        fixture.write_version(True)
    elif invalid == "missing-field":
        fixture.manifest.write_text('{"source":{}}', encoding="utf-8")
    elif invalid in {"duplicate", "case-ambiguous"}:
        name = "version" if invalid == "duplicate" else "Version"
        fixture.manifest.write_text(
            '{"source":{"version":"1.0.0","' + name + '":"1.0.0"}}', encoding="utf-8",
        )
    elif invalid == "invalid-utf8":
        fixture.manifest.write_bytes(b'{"source":{"version":"\xff"}}')
    elif invalid == "escaped-nul":
        fixture.write_version("1.0.0\0")
    elif invalid == "literal-nul":
        fixture.manifest.write_bytes(b'{"source":{"version":"1.0.0\0"}}')
    else:
        (fixture.payload / "scripts/installation-context/json-query.awk").unlink()
    result, _ = fixture.run()
    assert result.returncode == 3 and "metadata" in result.stderr, result
    assert not fixture.calls.exists()
    assert fixture.query_count() == (0 if invalid in {"literal-nul", "missing-reader"} else 1)


def test_metadata_version_linefeed_keeps_identity(bootstrap_metadata: MetadataFixture) -> None:
    fixture = bootstrap_metadata
    fixture.write_version("1.0.0\n")
    (fixture.manifest.parent / ".venv").mkdir()
    result, _ = fixture.run()
    if sys.platform == "linux":
        assert result.returncode == 0, result
        fixture.assert_handoff("provision")
        assert fixture.query_count() == 3
    else:
        assert result.returncode == 3 and "qualified Linux Bash" in result.stderr, result
        assert not fixture.calls.exists()
        assert fixture.query_count() == 1


def test_metadata_missing_reader_does_not_gate_first_stamp(bootstrap_metadata: MetadataFixture) -> None:
    fixture = bootstrap_metadata
    fixture.manifest.unlink()
    (fixture.payload / "scripts/installation-context/json-query.awk").unlink()
    result, _ = fixture.run()
    if sys.platform == "linux":
        assert result.returncode == 0, result
        fixture.assert_handoff("stamp")
        assert fixture.query_count() == 2
    else:
        assert result.returncode == 3 and "qualified Linux Bash" in result.stderr, result
        assert not fixture.calls.exists()
        assert fixture.query_count() == 0
