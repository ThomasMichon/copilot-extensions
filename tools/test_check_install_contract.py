from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "check-install-contract.py"
PLUGIN = REPO / "plugins" / "agent-pull-requests" / "scripts"

_SPEC = importlib.util.spec_from_file_location("check_install_contract", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)


def test_agent_pull_requests_wrapper_counts_as_real_engine_usage() -> None:
    ps1 = (PLUGIN / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "install.sh").read_text(encoding="utf-8")

    assert checker._uses_installer_engine(ps1, "ps1") is True
    assert checker._uses_engine_uv_install(ps1, "ps1") is True
    assert checker._uses_engine_manifest_writer(ps1, "ps1") is True

    assert checker._uses_installer_engine(sh, "sh") is True
    assert checker._uses_engine_uv_install(sh, "sh") is True
    assert checker._uses_engine_manifest_writer(sh, "sh") is True


def test_engine_usage_bypass_comments_do_not_count() -> None:
    ps1 = """
# . (Join-Path $PSScriptRoot 'installer-engine.ps1')
# Invoke-UvPipInstallResilient -Arguments @('--python', 'x')
$note = "Write-DeployManifest should not count here"
"""
    sh = """
# source "$SCRIPT_DIR/installer-engine.sh"
# invoke_uv_pip_install_resilient "$UV_CMD" --python "$VENV_PYTHON"
note="write_deploy_manifest should not count here"
"""

    assert checker._uses_installer_engine(ps1, "ps1") is False
    assert checker._uses_engine_uv_install(ps1, "ps1") is False
    assert checker._uses_engine_manifest_writer(ps1, "ps1") is False

    assert checker._uses_installer_engine(sh, "sh") is False
    assert checker._uses_engine_uv_install(sh, "sh") is False
    assert checker._uses_engine_manifest_writer(sh, "sh") is False


def test_engine_usage_requires_a_real_function_call() -> None:
    ps1 = """
. (Join-Path $PSScriptRoot 'installer-engine.ps1')
$doc = "Invoke-UvPipInstallResilient"
"""
    sh = """
source "$SCRIPT_DIR/installer-engine.sh"
note="invoke_uv_pip_install_resilient"
"""

    assert checker._uses_installer_engine(ps1, "ps1") is True
    assert checker._uses_engine_uv_install(ps1, "ps1") is False

    assert checker._uses_installer_engine(sh, "sh") is True
    assert checker._uses_engine_uv_install(sh, "sh") is False


def test_engine_usage_accepts_trailing_comment_same_as_shared_parser() -> None:
    sh = '. "$SCRIPT_DIR/installer-engine.sh" # load helpers\n'
    ps1 = ". (Join-Path $PSScriptRoot 'installer-engine.ps1') # load helpers\n"

    assert checker._uses_installer_engine(sh, "sh") is True
    assert checker._uses_installer_engine(ps1, "ps1") is True


def _runtime():
    spec = importlib.util.spec_from_file_location("guard_runtime", checker.VERSIONED_RUNTIME_CANONICAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_retention_contract_executes() -> None:
    checker._check_runtime_retention(_runtime())


@pytest.mark.parametrize("cleanup", ["gc", "toss_incomplete"])
def test_retention_contract_rejects_unprotected_cleanup(monkeypatch, cleanup) -> None:
    runtime = _runtime()
    monkeypatch.setattr(runtime, cleanup, lambda root: ["candidate"])
    with pytest.raises(AssertionError, match="construction owner"):
        checker._check_runtime_retention(runtime)


def test_retention_contract_rejects_unprotected_incomplete_slot(monkeypatch) -> None:
    runtime = _runtime()
    real_slot = runtime.slot
    monkeypatch.setattr(
        runtime, "slot",
        lambda root, version, **kwargs: real_slot(root, version, clean_incomplete=False),
    )
    with pytest.raises(AssertionError, match="incomplete cleanup"):
        checker._check_runtime_retention(runtime)


def _plugin(root: Path, name: str, manifest: object, *, installer: str | None = None) -> None:
    plugin = root / name
    plugin.mkdir(parents=True)
    text = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (plugin / "plugin.json").write_text(text, encoding="utf-8")
    if installer:
        (plugin / "scripts").mkdir()
        (plugin / "scripts" / installer).write_text("# installer\n", encoding="utf-8")


def test_runtime_scope_accepts_reconciled_runtime_and_payload_only(tmp_path) -> None:
    _plugin(tmp_path, "gated", {"runtimeScope": "machine-gated"}, installer="install.ps1")
    _plugin(tmp_path, "everywhere", {"runtimeScope": "universal"}, installer="init.sh")
    _plugin(tmp_path, "skills-only", {"runtimeScope": "none"})
    _plugin(tmp_path, "undeclared-payload", {"name": "undeclared-payload"})

    assert checker._runtime_scope_violations(tmp_path) == []


@pytest.mark.parametrize("installer", ["install.ps1", "install.sh", "init.ps1", "init.sh"])
def test_runtime_installer_must_not_declare_none(tmp_path, installer) -> None:
    _plugin(tmp_path, "agent-example", {"runtimeScope": "none"}, installer=installer)

    violations = checker._runtime_scope_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].startswith("agent-example: ships a runtime installer")
    assert "'none'" in violations[0]


