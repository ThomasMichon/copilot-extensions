"""Agent Index's shipped host declaration through attributed dispatch supervision."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin

from agent_dispatch.registrar import RegistrarError
from agent_dispatch.registrar_discovery import read_declaration_file
from agent_dispatch.registrar_reconcile import declared_registrations
from agent_dispatch.registrar_registry import scan_registrar_registry
from tests.test_managed_companion import Harness
from tests.test_registrar_registry import _write_manifest


PLUGIN = Path(__file__).resolve().parents[2] / "agent-index"
DECLARATION = (
    PLUGIN / "references" / "agent-dispatch" / "registrar" / "agent-index-service.json"
)
SOURCE = "agent-index@copilot-extensions"
SCOPES = ("global", "project:example")


def _module(path, name, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _activation(*, root=PLUGIN, scopes=SCOPES, source=SOURCE):
    name, marketplace = source.split("@", 1)
    return ActivationReport(
        ScanAuthority.COMPLETE,
        {
            source: EntryDecision.active(
                ActivePlugin(
                    source=source,
                    name=name,
                    marketplace=marketplace,
                    root=root.resolve(),
                    scopes=scopes,
                )
            )
        },
    )


def _config(root, machine="machine-a"):
    path = root / ".agent-index" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"indexers:\n  - machine: {machine}\n    ssh: {machine}\n"
        "corpus:\n  sources:\n    - name: git:example\n      repo: example\n",
        encoding="utf-8",
    )
    return path.resolve()


class IndexHarness(Harness):
    """Reuse fake build/process boundaries, but execute the shipped provider."""

    def __init__(self, tmp_path, monkeypatch, provider):
        self.provider_module = provider
        self.monkeypatch = monkeypatch
        self.provider_requests = []
        self.provider_results = []
        self.registry = tmp_path / "registrar.d"
        _write_manifest(self.registry, PLUGIN, source=SOURCE)
        super().__init__(tmp_path, monkeypatch)
        self.refresh()

    def refresh(self, activation=None):
        report = scan_registrar_registry(
            self.registry,
            activation_report=activation if activation is not None else _activation(),
        )
        self.registrations = [
            registration
            for registration in declared_registrations(
                [entry.declaration for entry in report.declarations], machine="machine-a"
            )
            if registration.get("logical_id") == "agent-index-service"
        ]
        if self.registrations:
            self.registration = self.registrations[0]
        return report

    def run(self, argv, **kwargs):
        if Path(argv[1]).name == "companion-provider.py":
            self.provider_requests.append(json.loads(kwargs["input_text"]))
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                self.monkeypatch.context() as environment_patch,
                patch.object(sys, "stdin", io.StringIO(kwargs["input_text"])),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                # Do not rewrite unchanged empty environment values: on Windows
                # putenv removes them from the native subprocess environment.
                for key in os.environ.keys() - kwargs["environment"].keys():
                    environment_patch.delenv(key)
                for key, value in kwargs["environment"].items():
                    if os.environ.get(key) != value:
                        environment_patch.setenv(key, value)
                code = self.provider_module.main()
            completed = subprocess.CompletedProcess(
                argv, code, stdout.getvalue(), stderr.getvalue()
            )
            self.provider_results.append(completed)
            return completed
        assert Path(argv[1]).name == "companion-service.py"
        action = argv[-1]
        assert action in {"health", "stop"}
        self.events.append((action, dict(kwargs["environment"]), argv))
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"schema_version": 1, "healthy": True}), ""
        )


@pytest.fixture
def index_host(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in tuple(os.environ):
        if name.upper().startswith("AGENT_INDEX_") or name in {
            "COPILOT_EXTENSIONS_CONTEXT",
            "COPILOT_PLUGIN_ROOT",
            "CLAUDE_PLUGIN_ROOT",
            "PLUGIN_ROOT",
            "AGENT_WORKTREES_COMMAND",
        }:
            monkeypatch.delenv(name)
    monkeypatch.setenv("MODE", "index")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    scripts = PLUGIN / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    resolver = _module(
        scripts / "resolve_effective_config.py", "resolve_effective_config", monkeypatch
    )
    provider = _module(scripts / "companion-provider.py", "_index_provider", monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    policy = repo / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir()
    policy.write_text("requires_external_state_root: false\n", encoding="utf-8")
    config = _config(repo)
    registry = home / ".agent-worktrees" / "repos.yaml"
    registry.parent.mkdir()
    registry.write_text(
        "repos:\n  example:\n"
        f"    {provider._platform_key()}: {json.dumps(str(repo))}\n",
        encoding="utf-8",
    )
    h = IndexHarness(tmp_path, monkeypatch, provider)
    h.home, h.repo, h.config, h.resolver = home, repo, config, resolver
    return h


def test_shipped_index_declaration_preserves_version_and_source_authority(index_host):
    h = index_host
    declaration = json.loads(DECLARATION.read_text(encoding="utf-8"))
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    match = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"',
        (PLUGIN / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert match is not None
    version = match.group(1)
    assert manifest["version"] == version
    managed = {
        "schema_version": 1,
        "runtimes": [
            {
                "name": "service",
                "version": version,
                "profile": "host",
                "python_env": "AGENT_INDEX_MANAGED_PYTHON",
                "projects": [
                    {"path": "libs/zdd"},
                    {"path": "libs/agent-procutil"},
                    {"path": ".", "extras": ["store"]},
                ],
                "imports": [
                    "agent_index.server",
                    "lancedb",
                    "pyarrow",
                    "tree_sitter",
                ],
            }
        ],
    }
    assert declaration["kind"] == "plugin-companion"
    assert declaration["spec"]["managed_runtime"] == managed
    assert h.registration["spec"] == declaration["spec"]
    assert h.registration["owner"] == SOURCE
    assert h.registration["source"] == "declared"
    assert h.registration["plugin"] == {
        "root": str(PLUGIN.resolve()),
        "source_path": str(DECLARATION.resolve()),
        "version": version,
        "activation_scopes": list(SCOPES),
    }
    assert h.registration["runtime_revision"] == {
        "plugin_root": str(PLUGIN.resolve()),
        "plugin_owner": SOURCE,
        "plugin_source_path": str(DECLARATION.resolve()),
        "plugin_version": version,
        "activation_scopes": list(SCOPES),
        "managed_runtime": managed,
    }
    assert h.builder.calls == h.processes == h.provider_requests == []
    with pytest.raises(RegistrarError, match="attributed plugin discovery"):
        read_declaration_file(DECLARATION)


def test_configured_index_host_prepares_before_launch_and_freezes_bound_runtime(
    index_host, monkeypatch
):
    h = index_host
    monkeypatch.setenv("AGENT_INDEX_MANAGED_PYTHON", "inherited-cannot-select")
    h.executor.defer = True

    assert h.daemon.reconcile_once().started == []
    assert h.processes == h.builder.calls == []
    result = h.provider_results[-1]
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["active"] is True
    assert not (h.home / ".agent-index").exists()
    assert h.provider_requests[-1] == {
        "schema_version": 1,
        "registration_id": h.rid,
        "plugin": SOURCE,
        "plugin_version": h.registration["plugin"]["version"],
        "activation_scopes": list(SCOPES),
        "machine": "machine-a",
        "environment": "default",
    }

    h.executor.complete()
    assert h.processes == []
    assert h.builder.install_count == 1
    assert h.daemon.reconcile_once().started == [h.rid]
    snapshot = h.unit.companion_resolution.managed_snapshot
    assert snapshot is not None
    assert h.controller.selected_managed(h.rid) == snapshot
    runtime = snapshot.runtimes[0]
    resolution = h.processes[0].resolution
    assert resolution.environment["AGENT_INDEX_MANAGED_PYTHON"] == str(runtime.python)
    assert resolution.environment["AGENT_INDEX_EFFECTIVE_CONFIG"] == str(h.config)
    assert resolution.environment["AGENT_INDEX_REPO"] == str(h.repo.resolve())
    assert resolution.environment["AGENT_INDEX_MACHINE"] == "machine-a"
    assert resolution.environment["AGENT_INDEX_NO_SELFPROVISION"] == "1"
    assert resolution.environment["PYTHONNOUSERSITE"] == "1"
    assert resolution.environment["PYTHONDONTWRITEBYTECODE"] == "1"
    for command, action in (
        (resolution.command, "start"),
        (resolution.stop_command, "stop"),
        (resolution.health_probe, "health"),
    ):
        assert command[1:] == (str(PLUGIN / "scripts" / "companion-service.py"), action)
    receipt = json.loads(runtime.receipt.read_text(encoding="utf-8"))
    declared_runtime = h.registration["spec"]["managed_runtime"]["runtimes"][0]
    assert receipt["ownership"]["authority"] == h.registration["runtime_revision"]
    for key in ("name", "version", "profile", "imports"):
        assert receipt[key] == declared_runtime[key]
    assert receipt["snapshot"]["projects"] == [
        {"path": project["path"], "extras": project.get("extras", [])}
        for project in declared_runtime["projects"]
    ]
    assert [event[0] for event in h.events][-3:] == ["launch", "release", "health"]
    builds = list(h.builder.calls)
    assert h.daemon.reconcile_once().running == [h.rid]
    assert h.builder.calls == builds
    assert len(h.processes) == 1

    (h.home / ".agent-worktrees" / "repos.yaml").write_text("{", encoding="utf-8")
    assert h.daemon.reconcile_once().running == [h.rid]
    assert h.provider_results[-1].returncode != 0
    assert h.unit.companion_resolution.managed_snapshot == snapshot
    h.unit.proc.returncode = 1
    assert h.daemon.reconcile_once().running == []
    assert h.builder.calls == builds
    assert len(h.processes) == 1


@pytest.mark.parametrize("case", ["client", "unconfigured", "global-only", "unsupported"])
def test_inactive_index_host_never_builds_or_starts(index_host, monkeypatch, case):
    h = index_host
    if case == "client":
        _config(h.repo, "another-machine")
    elif case == "unconfigured":
        h.config.unlink()
    elif case == "global-only":
        h.refresh(_activation(scopes=("global",)))
    else:
        monkeypatch.setattr(h.provider_module, "_supports_companion_mode", lambda _env: False)

    for _ in range(2):
        summary = h.daemon.reconcile_once()
        assert summary.started == summary.running == []
    assert h.provider_results
    assert all(
        result.returncode == 0 and json.loads(result.stdout)["active"] is False
        for result in h.provider_results
    )
    assert h.builder.calls == h.processes == []
    assert not h.materializer.policy.root.exists()
    assert h.controller.selected_managed(h.rid) is None


def test_index_required_state_root_is_resolved_without_a_runtime(index_host, monkeypatch):
    h = index_host
    h.config.unlink()
    (h.repo / ".agent-worktrees" / "config.yaml").write_text(
        "requires_external_state_root: true\n", encoding="utf-8"
    )
    state = h.home / "knowledge"
    config = _config(state)
    monkeypatch.setattr(h.resolver, "_external_state_root", lambda _root: ("ready", state))
    h.executor.defer = True

    assert h.daemon.reconcile_once().started == []
    result = h.provider_results[-1]
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["active"] is True
    assert value["environment"]["AGENT_INDEX_EFFECTIVE_CONFIG"] == str(config)
    assert value["environment"]["AGENT_INDEX_REPO"] == str(h.repo.resolve())
    assert h.builder.calls == h.processes == []
    assert not (h.home / ".agent-index").exists()


@pytest.mark.parametrize("case", ["registry", "required-state-root"])
def test_index_provider_uncertainty_cannot_authorize_first_build(index_host, monkeypatch, case):
    h = index_host
    if case == "registry":
        (h.home / ".agent-worktrees" / "repos.yaml").unlink()
    else:
        h.config.unlink()
        (h.repo / ".agent-worktrees" / "config.yaml").write_text(
            "requires_external_state_root: true\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            h.resolver, "_external_state_root", lambda _root: ("unavailable", None)
        )

    for _ in range(2):
        summary = h.daemon.reconcile_once()
        assert summary.started == summary.running == []
    assert all(result.returncode != 0 for result in h.provider_results)
    assert h.provider_results and "indeterminate" in h.provider_results[-1].stderr
    assert h.builder.calls == h.processes == []
    assert not h.materializer.policy.root.exists()


@pytest.mark.parametrize("case", ["inactive", "uncertain", "other-source", "other-root"])
def test_index_candidate_needs_authoritative_active_source(index_host, case):
    h = index_host
    if case == "inactive":
        activation = ActivationReport(ScanAuthority.COMPLETE, {})
    elif case == "uncertain":
        activation = ActivationReport(ScanAuthority.INDETERMINATE, {})
    elif case == "other-source":
        activation = _activation(source="agent-index@another-marketplace")
    else:
        activation = _activation(root=h.plugin)
    report = h.refresh(activation)

    assert report.declarations == ()
    assert h.registrations == []
    assert h.daemon.reconcile_once().started == []
    assert h.provider_requests == h.builder.calls == h.processes == []


@pytest.mark.parametrize("case", ["direct", "missing-plugin", "changed-revision"])
def test_index_registration_provenance_cannot_be_forged_after_discovery(index_host, case):
    h = index_host
    registration = copy.deepcopy(h.registration)
    if case == "direct":
        registration["source"] = "direct"
    elif case == "missing-plugin":
        registration.pop("plugin")
    else:
        registration["runtime_revision"]["plugin_owner"] = "agent-index@another-marketplace"
    h.registrations = [registration]

    summary = h.daemon.reconcile_once()
    assert summary.started == summary.running == []
    assert h.builder.calls == h.processes == []
