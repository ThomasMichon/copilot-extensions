"""Selected UV identity is shared by preflight and retained execution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import test_installer_dependency_policy as support

policy = support.policy


def test_windows_absolute_uv_selection_preserves_the_selected_target(tmp_path, monkeypatch):
    logical = str(tmp_path / "links" / "uv.exe")
    physical = str(tmp_path / "packages" / "uv.exe")
    seen = []
    monkeypatch.setattr(policy._configuration, "_WINDOWS", True)
    monkeypatch.setattr(
        policy._configuration, "physical_executable",
        lambda path: seen.append(path) or physical,
    )
    assert policy._select_uv_executable(logical) == physical
    assert seen == [logical]


def test_missing_selected_uv_is_not_replaced_by_another_resolver(tmp_path, monkeypatch):
    monkeypatch.setattr(policy._configuration, "_WINDOWS", True)
    monkeypatch.setattr(policy._configuration, "physical_executable", lambda _: None)
    with pytest.raises(policy.DependencyPolicyError, match="Selected UV executable"):
        policy._select_uv_executable(str(tmp_path / "missing.exe"))


@pytest.mark.parametrize("windows,path", [(False, "/selected/uv"), (True, "uv")])
def test_other_command_resolution_contracts_are_unchanged(monkeypatch, windows, path):
    monkeypatch.setattr(policy._configuration, "_WINDOWS", windows)
    monkeypatch.setattr(
        policy._configuration, "physical_executable",
        lambda _: pytest.fail("changed an unqualified or non-Windows resolver"),
    )
    assert policy._select_uv_executable(path) == path


def test_an_absolute_non_native_wrapper_is_not_reinterpreted(tmp_path, monkeypatch):
    wrapper = str(tmp_path / "uv.cmd")
    monkeypatch.setattr(policy._configuration, "_WINDOWS", True)
    monkeypatch.setattr(
        policy._configuration, "physical_executable",
        lambda _: pytest.fail("changed a caller-selected non-native wrapper"),
    )
    assert policy._select_uv_executable(wrapper) == wrapper


def test_all_settings_and_execution_use_the_same_physical_uv(tmp_path, monkeypatch):
    logical = str(tmp_path / "links" / "uv.exe")
    physical = str(tmp_path / "packages" / "uv.exe")
    commands = []
    environment = {"UNRELATED_VALUE": "retained"}
    monkeypatch.setattr(policy._configuration, "_WINDOWS", True)

    def select(path):
        assert path in (logical, physical)
        return physical

    def run(argv, **kwargs):
        commands.append((argv, kwargs))
        assert argv[0] == physical, "a guarded link reached process creation"
        output = support.settings() if "--show-settings" in argv else ""
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(policy._configuration, "physical_executable", select)
    monkeypatch.setattr(policy.subprocess, "run", run)
    result = policy.run_uv_install(
        logical, ["--no-config", "fixture-package"],
        env=environment, cwd=tmp_path, now=support.NOW,
    )
    assert result.returncode == 0
    assert len(commands) == 3
    assert all(command[0] == physical for command, _ in commands)
    assert "--exclude-newer" in commands[-1][0]
    assert environment == {"UNRELATED_VALUE": "retained"}


def test_sdk_projection_keeps_the_canonical_nontraversing_primitive():
    import installer_engine_ref

    canonical = Path(__file__).resolve().parents[1] / "libs" / "installer-engine"
    expected = installer_engine_ref.native_executable_config(canonical)
    assert expected == (canonical / "installer_dependency_config.py").read_text(encoding="utf-8")


def _projection_fixture(tmp_path):
    canonical = tmp_path / "libs" / "installer-engine"
    canonical.mkdir(parents=True)
    config = canonical / "installer_dependency_config.py"
    config.write_text(
        "prefix = 'preserved'\n# native-executable:begin\nold = True\n"
        "# native-executable:end\nsuffix = 'preserved'\n", encoding="utf-8",
    )
    owner = tmp_path / "libs" / "agent-procutil" / "src" / "agent_procutil" / "_interpreter.py"
    owner.parent.mkdir(parents=True)
    owner.write_text("def physical_executable(path):\n    return path\n", encoding="utf-8")
    return canonical, config, owner


def test_projection_uses_the_canonical_function_without_changing_other_configuration(tmp_path):
    import installer_engine_ref

    canonical, config, owner = _projection_fixture(tmp_path)
    original = config.read_bytes()
    projected = installer_engine_ref.native_executable_config(canonical)
    assert projected.startswith("prefix = 'preserved'\n")
    assert projected.endswith("suffix = 'preserved'\n")
    assert owner.read_text(encoding="utf-8") in projected
    assert "old = True" not in projected
    assert config.read_bytes() == original


@pytest.mark.parametrize("content", [
    "# native-executable:end\n# native-executable:begin\n",
    "# native-executable:begin\n",
    "def select_uv_executable(path): pass\n",
])
def test_projection_refuses_incomplete_or_reordered_markers(tmp_path, content):
    import installer_engine_ref

    canonical, config, _owner = _projection_fixture(tmp_path)
    config.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="projection markers"):
        installer_engine_ref.native_executable_config(canonical)


def test_projection_refuses_a_linked_canonical_authority(tmp_path, native_file_symlink):
    import installer_engine_ref

    canonical, _config, owner = _projection_fixture(tmp_path)
    original = owner.read_bytes()
    outside = tmp_path / "outside.py"
    outside.write_bytes(original)
    owner.unlink()
    native_file_symlink(owner, outside)
    with pytest.raises(ValueError, match="linked native executable source"):
        installer_engine_ref.native_executable_config(canonical)
    assert outside.read_bytes() == original
