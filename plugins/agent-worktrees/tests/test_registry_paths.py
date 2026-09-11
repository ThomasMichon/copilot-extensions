"""Installation-context selection for config/projects/repos registry paths."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import (
    __main__ as m,
    activity,
    config,
    config_migrations,
    doctor,
    installer,
    project_state,
    registry_paths,
    repos,
)
from agent_worktrees.picker_tui import pivots

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CONTEXT_HELPER = (
    PLUGIN_ROOT / "scripts" / "installation-context" / "installation_context.py"
)
REGISTRY_HELPER = PLUGIN_ROOT / "scripts" / "registry_root.py"


def _payload(tmp_path: Path, label: str, plugin: str = "agent-worktrees") -> Path:
    root = tmp_path / label
    helper = root / "scripts" / "installation-context" / "installation_context.py"
    helper.parent.mkdir(parents=True)
    shutil.copyfile(CONTEXT_HELPER, helper)
    shutil.copyfile(REGISTRY_HELPER, root / "scripts" / "registry_root.py")
    (root / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": "0.0.1-dev1"}),
        encoding="utf-8",
    )
    return root


def _stamp(
    durable: Path,
    payload: Path,
    *,
    marketplace: str,
    repository: str,
    plugin: str = "agent-worktrees",
) -> tuple[Path, Path]:
    # Scrub ambient installation-context env this test process may have
    # inherited from a live Copilot CLI session it is itself running inside
    # (COPILOT_PLUGIN_ROOT et al.) -- an explicit --payload-root must win, not
    # conflict with a stray inherited pointer to the *real* installed plugin.
    stamp_env = {**os.environ}
    for stray in ("COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
                  "AGENT_WORKTREES_PAYLOAD_ROOT"):
        stamp_env.pop(stray, None)
    completed = subprocess.run(
        [
            sys.executable,
            str(payload / "scripts" / "installation-context" / "installation_context.py"),
            "stamp",
            "--source-json",
            json.dumps({"source": "github", "repo": repository}),
            "--marketplace-key",
            marketplace,
            "--plugin-id",
            plugin,
            "--payload-root",
            str(payload),
            "--payload-version",
            "0.0.1-dev1",
            "--payload-origin",
            "explicit",
            "--expected-namespace-generation",
            "0",
            "--expected-install-generation",
            "0",
            "--durable-home",
            str(durable),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=stamp_env,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    return Path(result["installReceipt"]), Path(result["pluginRoot"])


def _select(monkeypatch, context: Path, payload: Path) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(payload))
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)


def _register_repo(name: str, remote: str) -> None:
    repos.write_registry(
        repos.ReposRegistry(
            repos={
                name: repos.RepoEntry(
                    name=name,
                    repo_class="worktree",
                    remote=remote,
                )
            }
        )
    )


def test_legacy_paths_remain_exact_and_ignore_runtime_root(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_RT_ROOT", str(tmp_path / "spoofed-runtime"))

    expected = Path.home() / ".agent-worktrees"
    assert config.global_config_path() == expected / "config.yaml"
    assert installer.projects_yaml_path() == expected / "projects.yaml"
    assert repos._repos_yaml_path() == expected / "repos.yaml"
    assert config.project_dir("example") == Path.home() / ".example"


def test_explicit_context_selects_all_three_registry_files(monkeypatch, tmp_path):
    payload = _payload(tmp_path, "payload")
    context, plugin_root = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="example-primary",
        repository="Example-Org/Example-Marketplace",
    )
    _select(monkeypatch, context, payload)

    assert config.global_config_path() == plugin_root / "config.yaml"
    assert config.install_dir() == plugin_root
    assert activity.log_path() == plugin_root / "logs" / "activity.jsonl"
    assert installer.projects_yaml_path() == plugin_root / "projects.yaml"
    assert repos._repos_yaml_path() == plugin_root / "repos.yaml"
    assert doctor._projects_path() == plugin_root / "projects.yaml"
    assert installer._receipt_path("example").is_relative_to(
        config.legacy_install_dir()
    )
    owner = installer._binstub_owner()
    resolved_context = registry_paths.installation_context()
    assert owner["marketplace_id"] == resolved_context["marketplaceId"]
    assert owner["install_receipt"] == resolved_context["installReceipt"]


def test_two_cells_do_not_share_registry_writes(monkeypatch, tmp_path):
    first_payload = _payload(tmp_path, "first-payload")
    second_payload = _payload(tmp_path, "second-payload")
    first_context, first_root = _stamp(
        tmp_path / "durable",
        first_payload,
        marketplace="first",
        repository="Example-Org/First-Marketplace",
    )
    second_context, second_root = _stamp(
        tmp_path / "durable",
        second_payload,
        marketplace="second",
        repository="Example-Org/Second-Marketplace",
    )

    _select(monkeypatch, first_context, first_payload)
    installer.write_projects_registry({"projects": {"first": {}}})
    repos.write_registry(
        repos.ReposRegistry(repos={"first": repos.RepoEntry(name="first")})
    )

    _select(monkeypatch, second_context, second_payload)
    installer.write_projects_registry({"projects": {"second": {}}})
    repos.write_registry(
        repos.ReposRegistry(repos={"second": repos.RepoEntry(name="second")})
    )

    first_projects = yaml.safe_load(
        (first_root / "projects.yaml").read_text(encoding="utf-8")
    )
    second_projects = yaml.safe_load(
        (second_root / "projects.yaml").read_text(encoding="utf-8")
    )
    assert set(first_projects["projects"]) == {"first"}
    assert set(second_projects["projects"]) == {"second"}
    first_repos = (first_root / "repos.yaml").read_text(encoding="utf-8")
    assert "first:" in first_repos
    assert "second:" not in first_repos


def test_invalid_context_refuses_legacy_fallback(monkeypatch, tmp_path):
    payload = _payload(tmp_path, "payload")
    invalid = tmp_path / "invalid" / "install.json"
    invalid.parent.mkdir()
    invalid.write_text("{", encoding="utf-8")
    legacy = Path.home() / ".agent-worktrees"
    (legacy / "projects.yaml").write_text(
        "projects:\n  legacy: {}\n", encoding="utf-8"
    )
    _select(monkeypatch, invalid, payload)

    with pytest.raises(ValueError):
        installer.read_projects_registry()


def test_missing_registry_helper_has_bounded_validation_error(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="missing-helper",
        repository="Example-Org/Missing-Helper-Marketplace",
    )
    (payload / "scripts" / "registry_root.py").unlink()
    _select(monkeypatch, context, payload)

    with pytest.raises(ValueError, match="registry-root helper is unavailable"):
        config.global_config_path()


def test_context_rejects_foreign_plugin_and_payload(monkeypatch, tmp_path):
    aw_payload = _payload(tmp_path, "aw-payload")
    other_payload = _payload(tmp_path, "other-payload", plugin="agent-bridge")
    foreign_context, _ = _stamp(
        tmp_path / "foreign-durable",
        other_payload,
        marketplace="foreign",
        repository="Example-Org/Foreign-Marketplace",
        plugin="agent-bridge",
    )
    _select(monkeypatch, foreign_context, aw_payload)
    with pytest.raises(ValueError):
        repos.read_registry()

    real_context, _ = _stamp(
        tmp_path / "real-durable",
        aw_payload,
        marketplace="real",
        repository="Example-Org/Real-Marketplace",
    )
    spoof_payload = _payload(tmp_path, "spoof-payload")
    _select(monkeypatch, real_context, spoof_payload)
    with pytest.raises(ValueError):
        config.global_config_path()


def test_eager_migration_only_mutates_selected_registry_root(monkeypatch, tmp_path):
    payload = _payload(tmp_path, "payload")
    context, plugin_root = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="migration",
        repository="Example-Org/Migration-Marketplace",
    )
    legacy = Path.home() / ".agent-worktrees" / "projects.yaml"
    legacy.write_text(
        "schema_version: 1\nprojects:\n  legacy:\n    anchor: /legacy\n",
        encoding="utf-8",
    )
    selected = plugin_root / "projects.yaml"
    selected.write_text(
        "schema_version: 1\nprojects:\n  selected:\n    anchor: /selected\n",
        encoding="utf-8",
    )
    _select(monkeypatch, context, payload)

    config_migrations.run_migrations()

    assert yaml.safe_load(selected.read_text(encoding="utf-8"))["schema_version"] == 2
    assert yaml.safe_load(legacy.read_text(encoding="utf-8"))["schema_version"] == 1


def test_namespaced_pivot_activation_stands_down_without_legacy_scan(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="pivots",
        repository="Example-Org/Pivot-Marketplace",
    )
    _select(monkeypatch, context, payload)
    monkeypatch.setattr(
        pivots,
        "resolve_active_plugins",
        lambda: pytest.fail("legacy plugin activation must not run"),
    )

    report = pivots._resolve_activation()

    assert not report.active


def test_namespaced_project_state_uses_stable_repository_identity(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, plugin_root = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="project-state",
        repository="Example-Org/Project-State-Marketplace",
    )
    _select(monkeypatch, context, payload)
    _register_repo(
        "example",
        "git@github.com:Example-Org/Example-Repo.git",
    )

    state_root = project_state.ensure_project_state("example")

    assert config.project_dir("example") == state_root
    config.set_active_project("example")
    assert config.tracking_dir() == state_root / "worktrees"
    identity_path = state_root.parent / "identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    assert identity["repositoryId"].startswith("repo-")
    assert identity["normalizedRemote"] == (
        "network:github.com/example-org/example-repo"
    )
    assert state_root.is_relative_to(plugin_root.parents[1] / "repos")
    assert doctor._overlay_path("~/.legacy-example", "example") == (
        state_root / "config.yaml"
    )


def test_same_repository_has_same_id_but_isolated_cell_state(
    monkeypatch, tmp_path
):
    first_payload = _payload(tmp_path, "first-payload")
    second_payload = _payload(tmp_path, "second-payload")
    first_context, _ = _stamp(
        tmp_path / "durable",
        first_payload,
        marketplace="first-project-state",
        repository="Example-Org/First-State-Marketplace",
    )
    second_context, _ = _stamp(
        tmp_path / "durable",
        second_payload,
        marketplace="second-project-state",
        repository="Example-Org/Second-State-Marketplace",
    )
    remote = "https://github.com/Example-Org/Example-Repo.git"

    _select(monkeypatch, first_context, first_payload)
    _register_repo("first-name", remote)
    first_state = project_state.ensure_project_state("first-name")
    (first_state / "config.yaml").write_text("cell: first\n", encoding="utf-8")

    _select(monkeypatch, second_context, second_payload)
    _register_repo("second-name", remote)
    second_state = project_state.ensure_project_state("second-name")
    (second_state / "config.yaml").write_text("cell: second\n", encoding="utf-8")

    assert first_state.parent.name == second_state.parent.name
    assert first_state != second_state
    assert (first_state / "config.yaml").read_text(encoding="utf-8") == (
        "cell: first\n"
    )
    assert (second_state / "config.yaml").read_text(encoding="utf-8") == (
        "cell: second\n"
    )


def test_default_remote_port_does_not_change_repository_identity(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="default-port",
        repository="Example-Org/Default-Port-Marketplace",
    )
    _select(monkeypatch, context, payload)
    _register_repo(
        "example",
        "https://github.com:443/example/repo.git",
    )
    first = project_state.ensure_project_state("example")
    _register_repo(
        "example",
        "https://github.com/example/repo.git",
    )
    second = project_state.ensure_project_state("example")

    assert first == second


def test_relative_remote_cannot_own_namespaced_project_state(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="relative-remote",
        repository="Example-Org/Relative-Remote-Marketplace",
    )
    _select(monkeypatch, context, payload)
    _register_repo("example", "origin")

    with pytest.raises(ValueError, match="not an absolute stable identity"):
        project_state.ensure_project_state("example")


def test_concurrent_identity_publication_is_idempotent(monkeypatch, tmp_path):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="concurrent-project-state",
        repository="Example-Org/Concurrent-State-Marketplace",
    )
    _select(monkeypatch, context, payload)
    _register_repo("example", "https://github.com/example/repo.git")

    with ThreadPoolExecutor(max_workers=2) as pool:
        roots = list(pool.map(
            lambda _index: project_state.ensure_project_state("example"),
            range(2),
        ))

    assert roots[0] == roots[1]


def test_failed_identity_publication_leaves_no_partial_receipt(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="interrupted-project-state",
        repository="Example-Org/Interrupted-State-Marketplace",
    )
    _select(monkeypatch, context, payload)
    _register_repo("example", "https://github.com/example/repo.git")
    original_publish = project_state._publish_receipt
    monkeypatch.setattr(
        project_state,
        "_publish_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("interrupted")
        ),
    )

    with pytest.raises(OSError, match="interrupted"):
        project_state.ensure_project_state("example")

    cell_root = Path(registry_paths.installation_context()["cellRoot"])
    assert not list((cell_root / "repos").glob("*/identity.json"))
    monkeypatch.setattr(project_state, "_publish_receipt", original_publish)
    assert project_state.ensure_project_state("example").is_dir()


def test_namespaced_project_state_requires_identity_receipt(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="missing-project-identity",
        repository="Example-Org/Missing-Project-Identity-Marketplace",
    )
    _select(monkeypatch, context, payload)
    _register_repo("example", "https://github.com/example/repo.git")

    with pytest.raises(ValueError, match="identity receipt is unreadable"):
        config.project_dir("example")


def test_namespaced_registration_persists_cell_config_dir(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="registration",
        repository="Example-Org/Registration-Marketplace",
    )
    _select(monkeypatch, context, payload)
    _register_repo("example", "https://github.com/example/repo.git")
    monkeypatch.setattr(installer.output, "ok", lambda *_args, **_kwargs: None)

    installer.register_project("example")

    entry = installer.read_projects_registry()["projects"]["example"]
    assert Path(entry["config_dir"]) == config.project_dir("example")


def test_namespaced_first_adoption_prepares_identity_before_project_dir(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="first-adoption",
        repository="Example-Org/First-Adoption-Marketplace",
    )
    _select(monkeypatch, context, payload)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="https://github.com/example/repo.git\n",
        ),
    )

    assert m._prepare_namespaced_project_state(
        "example", repo_dir, "windows"
    )
    assert config.project_dir("example").is_dir()


def test_namespaced_doctor_reports_and_repairs_missing_identity(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="doctor-project-state",
        repository="Example-Org/Doctor-State-Marketplace",
    )
    _select(monkeypatch, context, payload)
    _register_repo("example", "https://github.com/example/repo.git")
    installer.write_projects_registry({
        "projects": {
            "example": {
                "config_dir": "~/.example",
                "expose_agent": True,
            }
        }
    })

    findings = doctor.diagnose()

    assert any(f.kind == "project_identity_invalid" for f in findings)
    fixed = doctor.reconcile(fix=True)
    assert any(
        f.kind == "project_identity_invalid" and f.fixed
        for f in fixed
    )
    assert config.project_dir("example").is_dir()


def test_namespaced_doctor_reports_filesystem_repair_failure(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="doctor-permission",
        repository="Example-Org/Doctor-Permission-Marketplace",
    )
    _select(monkeypatch, context, payload)
    _register_repo("example", "https://github.com/example/repo.git")
    installer.write_projects_registry({
        "projects": {
            "example": {
                "config_dir": "~/.example",
                "expose_agent": True,
            }
        }
    })
    monkeypatch.setattr(
        project_state,
        "ensure_project_state",
        lambda _project: (_ for _ in ()).throw(PermissionError("denied")),
    )

    findings = doctor.reconcile(fix=True)

    identity = next(
        f for f in findings if f.kind == "project_identity_invalid"
    )
    assert not identity.fixed
    assert not identity.fixable
    assert "denied" in identity.fix_detail


def test_namespaced_doctor_repairs_repo_entry_before_identity(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="doctor-missing-repo",
        repository="Example-Org/Doctor-Missing-Repo-Marketplace",
    )
    _select(monkeypatch, context, payload)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(repo_dir)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "remote",
            "add",
            "origin",
            "https://github.com/example/repo.git",
        ],
        check=True,
    )
    installer.write_projects_registry({
        "projects": {
            "example": {
                "anchor": str(repo_dir),
                "config_dir": "~/.example",
                "expose_agent": True,
            }
        }
    })

    findings = doctor.reconcile(fix=True)

    assert any(f.kind == "missing_repo_entry" and f.fixed for f in findings)
    assert any(f.kind == "project_identity_invalid" and f.fixed for f in findings)
    assert config.project_dir("example").is_dir()


def test_namespaced_doctor_reports_repo_registry_write_failure(
    monkeypatch, tmp_path
):
    payload = _payload(tmp_path, "payload")
    context, _ = _stamp(
        tmp_path / "durable",
        payload,
        marketplace="doctor-registry-write",
        repository="Example-Org/Doctor-Registry-Write-Marketplace",
    )
    _select(monkeypatch, context, payload)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(repo_dir)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "remote",
            "add",
            "origin",
            "https://github.com/example/repo.git",
        ],
        check=True,
    )
    installer.write_projects_registry({
        "projects": {
            "example": {
                "anchor": str(repo_dir),
                "config_dir": "~/.example",
                "expose_agent": True,
            }
        }
    })
    monkeypatch.setattr(
        repos,
        "write_registry",
        lambda _registry: (_ for _ in ()).throw(PermissionError("denied")),
    )

    findings = doctor.reconcile(fix=True)

    missing = next(f for f in findings if f.kind == "missing_repo_entry")
    assert not missing.fixed
    assert not missing.fixable
    assert "denied" in missing.fix_detail
    assert any(f.kind == "registry_write_failed" for f in findings)
