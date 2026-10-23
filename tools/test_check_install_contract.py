from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import pytest
import install_contract_guard

pytest.register_assert_rewrite("runtime_profile_cases")
from runtime_profile_cases import TestExactRuntimeProfile as TestExactRuntimeProfile

from test_installer_finalizers import (
    test_posix_purge_ignores_cdpath_for_relative_roots as test_posix_purge_ignores_cdpath_for_relative_roots,
    test_posix_purge_keeps_the_locked_inode_and_link_target as test_posix_purge_keeps_the_locked_inode_and_link_target,
    test_posix_purge_keeps_the_live_pid_fallback as test_posix_purge_keeps_the_live_pid_fallback,
    test_posix_purge_path_resolution_negative_controls as test_posix_purge_path_resolution_negative_controls,
    test_posix_purge_propagates_removal_failure as test_posix_purge_propagates_removal_failure,
    test_posix_purge_preserves_trailing_newlines_in_the_root as test_posix_purge_preserves_trailing_newlines_in_the_root,
    test_posix_purge_preserves_trailing_newlines_in_home as test_posix_purge_preserves_trailing_newlines_in_home,
    test_posix_purge_timeout_retains_native_phase_output_and_original_exception as test_posix_purge_timeout_retains_native_phase_output_and_original_exception,
    test_posix_purge_refuses_home_and_link_roots as test_posix_purge_refuses_home_and_link_roots,
    test_posix_purge_refuses_unreadable_root as test_posix_purge_refuses_unreadable_root,
    test_posix_purge_uses_the_physical_directory_holding_the_lock as test_posix_purge_uses_the_physical_directory_holding_the_lock,
    test_posix_powershell_purge_compares_physical_home_identity as test_posix_powershell_purge_compares_physical_home_identity,
)


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "check-install-contract.py"
PLUGIN = REPO / "plugins" / "agent-pull-requests" / "scripts"

_SPEC = importlib.util.spec_from_file_location("check_install_contract", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)


def _gate_source(tmp_path: Path) -> Path:
    source = REPO / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"
    for name in ("reconcile.py", "update_gate.py", "update_runtime.py", "update_runtime_plan.py"):
        shutil.copyfile(source / name, tmp_path / name)
    return tmp_path


def test_actual_update_machine_gate_contract() -> None:
    source = REPO / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"
    assert checker.check_machine_gate(source) == []


