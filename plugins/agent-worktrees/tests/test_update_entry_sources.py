"""Durable source authority and shell-only acquisition, with no live Git source."""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_update_entry import PLUGIN, REPO, _arguments
from test_update_entry import update_entry_case as shared_update_entry_case

update_entry_case = shared_update_entry_case


@pytest.mark.parametrize("name", ["WORKTREE_MANAGER_BOOTSTRAP_REPO", "WORKTREE_MANAGER_BOOTSTRAP_REF"])
@pytest.mark.parametrize("value", ["", "-invalid", "invalid\nsource"])
def test_invalid_transport_never_downloads_or_resolves(
    update_entry_case, native_bash, monkeypatch, name, value,
):
    tmp, _home, _resolver = update_entry_case
    monkeypatch.setenv(name, value)
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 126
    assert not (tmp / "curl.args").exists()
    assert not (tmp / "bootstrap.args").exists()
    assert not (tmp / "resolved").exists()


@pytest.mark.parametrize("runtime", ["missing", "corrupt"])
@pytest.mark.parametrize("configured", [False, True])
def test_update_entry_missing_manager_payload_still_triggers(
    update_entry_case, native_bash, runtime, configured,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    (root / "current-version").write_text("selected-but-unavailable\n")
    if runtime == "corrupt":
        source = root / (
            "versions/selected-but-unavailable/payload/src/worktree_manager/source_config.py"
        )
        source.parent.mkdir(parents=True)
        source.write_text('not Python; DEFAULT_REPO = "must-not-be-used"\n')
    repo, ref = "https://github.com/ThomasMichon/copilot-extensions.git", "main"
    if configured:
        repo, ref = "https://github.com/example/durable.git", "canary"
        (root / "config.toml").write_text(f'[source]\nrepo = "{repo}"\nref = "{ref}"\n')
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 23, result.stderr.decode()
    assert _arguments(tmp / "bootstrap.args") == ["--bootstrap-update", "update"]
    assert _arguments(tmp / "curl.args")[1] == (
        "https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.sh"
    )
    assert not (tmp / "resolved").exists()
    assert not (tmp / "python.args").exists()
    assert not (tmp / "manager-called").exists()


def _fake_git(tmp: Path) -> None:
    git = tmp / "tools/git"
    git.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" >> "$GIT_ARGS"\nprintf "\\n" >> "$GIT_ARGS"\n'
        '[ -z "${GIT_ENV:-}" ] || printf "%s\\0%s\\0" '
        '"${GIT_DIR:-}" "$GIT_SSH_COMMAND" >> "$GIT_ENV"\n'
        'case "$1" in init) [ "$GIT_FAIL" != init ] || exit 41\n'
        '  for last do :; done\nmkdir -p "$last"; exit $? ;; esac\n'
        'for part do\n'
        '  if [ "$part" = fetch ]; then [ "$GIT_FAIL" != fetch ] || exit 42; exit 0; fi\n'
        '  if [ "$part" = checkout ]; then\n'
        '    [ "$GIT_FAIL" != checkout ] || exit 43\n'
        '    [ "$GIT_FAIL" != missing-bootstrap ] || exit 0\n'
        '    mkdir -p "$2/worktree-manager"\n'
        '    cp "$DOWNLOAD_SCRIPT" "$2/worktree-manager/bootstrap.sh"; exit $?\n'
        '  fi\n  [ "$part" != diff ] || exit 0\ndone\nexit 99\n'
    )
    git.chmod(0o755)


