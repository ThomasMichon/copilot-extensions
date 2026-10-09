from __future__ import annotations

import importlib.util
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


def _legacy_powershell_paths(root: Path, *, repo_backstop: bool = False) -> list[Path]:
    return [
        path for path in sorted(root.rglob("*.ps1"))
        if not repo_backstop or (
            checker.PLUGINS_DIR not in path.parents
            and not checker.is_ignored_scan_path(path)
        )
    ]


def _script_tree(root: Path) -> None:
    direct = "[Environment]::SetEnvironmentVariable('Path', 'x', 'User')\n"
    safe = "[Environment]::SetEnvironmentVariable('Path', 'x', 'Process')\n"
    adapter = (
        f"{checker.PERSISTENT_ENV_START}\n"
        + direct
        + f"{checker.PERSISTENT_ENV_END}\n"
        + "Set-CopilotPersistentEnvironmentVariable -Name Path -Value x -Target User\n"
    )
    scripts = {
        "plugins/demo/scripts/install.ps1": adapter,
        "plugins/demo/tests/helper.ps1": direct,
        "plugins/payload/scripts/init.ps1": safe,
        "libs/demo/tests/host.ps1": direct,
        "tools/process.ps1": safe,
        "tools/comment.ps1": "# " + direct,
        "tools/string.ps1": 'Write-Host "' + direct.rstrip() + '"\n',
        "tools/registry.ps1": "Set-ItemProperty 'HKCU:\\Environment' -Name Path -Value x\n",
        "integration/untracked/helper.ps1": direct,
        "integration/case.PS1": direct,
        "integration/replacement.ps1": direct + "\ufffd\n",
        "tools/not-a-script.txt": direct,
    }
    for ignored in (
        ".git", ".venv", "venv", ".venv-tools", ".test-venvs", "node_modules"
    ):
        scripts[f"generated/{ignored}/nested/activate.ps1"] = direct
        # These were never excluded from the stricter plugin sweep.
        scripts[f"plugins/demo/{ignored}/nested/activate.ps1"] = direct
    for relative, text in scripts.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "plugins/demo/scripts/install.sh").write_text(
        "_source_kind() { echo payload; }\n", encoding="utf-8"
    )
    (root / "plugins/payload/scripts/init.sh").write_text(
        "_source_kind() { echo other; }\n", encoding="utf-8"
    )
    (root / "plugins/demo/pyproject.toml").write_text("", encoding="utf-8")


def test_powershell_candidates_match_legacy_and_prune_only_backstop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script_tree(tmp_path)
    directory_script = tmp_path / "integration/directory.ps1"
    directory_script.mkdir()
    (directory_script / "nested.ps1").write_text("", encoding="utf-8")
    monkeypatch.setattr(checker, "PLUGINS_DIR", tmp_path / "plugins")
    walk = checker.os.walk
    visited: list[Path] = []

    def recording_walk(*args, **kwargs):
        for directory, dirs, files in walk(*args, **kwargs):
            visited.append(Path(directory))
            yield directory, dirs, files

    monkeypatch.setattr(checker.os, "walk", recording_walk)
    plugins = checker.PLUGINS_DIR
    assert checker._powershell_paths(plugins) == _legacy_powershell_paths(plugins)
    assert plugins / "demo/.venv/nested/activate.ps1" in checker._powershell_paths(plugins)
    visited.clear()
    assert checker._powershell_paths(
        tmp_path, repo_backstop=True
    ) == _legacy_powershell_paths(tmp_path, repo_backstop=True)
    assert directory_script in visited
    assert not any(plugins == path or plugins in path.parents for path in visited)
    assert not any(checker.is_ignored_scan_path(path) for path in visited)


def test_powershell_backstop_preserves_ignored_ancestor_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".venv/repo"
    _script_tree(root)
    monkeypatch.setattr(checker, "PLUGINS_DIR", root / "plugins")
    assert checker._powershell_paths(root, repo_backstop=True) == []
    assert checker._powershell_paths(root / "plugins") == _legacy_powershell_paths(
        root / "plugins"
    )


def test_powershell_candidates_preserve_readable_file_and_directory_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hidden = tmp_path / ".venv"
    hidden.mkdir()
    target = hidden / "target.ps1"
    target.write_text("Write-Host 'example'\n", encoding="utf-8")
    file_link = tmp_path / "readable.ps1"
    directory_link = tmp_path / "directory.ps1"
    try:
        file_link.symlink_to(target)
        directory_link.symlink_to(hidden, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    monkeypatch.setattr(checker, "PLUGINS_DIR", tmp_path / "plugins")
    candidates = checker._powershell_paths(tmp_path, repo_backstop=True)
    assert candidates == _legacy_powershell_paths(tmp_path, repo_backstop=True)
    assert candidates == sorted([file_link, directory_link])
    assert file_link.read_text(encoding="utf-8") == target.read_text(encoding="utf-8")


def test_install_contract_diagnostics_match_legacy_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _script_tree(tmp_path)
    canonical = tmp_path / "versioned_runtime.py"
    canonical.write_text("", encoding="utf-8")
    monkeypatch.setattr(checker, "REPO", tmp_path)
    monkeypatch.setattr(checker, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(checker, "VERSIONED_RUNTIME_CANONICAL", canonical)
    monkeypatch.setattr(checker, "_verify_installer_engine_sync", lambda: [])
    optimized_status = checker.check()
    optimized_output = capsys.readouterr()
    monkeypatch.setattr(checker, "_powershell_paths", _legacy_powershell_paths)
    assert checker.check() == optimized_status == 1
    assert capsys.readouterr() == optimized_output
    assert "libs/demo/tests/host.ps1:" in optimized_output.err
    assert "integration/untracked/helper.ps1:" in optimized_output.err
    assert "plugins/demo/.venv/nested/activate.ps1:" in optimized_output.err
    assert "generated/" not in optimized_output.err
    assert "tools/process.ps1:" not in optimized_output.err
    assert "tools/comment.ps1:" not in optimized_output.err
    assert "tools/string.ps1:" not in optimized_output.err


def test_powershell_scan_surfaces_directory_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_walk(root, *, onerror):
        onerror(PermissionError("script directory unreadable"))
        return iter(())

    monkeypatch.setattr(checker.os, "walk", failing_walk)
    with pytest.raises(PermissionError, match="script directory unreadable"):
        checker._powershell_paths(tmp_path)


def test_install_contract_surfaces_script_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script_tree(tmp_path)
    monkeypatch.setattr(checker, "REPO", tmp_path)
    monkeypatch.setattr(checker, "PLUGINS_DIR", tmp_path / "plugins")
    read_text = Path.read_text
    unreadable = tmp_path / "libs/demo/tests/host.ps1"

    def guarded_read(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("script unreadable")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    with pytest.raises(PermissionError, match="script unreadable"):
        checker.check()
