"""Behavior and parity tests for the opt-in worktree-focus sessionStart hook."""

from __future__ import annotations

from functools import cache
import importlib.util
import json
import os
import shlex
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
BASH_HOOK = PLUGIN / "scripts" / "focus-guidance.sh"
POWERSHELL_HOOK = PLUGIN / "scripts" / "focus-guidance.ps1"
HOOKS = PLUGIN / "hooks.json"
CONFIG = Path(".agent-dispatch/session-guidance.json")
VERSION = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))["version"]
KERNEL = (
    f"[owner: agent-dispatch@{VERSION}]\n"
    "Before choosing or starting new work, use the agent-dispatch session "
    "command catalog's exact `argv[0]` with `worktree-status`; resume or claim "
    "work explicitly targeted at this worktree before self-selecting unless it "
    "conflicts with the operator's current request. Before starting work likely "
    "to overlap another worktree, use the "
    "agent-dispatch session command catalog's exact `argv[0]` with "
    "`focus --list`. At the start of substantial operator-led or "
    "task-less work, and when its direction changes, advertise it early with "
    "that same command plus `focus \"<one-line subject>\"`; this is shorthand for writing "
    "the same agent-worktrees status-core summary, not a separate store. "
    "Agent-worktrees conduct and regular `agent-worktrees status --summary` remain "
    "authoritative for ongoing disposition, and their normal update cadence still "
    "applies."
)
EXPECTED = json.dumps(
    {"additionalContext": KERNEL},
    ensure_ascii=False,
    separators=(",", ":"),
)


def _powershell() -> str | None:
    if os.name == "nt":
        return shutil.which("pwsh") or shutil.which("powershell.exe")
    return shutil.which("pwsh")


def _hooks() -> list[Path]:
    powershell = _powershell()
    if os.name == "nt":
        assert powershell, "PowerShell is required for native-Windows hook tests"
        return [POWERSHELL_HOOK]
    hooks = [BASH_HOOK]
    if powershell:
        hooks.append(POWERSHELL_HOOK)
    return hooks


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_command_shim(path: Path, command: str, target: str) -> None:
    if os.name == "nt":
        (path / f"{command}.cmd").write_text(
            f'@echo off\r\n"{target}" %*\r\nexit /b %errorlevel%\r\n',
            encoding="utf-8",
        )
        return
    _write_executable(
        path / command,
        f"#!/bin/sh\nexec {shlex.quote(target)} \"$@\"\n",
    )


