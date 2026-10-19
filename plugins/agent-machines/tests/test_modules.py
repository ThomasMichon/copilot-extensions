from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_machines import modules
from agent_machines.manifest import ManifestError, load_package

from ._helpers import write_package


def _mod_package(tmp_path: Path, *, name="acme/mods", plat="linux", command=None,
                 dry_run_args=None, gate=None, mod_gate=None):
    module = {"name": "probe", plat: {"command": command or [sys.executable, "-c", "pass"]}}
    if dry_run_args is not None:
        module[plat]["dry_run_args"] = dry_run_args
    if mod_gate is not None:
        module["gate"] = mod_gate
    data = {
        "schema_version": 1,
        "package": name,
        "gate": gate or ["*"],
        "manage": {},
        "modules": [module],
    }
    path = write_package(tmp_path / "acme", "mods.yaml", data)
    return load_package(path, source_repo="acme")


def test_parse_modules(tmp_path):
    pkg = _mod_package(tmp_path)
    assert pkg.modules[0]["name"] == "probe"
    assert pkg.repo_root() == (tmp_path / "acme").resolve()


@pytest.mark.parametrize("schema_version", [1, 2, 3, 4])
@pytest.mark.parametrize("flag", [None, "true", "false", 0, 1, 1.0, [], {}])
def test_module_maintenance_safe_rejects_nonboolean(tmp_path, schema_version, flag):
    data = {
        "schema_version": schema_version, "package": "example/modules", "manage": {},
        "modules": [{"name": "probe", "maintenance_safe": flag}],
    }
    with pytest.raises(ManifestError, match="maintenance_safe must be a boolean"):
        load_package(write_package(tmp_path / "example", "modules.yaml", data))


@pytest.mark.parametrize("flag", [False, True])
def test_module_maintenance_safe_accepts_boolean(tmp_path, flag):
    data = {
        "schema_version": 4, "package": "example/modules", "manage": {},
        "modules": [{"name": "probe", "maintenance_safe": flag}],
    }
    pkg = load_package(write_package(tmp_path / "example", "modules.yaml", data))
    assert pkg.modules[0]["maintenance_safe"] is flag


@pytest.mark.parametrize("plat", ["windows", "linux", "wsl"])
def test_maintenance_safe_preserves_module_gates(tmp_path, monkeypatch, plat):
    pkg = _mod_package(tmp_path, plat="linux")
    pkg.modules[0]["maintenance_safe"] = True
    called = []
    monkeypatch.setattr(
        modules, "run_module",
        lambda *args: called.append(args) or modules.ModuleResult(
            "probe", "acme", ran=True, dry_run=False, returncode=0,
        ),
    )
    results = modules.run_modules(
        [pkg], "box-1", plat, dry_run=False, maintenance_safe=True,
    )
    assert len(results) == len(called) == (0 if plat == "windows" else 1)
    called.clear()
    pkg.modules[0]["gate"] = ["other"]
    assert modules.run_modules(
        [pkg], "box-1", plat, dry_run=False, maintenance_safe=True,
    ) == []
    assert called == []


def test_module_without_name_rejected(tmp_path):
    data = {"schema_version": 1, "package": "a/x", "manage": {}, "modules": [{"linux": {}}]}
    path = write_package(tmp_path / "a", "m.yaml", data)
    with pytest.raises(ManifestError):
        load_package(path)


def test_resolve_modules_platform_and_gate(tmp_path):
    pkg = _mod_package(tmp_path, plat="linux")
    assert len(modules.resolve_modules([pkg], "box-1", "linux")) == 1
    # No windows block -> not applicable on windows.
    assert modules.resolve_modules([pkg], "box-1", "windows") == []
    # Module gate excludes the machine.
    pkg2 = _mod_package(tmp_path, mod_gate=["other"])
    assert modules.resolve_modules([pkg2], "box-1", "linux") == []


def test_run_module_executes(tmp_path):
    pkg = _mod_package(tmp_path, plat="linux", command=[sys.executable, "-c", "print('ok')"])
    result = modules.run_module(pkg, pkg.modules[0], "linux", dry_run=False)
    assert result.ran is True
    assert result.returncode == 0
    assert result.ok


