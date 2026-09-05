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
PROJECTION_DECLARATION = PLUGIN / "instruction-projections.json"
PROJECTION_TEMPLATE = PLUGIN / "instructions" / "completion-gate.instructions.md"
PLANNING_SKILL = PLUGIN / "skills" / "planning-efforts" / "SKILL.md"
PLANNING_REFERENCE = (
    PLUGIN / "skills" / "planning-efforts" / "references" / "efforts.md"
)
README = PLUGIN / "README.md"
ROOT = PLUGIN.parents[1]
ROOT_INSTRUCTIONS = ROOT / "AGENTS.md"
ROOT_PROJECTION = (
    ROOT
    / ".github"
    / "instructions"
    / "efforts"
    / "completion-gate.instructions.md"
)
WORKING_CROSS_REPO = (
    ROOT / "plugins" / "agent-worktrees" / "skills" / "working-cross-repo" / "SKILL.md"
)
CONFIG = Path(".copilot-extensions/efforts/config.json")
ADOPTION_CAPABILITY = {
    "version": 1,
    "capability": "efforts",
    "adopted": True,
}


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


def _commit(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )


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
        timeout=10,
    )


def _context(result: subprocess.CompletedProcess[bytes]) -> str:
    payload = json.loads(result.stdout)
    assert set(payload) == {"additionalContext"}
    return payload["additionalContext"]


def _check_adoption(
    producer: Path,
    target: Path | str,
    *,
    extra_args: tuple[str, ...] = (),
    process_cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if producer.suffix == ".ps1":
        command = [
            _powershell(),
            "-NoProfile",
            "-File",
            str(producer),
            "-CheckAdoption",
            str(target),
            *extra_args,
        ]
    else:
        command = [
            "bash",
            str(producer),
            "--check-adoption",
            str(target),
            *extra_args,
        ]
    assert all(command)
    environment = os.environ.copy()
    environment.update(env_overrides or {})
    return subprocess.run(
        command,
        cwd=process_cwd or PLUGIN,
        env=environment,
        input=b"",
        capture_output=True,
        check=True,
        timeout=10,
    )


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
        assert "host-owned by default" in context
        assert "explicitly selected target-owned sub-effort" in context
        assert len(context.encode("utf-8")) < 900
        assert len(context.encode("utf-8")) < 1024
        assert result.stderr == b""
    assert len(set(contexts)) == 1


def test_read_only_probe_reports_exact_compatible_capability(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    _commit(repo)
    child = repo / "nested"
    child.mkdir()
    results = [_check_adoption(producer, child) for producer in _producers()]
    assert [json.loads(result.stdout) for result in results] == [
        ADOPTION_CAPABILITY
    ] * len(results)
    assert all(result.stderr == b"" for result in results)


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


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"version": 1, "enforcement": "advisory"},
        {"version": 2, "enforcement": "required"},
        {"version": 1, "enforcement": "required", "extra": True},
        b"{",
        b'{"version":1,"enforcement":"required",}',
        b'{"version":1,"version":1,"enforcement":"required"}',
    ],
)
def test_read_only_probe_rejects_absent_or_malformed_adoption(
    tmp_path: Path,
    config: object | bytes | None,
) -> None:
    repo = _repo(tmp_path / "repo", config)
    if config is not None:
        _commit(repo)
    for producer in _producers():
        assert _check_adoption(producer, repo).stdout == b"{}"


def test_read_only_probe_rejects_uncommitted_or_locally_enabled_adoption(
    tmp_path: Path,
) -> None:
    uncommitted = _repo(
        tmp_path / "uncommitted",
        {"version": 1, "enforcement": "required"},
    )
    locally_enabled = _repo(
        tmp_path / "locally-enabled",
        {"version": 1, "enforcement": "advisory"},
    )
    _commit(locally_enabled)
    (locally_enabled / CONFIG).write_text(
        '{"version":1,"enforcement":"required"}',
        encoding="utf-8",
    )
    for producer in _producers():
        assert _check_adoption(producer, uncommitted).stdout == b"{}"
        assert _check_adoption(producer, locally_enabled).stdout == b"{}"


