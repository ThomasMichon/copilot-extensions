"""Behavior and parity tests for the efforts policy producer."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
BASH_PRODUCER = PLUGIN / "scripts" / "emit-policy.sh"
POWERSHELL_PRODUCER = PLUGIN / "scripts" / "emit-policy.ps1"
PLUGIN_MANIFEST = PLUGIN / "plugin.json"
SETUP_SKILL = PLUGIN / "skills" / "efforts-setup" / "SKILL.md"
README = PLUGIN / "README.md"
ROOT = PLUGIN.parents[1]
ROOT_INSTRUCTIONS = ROOT / "AGENTS.md"
CONFIG = Path(".copilot-extensions/efforts/config.json")


def _powershell() -> str | None:
    if os.name == "nt":
        return shutil.which("pwsh") or shutil.which("powershell.exe")
    return shutil.which("pwsh")


def _producers() -> list[Path]:
    if os.name == "nt":
        powershell = _powershell()
        assert powershell
        return [POWERSHELL_PRODUCER]
    result = [BASH_PRODUCER]
    if _powershell():
        result.append(POWERSHELL_PRODUCER)
    return result


def _repo(path: Path, config: object | bytes | None = None) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if config is not None:
        target = path / CONFIG
        target.parent.mkdir(parents=True)
        target.write_bytes(
            config if isinstance(config, bytes) else json.dumps(config).encode()
        )
    return path


def _run(
    producer: Path,
    repo: Path,
    *,
    payload: str | bytes | None = None,
    payload_cwd: Path | None = None,
    process_cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if producer.suffix == ".ps1":
        command = [_powershell(), "-NoProfile", "-File", str(producer)]
    else:
        command = ["bash", str(producer)]
    assert all(command)
    environment = os.environ.copy()
    environment.update(env_overrides or {})
    if payload is None:
        payload = json.dumps(
            {"cwd": str(payload_cwd or repo), "source": "copilot-cli"}
        )
    return subprocess.run(
        command,
        cwd=process_cwd or repo,
        env=environment,
        input=payload.encode() if isinstance(payload, str) else payload,
        capture_output=True,
        check=True,
    )


def _context(result: subprocess.CompletedProcess[bytes]) -> str:
    payload = json.loads(result.stdout)
    assert set(payload) == {"additionalContext"}
    return payload["additionalContext"]


def test_exact_adoption_config_emits_bounded_owned_policy(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    version = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))["version"]
    results = [_run(producer, repo) for producer in _producers()]
    contexts = [_context(result) for result in results]
    for context, result in zip(contexts, results, strict=True):
        assert context.startswith(f"[owner: efforts@{version}]\n")
        assert "use `planning-efforts` to create or resume" in context
        assert "Only the rightful head drives" in context
        assert "A completed phase, PR, handoff, or session is not completion" in context
        assert "required safety/admin confirmation" in context
        assert "one target-owned sub-effort referenced one-way" in context
        assert len(context.encode("utf-8")) < 900
        assert len(context.encode("utf-8")) < 1024
        assert result.stderr == b""
    assert len(set(contexts)) == 1


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"version": 1},
        {"enforcement": "required"},
        {"version": 1, "enforcement": "advisory"},
        {"version": 2, "enforcement": "required"},
        {"version": True, "enforcement": "required"},
        {"version": "1", "enforcement": "required"},
        {"version": 1, "enforcement": "required", "extra": False},
        [],
        b"{",
        b'{"version":1,"enforcement":"required",}',
        b'{"version":1,/*comment*/"enforcement":"required"}',
        b'[{"version":1,"enforcement":"required"}]',
        b'{"version":1,"version":1,"enforcement":"required"}',
        b'{"version":1,"enforcement":"required"}\x00',
        b"x" * 4097,
    ],
)
def test_absent_or_invalid_config_emits_empty(
    tmp_path: Path,
    config: object | bytes | None,
) -> None:
    repo = _repo(tmp_path / "repo", config)
    for producer in _producers():
        assert _run(producer, repo).stdout == b"{}"


def test_nested_payload_cwd_uses_repository_config(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    child = repo / "a" / "b"
    child.mkdir(parents=True)
    for producer in _producers():
        assert _context(_run(producer, repo, payload_cwd=child)).startswith(
            "[owner: efforts@"
        )


def test_payload_cwd_is_authoritative_when_process_cwd_differs(
    tmp_path: Path,
) -> None:
    process_repo = _repo(tmp_path / "process")
    payload_repo = _repo(
        tmp_path / "payload",
        {"version": 1, "enforcement": "required"},
    )
    for producer in _producers():
        assert _context(
            _run(
                producer,
                payload_repo,
                payload_cwd=payload_repo,
                process_cwd=process_repo,
            )
        ).startswith("[owner: efforts@")


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{",
        "[]",
        '{"source":"copilot-cli"}',
        '{"cwd":"relative"}',
        '{"cwd":"/path/to/repo\\nchild"}',
        '[{"cwd":"/path/to/repo"}]',
        '{"cwd":"/path/to/repo",}',
        '{"cwd":"/path/to/repo","cwd":"/path/to/repo"}',
        "x" * 65537,
        b'{"cwd":"/path/to/repo"}\x00',
        b'{"cwd":"/path/to/repo"}\xff',
    ],
)
def test_malformed_payload_fails_open_once(
    tmp_path: Path,
    payload: str | bytes,
) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    for producer in _producers():
        result = _run(producer, repo, payload=payload)
        assert result.stdout == b"{}"
        assert result.stderr.count(b"\n") <= 1


def test_contaminated_git_environment_is_ignored(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    for producer in _producers():
        result = _run(
            producer,
            repo,
            env_overrides={
                "GIT_DIR": str(tmp_path / "missing"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.bare",
                "GIT_CONFIG_VALUE_0": "true",
            },
        )
        assert _context(result).startswith("[owner: efforts@")


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake executable")
def test_git_timeout_fails_open(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nwhile :; do :; done\n", encoding="utf-8")
    fake_git.chmod(0o755)
    environment = {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    for producer in _producers():
        result = _run(producer, repo, env_overrides=environment)
        assert result.stdout == b"{}"
        assert result.stderr.count(b"\n") <= 1


def test_symlinked_config_or_parent_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"version":1,"enforcement":"required"}', encoding="utf-8")

    file_repo = _repo(tmp_path / "file-repo")
    file_target = file_repo / CONFIG
    file_target.parent.mkdir(parents=True)
    try:
        file_target.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    try:
        parent_repo = _repo(tmp_path / "parent-repo")
        external = tmp_path / "external"
        (external / "efforts").mkdir(parents=True)
        (external / "efforts" / "config.json").write_bytes(source.read_bytes())
        (parent_repo / ".copilot-extensions").symlink_to(
            external,
            target_is_directory=True,
        )
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    for producer in _producers():
        assert _run(producer, file_repo).stdout == b"{}"
        assert _run(producer, parent_repo).stdout == b"{}"


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        {"version": "bad"},
        {"version": "1.2.3\n"},
        [{"version": "9.9.9"}],
        b'{"version":"9.9.9",}',
        b'{"version":"9.9.9","version":"9.9.9"}',
    ],
)
def test_missing_or_malformed_manifest_fails_open(
    tmp_path: Path,
    manifest: object | bytes | None,
) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    copied_plugin = tmp_path / "plugin"
    shutil.copytree(PLUGIN / "scripts", copied_plugin / "scripts")
    copied = [
        copied_plugin / "scripts" / producer.name for producer in _producers()
    ]
    if manifest is not None:
        (copied_plugin / "plugin.json").write_bytes(
            manifest
            if isinstance(manifest, bytes)
            else json.dumps(manifest).encode("utf-8")
        )
    for producer in copied:
        assert _run(producer, repo).stdout == b"{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink spelling test")
def test_symlinked_repository_ancestor_preserves_parity(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    repo = _repo(
        real_parent / "repo",
        {"version": 1, "enforcement": "required"},
    )
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    linked_repo = linked_parent / "repo"
    contexts = [
        _context(_run(producer, repo, payload_cwd=linked_repo))
        for producer in _producers()
    ]
    assert len(set(contexts)) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX wrapper behavior")
def test_posix_wrapper_contains_invalid_interpreter_stdout(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf 'Python was not found; install it from the app store\\n'\n"
        "exit 49\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    result = _run(
        BASH_PRODUCER,
        repo,
        env_overrides={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )
    assert result.stdout == b"{}"
    assert result.stderr.count(b"\n") == 1
    assert b"policy producer failed" in result.stderr


def test_powershell_parity_lane_is_explicit() -> None:
    if _powershell() is None:
        pytest.skip("PowerShell unavailable; live producer parity unverified")


def test_setup_contract_matches_manifest_and_defers_hook_registration() -> None:
    version = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))["version"]
    setup = SETUP_SKILL.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    root_instructions = ROOT_INSTRUCTIONS.read_text(encoding="utf-8")
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    assert f"[owner: efforts@{version}]" in setup
    assert '{"version": 1, "enforcement": "required"}' in setup
    assert setup.count("<!-- efforts:static-fallback:start -->") == 1
    assert setup.count("<!-- efforts:static-fallback:end -->") == 1
    assert "not declare the worktree complete" in setup
    assert "issue #1234" in setup
    assert root_instructions.count("<!-- efforts:static-fallback:start -->") == 1
    assert root_instructions.count("<!-- efforts:static-fallback:end -->") == 1
    assert f"[owner: efforts@{version}]" in root_instructions
    assert "hooks" not in manifest
    assert "#1234" in readme