def test_dry_run_skips_module_without_dry_args(tmp_path):
    pkg = _mod_package(tmp_path, plat="linux")
    result = modules.run_module(pkg, pkg.modules[0], "linux", dry_run=True)
    assert result.ran is False
    assert "dry_run_args" in (result.skipped_reason or "")
    assert result.ok  # a safe skip is not a failure


def test_dry_run_runs_with_dry_args(tmp_path):
    code = "import sys; sys.exit(0 if '--preview' in sys.argv else 1)"
    pkg = _mod_package(tmp_path, plat="linux", command=[sys.executable, "-c", code],
                       dry_run_args=["--preview"])
    result = modules.run_module(pkg, pkg.modules[0], "linux", dry_run=True)
    assert result.ran is True
    assert result.returncode == 0
    assert "--preview" in result.command


def test_missing_interpreter_is_skipped(tmp_path):
    pkg = _mod_package(tmp_path, plat="linux", command=["definitely-not-a-real-binary-xyz"])
    result = modules.run_module(pkg, pkg.modules[0], "linux", dry_run=False)
    assert result.ran is False
    assert "not found" in (result.skipped_reason or "")


def test_nonzero_returncode_is_failure(tmp_path):
    code = "import sys; sys.exit(3)"
    pkg = _mod_package(tmp_path, plat="linux", command=[sys.executable, "-c", code])
    result = modules.run_module(pkg, pkg.modules[0], "linux", dry_run=False)
    assert result.ran is True
    assert result.returncode == 3
    assert not result.ok


def test_non_utf8_module_output_does_not_crash(tmp_path):
    # A module can emit non-UTF-8 bytes on stdout (wsl.exe UTF-16 banners,
    # em-dashes in a cp1252 console). The runner must decode leniently rather
    # than let a reader thread die with UnicodeDecodeError. See dotfiles #802.
    code = (
        "import sys; "
        "sys.stdout.buffer.write(b'ok \\xc4\\x80 tail'); "
        "sys.stdout.buffer.flush()"
    )
    pkg = _mod_package(tmp_path, plat="linux", command=[sys.executable, "-c", code])
    result = modules.run_module(pkg, pkg.modules[0], "linux", dry_run=False)
    assert result.ran is True
    assert result.returncode == 0
    assert result.ok
    assert "ok" in (result.stdout_tail or "")


def _provider_package(tmp_path: Path):
    data = {
        "schema_version": 3,
        "package": "acme/provider",
        "gate": ["*"],
        "manage": {},
        "modules": [
            {
                "name": "ssh-host",
                "invocation": {
                    "plugin": "agent-ssh@copilot-extensions",
                    "command": "agent-ssh",
                    "platforms": ["windows"],
                    "arguments": ["restore-host", "--transport", "dtssh"],
                    "dry_run_arguments": ["--dry-run"],
                    "apply_arguments": ["--apply"],
                },
            }
        ],
    }
    return load_package(
        write_package(tmp_path / "acme", "provider.yaml", data),
        source_repo="acme",
    )


@pytest.mark.parametrize("maintenance_safe", [False, True])
def test_payload_provider_invocation_uses_attributed_shim(
    tmp_path, monkeypatch, maintenance_safe
):
    pkg = _provider_package(tmp_path)
    pkg.modules[0]["maintenance_safe"] = True
    payload = tmp_path / "payload"
    shim = payload / "bin" / "agent-ssh.ps1"
    shim.parent.mkdir(parents=True)
    shim.write_text("", encoding="utf-8")
    (payload / "payload-invocation.json").write_text(
        '{"version": 1, "command": "agent-ssh"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        modules,
        "_active_plugins",
        lambda: {
            "agent-ssh@copilot-extensions": SimpleNamespace(root=payload)
        },
    )
    monkeypatch.setattr(modules.shutil, "which", lambda _name: "pwsh")
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(modules.subprocess, "run", run)

    environment_before = dict(modules.os.environ)
    result = modules.run_modules(
        [pkg], "box-1", "windows", dry_run=True, maintenance_safe=maintenance_safe,
    )[0]

    assert result.ok
    assert result.command[-1] == "--dry-run"
    assert captured["kwargs"]["env"]["COPILOT_PLUGIN_ROOT"] == str(payload)
    assert str(shim) in captured["command"]
    assert dict(modules.os.environ) == environment_before


