"""Tests for canonical payload-local command generation."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "generate.py"
REPO = SCRIPT.parents[2]
_spec = importlib.util.spec_from_file_location("payload_invocation_generate", SCRIPT)
generator = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(generator)


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "plugin" / "payload-invocation.json"
    path.parent.mkdir()
    scripts = path.parent / "scripts"
    scripts.mkdir()
    (scripts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / "install.ps1").write_text("# generated fixture\n", encoding="utf-8")
    path.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.payload-invocation",
                "version": 1,
                "command": "agent-example",
                "module": "agent_example",
                "runtimeRoot": ".agent-example",
                "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION",
                "purpose": "Exercise an example runtime",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_generates_three_payload_local_shims(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    assert generator.process_manifest(manifest, check=False) == []
    generated = generator.expected_files(manifest)
    assert {path.name for path in generated} == {
        "agent-example",
        "agent-example.cmd",
        "agent-example.ps1",
        "emit-command-catalog.ps1",
        "emit-command-catalog.sh",
    }
    for path, expected in generated.items():
        assert path.read_text(encoding="utf-8") == expected
        assert ".local/bin" not in expected
        assert "installed-plugins/*" not in expected
    if os.name != "nt":
        assert (manifest.parent / "bin" / "agent-example").stat().st_mode & 0o100
        assert (
            manifest.parent / "scripts" / "emit-command-catalog.sh"
        ).stat().st_mode & 0o100


def test_check_detects_drift(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    assert generator.process_manifest(manifest, check=True) == []
    (manifest.parent / "bin" / "agent-example.ps1").write_text(
        "stale\n", encoding="utf-8"
    )
    assert generator.process_manifest(manifest, check=True)


def test_manifest_validation_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["command"] = "../agent-example"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    try:
        generator.load_manifest(manifest)
    except ValueError as error:
        assert "invalid command" in str(error)
    else:
        raise AssertionError("invalid command was accepted")


def test_manifest_selects_and_requires_installer_entrypoint(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["installer"] = "init"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="installer not found"):
        generator.expected_files(manifest)

    scripts = manifest.parent / "scripts"
    (scripts / "init.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / "init.ps1").write_text("# generated fixture\n", encoding="utf-8")
    generated = generator.expected_files(manifest)
    posix = generated[manifest.parent / "bin" / "agent-example"]
    powershell = generated[manifest.parent / "bin" / "agent-example.ps1"]
    assert 'scripts/init.sh"' in posix
    assert "'scripts\\init.ps1'" in powershell


def test_manifest_supports_nested_payload_output(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "bin/payload"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generated = generator.expected_files(manifest)
    assert manifest.parent / "bin" / "payload" / "agent-example" in generated
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]
    assert 'command_path="$self_root/bin/payload/agent-example"' in catalog


@pytest.mark.parametrize("plugin", ["agent-machines", "agent-ssh"])
def test_service_free_adopters_publish_payload_catalogs(plugin: str) -> None:
    plugin_root = REPO / "plugins" / plugin
    manifest = plugin_root / "payload-invocation.json"
    data = generator.load_manifest(manifest)
    assert data["command"] == plugin

    generated = generator.expected_files(manifest)
    assert generator.process_manifest(manifest, check=True) == []
    assert plugin_root / "bin" / plugin in generated

    hooks = json.loads((plugin_root / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    for shell in ("bash", "powershell"):
        catalog_hooks = [
            hook for hook in session_hooks if "emit-command-catalog" in hook[shell]
        ]
        assert len(catalog_hooks) == 1
        assert "COPILOT_PLUGIN_ROOT" in catalog_hooks[0][shell]


@pytest.mark.skipif(os.name == "nt", reason="POSIX catalog test")
def test_posix_catalog_fails_open_when_python_fails(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python = fake_bin / "python3"
    python.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["COPILOT_PLUGIN_ROOT"] = str(manifest.parent)
    result = subprocess.run(
        [str(manifest.parent / "scripts" / "emit-command-catalog.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_preserves_args_exit_and_project_cwd(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "bin/payload"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY="$AGENT_RT_ROOT/versions/test/bin/python"\n',
        encoding="utf-8",
    )

    home = tmp_path / "home"
    fake_python = home / ".agent-example" / "versions" / "test" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$PWD|$*"\nexit 23\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_PROJECT_DIR": str(project),
        }
    )
    result = subprocess.run(
        [str(plugin / "bin" / "payload" / "agent-example"), "search", "two words"],
        cwd=plugin,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 23
    assert result.stdout.strip() == f"{project}|-m agent_example search two words"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_rejects_conflicting_payload_context(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    env = os.environ.copy()
    env.update({"HOME": str(tmp_path / "home"), "COPILOT_PLUGIN_ROOT": str(other)})
    result = subprocess.run(
        [str(plugin / "bin" / "agent-example"), "status"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 126
    assert "payload context mismatch" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_first_use_provision_is_serialized(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY=""\n'
        'p="$AGENT_RT_ROOT/versions/test/bin/python"\n'
        '[ -x "$p" ] && AGENT_RT_PY="$p"\n'
        "true\n",
        encoding="utf-8",
    )
    installer = scripts / "install.sh"
    installer.write_text(
        '#!/bin/bash\nset -eu\nroot="$HOME/.agent-example"\n'
        'plugin="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'case "$1" in\n'
        '  stamp) mkdir -p "$root"; printf "%s\\n" "$plugin" > "$root/payload-dir" ;;\n'
        '  provision)\n'
        '    printf "provision\\n" >> "$root/provision-count"\n'
        '    sleep 0.5\n'
        '    mkdir -p "$root/versions/test/bin"\n'
        '    printf "%s\\n" "#!/bin/sh" "exit 0" > "$root/versions/test/bin/python"\n'
        '    chmod +x "$root/versions/test/bin/python" ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    installer.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_EXT_NO_FLOCK": "1",
        }
    )
    lock = home / ".agent-example" / ".provision.lock.pid"
    lock.parent.mkdir(parents=True)
    lock.symlink_to("999999999")
    command = [str(plugin / "bin" / "agent-example"), "status"]
    first = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    second = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    _first_out, first_err = first.communicate(timeout=10)
    _second_out, second_err = second.communicate(timeout=10)
    assert first.returncode == 0, first_err
    assert second.returncode == 0, second_err
    count = (home / ".agent-example" / "provision-count").read_text(
        encoding="utf-8"
    )
    assert count.splitlines() == ["provision"]


def test_windows_templates_preserve_context_and_release_payload_cwd(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    generated = generator.expected_files(manifest)
    powershell = next(
        content for path, content in generated.items() if path.suffix == ".ps1"
    )
    cmd = next(
        content for path, content in generated.items() if path.suffix == ".cmd"
    )
    assert "[IO.Directory]::SetCurrentDirectory($_outside)" in powershell
    assert "StartsWith($_payloadPrefix" in powershell
    assert "[IO.FileShare]::None" in powershell
    assert "if not defined COPILOT_PLUGIN_ROOT" in cmd


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_powershell_shim_preserves_sibling_cwd_and_leaves_payload(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "bin/payload"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir(exist_ok=True)
    python_literal = str(Path(sys.executable)).replace("'", "''")
    (scripts / "resolve-runtime.ps1").write_text(
        f"$AgentRtPy = '{python_literal}'\n",
        encoding="utf-8",
    )
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "agent_example.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "print(f\"{Path.cwd()}|{' '.join(sys.argv[1:])}\")\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    sibling = tmp_path / "plugin-backup"
    project.mkdir()
    sibling.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_PROJECT_DIR": str(project),
            "PYTHONPATH": str(module_dir),
        }
    )
    command = [
        pwsh,
        "-NoProfile",
        "-File",
        str(plugin / "bin" / "payload" / "agent-example.ps1"),
        "status",
    ]
    sibling_result = subprocess.run(
        command, cwd=sibling, env=env, capture_output=True, text=True, check=True
    )
    assert sibling_result.stdout.strip() == (
        f"{sibling}|status"
    )
    payload_result = subprocess.run(
        command, cwd=plugin, env=env, capture_output=True, text=True, check=True
    )
    assert payload_result.stdout.strip() == (
        f"{project}|status"
    )