def test_install_guard_runs_machine_gate_contract(monkeypatch, capsys) -> None:
    called = []

    def fail(source):
        called.append(source)
        return ["synthetic machine-gate violation"]

    monkeypatch.setattr(checker, "check_machine_gate", fail)
    assert checker.check() == 1
    assert called == [REPO / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"]
    output = capsys.readouterr()
    assert "synthetic machine-gate violation" in output.out + output.err


@pytest.mark.parametrize("name,before,after", [
    ("update_gate.py", 'if scope != "machine-gated":', "if True:"),
    ("update_gate.py", "repo_dir = context", "repo_dir = None"),
    ("update_gate.py", "return not reconcile.runtime_allowed(", "return reconcile.runtime_allowed("),
    ("update_gate.py", "gate_present=gate_present", "gate_present=True"),
    ("update_gate.py", "load_runtime_gate(repo_dir)", "load_runtime_gate(Path('other'))"),
    ("update_gate.py", "gate_manifest_present(repo_dir)", "gate_manifest_present(Path('other'))"),
    ("update_gate.py", "value=config.configured_machine", "value=None"),
    ("update_runtime.py", "if step.status:", "if False:"),
    ("update_gate.py", "return machine in allowed", "return True"),
    ("update_gate.py", "or physical_machine in allowed", "or False"),
    ("update_gate.py", "repo_dir=repo_dir", "repo_dir=None"),
    ("update_gate.py", "config=config", "config=None"),
])
def test_machine_gate_contract_rejects_actual_source_regressions(
    tmp_path: Path, name: str, before: str, after: str,
) -> None:
    source = _gate_source(tmp_path)
    path = source / name
    text = path.read_text(encoding="utf-8")
    assert before in text
    path.write_text(text.replace(before, after), encoding="utf-8")
    assert checker.check_machine_gate(source)


@pytest.mark.parametrize("function", ["plan_registered", "plan_module"])
@pytest.mark.parametrize("before,after", [
    ("if runtime._machine_gate_blocks(name, scope, gate_context):", "if False:"),
    ("if runtime._machine_gate_blocks(name, scope, gate_context):",
     "if not force and runtime._machine_gate_blocks(name, scope, gate_context):"),
    ("status=runtime.MACHINE_GATED_SKIP, eligible=False", 'status="SKIPPED (current)"'),
])
def test_machine_gate_contract_rejects_each_planner_bypass(
    tmp_path: Path, function: str, before: str, after: str,
) -> None:
    source = _gate_source(tmp_path)
    path = source / "update_runtime_plan.py"
    text = path.read_text(encoding="utf-8")
    node = next(item for item in ast.parse(text).body if isinstance(item, ast.FunctionDef)
                and item.name == function)
    lines = text.splitlines(keepends=True)
    body = "".join(lines[node.lineno - 1:node.end_lineno])
    assert before in body
    lines[node.lineno - 1:node.end_lineno] = [body.replace(before, after)]
    path.write_text("".join(lines), encoding="utf-8")
    assert checker.check_machine_gate(source)


@pytest.mark.parametrize("name", ["update_gate.py", "update_runtime_plan.py"])
def test_machine_gate_contract_reports_missing_source(tmp_path: Path, name: str) -> None:
    source = _gate_source(tmp_path)
    (source / name).unlink()
    assert checker.check_machine_gate(source)


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


def test_captured_uv_call_remains_an_actual_install() -> None:
    sh = '''
source "$SCRIPT_DIR/installer-engine.sh"
if out="$(invoke_uv_pip_install_resilient uv "$@" 2>&1)"; then
    return 0
fi
'''
    assert checker._uses_engine_uv_install(sh, "sh")
    assert not checker._uses_engine_uv_install("# " + sh.replace("\n", "\n# "), "sh")


def test_canonical_policy_invocation_counts_but_documentation_does_not() -> None:
    sh = '''
"$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" \\
    --uv "$(command -v uv)" -- --python "$VENV_PYTHON" "$PLUGIN_DIR"
'''
    assert checker._uses_dependency_policy_install(sh, "sh")
    assert not checker._uses_dependency_policy_install(
        "# " + sh.replace("\n", "\n# "), "sh",
    )
    assert not checker._uses_dependency_policy_install(
        'note="installer_dependency_policy.py --uv uv"', "sh",
    )


@pytest.mark.parametrize("statement", [
    '"$PY" -I "$SCRIPT_DIR/installer_dependency_policy.py" -- --python "$PY"',
    "& $Py -I (Join-Path $PSScriptRoot 'installer_dependency_policy.py') -- --python $Py",
    '"$PY" -I "$SCRIPT_DIR/installer_dependency_policy.py" --uv uv --python "$PY"',
])
def test_policy_calls_require_provider_and_argument_separator(statement):
    assert checker._incomplete_dependency_policy_calls(statement) == 1
    assert checker._incomplete_dependency_policy_calls("# " + statement) == 0


@pytest.mark.parametrize("statement", [
    '"$PY" -I "$SCRIPT_DIR/installer_dependency_policy.py" \\\n    --uv uv -- --python "$PY"',
    "& $Py -I (Join-Path $PSScriptRoot 'installer_dependency_policy.py') `\n    --uv $Uv -- --python $Py",
    "$args = @('-I', (Join-Path $PSScriptRoot 'installer_dependency_policy.py'), '--uv', 'uv', '--', '--python', $Py)",
])
def test_policy_calls_accept_complete_native_and_array_argv(statement):
    assert checker._incomplete_dependency_policy_calls(statement) == 0


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
    assert checker._check_runtime_retention is install_contract_guard.check_runtime_retention
    checker._check_runtime_retention(_runtime())


def test_install_guard_executes_shared_retention_contract(monkeypatch) -> None:
    calls = []

    def check(runtime):
        calls.append(Path(runtime.__file__))
        install_contract_guard.check_runtime_retention(runtime)

    monkeypatch.setattr(checker, "_check_runtime_retention", check)
    assert checker.check() == 0
    assert calls == [checker.VERSIONED_RUNTIME_CANONICAL]


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


# Realistic helpers that propagate the writer's status, so a case exercises
# only its call site.
_SH_HELPER = (
    "_versioned_mark_complete() {\n    local out rc=0\n"
    "    out=\"$(\"$py\" \"${args[@]}\" 2>&1)\" || rc=$?\n    return \"$rc\"\n}\n"
)
_PS1_HELPER = (
    "function Invoke-VersionedMarkComplete {\n"
    "    & $py @mcArgs 2>&1 | ForEach-Object { Write-Host $_ }\n"
    "    if ($LASTEXITCODE -ne 0) {\n        Write-Fail 'x'\n        return $false\n    }\n"
    "    return $true\n}\n"
)


def _scripts(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / "plugins" / "demo" / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return tmp_path / "plugins"


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("install.sh", "_versioned_mark_complete() {\n    \"$py\" \"${args[@]}\" 2>&1 | sed 's/^/  /' || true\n}\n"),
        ("install.sh", _SH_HELPER + "main() {\n    _versioned_mark_complete\n}\n"),
        ("install.ps1", _PS1_HELPER + "    Invoke-VersionedMarkComplete\n"),
        (
            "install.sh",
            "_versioned_mark_complete() {\n    \"$py\" \"${args[@]}\" 2>&1 | sed 's/^/  /' \\\n"
            "        || true\n}\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER
            + "    Invoke-VersionedMarkComplete -PayloadHash $payloadHash\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER
            + "    Invoke-VersionedMarkComplete `\n        -PayloadHash $payloadHash\n",
        ),
        ("install.sh", _SH_HELPER + "main() {\n    _versioned_mark_complete || true\n}\n"),
        ("install.sh", _SH_HELPER + "main() {\n    _versioned_mark_complete || _warn 'x'\n}\n"),
        (
            "install.ps1",
            _PS1_HELPER
            + "    if (-not (Invoke-VersionedMarkComplete)) {\n        Write-Warn 'marker not published'\n    }\n",
        ),
        (
            "install.sh",
            _SH_HELPER + "main() {\n"
            "    if ! _versioned_mark_complete; then\n        _warn 'marker not published'\n    fi\n"
            "    activate\n}\n",
        ),
        (
            "install.sh",
            _SH_HELPER + "main() {\n"
            "    if ! _versioned_mark_complete; then _warn 'exit skipped'; fi\n    activate\n}\n",
        ),
        (
            "install.sh",
            _SH_HELPER + "main() {\n"
            "    if _versioned_mark_complete; then\n        activate\n    else\n        _warn 'x'\n    fi\n}\n",
        ),
        (
            "install.sh",
            _SH_HELPER + "main() {\n"
            "    if ! _versioned_mark_complete; then\n        _warn 'x'\n    else\n        return 0\n    fi\n}\n",
        ),
        (
            "install.ps1",
            "function Invoke-VersionedMarkComplete {\n"
            "    & $py @mcArgs 2>&1 | ForEach-Object { Write-Host $_ }\n    return $true\n}\n"
            "    if (-not (Invoke-VersionedMarkComplete)) { exit 1 }\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER.replace("return $false\n    }", "Write-Warn 'x'\n    }")
            + "    if (-not (Invoke-VersionedMarkComplete)) { exit 1 }\n",
        ),
        (
            "install.sh",
            "_versioned_mark_complete() {\n"
            "    \"$py\" \"${args[@]}\" 2>&1 || _warn 'marker not published'\n}\n"
            "main() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
        (
            "install.ps1",
            "function Invoke-VersionedMarkComplete {\n    return $true\n}\n"
            "    if (-not (Invoke-VersionedMarkComplete)) { exit 1 }\n",
        ),
        ("install.sh", _SH_HELPER + "main() {\n    _versioned_mark_complete || return 0\n}\n"),
        (
            "install.sh",
            _SH_HELPER + "main() {\n"
            "    if ! _versioned_mark_complete; then\n        _warn 'x'\n        return\n    fi\n}\n",
        ),
        (
            "install.sh",
            _SH_HELPER + "main() {\n"
            "    if _versioned_mark_complete || true; then\n        activate\n"
            "    else\n        exit 1\n    fi\n}\n",
        ),
        ("install.ps1", _PS1_HELPER + "    if (-not (Invoke-VersionedMarkComplete)) { exit 0 }\n"),
        (
            "install.ps1",
            _PS1_HELPER.replace("return $false\n    }", "return $true\n    }")
            + "    if (-not (Invoke-VersionedMarkComplete)) { exit 1 }\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER.replace(
                "        Write-Fail 'x'\n        return $false\n    }\n    return $true",
                "        if ($optional) { return $false }\n    }\n    return $true",
            ) + "    if (-not (Invoke-VersionedMarkComplete)) { exit 1 }\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER + "    if (-not (Invoke-VersionedMarkComplete)) {\n"
            "        if ($strict) { exit 1 }\n    }\n",
        ),
        (
            "install.sh",
            _SH_HELPER + "main() {\n    _versioned_mark_complete || { [[ -n \"$strict\" ]] && exit 1; }\n}\n",
        ),
        (
            "install.sh",
            _SH_HELPER + "main() {\n"
            "    if ! _versioned_mark_complete; then\n        if [[ -n x ]]; then return 1; fi\n"
            "    fi\n    activate\n}\n",
        ),
        (
            "install.sh",
            "_versioned_mark_complete() {\n"
            "    \"$py\" \"${args[@]}\" 2>&1 || { [[ -n \"$strict\" ]] && return 1; }\n}\n"
            "main() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER + "    if (Invoke-VersionedMarkComplete) {\n        Activate\n    }\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER + "    if (Invoke-VersionedMarkComplete) { Activate } else { Write-Warn 'x' }\n",
        ),
        ("install.ps1", _PS1_HELPER + "    $ok = Invoke-VersionedMarkComplete\n"),
        ("install.sh", _SH_HELPER + "main() {\n    out=\"$(_versioned_mark_complete)\"\n}\n"),
        (
            "install.sh",
            "_versioned_mark_complete() {\n    local out rc=0\n    out=\"$(\"$py\" \"${args[@]}\" 2>&1)\" || rc=$?\n"
            "    return 0\n}\nmain() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
        (
            "install.sh",
            "_versioned_mark_complete() {\n    local out rc=0\n    out=\"$(\"$py\" \"${args[@]}\" 2>&1)\" || rc=$?\n"
            "    if [[ \"$rc\" -ne 0 ]]; then\n        _warn 'x'\n    fi\n    return 0\n}\n"
            "main() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
        (
            "install.sh",
            "_versioned_mark_complete() {\n    \"$py\" \"${args[@]}\" 2>&1\n    return 0\n}\n"
            "main() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
        ("install.sh", _SH_HELPER + "main() {\n    ok=0\n    _versioned_mark_complete || return \"$ok\"\n}\n"),
        ("install.ps1", _PS1_HELPER + "    if (-not (Invoke-VersionedMarkComplete)) { exit $code }\n"),
        (
            "install.sh",
            "_versioned_mark_complete() {\n    local out=\"$(\"$py\" \"${args[@]}\" 2>&1)\"\n}\n"
            "main() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
        (
            "install.sh",
            "_versioned_mark_complete() {\n    local rc=0\n"
            "    local out=\"$(\"$py\" \"${args[@]}\" 2>&1)\" || rc=$?\n    return \"$rc\"\n}\n"
            "main() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER.replace("return $false\n    }", "return 1\n    }")
            + "    if (-not (Invoke-VersionedMarkComplete)) { exit 1 }\n",
        ),
        ("install.ps1", _PS1_HELPER + "    if (-not (Invoke-VersionedMarkComplete)) { return 1 }\n"),
        ("install.sh", _SH_HELPER + "main() {\n    _versioned_mark_complete || return $false\n}\n"),
    ],
    ids=[
        "sh-swallowed-pipeline", "sh-bare-call", "ps1-bare-call",
        "sh-multiline-swallow", "ps1-ignored-call-with-arguments", "ps1-continued-ignored-call",
        "sh-or-true", "sh-or-warn-only", "ps1-warn-only-body",
        "sh-if-not-warn-only", "sh-if-not-one-line-quoted-stop", "sh-if-else-warn-only",
        "sh-if-not-stop-only-on-success", "ps1-writer-status-ignored",
        "ps1-status-check-only-warns", "sh-writer-fallback-warns", "ps1-no-writer",
        "sh-or-return-0", "sh-if-not-bare-return", "sh-if-condition-or-true",
        "ps1-exit-0", "ps1-helper-failure-returns-true",
        "ps1-helper-conditional-failure", "ps1-call-conditional-exit",
        "sh-or-conditional-exit", "sh-if-not-nested-conditional-return",
        "sh-helper-conditional-return",
        "ps1-positive-if-no-else", "ps1-positive-if-else-warns", "ps1-assigned-result",
        "sh-command-substitution", "sh-captured-status-returns-0", "sh-rc-test-only-warns",
        "sh-writer-status-dropped", "sh-or-return-zero-variable", "ps1-exit-arbitrary-variable",
        "sh-local-masks-writer", "sh-local-masks-captured-writer",
        "ps1-helper-returns-numeric", "ps1-call-returns-numeric", "sh-returns-ps1-false",
    ],
)
def test_ignored_completion_marker_failures_are_violations(tmp_path, name, text) -> None:
    assert checker._mark_complete_result_violations(_scripts(tmp_path, name, text))


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (
            "install.sh",
            "_versioned_mark_complete() {\n    local out rc=0\n"
            "    out=\"$(\"$py\" \"${args[@]}\" 2>&1)\" || rc=$?\n    return \"$rc\"\n}\n"
            "main() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER + "    if (-not (Invoke-VersionedMarkComplete)) { exit 1 }\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER.replace(
                "& $py @mcArgs 2>&1 | ForEach-Object { Write-Host $_ }\n"
                "    if ($LASTEXITCODE -ne 0) {",
                "$result = Invoke-NativeCapture { & $py @mcArgs }\n"
                "    if ($result.ExitCode -ne 0) {",
            ) + "    if (-not (Invoke-VersionedMarkComplete)) { exit 1 }\n",
        ),
        (
            "install.sh",
            _SH_HELPER + "main() {\n"
            "    if ! _versioned_mark_complete; then\n        if [[ -n x ]]; then _warn 'x'; fi\n"
            "        return 1\n    fi\n}\n",
        ),
        (
            "install.sh",
            _SH_HELPER + "main() {\n"
            "    if _versioned_mark_complete; then\n        activate\n    else\n        exit 1\n    fi\n}\n",
        ),
        (
            "install.sh",
            "_versioned_mark_complete() {\n"
            "    \"$py\" \"${args[@]}\" 2>&1 | sed 's/^/  /'\n}\n"
            "main() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER + "    if (Invoke-VersionedMarkComplete -PayloadHash $h) {\n        Activate\n"
            "    } else {\n        Write-Fail 'x'\n        exit 1\n    }\n",
        ),
        (
            "install.ps1",
            _PS1_HELPER + "<# Invoke-VersionedMarkComplete publishes the marker. #>\n"
            "# Invoke-VersionedMarkComplete runs after the health gate.\n"
            "    Write-Host 'see Invoke-VersionedMarkComplete'\n"
            "    if (-not (Invoke-VersionedMarkComplete)) { exit 1 }\n",
        ),
        (
            "install.sh",
            "_versioned_mark_complete() {\n"
            "    if ! \"$py\" \"${args[@]}\" 2>&1 | sed 's/^/  /'; then\n"
            "        _fail 'x'\n        return 1\n    fi\n    return 0\n}\n"
            "main() {\n    _versioned_mark_complete || exit 1\n}\n",
        ),
    ],
    ids=[
        "sh-checked", "ps1-checked", "ps1-captured-exit-code", "sh-if-not-checked-nested",
        "sh-if-else-checked", "sh-pipefail-tail", "ps1-positive-if-else-fails",
        "mentions-in-comments-and-strings", "sh-if-not-writer",
    ],
)
def test_checked_completion_marker_publication_passes(tmp_path, name, text) -> None:
    assert checker._mark_complete_result_violations(_scripts(tmp_path, name, text)) == []


