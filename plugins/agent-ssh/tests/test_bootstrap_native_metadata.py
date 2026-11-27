"""Native manifest-selected metadata with poisoned interpreters and an inert driver."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import cast

import pytest

_fixture = import_module(
    ".managed_command_fixture", __package__,
) if __package__ else import_module("managed_command_fixture")
snapshot = cast(Callable[[Path], dict[str, str]], _fixture.snapshot)
packaged_command = _fixture.packaged_command
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
    invoked: Path
    selected: Path
    project: Path
    env: dict[str, str]
    calls: Path
    poison: Path
    queries: Path

    @property
    def manifest(self) -> Path:
        return self.home / ".agent-ssh/deploy-manifest.json"

    def write_source(self, **fields: object) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        source = {"version": "1.0.0", "path": str(self.selected), **fields}
        self.manifest.write_text(json.dumps({"source": source}), encoding="utf-8")

    def run(self, manifest_after: bytes | None = None) -> tuple[subprocess.CompletedProcess[str], float]:
        assert BASH is not None
        before = snapshot(self.home)
        if manifest_after is not None:
            before[str(self.manifest.relative_to(self.home))] = hashlib.sha256(manifest_after).hexdigest()
        started = time.monotonic()
        result = subprocess.run(
            [BASH, str(self.invoked / "scripts/bootstrap-check.sh")],
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
            str(self.home / ".copilot-extensions/update-driver"), str(self.selected),
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
    selected = home / ".copilot/installed-plugins/selected-market/agent-ssh"
    selected.mkdir(parents=True)
    (selected / "plugin.json").write_text('{"name":"agent-ssh"}\n', encoding="utf-8")
    (selected / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    invoked = tmp_path / "invoked payload"
    scripts = invoked / "scripts"
    (scripts / "installation-context").mkdir(parents=True)
    shutil.copy2(source / "scripts/bootstrap-check.sh", scripts / "bootstrap-check.sh")
    shutil.copy2(
        source / "scripts/installation-context/json-query.awk",
        scripts / "installation-context/json-query.awk",
    )
    (invoked / "pyproject.toml").write_text('version = "9.9.9"\n', encoding="utf-8")
    project = tmp_path / "project with spaces"
    project.mkdir()
    calls, poison, queries = (tmp_path / name for name in ("handoff", "ambient", "queries"))
    driver = selected / "libs/update-driver/scripts/first-use.sh"
    _executable(
        driver,
        "#!/usr/bin/env bash\n"
        "# Installation driver lifecycle contract: vault-stamp-v1\n"
        'printf "%s\\n" "$@" >"$SSH_METADATA_CALLS"\n'
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
    awk, mv = shutil.which("awk"), shutil.which("mv")
    assert awk is not None and mv is not None
    _executable(
        tools / "awk",
        '#!/bin/sh\nprintf "query\\n" >>"$SSH_METADATA_QUERIES"\n'
        f'{shlex.quote(awk)} "$@" || exit $?\n'
        'if [ -n "${SSH_METADATA_REPLACEMENT:-}" ] && [ -f "$SSH_METADATA_REPLACEMENT" ]; then\n'
        f'  {shlex.quote(mv)} "$SSH_METADATA_REPLACEMENT" "$HOME/.agent-ssh/deploy-manifest.json"\n'
        'fi\n',
    )
    for name in ("python", "python3", "py", "uv", "uvx"):
        _executable(
            tools / name,
            '#!/bin/sh\nprintf "ambient\\n" >>"$SSH_METADATA_POISON"\nexit 97\n',
        )
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith((
            "COPILOT_", "AGENT_", "PYTHON", "UV_", "CONDA", "VIRTUAL_ENV",
            "BOOTSTRAP_KILLSWITCH_", "SSH_", "UPDATE_DRIVER_",
        ))
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), PATH=str(tools),
        SSH_METADATA_CALLS=str(calls), SSH_METADATA_POISON=str(poison),
        SSH_METADATA_QUERIES=str(queries),
        PYTHONHOME=str(tools), PYTHONPATH=str(tools),
        VIRTUAL_ENV=str(tools), CONDA_PREFIX=str(tools),
        UV_PYTHON=str(tools / "python"), UV_OFFLINE="1",
    )
    fixture = MetadataFixture(str(request.param), home, invoked, selected, project, env, calls, poison, queries)
    fixture.write_source()
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
            '#!/bin/sh\nprintf "runtime\\n" >>"$SSH_METADATA_POISON"\nexit 97\n',
        )
    samples = []
    for _ in range(2):
        result, elapsed = fixture.run()
        assert result.returncode == 0 and result.stderr == "", result
        samples.append(elapsed)
    assert not fixture.calls.exists()
    assert fixture.query_count() == 4
    record_property("ssh_native_metadata", json.dumps({
        "layout": fixture.layout, "selection": selection, "samples_seconds": samples,
        "query_calls": fixture.query_count(), "python_calls": 0, "uv_calls": 0,
        "driver_calls": 0, "instrumentation": "native command counting shim",
    }))


@pytest.mark.parametrize("phase", ["cold", "stale", "missing-current"])
def test_metadata_preserves_existing_action(bootstrap_metadata: MetadataFixture, phase: str) -> None:
    fixture = bootstrap_metadata
    if phase == "cold":
        fixture.manifest.unlink()
        hook = (fixture.invoked / "scripts/bootstrap-check.sh").read_bytes()
        fixture.invoked = fixture.selected
        scripts = fixture.invoked / "scripts"
        scripts.mkdir()
        (scripts / "bootstrap-check.sh").write_bytes(hook)
    elif phase == "stale":
        fixture.write_source(version="0.9.0")
        (fixture.manifest.parent / ".venv").mkdir()
    result, _ = fixture.run()
    if sys.platform == "linux":
        assert result.returncode == 0, result
        fixture.assert_handoff("stamp" if phase == "cold" else "provision")
        assert fixture.query_count() == (2 if phase == "cold" else 4)
    else:
        assert result.returncode == 3 and "qualified Linux Bash" in result.stderr, result
        assert not fixture.calls.exists()
        assert fixture.query_count() == (0 if phase == "cold" else 2)


@pytest.mark.parametrize("condition", ["context", "no-self", "pinned", "witness"])
def test_metadata_retains_existing_holds(bootstrap_metadata: MetadataFixture, condition: str) -> None:
    fixture = bootstrap_metadata
    fixture.write_source(version="0.9.0")
    if condition == "context":
        fixture.env["COPILOT_EXTENSIONS_CONTEXT"] = str(fixture.home / "context.json")
        fixture.manifest.write_text("{", encoding="utf-8")
    elif condition == "no-self":
        fixture.env["AGENT_SSH_NO_SELFPROVISION"] = "1"
    elif condition == "pinned":
        fixture.env["AGENT_SSH_PINNED_CONTEXT"] = ""
    else:
        fixture.env["UPDATE_DRIVER_WITNESS"] = ""
    result, _ = fixture.run()
    assert result.returncode == (3 if condition == "witness" else 0), result
    assert not fixture.calls.exists()
    assert fixture.query_count() == (0 if condition == "context" else 2)


@pytest.mark.parametrize("field", ["path", "version"])
@pytest.mark.parametrize("invalid", ["empty", "nonstring", "missing", "duplicate", "escaped-nul"])
def test_metadata_invalid_field_never_submits(
    bootstrap_metadata: MetadataFixture, field: str, invalid: str,
) -> None:
    fixture = bootstrap_metadata
    if invalid == "missing":
        source = {"version": "1.0.0", "path": str(fixture.selected)}
        del source[field]
        fixture.manifest.write_text(json.dumps({"source": source}), encoding="utf-8")
    elif invalid == "duplicate":
        text = fixture.manifest.read_text(encoding="utf-8")
        fixture.manifest.write_text(text[:-2] + f',"{field}":"duplicate"}}}}', encoding="utf-8")
    else:
        value: object = "" if invalid == "empty" else True if invalid == "nonstring" else "value\0"
        fixture.write_source(**{field: value})
    result, _ = fixture.run()
    assert result.returncode == 3 and "metadata" in result.stderr, result
    assert not fixture.calls.exists()


@pytest.mark.parametrize("invalid", [
    "syntax", "case-ambiguous", "invalid-utf8", "literal-nul", "missing-reader",
])
def test_metadata_invalid_document_never_submits(
    bootstrap_metadata: MetadataFixture, invalid: str,
) -> None:
    fixture = bootstrap_metadata
    if invalid == "syntax":
        fixture.manifest.write_text("{", encoding="utf-8")
    elif invalid == "case-ambiguous":
        text = fixture.manifest.read_text(encoding="utf-8")
        fixture.manifest.write_text(text[:-1] + ',"Source":{}}', encoding="utf-8")
    elif invalid == "invalid-utf8":
        fixture.manifest.write_bytes(b'{"source":{"path":"\xff"}}')
    elif invalid == "literal-nul":
        fixture.manifest.write_bytes(b'{"source":{"path":"value\0"}}')
    else:
        (fixture.invoked / "scripts/installation-context/json-query.awk").unlink()
    result, _ = fixture.run()
    assert result.returncode == 3 and "metadata" in result.stderr, result
    assert not fixture.calls.exists()
    assert fixture.query_count() == (0 if invalid in {"literal-nul", "missing-reader"} else 1)


def test_metadata_version_linefeed_keeps_identity(bootstrap_metadata: MetadataFixture) -> None:
    fixture = bootstrap_metadata
    fixture.write_source(version="1.0.0\n")
    (fixture.manifest.parent / ".venv").mkdir()
    result, _ = fixture.run()
    if sys.platform == "linux":
        assert result.returncode == 0, result
        fixture.assert_handoff("provision")
    else:
        assert result.returncode == 3 and "qualified Linux Bash" in result.stderr, result
        assert not fixture.calls.exists()


def test_metadata_source_linefeed_keeps_identity(bootstrap_metadata: MetadataFixture) -> None:
    fixture = bootstrap_metadata
    selected = fixture.selected.parent / "agent-ssh\n"
    shutil.copytree(fixture.selected, selected)
    fixture.write_source(path=str(selected))
    (fixture.manifest.parent / ".venv").mkdir()
    result, _ = fixture.run()
    assert result.returncode == 0 and result.stderr == "", result
    assert not fixture.calls.exists()
    assert fixture.query_count() == 2


def test_metadata_uses_one_document_when_manifest_replaced(
    bootstrap_metadata: MetadataFixture, tmp_path: Path,
) -> None:
    fixture = bootstrap_metadata
    fixture.write_source(version="0.9.0")
    (fixture.manifest.parent / ".venv").mkdir()
    replacement = tmp_path / "replacement.json"
    document = json.dumps({"source": {"path": "/missing/other-source", "version": "1.0.0"}}).encode()
    replacement.write_bytes(document)
    fixture.env["SSH_METADATA_REPLACEMENT"] = str(replacement)
    result, _ = fixture.run(manifest_after=document)
    assert not replacement.exists()
    if sys.platform == "linux":
        assert result.returncode == 0, result
        fixture.assert_handoff("provision")
    else:
        assert result.returncode == 3 and "qualified Linux Bash" in result.stderr, result
        assert not fixture.calls.exists()


def test_metadata_missing_selected_source_never_falls_back(
    bootstrap_metadata: MetadataFixture,
) -> None:
    fixture = bootstrap_metadata
    fixture.write_source(path=str(fixture.project / "missing"))
    result, _ = fixture.run()
    assert result.returncode == 3 and "source is unavailable" in result.stderr, result
    assert not fixture.calls.exists()
    assert fixture.query_count() == 1
