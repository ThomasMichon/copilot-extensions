"""Adopter data is preflighted before tool execution or native config writes."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from worktree_manager import toolchain_config as tc

INDEX = "https://packages.example.invalid/simple/"
REGISTRY = "https://packages.example.invalid/npm/"
PYTHON_MIRROR = "https://downloads.example.invalid/python/"
UV_MIRROR = "https://downloads.example.invalid/uv/"


@pytest.fixture(autouse=True)
def restore_process_environment():
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


@pytest.mark.parametrize("body", [
    "schema=2\npython_index_url=" + INDEX,
    "schema=1\nunknown=" + INDEX,
    "schema=1\nschema=1\npython_index_url=" + INDEX,
    "schema=1\npython_index_url=https://user:password@example.invalid/simple/",
    "schema=1\npython_index_url=https://example.invalid/simple/?secret=value",
    "schema=1\npython_index_url=$(touch bad)",
    "schema=1\n",
    "python_index_url=" + INDEX,
    "\ufeffschema=1\npython_index_url=" + INDEX,
    "schema=1\n# non-ASCII: \u00e9\npython_index_url=" + INDEX,
])
def test_toolchain_config_rejects_non_data_and_unknown_fields(tmp_path: Path, body: str) -> None:
    config = tmp_path / "toolchain.conf"
    config.write_text(body, encoding="utf-8")
    with pytest.raises((ValueError, UnicodeError)):
        tc.read_configuration(config)


def test_toolchain_config_adds_only_selected_environment_and_preserves_input(tmp_path: Path) -> None:
    config = tmp_path / "toolchain.conf"
    config.write_text(f"# adopter defaults\nschema=1\npython_index_url={INDEX}\n", encoding="ascii")
    values = tc.read_configuration(config)
    original = {"UV_HTTP_TIMEOUT": "200", "UV_INDEX_URL": INDEX.rstrip("/")}
    updated = tc.environment(values, original)
    assert updated == {**original, "UV_DEFAULT_INDEX": INDEX, "PIP_INDEX_URL": INDEX}
    assert original == {"UV_HTTP_TIMEOUT": "200", "UV_INDEX_URL": INDEX.rstrip("/")}
    for name in ("PIP_INDEX_URL", "UV_EXTRA_INDEX_URL", "UV_CONFIG_FILE"):
        with pytest.raises(ValueError, match=name):
            tc.environment(values, {name: "other-selection"})
    assert tc.environment(values, {"PIP_CONFIG_FILE": "relative.ini"})["PIP_CONFIG_FILE"] == str(
        Path("relative.ini").absolute()
    )
    with pytest.raises(ValueError, match="disables"):
        tc.environment(values, {"PIP_CONFIG_FILE": os.devnull})


def _native_fixture(monkeypatch: pytest.MonkeyPatch, home: Path) -> tuple[Path, Path, Path]:
    pip = home / "pip" / "pip.ini"
    uv = home / "uv" / "uv.toml"
    site = home / "venv" / "pip.ini"
    monkeypatch.setattr(tc, "_native_paths", lambda *_: ([pip, site], uv, pip))
    for name in tc.FIELDS:
        for variable in tc.FIELDS[name]:
            monkeypatch.delenv(variable, raising=False)
    for name in (
        "UV_INDEX_URL", "UV_INDEX", "UV_EXTRA_INDEX_URL", "UV_FIND_LINKS",
        "UV_CONFIG_FILE", "UV_INSECURE_HOST", "UV_NO_INDEX", "UV_NO_CONFIG",
        "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS", "PIP_TRUSTED_HOST", "PIP_NO_INDEX",
        "NPM_CONFIG_REGISTRY", "NPM_CONFIG_USERCONFIG", "npm_config_userconfig",
        "NPM_CONFIG_GLOBALCONFIG", "npm_config_globalconfig",
    ):
        monkeypatch.delenv(name, raising=False)
    return pip, uv, site


def test_toolchain_config_preserves_options_and_reconciles_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pip, uv, _ = _native_fixture(monkeypatch, tmp_path)
    pip.parent.mkdir()
    pip.write_text("# pip options\n[global]\ntimeout = 73\n", encoding="utf-8")
    uv.parent.mkdir()
    uv.write_text("# uv options\nhttp-timeout = 73\n", encoding="utf-8")
    npm = tmp_path / ".npmrc"
    npm.write_text("# npm options\nfetch-retries=3\n", encoding="utf-8")
    config = tmp_path / "toolchain.conf"
    config.write_text(
        f"schema=1\npython_index_url={INDEX}\nnpm_registry={REGISTRY}\n", encoding="ascii",
    )
    root = tmp_path / "manager"
    tc.apply_configuration(config, home=tmp_path, root=root)
    before = {path: path.read_bytes() for path in (pip, uv, npm)}
    assert "# pip options\n[global]\nindex-url = " in pip.read_text(encoding="utf-8")
    assert b"timeout = 73" in before[pip]
    assert "# uv options\nhttp-timeout = 73" in uv.read_text(encoding="utf-8")
    assert "# npm options\nfetch-retries=3" in npm.read_text(encoding="utf-8")
    assert tc.read_configuration(root / "toolchain.conf") == tc.read_configuration(config)
    tc.apply_configuration(root / "toolchain.conf", home=tmp_path, root=root)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("conflict", ["site", "command", "npm", "uv"])
def test_toolchain_config_conflicts_do_not_partially_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: str,
) -> None:
    pip, uv, site = _native_fixture(monkeypatch, tmp_path)
    target = site if conflict == "site" else pip
    target.parent.mkdir()
    target.write_text(
        f"[{'install' if conflict == 'command' else 'global'}]\nindex-url = {INDEX}\n",
        encoding="utf-8",
    )
    npm = tmp_path / ".npmrc"
    config = tmp_path / "toolchain.conf"
    config.write_text(f"schema=1\npython_index_url={INDEX}\nnpm_registry={REGISTRY}\n", encoding="ascii")
    root = tmp_path / "manager"
    if conflict in {"site", "command"}:
        target.write_text("[install]\nindex-url = https://other.example.invalid/simple/\n", encoding="utf-8")
    elif conflict == "npm":
        npm.write_text("registry=https://other.example.invalid/\n", encoding="utf-8")
    else:
        uv.parent.mkdir()
        uv.write_text(
            '[[index]]\nurl = "https://other.example.invalid/simple/"\ndefault = true\n', encoding="utf-8",
        )
    before = {path: path.read_bytes() if path.exists() else None for path in (pip, uv, npm, site)}
    environment = dict(os.environ)
    with pytest.raises(ValueError):
        tc.apply_configuration(config, home=tmp_path, root=root)
    assert dict(os.environ) == environment
    assert {path: path.read_bytes() if path.exists() else None for path in before} == before


@pytest.mark.parametrize("kind", ["ps1", "sh"])
@pytest.mark.parametrize("invalid", ["", "unknown", "pip", "uv", "npm", "environment"])
def test_toolchain_config_native_entry_precedes_first_prerequisite(
    tmp_path: Path, kind: str, invalid: str,
) -> None:
    shell = shutil.which("pwsh") if kind == "ps1" else (shutil.which("bash") if os.name != "nt" else None)
    if shell is None:
        pytest.skip("native shell unavailable")
    # This first-touch control stops at uv discovery. No Python, package manager,
    # Git source or installed Manager is required or allowed to execute.
    bootstrap = Path(__file__).resolve().parents[1] / f"bootstrap.{kind}"
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "toolchain.conf"
    config.write_text(
        f"schema=1\npython_index_url={INDEX}\nnpm_registry={REGISTRY}\n"
        f"python_install_mirror={PYTHON_MIRROR}\nuv_install_mirror={UV_MIRROR}\n"
        + ("unknown=value\n" if invalid == "unknown" else ""),
        encoding="ascii",
    )
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("UV_", "PIP_", "NPM_CONFIG_", "COPILOT_EXTENSIONS_TOOLCHAIN_"))
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "appdata"),
        PROGRAMDATA=str(home / "programdata"), XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CONFIG_DIRS=str(home / "global-config"), WORKTREE_MANAGER_ROOT=str(home / "manager"),
    )
    if invalid == "environment":
        env["PIP_INDEX_URL"] = "https://other.example.invalid/simple/"
    elif invalid in {"pip", "uv", "npm"}:
        target = {
            "pip": home / "appdata" / "pip" / "pip.ini" if os.name == "nt" else home / ".config" / "pip" / "pip.conf",
            "uv": home / "appdata" / "uv" / "uv.toml" if os.name == "nt" else home / ".config" / "uv" / "uv.toml",
            "npm": home / ".npmrc",
        }[invalid]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text({
            "pip": "[install]\nindex-url=https://other.example.invalid/simple/\n",
            "uv": '[[index]]\nurl = "https://other.example.invalid/simple/"\ndefault = true\n',
            "npm": "registry=https://other.example.invalid/npm/\n",
        }[invalid], encoding="utf-8")
    if kind == "ps1":
        escaped = str(bootstrap).replace("'", "''")
        selected = str(config).replace("'", "''")
        text = (
            "function Get-Command { param($Name); "
            "if ($Name -like 'uv*') { "
            "[Console]::Out.WriteLine(($env:UV_DEFAULT_INDEX, $env:PIP_INDEX_URL, "
            "$env:npm_config_registry, $env:UV_PYTHON_INSTALL_MIRROR, "
            "$env:UV_INSTALLER_GITHUB_BASE_URL -join '|')); throw 'FIRST_CONSUMER'; }; "
            "throw 'UNEXPECTED_COMMAND' }\n"
            f"& '{escaped}' --toolchain-config '{selected}' --version"
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-Command", text]
    else:
        # A shell function is sufficient: UV discovery uses type -ap and must
        # not execute it. The mocked curl is the first prerequisite acquisition.
        tools = tmp_path / "tools"
        tools.mkdir()
        curl = tools / "curl"
        curl.write_text(
            "#!/bin/sh\nprintf '%s|%s|%s|%s|%s\\n' \"$UV_DEFAULT_INDEX\" \"$PIP_INDEX_URL\" "
            "\"$npm_config_registry\" \"$UV_PYTHON_INSTALL_MIRROR\" "
            "\"$UV_INSTALLER_GITHUB_BASE_URL\" >&2\nexit 91\n", encoding="ascii",
        )
        curl.chmod(0o755)
        env["PATH"] = str(tools) + os.pathsep + os.pathsep.join(
            entry for entry in os.get_exec_path()
            if not any((Path(entry) / name).is_file() for name in ("uv", "uv.exe"))
        )
        command = [shell, str(bootstrap), "--toolchain-config", str(config), "--version"]
    result = subprocess.run(
        command, env=env, cwd=home, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    if invalid:
        assert INDEX not in output
        assert "FIRST_CONSUMER" not in output
    else:
        assert f"{INDEX}|{INDEX}|{REGISTRY}|{PYTHON_MIRROR}|{UV_MIRROR}" in output
    assert not (home / "manager").exists()


@pytest.mark.parametrize("tool", ["pip", "uv", "uv-python", "npm"])
def test_toolchain_config_real_native_tool_reads_persisted_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    for key in list(os.environ):
        if key.upper().startswith(("UV_", "PIP_", "NPM_CONFIG_", "COPILOT_EXTENSIONS_TOOLCHAIN_")):
            monkeypatch.delenv(key)
    for key, path in {
        "HOME": tmp_path, "USERPROFILE": tmp_path,
        "APPDATA": tmp_path / "appdata", "PROGRAMDATA": tmp_path / "programdata",
        "XDG_CONFIG_HOME": tmp_path / ".config", "XDG_CONFIG_DIRS": tmp_path / "global-config",
    }.items():
        monkeypatch.setenv(key, str(path))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    config = tmp_path / "toolchain.conf"
    config.write_text(
        f"schema=1\npython_index_url={INDEX}\nnpm_registry={REGISTRY}\n"
        f"python_install_mirror={PYTHON_MIRROR}\n", encoding="ascii",
    )
    tc.apply_configuration(config, home=tmp_path)
    # A new terminal has no bootstrap-injected index/registry variables.
    env = dict(os.environ)
    for key in ("UV_DEFAULT_INDEX", "PIP_INDEX_URL", "npm_config_registry", "UV_PYTHON_INSTALL_MIRROR"):
        env.pop(key, None)
    env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
    env["npm_config_cache"] = str(tmp_path / "npm-cache")
    env["npm_config_update_notifier"] = "false"
    base_python = sys._base_executable
    expected = INDEX
    if tool == "pip":
        command = [base_python, "-m", "pip", "config", "list"]
        # Windows pip resolves known folders through the OS, not redirected
        # APPDATA. Select the fixture file so this check never reads user state.
        env["PIP_CONFIG_FILE"] = str(
            tmp_path / "appdata" / "pip" / "pip.ini" if os.name == "nt"
            else tmp_path / ".config" / "pip" / "pip.conf"
        )
    else:
        executable = shutil.which("uv" if tool == "uv-python" else tool)
        if executable is None:
            pytest.skip(f"real {tool} unavailable")
        if tool == "npm":
            command = [executable, "config", "get", "registry"]
            expected = REGISTRY
        elif tool == "uv-python":
            env["UV_PYTHON_INSTALL_DIR"] = str(tmp_path / "managed-python")
            command = [executable, "-vv", "--offline", "--no-cache", "python", "install", "--no-bin", "--no-registry", "3.11"]
            expected = PYTHON_MIRROR.rstrip("/")
        else:
            requirements = tmp_path / "requirements.txt"
            requirements.write_text("bootstrap-config-probe-does-not-exist==0.0.0\n", encoding="ascii")
            command = [
                executable, "-vv", "--offline", "--no-cache", "pip", "compile",
                "--python", base_python, str(requirements),
            ]
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if tool == "pip" and "No module named pip" in result.stderr:
        pytest.skip("base interpreter has no pip")
    if tool.startswith("uv"):
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout + result.stderr
    assert "https://pypi.org/simple" not in result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["ps1", "sh"])
@pytest.mark.parametrize("supported", [False, True])
def test_toolchain_config_native_source_skew_is_explicit(
    tmp_path: Path, kind: str, supported: bool,
) -> None:
    from test_bootstrap_ps1 import _GIT_ID, _fake_tools, _source_repo

    shell = shutil.which("pwsh") if kind == "ps1" else (shutil.which("bash") if os.name != "nt" else None)
    if shell is None or shutil.which("git") is None:
        pytest.skip("native shell and Git required")
    source = _source_repo(tmp_path)
    entry = source / "worktree-manager" / "scripts" / "runtime_install.py"
    if supported:
        entry.write_text(entry.read_text(encoding="utf-8") + "# copilot-extensions:toolchain-config=1\n", encoding="utf-8")
        for args in (["add", "."], [*_GIT_ID, "commit", "-qm", "toolchain capability"]):
            subprocess.run(
                ["git", "-C", str(source), *args], check=True, capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    tools = tmp_path / "tools"
    _fake_tools(tools, uv_exit=0, python_exit=0)
    home = tmp_path / "home"
    root = home / "manager"
    root.mkdir(parents=True)
    (root / "config.toml").write_text(
        f'[source]\nrepo = "{source.as_posix()}"\nref = "main"\n', encoding="utf-8",
    )
    config = tmp_path / "toolchain.conf"
    config.write_text(f"schema=1\npython_index_url={INDEX}\n", encoding="ascii")
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("UV_", "PIP_", "NPM_CONFIG_", "COPILOT_EXTENSIONS_TOOLCHAIN_"))
    }
    handoff = tmp_path / "python-arguments"
    env.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "appdata"),
        PROGRAMDATA=str(home / "programdata"), XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CONFIG_DIRS=str(home / "global-config"), WORKTREE_MANAGER_ROOT=str(root),
        BOOTSTRAP_ARGUMENT_HANDOFF=str(handoff),
        PATH=str(tools) + os.pathsep + os.environ.get("PATH", ""),
    )
    bootstrap = Path(__file__).resolve().parents[1] / f"bootstrap.{kind}"
    command = [shell]
    if kind == "ps1":
        command += ["-NoProfile", "-NonInteractive", "-File"]
    result = subprocess.run(
        [*command, str(bootstrap), "--toolchain-config", str(config), "--version"],
        env=env, cwd=home, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if supported:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "--bootstrap-uv" in handoff.read_text(encoding="utf-8")
    else:
        assert result.returncode != 0
        assert "does not support toolchain configuration" in result.stdout + result.stderr
        assert not handoff.exists()


def test_toolchain_config_standard_setup_applies_before_prerequisite_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from worktree_manager import setup_cli

    pip, _, _ = _native_fixture(monkeypatch, tmp_path)
    selected = tmp_path / "toolchain.conf"
    selected.write_text(f"schema=1\npython_index_url={INDEX}\n", encoding="ascii")
    monkeypatch.setenv(tc.CONFIGURATION_ENV, str(selected))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))

    def detect():
        assert os.environ["UV_DEFAULT_INDEX"] == INDEX
        assert os.environ["PIP_INDEX_URL"] == INDEX
        assert INDEX in pip.read_text(encoding="utf-8")
        return []

    monkeypatch.setattr(setup_cli, "detect_baseline", detect)
    monkeypatch.setattr(setup_cli, "core_status", lambda: SimpleNamespace(installed=True))
    monkeypatch.setattr(setup_cli, "install_command", lambda: None)
    monkeypatch.setattr(
        setup_cli, "install_core",
        lambda **_: SimpleNamespace(ran=False, reason="already-installed"),
    )
    assert setup_cli.cmd_setup(["--apply"], banner="fixture") == 0
