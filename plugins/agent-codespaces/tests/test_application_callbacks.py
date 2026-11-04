"""Contained app consumer checks; no installed-host or live venue effects."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import sysconfig
import time
import types
import venv
from pathlib import Path

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_codespaces import __main__ as cli, config, owner_cli
from agent_codespaces.config import CodespacesConfig

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
SOURCE = "1.2.3-dev4+source." + "a" * 40 + "." + "b" * 16 + ".profile." + "c" * 16


def test_disabled_start_never_sets_up_or_launches_owner(monkeypatch):
    resolved = CodespacesConfig()
    resolved.connection_owner.enabled = False

    def read(*, include_cwd):
        assert include_cwd is False
        return resolved

    monkeypatch.setattr(config, "load_merged_config", read)
    monkeypatch.setattr(owner_cli, "cmd_source_setup", lambda *_: pytest.fail("unexpected setup"))
    monkeypatch.setattr("agent_codespaces.connection_owner.ensure_owner_running",
                        lambda *_: pytest.fail("unexpected launch"))
    assert cli.main(["__source-start"]) == 0


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("state", [
    "ready", "failed-ensure", "unselected", "foreign-package",
    "missing-proof", "refused-selection", "legacy-binding",
])
def test_start_admits_only_selected_installed_generation(
    tmp_path, monkeypatch, capsys, nested, state,
):
    from importlib import metadata

    resolved = CodespacesConfig()
    resolved.connection_owner.enabled = True
    monkeypatch.setattr(config, "load_merged_config", lambda **_: resolved)
    root = tmp_path / "runtime"
    slot = root / "versions" / SOURCE
    slot.mkdir(parents=True)
    prefix = slot / ".venv" if nested else slot
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(owner_cli, "__file__", str(prefix / "lib" / "owner_cli.py"))
    monkeypatch.setattr(metadata, "version", lambda _: (
        "9.0.0" if state == "foreign-package" else "1.2.3.dev4"
    ))
    (root / "current-version").write_text(
        "different\n" if state == "unselected" else SOURCE + "\n", encoding="utf-8-sig",
    )
    calls = []
    from agent_codespaces import application_provider, connection_owner, source_build

    monkeypatch.setattr(application_provider, "register_installed_application",
                        lambda: pytest.fail("startup must not register providers"))

    selection = types.SimpleNamespace(
        root=root, identity="qualified-completion",
        selected_identity=lambda: None if state == "refused-selection" else "qualified-completion",
    )
    monkeypatch.setattr(source_build, "capture_owner_runtime", lambda actual: (
        None if state == "missing-proof" else selection
    ))
    monkeypatch.setattr(connection_owner, "_PROCESS_SELECTION", (
        None if state == "legacy-binding" else selection
    ))
    monkeypatch.setattr("agent_codespaces.connection_owner.ensure_owner_running",
                        lambda actual: calls.append(actual) or state == "ready")
    if state in {
        "unselected", "foreign-package", "missing-proof", "refused-selection", "legacy-binding",
    }:
        assert cli.main(["__source-start"]) == 1
        message = capsys.readouterr().err
        expected = (
            "not selected" if state == "unselected"
            else "generation differ" if state == "foreign-package"
            else "attribution"
        )
        assert expected in message
        assert not calls
    else:
        assert cli.main(["__source-start"]) == (0 if state == "ready" else 1)
        assert calls == [resolved]


def test_stage_only_preserves_provider_selectors_and_live_records(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "_codespaces_application_install",
        REPO / "libs" / "installer-engine" / "runtime_application_install.py",
    )
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    slot = tmp_path / "runtime" / "versions" / SOURCE
    prefix = slot / ".venv"
    prefix.mkdir(parents=True)
    (prefix / "pyvenv.cfg").write_text("fixture\n")
    protected = {}
    for relative in (
        "runtime/current-version", "runtime/last-known-good",
        "runtime/connection-owner.holds.json", "runtime/connection-owner.local-forwards.json",
        "bridge/providers.d/agent-codespaces.json", "bridge/config.yaml",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"incumbent\n")
        protected[path] = (path.read_bytes(), path.stat().st_mtime_ns)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(installer, "_run", lambda *_: pytest.fail("stage invoked callback"))
    monkeypatch.setattr(installer, "_qualified", lambda *_: pytest.fail("stage published"))
    assert installer.install(slot, stage=True) == {"state": "staged", "slot": str(slot)}
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in protected} == protected


@pytest.fixture(scope="module")
def installed_application(tmp_path_factory):
    root = tmp_path_factory.mktemp("copied-app")
    project = root / "source"
    project.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copyfile(PLUGIN / name, project / name)
    shutil.copytree(PLUGIN / "src", project / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    wheels = root / "wheels"
    wheels.mkdir()
    build = run_caller_owned([
        sys.executable, "-I", "-c",
        "from setuptools.build_meta import build_wheel; "
        "import sys; build_wheel(sys.argv[1])", str(wheels),
    ], cwd=project, timeout=60, max_stdout_bytes=1 << 20, max_stderr_bytes=1 << 20)
    assert build.returncode == 0, build.stdout + build.stderr
    wheel, = wheels.glob("*.whl")
    copied = root / "copied" / wheel.name
    copied.parent.mkdir()
    shutil.copyfile(wheel, copied)
    prefix = root / "slot" / ".venv"
    venv.EnvBuilder(with_pip=False).create(prefix)
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv is not None, "canonical test runner requires UV"
    assert copied.read_bytes() == wheel.read_bytes()
    # Closed local artifact fixture, as in test_runtime_application_binstubs;
    # third-party dependencies come only from the runner's seasoned closure.
    result = run_caller_owned([
        uv, "pip", "install", "--python", str(python), "--offline", "--no-deps",
        "--no-index", str(copied),
    ], cwd=root, timeout=60, max_stdout_bytes=1 << 20, max_stderr_bytes=1 << 20)
    assert result.returncode == 0, result.stdout + result.stderr
    # Reuse the runner's admitted dependency closure, not its editable app.
    site = prefix / ("Lib/site-packages" if os.name == "nt"
                     else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    (site / "fixture-dependencies.pth").write_text(
        f"import site; site.addsitedir({sysconfig.get_path('purelib')!r})\n", encoding="utf-8",
    )
    shutil.rmtree(project)
    shutil.rmtree(wheels)
    yield root, prefix, python


@pytest.mark.parametrize("bridge_first", [False, True])
def test_copied_wheel_installed_setup_resources_and_discovery(
    tmp_path, monkeypatch, installed_application, bridge_first, record_property,
):
    root, prefix, python = installed_application
    home = tmp_path / "home"
    home.mkdir()
    bridge = tmp_path / "bridge-config"
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "AGENT_HOME": str(home), "AGENT_BRIDGE_CONFIG_DIR": str(bridge),
        "AGENT_BRIDGE_INSTALL_DIR": str(tmp_path / "other-code"),
        "AGENT_CODESPACES_HOME": str(tmp_path / "codespaces-state"),
    }
    env.pop("PYTHONPATH", None)
    env.pop("AGENT_BRIDGE_PROVIDERS_DIR", None)
    global_command = home / ".local" / "bin" / (
        "agent-codespaces.cmd" if os.name == "nt" else "agent-codespaces"
    )
    if bridge_first:
        bridge.mkdir()
        (bridge / "config.yaml").write_bytes(b"existing bridge configuration\n")
    preserved = {}
    for name in ("connection-owner.holds.json", "connection-owner.local-forwards.json"):
        path = Path(env["AGENT_CODESPACES_HOME"]) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"reserved fixture state\n")
        preserved[path] = (path.read_bytes(), path.stat().st_mtime_ns)
    resources = run_caller_owned([
        str(python), "-I", "-c",
        "from pathlib import Path; import sys, agent_codespaces; "
        "from agent_codespaces.codespace_assets import asset_text; "
        "assert Path(agent_codespaces.__file__).is_relative_to(Path(sys.prefix)); "
        "assert all(asset_text(name) for name in "
        "('ado-auth-helper-relay','ado-auth-helper-wrapper','auth-error-policy.instructions.md'))",
    ], cwd=root, env=env, timeout=30, max_stdout_bytes=1 << 20, max_stderr_bytes=1 << 20)
    assert resources.returncode == 0, resources.stdout + resources.stderr
    console = prefix / ("Scripts/agent-codespaces.exe" if os.name == "nt"
                        else "bin/agent-codespaces")
    declaration = bridge / "providers.d" / "agent-codespaces.standalone.json"
    modified = None
    for label in ("first", "repeat"):
        started = time.monotonic()
        result = run_caller_owned(
            [str(console), "__source-setup"], cwd=root, env=env, timeout=30,
            max_stdout_bytes=1 << 20, max_stderr_bytes=1 << 20,
        )
        elapsed = time.monotonic() - started
        record_property(f"installed_setup_{label}_seconds", elapsed)
        print(f"codespaces installed setup {label}: {elapsed:.3f}s; bridge_first={bridge_first}")
        assert result.returncode == 0, result.stdout + result.stderr
        if modified is not None:
            assert declaration.stat().st_mtime_ns == modified
        modified = declaration.stat().st_mtime_ns
    manifest = json.loads(declaration.read_text())
    assert manifest["command"] == [str(global_command)]
    assert not global_command.exists()
    assert not (tmp_path / "other-code").exists()
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in preserved} == preserved
    if bridge_first:
        assert (bridge / "config.yaml").read_bytes() == b"existing bridge configuration\n"
    monkeypatch.syspath_prepend(str(REPO / "plugins" / "agent-bridge" / "src"))
    from agent_bridge import provider_sources

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(bridge))
    monkeypatch.delenv("AGENT_BRIDGE_PROVIDERS_DIR", raising=False)
    monkeypatch.setattr(provider_sources, "resolve_active_plugins",
                        lambda: pytest.fail("plain application must not require plugin activation"))
    before = provider_sources.scan_provider_registry()
    assert "codespace" not in before.manifests
    assert any(f.reason == "missing-target" for f in before.findings)
    global_command.parent.mkdir(parents=True)
    global_command.write_text("fixture global front door\n")
    global_command.chmod(0o700)
    after = provider_sources.scan_provider_registry()
    assert after.manifests["codespace"].command == (str(global_command),)
    assert any(f.reason == "legacy-unattributed" for f in after.findings)