def test_runtime_installer_must_declare_a_scope(tmp_path) -> None:
    _plugin(tmp_path, "agent-example", {"name": "agent-example"}, installer="install.sh")

    violations = checker._runtime_scope_violations(tmp_path)

    assert len(violations) == 1
    assert "undeclared" in violations[0]


@pytest.mark.parametrize("scope", ["machine_gated", "Universal", "", 3, None])
def test_declared_scope_must_be_valid(tmp_path, scope) -> None:
    _plugin(tmp_path, "skills-only", {"runtimeScope": scope})

    violations = checker._runtime_scope_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].startswith("skills-only: runtimeScope ")


def test_unreadable_manifest_is_reported(tmp_path) -> None:
    _plugin(tmp_path, "broken", "{not json")
    _plugin(tmp_path, "array", "[]")

    violations = checker._runtime_scope_violations(tmp_path)

    assert [v.split(":", 1)[0] for v in violations] == ["array", "broken"]


def test_repository_plugins_declare_valid_runtime_scopes() -> None:
    assert checker._runtime_scope_violations(REPO / "plugins") == []


def test_valid_scopes_match_the_reconciler() -> None:
    source = (
        REPO / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "reconcile.py"
    ).read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "VALID_SCOPES" for t in node.targets)
        ):
            assert set(ast.literal_eval(node.value)) == set(checker.VALID_RUNTIME_SCOPES)
            return
    raise AssertionError("reconcile.VALID_SCOPES not found")


@pytest.mark.parametrize(
    "line",
    [
        '    exec 8>"$INSTALL_DIR/.install.lock" 2>/dev/null || return 1',
        "            exec 8>&- 2>/dev/null || true",
        '    exec 9>"${LOCK}" 2> /dev/null || return 0',
        '    exec 8> "$INSTALL_DIR/.install.lock" 2>/dev/null || return 1',
        '    exec 7< "$f" 2>/dev/null',
    ],
)
def test_exec_redirect_that_swallows_stderr_is_rejected(tmp_path, line) -> None:
    scripts = tmp_path / "plugins" / "demo" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "install.sh").write_text(f"#!/usr/bin/env bash\n{line}\n", encoding="utf-8")
    violations = checker._exec_stderr_swallow_violations(tmp_path / "plugins")
    assert len(violations) == 1
    assert "demo/scripts/install.sh:2" in violations[0]


@pytest.mark.parametrize(
    "line",
    [
        '    { exec 8>"$INSTALL_DIR/.install.lock"; } 2>/dev/null || return 1',
        "            { exec 8>&-; } 2>/dev/null || true",
        '        if { exec 8>"$lease_path"; } 2>/dev/null; then',
        '    # exec 8>"$f" 2>/dev/null would swallow stderr',
        '    exec "$PY" -m tool 2>/dev/null',
    ],
)
def test_grouped_exec_redirect_is_accepted(tmp_path, line) -> None:
    scripts = tmp_path / "plugins" / "demo" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "install.sh").write_text(f"#!/usr/bin/env bash\n{line}\n", encoding="utf-8")
    assert checker._exec_stderr_swallow_violations(tmp_path / "plugins") == []


def test_repo_installers_do_not_swallow_stderr_with_exec() -> None:
    assert checker._exec_stderr_swallow_violations(REPO / "plugins") == []