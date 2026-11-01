"""Tests for inert model-routing configuration resolution."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
RESOLVER = PLUGIN / "scripts" / "resolve-model-routing.py"
SCHEMA = PLUGIN / "schemas" / "model-routing.schema.json"
EXAMPLE = PLUGIN / "examples" / "model-routing.json"


def _config(
    purposes: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    return {
        "schema": "copilot-extensions.model-routing",
        "version": 1,
        "purposes": purposes,
    }


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _run(
    repo: Path,
    home: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    return subprocess.run(
        [
            sys.executable,
            str(RESOLVER),
            "--repo",
            str(repo),
            *args,
        ],
        check=check,
        capture_output=True,
        text=True,
        env=environment,
    )


def _repo(tmp_path: Path, *, trusted: bool = True) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / ".git").mkdir(parents=True)
    if trusted:
        _write(
            home / ".copilot" / "config.json",
            {"trustedFolders": [str(repo.resolve())]},
        )
    return repo, home


def _model_helper():
    spec = importlib.util.spec_from_file_location(
        "model_configuration", PLUGIN / "scripts" / "model_configuration.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_projected(repo, home, monkeypatch, *, owner_report=None):
    monkeypatch.syspath_prepend(str(PLUGIN / "scripts"))
    helper = _model_helper()
    report = owner_report or json.loads(os.environ["AGENT_CONFIGURATION_CONTEXT"])
    monkeypatch.setattr(helper, "qualified_owner_report", lambda *args: report)
    spec = importlib.util.spec_from_file_location("projected_model_resolver", RESOLVER)
    resolver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resolver)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(sys, "argv", [str(RESOLVER), "--repo", str(repo)])
    output = io.StringIO()
    with redirect_stdout(output):
        assert resolver.main() == 0
    return json.loads(output.getvalue())


@pytest.mark.parametrize("level", range(4))
def test_native_routing_four_tiers_replace_whole_entries(tmp_path, monkeypatch, level):
    repo, home = _repo(tmp_path)
    knowledge, local = tmp_path / "knowledge", tmp_path / "local"
    knowledge.mkdir()
    local.mkdir()
    _write(home / ".copilot" / "config.json", {
        "trustedFolders": [str(repo), str(knowledge)],
    })
    paths = (
        repo / ".github" / "copilot" / "model-routing.json",
        knowledge / ".github" / "copilot" / "model-routing.json",
        home / ".copilot" / "model-routing.json",
        local / ".github" / "copilot" / "model-routing.json",
    )
    states = ("candidate", "held", "failed", "candidate")
    for index, path in enumerate(paths[:level + 1]):
        entry = {
            "model": "shared-model", "state": states[index], "surfaces": ["task"],
            "costRank": index,
        }
        if index == 0:
            entry["constraints"] = ["base-only"]
        _write(path, _config({"coding": [entry]}))
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps({
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "repo-registered", "execution_machine": "machine-a",
            "installation_cell": "cell-a", "binding": "bound",
            "base_root": str(repo), "knowledge_root": str(knowledge),
        },
        "repository_machine_root": str(local),
    }))

    payload = _run_projected(repo, home, monkeypatch)

    entry = payload["purposes"]["coding"][0]
    assert entry["state"] == states[level]
    assert entry["costRank"] == level
    if level > 0:
        assert "base-only" not in entry.get("constraints", [])
    assert payload["provenance"]["/coding/shared-model"]["id"] == (
        "repository", "knowledge", "operator", "repository-machine",
    )[level]


def test_native_invalid_operator_suppresses_all_lower_and_local_choices(tmp_path, monkeypatch):
    repo, home = _repo(tmp_path)
    local = tmp_path / "local"
    local.mkdir()
    _write(repo / ".github" / "copilot" / "model-routing.json", _config({
        "coding": [{"model": "base", "state": "candidate", "surfaces": ["task"]}],
    }))
    _write(local / ".github" / "copilot" / "model-routing.json", _config({
        "coding": [{"model": "local", "state": "candidate", "surfaces": ["task"]}],
    }))
    _write(home / ".copilot" / "model-routing.json", {"invalid": True})
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps({
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "repo-registered", "execution_machine": "machine-a",
            "installation_cell": "cell-a", "binding": "unbound", "base_root": str(repo),
        },
        "repository_machine_root": str(local),
    }))
    assert _run_projected(repo, home, monkeypatch)["purposes"] == {}


def test_projected_routing_uses_fresh_owner_root_not_input_root(tmp_path, monkeypatch):
    repo, home = _repo(tmp_path)
    injected = tmp_path / "injected"
    actual = tmp_path / "actual"
    for root, model in ((injected, "injected-model"), (actual, "actual-model")):
        _write(root / ".github" / "copilot" / "model-routing.json", _config({
            "coding": [{"model": model, "state": "candidate", "surfaces": ["task"]}],
        }))
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "owner-repository", "execution_machine": "owner-machine",
            "installation_cell": str(tmp_path / "owner-cell"), "binding": "unbound",
            "base_root": str(repo),
        },
        "repository_machine_root": str(injected),
    }
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    fresh = {**report, "repository_machine_root": str(actual)}
    payload = _run_projected(repo, home, monkeypatch, owner_report=fresh)
    assert payload["purposes"]["coding"][0]["model"] == "actual-model"


def test_matching_repo_wrong_delivered_cell_is_refused(tmp_path, monkeypatch):
    repo, home = _repo(tmp_path)
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "owner-repository", "execution_machine": "owner-machine",
            "installation_cell": str(tmp_path / "foreign-cell"), "binding": "unbound",
            "base_root": str(repo),
        },
        "repository_machine_root": str(tmp_path / "must-not-be-read"),
    }
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    fresh = {
        **report, "context": {**report["context"], "installation_cell": str(tmp_path / "actual-cell")},
    }
    with pytest.raises(ValueError, match="another current cell"):
        _run_projected(repo, home, monkeypatch, owner_report=fresh)


def test_report_absent_remains_inert_without_loading_sdk(tmp_path, monkeypatch):
    repo, home = _repo(tmp_path)
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    helper = _model_helper()
    monkeypatch.setattr(helper, "_installation_sdk", lambda *args: pytest.fail("SDK loaded"))
    assert helper.compose_routing(
        repo, home, {}, {}, "missing", None, None, [], lambda a, b: {},
    ) == ({}, {}, {})


def test_real_sdk_first_touch_missing_peer_does_not_create_runtime(tmp_path, monkeypatch):
    helper = _model_helper()
    repo, home = _repo(tmp_path)
    with pytest.raises(ValueError, match="qualified installed configuration owner"):
        helper.qualified_owner_report(repo, PLUGIN, home, {})
    assert not (home / ".copilot-extensions").exists()
    assert not (home / ".agent-worktrees").exists()


@pytest.mark.parametrize("mode,matching", [
    ("legacy", True), ("legacy", False), ("namespaced", True), ("namespaced", False),
    ("unready", True), ("namespaced-inactive", True),
])
def test_native_owner_mode_cell_and_ready_read(tmp_path, monkeypatch, mode, matching):
    helper = _model_helper()
    monkeypatch.syspath_prepend(str(PLUGIN / "scripts" / "configuration-layers" / "src"))
    repo, home = _repo(tmp_path)
    payload = home / ".copilot" / "installed-plugins" / "declared" / "agent-worktrees"
    for sub in ("scripts", "src/agent_worktrees"):
        (payload / sub).mkdir(parents=True)
    worktrees = PLUGIN.parent / "agent-worktrees"
    shutil.copyfile(worktrees / "scripts" / "registry_root.py", payload / "scripts" / "registry_root.py")
    shutil.copyfile(
        worktrees / "src" / "agent_worktrees" / "_peer_launch.py",
        payload / "src" / "agent_worktrees" / "_peer_launch.py",
    )
    cell = tmp_path / "active-cell"
    effective_mode = "namespaced" if mode.startswith("namespaced") else "legacy"
    runtime = cell / "plugins" / "agent-worktrees" if effective_mode == "namespaced" else home / ".agent-worktrees"
    expected_cell = cell if effective_mode == "namespaced" else runtime
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "owner-repository", "execution_machine": "owner-machine",
            "installation_cell": str(expected_cell if matching else tmp_path / "foreign-cell"),
            "binding": "unbound", "base_root": str(repo),
        },
        "repository_machine_root": str(tmp_path / "owner-exact"),
    }
    owner_script = tmp_path / "read_owner.py"
    owner_script.write_text("import json\nprint(" + repr(json.dumps(report)) + ")\n", encoding="utf-8")
    context = {
        "state": "inactive" if mode == "namespaced-inactive" else "active",
        "pluginRoot": str(runtime), "cellRoot": str(cell),
        "namespaceReceipt": str(cell / "namespace.json"), "pluginId": "agent-worktrees",
        "installReceipt": str(runtime / "install.json"), "payloadRoot": str(payload),
    }
    calls = []

    def resolve_context(**kwargs):
        calls.append("active-context")
        return context

    sdk = SimpleNamespace(
        resolve_payload_source=lambda **kwargs: {
            "sourceFingerprint": "qualified-source", "marketplaceId": "declared",
            "payloadRoot": str(payload), "locator": {"kind": "installed", "marketplaceKey": "declared"},
        },
        resolve_runtime_deployment=lambda **kwargs: {
            "status": "ready", "reason": "namespaced-active" if effective_mode == "namespaced" else "policy-disabled",
            "actualMode": effective_mode, "runtimeRoot": str(runtime), "context": context["installReceipt"],
            "deployment": {"state": "valid"},
        },
        resolve_context=resolve_context,
        validate_namespace_receipt=lambda *args: {"receipt": {"state": "active"}},
        paths_equal=lambda a, b: Path(a) == Path(b),
    )
    monkeypatch.setattr(helper, "_installation_sdk", lambda *args: sdk)
    monkeypatch.setattr(
        helper, "_ready_owner_prefix",
        lambda *args: [] if mode == "unready" else [sys.executable, str(owner_script)],
    )
    if mode == "unready":
        from configuration_layers import ConfigurationError

        with pytest.raises(ConfigurationError, match="required configuration owner"):
            helper.qualified_owner_report(repo, PLUGIN, home, {})
    elif mode == "namespaced-inactive":
        with pytest.raises(ValueError, match="not currently active"):
            helper.qualified_owner_report(repo, PLUGIN, home, {})
    elif matching:
        assert helper.qualified_owner_report(repo, PLUGIN, home, {}) == report
    else:
        with pytest.raises(ValueError, match="another current cell"):
            helper.qualified_owner_report(repo, PLUGIN, home, {})
    assert calls == (["active-context"] if effective_mode == "namespaced" else [])


@pytest.mark.parametrize("status,reason", [
    ("ready", "activation-required"), ("migration-required", "migration-required"),
    ("deactivation-required", "deactivation-required"), ("invalid", "context-invalid"),
])
def test_owner_mode_refusal_never_reads_configuration(tmp_path, monkeypatch, status, reason):
    helper = _model_helper()
    monkeypatch.syspath_prepend(str(PLUGIN / "scripts" / "configuration-layers" / "src"))
    repo, home = _repo(tmp_path)
    peer = tmp_path / "qualified-peer"
    monkeypatch.setattr(helper, "_qualified_peer_payload", lambda *args: {"payloadRoot": str(peer), "marketplaceId": "declared"})
    sdk = SimpleNamespace(
        resolve_payload_source=lambda **kwargs: {},
        resolve_runtime_deployment=lambda **kwargs: {
            "status": status, "reason": reason, "actualMode": "legacy",
            "runtimeRoot": str(home / ".agent-worktrees"), "deployment": {"state": "valid"},
        },
    )
    monkeypatch.setattr(helper, "_installation_sdk", lambda *args: sdk)
    monkeypatch.setattr(helper, "_load_owned_module", lambda path, name: SimpleNamespace(
        _legacy_home=lambda env: home,
    ))
    monkeypatch.setattr(helper, "_ready_owner_prefix", lambda *args: pytest.fail("unready owner invoked"))
    with pytest.raises(ValueError, match="mode is unavailable"):
        helper.qualified_owner_report(repo, PLUGIN, home, {})


def test_schema_and_example_use_portable_placeholder_models() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    assert schema["properties"]["schema"]["const"] == (
        "copilot-extensions.model-routing"
    )
    assert schema["properties"]["version"]["const"] == 1
    models = {
        entry["model"]
        for entries in example["purposes"].values()
        for entry in entries
    }
    assert models == {"example-code-model", "example-fast-model"}


def test_missing_configs_resolve_to_empty_registry(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    result = _run(repo, home)
    payload = json.loads(result.stdout)

    assert payload["sources"] == {
        "operator": "missing",
        "repository": "missing",
    }
    assert payload["purposes"] == {}
    assert payload["diagnostics"] == []
    assert result.stderr == ""


def test_untrusted_repository_config_is_ignored(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path, trusted=False)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        _config({
            "evidence": [{
                "model": "repo-model",
                "state": "candidate",
                "surfaces": ["task"],
            }],
        }),
    )

    result = _run(repo, home)
    payload = json.loads(result.stdout)

    assert payload["sources"]["repository"] == "untrusted"
    assert payload["purposes"] == {}
    assert "repository config ignored because the repository is untrusted" in (
        payload["diagnostics"]
    )


def test_operator_layer_overrides_repository_entry(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        _config({
            "evidence": [{
                "model": "shared-model",
                "state": "candidate",
                "surfaces": ["task"],
                "costRank": 30,
            }],
        }),
    )
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "evidence": [
                {
                    "model": "shared-model",
                    "state": "held",
                    "surfaces": ["task"],
                    "costRank": 30,
                },
                {
                    "model": "demonstrated-model",
                    "state": "demonstrated",
                    "surfaces": ["task"],
                    "costRank": 10,
                    "evidence": [{
                        "ref": "https://example.com/evidence",
                        "observedAt": "2026-01-01",
                        "sampleCount": 3,
                    }],
                },
            ],
        }),
    )

    payload = json.loads(_run(repo, home).stdout)
    entries = payload["purposes"]["evidence"]

    assert [entry["model"] for entry in entries] == [
        "demonstrated-model",
        "shared-model",
    ]
    assert entries[1]["state"] == "held"
    assert payload["sources"] == {
        "operator": "loaded",
        "repository": "loaded",
    }


def test_invalid_layer_fails_open_with_diagnostic(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        {
            **_config({}),
            "execute": "never",
        },
    )

    result = _run(repo, home)
    payload = json.loads(result.stdout)

    assert payload["sources"]["repository"] == "invalid"
    assert payload["purposes"] == {}
    assert payload["diagnostics"]
    assert "[agent-conduct-guidance]" in result.stderr


def test_invalid_operator_suppresses_repository_choices(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        _config({
            "coding": [{
                "model": "repository-model",
                "state": "demonstrated",
                "surfaces": ["task"],
                "evidence": [{
                    "ref": "https://example.com/repository-evidence",
                    "observedAt": "2026-01-01",
                    "sampleCount": 3,
                }],
            }],
        }),
    )
    _write(
        home / ".copilot" / "model-routing.json",
        {
            **_config({}),
            "unsupported": True,
        },
    )

    payload = json.loads(_run(repo, home).stdout)

    assert payload["sources"] == {
        "operator": "invalid",
        "repository": "loaded",
    }
    assert payload["purposes"] == {}
    assert "repository config suppressed" in payload["diagnostics"][-1]


def test_schema_invalid_null_operator_field_suppresses_repository(
    tmp_path: Path,
) -> None:
    repo, home = _repo(tmp_path)
    _write(
        repo / ".github" / "copilot" / "model-routing.json",
        _config({
            "coding": [{
                "model": "repository-model",
                "state": "demonstrated",
                "surfaces": ["task"],
                "evidence": [{
                    "ref": "https://example.com/repository-evidence",
                    "observedAt": "2026-01-01",
                    "sampleCount": 3,
                }],
            }],
        }),
    )
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "coding": [{
                "model": "operator-model",
                "state": "candidate",
                "surfaces": ["task"],
                "constraints": None,
            }],
        }),
    )

    payload = json.loads(_run(repo, home).stdout)

    assert payload["sources"] == {
        "operator": "invalid",
        "repository": "loaded",
    }
    assert payload["purposes"] == {}
    assert "constraints must be an array" in payload["diagnostics"][0]


def test_duplicate_object_keys_make_layer_invalid(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    config = home / ".copilot" / "model-routing.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{"schema":"copilot-extensions.model-routing","version":1,'
        '"purposes":{"coding":[{"model":"duplicate-state",'
        '"state":"held","state":"demonstrated","surfaces":["task"],'
        '"evidence":[{"ref":"https://example.com/evidence",'
        '"observedAt":"2026-01-01","sampleCount":3}]}]}}',
        encoding="utf-8",
    )

    payload = json.loads(_run(repo, home).stdout)

    assert payload["sources"]["operator"] == "invalid"
    assert payload["purposes"] == {}
    assert "duplicate object key: state" in payload["diagnostics"][0]


def test_loader_matches_surface_date_and_notes_schema(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    operator = home / ".copilot" / "model-routing.json"
    _write(
        operator,
        _config({
            "evidence": [{
                "model": "valid-notes",
                "state": "demonstrated",
                "surfaces": ["task"],
                "evidence": [{
                    "ref": "https://example.com/evidence",
                    "observedAt": "2026-01-01",
                    "sampleCount": 1,
                    "notes": "",
                }],
            }],
        }),
    )
    loaded = json.loads(_run(repo, home).stdout)
    assert loaded["purposes"]["evidence"][0]["evidence"][0]["notes"] == ""

    _write(
        operator,
        _config({
            "evidence": [{
                "model": "invalid-surface",
                "state": "candidate",
                "surfaces": ["TASK SPACE"],
            }],
        }),
    )
    invalid_surface = json.loads(_run(repo, home).stdout)
    assert invalid_surface["sources"]["operator"] == "invalid"

    _write(
        operator,
        _config({
            "evidence": [{
                "model": "invalid-date",
                "state": "demonstrated",
                "surfaces": ["task"],
                "evidence": [{
                    "ref": "https://example.com/evidence",
                    "observedAt": "20260101",
                    "sampleCount": 1,
                }],
            }],
        }),
    )
    invalid_date = json.loads(_run(repo, home).stdout)
    assert invalid_date["sources"]["operator"] == "invalid"


def test_demonstrated_model_requires_evidence(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "coding": [{
                "model": "unsupported-demonstration",
                "state": "demonstrated",
                "surfaces": ["task"],
            }],
        }),
    )

    payload = json.loads(_run(repo, home).stdout)

    assert payload["sources"]["operator"] == "invalid"
    assert payload["purposes"] == {}
    assert "evidence is required" in payload["diagnostics"][0]


def test_resolver_is_inert_and_uses_no_process_execution() -> None:
    source = RESOLVER.read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "eval(" not in source
    assert "exec(" not in source
    assert '"--home"' not in source


def test_selects_lowest_cost_demonstrated_model(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "evidence": [
                {
                    "model": "higher-cost",
                    "state": "demonstrated",
                    "surfaces": ["task"],
                    "costRank": 20,
                    "fallbackOrder": ["lower-cost"],
                    "evidence": [{
                        "ref": "https://example.com/higher",
                        "observedAt": "2026-01-01",
                        "sampleCount": 3,
                    }],
                },
                {
                    "model": "lower-cost",
                    "state": "demonstrated",
                    "surfaces": ["task"],
                    "costRank": 10,
                    "evidence": [{
                        "ref": "https://example.com/lower",
                        "observedAt": "2026-01-01",
                        "sampleCount": 3,
                    }],
                },
            ],
        }),
    )

    payload = json.loads(_run(
        repo,
        home,
        "--purpose",
        "evidence",
        "--surface",
        "task",
        "--available-model",
        "higher-cost",
        "--available-model",
        "lower-cost",
        "--as-of",
        "2026-02-01",
    ).stdout)

    assert payload["decision"]["model"] == "lower-cost"
    assert payload["decision"]["mode"] == "ordinary"
    assert payload["decision"]["fallbacks"] == ["higher-cost"]


def test_unavailable_demonstrated_model_falls_through(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "coding": [
                {
                    "model": "preferred",
                    "state": "demonstrated",
                    "surfaces": ["task"],
                    "costRank": 1,
                    "evidence": [{
                        "ref": "https://example.com/preferred",
                        "observedAt": "2026-01-01",
                        "sampleCount": 3,
                    }],
                },
                {
                    "model": "available-fallback",
                    "state": "demonstrated",
                    "surfaces": ["task"],
                    "costRank": 2,
                    "evidence": [{
                        "ref": "https://example.com/fallback",
                        "observedAt": "2026-01-01",
                        "sampleCount": 3,
                    }],
                },
            ],
        }),
    )

    payload = json.loads(_run(
        repo,
        home,
        "--purpose",
        "coding",
        "--surface",
        "task",
        "--available-model",
        "available-fallback",
        "--as-of",
        "2026-02-01",
    ).stdout)

    assert payload["decision"]["model"] == "available-fallback"
    preferred = payload["decision"]["considered"][0]
    assert preferred["reasons"] == ["unavailable"]


def test_candidate_requires_explicit_model_and_trial_id(tmp_path: Path) -> None:
    repo, home = _repo(tmp_path)
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "review": [{
                "model": "candidate-reviewer",
                "state": "candidate",
                "surfaces": ["task"],
                "costRank": 1,
            }],
        }),
    )
    base_args = (
        "--purpose",
        "review",
        "--surface",
        "task",
        "--available-model",
        "candidate-reviewer",
        "--as-of",
        "2026-02-01",
    )

    ordinary = json.loads(_run(repo, home, *base_args).stdout)
    assert ordinary["decision"]["status"] == "no-eligible-model"

    missing_id = _run(
        repo,
        home,
        *base_args,
        "--trial-model",
        "candidate-reviewer",
        check=False,
    )
    assert missing_id.returncode != 0
    assert "requires a non-empty --trial-id" in missing_id.stderr

    blank_id = _run(
        repo,
        home,
        *base_args,
        "--trial-model",
        "candidate-reviewer",
        "--trial-id",
        "   ",
        check=False,
    )
    assert blank_id.returncode != 0
    assert "requires a non-empty --trial-id" in blank_id.stderr

    trial = json.loads(_run(
        repo,
        home,
        *base_args,
        "--trial-model",
        "candidate-reviewer",
        "--trial-id",
        "trial-1",
    ).stdout)
    assert trial["decision"]["model"] == "candidate-reviewer"
    assert trial["decision"]["mode"] == "trial"
    assert trial["decision"]["trialId"] == "trial-1"


def test_selection_filters_surface_context_constraints_and_expiry(
    tmp_path: Path,
) -> None:
    repo, home = _repo(tmp_path)
    _write(
        home / ".copilot" / "model-routing.json",
        _config({
            "coding": [
                {
                    "model": "wrong-surface",
                    "state": "demonstrated",
                    "surfaces": ["restricted-container"],
                    "evidence": [{
                        "ref": "https://example.com/surface",
                        "observedAt": "2026-01-01",
                        "sampleCount": 3,
                    }],
                },
                {
                    "model": "expired",
                    "state": "demonstrated",
                    "surfaces": ["task"],
                    "recheckAfter": "2026-01-31",
                    "evidence": [{
                        "ref": "https://example.com/expired",
                        "observedAt": "2026-01-01",
                        "sampleCount": 3,
                    }],
                },
                {
                    "model": "eligible",
                    "state": "demonstrated",
                    "surfaces": ["task"],
                    "contextTiers": ["long_context"],
                    "reasoningEfforts": ["high"],
                    "constraints": ["typescript"],
                    "evidence": [{
                        "ref": "https://example.com/eligible",
                        "observedAt": "2026-01-01",
                        "sampleCount": 3,
                    }],
                },
            ],
        }),
    )

    payload = json.loads(_run(
        repo,
        home,
        "--purpose",
        "coding",
        "--surface",
        "task",
        "--available-model",
        "wrong-surface",
        "--available-model",
        "expired",
        "--available-model",
        "eligible",
        "--context-tier",
        "long_context",
        "--reasoning-effort",
        "high",
        "--constraint",
        "typescript",
        "--as-of",
        "2026-02-01",
    ).stdout)

    assert payload["decision"]["model"] == "eligible"
    reasons = {
        row["model"]: row["reasons"]
        for row in payload["decision"]["considered"]
    }
    assert reasons["wrong-surface"] == ["surface-mismatch"]
    assert reasons["expired"] == ["evidence-expired"]


def test_selection_requires_purpose_surface_and_availability(
    tmp_path: Path,
) -> None:
    repo, home = _repo(tmp_path)

    missing_surface = _run(
        repo,
        home,
        "--purpose",
        "evidence",
        "--available-model",
        "example",
        check=False,
    )
    assert missing_surface.returncode != 0
    assert "--surface" in missing_surface.stderr

    missing_availability = _run(
        repo,
        home,
        "--purpose",
        "evidence",
        "--surface",
        "task",
        check=False,
    )
    assert missing_availability.returncode != 0
    assert "--available-model" in missing_availability.stderr

    trial_id_without_model = _run(
        repo,
        home,
        "--purpose",
        "evidence",
        "--surface",
        "task",
        "--available-model",
        "example",
        "--trial-id",
        "trial-1",
        check=False,
    )
    assert trial_id_without_model.returncode != 0
    assert "--trial-id requires --trial-model" in trial_id_without_model.stderr
