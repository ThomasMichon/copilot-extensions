"""Windows pip.ini discovery and ASCII-safe native capture output."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from .test_installer_pip_index import (
    _config,
    reader as reader,
)


@pytest.mark.parametrize("layer", ["global", "legacy", "user", "site", "explicit"])
def test_windows_pip_ini_precedence(
    reader: ModuleType, tmp_path: Path, layer: str,
) -> None:
    home, prefix = tmp_path / "home", tmp_path / "python"
    environment = {
        "PROGRAMDATA": str(tmp_path / "system"), "APPDATA": str(tmp_path / "roaming"),
    }
    files = {
        "global": tmp_path / "system/pip/pip.ini",
        "legacy": home / "pip/pip.ini",
        "user": tmp_path / "roaming/pip/pip.ini",
        "site": prefix / "pip.ini",
        "explicit": tmp_path / "explicit.ini",
    }
    for name, path in files.items():
        _config(path, "https://" + name + ".example/simple")
        if name == layer:
            break
    _config(home / ".config/pip/pip.conf", "https://posix-user.example/simple")
    _config(prefix / "pip.conf", "https://posix-site.example/simple")
    if layer == "explicit":
        environment["PIP_CONFIG_FILE"] = str(files["explicit"])
    assert reader.configured_index(
        environment, home=home, prefix=prefix, platform="win32",
    ) == "https://" + layer + ".example/simple"


@pytest.mark.parametrize("existing", [True, False])
def test_windows_explicit_config_controls_user_file_loading(
    reader: ModuleType, tmp_path: Path, existing: bool,
) -> None:
    home = tmp_path / "home"
    _config(tmp_path / "system/pip/pip.ini", "https://global.example/simple")
    _config(tmp_path / "roaming/pip/pip.ini", "https://user.example/simple")
    explicit = tmp_path / "explicit.ini"
    if existing:
        explicit.write_text("[global]\ntimeout=5\n", encoding="utf-8")
    environment = {
        "PROGRAMDATA": str(tmp_path / "system"), "APPDATA": str(tmp_path / "roaming"),
        "PIP_CONFIG_FILE": str(explicit),
    }
    assert reader.configured_index(
        environment, home=home, prefix=tmp_path / "python", platform="win32",
    ) == ("https://global.example/simple" if existing else "https://user.example/simple")


@pytest.mark.parametrize("outcome", ["selected", "failed", "empty"])
def test_windows_native_folder_fallback(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    def folder(
        window: object, folder_id: int, token: object, flags: int, output: object,
    ) -> int:
        assert window is token is None and folder_id == 26 and flags == 0
        if outcome != "empty":
            setattr(output, "value", str(tmp_path))
        return 1 if outcome == "failed" else 0

    monkeypatch.setattr(reader, "sys", SimpleNamespace(platform="win32"))
    native = ModuleType("ctypes")
    monkeypatch.setattr(
        native, "create_unicode_buffer", lambda _size: SimpleNamespace(value=""),
        raising=False,
    )
    monkeypatch.setattr(
        native, "windll", SimpleNamespace(shell32=SimpleNamespace(SHGetFolderPathW=folder)),
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "ctypes", native)
    if outcome == "selected":
        assert reader._windows_folder({}, "APPDATA", 26) == tmp_path
    else:
        with pytest.raises(OSError):
            reader._windows_folder({}, "APPDATA", 26)


def test_json_index_output_preserves_native_pipe_unicode(
    reader: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    index = "https://index.example/caf\u00e9/simple"
    monkeypatch.setattr(reader, "_uv_default", lambda *_args: False)
    monkeypatch.setattr(reader.os, "environ", {
        "PIP_INDEX_URL": index, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
    })
    assert reader.main(["--json"]) == 0
    output = capsys.readouterr()
    assert output.out.isascii() and json.loads(output.out) == index
    assert not output.err