def test_read_only_probe_requires_authoritative_local_checkout(
    tmp_path: Path,
) -> None:
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    adopting_repo = _repo(
        tmp_path / "adopting",
        {"version": 1, "enforcement": "required"},
    )
    _commit(adopting_repo)
    for producer in _producers():
        assert _check_adoption(producer, non_repo).stdout == b"{}"
        assert _check_adoption(producer, "relative/repo").stdout == b"{}"
        assert _check_adoption(
            producer,
            adopting_repo,
            extra_args=("unexpected",),
        ).stdout == b"{}"
        if producer.suffix == ".ps1":
            positional = subprocess.run(
                [
                    _powershell(),
                    "-NoProfile",
                    "-File",
                    str(producer),
                    str(adopting_repo),
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )
            abbreviated = subprocess.run(
                [
                    _powershell(),
                    "-NoProfile",
                    "-File",
                    str(producer),
                    "-CheckA",
                    str(adopting_repo),
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )
            assert positional.stdout == b"{}"
            assert abbreviated.stdout == b"{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission fixture")
def test_read_only_probe_fails_closed_for_inaccessible_target(
    tmp_path: Path,
) -> None:
    restricted = tmp_path / "restricted"
    repo = _repo(
        restricted / "repo",
        {"version": 1, "enforcement": "required"},
    )
    _commit(repo)
    restricted.chmod(0)
    try:
        for producer in _producers():
            result = _check_adoption(producer, repo)
            assert result.stdout == b"{}"
            assert b"Traceback" not in result.stderr
            assert result.stderr.count(b"\n") <= 1
    finally:
        restricted.chmod(0o700)


def test_read_only_probe_does_not_execute_target_content(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    _commit(repo)
    marker = repo / "target-code-ran"
    target_code = repo / "efforts" / "probe.py"
    target_code.parent.mkdir()
    target_code.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    for producer in _producers():
        assert json.loads(_check_adoption(producer, repo).stdout) == ADOPTION_CAPABILITY
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake executable")
def test_read_only_probe_disables_git_indirection(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    _commit(repo)
    marker = tmp_path / "unsafe-git-environment"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    real_git = shutil.which("git")
    assert real_git
    fake_git.write_text(
        "#!/bin/sh\n"
        'if [ "$GIT_NO_LAZY_FETCH" != "1" ] || '
        '[ "$GIT_NO_REPLACE_OBJECTS" != "1" ]; then\n'
        '  printf unsafe > "$EFFORTS_TEST_MARKER"\n'
        "fi\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    environment = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "EFFORTS_TEST_MARKER": str(marker),
    }
    for producer in _producers():
        assert json.loads(
            _check_adoption(
                producer,
                repo,
                env_overrides=environment,
            ).stdout
        ) == ADOPTION_CAPABILITY
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture")
def test_read_only_probe_rejects_committed_symlink_config(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    config = repo / CONFIG
    config.parent.mkdir(parents=True)
    try:
        config.symlink_to('{"version":1,"enforcement":"required"}')
    except OSError:
        pytest.skip("symlink creation is unavailable")
    _commit(repo)
    config.unlink()
    config.write_text(
        '{"version":1,"enforcement":"required"}',
        encoding="utf-8",
    )
    for producer in _producers():
        assert _check_adoption(producer, repo).stdout == b"{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO fixture")
def test_read_only_probe_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    _commit(repo)
    config = repo / CONFIG
    config.unlink()
    os.mkfifo(config)
    for producer in _producers():
        environment = {"OS": "Windows_NT"} if producer.suffix == ".ps1" else None
        assert _check_adoption(
            producer,
            repo,
            env_overrides=environment,
        ).stdout == b"{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO fixture")
def test_session_policy_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "required"},
    )
    config = repo / CONFIG
    config.unlink()
    os.mkfifo(config)
    for producer in _producers():
        result = _run(producer, repo)
        assert result.stdout == b"{}"
        assert result.stderr.count(b"\n") <= 1


def test_read_only_probe_ignores_replacement_commits(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        {"version": 1, "enforcement": "advisory"},
    )
    _commit(repo)
    base_branch = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    base_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-qb", "required-replacement"],
        check=True,
    )
    (repo / CONFIG).write_text(
        '{"version":1,"enforcement":"required"}',
        encoding="utf-8",
    )
    _commit(repo)
    replacement_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", base_branch],
        check=True,
    )
    (repo / CONFIG).write_text(
        '{"version":1,"enforcement":"required"}',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(repo), "replace", base_commit, replacement_commit],
        check=True,
    )
    for producer in _producers():
        assert _check_adoption(producer, repo).stdout == b"{}"


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
    ids=lambda payload: f"{type(payload).__name__}-{len(payload)}",
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
    _commit(repo)
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
        assert json.loads(
            _check_adoption(
                producer,
                repo,
                env_overrides={
                    "GIT_DIR": str(tmp_path / "missing"),
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "core.bare",
                    "GIT_CONFIG_VALUE_0": "true",
                },
            ).stdout
        ) == ADOPTION_CAPABILITY


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
    _commit(repo)
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
    for producer in _producers():
        assert _check_adoption(producer, linked_repo).stdout == b"{}"


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


def test_setup_contract_projects_fallback_and_defers_hook_registration() -> None:
    version = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))["version"]
    setup = SETUP_SKILL.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    root_instructions = ROOT_INSTRUCTIONS.read_text(encoding="utf-8")
    root_projection = ROOT_PROJECTION.read_text(encoding="utf-8")
    template = PROJECTION_TEMPLATE.read_text(encoding="utf-8")
    declaration = json.loads(PROJECTION_DECLARATION.read_text(encoding="utf-8"))
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    assert f"[owner: efforts@{version}]" in template
    assert '{"version": 1, "enforcement": "required"}' in setup
    assert "manage-instruction-projections.py" in setup
    assert "instruction-projections.json" in setup
    assert "hand-copying the template" in setup
    assert declaration["schema"] == "copilot-extensions.instruction-projections"
    assert declaration["version"] == 1
    assert declaration["projections"][0]["legacyMarkers"] == [
        "efforts:static-fallback"
    ]
    assert "issue #1234" in setup
    assert "efforts:static-fallback:start" not in root_instructions
    assert "efforts:static-fallback:end" not in root_instructions
    assert f"[owner: efforts@{version}]" in root_projection
    assert "copilot-extension-instruction-projection" in root_projection
    assert "hooks" not in manifest
    assert "#1234" in readme


def test_cross_repository_guidance_uses_capability_before_placement() -> None:
    planning = PLANNING_SKILL.read_text(encoding="utf-8")
    reference = PLANNING_REFERENCE.read_text(encoding="utf-8")
    working = WORKING_CROSS_REPO.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for text in (planning, reference, working, readme):
        assert "--check-adoption" in text
        assert '{"version":1,"capability":"efforts","adopted":true}' in text
        assert "remote-only" in text
    assert "one-way reference" in planning
    assert "one-way reference" in reference
    assert "drifting peer copies" in planning
    assert "cyclic" in reference
    assert "later hosts" in planning
    assert "target code" in working
