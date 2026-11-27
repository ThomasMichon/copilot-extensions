"""Real POSIX manifest reads with inert installation and publication owners."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import time
from typing import Any

import pytest

from .managed_command_fixture import packaged_command

__all__ = ["packaged_command"]

pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX hook; PowerShell reads JSON natively"),
]
PLUGIN = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@dataclass
class ManifestFixture:
    home: Path
    payload: Path
    env: dict[str, str]
    root: Path
    manifest: dict[str, Any]
    manifest_path: Path
    calls: Path
    poison: Path

    def write(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def run(self, *, status: int = 0) -> tuple[subprocess.CompletedProcess[str], float]:
        assert BASH is not None
        started = time.monotonic()
        result = subprocess.run(
            [BASH, str(self.payload / "scripts/bootstrap-check.sh")],
            env=self.env, cwd=self.home, capture_output=True, text=True, timeout=15,
        )
        elapsed = time.monotonic() - started
        assert result.returncode == status, result
        assert result.stdout == "{}", result
        assert not self.poison.exists(), self.poison.read_text() if self.poison.exists() else ""
        return result, elapsed

    def launched(self) -> list[str]:
        deadline = time.monotonic() + 3
        while not self.calls.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self.calls.exists(), "inert installer was not invoked"
        return self.calls.read_text(encoding="utf-8").splitlines()


@pytest.fixture(params=["raw", "materialized"])
def bootstrap(
    tmp_path: Path, request: pytest.FixtureRequest, packaged_command: Path,
) -> ManifestFixture:
    if BASH is None:
        pytest.skip("Bash unavailable")
    source = PLUGIN if request.param == "raw" else packaged_command
    home = tmp_path / "home with spaces"
    payload = tmp_path / "payload caf\u00e9"
    scripts = payload / "scripts"
    context_scripts = scripts / "installation-context"
    context_scripts.mkdir(parents=True)
    home.mkdir()
    for name in ("bootstrap-check.sh",):
        shutil.copy2(source / "scripts" / name, scripts / name)
    shutil.copy2(
        source / "scripts/installation-context/json-query.awk",
        context_scripts / "json-query.awk",
    )
    (payload / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    (scripts / "init.sh").write_text(
        '#!/bin/sh\ncase "${1:-}" in stamp) ;; esac\n'
        'printf "%s\\n" "$@" > "$FIXTURE_CALLS"\n', encoding="utf-8",
    )
    _executable(
        context_scripts / "installation-context.sh",
        '#!/bin/sh\nprintf "%s\\n" "$1" >> "$FIXTURE_PROBES"\n'
        'if [ "$1" = probe-plugin-publication ]; then\n'
        '  printf "%s\\n" "$FIXTURE_PUBLICATION"\n'
        'else printf "%s\\n" "$FIXTURE_RESOLUTION"; fi\n',
    )
    _executable(
        context_scripts / "plugin-publication-probe.sh",
        '#!/bin/sh\nprintf "publication\\n" >> "$FIXTURE_PROBES"\n'
        '[ "$FIXTURE_DENY_PUBLICATION" != 1 ]\n',
    )
    tool_bin = tmp_path / "native tools"
    tool_bin.mkdir()
    for name in ("awk", "id", "cut", "dirname", "grep", "sed", "cat", "bash"):
        address = shutil.which(name)
        assert address is not None, name
        (tool_bin / name).symlink_to(address)
    _executable(
        tool_bin / "getent",
        '#!/bin/sh\nprintf "fixture:x:%s:0:fixture:%s:/bin/sh\\n" "$(id -u)" "$HOME"\n',
    )
    calls = tmp_path / "installer calls"
    # The asynchronous production boundary remains, but no installer is executed.
    _executable(
        tool_bin / "nohup",
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$FIXTURE_CALLS"\n',
    )
    poison = tmp_path / "ambient called"
    for name in ("python", "python3", "py", "uv", "uvx"):
        _executable(
            tool_bin / name,
            '#!/bin/sh\nprintf "ambient\\n" >> "$FIXTURE_POISON"\nexit 97\n',
        )
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_", "PYTHON", "UV_", "CONDA", "VIRTUAL_ENV"))
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), PATH=str(tool_bin),
        FIXTURE_CALLS=str(calls), FIXTURE_POISON=str(poison),
        FIXTURE_PROBES=str(tmp_path / "probes"), FIXTURE_DENY_PUBLICATION="0",
        FIXTURE_RESOLUTION=json.dumps({
            "status": "ready", "actualMode": "legacy", "desiredMode": "legacy",
        }),
        PYTHONHOME=str(tool_bin), PYTHONPATH=str(tool_bin),
        VIRTUAL_ENV=str(tool_bin), CONDA_PREFIX=str(tool_bin),
        UV_PYTHON=str(tool_bin / "python"), UV_OFFLINE="1",
    )
    root = home / ".agent-machines"
    command = home / ".local/bin/agent-machines"
    _executable(command, "#!/bin/sh\nexit 98\n")
    fixture = ManifestFixture(
        home, payload, env, root,
        {"source": {"version": "1.0.0", "path": str(payload)}},
        root / "deploy-manifest.json", calls, poison,
    )
    fixture.write()
    return fixture


def _namespaced(fixture: ManifestFixture) -> None:
    fixture.root = fixture.home / "cell runtime"
    context = fixture.home / "install.json"
    marketplace = "example--0123456789abcdef"
    fixture.env.update(
        COPILOT_EXTENSIONS_CONTEXT=str(context),
        FIXTURE_RESOLUTION=json.dumps({
            "status": "ready", "reason": "namespaced-active",
            "actualMode": "namespaced", "desiredMode": "namespaced",
            "runtimeRoot": str(fixture.root), "context": str(context),
            "marketplaceId": marketplace,
        }),
        FIXTURE_PUBLICATION=json.dumps({
            "status": "ready", "reason": "namespaced-active",
            "actualMode": "namespaced", "runtimeRoot": str(fixture.root),
            "context": str(context), "deployment": {
                "allowPluginPublication": True,
                "path": str(fixture.root / "runtime-deployment.json"),
            },
        }),
    )
    runtime = fixture.root / "versions/1.0.0"
    interpreter = runtime / "bin/python"
    _executable(interpreter, '#!/bin/sh\nprintf "runtime\\n" >> "$FIXTURE_POISON"\nexit 97\n')
    (fixture.root / "current-version").write_text("1.0.0\n", encoding="utf-8")
    fixture.manifest.update(
        schema_version=4, service="agent-machines",
        source={
            "repo": "copilot-extensions", "plugin": "agent-machines",
            "kind": "local", "path": str(fixture.payload), "version": "1.0.0", "dirty": False,
        },
        runtime={
            "kind": "python", "version": "1.0.0", "path": str(runtime),
            "interpreter": str(interpreter),
            "selectedBy": {"kind": "payload", "path": str(fixture.payload), "version": "1.0.0"},
        },
        installation={"marketplaceId": marketplace, "pluginId": "agent-machines", "context": str(context)},
    )
    fixture.manifest_path = fixture.root / "deploy-manifest.json"
    fixture.write()


@pytest.mark.parametrize("namespaced", [False, True])
def test_native_manifest_current_is_quiet_without_python(
    bootstrap: ManifestFixture, namespaced: bool, record_property: Any,
) -> None:
    if namespaced:
        _namespaced(bootstrap)
    samples = []
    for _ in range(2):
        result, elapsed = bootstrap.run()
        assert result.stderr == ""
        samples.append(elapsed)
    record_property("native_manifest_seconds", json.dumps(samples))
    assert not bootstrap.calls.exists()


@pytest.mark.parametrize("namespaced", [False, True])
def test_native_manifest_stale_retains_publication_and_launch_owner(
    bootstrap: ManifestFixture, namespaced: bool,
) -> None:
    if namespaced:
        _namespaced(bootstrap)
    bootstrap.manifest["source"]["version"] = "0.9.0"
    bootstrap.write()
    result, _ = bootstrap.run()
    assert "reconciling in background" in result.stderr
    assert bootstrap.launched() == [
        "bash", str(bootstrap.payload / "scripts/init.sh"),
        *(["cell-provision", "--context", bootstrap.env["COPILOT_EXTENSIONS_CONTEXT"],
           "--expected-marketplace-id", "example--0123456789abcdef"] if namespaced else []),
    ]
    probes = Path(bootstrap.env["FIXTURE_PROBES"]).read_text().splitlines()
    assert probes == ["status", "probe-plugin-publication" if namespaced else "publication"]


@pytest.mark.parametrize("invalid", [
    "syntax", "duplicate", "case-ambiguous", "invalid-utf8", "schema-bool",
    "dirty-string", "source-nonstring", "wrong-context", "wrong-runtime",
    "wrong-selected-version", "wrong-marker", "runtime-not-executable",
    "newline-context", "newline-runtime", "newline-interpreter",
])
def test_native_namespaced_manifest_refuses_invalid_without_fallback(
    bootstrap: ManifestFixture, invalid: str,
) -> None:
    _namespaced(bootstrap)
    if invalid == "schema-bool":
        bootstrap.manifest["schema_version"] = True
    elif invalid == "dirty-string":
        bootstrap.manifest["source"]["dirty"] = "false"
    elif invalid == "source-nonstring":
        bootstrap.manifest["source"]["path"] = 1
    elif invalid == "wrong-context":
        bootstrap.manifest["installation"]["context"] += ".other"
    elif invalid == "wrong-runtime":
        bootstrap.manifest["runtime"]["path"] += ".other"
    elif invalid == "wrong-selected-version":
        bootstrap.manifest["runtime"]["selectedBy"]["version"] = "other"
    elif invalid == "wrong-marker":
        (bootstrap.root / "current-version").write_text("other\n", encoding="utf-8")
    elif invalid == "runtime-not-executable":
        Path(bootstrap.manifest["runtime"]["interpreter"]).chmod(0o644)
    elif invalid == "newline-context":
        bootstrap.manifest["installation"]["context"] += "\n"
    elif invalid == "newline-runtime":
        bootstrap.manifest["runtime"]["path"] += "\n"
    elif invalid == "newline-interpreter":
        bootstrap.manifest["runtime"]["interpreter"] += "\n"
    bootstrap.write()
    if invalid == "syntax":
        bootstrap.manifest_path.write_text("{", encoding="utf-8")
    elif invalid in {"duplicate", "case-ambiguous"}:
        original = bootstrap.manifest_path.read_text(encoding="utf-8")
        key = "source" if invalid == "duplicate" else "Source"
        bootstrap.manifest_path.write_text(original[:-1] + f', "{key}": {{}}}}', encoding="utf-8")
    elif invalid == "invalid-utf8":
        bootstrap.manifest_path.write_bytes(b'{"source":{"path":"\xff"}}')
    result, _ = bootstrap.run()
    assert "deploy manifest is invalid" in result.stderr
    assert not bootstrap.calls.exists()
    assert Path(bootstrap.env["FIXTURE_PROBES"]).read_text().splitlines() == ["status"]


@pytest.mark.parametrize("namespaced", [False, True])
def test_native_first_use_still_precedes_manifest_reader(
    bootstrap: ManifestFixture, namespaced: bool,
) -> None:
    if namespaced:
        _namespaced(bootstrap)
    bootstrap.manifest_path.unlink()
    bootstrap.run()
    if namespaced:
        assert bootstrap.launched()[2] == "cell-provision"
    else:
        assert bootstrap.calls.read_text().splitlines() == ["stamp"]


def test_native_legacy_invalid_manifest_never_publishes(bootstrap: ManifestFixture) -> None:
    bootstrap.manifest_path.write_text('{"source": null}', encoding="utf-8")
    bootstrap.run()
    assert not bootstrap.calls.exists()


def test_native_governance_refusal_precedes_manifest_reader(bootstrap: ManifestFixture) -> None:
    bootstrap.env["FIXTURE_RESOLUTION"] = json.dumps({
        "status": "blocked", "reason": "fixture-policy-hold",
    })
    bootstrap.manifest["source"]["version"] = "0.9.0"
    bootstrap.write()
    result, _ = bootstrap.run()
    assert "governance blocks reconcile" in result.stderr
    assert not bootstrap.calls.exists()


@pytest.mark.parametrize("namespaced", [False, True])
def test_native_stale_manifest_cannot_bypass_publication(
    bootstrap: ManifestFixture, namespaced: bool,
) -> None:
    if namespaced:
        _namespaced(bootstrap)
        publication = json.loads(bootstrap.env["FIXTURE_PUBLICATION"])
        publication["deployment"]["allowPluginPublication"] = False
        bootstrap.env["FIXTURE_PUBLICATION"] = json.dumps(publication)
    else:
        bootstrap.env["FIXTURE_DENY_PUBLICATION"] = "1"
    bootstrap.manifest["source"]["version"] = "0.9.0"
    bootstrap.write()
    result, _ = bootstrap.run(status=3)
    assert "Runtime publication refused" in result.stderr
    assert not bootstrap.calls.exists()


@pytest.mark.parametrize("namespaced", [False, True])
@pytest.mark.parametrize("field", ["version", "path"])
def test_native_manifest_preserves_source_identity_newlines(
    bootstrap: ManifestFixture, namespaced: bool, field: str,
) -> None:
    if namespaced:
        _namespaced(bootstrap)
    bootstrap.manifest["source"][field] += "\n"
    bootstrap.write()
    result, _ = bootstrap.run()
    if not namespaced and field == "path":
        assert result.stderr == ""
        assert not bootstrap.calls.exists()
    else:
        assert "reconciling in background" in result.stderr
        bootstrap.launched()


@pytest.mark.parametrize("namespaced", [False, True])
def test_native_manifest_uses_one_snapshot_during_replacement(
    bootstrap: ManifestFixture, namespaced: bool,
) -> None:
    if namespaced:
        _namespaced(bootstrap)
    replacement = bootstrap.home / "replacement.json"
    other_payload = bootstrap.home / "other payload"
    (other_payload / "scripts").mkdir(parents=True)
    shutil.copy2(bootstrap.payload / "scripts/init.sh", other_payload / "scripts/init.sh")
    (other_payload / "pyproject.toml").write_text('version = "2.0.0"\n', encoding="utf-8")
    replacement_doc = json.loads(bootstrap.manifest_path.read_bytes())
    replacement_doc["source"]["version"] = "0.9.0"
    replacement_doc["source"]["path"] = str(other_payload)
    replacement.write_text(json.dumps(replacement_doc), encoding="utf-8")
    awk = shutil.which("awk")
    move = shutil.which("mv")
    assert awk is not None and move is not None
    tool = Path(bootstrap.env["PATH"]) / "awk"
    tool.unlink()
    _executable(
        tool,
        '#!/bin/sh\nreplace=0\nfor argument do\n'
        '  if [ "$argument" = "query_path=source$(printf \'\\034\')version" ]; then replace=1; fi\n'
        f'done\n{shlex.quote(awk)} "$@"\ncode=$?\n'
        f'if [ "$replace" = 1 ] && [ -f {shlex.quote(str(replacement))} ]; then\n'
        f'  {shlex.quote(move)} {shlex.quote(str(replacement))} '
        f'{shlex.quote(str(bootstrap.manifest_path))}\nfi\nexit "$code"\n',
    )
    result, _ = bootstrap.run()
    assert result.stderr == ""
    assert not bootstrap.calls.exists()
    assert not replacement.exists(), "atomic replacement seam was not exercised"
    assert json.loads(bootstrap.manifest_path.read_bytes()) == replacement_doc


def test_native_manifest_refuses_literal_nul_before_capture(bootstrap: ManifestFixture) -> None:
    bootstrap.manifest_path.write_bytes(b'{"source":{"path":"\0"}}')
    result, _ = bootstrap.run()
    assert "manifest contains NUL" in result.stderr
    assert not bootstrap.calls.exists()
