"""Fresh installed-package consumer without projection assets or sibling plugins."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def standalone_workspace(tmp_path):
    from agent_worktrees import static_instructions

    package = Path(static_instructions.__file__).parent
    standalone = tmp_path / "standalone"
    shutil.copytree(package, standalone / "agent_worktrees", ignore=shutil.ignore_patterns("__pycache__"))
    home = tmp_path / "home"
    plugin = home / ".copilot" / "installed-plugins" / "example" / "guidance"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"guidance"}', encoding="utf-8")
    content = b'---\r\napplyTo: "**"\r\n---\r\nUnmodified body.\r\n'
    (plugin / "body.md").write_bytes(content)
    (plugin / "instruction-projections.json").write_text(json.dumps({
        "schema": "copilot-extensions.instruction-projections", "version": 1,
        "projections": [{
            "id": "body", "template": "body.md",
            "destination": ".github/instructions/guidance/nested/body.instructions.md",
        }],
    }), encoding="utf-8")
    repo = tmp_path / "repo"
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"enabledPlugins":{"guidance@example":true}}', encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONPATH=str(standalone))
    env["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(tmp_path / "removed-snapshot")
    return standalone, home, repo, content, env


@pytest.mark.parametrize("safe_path", [False, True])
def test_standalone_module_copies_without_projection_engine(standalone_workspace, safe_path):
    from agent_worktrees import static_instructions

    standalone, home, repo, content, env = standalone_workspace
    if safe_path:
        env["PYTHONSAFEPATH"] = "1"
    else:
        env.pop("PYTHONSAFEPATH", None)
    (repo / "instruction_projections.py").write_text(
        'raise AssertionError("static copying imported an untrusted checkout module")\n',
        encoding="utf-8",
    )
    # Resolve the copied runtime first, not the editable source installation.
    code = (
        "import pathlib, sys; "
        "import agent_worktrees.local_cache_refresh as refresh; "
        "assert pathlib.Path(refresh.__file__).is_relative_to(pathlib.Path(sys.argv[1])); "
        "result = refresh.prepare_for_launch(sys.argv[2]); "
        "assert result.status == 'ready', result.diagnostic; "
        "assert 'agent_worktrees.projection_runtime_assets' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(standalone), str(repo)],
        cwd=repo, env=env, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    output = repo / static_instructions.NAMESPACE / "example" / "guidance" / "nested" / "body.local.instructions.md"
    assert output.read_bytes() == content
    assert not (standalone / "packaged-assets.json").exists()
    assert not (home / ".copilot" / "installed-plugins" / "example" / "customizing-copilot").exists()


@pytest.mark.parametrize("shell", ["powershell", "bash"])
def test_native_final_preflight_copies_knowledge_only_provider(
    standalone_workspace, tmp_path, request, shell,
):
    from agent_worktrees.static_instructions import NAMESPACE
    from native_shell_support import bash_path, run_headless

    standalone, home, repo, content, env = standalone_workspace
    knowledge = tmp_path / "knowledge"
    knowledge_settings = knowledge / ".github" / "copilot" / "settings.json"
    knowledge_settings.parent.mkdir(parents=True)
    harness_settings = repo / ".github" / "copilot" / "settings.json"
    settings = json.loads(harness_settings.read_text())
    settings["extraKnownMarketplaces"] = {
        "example": {"source": {"source": "github", "repo": "example/plugins"}},
    }
    knowledge_settings.write_text(json.dumps(settings), encoding="utf-8")
    harness_settings.unlink()
    report = tmp_path / "report.json"
    root = Path(__file__).resolve().parents[3]
    if shell == "powershell":
        executable = request.getfixturevalue("native_powershell")
        source = (root / "worktree-manager" / "bin" / "launch-session.ps1").read_text(encoding="utf-8-sig")
        start = source.index("$knowledgeArgs = @")
        end = source.index("\n\n$marketplaceArgs", start)
        function_start = source.index("function Invoke-RuntimePreflight {")
        function_end = source.index("\n$VenvPython = $AwPy", function_start)
        block = source[start:end].replace(
            "Invoke-RuntimePreflight -Label",
            f"$knowledgeArgs += @('--harness-path', '{repo}', '--knowledge-path', '{knowledge}')\n"
            "Invoke-RuntimePreflight -Label",
        )
        script = (
            f"$VenvPython = '{sys.executable}'; $knowledgeCwd = '{repo}'; "
            "$script:LaunchProject = $null\n"
            f"function Resolve-RuntimePython {{ '{sys.executable}' }}\n"
            "function Write-SetupLog { param($Message, $Level)\n"
            " $prefix = 'Knowledge plugin preflight completed: '\n"
            f" if ($Message.StartsWith($prefix)) {{ $Message.Substring($prefix.Length) | Out-File -Encoding utf8 '{report}' }}\n"
            "}\n" + source[function_start:function_end] + "\n" + block
        )
        command = [executable, "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        executable = request.getfixturevalue("native_bash")
        source = (root / "worktree-manager" / "bin" / "launch-session.sh").read_text(encoding="utf-8")
        start = source.index('    _KNOWLEDGE_CWD="')
        end = source.index("\n\n    _MARKETPLACE_ARGS", start)
        function_start = source.index("run_runtime_preflight() {")
        function_end = source.index('\nif [[ -n "$PYTHON"', function_start)
        block = source[start:end].replace(
            '    run_runtime_preflight "Knowledge plugin preflight"',
            f"    _KNOWLEDGE_ARGS+=(--harness-path '{bash_path(executable, repo)}' "
            f"--knowledge-path '{bash_path(executable, knowledge)}')\n"
            '    run_runtime_preflight "Knowledge plugin preflight"',
        )
        script = (
            "set -euo pipefail\n"
            f"PYTHON='{bash_path(executable, Path(sys.executable))}'\n"
            f"STATUS_PATH='{bash_path(executable, repo)}'\nLAUNCH_PROJECT=''\n"
            "resolve_runtime_python() { printf '%s' \"$PYTHON\"; }\n"
            "setup_log() { shift; local prefix='Knowledge plugin preflight completed: ';\n"
            f" if [[ \"$1\" == \"$prefix\"* ]]; then printf '%s' \"${{1#\"$prefix\"}}\" > '{bash_path(executable, report)}'; fi\n"
            "}\n" + source[function_start:function_end] + "\n" + block
        )
        command = [executable, "-c", script]
    result = run_headless(command, cwd=standalone, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Preparing instruction projections: starting" in result.stderr
    assert "Copying declared instructions: finished" in result.stderr
    summary = json.loads(report.read_text(encoding="utf-8-sig"))
    assert summary["projections"]["status"] == "ready"
    assert summary["projections"]["changed"] == 1
    output = repo / NAMESPACE / "example" / "guidance" / "nested" / "body.local.instructions.md"
    assert output.read_bytes() == content
