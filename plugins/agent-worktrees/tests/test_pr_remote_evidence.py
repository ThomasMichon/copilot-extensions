"""Fresh URL authority and exact, batched transport configuration."""

import subprocess

import pytest

from agent_worktrees import git_ops, pr_publish
from agent_worktrees.git_push_transport import REMOTE_TRANSPORT_SETTINGS
from pr_test_helpers import _stub_remote_target


@pytest.fixture
def repository(tmp_path):
    git_ops.git("init", "--template=", "-b", "main", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("remote", ["origin", "fork.with+regexp", "fork=alice"])
def test_transport_settings_use_one_literal_query_with_exact_order_and_values(
    repository, monkeypatch, remote,
):
    configured = [
        ("pushoption", "first\ncontinued"),
        ("proxy", "https://proxy.invalid"),
        ("pushoption", "second\toption"),
        ("mirror", ""),
        ("uploadpack", "custom-upload-pack"),
    ]
    for key, value in configured:
        git_ops.git("config", "--add", f"remote.{remote}.{key}", value, cwd=repository)
    git_ops.git("config", "--add", f"remote.{remote}x.pushoption", "foreign", cwd=repository)
    original = git_ops.git
    calls = []

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", observed)
    result = pr_publish._transport_settings(remote, cwd=str(repository))
    assert result == tuple(
        (key, value) for key in REMOTE_TRANSPORT_SETTINGS
        for candidate, value in configured if candidate == key
    )
    assert len(calls) == 1
    assert calls[0][:3] == ("config", "--null", "--get-regexp")


def test_transport_settings_preserve_real_valueless_boolean(repository, monkeypatch):
    original = git_ops.git
    monkeypatch.setattr(
        git_ops, "git",
        lambda *args, **kwargs: original("-c", "remote.origin.mirror", *args, **kwargs),
    )
    assert pr_publish._transport_settings("origin", cwd=str(repository)) == (("mirror", ""),)


def test_transport_settings_absence_is_not_a_read_error(repository):
    assert pr_publish._transport_settings("absent", cwd=str(repository)) == ()


@pytest.mark.parametrize("returncode,stdout,error", [
    (3, "", git_ops.GitError),
    (1, "", git_ops.GitError),
    (1, "unexpected output", git_ops.GitError),
    (0, "remote.origin.mirror\ntrue", ValueError),
    (0, "remote.other.mirror\ntrue\0", ValueError),
    (0, "remote.origin.unrecognized\ntrue\0", ValueError),
])
def test_transport_settings_fail_explicitly_on_read_or_framing_errors(
    monkeypatch, returncode, stdout, error,
):
    monkeypatch.setattr(
        git_ops, "git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, returncode, stdout, "controlled configuration read failure",
        ),
    )
    with pytest.raises(error):
        pr_publish._transport_settings("origin", cwd=".")


def test_remote_snapshot_uses_two_reads_and_observes_a_later_repoint(repository, monkeypatch):
    remote = "fork"
    git_ops.git("remote", "add", remote, "https://first.invalid/owner/repo.git", cwd=repository)
    original = git_ops.git
    calls = []

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", observed)
    _, _, first = pr_publish._remote_snapshot(remote, cwd=str(repository))
    original("remote", "set-url", remote, "https://second.invalid/owner/repo.git", cwd=repository)
    _, _, second = pr_publish._remote_snapshot(remote, cwd=str(repository))
    assert first.head_repo == second.head_repo == "owner/repo"
    assert first.head_identity == "first.invalid/owner/repo"
    assert second.head_identity == "second.invalid/owner/repo"
    assert calls == [
        ("remote", "get-url", remote),
        ("remote", "get-url", "--push", "--all", remote),
    ] * 2


def test_remote_snapshot_refuses_a_push_destination_changed_between_reads(repository, monkeypatch):
    git_ops.git("remote", "add", "fork", "https://first.invalid/owner/repo.git", cwd=repository)
    original = git_ops.git

    def repoint_after_fetch(*args, **kwargs):
        result = original(*args, **kwargs)
        if args == ("remote", "get-url", "fork"):
            original("remote", "set-url", "fork", "https://second.invalid/owner/repo.git",
                     cwd=repository)
        return result

    monkeypatch.setattr(git_ops, "git", repoint_after_fetch)
    _, _, result = pr_publish._remote_snapshot("fork", cwd=str(repository))
    assert result.head_repo == result.head_identity == ""


@pytest.mark.parametrize("detail,error", [
    ("controlled transport read failure", git_ops.GitError),
    ("controlled malformed transport response", ValueError),
])
def test_transport_read_failure_prevents_any_push(repository, monkeypatch, detail, error):
    git_ops.git("remote", "add", "origin", "https://first.invalid/owner/repo.git", cwd=repository)

    def fail(*args, **kwargs):
        if error is git_ops.GitError:
            raise git_ops.GitError(["git", "config"], 3, detail)
        raise error(detail)

    monkeypatch.setattr(pr_publish, "_transport_settings", fail)
    monkeypatch.setattr(git_ops, "push", lambda *args, **kwargs: pytest.fail("unproved transport pushed"))
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/change", cwd=str(repository))
    assert not result
    assert result.stderr == detail


def test_scenario_adapter_cannot_hide_invalid_transport(repository, monkeypatch):
    git_ops.git("remote", "add", "fork", "https://first.invalid/owner/repo.git", cwd=repository)
    _stub_remote_target(monkeypatch, "fork", "scenario/project", "scenario.invalid/scenario/project")
    assert pr_publish.push_slug("fork", cwd=str(repository)) == "scenario/project"
    git_ops.git("remote", "set-url", "--push", "fork",
                "https://second.invalid/owner/repo.git", cwd=repository)
    _, _, target = pr_publish._remote_snapshot("fork", cwd=str(repository))
    assert target.head_repo == target.head_identity == ""