@pytest.mark.parametrize("repo", [
    "ssh://git@example.test/team/fork.git",
    "git@example.test:team/fork.git",
    "https://code.example.test/team/fork.git",
    "file:///selected/source.git",
    "/selected/source with spaces.git",
    "relative source.git",
    'relative quote"and\\backslash.git',
])
@pytest.mark.parametrize("ref", ["release/canary", "refs/tags/v1", "a" * 40])
def test_update_entry_generic_git_uses_exact_source(
    update_entry_case, native_bash, monkeypatch, repo, ref,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    data = f"[source]\nrepo = {json.dumps(repo)}\nref = {json.dumps(ref)}\n"
    (root / "config.toml").write_text(data)
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", repo)
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REF", ref)
    _fake_git(tmp)
    monkeypatch.setenv("GIT_ARGS", str(tmp / "git.args"))
    monkeypatch.setenv("GIT_FAIL", "")
    monkeypatch.setenv("GIT_DIR", "/must-not-route-to-an-existing-repository")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    monkeypatch.setenv("GIT_ENV", str(tmp / "git.env"))
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "--project", "sample",
         "update", "--force", "--skip-modules", "module with spaces"],
        capture_output=True, timeout=15, cwd=tmp,
    )
    assert result.returncode == 23, result.stderr.decode()
    expected_repo = str(tmp / repo) if repo.startswith("relative") else repo
    calls = [
        line.decode().split("\0")[:-1] for line in (tmp / "git.args").read_bytes().splitlines()
    ]
    assert len(calls) == 3
    checkout = calls[0][-1]
    assert calls[0] == ["init", "--quiet", "--", checkout]
    assert calls[1] == [
        "-C", checkout, "fetch", "--quiet", "--depth", "1", "--", expected_repo, ref,
    ]
    assert calls[2] == [
        "-C", checkout, "-c", "core.hooksPath=", "checkout", "--quiet", "--detach", "FETCH_HEAD",
    ]
    assert _arguments(tmp / "bootstrap.args") == [
        "--bootstrap-update", "update", "--project", "sample",
        "--force", "--skip-modules", "module with spaces",
    ]
    assert (root / "config.toml").read_text() == data
    assert not (tmp / "curl.args").exists()
    assert not (tmp / "resolved").exists()
    assert not (tmp / "python.args").exists()
    assert not list(tmp.glob("agent-update-*"))
    assert _arguments(tmp / "git.env") == ["", "ssh -o BatchMode=yes"] * 3


@pytest.mark.parametrize(("phase", "expected"), [
    ("init", 41), ("fetch", 42), ("checkout", 43), ("missing-bootstrap", 126),
])
def test_update_entry_generic_git_failure_is_not_success(
    update_entry_case, native_bash, monkeypatch, phase, expected,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    (root / "config.toml").write_text(
        '[source]\nrepo = "ssh://git@example.test/team/fork.git"\nref = "canary"\n',
    )
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", "ssh://git@example.test/team/fork.git")
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REF", "canary")
    _fake_git(tmp)
    monkeypatch.setenv("GIT_ARGS", str(tmp / "git.args"))
    monkeypatch.setenv("GIT_FAIL", phase)
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        capture_output=True, timeout=15,
    )
    assert result.returncode == expected, result.stderr.decode()
    assert not (tmp / "bootstrap.args").exists()
    assert not (tmp / "curl.args").exists()
    assert not list(tmp.glob("agent-update-*"))


