"""Tests for the ai-attribution session guidance writer."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
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


def test_root_lookup_is_reused_for_cache_and_profile(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(writer, "_repo_root", lambda cwd: calls.append(cwd) or cwd)
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "Required disclosure.")
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "")
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = {"sessionId": "session-1", "cwd": str(repo)}
    assert writer.write_session_guidance(payload, home=tmp_path)
    assert calls == [str(repo)]
    calls.clear()
    assert writer.write_session_guidance(payload, home=tmp_path)
    assert calls == [str(repo)]


def test_writer_subprocesses_do_not_allocate_windows_consoles(monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        output = b'{"additionalContext":"policy"}' if "input" in kwargs else "repo"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(writer.subprocess, "run", run)
    monkeypatch.setattr(writer, "_contributor_argv", lambda root: ["shell"])
    assert writer._repo_root("repo") == "repo"
    assert writer._run_contributor(Path("."), b"{}") == "policy"
    expected = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    assert [call["creationflags"] for call in calls] == [expected, expected]


def test_over_budget_content_writes_explicit_status(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "x" * 5000)
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert "omitted because" in _target(tmp_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("base_bytes", [3400, 4050])
@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_large_profile_never_displaces_policy_or_tightening(monkeypatch, tmp_path, base_bytes):
    kernel = "Self-authored contributions may omit disclosure."
    base = kernel + "x" * (base_bytes - len(kernel))
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: base)
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    config = tmp_path / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"version": 1, "rules": [
        {"header": "AI " * 150, "required": True},
    ]}),
                      encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    assert writer.write_session_guidance(
        {"sessionId": "session-1", "cwd": str(repo)}, home=tmp_path
    )
    actual = _target(tmp_path).read_text(encoding="utf-8")
    assert base in actual
    assert actual.startswith("Disclose AI for ALL contributions.\n\n")
    assert len(actual.encode("utf-8")) <= 4096
    cache = json.loads(next(writer._cache_dir(tmp_path).glob("*.json")).read_text())
    assert cache["profile_header"] + cache["profile_guidance"] + "\n" == actual
    shell = (shutil.which("pwsh") or shutil.which("powershell.exe")) if os.name == "nt" else shutil.which("bash")
    if not shell:
        pytest.skip("native shell unavailable")
    if os.name == "nt":
        native_path = os.pathsep.join((
            str(Path(shutil.which("git")).parent), str(Path(shell).parent),
            str(Path(os.environ["SystemRoot"]) / "System32"),
        ))
        command = [shell, "-NoProfile", "-File", str(_PLUGIN / "scripts" / "write-session-guidance.ps1")]
    else:
        tools = tmp_path / "native-tools"
        tools.mkdir()
        for name in ("git", "jq", "sha256sum", "shasum", "cat", "cut", "date",
                     "awk", "dirname", "wc", "mkdir", "mv", "rm"):
            executable = shutil.which(name)
            if executable:
                (tools / name).symlink_to(executable)
        if not shutil.which("jq") or not (shutil.which("sha256sum") or shutil.which("shasum")):
            pytest.skip("native JSON/hash tools unavailable")
        native_path = str(tools)
        command = [shell, str(_PLUGIN / "scripts" / "write-session-guidance.sh")]
    env = {**os.environ, "PATH": native_path, "HOME": str(tmp_path),
           "USERPROFILE": str(tmp_path), "COPILOT_PLUGIN_ROOT": str(_PLUGIN)}
    env.pop("AI_ATTRIBUTION_FORCE_REFRESH", None)
    result = subprocess.run(
        command, input=json.dumps({"sessionId": "session-2", "cwd": str(repo)}),
        text=True, capture_output=True, env=env, check=True,
    )
    assert json.loads(result.stdout) == {}
    warm_target = _target(tmp_path).parents[3] / "session-2" / "instructions" / "ai-attribution" / _target(tmp_path).name
    assert warm_target.read_text(encoding="utf-8") == actual


@pytest.mark.parametrize("raw", [
    "invalid", '{"version":1,"rules":' + "[" * 10000 + "]" * 10000 + "}",
    json.dumps({"version": 1, "variables": {"assistant": "human-review"},
                "rules": [{"header": "{assistant}ed"}]}),
], ids=["invalid-json", "deep-json", "review-claim"])
def test_bad_profile_preserves_kernel_with_visible_diagnostic(monkeypatch, tmp_path, raw):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "Required disclosure.")
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "")
    config = tmp_path / ".copilot" / "ai-attribution.json"
    config.parent.mkdir()
    config.write_text(raw, encoding="utf-8")
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    actual = _target(tmp_path).read_text(encoding="utf-8")
    assert "Required disclosure." in actual
    assert "rejected" in actual
    assert actual.startswith("Disclose AI for ALL contributions.\n\n")


@pytest.mark.parametrize("directories", [
    "~missing-attribution-user/instructions", ",".join(["x"] * 129),
], ids=["missing-user", "entry-limit"])
def test_discovery_errors_preserve_kernel(monkeypatch, tmp_path, directories):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "Required disclosure.")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", directories)
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    actual = _target(tmp_path).read_text(encoding="utf-8")
    assert "Required disclosure." in actual
    assert "discovery rejected" in actual
    assert actual.startswith("Disclose AI for ALL contributions.\n\n")


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


def test_powershell_wrapper_serves_warm_cache_without_spawning_python(monkeypatch, tmp_path):
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

    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "")
    monkeypatch.setattr(
        writer, "_run_contributor",
        lambda *args: "[owner: ai-attribution@1.0.0]\ncached policy only",
    )
    assert writer.write_session_guidance({"sessionId": "seed", "cwd": str(repo)}, home=home)

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


def test_bash_wrapper_serves_warm_cache_without_spawning_python(monkeypatch, tmp_path):
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

    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(home))
    for variable in ("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "XDG_CONFIG_HOME", "APPDATA"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(
        writer, "_run_contributor",
        lambda *args: "[owner: ai-attribution@1.0.0]\ncached policy only",
    )
    assert writer.write_session_guidance({"sessionId": "seed", "cwd": str(repo)}, home=home)

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


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_bash_warm_missing_roster_uses_constant_parser_processes(monkeypatch, tmp_path):
    bash, jq, sha = _bash_prereqs()
    if os.name == "nt" or not (bash and jq and sha):
        pytest.skip("native POSIX bash/jq/hash tools unavailable")
    repo, home, tools = (tmp_path / name for name in ("repo", "home", "tools"))
    home.mkdir()
    tools.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(writer, "_run_contributor", lambda *args: "Required disclosure.")
    for tool in ("git", "sha256sum", "shasum", "cat", "cut", "date", "awk",
                 "wc", "mkdir", "mv", "rm"):
        source = shutil.which(tool)
        if source:
            (tools / tool).symlink_to(source)
    for tool in ("jq", "dirname"):
        source = shutil.which(tool)
        assert source
        wrapper = tools / tool
        wrapper.write_text(
            "#!/bin/sh\nprintf '" + tool + "\\n' >> \"$CALL_LOG\"\nexec "
            + shlex.quote(source) + " \"$@\"\n", encoding="utf-8"
        )
        wrapper.chmod(0o700)
    log = tmp_path / "calls"
    counts = []
    for size in (1, 128):
        directories = [tmp_path / f"missing-{i}" for i in range(size)]
        monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", os.pathsep.join(map(str, directories)))
        assert writer.write_session_guidance(
            {"sessionId": f"seed-{size}", "cwd": str(repo)}, home=home
        )
        log.write_text("", encoding="utf-8")
        env = {**os.environ, "PATH": str(tools), "CALL_LOG": str(log),
               "COPILOT_PLUGIN_ROOT": str(_PLUGIN)}
        env.pop("AI_ATTRIBUTION_FORCE_REFRESH", None)
        session = f"warm-{size}"
        result = subprocess.run(
            [bash, str(_PLUGIN / "scripts" / "write-session-guidance.sh")],
            input=json.dumps({"sessionId": session, "cwd": str(repo)}),
            text=True, capture_output=True, env=env, check=True, timeout=30,
        )
        assert result.stdout == "{}"
        target = home / ".copilot" / "session-state" / session / "instructions" / "ai-attribution" / "session-guidance.instructions.md"
        assert "Required disclosure." in target.read_text(encoding="utf-8")
        calls = log.read_text(encoding="utf-8").splitlines()
        assert "dirname" not in calls
        counts.append(calls.count("jq"))
    assert 0 < counts[0] == counts[1] <= 12


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
