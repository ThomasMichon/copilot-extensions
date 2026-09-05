"""Behavior and parity tests for the payload-only ai-attribution hook."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
BASH_HOOK = PLUGIN / "scripts" / "emit-policy.sh"
POWERSHELL_HOOK = PLUGIN / "scripts" / "emit-policy.ps1"
HOOKS = PLUGIN / "hooks.json"
SETUP_SKILL = PLUGIN / "skills" / "ai-attribution-setup" / "SKILL.md"
PROJECTION_DECLARATION = PLUGIN / "instruction-projections.json"
PROJECTION_TEMPLATE = (
    PLUGIN / "instructions" / "publication-safety.instructions.md"
)


def test_authority_resolver_matches_canonical_copy() -> None:
    canonical = (
        PLUGIN.parent
        / "context-injection"
        / "scripts"
        / "resolve_context_authority.py"
    )
    resolver = PLUGIN / "scripts" / "resolve_context_authority.py"

    assert resolver.read_bytes() == canonical.read_bytes()


def _powershell_command() -> str | None:
    if os.name == "nt":
        return shutil.which("pwsh") or shutil.which("powershell.exe")
    return shutil.which("pwsh")


def _native_hook() -> Path:
    if os.name == "nt":
        assert _powershell_command(), "PowerShell is required on Windows"
        return POWERSHELL_HOOK
    assert shutil.which("bash"), "Bash is required on POSIX"
    return BASH_HOOK


def _parity_hooks() -> list[Path]:
    hooks = [_native_hook()]
    if os.name != "nt" and shutil.which("pwsh"):
        hooks.append(POWERSHELL_HOOK)
    return hooks


def _git_repo(path: Path, remote: str | None = None) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", remote],
            check=True,
        )
    return path


def _environment(home: Path, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": (
                str(Path(sys.executable).parent)
                + os.pathsep
                + env.get("PATH", "")
            ),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
        }
    )
    env.pop("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", None)
    env.update(extra)
    return env


def _run(
    hook: Path,
    cwd: Path,
    home: Path,
    *args: str,
    payload: str | None = None,
    payload_cwd: Path | None = None,
    process_cwd: Path | None = None,
    **extra: str,
) -> subprocess.CompletedProcess[str]:
    if hook.suffix == ".ps1":
        powershell = _powershell_command()
        assert powershell
        command = [powershell, "-NoProfile", "-File", str(hook), *args]
    else:
        command = ["bash", str(hook), *args]
    environment = _environment(home, **extra)
    environment["GIT_CEILING_DIRECTORIES"] = str(cwd.parent)
    if payload is None:
        payload = json.dumps(
            {
                "cwd": str(payload_cwd or cwd),
                "source": "copilot-cli",
            }
        )
    return subprocess.run(
        command,
        cwd=process_cwd or cwd,
        env=environment,
        input=payload,
        capture_output=True,
        text=True,
        check=True,
    )


def _context(result: subprocess.CompletedProcess[str]) -> str:
    payload = json.loads(result.stdout)
    assert set(payload) == {"additionalContext"}
    return payload["additionalContext"]


def _run_hook_wrapper(
    shell_key: str,
    cwd: Path,
    home: Path,
    *,
    payload: str | None = None,
    plugin_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    hook_command = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"][
        "sessionStart"
    ][0][shell_key]
    if shell_key == "powershell":
        powershell = _powershell_command()
        assert powershell
        command = [powershell, "-NoProfile", "-Command", hook_command]
    else:
        command = ["bash", "-c", hook_command]
    environment = _environment(home)
    if plugin_root is not None:
        environment["COPILOT_PLUGIN_ROOT"] = str(plugin_root)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        input=payload or json.dumps({"cwd": str(cwd), "source": "copilot-cli"}),
        capture_output=True,
        text=True,
        check=True,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")


def _write_guide(repo: Path, relative_path: str) -> None:
    _write(repo / relative_path, "# Contribution guide\n")


def _run_bytes(
    hook: Path,
    cwd: Path,
    home: Path,
    payload: bytes,
) -> subprocess.CompletedProcess[bytes]:
    if hook.suffix == ".ps1":
        powershell = _powershell_command()
        assert powershell
        command = [powershell, "-NoProfile", "-File", str(hook)]
    else:
        command = ["bash", str(hook)]
    environment = _environment(home)
    environment["GIT_CEILING_DIRECTORIES"] = str(cwd.parent)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        input=payload,
        capture_output=True,
        check=True,
    )


def _serializer_output(hook: Path, value: str) -> str:
    env = os.environ.copy()
    env["SERIALIZER_INPUT"] = value
    if hook.suffix == ".ps1":
        powershell = _powershell_command()
        assert powershell
        command = [
            powershell,
            "-NoProfile",
            "-Command",
            f". '{hook}'; [Console]::Out.Write("
            "(ConvertTo-JsonString $env:SERIALIZER_INPUT))",
        ]
    else:
        command = [
            "bash",
            "-c",
            'source "$1"; json_escape "$SERIALIZER_INPUT"',
            "--",
            str(hook),
        ]
    return subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_no_git_gate_emits_empty_object(tmp_path: Path) -> None:
    result = _run(_native_hook(), tmp_path, tmp_path / "home")
    assert result.stdout == "{}"
    assert result.stderr == ""


def test_no_config_emits_safe_defaults(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    context = _context(_run(_native_hook(), repo, tmp_path / "home"))
    assert context.startswith(
        "[owner: ai-attribution@0.1.0-dev10] Before publishing"
    )
    assert "another party's repo require" in context
    assert "verified operator-owned repo, omit disclosure" in context
    assert "own-repo carve-out changes disclosure only" in context
    assert "persona-neutral" in context
    assert "Audit the live published surface" in context
    assert "session-start repository is unresolved" in context
    assert "re-derive ownership before publishing to any other repository" in context


def test_payload_cwd_is_authoritative_when_process_cwd_differs(
    tmp_path: Path,
) -> None:
    process_repo = _git_repo(
        tmp_path / "process-repo",
        "https://github.com/process-owner/repo.git",
    )
    payload_repo = _git_repo(
        tmp_path / "payload-repo",
        "https://github.com/example-owner/repo.git",
    )
    home = tmp_path / "home"
    _write(
        home / ".copilot" / "ai-attribution.conf",
        "owned_account=github.com/example-owner\n",
    )
    hooks = _parity_hooks()
    for hook in hooks:
        context = _context(
            _run(
                hook,
                payload_repo,
                home,
                payload_cwd=payload_repo,
                process_cwd=process_repo,
            )
        )
        assert "configured public account `github.com/example-owner`" in context
        assert "process-owner" not in context


def test_payload_cwd_decodes_json_unicode_escapes(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "r\u00e9po-\N{GRINNING FACE}")
    hooks = _parity_hooks()
    for hook in hooks:
        assert _context(_run(hook, repo, tmp_path / "home")).startswith(
            "[owner: ai-attribution@0.1.0-dev10]"
        )


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{",
        "[]",
        '{"source":"copilot-cli"}',
        '{"cwd":"/path/to/repo",}',
        '{"cwd":"/path/to/repo","extra":[1,]}',
        '{"cwd":"/path/to/repo","extra":{"value":1,}}',
        '{"cwd":"/path/to/repo","source":tru}',
        '{"cwd":"/path/to/repo","extra":{"broken":]}}',
    ],
)
def test_missing_or_malformed_payload_fails_open_once(
    tmp_path: Path,
    payload: str,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    hooks = _parity_hooks()
    for hook in hooks:
        result = _run(hook, repo, tmp_path / "home", payload=payload)
        assert result.stdout == "{}"
        assert result.stderr.count("\n") == 1
        assert "missing or malformed sessionStart payload" in result.stderr


@pytest.mark.parametrize(
    "payload",
    [
        "x" * 65537,
        '{"cwd":"/path/to/repo"}\x00',
    ],
    ids=["oversized", "nul"],
)
def test_oversized_or_nul_payload_fails_open_before_parsing(
    tmp_path: Path,
    payload: str,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    hooks = _parity_hooks()
    results = [_run(hook, repo, tmp_path / "home", payload=payload) for hook in hooks]
    for result in results:
        assert result.stdout == "{}"
        assert result.stderr.count("\n") == 1
        assert "missing or malformed sessionStart payload" in result.stderr
    if len(results) == 2:
        assert results[1].stdout == results[0].stdout
        assert results[1].stderr == results[0].stderr


def test_relative_payload_cwd_is_rejected_with_shell_parity(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    payload = json.dumps({"cwd": ".", "source": "copilot-cli"})
    hooks = _parity_hooks()
    results = [
        _run(hook, repo, tmp_path / "home", payload=payload) for hook in hooks
    ]
    for result in results:
        assert result.stdout == "{}"
        assert "missing or malformed sessionStart payload" in result.stderr
    if len(results) == 2:
        assert results[1].stderr == results[0].stderr


@pytest.mark.parametrize("escaped", [True, False])
def test_payload_cwd_rejects_escaped_and_raw_newlines_with_shell_parity(
    tmp_path: Path,
    escaped: bool,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    separator = r"\n" if escaped else "\n"
    payload = '{"cwd":"' + str(repo) + separator + 'child"}'
    results = [
        _run(hook, repo, tmp_path / "home", payload=payload)
        for hook in _parity_hooks()
    ]
    for result in results:
        assert result.stdout == "{}"
        assert "missing or malformed sessionStart payload" in result.stderr
    if len(results) == 2:
        assert results[1].stdout == results[0].stdout
        assert results[1].stderr == results[0].stderr


def test_malformed_utf8_payload_fails_open_with_shell_parity(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    payload = b'{"cwd":"' + os.fsencode(repo) + b'"}\xff'
    results = [
        _run_bytes(hook, repo, tmp_path / "home", payload)
        for hook in _parity_hooks()
    ]
    for result in results:
        assert result.stdout == b"{}"
        assert b"missing or malformed sessionStart payload" in result.stderr
    if len(results) == 2:
        assert results[1].stdout == results[0].stdout
        assert results[1].stderr == results[0].stderr


def test_canonical_personal_config_tightens_disclosure(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    _write(home / ".copilot" / "ai-attribution.conf", "disclosure=always\n")
    context = _context(_run(_native_hook(), repo, home))
    assert "every contribution" in context


@pytest.mark.parametrize(("nested_arrays", "accepted"), [(63, True), (64, False)])
def test_payload_depth_limit_has_shell_parity(
    tmp_path: Path,
    nested_arrays: int,
    accepted: bool,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    nested = "[" * nested_arrays + "0" + "]" * nested_arrays
    payload = f'{{"extra":{nested},"cwd":{json.dumps(str(repo))}}}'
    hooks = _parity_hooks()

    results = [
        _run(hook, repo, tmp_path / "home", payload=payload) for hook in hooks
    ]

    for result in results:
        if accepted:
            assert _context(result).startswith(
                "[owner: ai-attribution@0.1.0-dev10]"
            )
        else:
            assert result.stdout == "{}"
            assert "missing or malformed sessionStart payload" in result.stderr
    if len(results) == 2:
        assert results[1].stdout == results[0].stdout
        assert results[1].stderr == results[0].stderr


def test_operator_config_home_tightens_disclosure_and_classifies_owner(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo", "https://example.com/example-owner/repo.git")
    home = tmp_path / "home"
    _write(
        home / "config" / "ai-attribution" / "config.conf",
        "disclosure=always\nowned_account=example.com/example-owner\n",
    )
    context = _context(_run(_native_hook(), repo, home))
    assert "requires a prominent" in context
    assert "every contribution" in context
    assert "configured public account `example.com/example-owner`" in context
    assert "local hint is not proof" in context


@pytest.mark.parametrize("separator", [",", os.pathsep])
def test_custom_instruction_dirs_support_scanner_separators(
    tmp_path: Path,
    separator: str,
) -> None:
    repo = _git_repo(tmp_path / "repo", "git@example.com:second-owner/repo.git")
    home = tmp_path / "home"
    first = tmp_path / "first-policy"
    second = tmp_path / "second-policy"
    _write(
        first / "ai-attribution.conf",
        "owned_account=example.com/first-owner\n",
    )
    _write(
        second / "ai-attribution.conf",
        "owned_account=example.com/second-owner\n",
    )
    hooks = _parity_hooks()
    for hook in hooks:
        context = _context(
            _run(
                hook,
                repo,
                home,
                COPILOT_CUSTOM_INSTRUCTIONS_DIRS=f"{first}{separator}{second}",
            )
        )
        assert "configured public account `example.com/second-owner`" in context


def test_repo_custom_instruction_dir_cannot_self_promote(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo", "https://example.com/target-owner/repo.git")
    home = tmp_path / "home"
    policy = repo / ".operator-policy"
    _write(
        policy / "ai-attribution.conf",
        "owned_account=example.com/target-owner\n",
    )
    hooks = _parity_hooks()
    for hook in hooks:
        result = _run(
            hook,
            repo,
            home,
            COPILOT_CUSTOM_INSTRUCTIONS_DIRS=str(policy),
        )
        context = _context(result)
        assert "configured public account" not in context
        assert "No operator accounts are configured" in context
        assert "at or beneath the session-start repository" in result.stderr


@pytest.mark.parametrize(
    ("kind", "diagnostic"),
    [
        ("length", "65536-character limit"),
        ("entries", "128-entry limit"),
    ],
)
def test_custom_instruction_dirs_are_bounded_with_shell_parity(
    tmp_path: Path,
    kind: str,
    diagnostic: str,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    value = (
        "x" * 65537
        if kind == "length"
        else os.pathsep.join(["missing"] * 129)
    )
    hooks = _parity_hooks()
    results = [
        _run(
            hook,
            repo,
            tmp_path / "home",
            COPILOT_CUSTOM_INSTRUCTIONS_DIRS=value,
        )
        for hook in hooks
    ]
    for result in results:
        assert "configured public account" not in _context(result)
        assert diagnostic in result.stderr
    if len(results) == 2:
        assert results[1].stdout == results[0].stdout
        assert results[1].stderr == results[0].stderr


def test_home_rooted_repository_cannot_supply_operator_policy(
    tmp_path: Path,
) -> None:
    repo = _git_repo(
        tmp_path / "repo",
        "https://example.com/target-owner/repo.git",
    )
    _write(
        repo / ".copilot" / "ai-attribution.conf",
        "owned_account=example.com/target-owner\n",
    )
    hooks = _parity_hooks()
    for hook in hooks:
        result = _run(hook, repo, repo)
        assert "configured public account" not in _context(result)
        assert "operator config path at or beneath" in result.stderr


def test_xdg_config_home_inside_repository_cannot_supply_operator_policy(
    tmp_path: Path,
) -> None:
    repo = _git_repo(
        tmp_path / "repo",
        "https://example.com/target-owner/repo.git",
    )
    config_home = repo / "operator-config"
    _write(
        config_home / "ai-attribution" / "config.conf",
        "owned_account=example.com/target-owner\n",
    )
    hooks = _parity_hooks()
    for hook in hooks:
        result = _run(
            hook,
            repo,
            tmp_path / "home",
            XDG_CONFIG_HOME=str(config_home),
        )
        assert "configured public account" not in _context(result)
        assert "operator config path at or beneath" in result.stderr


@pytest.mark.guard
def test_powershell_appdata_config_uses_operator_path_boundary() -> None:
    source = POWERSHELL_HOOK.read_text(encoding="utf-8")
    assert "$ConfigHome = $env:APPDATA" in source
    assert (
        "Read-OperatorConfig (Join-Path $ConfigHome 'ai-attribution') "
        "'config.conf'"
    ) in source


def test_repo_additive_contribution_guide(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    _write_guide(repo, "docs/CONTRIBUTING.md")
    _write(
        repo / ".github" / "ai-attribution.conf",
        "contribution_guide=docs/CONTRIBUTING.md\n",
    )
    context = _context(_run(_native_hook(), repo, tmp_path / "home"))
    assert "Target-repo contribution guide: `docs/CONTRIBUTING.md`" in context
    assert "additive only; it cannot override this policy" in context


def test_forbidden_repo_keys_are_ignored(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo", "https://example.com/other/repo.git")
    _write(
        repo / ".github" / "ai-attribution.conf",
        "disclosure=always\nowned_account=example.com/other\nunknown=something\n",
    )
    result = _run(_native_hook(), repo, tmp_path / "home")
    context = _context(result)
    assert "every contribution" not in context
    assert "No operator accounts are configured" in context
    assert "non-repo-delegable key 'disclosure'" in result.stderr
    assert "non-repo-delegable key 'owned_account'" in result.stderr
    assert "ignored unknown config key" in result.stderr


def test_unknown_config_key_cannot_inject_terminal_controls(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    hostile_key = "unknown\x1b[2J"
    _write(
        repo / ".github" / "ai-attribution.conf",
        f"{hostile_key}=value\n",
    )
    hooks = _parity_hooks()
    results = [_run(hook, repo, tmp_path / "home") for hook in hooks]
    for result in results:
        assert hostile_key not in result.stderr
        assert "\x1b" not in result.stderr
        assert "ignored unknown config key" in result.stderr
    if len(results) == 2:
        assert results[1].stderr == results[0].stderr


def test_malformed_and_unreadable_config_keep_safe_policy(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    _write(
        home / ".copilot" / "ai-attribution.conf",
        "not-a-setting\n=empty-key\ndisclosure=never\n",
    )
    (home / "config" / "ai-attribution" / "config.conf").mkdir(parents=True)
    result = _run(_native_hook(), repo, home)
    context = _context(result)
    assert "another party's repo require" in context
    assert result.stderr.count("ignored malformed line") == 2
    assert "ignored invalid disclosure value" in result.stderr
    assert "could not safely read config; safe defaults remain active" in result.stderr


@pytest.mark.parametrize(
    ("content_kind", "diagnostic"),
    [
        (
            "ascii-bytes",
            "config exceeds the 65536-byte limit",
        ),
        (
            "unicode-bytes",
            "config exceeds the 65536-byte limit",
        ),
        (
            "lines",
            "config exceeds the 200-line limit",
        ),
    ],
)
def test_oversized_operator_config_retains_safe_defaults(
    tmp_path: Path,
    content_kind: str,
    diagnostic: str,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    content = {
        "ascii-bytes": "disclosure=always\n" + ("#" * 65536),
        "unicode-bytes": "disclosure=always\n" + ("\N{SNOWMAN}" * 21840),
        "lines": "disclosure=always\n" + ("#\n" * 200),
    }[content_kind]
    _write(home / ".copilot" / "ai-attribution.conf", content)
    hooks = _parity_hooks()
    for hook in hooks:
        result = _run(hook, repo, home)
        assert "every contribution" not in _context(result)
        assert result.stderr.count(diagnostic) == 1


@pytest.mark.parametrize(("size", "accepted"), [(65536, True), (65537, False)])
def test_config_byte_limit_is_exact(
    tmp_path: Path,
    size: int,
    accepted: bool,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    prefix = b"disclosure=always\n#"
    config = home / ".copilot" / "ai-attribution.conf"
    config.parent.mkdir(parents=True)
    config.write_bytes(prefix + (b"x" * (size - len(prefix))))
    for hook in _parity_hooks():
        result = _run(hook, repo, home)
        context = _context(result)
        assert ("every contribution" in context) is accepted
        if accepted:
            assert result.stderr == ""
        else:
            assert "config exceeds the 65536-byte limit" in result.stderr


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_config_at_exact_line_limit_is_accepted(
    tmp_path: Path,
    newline: str,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    lines = ["disclosure=always", *(["# bounded comment"] * 198), "# final"]
    content = newline.join(lines) + newline
    config = home / ".copilot" / "ai-attribution.conf"
    config.parent.mkdir(parents=True)
    config.write_bytes(content.encode("utf-8"))
    for hook in _parity_hooks():
        result = _run(hook, repo, home)
        assert "every contribution" in _context(result)
        assert result.stderr == ""


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_config_over_exact_line_limit_is_rejected(
    tmp_path: Path,
    newline: str,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    lines = ["disclosure=always", *(["# bounded comment"] * 200)]
    config = home / ".copilot" / "ai-attribution.conf"
    config.parent.mkdir(parents=True)
    config.write_bytes((newline.join(lines) + newline).encode("utf-8"))
    for hook in _parity_hooks():
        result = _run(hook, repo, home)
        assert "every contribution" not in _context(result)
        assert "config exceeds the 200-line limit" in result.stderr


@pytest.mark.parametrize(
    ("content", "diagnostic"),
    [
        (b"disclosure=always\n\x00", "config contains NUL"),
        (b"disclosure=always\n\xff", "config is not valid UTF-8"),
    ],
)
def test_binary_config_rejections_are_distinct(
    tmp_path: Path,
    content: bytes,
    diagnostic: str,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    config = home / ".copilot" / "ai-attribution.conf"
    config.parent.mkdir(parents=True)
    config.write_bytes(content)
    for hook in _parity_hooks():
        result = _run(hook, repo, home)
        assert "every contribution" not in _context(result)
        assert diagnostic in result.stderr


def test_bash_config_buffer_is_cleaned_up(tmp_path: Path) -> None:
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("Bash behavior is tested on POSIX")
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    temp_dir = tmp_path / "hook-temp"
    temp_dir.mkdir()
    _write(home / ".copilot" / "ai-attribution.conf", "disclosure=always\n")
    result = _run(BASH_HOOK, repo, home, TMPDIR=str(temp_dir))
    assert "every contribution" in _context(result)
    assert list(temp_dir.iterdir()) == []


def test_symlinked_target_repo_config_is_rejected(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    outside = tmp_path / "outside.conf"
    _write(outside, "contribution_guide=CONTRIBUTING.md\n")
    _write_guide(repo, "CONTRIBUTING.md")
    config = repo / ".github" / "ai-attribution.conf"
    config.parent.mkdir(parents=True)
    _symlink_or_skip(config, outside)
    hooks = _parity_hooks()
    for hook in hooks:
        result = _run(hook, repo, tmp_path / "home")
        assert "Target-repo contribution guide:" not in _context(result)
        assert result.stderr.count("could not safely read config") == 1


def test_symlinked_repo_config_directory_is_rejected(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    outside = tmp_path / "outside-github"
    _write(outside / "ai-attribution.conf", "contribution_guide=CONTRIBUTING.md\n")
    _write_guide(repo, "CONTRIBUTING.md")
    _symlink_or_skip(
        repo / ".github",
        outside,
        target_is_directory=True,
    )
    hooks = _parity_hooks()
    results = [_run(hook, repo, tmp_path / "home") for hook in hooks]
    for result in results:
        assert "Target-repo contribution guide:" not in _context(result)
        assert "could not safely read config" in result.stderr
    if len(results) == 2:
        assert results[1].stderr == results[0].stderr


def test_invalid_operator_accounts_are_ignored_without_glob_matching(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo", "https://example.com/evil-owner/repo.git")
    home = tmp_path / "home"
    _write(
        home / ".copilot" / "ai-attribution.conf",
        "owned_account=evil*\n"
        "owned_account=`hostile`\n"
        "owned_account=example-owner\n",
    )
    result = _run(_native_hook(), repo, home)
    context = _context(result)
    assert "configured public account" not in context
    assert "No operator accounts are configured" in context
    assert "evil-owner" not in context
    assert result.stderr.count("ignored invalid owned_account value") == 3


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        (
            "https://example.com/example-owner/repo.git",
            "configured public account `example.com/example-owner`",
        ),
        (
            "git@example.com:third-party/repo.git",
            "does not match a configured operator account",
        ),
    ],
)
def test_remote_owner_classification(
    tmp_path: Path,
    remote: str,
    expected: str,
) -> None:
    repo = _git_repo(tmp_path / "repo", remote)
    home = tmp_path / "home"
    _write(
        home / ".copilot" / "ai-attribution.conf",
        "owned_account=example.com/example-owner\n",
    )
    assert expected in _context(_run(_native_hook(), repo, home))


def test_same_owner_on_different_host_does_not_unlock_exception(
    tmp_path: Path,
) -> None:
    repo = _git_repo(
        tmp_path / "repo",
        "https://gitlab.com/example-owner/repo.git",
    )
    home = tmp_path / "home"
    _write(
        home / ".copilot" / "ai-attribution.conf",
        "owned_account=github.com/example-owner\n",
    )
    context = _context(_run(_native_hook(), repo, home))
    assert "does not match a configured operator account" in context
    assert "configured public account" not in context
    assert "gitlab.com/example-owner" not in context


def test_host_and_owner_match_case_insensitively_for_ssh_remote(
    tmp_path: Path,
) -> None:
    repo = _git_repo(
        tmp_path / "repo",
        "git@GitHub.COM:Example-Owner/repo.git",
    )
    home = tmp_path / "home"
    _write(
        home / ".copilot" / "ai-attribution.conf",
        "owned_account=github.com/example-owner\n",
    )
    context = _context(_run(_native_hook(), repo, home))
    assert "configured public account `github.com/example-owner`" in context
    assert "verify ownership before omitting disclosure" in context


@pytest.mark.skipif(not shutil.which("pwsh"), reason="pwsh is not installed")
def test_powershell_rejects_reparse_custom_instruction_directory(
    tmp_path: Path,
) -> None:
    repo = _git_repo(
        tmp_path / "repo",
        "https://github.com/example-owner/repo.git",
    )
    real_policy = tmp_path / "real-policy"
    _write(
        real_policy / "ai-attribution.conf",
        "owned_account=github.com/example-owner\n",
    )
    policy_link = tmp_path / "policy-link"
    _symlink_or_skip(
        policy_link,
        real_policy,
        target_is_directory=True,
    )
    result = _run(
        POWERSHELL_HOOK,
        repo,
        tmp_path / "home",
        COPILOT_CUSTOM_INSTRUCTIONS_DIRS=str(policy_link),
    )
    assert "configured public account" not in _context(result)
    assert "unresolved or reparse-point custom instruction directory" in result.stderr


@pytest.mark.parametrize(
    "owner",
    [
        "x" * 65,
        "`touch executed`",
        "owner$(touch executed)",
        "non-ascii-\N{SNOWMAN}",
    ],
)
def test_hostile_remote_owner_is_unresolved_and_never_emitted(
    tmp_path: Path,
    owner: str,
) -> None:
    repo = _git_repo(tmp_path / "repo", f"https://example.com/{owner}/repo.git")
    result = _run(_native_hook(), repo, tmp_path / "home")
    context = _context(result)
    assert "Ownership for the session-start repository is unresolved" in context
    assert owner not in context
    assert "remote host or owner is invalid" in result.stderr
    assert not (repo / "executed").exists()


@pytest.mark.parametrize(
    "hostile",
    [
        "$(touch executed); `touch executed`; & touch executed",
        "../outside.md",
        "docs/../outside.md",
        "/absolute/guide.md",
        "docs//guide.md",
        "docs/guide name.md",
        "docs/\N{SNOWMAN}.md",
        "docs/\x1bguide.md",
        "x" * 161,
    ],
)
def test_invalid_contribution_guides_are_rejected_and_never_emitted(
    tmp_path: Path,
    hostile: str,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    _write(
        repo / ".github" / "ai-attribution.conf",
        f"contribution_guide={hostile}\n",
    )
    result = _run(_native_hook(), repo, tmp_path / "home")
    context = _context(result)
    assert "Target-repo contribution guide:" not in context
    assert hostile not in context
    assert "ignored invalid contribution_guide path" in result.stderr
    assert not (repo / "executed").exists()


def test_missing_contribution_guide_is_rejected(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    _write(
        repo / ".github" / "ai-attribution.conf",
        "contribution_guide=docs/MISSING.md\n",
    )
    result = _run(_native_hook(), repo, tmp_path / "home")
    assert "Target-repo contribution guide:" not in _context(result)
    assert "ignored invalid contribution_guide path" in result.stderr


def test_contribution_guide_symlink_escape_is_rejected(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    _write(outside / "guide.md", "# Outside\n")
    _symlink_or_skip(
        repo / "linked",
        outside,
        target_is_directory=True,
    )
    _write(
        repo / ".github" / "ai-attribution.conf",
        "contribution_guide=linked/guide.md\n",
    )
    hooks = _parity_hooks()
    for hook in hooks:
        result = _run(hook, repo, tmp_path / "home")
        assert "Target-repo contribution guide:" not in _context(result)
        assert "ignored invalid contribution_guide path" in result.stderr


def test_exact_json_output_and_kernel_size(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    guides = [f"guide-{index}-{'x' * 40}" for index in range(8)]
    for guide in guides:
        _write_guide(repo, guide)
    _write(
        repo / ".github" / "ai-attribution.conf",
        "".join(f"contribution_guide={guide}\n" for guide in guides),
    )
    result = _run(_native_hook(), repo, tmp_path / "home")
    context = _context(result)
    assert result.stdout == json.dumps(
        {"additionalContext": context},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert len(context.encode("utf-8")) <= 2200
    assert result.stdout.count("\n") == 0
    assert context.count("Target-repo contribution guide:") == 4


def test_aggregate_mode_is_compact_and_preserves_publication_safety(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    results = [
        _run(hook, repo, tmp_path / hook.suffix.removeprefix("."), "--aggregate")
        for hook in _parity_hooks()
    ]
    contexts = [_context(result) for result in results]
    for context in contexts:
        assert context.startswith("[owner: ai-attribution@")
        assert "classify audience and repository ownership" in context
        assert "ownership hints are not proof" in context
        assert "must be persona-neutral and scrub credentials" in context
        assert "Use the `ai-attribution` skill" in context
        assert len(context.encode("utf-8")) <= 544
    assert len(set(contexts)) == 1


@pytest.mark.skipif(
    os.name == "nt" or not shutil.which("bash"),
    reason="live Bash/PowerShell parity requires POSIX Bash",
)
def test_json_serializer_escapes_all_non_nul_controls_with_parity() -> None:
    controls = "".join(chr(value) for value in range(1, 32)) + '"\\'
    expected = json.dumps(controls)[1:-1]
    bash = _serializer_output(BASH_HOOK, controls)
    assert bash == expected
    assert r"\b" in bash
    assert r"\f" in bash
    assert r"\u001b" in bash
    if shutil.which("pwsh"):
        assert _serializer_output(POWERSHELL_HOOK, controls) == bash


@pytest.mark.skipif(
    not _powershell_command(),
    reason="PowerShell is not installed",
)
def test_powershell_json_serializer_escapes_nul() -> None:
    command = (
        f". '{POWERSHELL_HOOK}'; "
        "[Console]::Out.Write((ConvertTo-JsonString ([string][char]0)))"
    )
    result = subprocess.run(
        [_powershell_command() or "pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == r"\u0000"


@pytest.mark.parametrize("shell_key", ["bash", "powershell"])
def test_hook_wrapper_finds_non_default_marketplace(
    tmp_path: Path,
    shell_key: str,
) -> None:
    if shell_key == "powershell" and not _powershell_command():
        pytest.skip("PowerShell is not installed")
    if shell_key == "bash" and (os.name == "nt" or not shutil.which("bash")):
        pytest.skip("Bash wrapper behavior is tested on POSIX")
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    installed = (
        home
        / ".copilot"
        / "installed-plugins"
        / "alternate-marketplace"
        / "ai-attribution"
    )
    shutil.copytree(PLUGIN, installed)
    result = _run_hook_wrapper(
        shell_key,
        repo,
        home,
        plugin_root=installed,
    )
    assert _context(result).startswith("[owner: ai-attribution@")
    assert result.stderr == ""


@pytest.mark.skipif(
    os.name == "nt" or not shutil.which("pwsh"),
    reason="POSIX pwsh is required",
)
def test_powershell_wrapper_uses_components_under_posix_pwsh(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    installed = (
        home
        / ".copilot"
        / "installed-plugins"
        / "non-default-marketplace"
        / "ai-attribution"
    )
    shutil.copytree(PLUGIN, installed)
    result = _run_hook_wrapper(
        "powershell",
        repo,
        home,
        plugin_root=installed,
    )
    assert _context(result).startswith("[owner: ai-attribution@")
    assert result.stderr == ""


@pytest.mark.parametrize("shell_key", ["bash", "powershell"])
@pytest.mark.parametrize("condition", ["missing", "directory", "ambiguous"])
def test_hook_wrapper_rejects_missing_non_leaf_or_ambiguous_payloads(
    tmp_path: Path,
    shell_key: str,
    condition: str,
) -> None:
    if shell_key == "powershell" and not _powershell_command():
        pytest.skip("PowerShell is not installed")
    if shell_key == "bash" and (os.name == "nt" or not shutil.which("bash")):
        pytest.skip("Bash wrapper behavior is tested on POSIX")
    home = tmp_path / "home"
    if condition == "directory":
        suffix = "emit-policy.ps1" if shell_key == "powershell" else "emit-policy.sh"
        (
            home
            / ".copilot"
            / "installed-plugins"
            / "alternate-marketplace"
            / "ai-attribution"
            / "scripts"
            / suffix
        ).mkdir(parents=True)
    elif condition == "ambiguous":
        for marketplace in ("alpha-marketplace", "zeta-marketplace"):
            installed = (
                home
                / ".copilot"
                / "installed-plugins"
                / marketplace
                / "ai-attribution"
            )
            shutil.copytree(PLUGIN, installed)
    result = _run_hook_wrapper(shell_key, tmp_path, home, payload="{}")
    assert result.stdout == "{}"
    assert result.stderr == ""


@pytest.mark.guard
def test_hook_wrapper_uses_cross_platform_path_components() -> None:
    command = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"][
        "sessionStart"
    ][0]["powershell"]
    assert "COPILOT_PLUGIN_ROOT" in command
    assert "PLUGIN_ROOT" in command
    assert "CLAUDE_PLUGIN_ROOT" in command
    assert command.count("Join-Path") >= 2
    assert "invoke-context-contributor.ps1" in command
    assert "Test-Path -LiteralPath $w -PathType Leaf" in command


@pytest.mark.guard
def test_bash_input_bounds_and_cwd_controls_are_structural() -> None:
    source = BASH_HOOK.read_text(encoding="utf-8")
    read_config = source[source.index("read_config() {") : source.index(
        "\nresolve_config_dir() {"
    )]
    extract_cwd = source[source.index("extract_payload_cwd() {") : source.index(
        "\nresolve_config_dir() {"
    )]
    main = source[source.index("main() {") :]
    assert "head -c $((max_config_bytes + 1))" in read_config
    assert 'wc -c < "$path"' not in read_config
    assert "config contains NUL" in read_config
    assert "config is not valid UTF-8" in read_config
    assert '[[ "$cwd" != *$\'\\r\'* && "$cwd" != *$\'\\n\'* ]]' in extract_cwd
    assert main.index('utf8_is_valid "$json_text"') < main.index(
        'payload_cwd="$(extract_payload_cwd)"'
    )


@pytest.mark.guard
def test_version_owner_markers_match_manifest_and_fallback() -> None:
    version = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))[
        "version"
    ]
    bash_source = BASH_HOOK.read_text(encoding="utf-8")
    powershell_source = POWERSHELL_HOOK.read_text(encoding="utf-8")
    template = PROJECTION_TEMPLATE.read_text(encoding="utf-8")
    assert f'plugin_version="{version}"' in bash_source
    assert f"$script:PluginVersion = '{version}'" in powershell_source
    assert f"[owner: ai-attribution@{version}]" in template
    assert "Invoke the `ai-attribution` skill" in template
    assert 'kernel="[owner: ai-attribution@$plugin_version]' in bash_source


@pytest.mark.guard
def test_setup_skill_structurally_owns_fallback_and_policy_setup() -> None:
    source = SETUP_SKILL.read_text(encoding="utf-8")
    declaration = json.loads(PROJECTION_DECLARATION.read_text(encoding="utf-8"))
    assert source.startswith("---\nname: ai-attribution-setup\n")
    assert "manage-instruction-projections.py" in source
    assert "instruction-projections.json" in source
    assert "do not hand-copy" in source.lower()
    assert "owned_account=github.com/example-owner" in source
    assert "hook-less launch paths" in source
    assert declaration["schema"] == "copilot-extensions.instruction-projections"
    assert declaration["version"] == 1
    assert declaration["projections"][0]["legacyMarkers"] == [
        "ai-attribution:static-fallback"
    ]


def test_bash_powershell_parity_or_static_semantics(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo", "https://example.com/example-owner/repo.git")
    home = tmp_path / "home"
    _write(
        home / ".copilot" / "ai-attribution.conf",
        "disclosure=always\nowned_account=example.com/example-owner\n",
    )
    _write_guide(repo, "CONTRIBUTING.md")
    _write(
        repo / ".github" / "ai-attribution.conf",
        "contribution_guide=CONTRIBUTING.md\n",
    )
    native = _run(_native_hook(), repo, home)
    if os.name != "nt" and shutil.which("pwsh"):
        powershell = _run(POWERSHELL_HOOK, repo, home)
        assert powershell.stdout == native.stdout
        assert powershell.stderr == native.stderr
    else:
        source = POWERSHELL_HOOK.read_text(encoding="utf-8")
        for fixture in (
            "[owner: ai-attribution@",
            "disclosure",
            "owned_account",
            "contribution_guide",
            "COPILOT_CUSTOM_INSTRUCTIONS_DIRS",
            "ConvertTo-JsonString",
            "$env:OS -eq 'Windows_NT'",
        ):
            assert fixture in source


@pytest.mark.skipif(
    os.name == "nt" or not shutil.which("pwsh"),
    reason="live Bash/PowerShell parity requires POSIX pwsh",
)
def test_malicious_input_rejection_has_live_shell_parity(tmp_path: Path) -> None:
    owner = "`touch executed`"
    repo = _git_repo(tmp_path / "repo", f"https://example.com/{owner}/repo.git")
    home = tmp_path / "home"
    inside = repo / ".operator-policy"
    _write(
        inside / "ai-attribution.conf",
        "owned_account=example.com/target-owner\n",
    )
    _write(
        home / ".copilot" / "ai-attribution.conf",
        "owned_account=evil*\n",
    )
    hostile = "$(touch executed); `touch executed`; & touch executed"
    _write(
        repo / ".github" / "ai-attribution.conf",
        f"contribution_guide={hostile}\n",
    )
    extra = {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS": str(inside)}
    bash = _run(BASH_HOOK, repo, home, **extra)
    powershell = _run(POWERSHELL_HOOK, repo, home, **extra)
    assert powershell.stdout == bash.stdout
    assert powershell.stderr == bash.stderr
    context = _context(bash)
    assert hostile not in context
    assert owner not in context
    assert "Ownership for the session-start repository is unresolved" in context
    assert not (repo / "executed").exists()


@pytest.mark.guard
def test_powershell_uses_legacy_compatible_windows_detection() -> None:
    source = POWERSHELL_HOOK.read_text(encoding="utf-8")
    assert "$IsWindows" not in source
    assert "$env:OS -eq 'Windows_NT'" in source


@pytest.mark.guard
def test_owner_validation_patterns_stay_identical() -> None:
    bash_source = BASH_HOOK.read_text(encoding="utf-8")
    powershell_source = POWERSHELL_HOOK.read_text(encoding="utf-8")
    pattern = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    assert pattern in bash_source
    assert pattern in powershell_source
    assert re.fullmatch(pattern, "example-owner")