def test_payload_provider_honors_commands_output_dir_and_cmd_shim(
    tmp_path, monkeypatch
):
    pkg = _provider_package(tmp_path)
    payload = tmp_path / "payload"
    shim = payload / "bin" / "payload" / "agent-ssh.cmd"
    shim.parent.mkdir(parents=True)
    shim.write_text("", encoding="utf-8")
    (payload / "payload-invocation.json").write_text(
        (
            '{"version": 1, "plugin": "agent-ssh", '
            '"commands": [{"command": "agent-ssh", "module": "agent_ssh", '
            '"purpose": "restore"}], "outputDir": "bin/payload", '
            '"windowsCatalogShim": "cmd"}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        modules,
        "_active_plugins",
        lambda: {
            "agent-ssh@copilot-extensions": SimpleNamespace(root=payload)
        },
    )
    monkeypatch.setenv("COMSPEC", "cmd.exe")
    monkeypatch.setattr(
        modules.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="ok",
            stderr="",
        ),
    )

    result = modules.run_module(
        pkg,
        pkg.modules[0],
        "windows",
        dry_run=True,
    )

    assert result.command[:4] == ["cmd.exe", "/d", "/c", str(shim)]


def test_payload_provider_missing_is_failure(tmp_path, monkeypatch):
    pkg = _provider_package(tmp_path)
    monkeypatch.setattr(
        modules,
        "_active_plugins",
        lambda: {},
    )

    result = modules.run_module(
        pkg,
        pkg.modules[0],
        "windows",
        dry_run=True,
    )

    assert result.returncode == 127
    assert not result.ok
    assert "required active plugin is unavailable" in result.stderr_tail


def test_invalid_payload_provider_invocation_rejected(tmp_path):
    data = {
        "schema_version": 3,
        "package": "acme/provider",
        "manage": {},
        "modules": [
            {
                "name": "ssh-host",
                "invocation": {
                    "plugin": "agent-ssh",
                    "command": "agent-ssh",
                },
            }
        ],
    }

    with pytest.raises(ManifestError, match="invocation requires"):
        load_package(write_package(tmp_path / "acme", "provider.yaml", data))


@pytest.mark.parametrize(
    "identity",
    ["@copilot-extensions", "agent-ssh@", "agent ssh@copilot-extensions"],
)
def test_invalid_payload_provider_plugin_identity_rejected(tmp_path, identity):
    data = {
        "schema_version": 3,
        "package": "acme/provider",
        "manage": {},
        "modules": [
            {
                "name": "ssh-host",
                "invocation": {
                    "plugin": identity,
                    "command": "agent-ssh",
                },
            }
        ],
    }

    with pytest.raises(ManifestError, match="invocation requires"):
        load_package(write_package(tmp_path / "acme", "provider.yaml", data))


def test_payload_provider_command_path_traversal_rejected(tmp_path):
    data = {
        "schema_version": 3,
        "package": "acme/provider",
        "manage": {},
        "modules": [
            {
                "name": "ssh-host",
                "invocation": {
                    "plugin": "agent-ssh@copilot-extensions",
                    "command": "../agent-ssh",
                },
            }
        ],
    }

    with pytest.raises(ManifestError, match="invocation requires"):
        load_package(write_package(tmp_path / "acme", "provider.yaml", data))


def test_payload_provider_rejects_drive_relative_output_dir(
    tmp_path, monkeypatch
):
    pkg = _provider_package(tmp_path)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "payload-invocation.json").write_text(
        '{"version": 1, "command": "agent-ssh", "outputDir": "C:escape"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        modules,
        "_active_plugins",
        lambda: {
            "agent-ssh@copilot-extensions": SimpleNamespace(root=payload)
        },
    )

    result = modules.run_module(
        pkg,
        pkg.modules[0],
        "windows",
        dry_run=True,
    )

    assert result.returncode == 127
    assert "invalid payload outputDir" in result.stderr_tail
