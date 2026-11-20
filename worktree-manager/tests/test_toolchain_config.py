"""Adopter data is preflighted before tool execution or native config writes."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import configparser
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
def restore_process_environment() -> Iterator[None]:
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
    for selector in ("UV_CONFIG_FILE", "UV_NO_CONFIG"):
        with pytest.raises(ValueError, match=selector):
            tc.environment({"python_install_mirror": PYTHON_MIRROR}, {selector: "selected"})


def _native_fixture(monkeypatch: pytest.MonkeyPatch, home: Path) -> tuple[Path, Path, Path]:
    pip = home / "pip" / "pip.ini"
    uv = home / "uv" / "uv.toml"
    site = home / "venv" / "pip.ini"
    monkeypatch.setattr(tc, "_native_paths", lambda *_: ([pip, site], uv, pip))
    monkeypatch.setattr(tc, "_system_uv_path", lambda *_a, **_k: None)
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
    pip.write_text("# pip options\n[global] # user settings\ntimeout = 73\n", encoding="utf-8")
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
    assert "# pip options\n[global] # user settings\nindex-url = " in pip.read_text(encoding="utf-8")
    assert b"timeout = 73" in before[pip]
    assert "# uv options\nhttp-timeout = 73" in uv.read_text(encoding="utf-8")
    assert "# npm options\nfetch-retries=3" in npm.read_text(encoding="utf-8")
    assert tc.read_configuration(root / "toolchain.conf") == tc.read_configuration(config)
    assert os.environ[tc.CONFIGURATION_ENV] == str(root / "toolchain.conf")
    tc.apply_configuration(root / "toolchain.conf", home=tmp_path, root=root)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_toolchain_config_preserves_indented_pip_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, indent: str,
) -> None:
    pip, _, _ = _native_fixture(monkeypatch, tmp_path)
    pip.parent.mkdir()
    pip.write_text(f"[global]\n{indent}timeout = 73\n{indent}retries = 3\n", encoding="ascii")
    config = tmp_path / "toolchain.conf"
    config.write_text(f"schema=1\npython_index_url={INDEX}\n", encoding="ascii")
    tc.apply_configuration(config, home=tmp_path)
    parser = configparser.RawConfigParser()
    parser.read(pip, encoding="utf-8")
    assert parser.get("global", "index-url") == INDEX
    assert parser.getint("global", "timeout") == 73
    assert parser.getint("global", "retries") == 3


@pytest.mark.parametrize("shell_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("conflict", [False, True])
@pytest.mark.parametrize("selector", ["PIP_CONFIG_FILE", "npm_config_userconfig", "npm_config_globalconfig"])
def test_toolchain_config_powershell_relative_paths_follow_provider_location(
    tmp_path: Path, shell_name: str, conflict: bool, selector: str,
) -> None:
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip("native PowerShell unavailable")
    home, other = tmp_path / "selected", tmp_path / "other"
    home.mkdir()
    other.mkdir()
    foreign = "https://other.example.invalid/simple/"
    expected = INDEX if selector == "PIP_CONFIG_FILE" else REGISTRY
    for directory, index in ((home, foreign if conflict else expected), (other, expected if conflict else foreign)):
        content = f"[global]\nindex-url={index}\n" if selector == "PIP_CONFIG_FILE" else f"registry={index}\n"
        (directory / "selected.ini").write_text(content, encoding="ascii")
    config = tmp_path / "toolchain.conf"
    field = "python_index_url" if selector == "PIP_CONFIG_FILE" else "npm_registry"
    config.write_text(f"schema=1\n{field}={expected}\n", encoding="ascii")
    bootstrap = Path(__file__).resolve().parents[1] / "bootstrap.ps1"
    def quote(path: Path) -> str:
        return str(path).replace("'", "''")

    program = (
        f"[Environment]::CurrentDirectory='{quote(other)}'; Set-Location -LiteralPath '{quote(home)}'; "
        "function Get-Command { param($Name); if ($Name -like 'uv*') { "
        f"[Console]::Out.WriteLine('FIRST_CONSUMER|' + [Environment]::GetEnvironmentVariable('{selector}')); "
        "throw 'STOP'; }; "
        "throw 'UNEXPECTED_COMMAND' }; "
        f"& '{quote(bootstrap)}' --toolchain-config '{quote(config)}' --version"
    )
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("UV_", "PIP_", "NPM_CONFIG_", "COPILOT_EXTENSIONS_TOOLCHAIN_"))
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "appdata"),
        PROGRAMDATA=str(home / "programdata"), XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CONFIG_DIRS=str(home / "system"), WORKTREE_MANAGER_ROOT=str(home / "manager"),
    )
    env[selector] = "selected.ini"
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", program],
        cwd=other, env=env, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    if conflict:
        assert "FIRST_CONSUMER" not in output
    else:
        assert "FIRST_CONSUMER|" + str(home / "selected.ini") in output
    assert not (home / "manager").exists()


@pytest.mark.parametrize("kind", ["pip", "uv", "npm"])
def test_toolchain_config_preserves_writer_completed_before_lock_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from worktree_manager import config_write_lock as writer

    pip, uv, _ = _native_fixture(monkeypatch, tmp_path)
    target, content = {
        "pip": (pip, "[global]\ntimeout = 73\n"),
        "uv": (uv, "http-timeout = 73\n"),
        "npm": (tmp_path / ".npmrc", "fetch-retries=3\n"),
    }[kind]
    original_lock = writer.config_write_lock
    acquired: list[Path] = []

    @contextmanager
    def competing_writer(path: Path, *, timeout: float = 5.0) -> Iterator[None]:
        if path == target:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        with original_lock(path, timeout=timeout):
            acquired.append(path)
            yield

    monkeypatch.setattr(writer, "config_write_lock", competing_writer)
    config = tmp_path / "toolchain.conf"
    config.write_text(f"schema=1\npython_index_url={INDEX}\nnpm_registry={REGISTRY}\n", encoding="ascii")
    tc.apply_configuration(config, home=tmp_path)
    assert target in acquired
    assert content.splitlines()[-1] in target.read_text(encoding="utf-8")
    assert (REGISTRY if kind == "npm" else INDEX) in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("conflict", ["site", "command", "defaults", "npm", "uv"])
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
    if conflict in {"site", "command", "defaults"}:
        section = "DEFAULT" if conflict == "defaults" else "install"
        target.write_text(f"[{section}]\nindex-url = https://other.example.invalid/simple/\n", encoding="utf-8")
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
@pytest.mark.parametrize("invalid", [
    "", "empty-option", "unknown", "pip", "pip-colon", "uv", "uv-pip", "uv-system", "uv-system-default",
    "uv-explicit", "uv-explicit-false",
    "uv-inline", "uv-dotted", "npm", "environment", "npm-upper-environment", "npm-config-aliases",
    "mirror-only", "mirror-configfile", "mirror-noconfig",
])
def test_toolchain_config_native_entry_precedes_first_prerequisite(
    tmp_path: Path, kind: str, invalid: str,
) -> None:
    shell = shutil.which("pwsh") if kind == "ps1" else (shutil.which("bash") if os.name != "nt" else None)
    if shell is None:
        pytest.skip("native shell unavailable")
    if invalid == "uv-system-default" and os.name == "nt":
        pytest.skip("XDG system defaults are a POSIX native contract")
    if invalid == "npm-config-aliases" and os.name == "nt":
        pytest.skip("Windows environment variable names are case-insensitive")
    # This first-touch control stops at uv discovery. No Python, package manager,
    # Git source or installed Manager is required or allowed to execute.
    bootstrap = Path(__file__).resolve().parents[1] / f"bootstrap.{kind}"
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "toolchain.conf"
    mirror_only = invalid.startswith("mirror-")
    config.write_text(
        f"schema=1\npython_install_mirror={PYTHON_MIRROR}\n"
        + ("" if mirror_only else
           f"python_index_url={INDEX}\nnpm_registry={REGISTRY}\nuv_install_mirror={UV_MIRROR}\n")
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
    if invalid == "uv-system-default":
        candidate = tmp_path / f"bootstrap.{kind}"
        candidate.write_text(
            bootstrap.read_text(encoding="utf-8").replace("/etc/xdg", str(home / "global-config")),
            encoding="utf-8",
        )
        bootstrap = candidate
        env.pop("XDG_CONFIG_DIRS")
    if invalid == "environment":
        env["PIP_INDEX_URL"] = "https://other.example.invalid/simple/"
    elif invalid == "npm-upper-environment":
        env["NPM_CONFIG_REGISTRY"] = "https://other.example.invalid/npm/"
    elif invalid == "npm-config-aliases":
        env["NPM_CONFIG_USERCONFIG"] = str(home / "upper.npmrc")
        env["npm_config_userconfig"] = str(home / "lower.npmrc")
    elif mirror_only:
        target = (
            home / "appdata" / "uv" / "uv.toml" if os.name == "nt"
            else home / ".config" / "uv" / "uv.toml"
        )
        target.parent.mkdir(parents=True)
        target.write_text('[pip]\nextra-index-url = ["https://unrelated.example.invalid/simple/"]\n', encoding="utf-8")
        if invalid == "mirror-configfile":
            env["UV_CONFIG_FILE"] = str(target)
        elif invalid == "mirror-noconfig":
            env["UV_NO_CONFIG"] = "1"
    elif invalid in {"pip", "pip-colon", "uv", "uv-pip", "uv-system", "uv-system-default",
                     "uv-explicit", "uv-explicit-false", "uv-inline", "uv-dotted", "npm"}:
        family = "pip" if invalid.startswith("pip") else "uv" if invalid.startswith("uv") else "npm"
        target = {
            "pip": home / "appdata" / "pip" / "pip.ini" if os.name == "nt" else home / ".config" / "pip" / "pip.conf",
            "uv": home / "appdata" / "uv" / "uv.toml" if os.name == "nt" else home / ".config" / "uv" / "uv.toml",
            "npm": home / ".npmrc",
        }[family]
        if invalid in {"uv-system", "uv-system-default"}:
            target = (
                home / "programdata" / "uv" / "uv.toml" if os.name == "nt"
                else home / "global-config" / "uv" / "uv.toml"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        content = {
            "pip": "[install]\nindex-url=https://other.example.invalid/simple/\n",
            "uv": '[[index]]\nurl = "https://other.example.invalid/simple/"\ndefault = true\n',
            "npm": "registry=https://other.example.invalid/npm/\n",
        }[family]
        if invalid == "pip-colon":
            content = "[install]\nindex-url: https://other.example.invalid/simple/\n"
        elif invalid == "uv-pip":
            content = '[pip]\n"extra-index-url" = ["https://other.example.invalid/simple/"]\n'
        elif invalid == "uv-inline":
            content = 'pip = { extra-index-url = ["https://other.example.invalid/simple/"] }\n'
        elif invalid == "uv-dotted":
            content = 'pip.extra-index-url = ["https://other.example.invalid/simple/"]\n'
        elif invalid.startswith("uv-explicit"):
            explicit = "false" if invalid.endswith("-false") else "true"
            content = f'[[index]]\nname = "selected"\nurl = "{INDEX}"\ndefault = true\nexplicit = {explicit}\n'
        target.write_text(content, encoding="utf-8")
    if kind == "ps1":
        escaped = str(bootstrap).replace("'", "''")
        selected = "" if invalid == "empty-option" else str(config).replace("'", "''")
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
            "#!/bin/sh\nprintf 'FIRST_CONSUMER|%s|%s|%s|%s|%s\\n' \"$UV_DEFAULT_INDEX\" \"$PIP_INDEX_URL\" "
            "\"$npm_config_registry\" \"$UV_PYTHON_INSTALL_MIRROR\" "
            "\"$UV_INSTALLER_GITHUB_BASE_URL\" >&2\nexit 91\n", encoding="ascii",
        )
        curl.chmod(0o755)
        env["PATH"] = str(tools) + os.pathsep + os.pathsep.join(
            entry for entry in os.get_exec_path()
            if not any((Path(entry) / name).is_file() for name in ("uv", "uv.exe"))
        )
        command = [shell, str(bootstrap), "--toolchain-config",
                   "" if invalid == "empty-option" else str(config), "--version"]
    result = subprocess.run(
        command, env=env, cwd=home, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    if invalid not in {"", "mirror-only", "uv-explicit-false"}:
        assert INDEX not in output
        assert PYTHON_MIRROR not in output
        assert "FIRST_CONSUMER" not in output
    elif mirror_only:
        assert f"|||{PYTHON_MIRROR}|" in output
    else:
        assert f"{INDEX}|{INDEX}|{REGISTRY}|{PYTHON_MIRROR}|{UV_MIRROR}" in output
    assert not (home / "manager").exists()


@pytest.mark.parametrize("tool", ["pip", "pip-explicit", "uv", "uv-python", "npm"])
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
    user_pip = (
        tmp_path / "appdata" / "pip" / "pip.ini" if os.name == "nt"
        else tmp_path / ".config" / "pip" / "pip.conf"
    )
    if tool == "pip-explicit":
        explicit = tmp_path / "selected-pip.ini"
        explicit.write_text("[global]\ntimeout = 73\n", encoding="utf-8")
        user_pip.parent.mkdir(parents=True)
        user_pip.write_text("[global]\nindex-url=https://ignored.example.invalid/simple/\n", encoding="utf-8")
        monkeypatch.setenv("PIP_CONFIG_FILE", str(explicit))
    tc.apply_configuration(config, home=tmp_path)
    # A new terminal has no bootstrap-injected index/registry variables.
    injected = {"UV_DEFAULT_INDEX", "PIP_INDEX_URL", "NPM_CONFIG_REGISTRY", "UV_PYTHON_INSTALL_MIRROR"}
    env = {key: value for key, value in os.environ.items() if key.upper() not in injected}
    env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
    env["npm_config_cache"] = str(tmp_path / "npm-cache")
    env["npm_config_update_notifier"] = "false"
    base_python = str(getattr(sys, "_base_executable", sys.executable))
    expected = INDEX
    if tool.startswith("pip"):
        command = [base_python, "-m", "pip", "config", "list"]
        # Windows pip resolves known folders through the OS, not redirected
        # APPDATA. Select the fixture file so this check never reads user state.
        if tool == "pip":
            env["PIP_CONFIG_FILE"] = str(user_pip)
        else:
            assert tc._native_paths(tmp_path, env, os.name == "nt")[2] == explicit
            assert "ignored.example.invalid" in user_pip.read_text(encoding="utf-8")
            assert "timeout = 73" in explicit.read_text(encoding="utf-8")
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
    if tool.startswith("pip") and "No module named pip" in result.stderr:
        pytest.skip("base interpreter has no pip")
    if tool.startswith("uv"):
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout + result.stderr
    assert "https://pypi.org/simple" not in result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["ps1", "sh"])
@pytest.mark.parametrize("supported", [False, None, True])
@pytest.mark.parametrize("configuration_only", [False, True])
def test_toolchain_config_native_source_skew_is_explicit(
    tmp_path: Path, kind: str, supported: bool | None, configuration_only: bool,
) -> None:
    from test_bootstrap_ps1 import _GIT_ID, _fake_tools, _source_repo

    shell = shutil.which("pwsh") if kind == "ps1" else (shutil.which("bash") if os.name != "nt" else None)
    if shell is None or shutil.which("git") is None:
        pytest.skip("native shell and Git required")
    source = _source_repo(tmp_path)
    entry = source / "worktree-manager" / "scripts" / "runtime_install.py"
    if supported is not False:
        entry.write_text(
            entry.read_text(encoding="utf-8")
            + "# copilot-extensions:toolchain-config=1\n"
            + ("# copilot-extensions:toolchain-context=1\n" if supported or configuration_only else "")
            + ("# copilot-extensions:toolchain-bootstrap-only=1\n" if supported and configuration_only else ""),
            encoding="utf-8",
        )
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
    staging = root / "staging" / "unrelated-installation"
    if configuration_only:
        staging.parent.mkdir()
        staging.write_bytes(b"retain unrelated staging\n")
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
    arguments = ["setup", "--toolchain-only", "--apply"] if configuration_only else ["--version"]
    if configuration_only and supported:
        metadata = subprocess.run(
            [*command, str(bootstrap), "--describe-toolchain-config"],
            env=env, cwd=home, capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        import json

        assert metadata.returncode == 0, metadata.stdout + metadata.stderr
        assert "bootstrap-toolchain-only" in json.loads(metadata.stdout)["capabilities"]
    result = subprocess.run(
        [*command, str(bootstrap), "--toolchain-config", str(config), *arguments],
        env=env, cwd=home, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if supported:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "--bootstrap-uv" in handoff.read_text(encoding="utf-8")
        if configuration_only:
            assert " ".join(handoff.read_text(encoding="utf-8").split()).endswith(
                " -- setup --toolchain-only --apply"
            )
            assert list((root / "bootstrap-acquisition").iterdir()) == []
    else:
        assert result.returncode != 0
        assert "does not support toolchain configuration" in result.stdout + result.stderr
        assert not handoff.exists()
    if configuration_only:
        assert staging.read_bytes() == b"retain unrelated staging\n"


def test_toolchain_config_standard_setup_applies_before_prerequisite_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from worktree_manager import setup_cli
    from worktree_manager.prereqs import PrereqStatus

    pip, _, _ = _native_fixture(monkeypatch, tmp_path)
    selected = tmp_path / "toolchain.conf"
    selected.write_text(f"schema=1\npython_index_url={INDEX}\n", encoding="ascii")
    monkeypatch.setenv(tc.CONFIGURATION_ENV, str(selected))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))

    def detect() -> list[PrereqStatus]:
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


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("selected", [False, True])
def test_toolchain_config_only_uses_real_owner_without_provisioning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply: bool, selected: bool,
) -> None:
    from worktree_manager import repository_bootstrap, setup_cli

    pip, uv, _ = _native_fixture(monkeypatch, tmp_path)
    root = tmp_path / "manager"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.chdir(tmp_path)
    if selected:
        configuration = tmp_path / "selected.conf"
        configuration.write_text(f"schema=1\npython_index_url={INDEX}\n", encoding="ascii")
        monkeypatch.setenv(tc.CONFIGURATION_ENV, str(configuration))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("configuration-only setup reached an installation/repository owner")

    for name in ("detect_baseline", "core_status", "provision_plan", "provision_apply",
                 "install_command", "install_core"):
        monkeypatch.setattr(setup_cli, name, forbidden)
    monkeypatch.setattr(repository_bootstrap, "run", forbidden)
    arguments = ["--toolchain-only", *(["--apply"] if apply else [])]
    assert setup_cli.cmd_setup(arguments, banner="fixture") == 0
    if apply and selected:
        assert INDEX in pip.read_text(encoding="utf-8")
        assert INDEX in uv.read_text(encoding="utf-8")
        assert tc.read_configuration(root / "toolchain.conf") == {"python_index_url": INDEX}
        before = {path: path.read_bytes() for path in (pip, uv, root / "toolchain.conf")}
        assert setup_cli.cmd_setup(arguments, banner="fixture") == 0
        assert {path: path.read_bytes() for path in before} == before
    else:
        assert not pip.exists()
        assert not uv.exists()
        assert not root.exists()


@pytest.mark.parametrize(("register", "arguments"), [
    (True, ["https://example.invalid/repository"]),
    (False, ["--repository", "https://example.invalid/repository"]),
    (False, ["--repository-target", str(Path.cwd().anchor)]),
    (False, ["--repository-commit", "a" * 40]),
    (False, ["--trust-repository-setup"]),
    (False, ["--context", "example"]),
    (False, ["--toolchain-onl"]),
])
def test_toolchain_config_only_conflicting_intent_refuses_before_configuration(
    monkeypatch: pytest.MonkeyPatch, register: bool, arguments: list[str],
) -> None:
    from worktree_manager import setup_cli

    def forbidden(**_kwargs: object) -> None:
        pytest.fail("invalid intent reached configuration")

    monkeypatch.setattr(tc, "configure_selected", forbidden)
    assert setup_cli.cmd_setup(
        ["--toolchain-only", "--apply", *arguments], banner="fixture", register=register,
    ) == 2


def test_toolchain_config_only_help_is_safe_with_invalid_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from worktree_manager import setup_cli

    configuration = tmp_path / "invalid.conf"
    configuration.write_text("invalid data", encoding="ascii")
    monkeypatch.setenv(tc.CONFIGURATION_ENV, str(configuration))

    def forbidden(**_kwargs: object) -> None:
        pytest.fail("help reached configuration")

    monkeypatch.setattr(tc, "configure_selected", forbidden)
    assert setup_cli.cmd_setup(["--help"], banner="fixture") == 0
    assert "toolchain-config-only=1" in capsys.readouterr().out.splitlines()
    assert configuration.read_text(encoding="ascii") == "invalid data"


@pytest.mark.parametrize("entry", ["installed", "source"])
def test_toolchain_config_only_actual_cli_does_not_need_bootstrap_or_core(
    tmp_path: Path, entry: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    root = home / "manager"
    root.mkdir()
    sentinel = root / "knowledge-binding.json"
    sentinel.write_text('{"retained":true}\n', encoding="ascii")
    selected = tmp_path / "selected.conf"
    selected.write_text(f"schema=1\nnpm_registry={REGISTRY}\n", encoding="ascii")
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith((
            "UV_", "PIP_", "NPM_CONFIG_", "COPILOT_EXTENSIONS_TOOLCHAIN_",
            "AGENT_WORKTREES_ENGINE_", "WORKTREE_MANAGER_ENGINE_",
        ))
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "appdata"),
        PROGRAMDATA=str(home / "programdata"), XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CONFIG_DIRS=str(home / "system"), WORKTREE_MANAGER_ROOT=str(root),
        COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG=str(selected), PATH="",
    )

    def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
        command = (
            [sys.executable, "-m", "worktree_manager", "setup"] if entry == "installed"
            else [
                sys.executable, "-I", "-S", "-B",
                str(Path(__file__).resolve().parents[1] / "scripts" / "runtime_install.py"),
                "--root", str(root), "--", "setup",
            ]
        )
        return subprocess.run(
            [*command, *arguments],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    result = invoke("--toolchain-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == before
    result = invoke("--toolchain-only", "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".npmrc").read_text(encoding="utf-8").strip() == f"registry={REGISTRY}"
    assert tc.read_configuration(root / "toolchain.conf") == tc.read_configuration(selected)
    assert sentinel.read_bytes() == before[sentinel]
    assert not (root / "versions").exists()
    assert not (root / "current-version").exists()
    assert not (home / ".local" / "bin").exists()
    first_apply = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    result = invoke("--toolchain-only", "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == first_apply
    result = invoke("--toolchain-only", "--apply", "--repository", "https://example.invalid/repository")
    assert result.returncode == 2, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == first_apply
    (home / ".npmrc").write_text("registry=https://other.example.invalid/npm/\n", encoding="ascii")
    conflicting = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    result = invoke("--toolchain-only", "--apply")
    assert result.returncode == 2, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == conflicting
    selected.write_text("invalid data", encoding="ascii")
    result = invoke(*(["--toolchain-only"] if entry == "source" else []), "--help")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "toolchain-config-only=1" in result.stdout.splitlines()


@pytest.mark.parametrize("failure", ["conflict", "parse"])
def test_toolchain_config_setup_reports_safe_cause_before_prerequisites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str,
) -> None:
    from worktree_manager import setup_cli

    pip, _, _ = _native_fixture(monkeypatch, tmp_path)
    selected = tmp_path / "toolchain.conf"
    selected.write_text(f"schema=1\npython_index_url={INDEX}\n", encoding="ascii")
    monkeypatch.setenv(tc.CONFIGURATION_ENV, str(selected))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    private_value = "native-content-must-not-be-printed"
    if failure == "conflict":
        monkeypatch.setenv("PIP_INDEX_URL", private_value)
    else:
        pip.parent.mkdir()
        pip.write_text(f"private-option={private_value}\n", encoding="ascii")
    monkeypatch.setattr(setup_cli, "detect_baseline", lambda: pytest.fail("prerequisites reached"))
    assert setup_cli.cmd_setup(["--apply"], banner="fixture") == 2
    output = capsys.readouterr().out
    if failure == "conflict":
        assert "PIP_INDEX_URL conflicts with toolchain configuration" in output
    else:
        assert "MissingSectionHeaderError" in output
    assert private_value not in output


@pytest.mark.parametrize(("code", "expected"), [(2, 2), (None, 0), ("parse-error", 1)])
def test_toolchain_config_setup_preserves_parser_exit_status(
    monkeypatch: pytest.MonkeyPatch, code: int | str | None, expected: int,
) -> None:
    from worktree_manager import repository_bootstrap, setup_cli

    def exit_parser(*_args: object, **_kwargs: object) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(repository_bootstrap, "parse", exit_parser)
    assert setup_cli.cmd_setup([], banner="fixture") == expected


def test_toolchain_config_darwin_pip_paths_follow_native_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(tc, "sys", SimpleNamespace(platform="darwin"))
    paths, _, target = tc._native_paths(tmp_path, {}, False)
    assert target == tmp_path / ".config" / "pip" / "pip.conf"
    assert Path("/Library/Application Support/pip/pip.conf") in paths
    native = tmp_path / "Library" / "Application Support" / "pip"
    native.mkdir(parents=True)
    assert tc._native_paths(tmp_path, {}, False)[2] == native / "pip.conf"


@pytest.mark.parametrize("url", [
    "https://user:password@example.invalid/simple/",
    "https://example.invalid/simple/?token=synthetic",
])
def test_toolchain_config_request_metadata_refuses_inline_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str,
) -> None:
    from worktree_manager import update_driver_cli

    monkeypatch.setenv(tc.CONFIGURATION_ENV, str(tmp_path / "toolchain.conf"))
    monkeypatch.setenv("PIP_INDEX_URL", url)
    legacy = update_driver_cli.request_context(capture_engine=False)
    assert tc.CONFIGURATION_ENV not in legacy and "PIP_INDEX_URL" not in legacy
    with pytest.raises(ValueError, match="without credentials"):
        update_driver_cli.request_context(capture_engine=False, capture_toolchain=True)


@pytest.mark.parametrize("context", [
    {"COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST": "2", tc.CONFIGURATION_ENV: "selected"},
    {"COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST": "1"},
])
def test_toolchain_config_request_receiver_refuses_unsupported_context(
    context: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import update_driver_cli

    original = dict(context)
    with pytest.raises(ValueError):
        tc._routing_environment(context)
    assert context == original
    monkeypatch.delenv(tc.CONFIGURATION_ENV, raising=False)
    for key, value in context.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        update_driver_cli.request_context(capture_engine=False, capture_toolchain=True)


@pytest.mark.parametrize("selector", [
    "user-pip", "system-pip", "system-index", "system-mirror", "system-matching", "mirror-only",
    "user-explicit", "system-explicit",
])
def test_toolchain_config_checks_merged_uv_sources_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str,
) -> None:
    pip, uv, _ = _native_fixture(monkeypatch, tmp_path)
    system = tmp_path / "system-uv.toml"
    monkeypatch.setattr(tc, "_system_uv_path", lambda *_a, **_k: system)
    content = '[pip]\nextra-index-url = ["https://other.example.invalid/simple/"]\n'
    if selector == "system-index":
        content = '[[index]]\nurl = "https://other.example.invalid/simple/"\ndefault = true\n'
    elif selector == "system-mirror":
        content = 'python-install-mirror = "https://other.example.invalid/python/"\n'
    elif selector == "system-matching":
        content = f'[[index]]\nurl = "{INDEX}"\ndefault = true\nexplicit = false\n'
    elif selector in {"user-explicit", "system-explicit"}:
        content = f'[[index]]\nname = "selected"\nurl = "{INDEX}"\ndefault = true\nexplicit = true\n'
    system.write_text("" if selector in {"user-pip", "user-explicit", "mirror-only"} else content, encoding="utf-8")
    if selector in {"user-pip", "user-explicit", "mirror-only"}:
        uv.parent.mkdir()
        uv.write_text(content, encoding="utf-8")
    config = tmp_path / "toolchain.conf"
    config.write_text(
        f"schema=1\npython_install_mirror={PYTHON_MIRROR}\n"
        + ("" if selector == "mirror-only" else f"python_index_url={INDEX}\n"), encoding="ascii",
    )
    before = {path: path.read_bytes() if path.exists() else None for path in (pip, uv, system)}
    if selector == "mirror-only":
        tc.apply_configuration(config, home=tmp_path)
        assert "extra-index-url" in uv.read_text(encoding="utf-8")
        assert not pip.exists()
    elif selector == "system-matching":
        tc.apply_configuration(config, home=tmp_path)
        assert "[[index]]" not in uv.read_text(encoding="utf-8")
        assert INDEX in pip.read_text(encoding="utf-8")
        assert system.read_bytes() == before[system]
    else:
        with pytest.raises(ValueError):
            tc.apply_configuration(config, home=tmp_path)
        assert {path: path.read_bytes() if path.exists() else None for path in before} == before


@pytest.mark.parametrize("directories", [None, "", "/first:/second"])
def test_toolchain_config_system_uv_uses_default_xdg_and_first_discovered_directory(
    monkeypatch: pytest.MonkeyPatch, directories: str | None,
) -> None:
    available = {Path("/etc/xdg/uv/uv.toml"), Path("/first/uv/uv.toml"),
                 Path("/second/uv/uv.toml"), Path("/etc/uv/uv.toml")}
    checked: list[Path] = []

    def exists(path: Path) -> bool:
        checked.append(path)
        return path in available

    monkeypatch.setattr(Path, "exists", exists)
    env = {} if directories is None else {"XDG_CONFIG_DIRS": directories}
    expected = Path("/first/uv/uv.toml" if directories else "/etc/xdg/uv/uv.toml")
    assert tc._system_uv_path(env, windows=False) == expected
    assert checked == [expected]
    available.remove(expected)
    checked.clear()
    assert tc._system_uv_path(env, windows=False) == Path(
        "/second/uv/uv.toml" if directories else "/etc/uv/uv.toml",
    )