@cache
def _prepared_uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("prepared managed bootstrap requires uv")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "PYTHON")) and key not in {"VIRTUAL_ENV", "CONDA_PREFIX"}
    }
    if "UV_PYTHON_INSTALL_DIR" in os.environ:
        environment["UV_PYTHON_INSTALL_DIR"] = os.environ["UV_PYTHON_INSTALL_DIR"]
    result = subprocess.run(
        [uv, "python", "find", "--no-project", "--no-config", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        env=environment, capture_output=True, text=True, timeout=10,
    )
    if result.returncode:
        pytest.skip("managed CPython 3.11.18 is not prepared; no acquisition attempted")
    return uv


def _tool_path(
    path: Path,
    *,
    managed: bool = True,
    include_aw: bool = True,
    status_core: bool = True,
) -> Path:
    path.mkdir(parents=True)
    tool_names = ("git", "python") if os.name == "nt" else ("git", "python3")
    for command in tool_names:
        target = shutil.which(command)
        assert target
        _write_command_shim(path, command, target)
    if os.name != "nt":
        _write_command_shim(path, "uv", _prepared_uv())
    if include_aw:
        if os.name == "nt":
            project = "echo example-project" if managed else "exit /b 1"
            status = "exit /b 0" if status_core else "exit /b 1"
            (path / "agent-worktrees.cmd").write_text(
                "@echo off\r\n"
                "for %%V in (GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE "
                "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES "
                "GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM "
                "GIT_PREFIX GIT_SUPER_PREFIX GIT_QUARANTINE_PATH GIT_NAMESPACE "
                "GIT_CONFIG GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL "
                "GIT_CONFIG_NOSYSTEM GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 "
                "GIT_CONFIG_VALUE_0) "
                "do if defined %%V exit /b 88\r\n"
                f'if "%1 %2"=="get project" ({project} & exit /b %errorlevel%)\r\n'
                f'if "%1 %2"=="status --help" ({status})\r\n'
                "exit /b 1\r\n",
                encoding="utf-8",
            )
        else:
            project_line = 'printf "example-project\\n"' if managed else "exit 1"
            status_line = "exit 0" if status_core else "exit 1"
            _write_executable(
                path / "agent-worktrees",
                "#!/bin/sh\n"
                'for name in GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE '
                "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES "
                "GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM "
                "GIT_PREFIX GIT_SUPER_PREFIX GIT_QUARANTINE_PATH GIT_NAMESPACE "
                "GIT_CONFIG GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL "
                "GIT_CONFIG_NOSYSTEM GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 "
                "GIT_CONFIG_VALUE_0; do\n"
                '  eval "value=\\${$name-}"; [ -z "$value" ] || exit 88\n'
                "done\n"
                'if [ "$1" = get ] && [ "$2" = project ]; then '
                f"{project_line}; exit $?; fi\n"
                'if [ "$1" = status ] && [ "$2" = --help ]; then '
                f"{status_line}; fi\n"
                "exit 1\n",
            )
    return path


@pytest.mark.skipif(os.name != "nt", reason="Windows command shim regression")
def test_tool_path_does_not_relocate_windows_executables(tmp_path: Path) -> None:
    tools = _tool_path(tmp_path / "bin")

    assert (tools / "git.cmd").is_file()
    assert (tools / "python.cmd").is_file()
    assert list(tools.glob("*.exe")) == []


def _repo(path: Path, *, enabled: object | None = True) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if enabled is not None:
        config = path / CONFIG
        config.parent.mkdir()
        config.write_text(
            json.dumps({"session_guidance": {"focus": enabled}}),
            encoding="utf-8",
        )
    return path


def _run(
    hook: Path,
    payload_repo: Path,
    tool_path: Path,
    *args: str,
    payload: str | bytes | None = None,
    process_cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if hook.suffix == ".ps1":
        host = _powershell()
        assert host
        command = [host, "-NoProfile", "-File", str(hook), *args]
    else:
        host = shutil.which("bash")
        assert host
        command = [host, str(hook), *args]
    return subprocess.run(
        command,
        cwd=process_cwd or payload_repo,
        env={
            **os.environ,
            "PATH": os.pathsep.join((str(tool_path), os.defpath)),
            **(env_overrides or {}),
        },
        input=(
            payload.encode()
            if isinstance(payload, str)
            else payload
            if payload is not None
            else json.dumps(
                {"cwd": str(payload_repo), "source": "copilot-cli"}
            ).encode()
        ),
        capture_output=True,
        check=True,
    )


def _hook_with_manifest(
    tmp_path: Path, hook: Path, manifest: object | bytes | None
) -> Path:
    plugin = tmp_path / hook.stem / "plugin"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    copied = scripts / hook.name
    shutil.copy2(hook, copied)
    if hook.suffix == ".sh":
        shutil.copy2(PLUGIN / "scripts/bootstrap-python.sh", scripts)
        spec = importlib.util.spec_from_file_location(
            "_focus_bootstrap_producer", REPO / "libs/payload-invocation/bootstrap_python.py",
        )
        assert spec and spec.loader
        producer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(producer)
        declaration = PLUGIN / "payload-invocation.json"
        data = json.loads(declaration.read_text(encoding="utf-8"))
        generated: dict[Path, str | bytes] = producer.expected_files(declaration, data)
        for source, content in generated.items():
            destination = plugin / source.relative_to(PLUGIN)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    if manifest is not None:
        content = manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode()
        (plugin / "plugin.json").write_bytes(content)
    return copied


@pytest.mark.skipif(os.name == "nt", reason="POSIX focus reader")
@pytest.mark.parametrize("layout", ["source", "materialized"])
@pytest.mark.parametrize("available", [True, False])
def test_focus_reader_uses_one_prepared_bootstrap_without_ambient_python(
    tmp_path: Path, layout: str, available: bool,
    record_property: Callable[[str, object], None],
) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    calls = tmp_path / "uv-calls"
    poison = tmp_path / "ambient-python-called"
    for name in ("python", "python3", "py"):
        _write_executable(
            tools / name, f"#!/bin/sh\n: > {shlex.quote(str(poison))}\nexit 97\n",
        )
    uv_command = f'exec {shlex.quote(_prepared_uv())} "$@"' if available else "exit 42"
    _write_executable(
        tools / "uv",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n{uv_command}\n",
    )
    hook = (
        BASH_HOOK if layout == "source"
        else _hook_with_manifest(tmp_path, BASH_HOOK, {"version": VERSION})
    )
    samples = []
    for _ in range(2 if available else 1):
        started = time.perf_counter()
        result = _run(
            hook, repo, tools,
            env_overrides={
                "UV_PYTHON": "3.9", "UV_NO_MANAGED_PYTHON": "true",
                "VIRTUAL_ENV": str(tmp_path / "foreign-venv"),
                "PYTHONHOME": str(tmp_path / "foreign-home"),
                "PYTHONPATH": str(tmp_path / "foreign-modules"),
                "UV_OFFLINE": "1",
            },
        )
        samples.append(time.perf_counter() - started)
        assert result.stdout == (EXPECTED.encode() if available else b"{}"), result
        assert result.stderr == b""
    assert not poison.exists()
    observed = calls.read_text(encoding="utf-8").splitlines()
    expected_calls = ["--version"]
    if available:
        expected_calls.append(
            "python find --no-project --system --managed-python "
            "--no-python-downloads cpython@3.11.18"
        )
    assert observed == expected_calls * len(samples)
    record_property("dispatch_focus_admission", json.dumps({
        "layout": layout, "available": available, "samples_seconds": samples,
        "uv_calls": len(observed), "acquisition": "disabled",
    }, sort_keys=True))


@pytest.mark.parametrize("enabled", [None, False, "true", 1, "yes", [], {}])
def test_absent_or_disabled_or_non_boolean_opt_in_emits_empty(
    tmp_path: Path, enabled: object | None
) -> None:
    repo = _repo(tmp_path / "repo", enabled=enabled)
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        result = _run(hook, repo, tools)
        assert result.stdout == b"{}"
        assert result.stderr == b""


def test_enabled_opt_in_emits_exact_bounded_owned_kernel(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    results = [_run(hook, repo, tools) for hook in _hooks()]
    for result in results:
        assert result.stdout.decode() == EXPECTED
        assert result.stderr == b""
        assert len(json.loads(result.stdout)["additionalContext"].encode()) < 1024
        assert json.loads(result.stdout)["additionalContext"].count(
            f"[owner: agent-dispatch@{VERSION}]"
        ) == 1
        kernel = json.loads(result.stdout)["additionalContext"]
        assert "`worktree-status`" in kernel
        assert "resume or claim work explicitly targeted" in kernel
        assert (
            "Before starting work likely to overlap another worktree, use the "
            "agent-dispatch session command catalog's exact `argv[0]` with "
            "`focus --list`."
        ) in kernel
        assert "advertise it early" in kernel
        assert "same agent-worktrees status-core summary" in kernel
        assert "normal update cadence still applies" in kernel
    if len(results) == 2:
        assert results[1].stdout == results[0].stdout


def test_aggregate_mode_is_compact_and_keeps_focus_invariants(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    results = [_run(hook, repo, tools, "--aggregate") for hook in _hooks()]
    contexts = [json.loads(result.stdout)["additionalContext"] for result in results]
    for context in contexts:
        assert context.startswith(f"[owner: agent-dispatch@{VERSION}]\n")
        assert "`worktree-status`" in context
        assert "`focus --list`" in context
        assert "Agent-worktrees status remains authoritative" in context
        assert "`agent-dispatch:pick-and-claim` skill" in context
        assert len(context.encode("utf-8")) <= 384
    assert len(set(contexts)) == 1


def test_owner_marker_is_derived_from_adjacent_manifest(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    version = "9.8.7-dev654"
    for hook in _hooks():
        copied = _hook_with_manifest(
            tmp_path / hook.suffix.removeprefix("."),
            hook,
            {"name": "agent-dispatch", "version": version},
        )
        kernel = json.loads(_run(copied, repo, tools).stdout)["additionalContext"]
        assert kernel.startswith(f"[owner: agent-dispatch@{version}]\n")


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        b"{",
        [{"version": "1.2.3"}],
        [{"version": "1.2.3"}, {"version": "1.2.4"}],
        {"version": 1},
        {"version": "not-a-version"},
        {"version": "1.2.3-dev" + ("1" * 65)},
        b'{"version":"1.2.3"}\x00',
        b"x" * 4097,
    ],
)
def test_missing_malformed_or_invalid_manifest_fails_open(
    tmp_path: Path, manifest: object | bytes | None
) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        copied = _hook_with_manifest(
            tmp_path / hook.suffix.removeprefix("."),
            hook,
            manifest,
        )
        result = _run(copied, repo, tools)
        assert result.stdout == b"{}"
        assert result.stderr == b""


def test_absent_agent_worktrees_emits_empty(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin", include_aw=False)
    for hook in _hooks():
        assert _run(hook, repo, tools).stdout == b"{}"


def test_unmanaged_repo_emits_empty(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin", managed=False)
    for hook in _hooks():
        assert _run(hook, repo, tools).stdout == b"{}"


def test_missing_status_core_emits_empty(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin", status_core=False)
    for hook in _hooks():
        assert _run(hook, repo, tools).stdout == b"{}"


def test_aggregate_mode_skips_expensive_status_capability_probe(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin", status_core=False)
    for hook in _hooks():
        context = json.loads(
            _run(hook, repo, tools, "--aggregate").stdout
        )["additionalContext"]
        assert context.startswith(f"[owner: agent-dispatch@{VERSION}]\n")


@pytest.mark.parametrize(
    "payload",
    ["", "{", "[]", '{"source":"copilot-cli"}', '{"cwd":"relative"}'],
)
def test_missing_or_malformed_payload_fails_open(
    tmp_path: Path, payload: str
) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        result = _run(hook, repo, tools, payload=payload)
        assert result.stdout == b"{}"
        assert result.stderr == b""


def test_malformed_config_fails_open(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / CONFIG).write_text("{", encoding="utf-8")
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        assert _run(hook, repo, tools).stdout == b"{}"


@pytest.mark.parametrize(
    "config",
    [
        {"session_guidance": {"focus": True, "future": False}},
        {"session_guidance": {"focus": True}, "extra": {}},
        {"session_guidance": True},
        {"session_guidance": []},
        [],
    ],
)
def test_non_exact_config_shape_fails_open(tmp_path: Path, config: object) -> None:
    repo = _repo(tmp_path / "repo", enabled=None)
    target = repo / CONFIG
    target.parent.mkdir()
    target.write_text(json.dumps(config), encoding="utf-8")
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        assert _run(hook, repo, tools).stdout == b"{}"


def test_config_parent_symlink_or_reparse_fails_open(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", enabled=None)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / CONFIG.name).write_text(
        json.dumps({"session_guidance": {"focus": True}}),
        encoding="utf-8",
    )
    try:
        (repo / CONFIG.parent).symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks/reparse points unavailable: {error}")
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        assert _run(hook, repo, tools).stdout == b"{}"


def test_config_file_symlink_or_reparse_fails_open(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", enabled=None)
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"session_guidance": {"focus": True}}),
        encoding="utf-8",
    )
    target = repo / CONFIG
    target.parent.mkdir()
    try:
        target.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"file symlinks/reparse points unavailable: {error}")
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        assert _run(hook, repo, tools).stdout == b"{}"


@pytest.mark.parametrize("content", [b"x" * 4097, b'{"session_guidance":\x00{}}', b"\xff"])
def test_config_read_is_bounded_strict_utf8_and_nul_free(
    tmp_path: Path, content: bytes
) -> None:
    repo = _repo(tmp_path / "repo", enabled=None)
    target = repo / CONFIG
    target.parent.mkdir()
    target.write_bytes(content)
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        assert _run(hook, repo, tools).stdout == b"{}"


def test_repo_root_equal_to_home_does_not_consume_machine_config(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "home", enabled=None)
    machine_config = repo / ".agent-dispatch/config.json"
    machine_config.parent.mkdir()
    machine_config.write_text(
        json.dumps({"session_guidance": {"focus": True}}),
        encoding="utf-8",
    )
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        assert (
            _run(hook, repo, tools, env_overrides={"HOME": str(repo)}).stdout
            == b"{}"
        )


def test_contaminated_git_environment_is_isolated_and_restored(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    poison = tmp_path / "not-a-repo"
    poison.mkdir()
    contaminated = {
        "GIT_DIR": str(poison),
        "GIT_WORK_TREE": str(poison),
        "GIT_COMMON_DIR": str(poison),
        "GIT_INDEX_FILE": str(poison / "index"),
        "GIT_OBJECT_DIRECTORY": str(poison / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(poison / "alternate"),
        "GIT_CEILING_DIRECTORIES": str(repo),
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "false",
        "GIT_PREFIX": "poison/",
        "GIT_SUPER_PREFIX": "poison/",
        "GIT_QUARANTINE_PATH": str(poison / "quarantine"),
        "GIT_NAMESPACE": "poison",
        "GIT_CONFIG": str(poison / "config"),
        "GIT_CONFIG_SYSTEM": str(poison / "system-config"),
        "GIT_CONFIG_GLOBAL": str(poison / "global-config"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "remote.origin.url",
        "GIT_CONFIG_VALUE_0": "https://example.invalid/poison.git",
    }
    for hook in _hooks():
        result = _run(hook, repo, tools, env_overrides=contaminated)
        assert result.stdout.decode() == EXPECTED


@pytest.mark.parametrize("size,expected", [(65536, EXPECTED.encode()), (65537, b"{}")])
def test_payload_raw_byte_limit_parity(
    tmp_path: Path, size: int, expected: bytes
) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    base = json.dumps({"cwd": str(repo), "source": "copilot-cli"}).encode()
    assert len(base) < size
    payload = base + (b" " * (size - len(base)))
    for hook in _hooks():
        assert _run(hook, repo, tools, payload=payload).stdout == expected


@pytest.mark.parametrize("payload", [b'{"cwd":"\xff"}', b'{"cwd":"x\x00"}'])
def test_payload_requires_strict_utf8_and_rejects_nul(
    tmp_path: Path, payload: bytes
) -> None:
    repo = _repo(tmp_path / "repo")
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        assert _run(hook, repo, tools, payload=payload).stdout == b"{}"


def test_payload_cwd_is_authoritative_when_process_cwd_differs(
    tmp_path: Path,
) -> None:
    payload_repo = _repo(tmp_path / "payload-repo")
    process_repo = _repo(tmp_path / "process-repo", enabled=None)
    tools = _tool_path(tmp_path / "bin")
    for hook in _hooks():
        assert (
            _run(hook, payload_repo, tools, process_cwd=process_repo).stdout
            .decode()
            == EXPECTED
        )


def test_hook_registration_is_separate_from_bootstrap_contract() -> None:
    entries = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]["sessionStart"]
    assert len(entries) == 3
    assert "bootstrap-check" in entries[0]["bash"]
    assert "bootstrap-check" in entries[0]["powershell"]
    assert "COPILOT_PLUGIN_ROOT" in entries[0]["bash"]
    assert "COPILOT_PLUGIN_ROOT" in entries[0]["powershell"]
    assert "else printf '{}'" in entries[0]["bash"]
    assert "else { [Console]::Out.Write('{}') }" in entries[0]["powershell"]
    assert "register-bridge-provider" in entries[1]["bash"]
    assert "register-bridge-provider" in entries[1]["powershell"]
    assert "write-session-guidance" in entries[2]["bash"]
    assert "write-session-guidance" in entries[2]["powershell"]
    assert "COPILOT_PLUGIN_ROOT" in entries[2]["bash"]
    assert "COPILOT_PLUGIN_ROOT" in entries[2]["powershell"]
    assert "else printf '{}'" in entries[2]["bash"]
    assert "else { [Console]::Out.Write('{}') }" in entries[2]["powershell"]
    assert "focus-guidance" not in json.dumps(entries)
    assert "emit-command-catalog" not in json.dumps(entries)
    for bootstrap in (
        PLUGIN / "scripts" / "bootstrap-check.sh",
        PLUGIN / "scripts" / "bootstrap-check.ps1",
    ):
        text = bootstrap.read_text(encoding="utf-8")
        assert "agent-dispatch@" not in text
        assert "additionalContext" not in text


def test_tracked_repo_opt_in_uses_documented_exact_filename_and_key() -> None:
    assert CONFIG.as_posix() == ".agent-dispatch/session-guidance.json"
    config = json.loads((REPO / CONFIG).read_text())
    assert config == {"session_guidance": {"focus": True}}