def test_update_entry_missing_git_has_no_canonical_fallback(
    update_entry_case, native_bash, monkeypatch,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    (root / "config.toml").write_text('[source]\nrepo = "ssh://example.test/fork.git"\n')
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", "ssh://example.test/fork.git")
    tools = tmp / "tools"
    (tools / "git").unlink()
    for name in ("dirname", "awk", "date", "mktemp", "rm", "rmdir"):
        (tools / name).symlink_to(shutil.which(name))
    monkeypatch.setenv("PATH", str(tools))
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 127
    assert b"Git is required" in result.stderr
    assert not (tmp / "curl.args").exists()
    assert not (tmp / "bootstrap.args").exists()
    assert not list(tmp.glob("agent-update-*"))


def test_update_entry_raw_failure_retries_only_same_configured_git_source(
    update_entry_case, native_bash, monkeypatch,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    repo, ref = "https://github.com/example/private.git", "canary"
    (root / "config.toml").write_text(f'[source]\nrepo = "{repo}"\nref = "{ref}"\n')
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", repo)
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REF", ref)
    _fake_git(tmp)
    monkeypatch.setenv("GIT_ARGS", str(tmp / "git.args"))
    monkeypatch.setenv("GIT_FAIL", "")
    monkeypatch.setenv("CURL_RC", "22")
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update"],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 23, result.stderr.decode()
    calls = [
        line.decode().split("\0")[:-1] for line in (tmp / "git.args").read_bytes().splitlines()
    ]
    assert calls[1][-2:] == [repo, ref]
    assert _arguments(tmp / "bootstrap.args") == ["--bootstrap-update", "update"]
    assert b"same configured Git source" in result.stderr
    assert not list(tmp.glob("agent-update-*"))


@pytest.mark.parametrize("override", [False, True])
def test_update_entry_supported_bootstrap_preserves_independent_exact_git_ref(
    update_entry_case, native_bash, monkeypatch, override,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    repo, ref = "ssh://git@example.test/team/selected.git", "b" * 40
    (root / "config.toml").write_text(
        '[runtime_source]\nchannel = "dev"\nallow_dev = true\n'
        f'[source]\nrepo = "{repo}"\nref = "{ref}"\n',
    )
    if override:
        monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", "ssh://git@example.test/tools/bootstrap.git")
        monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REF", "transport-branch")
    _fake_git(tmp)
    monkeypatch.setenv("GIT_ARGS", str(tmp / "git.args"))
    monkeypatch.setenv("GIT_FAIL", "")
    monkeypatch.setenv("DOWNLOAD_SCRIPT", str(REPO / "worktree-manager/bootstrap.sh"))
    monkeypatch.setenv("FRESH_ARGS", str(tmp / "fresh.args"))
    fresh = tmp / "fresh-python"
    fresh.write_text('#!/bin/sh\nprintf "%s\\0" "$@" > "$FRESH_ARGS"\nexit 37\n')
    fresh.chmod(0o755)
    uv = tmp / "tools/uv"
    uv.write_text(f'#!/bin/sh\nprintf "%s\\n" "{fresh}"\n')
    uv.chmod(0o755)
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), "update", "--force"],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 37, result.stderr.decode()
    calls = [
        line.decode().split("\0")[:-1] for line in (tmp / "git.args").read_bytes().splitlines()
    ]
    fetches = [args for args in calls if "fetch" in args]
    assert len(fetches) == (2 if override else 1)
    if override:
        assert fetches[0][-3:] == ["--", "ssh://git@example.test/tools/bootstrap.git", "transport-branch"]
        assert not (tmp / "curl.args").exists()
    else:
        assert _arguments(tmp / "curl.args")[1] == (
            "https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.sh"
        )
    assert fetches[-1][-3:] == ["--", repo, ref]
    assert _arguments(tmp / "fresh.args")[-4:] == [
        "--repair-update", "--", "update", "--force",
    ]
    assert not (tmp / "python.args").exists()
    assert not list(tmp.glob("agent-update-*"))


def test_update_entry_publication_defaults_match_supported_bootstrap():
    tree = ast.parse((REPO / "worktree-manager/src/worktree_manager/source_config.py").read_text(encoding="utf-8"))
    defaults = {
        target.id: ast.literal_eval(node.value)
        for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
        and target.id in ("DEFAULT_REPO", "DEFAULT_REF")
    }
    for relative, names in (
        ("plugins/agent-worktrees/bin/agent-worktrees-update-entry.sh", ("_repo", "_ref")),
        ("plugins/agent-worktrees/bin/agent-worktrees-update-entry.ps1",
         ("$_updateRepo", "$_updateRef")),
        ("worktree-manager/bootstrap.sh", ("REPO", "REF")),
        ("worktree-manager/bootstrap.ps1", ("$Repo", "$Ref")),
    ):
        text = (REPO / relative).read_text(encoding="utf-8")
        for name, key in zip(names, ("DEFAULT_REPO", "DEFAULT_REF"), strict=True):
            pattern = rf"(?m)^\s*{re.escape(name)}\s*=\s*'([^']+)'"
            assert re.search(pattern, text).group(1) == defaults[key]
    for suffix in ("sh", "ps1"):
        text = (PLUGIN / "bin" / f"agent-worktrees-update-entry.{suffix}").read_text(encoding="utf-8")
        assert "current-version" not in text
        assert "source_config.py" not in text