def test_every_shipped_installer_checks_its_completion_marker() -> None:
    assert checker._mark_complete_result_violations(checker.PLUGINS_DIR) == []


@pytest.mark.parametrize(("suffix", "body", "accepted"), [
    (".sh", '_runtime_construction_complete "$actual_hash"\n', True),
    (".sh", '_runtime_construction_complete "$actual_hash" || true\n', False),
    (".sh", '_runtime_construction_complete "$actual_hash"\nreturn 0\n', False),
    (".ps1", "Complete-RuntimeConstruction -PayloadHash $actualHash\nreturn $true\n", True),
    (".ps1", "try {\nComplete-RuntimeConstruction -PayloadHash $actualHash\n} catch {}\nreturn $true\n", False),
    (".ps1", "if ($enabled) {\nComplete-RuntimeConstruction -PayloadHash $actualHash\n}\nreturn $true\n", False),
    (".ps1", "Complete-RuntimeConstruction -PayloadHash $actualHash | Out-Null\nreturn $true\n", False),
])
def test_attached_completion_must_propagate_failure(suffix, body, accepted):
    assert checker._helper_propagates_writer_status(body, suffix) is accepted


@pytest.mark.parametrize("failure", [None, "writer", "hash"])
def test_shipped_powershell_completion_returns_true_only_after_publication(tmp_path, failure):
    host = shutil.which("powershell.exe") if os.name == "nt" else shutil.which("pwsh")
    if not host:
        pytest.skip("no PowerShell")
    helpers = []
    for path in sorted(checker.PLUGINS_DIR.glob("*/scripts/*.ps1")):
        helper = re.search(
            r"^function Invoke-VersionedMarkComplete \{\n.*?^\}\n",
            path.read_text(encoding="utf-8"), re.M | re.S,
        )
        if helper:
            helpers.append((path, helper.group(0)))
    assert len(helpers) >= 10
    script = "$ErrorActionPreference = 'Stop'\n$VersionedRuntime = $true\n"
    script += f"$Failure = '{failure or ''}'\n"
    script += """
function Get-PayloadHash {
    if ($Failure -eq 'hash') { return '' }
    return ('a' * 64)
}
function Assert-SourceRuntime {}
function Assert-SourceRuntimeIdentity {}
function Test-SourceRuntimeUnchanged {}
function Complete-RuntimeConstruction([string]$PayloadHash) {
    if ($PayloadHash -notmatch '^[0-9a-f]{64}$') { throw 'hash-refused' }
    if ($Failure -eq 'writer') { throw 'writer-refused' }
    $script:Published = $true
}
"""
    for path, helper in helpers:
        script += helper + "\n$script:Published = $false\n"
        script += """
$refused = $false
try { $ok = Invoke-VersionedMarkComplete } catch { $refused = $true }
if ($Failure) {
    if (-not $refused -or $script:Published) { throw 'failure did not stop publication' }
} elseif ($refused -or $ok -ne $true -or -not $script:Published) {
    throw 'successful publication did not return true'
}
"""
        script += "Write-Output '" + path.parent.parent.name + "'\n"
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [path.parent.parent.name for path, _ in helpers]


