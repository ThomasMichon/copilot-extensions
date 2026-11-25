from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.delenv("UV_NO_SYSTEM_CONFIG", raising=False)
    source = Path(__file__).resolve().parents[2] / "libs/installer-engine/installer_pip_index.py"
    spec = importlib.util.spec_from_file_location("installer_pip_index", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(path: Path, index: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[global]\nindex-url = {index}\n", encoding="utf-8")
    return path


def test_index_uses_pip_file_precedence(reader: ModuleType, tmp_path: Path) -> None:
    home, prefix = tmp_path / "home", tmp_path / "python"
    global_file = _config(tmp_path / "system/pip.conf", "https://system.example/simple")
    _config(home / ".pip/pip.conf", "https://legacy.example/simple")
    _config(home / ".config/pip/pip.conf", "https://user.example/a%2Fb/simple")
    assert reader.configured_index(
        {}, home=home, prefix=prefix, global_files=[global_file], platform="linux",
    ) == (
        "https://user.example/a%2Fb/simple"
    )
    _config(prefix / "pip.conf", "https://site.example/simple")
    assert reader.configured_index(
        {}, home=home, prefix=prefix, global_files=[global_file], platform="linux",
    ) == (
        "https://site.example/simple"
    )
    override = _config(tmp_path / "override.conf", "https://override.example/simple")
    assert reader.configured_index(
        {"PIP_CONFIG_FILE": str(override)}, home=home, prefix=prefix, global_files=[global_file],
        platform="linux",
    ) == "https://override.example/simple"


def test_existing_explicit_config_disables_user_files(reader: ModuleType, tmp_path: Path) -> None:
    home = tmp_path / "home"
    _config(home / ".config/pip/pip.conf", "https://user.example/simple")
    override = tmp_path / "override.conf"
    override.write_text("[global]\ntimeout = 5\n", encoding="utf-8")
    system = _config(tmp_path / "system.conf", "https://system.example/simple")
    assert reader.configured_index(
        {"PIP_CONFIG_FILE": str(override)}, home=home, prefix=tmp_path, global_files=[system],
        platform="linux",
    ) == "https://system.example/simple"
    assert reader.configured_index(
        {"PIP_CONFIG_FILE": str(tmp_path / "absent")}, home=home, prefix=tmp_path,
        global_files=[system], platform="linux",
    ) == "https://user.example/simple"


@pytest.mark.parametrize("variable", ["UV_DEFAULT_INDEX", "UV_INDEX_URL", "PIP_INDEX_URL"])
def test_explicit_index_avoids_config_reads(
    reader: ModuleType, tmp_path: Path, variable: str,
) -> None:
    malformed = tmp_path / "invalid.conf"
    malformed.write_text("invalid configuration", encoding="utf-8")
    expected = "https://chosen.example/simple" if variable == "PIP_INDEX_URL" else None
    assert reader.configured_index(
        {variable: "https://chosen.example/simple"}, home=tmp_path, prefix=tmp_path,
        global_files=[malformed],
    ) == expected


def test_null_config_disables_all_files(reader: ModuleType, tmp_path: Path) -> None:
    import os

    malformed = tmp_path / "invalid.conf"
    malformed.write_text("invalid configuration", encoding="utf-8")
    assert reader.configured_index(
        {"PIP_CONFIG_FILE": os.devnull}, home=tmp_path, prefix=tmp_path,
        global_files=[malformed],
    ) is None


def test_null_config_switch_is_literal_not_a_normalized_alias(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    null = tmp_path / "null.conf"
    null.write_text("", encoding="utf-8")
    branch = tmp_path / "branch"
    branch.mkdir()
    system = _config(tmp_path / "system.conf", "https://system.example/simple")
    monkeypatch.setattr(reader, "os", SimpleNamespace(**{**vars(os), "devnull": str(null)}))
    alias = branch / ".." / null.name
    assert reader.configured_index(
        {"PIP_CONFIG_FILE": str(alias)}, home=tmp_path / "home", prefix=tmp_path,
        global_files=[system],
    ) == "https://system.example/simple"
    assert reader.configured_index(
        {"PIP_CONFIG_FILE": str(null)}, home=tmp_path / "home", prefix=tmp_path,
        global_files=[system],
    ) is None


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_empty_xdg_config_dirs_uses_unix_default(reader: ModuleType, value: str | None) -> None:
    environment = {} if value is None else {"XDG_CONFIG_DIRS": value}
    assert reader._global_files(environment, "linux") == [
        Path("/etc/xdg/pip/pip.conf"), Path("/etc/pip.conf"),
    ]


@pytest.mark.parametrize("location", ["native", "xdg", "xdg-missing"])
def test_macos_pip_paths_and_precedence(
    reader: ModuleType, tmp_path: Path, location: str,
) -> None:
    home, prefix = tmp_path / "home", tmp_path / "python"
    environment = {"XDG_DATA_DIRS": str(tmp_path / "global")}
    _config(tmp_path / "global/pip/pip.conf", "https://system.example/simple")
    _config(home / ".pip/pip.conf", "https://legacy.example/simple")
    native = home / "Library/Application Support/pip/pip.conf"
    if location == "native":
        _config(native, "https://selected.example/simple")
    elif location == "xdg":
        environment["XDG_DATA_HOME"] = str(tmp_path / "data")
        _config(tmp_path / "data/pip/pip.conf", "https://selected.example/simple")
    else:
        _config(native, "https://not-selected.example/simple")
        environment["XDG_DATA_HOME"] = str(tmp_path / "missing")
        _config(home / ".config/pip/pip.conf", "https://selected.example/simple")
    assert reader._global_files({}, "darwin") == [
        Path("/Library/Application Support/pip/pip.conf"),
    ]
    assert reader.configured_index(
        environment, home=home, prefix=prefix, platform="darwin",
    ) == "https://selected.example/simple"


@pytest.mark.parametrize("option", ["index-url", "index_url"])
def test_later_file_defaults_replace_earlier_global_options(
    reader: ModuleType, tmp_path: Path, option: str,
) -> None:
    first = _config(tmp_path / "first.conf", "https://old.example/simple")
    last = tmp_path / "last.conf"
    last.write_text(
        f"[DEFAULT]\n{option} = https://selected.example/simple\n[global]\ntimeout = 5\n",
        encoding="utf-8",
    )
    assert reader.configured_index(
        {}, home=tmp_path, prefix=tmp_path, global_files=[first, last], platform="linux",
    ) == "https://selected.example/simple"


@pytest.mark.parametrize(("first", "last"), [
    ("index_url", "index-url"), ("index-url", "index_url"),
], ids=["underscore-first", "dash-first"])
def test_aliases_in_one_section_keep_the_last_normalized_value(
    reader: ModuleType, tmp_path: Path, first: str, last: str,
) -> None:
    config = tmp_path / "pip.conf"
    config.write_text(
        f"[global]\n{first} = https://old.example/simple\n"
        f"{last} = https://selected.example/simple\n",
        encoding="utf-8",
    )
    assert reader.configured_index(
        {}, home=tmp_path, prefix=tmp_path, global_files=[config], platform="linux",
    ) == "https://selected.example/simple"


def test_uv_default_after_empty_system_directory_still_precedes_pip(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = tmp_path / "system/uv/uv.toml"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(
        '[[index]]\nurl = "https://uv.example/simple"\ndefault = true\n', encoding="utf-8",
    )
    policy = reader._dependency_policy()
    monkeypatch.setattr(policy._configuration, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
    monkeypatch.setattr(reader, "_dependency_policy", lambda: policy)
    assert reader._uv_default({
        "UV_ISOLATED": "1", "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "user"), "XDG_CONFIG_DIRS": ":missing::system:",
        "PIP_INDEX_URL": "https://not-selected.example/simple",
    }, tmp_path)


@pytest.mark.parametrize(("filename", "document"), [
    ("selected.toml", '[[index]]\nurl = "https://uv.example/simple"\ndefault = true\n'),
    ("uv.toml", '[[index]]\nurl = "https://uv.example/simple"\ndefault = true\n'),
    ("pyproject.toml", '[tool.uv.pip]\nindex-url = "https://uv.example/simple"\n'),
], ids=["explicit", "discovered", "project"])
def test_uv_config_default_precedes_pip_environment(
    reader: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    tmp_path: Path, filename: str, document: str,
) -> None:
    configuration = tmp_path / filename
    configuration.write_text(document, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for variable in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_NO_CONFIG", "UV_ISOLATED", "UV_PROJECT",
                     "UV_CONFIG_FILE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "system"))
    if filename == "selected.toml":
        monkeypatch.setenv("UV_CONFIG_FILE", str(configuration))
    monkeypatch.setenv("PIP_INDEX_URL", "https://not-selected.example/simple")
    assert reader.main() == 0
    output = capsys.readouterr()
    assert output.out == output.err == ""


@pytest.mark.parametrize("selection", [
    "relative-directory-project", "absolute-directory-project",
    "relative-directory-config", "absolute-directory-config",
    "isolated-project", "explicit-index",
])
def test_index_reader_uses_construction_directory_for_relative_uv_inputs(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], selection: str,
) -> None:
    shell = tmp_path / "launcher"
    effective = shell / "selected directory"
    project = effective / "project"
    project.mkdir(parents=True)
    configuration = effective / "configuration/selected.toml"
    configuration.parent.mkdir()
    document = '[[index]]\nurl = "https://uv.example/simple"\ndefault = true\n'
    (project / "uv.toml").write_text(document, encoding="utf-8")
    configuration.write_text(document, encoding="utf-8")
    environment = {
        "UV_WORKING_DIR": str(effective) if selection.startswith("absolute-") else effective.name,
        "HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "user"),
        "XDG_CONFIG_DIRS": str(tmp_path / "system"), "APPDATA": str(tmp_path / "user"),
        "SYSTEMDRIVE": str(tmp_path / "drive"), "PIP_INDEX_URL": "https://pip.example/simple",
    }
    if selection.endswith("-config"):
        environment["UV_CONFIG_FILE"] = "configuration/selected.toml"
    else:
        environment["UV_PROJECT"] = "project"
    if selection == "isolated-project":
        environment["UV_ISOLATED"] = "1"
    elif selection == "explicit-index":
        environment["UV_DEFAULT_INDEX"] = "https://explicit.example/simple"
    monkeypatch.chdir(shell)
    monkeypatch.setattr(reader.os, "environ", environment)
    policy = reader._dependency_policy()
    assert policy._source_inputs([], environment, shell, ())[0] == effective
    monkeypatch.setattr(reader, "_dependency_policy", lambda: policy)
    assert reader.main() == 0
    output = capsys.readouterr()
    assert output.out == ("https://pip.example/simple\n" if selection == "isolated-project" else "")
    assert output.err == ""


@pytest.mark.parametrize("higher", ["project", "environment", "distinct"])
def test_effective_named_uv_default_respects_configuration_precedence(
    reader: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    tmp_path: Path, higher: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    user = tmp_path / "user/uv/uv.toml"
    user.parent.mkdir(parents=True)
    user.write_text(
        '[[index]]\nname = "shared"\nurl = "https://user.example/simple"\ndefault = true\n',
        encoding="utf-8",
    )
    index_name = "other" if higher == "distinct" else "shared"
    document = "" if higher == "environment" else (
        f'[[index]]\nname = "{index_name}"\nurl = "https://project.example/simple"\n'
    )
    (project / "uv.toml").write_text(document, encoding="utf-8")
    system = project / "system/uv/uv.toml"
    system.parent.mkdir(parents=True)
    system.write_text("", encoding="utf-8")
    policy = reader._dependency_policy()
    monkeypatch.setattr(policy._configuration, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
    monkeypatch.setattr(reader, "_dependency_policy", lambda: policy)
    monkeypatch.chdir(project)
    for variable in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_NO_CONFIG", "UV_ISOLATED",
                     "UV_PROJECT", "UV_CONFIG_FILE", "UV_INDEX"):
        monkeypatch.delenv(variable, raising=False)
    for key, value in {
        "HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "user"),
        "XDG_CONFIG_DIRS": "system", "PIP_INDEX_URL": "https://pip.example/simple",
    }.items():
        monkeypatch.setenv(key, value)
    if higher == "environment":
        monkeypatch.setenv("UV_INDEX", "shared=https://environment.example/simple")
    assert reader.main() == 0
    output = capsys.readouterr()
    assert output.out == ("" if higher == "distinct" else "https://pip.example/simple\n")
    assert output.err == ""


@pytest.mark.parametrize(("flag", "system_selected", "user_default"), [
    (None, True, False), ("0", True, False), ("1", False, False), ("YES", False, True),
], ids=["unset", "false", "true", "boolish"])
def test_system_uv_flag_preserves_user_and_project_retention_inputs(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    flag: str | None, system_selected: bool, user_default: bool,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    project_config = project / "uv.toml"
    project_config.write_text("", encoding="utf-8")
    user = tmp_path / "user/uv/uv.toml"
    user.parent.mkdir(parents=True)
    user.write_text(
        '[[index]]\nurl = "https://user.example/simple"\ndefault = true\n' if user_default else "",
        encoding="utf-8",
    )
    system = project / "system/uv/uv.toml"
    system.parent.mkdir(parents=True)
    system.write_text(
        '[[index]]\nurl = "https://system.example/simple"\ndefault = true\n', encoding="utf-8",
    )
    policy = reader._dependency_policy()
    monkeypatch.setattr(policy._configuration, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
    environment = {
        "HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "user"),
        "XDG_CONFIG_DIRS": "system", "PIP_INDEX_URL": "https://pip.example/simple",
    }
    if flag is not None:
        environment["UV_NO_SYSTEM_CONFIG"] = flag
    inputs = policy._config_inputs([], environment, project)
    assert (project_config, False) in inputs and (user, False) in inputs
    assert ((system, False) in inputs) is system_selected
    assert policy._config_inputs([], {**environment, "UV_CONFIG_FILE": str(system)}, project) == [
        (system, False),
    ]
    monkeypatch.setattr(reader, "_dependency_policy", lambda: policy)
    monkeypatch.setattr(policy._configuration.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="uv 0.11.16\n",
    ))
    assert reader._uv_default(
        environment, project, str(tmp_path / "selected-uv"),
    ) is (system_selected or user_default)


@pytest.mark.parametrize("version", ["0.8.0", "0.11.15", "0.11.16", "0.12.5"])
def test_system_flag_qualifies_the_selected_provider_before_discovery_or_retention(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str,
) -> None:
    policy = reader._dependency_policy()
    monkeypatch.setattr(reader, "_dependency_policy", lambda: policy)
    probes = []

    def run(command: list[str], **_options: object) -> SimpleNamespace:
        probes.append(command)
        return SimpleNamespace(returncode=0, stdout=f"uv {version}\n")

    monkeypatch.setattr(policy._configuration.subprocess, "run", run)
    uv = str(tmp_path / "selected-uv")
    environment = {"UV_NO_SYSTEM_CONFIG": "1"}
    supported = version in {"0.11.16", "0.12.5"}
    actions: tuple[Callable[[], object], ...] = (
        lambda: reader._uv_default(environment, tmp_path, uv),
        lambda: policy.prepare_uv_install(uv, [], env=environment, cwd=tmp_path),
        lambda: policy.retain_uv_install(uv, [], env=environment, cwd=tmp_path).__enter__(),
    )
    if not supported:
        for action in actions:
            with pytest.raises((ValueError, RuntimeError), match="0.11.16"):
                action()
        assert probes == [[uv, "--version"]] * len(actions)
    else:
        policy._configuration.require_system_config_provider(
            uv, environment, error_type=ValueError,
        )
        assert probes == [[uv, "--version"]]


def test_reader_reports_safe_provider_requirement(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = reader._dependency_policy()
    monkeypatch.setattr(reader, "_dependency_policy", lambda: policy)
    monkeypatch.setattr(policy._configuration.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="uv 0.8.0\n", stderr="private-feed-value",
    ))
    monkeypatch.setenv("UV_NO_SYSTEM_CONFIG", "1")
    for variable in ("UV_INDEX_URL", "UV_DEFAULT_INDEX"):
        monkeypatch.delenv(variable, raising=False)
    assert reader.main(["--uv", str(tmp_path / "uv")]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "UV_NO_SYSTEM_CONFIG requires UV 0.11.16 or newer\n"


def test_system_flag_pins_windows_wrapper_from_captured_path(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, providers, child = (tmp_path / name for name in ("parent", "providers", "child"))
    for directory in (parent, providers, child):
        directory.mkdir()
    wrapper = providers / "uv.cmd"
    wrapper.write_text("@echo off\necho uv 0.11.16\n", encoding="utf-8")
    wrapper.chmod(0o700)
    (parent / "uv.cmd").write_text("wrong provider", encoding="utf-8")
    monkeypatch.chdir(parent)
    monkeypatch.setenv("PATHEXT", ".UNRELATED")
    configuration = reader._dependency_policy()._configuration
    monkeypatch.setattr(configuration, "_WINDOWS", True)
    probes: list[list[str]] = []

    def run(command: list[str], **_options: object) -> SimpleNamespace:
        probes.append(command)
        assert command == [str(wrapper), "--version"]
        return SimpleNamespace(returncode=0, stdout="uv 0.11.16\n")

    monkeypatch.setattr(configuration.subprocess, "run", run)
    for path in (str(providers), f'"{providers}"'):
        selected = configuration.require_system_config_provider(
            "uv.cmd", {"UV_NO_SYSTEM_CONFIG": "1", "PATH": path},
            cwd=child, error_type=ValueError,
        )
        assert selected == str(wrapper)
    assert probes == [[str(wrapper), "--version"]] * 2


@pytest.mark.parametrize("lookup", ["relative-path", "relative-argument"])
def test_reader_keeps_provider_launch_directory_before_uv_configuration_directory(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lookup: str,
) -> None:
    launch = tmp_path / "launch"
    effective = launch / "selected"
    provider = launch / "bin/uv"
    provider.parent.mkdir(parents=True)
    provider.write_text("prepared provider", encoding="utf-8")
    provider.chmod(0o700)
    effective.mkdir()
    (effective / "uv.toml").write_text(
        '[[index]]\nurl = "https://configured.example/simple"\ndefault = true\n',
        encoding="utf-8",
    )
    policy = reader._dependency_policy()
    monkeypatch.setattr(reader, "_dependency_policy", lambda: policy)
    monkeypatch.chdir(launch)
    monkeypatch.setattr(policy._configuration, "_WINDOWS", False)
    probes = []

    def run(command: list[str], **_options: object) -> SimpleNamespace:
        probes.append(command)
        assert command == [str(provider), "--version"]
        return SimpleNamespace(returncode=0, stdout="uv 0.13.7\n")

    monkeypatch.setattr(policy._configuration.subprocess, "run", run)
    assert reader._uv_default({
        "UV_NO_SYSTEM_CONFIG": "1", "UV_WORKING_DIR": "selected", "PATH": "bin",
        "HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "user"),
    }, launch, "bin/uv" if lookup == "relative-argument" else "uv")
    assert probes == [[str(provider), "--version"]]


def test_system_flag_qualifies_from_short_windows_directory(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = reader._dependency_policy()._configuration
    monkeypatch.setattr(configuration, "_WINDOWS", True)
    deep = tmp_path / ("deep" * 50) / ("path" * 50)
    assert len(str(deep)) >= 260
    uv = str(tmp_path / "uv")

    def run(command: list[str], **options: object) -> SimpleNamespace:
        assert command == [uv, "--version"]
        assert options["cwd"] == Path(deep.anchor)
        return SimpleNamespace(returncode=0, stdout="uv 0.11.16\n")

    monkeypatch.setattr(configuration.subprocess, "run", run)
    assert configuration.require_system_config_provider(
        uv, {"UV_NO_SYSTEM_CONFIG": "1"}, cwd=deep, error_type=ValueError,
    ) == uv


@pytest.mark.parametrize("variable", ["UV_NO_CONFIG", "UV_ISOLATED"])
@pytest.mark.parametrize("value", ["yes", "ON", "t", "Y"])
def test_boolish_config_flags_preserve_composed_index_precedence(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], variable: str, value: str,
) -> None:
    (tmp_path / "uv.toml").write_text(
        '[[index]]\nurl = "https://disabled.example/simple"\ndefault = true\n',
        encoding="utf-8",
    )
    system = tmp_path / "system/uv/uv.toml"
    system.parent.mkdir(parents=True)
    system.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for key in ("UV_NO_CONFIG", "UV_ISOLATED", "UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_CONFIG_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(variable, value)
    monkeypatch.setenv("PIP_INDEX_URL", "https://selected.example/simple")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "system"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "user"))
    monkeypatch.setenv("SYSTEMDRIVE", str(tmp_path / "drive"))
    assert reader.main() == 0
    output = capsys.readouterr()
    assert output.out == "https://selected.example/simple\n"
    assert output.err == ""


@pytest.mark.parametrize("reference", ["environment", "argument"])
def test_explicit_uv_configuration_precedes_disabled_discovery(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], reference: str,
) -> None:
    config = tmp_path / "explicit.toml"
    config.write_text(
        '[[index]]\nurl = "https://configured.example/simple"\ndefault = true\n',
        encoding="utf-8",
    )
    policy = reader._dependency_policy()
    environment = {"UV_NO_CONFIG": "yes", "PIP_INDEX_URL": "https://not-selected.example/simple"}
    arguments = ["--no-config"]
    if reference == "environment":
        environment["UV_CONFIG_FILE"] = str(config)
    else:
        arguments.extend(["--config-file", str(config)])
    assert policy._config_inputs(arguments, environment, tmp_path) == [(config, False)]
    if reference == "environment":
        monkeypatch.setattr(reader, "_dependency_policy", lambda: policy)
        for key in ("UV_INDEX_URL", "UV_DEFAULT_INDEX"):
            monkeypatch.delenv(key, raising=False)
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        assert reader.main() == 0
        output = capsys.readouterr()
        assert output.out == output.err == ""


def test_system_uv_flag_rejects_invalid_values(reader: ModuleType, tmp_path: Path) -> None:
    policy = reader._dependency_policy()
    with pytest.raises(RuntimeError, match="UV_NO_SYSTEM_CONFIG"):
        policy._config_inputs([], {"UV_NO_SYSTEM_CONFIG": "invalid"}, tmp_path)


def test_system_uv_flag_keeps_windows_user_configuration(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = tmp_path / "user/uv/uv.toml"
    user.parent.mkdir(parents=True)
    user.write_text("", encoding="utf-8")
    system = Path(str(tmp_path / "drive") + "\\") / "ProgramData/uv/uv.toml"
    system.parent.mkdir(parents=True)
    system.write_text("", encoding="utf-8")
    policy = reader._dependency_policy()
    monkeypatch.setattr(policy._configuration, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    inputs = policy._config_inputs([], {
        "UV_ISOLATED": "1", "APPDATA": str(tmp_path / "user"),
        "SYSTEMDRIVE": str(tmp_path / "drive"), "UV_NO_SYSTEM_CONFIG": "1",
    }, tmp_path)
    assert (user, False) in inputs and (system, False) not in inputs


@pytest.mark.parametrize("value", [" ", "\t"], ids=["spaces", "tabs"])
def test_whitespace_pip_environment_refuses_instead_of_falling_back(
    reader: ModuleType, tmp_path: Path, value: str,
) -> None:
    with pytest.raises(ValueError):
        reader.configured_index(
            {"PIP_INDEX_URL": value}, home=tmp_path, prefix=tmp_path, global_files=[],
        )


def test_install_guard_accepts_only_complete_resolved_owner_policy_calls() -> None:
    from install_contract_guard import _incomplete_dependency_policy_calls, _uses_dependency_policy_install

    call = (
        'invoke_uv_bootstrap_environment "$VENV_PYTHON" -I '
        '"$(dirname "$index_helper")/installer_dependency_policy.py" '
        '--uv "$uv" -- --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet'
    )
    assert _uses_dependency_policy_install(call, "sh")
    assert _incomplete_dependency_policy_calls(call) == 0
    assert not _uses_dependency_policy_install(call.replace("--uv", "--other-provider"), "sh")
    assert _incomplete_dependency_policy_calls(call.replace(" -- --python", " --python")) == 1
    assert not _uses_dependency_policy_install(call.replace("installer_dependency_policy.py", "other.py"), "sh")


def test_invalid_config_fails_without_exposing_values(
    reader: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "invalid.conf"
    malformed.write_text("private-feed-value", encoding="utf-8")
    monkeypatch.delenv("UV_DEFAULT_INDEX", raising=False)
    monkeypatch.delenv("UV_INDEX_URL", raising=False)
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.setenv("PIP_CONFIG_FILE", str(malformed))
    monkeypatch.setattr(reader, "_uv_default", lambda *_args: False)
    original = reader.configured_index
    monkeypatch.setattr(reader, "configured_index", lambda environment, **_kwargs: original(
        environment, home=tmp_path, prefix=tmp_path, global_files=[],
    ))
    assert reader.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Cannot read package index configuration.\n"
