from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def reader() -> ModuleType:
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
    assert reader.configured_index({}, home=home, prefix=prefix, global_files=[global_file]) == (
        "https://user.example/a%2Fb/simple"
    )
    _config(prefix / "pip.conf", "https://site.example/simple")
    assert reader.configured_index({}, home=home, prefix=prefix, global_files=[global_file]) == (
        "https://site.example/simple"
    )
    override = _config(tmp_path / "override.conf", "https://override.example/simple")
    assert reader.configured_index(
        {"PIP_CONFIG_FILE": str(override)}, home=home, prefix=prefix, global_files=[global_file],
    ) == "https://override.example/simple"


def test_existing_explicit_config_disables_user_files(reader: ModuleType, tmp_path: Path) -> None:
    home = tmp_path / "home"
    _config(home / ".config/pip/pip.conf", "https://user.example/simple")
    override = tmp_path / "override.conf"
    override.write_text("[global]\ntimeout = 5\n", encoding="utf-8")
    system = _config(tmp_path / "system.conf", "https://system.example/simple")
    assert reader.configured_index(
        {"PIP_CONFIG_FILE": str(override)}, home=home, prefix=tmp_path, global_files=[system],
    ) == "https://system.example/simple"
    assert reader.configured_index(
        {"PIP_CONFIG_FILE": str(tmp_path / "absent")}, home=home, prefix=tmp_path,
        global_files=[system],
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
        {}, home=tmp_path, prefix=tmp_path, global_files=[first, last],
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
        {}, home=tmp_path, prefix=tmp_path, global_files=[config],
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
