"""Fresh installed-package consumer without projection assets or sibling plugins."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest


def _copy_runtime_package(source, destination):
    pending = []
    with ThreadPoolExecutor(max_workers=16) as copies:
        def queue_copy(source, destination):
            pending.append(copies.submit(shutil.copy2, source, destination))
            return destination

        shutil.copytree(
            source, destination, copy_function=queue_copy,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        for future in pending:
            future.result()


@pytest.mark.guard
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
def test_runtime_fixture_copy_preserves_source_and_staged_bytes(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "standalone"
    (source / "nested").mkdir(parents=True)
    contents = {"module.py": b"opaque\xff\r\n", "nested/data.bin": b"\x00\x01\r\n"}
    for name, content in contents.items():
        (source / name).write_bytes(content)
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "ignored.pyc").write_bytes(b"stale")

    _copy_runtime_package(source, destination)

    for name, content in contents.items():
        assert (source / name).read_bytes() == content
        assert (destination / name).read_bytes() == content
    assert not (destination / "__pycache__").exists()


@pytest.mark.guard
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
def test_runtime_fixture_copy_propagates_worker_failure(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_bytes(b"fixture")

    def fail_copy(*args, **kwargs):
        raise OSError("fixture copy refused")

    monkeypatch.setattr(shutil, "copy2", fail_copy)
    with pytest.raises(OSError, match="fixture copy refused"):
        _copy_runtime_package(source, tmp_path / "standalone")


@pytest.fixture
def standalone_workspace(tmp_path):
    from agent_worktrees import static_instructions

    package = Path(static_instructions.__file__).parent
    standalone = tmp_path / "standalone"
    _copy_runtime_package(package, standalone / "agent_worktrees")
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
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
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


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_compatibility_publisher_uses_the_managed_publication_lease(standalone_workspace):
    from agent_worktrees import static_instructions

    standalone, _, repo, _, env = standalone_workspace
    scripts = (
        Path(__file__).resolve().parents[3] / "plugins/customizing-copilot"
        / "skills/reviewing-customizations/scripts"
    )
    code = (
        "import pathlib,sys; sys.path.insert(0,sys.argv[1]); "
        "import instruction_projections as p; "
        "root=pathlib.Path(sys.argv[2]); "
        "def_discover=lambda: (_ for _ in ()).throw(AssertionError('discovery before lease')); "
        "r=p.render_local_cache(root,def_discover); "
        "assert r.blocking == 1, r.findings; "
        "assert r.findings[0].check == 'projection-local-cache-lock', r.findings; "
        "assert not r.changed"
    )
    with static_instructions.publication_lock(repo):
        child = subprocess.run(
            [sys.executable, "-B", "-c", code, str(scripts), str(repo)],
            cwd=standalone, env=env, capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    assert child.returncode == 0, child.stderr


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_separate_launch_processes_always_copy_current_plugin(standalone_workspace):
    standalone, _, repo, _, env = standalone_workspace
    code = (
        "import json,sys; from dataclasses import asdict; "
        "from agent_worktrees import local_cache_refresh as r,static_instructions as s\n"
        "print(json.dumps(asdict(r.prepare_for_launch(sys.argv[1]))))\n"
    )
    results = []
    for mode in ("first", "again"):
        child = subprocess.run(
            [sys.executable, "-c", code, str(repo), mode], env=env, cwd=standalone,
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert child.returncode == 0, child.stderr
        results.append(json.loads(child.stdout))
    assert results[0]["status"] == results[1]["status"] == "ready"
    assert results[0]["changed"] == results[1]["changed"] == 1
    assert results[0]["reused"] is results[1]["reused"] is False
    assert results[0]["publication_revision"] != results[1]["publication_revision"]


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_newer_cross_process_publication_cannot_be_overwritten(standalone_workspace):
    standalone, home, repo, _, env = standalone_workspace
    entered, release = standalone / "entered", standalone / "release"
    code = (
        "import pathlib,json,sys,time; from dataclasses import asdict; "
        "from agent_worktrees import local_cache_refresh as r,static_instructions as s\n"
        "if sys.argv[2]=='old':\n"
        " original=s._snapshot\n"
        " def held(*a,**kw):\n"
        "  value=original(*a,**kw); pathlib.Path(sys.argv[3]).touch()\n"
        "  until=time.monotonic()+10\n"
        "  while not pathlib.Path(sys.argv[4]).exists():\n"
        "   if time.monotonic()>until: raise TimeoutError('fixture release missing')\n"
        "   time.sleep(.01)\n"
        "  return value\n"
        " s._snapshot=held\n"
        "print(json.dumps(asdict(r.prepare_for_launch(sys.argv[1]))))\n"
    )
    children = []
    try:
        for mode in ("old", "new"):
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(repo), mode, str(entered), str(release)],
                env=env, cwd=standalone, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            children.append(child)
            if mode == "old":
                until = time.monotonic() + 10
                while not entered.exists() and time.monotonic() < until:
                    time.sleep(.01)
                assert entered.exists()
                (home / ".copilot/installed-plugins/example/guidance/body.md").write_bytes(b"new generation")
        release.touch()
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert results[0]["status"] == "ready"
        assert results[1]["status"] == "ready"
        output = repo / ".github/instructions/agent-worktrees-generated/example/guidance/nested/body.local.instructions.md"
        assert output.read_bytes() == b"new generation"
    finally:
        release.touch()
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


@pytest.mark.skipif(
    sys.platform != "linux" or not hasattr(os, "pidfd_open"),
    reason="native Linux process-group and identity-safe cleanup unavailable",
)
@pytest.mark.parametrize("failure", ["cancellation", "operation-timeout"])
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_manager_reaps_foreground_git_descendants(tmp_path, failure):
    import importlib.util
    import signal

    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "engine_posix_process", root / "worktree-manager/src/worktree_manager/engine_posix_process.py",
    )
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    receipt = tmp_path / "descendants.json"
    fixture = (
        "import json,os,pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(12)']); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'group':os.getpgrp(),'members':[(pid,pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]) "
        "for pid in (os.getpid(),child.pid)]})); time.sleep(12)"
    )
    driver = (
        "import pathlib,sys; "
        "from agent_worktrees import static_instructions as s,push_timeout as p; "
        "original=p.run_bounded\n"
        "def run(cmd,**kwargs):\n"
        " assert kwargs['inherit_process_group'] is True\n"
        " return original([sys.executable,'-c',sys.argv[1],sys.argv[2]],**kwargs)\n"
        "p.run_bounded=run\n"
        "s._remaining=lambda deadline,ceiling=None:1\n"
        "s._git(pathlib.Path.cwd(),'status')\n"
        "raise AssertionError('timed-out foreground operation continued')\n"
    )
    env = dict(os.environ, WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP="1")
    env["PYTHONPATH"] = str(root / "plugins/agent-worktrees/src")
    members = []

    def live(pid, started):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        except FileNotFoundError:
            return False
        return fields[19] == started and fields[0] != "Z"

    try:
        args = [sys.executable, "-c", driver, fixture, str(receipt)]
        if failure == "cancellation":
            with pytest.raises(subprocess.TimeoutExpired):
                owner.run(args, env=env, timeout=0.8)
        else:
            result = owner.run(args, env=env, timeout=10)
            assert result.returncode != 0
        data = json.loads(receipt.read_text())
        members = data["members"]
        assert data["group"] not in (os.getpgrp(), *(pid for pid, _ in members))
        until = time.monotonic() + 2
        while True:
            alive = [pid for pid, started in members if live(pid, started)]
            if not alive or time.monotonic() >= until:
                break
            time.sleep(0.02)
        assert not alive, "foreground Git descendants escaped the Manager owner"
    finally:
        if not members and receipt.exists():
            members = json.loads(receipt.read_text())["members"]
        for pid, started in members:
            try:
                descriptor = os.pidfd_open(pid)
            except ProcessLookupError:
                pass
            else:
                try:
                    if live(pid, started):
                        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                finally:
                    os.close(descriptor)


@pytest.mark.parametrize("shell", ["powershell", "bash"])
@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_native_resolve_remains_compatible_with_older_runtime(
    standalone_workspace, request, shell,
):
    from native_shell_support import bash_path, run_headless

    standalone, _, _, _, env = standalone_workspace
    (standalone / "agent_worktrees" / "__main__.py").write_text(
        "import argparse,json,os\n"
        "parser=argparse.ArgumentParser()\n"
        "sub=parser.add_subparsers(required=True)\n"
        "sub.add_parser('resolve').add_argument('--json',action='store_true')\n"
        "args=parser.parse_args()\n"
        "assert args.json\n"
        "print(json.dumps({'deferred':os.environ.get('AGENT_WORKTREES_DEFER_PROJECTIONS')}))\n",
        encoding="utf-8",
    )
    env["AGENT_WORKTREES_DEFER_PROJECTIONS"] = "previous"
    root = Path(__file__).resolve().parents[3] / "worktree-manager" / "bin"
    if shell == "powershell":
        executable = request.getfixturevalue("native_powershell")
        text = (root / "launch-session.ps1").read_text(encoding="utf-8-sig")
        block = text[text.index("$resolveArgs = @"):text.index("\nif ($LASTEXITCODE -ne 0)", text.index("$resolveArgs = @"))]
        script = (
            f"$VenvPython='{sys.executable}'; $script:LaunchProject=$null; "
            "$CopilotArgs=@('--json')\n"
            "function Write-SetupLog {}\nfunction Write-LaunchTrace {}\n"
            + block
            + "\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
            + "if ($env:AGENT_WORKTREES_DEFER_PROJECTIONS -ne 'previous') { throw 'environment leaked' }\n"
            + "$jsonOutput\n"
        )
        command = [executable, "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        executable = request.getfixturevalue("native_bash")
        text = (root / "launch-session.sh").read_text(encoding="utf-8")
        start = text.index("resolve_args=(-m agent_worktrees)")
        block = text[start:text.index("\nif [[ $RC -ne 0 ]]", start)]
        script = (
            f"PYTHON='{bash_path(executable, Path(sys.executable))}'; LAUNCH_PROJECT=''\n"
            "set -- --json\n" + block
            + "\n[[ $RC -eq 0 ]] || exit \"$RC\"\n"
            + "[[ $AGENT_WORKTREES_DEFER_PROJECTIONS == previous ]] || exit 99\n"
            + "printf '%s' \"$JSON\"\n"
        )
        command = [executable, "-c", script]
    result = run_headless(command, cwd=standalone, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"deferred": "1"}


@pytest.mark.parametrize("shell", ["powershell", "bash"])
@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
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
            " printf '%s\\n' \"$1\" >&2\n"
            "}\n" + source[function_start:function_end] + "\n" + block
        )
        command = [executable, "-c", script]
    result = run_headless(command, cwd=standalone, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "launch/Preparing-instruction-projections starting [elapsed " in result.stderr
    assert "launch/Copying-declared-instructions done [duration " in result.stderr
    summary = json.loads(report.read_text(encoding="utf-8-sig"))
    assert summary["projections"]["status"] == "ready"
    assert summary["projections"]["changed"] == 1
    output = repo / NAMESPACE / "example" / "guidance" / "nested" / "body.local.instructions.md"
    assert output.read_bytes() == content
