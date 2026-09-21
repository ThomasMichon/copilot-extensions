"""Tests for the ai-attribution session guidance writer."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import time as time_module
from pathlib import Path
from types import SimpleNamespace

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPT = _PLUGIN / "scripts" / "write_session_guidance.py"
_SPEC = importlib.util.spec_from_file_location("ai_guidance_writer_under_test", _SCRIPT)
assert _SPEC and _SPEC.loader
writer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = writer
_SPEC.loader.exec_module(writer)


def _target(home: Path) -> Path:
    return (
        home
        / ".copilot"
        / "session-state"
        / "session-1"
        / "instructions"
        / "ai-attribution"
        / "session-guidance.instructions.md"
    )


def test_writes_current_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(
        writer,
        "_run_contributor",
        lambda root, payload: "[owner: ai-attribution@1.0.0]\npolicy",
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert _target(tmp_path).read_text(encoding="utf-8") == (
        "# AI attribution session guidance\n\n"
        "[owner: ai-attribution@1.0.0]\npolicy\n"
    )


def test_no_content_replaces_stale_guidance(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "")
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("stale policy", encoding="utf-8")
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = target.read_text(encoding="utf-8")
    assert "stale policy" not in content
    assert "unavailable" in content


def test_cache_miss_calls_contributor_then_hits_cache(monkeypatch, tmp_path):
    calls = []

    def fake_contributor(root, payload):
        calls.append(payload)
        return "[owner: ai-attribution@1.0.0]\npolicy"

    monkeypatch.setattr(writer, "_run_contributor", fake_contributor)
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = {"sessionId": "session-1", "cwd": str(repo)}

    assert writer.write_session_guidance(payload, home=tmp_path)
    assert len(calls) == 1
    assert "policy" in _target(tmp_path).read_text(encoding="utf-8")

    # A second session (different sessionId, same repo cwd) must hit the
    # cache rather than invoking the contributor again.
    payload2 = {"sessionId": "session-2", "cwd": str(repo)}
    assert writer.write_session_guidance(payload2, home=tmp_path)
    assert len(calls) == 1, "second call should have been served from cache"
    second_target = (
        tmp_path
        / ".copilot"
        / "session-state"
        / "session-2"
        / "instructions"
        / "ai-attribution"
        / "session-guidance.instructions.md"
    )
    assert "policy" in second_target.read_text(encoding="utf-8")


def test_cache_expired_recomputes(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        writer, "_run_contributor", lambda *args: calls.append(1) or "fresh policy"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    cache_dir = writer._cache_dir(tmp_path)
    cache_dir.mkdir(parents=True)
    key = writer._repo_cache_key(str(repo))
    (cache_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "version": writer._CACHE_FORMAT_VERSION,
                "computed_at": 0.0,
                "guidance": "stale cached policy",
            }
        ),
        encoding="utf-8",
    )
    assert writer.write_session_guidance(
        {"sessionId": "session-1", "cwd": str(repo)}, home=tmp_path
    )
    assert len(calls) == 1
    assert "fresh policy" in _target(tmp_path).read_text(encoding="utf-8")


def test_force_refresh_env_bypasses_fresh_cache(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        writer, "_run_contributor", lambda *args: calls.append(1) or "recomputed"
    )
    monkeypatch.setenv("AI_ATTRIBUTION_FORCE_REFRESH", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    cache_dir = writer._cache_dir(tmp_path)
    cache_dir.mkdir(parents=True)
    key = writer._repo_cache_key(str(repo))
    (cache_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "version": writer._CACHE_FORMAT_VERSION,
                "computed_at": time_module.time(),
                "guidance": "fresh cached policy",
            }
        ),
        encoding="utf-8",
    )
    assert writer.write_session_guidance(
        {"sessionId": "session-1", "cwd": str(repo)}, home=tmp_path
    )
    assert len(calls) == 1
    assert "recomputed" in _target(tmp_path).read_text(encoding="utf-8")


def test_missing_cwd_never_caches(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        writer, "_run_contributor", lambda *args: calls.append(1) or "policy"
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert writer.write_session_guidance({"sessionId": "session-2"}, home=tmp_path)
    assert len(calls) == 2, "no cwd means no cache key, so every call recomputes"
    assert not writer._cache_dir(tmp_path).exists()


def test_over_budget_content_writes_explicit_status(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "x" * 5000)
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert "omitted because" in _target(tmp_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("session_id", ("", "../escape", "slash/value", "a" * 129))
def test_rejects_unsafe_session_ids(monkeypatch, tmp_path, session_id):
    monkeypatch.setattr(
        writer,
        "_run_contributor",
        lambda *args: pytest.fail("must not run for an unsafe session id"),
    )
    assert not writer.write_session_guidance({"sessionId": session_id}, home=tmp_path)
    assert not (tmp_path / ".copilot").exists()


@pytest.mark.parametrize("component", (".copilot", "session-state", "session-1"))
def test_rejects_ancestor_escape(monkeypatch, tmp_path, component):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "policy")
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == ".copilot":
        link = tmp_path / ".copilot"
    elif component == "session-state":
        copilot = tmp_path / ".copilot"
        copilot.mkdir()
        link = copilot / "session-state"
    else:
        state = tmp_path / ".copilot" / "session-state"
        state.mkdir(parents=True)
        link = state / "session-1"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    assert not writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_resolve_runtime_error_fails_open(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "policy")
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path.name == "session-state":
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert not writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_reparse_detection_without_link_creation():
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
    )
    assert writer._is_link_or_reparse(path)


def test_main_always_emits_empty_object(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("not json"))
    assert writer.main() == 0
    assert capsys.readouterr().out == "{}"


def test_powershell_wrapper_writes_bounded_session_file(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    payload = json.dumps(
        {"sessionId": "session-1", "cwd": str(repo), "source": "copilot-cli"}
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(_PLUGIN),
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(_PLUGIN / "scripts" / "write-session-guidance.ps1"),
        ],
        cwd=repo,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert content.startswith("# AI attribution session guidance\n\n")
    assert "[owner: ai-attribution@" in content
    assert len(content.encode("utf-8")) <= writer._GUIDANCE_MAX_BYTES


def test_powershell_wrapper_serves_warm_cache_without_spawning_python(tmp_path):
    """Proves the wrapper's cache fast path actually skips python on a hit,
    by stripping every python interpreter from PATH before invoking it: if
    the fast path did not serve this from cache and instead fell through to
    the python fallback, there would be no python to run it, and the session
    file would show the "unavailable" fallback text instead of the cached
    guidance asserted below."""
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    git_exe = shutil.which("git")
    if not git_exe:
        pytest.skip("git is unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "home"
    home.mkdir()

    key = writer._repo_cache_key(str(repo))
    cache_dir = writer._cache_dir(home)
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "version": writer._CACHE_FORMAT_VERSION,
                "computed_at": time_module.time(),
                "guidance": "[owner: ai-attribution@1.0.0]\ncached policy only",
            }
        ),
        encoding="utf-8",
    )

    payload = json.dumps(
        {"sessionId": "session-1", "cwd": str(repo), "source": "copilot-cli"}
    )
    # Start from the real environment (pwsh itself needs plenty of it --
    # SystemRoot, TEMP, PSModulePath, etc.) and strip out only the
    # directories that would let it find a python interpreter.
    python_dirs = {
        str(Path(p).resolve().parent)
        for name in ("python3", "python", "py")
        for p in [shutil.which(name)]
        if p
    }
    filtered_path = os.pathsep.join(
        part
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if part and str(Path(part).resolve()) not in python_dirs
    )
    env = {
        **os.environ,
        "PATH": filtered_path,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(_PLUGIN),
    }
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(_PLUGIN / "scripts" / "write-session-guidance.ps1"),
        ],
        cwd=repo,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert "cached policy only" in content
    assert "unavailable" not in content


def test_powershell_wrapper_falls_back_on_cold_cache(tmp_path):
    """No cache entry yet -> the wrapper must fall through to the unchanged
    python path rather than silently producing no guidance."""
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    payload = json.dumps(
        {"sessionId": "session-1", "cwd": str(repo), "source": "copilot-cli"}
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(_PLUGIN),
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(_PLUGIN / "scripts" / "write-session-guidance.ps1"),
        ],
        cwd=repo,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert "[owner: ai-attribution@" in content
    # Recomputing via python must also have warmed the cache for next time.
    assert (writer._cache_dir(home) / f"{writer._repo_cache_key(str(repo))}.json").is_file()


def _bash_prereqs():
    bash = shutil.which("bash")
    jq = shutil.which("jq")
    sha = shutil.which("sha256sum") or shutil.which("shasum")
    return bash, jq, sha


def test_bash_wrapper_writes_bounded_session_file(tmp_path):
    bash, jq, sha = _bash_prereqs()
    if not (bash and jq and sha):
        pytest.skip("bash/jq/sha256sum unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    payload = json.dumps(
        {"sessionId": "session-1", "cwd": str(repo), "source": "copilot-cli"}
    )
    env = {**os.environ, "HOME": str(home), "COPILOT_PLUGIN_ROOT": str(_PLUGIN)}
    result = subprocess.run(
        [bash, str(_PLUGIN / "scripts" / "write-session-guidance.sh")],
        cwd=repo,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert content.startswith("# AI attribution session guidance\n\n")
    assert "[owner: ai-attribution@" in content
    assert len(content.encode("utf-8")) <= writer._GUIDANCE_MAX_BYTES


def test_bash_wrapper_serves_warm_cache_without_spawning_python(tmp_path):
    """Same proof as the PowerShell equivalent, adapted for bash: build an
    isolated PATH containing symlinks only to the tools the fast path needs
    (bash, jq, sha256sum/shasum, git, coreutils) and deliberately excluding
    every python3/python/py -- if the fast path did not serve this from
    cache and fell through to python, there would be none to invoke."""
    bash, jq, sha = _bash_prereqs()
    if not (bash and jq and sha):
        pytest.skip("bash/jq/sha256sum unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "home"
    home.mkdir()

    key = writer._repo_cache_key(str(repo))
    cache_dir = writer._cache_dir(home)
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "version": writer._CACHE_FORMAT_VERSION,
                "computed_at": time_module.time(),
                "guidance": "[owner: ai-attribution@1.0.0]\ncached policy only",
            }
        ),
        encoding="utf-8",
    )

    isolated_bin = tmp_path / "isolated-bin"
    isolated_bin.mkdir()
    for tool in (
        "bash", "jq", "sha256sum", "shasum", "git", "mkdir", "cat", "printf",
        "awk", "date", "mv", "rm", "dirname", "cut", "sed", "grep",
    ):
        src = shutil.which(tool)
        if src:
            try:
                (isolated_bin / tool).symlink_to(src)
            except OSError:
                pytest.skip("symlinks are unavailable")

    payload = json.dumps(
        {"sessionId": "session-1", "cwd": str(repo), "source": "copilot-cli"}
    )
    env = {
        "PATH": str(isolated_bin),
        "HOME": str(home),
        "COPILOT_PLUGIN_ROOT": str(_PLUGIN),
    }
    result = subprocess.run(
        [str(isolated_bin / "bash"), str(_PLUGIN / "scripts" / "write-session-guidance.sh")],
        cwd=repo,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert "cached policy only" in content
    assert "unavailable" not in content


def test_bash_wrapper_falls_back_on_cold_cache(tmp_path):
    bash, jq, sha = _bash_prereqs()
    if not (bash and jq and sha):
        pytest.skip("bash/jq/sha256sum unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    payload = json.dumps(
        {"sessionId": "session-1", "cwd": str(repo), "source": "copilot-cli"}
    )
    env = {**os.environ, "HOME": str(home), "COPILOT_PLUGIN_ROOT": str(_PLUGIN)}
    result = subprocess.run(
        [bash, str(_PLUGIN / "scripts" / "write-session-guidance.sh")],
        cwd=repo,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert "[owner: ai-attribution@" in content
    assert (writer._cache_dir(home) / f"{writer._repo_cache_key(str(repo))}.json").is_file()