def _bash_environment(bash: str) -> dict[str, str]:
    environment = dict(os.environ)
    if os.name == "nt":
        executable = Path(bash)
        environment["PATH"] = os.pathsep.join((
            str(executable.parent), str(executable.parent.parent / "usr" / "bin"),
            environment.get("PATH", ""),
        ))
    return environment


def _working_bash() -> str | None:
    candidates = [os.environ.get("CE_TEST_BASH")]
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidates.append(str(Path(git).parent.parent / "bin" / "bash.exe"))
        candidates.append(r"C:\Program Files\Git\bin\bash.exe")
    candidates.append(shutil.which("bash"))
    for candidate in filter(None, candidates):
        try:
            probe = subprocess.run(
                [candidate, "-c", 'probe() { printf "%s" "$1"; }; probe "quoted value"'],
                capture_output=True, text=True, timeout=30, env=_bash_environment(candidate),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0 and probe.stdout == "quoted value":
            return candidate
    return None


_SH_HELPER_DEF = re.compile(r"^_versioned_mark_complete\(\) \{\n.*?^\}\n", re.M | re.S)
_SH_GLOBAL_REF = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)")
_SH_HARNESS = """set -euo pipefail
{globals}
_fail() {{ :; }}; err() {{ :; }}; warn() {{ :; }}; ok() {{ :; }}; info() {{ :; }}
_warn() {{ :; }}; _ok() {{ :; }}; _info() {{ :; }}
_assert_source_runtime() {{ :; }}
_assert_source_runtime_identity() {{ :; }}
_source_runtime_unchanged() {{ :; }}
_bootstrap_python() {{ echo "$STUB_PY"; }}
_payload_hash() {{ [[ -z "${{STUB_HASH_FAIL:-}}" ]] || return 1; echo deadbeef; }}
_runtime_construction_complete() {{
    [[ -n "$1" ]] || return 1
    "$STUB_PY"
}}
{helper}
if _versioned_mark_complete >/dev/null; then echo RC=0; else echo "RC=$?"; fi
"""


