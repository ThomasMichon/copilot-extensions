"""Local Git discovery never borrows the native JSON control channel."""

import asyncio
import json
import os
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs, windowless_daemon_kwargs
from ssh_manager.process import terminate_ssh_process_tree

from agent_codespaces import config


@pytest.mark.parametrize("probe", ["root", "origin"])
def test_git_probe_has_null_stdin_and_bounded_wait(tmp_path, monkeypatch, probe):
    calls = []
    output = str(tmp_path) if probe == "root" else "https://example.test/repo.git"

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(config.subprocess, "run", run)
    if probe == "root":
        assert config.cwd_repo_root() == tmp_path.resolve()
    else:
        assert config._git_origin_remote(tmp_path) == output
    argv, kwargs = calls[0]
    assert argv == (["git", "rev-parse", "--show-toplevel"] if probe == "root" else
                    ["git", "config", "--get", "remote.origin.url"])
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] == config._GIT_PROBE_TIMEOUT == 10.0
    assert kwargs.get("creationflags", 0) == no_window_kwargs().get("creationflags", 0)


@pytest.mark.parametrize("probe", ["root", "origin"])
def test_git_probe_timeout_is_not_missing_configuration(tmp_path, monkeypatch, probe):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(config.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="Local Git .* discovery timed out"):
        if probe == "root":
            config.cwd_repo_root()
        else:
            config._git_origin_remote(tmp_path)


@pytest.mark.parametrize("probe", ["root", "origin"])
def test_non_repository_and_missing_origin_remain_absent(tmp_path, monkeypatch, probe):
    monkeypatch.setattr(config.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 1, "", ""))
    assert (config.cwd_repo_root() if probe == "root" else config._git_origin_remote(tmp_path)) is None


@pytest.mark.asyncio
async def test_real_git_finishes_while_native_control_pipe_stays_open(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git is required for the real process-shape regression")
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--quiet", str(repo)], check=True, capture_output=True,
        stdin=subprocess.DEVNULL, timeout=10, **no_window_kwargs(),
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "remote.origin.url", "https://example.test/repo.git"],
        check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=10, **no_window_kwargs(),
    )
    script = r"""
import json,threading
from agent_codespaces.native_transport import InputPump
from agent_codespaces.config import cwd_repo_root, _git_origin_remote
pump = InputPump()
print(json.dumps({"stage":"before-git"}), flush=True)
root = cwd_repo_root()
print(json.dumps({"stage":"after-git","rootFound":root is not None,
                  "origin":_git_origin_remote(root)}), flush=True)
frame = pump.frames.get(timeout=10)
print(json.dumps({"control":json.loads(frame)["id"]}), flush=True)
pump.closed.wait(10)
for thread in threading.enumerate():
    if thread.name == "native-control-input":
        thread.join(2)
"""
    env = dict(os.environ)
    for key in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "SESSION_ID", "COPILOT_PLUGIN_ROOT"):
        env.pop(key, None)
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, cwd=repo, env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=1048576,
        **windowless_daemon_kwargs(breakaway=True),
    )
    try:
        assert json.loads(await asyncio.wait_for(process.stdout.readline(), 5)) == {"stage": "before-git"}
        result = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
        assert result == {"stage": "after-git", "rootFound": True, "origin": "https://example.test/repo.git"}
        assert not process.stdin.is_closing()
        process.stdin.write(b'{"id":"control-kept"}\n')
        await process.stdin.drain()
        assert json.loads(await asyncio.wait_for(process.stdout.readline(), 5)) == {"control": "control-kept"}
        process.stdin.close()
        await process.stdin.wait_closed()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 0, (await process.stderr.read()).decode(errors="replace")
    finally:
        if not process.stdin.is_closing():
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            await terminate_ssh_process_tree(process)