@pytest.mark.parametrize(("writer_rc", "hash_fails"), [(0, False), (3, False), (0, True)],
                         ids=["writer-ok", "writer-fails", "payload-hash-fails"])
def test_every_shipped_shell_helper_reports_its_writer_status(
    tmp_path, writer_rc, hash_fails
) -> None:
    """Run each shipped ``_versioned_mark_complete`` against a stub writer.

    A writer exit must fail the helper and a clean write must pass it. Only
    the globals the installer itself assigns are defined, so a helper reading
    a variable its script never sets fails under ``set -u`` instead of passing.
    A helper that computes the payload hash must fail, without running the
    writer, when that computation fails: called from a condition (as every
    call site does) bash's ``errexit`` is off inside it.
    """
    bash = _working_bash()
    if bash is None:
        pytest.skip("no working bash")
    stub = tmp_path / "stub-python"
    stub.write_text(
        '#!/usr/bin/env bash\ntouch "$STUB_MARK"\nexit "${STUB_RC:-0}"\n', encoding="utf-8", newline="\n"
    )
    stub.chmod(0o755)
    env = {**_bash_environment(bash), "STUB_PY": stub.as_posix(), "STUB_RC": str(writer_rc)}
    if hash_fails:
        env["STUB_HASH_FAIL"] = "1"
    values = {"VERSIONED_RUNTIME": "1", "SRC_VERSION": "1.2.3", "PAYLOAD_HASH": "deadbeef"}
    failures = []
    helpers = []
    for path in sorted(checker.PLUGINS_DIR.glob("*/scripts/*.sh")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        helper = _SH_HELPER_DEF.search(text)
        if helper is None:
            continue
        helpers.append(path)
        computes_hash = "_payload_hash" in helper.group(0)
        mark = tmp_path / f"{path.parent.parent.name}-{path.stem}.ran"
        env["STUB_MARK"] = mark.as_posix()
        assigned = sorted(
            name for name in set(_SH_GLOBAL_REF.findall(helper.group(0)))
            if re.search(rf"^\s*(?:export\s+|readonly\s+)?{name}=", text, re.M)
        )
        script = _SH_HARNESS.format(
            globals="\n".join(
                f'{name}="{values.get(name, (tmp_path / "absent" / name).as_posix())}"'
                for name in assigned
            ),
            helper=helper.group(0),
        )
        result = subprocess.run(
            [bash, "-c", script], capture_output=True, text=True, env=env, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        status = result.stdout.strip().rpartition("RC=")[2]
        should_fail = writer_rc != 0 or (hash_fails and computes_hash)
        if hash_fails and computes_hash and mark.exists():
            failures.append(f"{path.relative_to(REPO).as_posix()}: wrote a marker without a payload hash")
        if (status == "0") == should_fail:
            failures.append(f"{path.relative_to(REPO).as_posix()}: RC={status} {result.stderr.strip()}")
    assert len(helpers) >= 10
    assert failures == []


_SH_PAYLOAD_HASH_DEF = re.compile(r"^_payload_hash\(\) \{\n.*?^\}\n", re.M | re.S)


@pytest.mark.parametrize("tools", ["working", "failing"])
def test_shipped_payload_hash_helpers_fail_closed(tmp_path, tools) -> None:
    """Run each shipped self-contained ``_payload_hash`` for real.

    With a working SHA-256 tool it prints a 64-hex digest; when every tool
    fails it must exit non-zero with no output, so a completion marker is
    never published without its fingerprint (a ``|| true`` tail used to turn
    a failed hash into a successful empty one).
    """
    bash = _working_bash()
    if bash is None:
        pytest.skip("no working bash")
    payload = tmp_path / "payload"
    (payload / "libs" / "lib").mkdir(parents=True)
    (payload / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (payload / "libs" / "lib" / "pyproject.toml").write_text("[project]\nname='l'\n", encoding="utf-8")
    procutil = payload / "libs" / "agent-procutil"
    procutil.mkdir()
    (procutil / "pyproject.toml").write_text("[project]\nname='agent-procutil'\n", encoding="utf-8")
    stubs = (
        "sha256sum() { return 1; }; shasum() { return 1; }; openssl() { return 1; }\n"
        if tools == "failing" else ""
    )
    checked, failures = [], []
    for path in sorted(checker.PLUGINS_DIR.glob("*/scripts/*.sh")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        helper = _SH_PAYLOAD_HASH_DEF.search(text)
        if helper is None:
            continue
        # Source validation may need Python, but this fixture has no source
        # receipt and still exercises the original package-only hash for real.
        package_hash = re.sub(
            r'(?ms)^    if \[\[ -n "\$\{SOURCE_RUNTIME_IDENTITY:-\}" \]\]; then\n.*?^    fi\n',
            "",
            helper.group(0),
            count=1,
        )
        # Include streaming hashes as well as buffered hashes, but not helpers
        # needing another resolver or interpreter from their installer.
        if re.search(
            r"\b(?:_bootstrap_python|_compute_payload_hash)\b",
            package_hash,
        ):
            continue
        checked.append(path)
        script = (
            f"set -euo pipefail\nSOURCE_RUNTIME_IDENTITY=''\nPLUGIN_DIR='{payload.as_posix()}'\n{stubs}{helper.group(0)}"
            'if out="$(_payload_hash)"; then echo "RC=0 OUT=$out"; else echo "RC=$? OUT=$out"; fi\n'
        )
        result = subprocess.run(
            [bash, "-c", script], capture_output=True, text=True, timeout=60,
            env=_bash_environment(bash),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        rc, _, out = line.partition(" OUT=")
        name = path.relative_to(REPO).as_posix()
        if tools == "working" and (rc != "RC=0" or not re.fullmatch(r"[0-9a-f]{64}", out)):
            failures.append(f"{name}: {line!r} {result.stderr.strip()}")
        if tools == "failing" and (rc == "RC=0" or out):
            failures.append(f"{name}: hashing failed but returned {line!r}")
    assert len(checked) >= 8
    assert failures == []