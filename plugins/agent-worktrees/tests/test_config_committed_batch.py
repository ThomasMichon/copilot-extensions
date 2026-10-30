"""Fresh committed policy reads share plumbing, not a cross-request cache."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, inrepo_config_source as source


@pytest.fixture
def anchor(tmp_path):
    def git(*args, **kwargs):
        return git_ops.git(
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            *args, cwd=tmp_path, **kwargs,
        )

    git("init", "--template=", "-b", "main")

    def commit(text, *, branch="main"):
        (tmp_path / "config.yaml").write_bytes(text)
        git("add", "config.yaml")
        git("commit", "-m", "fixture policy")
        git("update-ref", f"refs/remotes/origin/{branch}", "HEAD")
        git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    return tmp_path, git, commit


def resolve(path):
    return source.load_inrepo_config_from_committed_ref(
        path, (Path("missing.yaml"), Path("config.yaml"), Path("later.yaml")),
    )


def test_real_batch_preserves_utf8_crlf_and_missing_candidates(anchor, monkeypatch):
    path, _git, commit = anchor
    commit("marker: caf\u00e9\r\nheadless: false\r\n".encode("utf-8"))
    original, calls = git_ops.git, []

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", observed)
    assert resolve(path) == {"marker": "caf\u00e9", "headless": False}
    assert calls == [("symbolic-ref", "-q", "refs/remotes/origin/HEAD"), ("cat-file", "--batch")]


def test_later_request_observes_new_committed_headless_policy(anchor):
    path, _git, commit = anchor
    commit(b"headless: false\n")
    assert resolve(path) == {"headless": False}
    (path / "config.yaml").write_text("headless: true\n", encoding="utf-8")
    assert resolve(path) == {"headless": False}
    commit(b"headless: true\n")
    assert resolve(path) == {"headless": True}


@pytest.mark.parametrize("first", [b"", b"[malformed", b"marker: first\n"])
def test_empty_or_malformed_first_file_never_promotes_later_policy(anchor, first):
    path, git, commit = anchor
    commit(first)
    (path / "later.yaml").write_text("headless: true\n", encoding="utf-8")
    git("add", "later.yaml")
    git("commit", "-m", "lower priority")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    assert resolve(path) == ({"marker": "first"} if first == b"marker: first\n" else {})


def test_default_branch_hop_remains_fresh(anchor):
    path, git, commit = anchor
    commit(b"default_branch: dev\nheadless: false\n")
    git("checkout", "-b", "dev")
    commit(b"default_branch: dev\nheadless: true\n", branch="dev")
    assert resolve(path) == {"default_branch": "dev", "headless": True}


@pytest.mark.parametrize("output", [
    b"", b"not-a-header\n", b"a" * 40 + b" tree 0\n\n",
    b"x" * 40 + b" blob 0\n\n", b"a" * 40 + b" blob -1\n\n",
    b"a" * 40 + b" blob 20\nshort\n",
    b"a" * 40 + b" blob 0\n\nunexpected",
    "decoded text is not a binary packet",
])
def test_malformed_binary_packet_retains_ordered_fallback(monkeypatch, tmp_path, output):
    monkeypatch.setattr(
        git_ops, "git", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=output),
    )
    assert source._read_committed_batch(
        tmp_path, "origin", "main", (Path("config.yaml"),), time.monotonic() + 10,
    ) is None


def test_batch_failure_uses_existing_bounded_fallback(anchor, monkeypatch):
    path, _git, commit = anchor
    commit(b"marker: retained\n")
    original = git_ops.git

    def unavailable(*args, **kwargs):
        if args[0] == "cat-file":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", unavailable)
    assert resolve(path) == {"marker": "retained"}


def test_git_binary_input_preserves_bytes_and_default_text_contract(anchor):
    _path, git, _commit = anchor
    payload = "line one\r\ncaf\u00e9\r\n".encode("utf-8")
    oid = git("hash-object", "-w", "--stdin", input_data=payload).stdout.strip()
    result = git("cat-file", "--batch", input_data=oid + b"\n")
    assert result.stdout == oid + b" blob " + str(len(payload)).encode() + b"\n" + payload + b"\n"
    assert isinstance(git("--version").stdout, str)
    with pytest.raises(git_ops.GitError) as failure:
        git("not-a-real-git-command", input_data=b"")
    assert isinstance(failure.value.stderr, str)


def test_binary_input_retains_requested_tree_runner(monkeypatch):
    monkeypatch.setattr(
        git_ops.subprocess, "run", lambda *_a, **_k: pytest.fail("tree custody bypassed"),
    )
    calls = []

    def bounded(cmd, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, b"packet", b"")

    monkeypatch.setattr(git_ops.push_timeout, "run_bounded", bounded)
    assert git_ops.git("cat-file", "--batch", input_data=b"", kill_tree=True, timeout=1).stdout == b"packet"
    assert calls[0]["binary"] is True and calls[0]["input"] == b""
    assert calls[0]["timeout"] == 1


@pytest.mark.parametrize("body, authority", [
    (None, "indeterminate"), ("", "complete"), ("marker: disk\n", "complete"),
    ("marker: [\n", "indeterminate"),
])
def test_inrepo_base_report_distinguishes_optional_empty_and_invalid(tmp_path, monkeypatch, body, authority):
    monkeypatch.setattr(git_ops, "git", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    if body is not None:
        (tmp_path / "config.yaml").write_text(body, encoding="utf-8")
    candidates = (Path("config.yaml"), Path("later.yaml"))
    legacy = source.load_config_base(tmp_path, candidates)
    report = source.load_config_base_report(tmp_path, candidates)
    assert report.value == legacy
    assert report.authority.value == authority
    assert report.reusable is False
    assert "stable-owner-input-frontier" in report.missing_coverage


def test_inrepo_base_report_unreadable_and_strict_errors_keep_legacy_behavior(tmp_path, monkeypatch):
    from agent_worktrees.repos_configuration_sources import ConfigurationError

    monkeypatch.setattr(git_ops, "git", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    path = tmp_path / "config.yaml"
    path.write_text("marker: [\n", encoding="utf-8")
    candidates = (Path("config.yaml"),)
    with pytest.raises(ConfigurationError) as legacy:
        source.load_config_base(tmp_path, candidates, strict=True)
    with pytest.raises(ConfigurationError) as diagnostic:
        source.load_config_base_report(tmp_path, candidates, strict=True)
    assert diagnostic.value.code == legacy.value.code
    real_open = open

    def unreadable(file, *args, **kwargs):
        if Path(file) == path:
            raise PermissionError("fixture denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", unreadable)
    assert source.load_config_base(tmp_path, candidates) == {}
    report = source.load_config_base_report(tmp_path, candidates)
    assert report.value == {}
    assert report.authority.value == "indeterminate"
    assert any(f.reason == "unreadable-source" for f in report.findings)
    assert "fixture denied" not in repr(report.observations)


def test_inrepo_base_report_fallback_success_does_not_hide_unobserved_candidates(tmp_path, monkeypatch):
    calls = []

    def git(*args, **kwargs):
        calls.append(args)
        if args[0] == "symbolic-ref":
            return SimpleNamespace(returncode=0, stdout="refs/remotes/origin/main\n")
        if args == ("show", "origin/main:later.yaml"):
            return SimpleNamespace(returncode=0, stdout="marker: retained\n")
        return SimpleNamespace(returncode=128, stdout="")

    monkeypatch.setattr(git_ops, "git", git)
    candidates = (Path("first.yaml"), Path("later.yaml"))
    assert source.load_config_base(tmp_path, candidates) == {"marker": "retained"}
    calls.clear()
    report = source.load_config_base_report(tmp_path, candidates)
    assert report.value == {"marker": "retained"}
    assert report.authority.value == "indeterminate"
    assert report.findings
    assert calls == [
        ("symbolic-ref", "-q", "refs/remotes/origin/HEAD"), ("cat-file", "--batch"),
        ("--literal-pathspecs", "ls-tree", "-z", "--full-tree", "origin/main", "--",
         "first.yaml", "later.yaml"),
        ("show", "origin/main:first.yaml"), ("show", "origin/main:later.yaml"),
    ]


def test_inrepo_base_report_snapshot_retains_original_acquisition_metadata(tmp_path, monkeypatch):
    calls = []
    spec = b"origin/main:config.yaml"
    blob = b"marker: committed\n"
    packet = b"a" * 40 + b" blob " + str(len(blob)).encode() + b"\n" + blob + b"\n"

    def git(*args, **kwargs):
        calls.append(args)
        if args[0] == "symbolic-ref":
            return SimpleNamespace(returncode=0, stdout="refs/remotes/origin/main\n")
        assert args == ("cat-file", "--batch")
        assert kwargs["input_data"] == spec + b"\n"
        return SimpleNamespace(returncode=0, stdout=packet)

    monkeypatch.setattr(git_ops, "git", git)
    candidates = (Path("config.yaml"),)
    with source.resolution_budget(reuse_snapshots=True):
        first = source.load_config_base_report(tmp_path, candidates)
        second = source.load_config_base_report(tmp_path, candidates)
    assert first.value == second.value == {"marker": "committed"}
    assert first.observations == second.observations
    assert first.authority.value == second.authority.value == "complete"
    assert len(calls) == 2
    assert second.reusable is False



def test_inrepo_base_report_retains_branch_probe_uncertainty_in_legacy_snapshot(tmp_path, monkeypatch):
    calls = []
    blob = b"default_branch: dev\nmarker: base\n"
    packet = b"a" * 40 + b" blob " + str(len(blob)).encode() + b"\n" + blob + b"\n"

    def git(*args, **kwargs):
        calls.append(args)
        if args[0] == "symbolic-ref":
            return SimpleNamespace(returncode=0, stdout="refs/remotes/origin/main\n")
        if args[0] == "cat-file":
            return SimpleNamespace(returncode=0, stdout=packet)
        assert args == ("show-ref", "--verify", "--quiet", "refs/remotes/origin/dev")
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(git_ops, "git", git)
    candidates = (Path("config.yaml"),)
    with source.resolution_budget(reuse_snapshots=True):
        legacy = source.load_config_base(tmp_path, candidates)
        legacy["marker"] = "caller mutation"
        report = source.load_config_base_report(tmp_path, candidates)
    assert report.value == {"default_branch": "dev", "marker": "base"}
    assert report.authority.value == "indeterminate"
    assert any(o.stage == "committed-branch" and o.findings for o in report.observations)
    assert len(calls) == 3
    assert report.reusable is False



@pytest.mark.parametrize("positive_peer", [False, True])
def test_inrepo_base_report_preserves_suppressed_presence_error(tmp_path, monkeypatch, positive_peer):
    import errno
    from agent_worktrees import source_probes

    monkeypatch.setattr(git_ops, "git", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    missing = (Path("ordinary-missing.yaml"),)
    ordinary_missing = source.load_config_base_report(tmp_path, missing)
    assert ordinary_missing.authority.value == "indeterminate"
    assert any(f.reason == "presence-provenance-unavailable" for f in ordinary_missing.findings)
    first, later = tmp_path / "first.yaml", tmp_path / "later.yaml"
    if positive_peer:
        later.write_text("marker: fallback\n", encoding="utf-8")
    original_stat = source_probes.os.stat
    failed_probes = []

    def stat(path, *args, **kwargs):
        if isinstance(path, (str, source_probes.os.PathLike)) and Path(path) == first:
            failed_probes.append(path)
            raise OSError(errno.ELOOP, "synthetic inaccessible candidate")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(source_probes.os, "stat", stat)
    # Exercise pathlib's real suppression, not a mocked False return.
    assert first.exists() is False
    failed_probes.clear()
    candidates = (Path("first.yaml"), Path("later.yaml"))
    expected = {"marker": "fallback"} if positive_peer else {}
    assert source.load_config_base(tmp_path, candidates) == expected
    assert failed_probes == [first]
    failed_probes.clear()
    report = source.load_config_base_report(tmp_path, candidates)
    assert report.value == expected
    assert failed_probes == [first]
    assert report.authority.value == "indeterminate"
    assert any(o.source == str(first) and o.findings for o in report.observations)


def test_inrepo_strict_file_report_cannot_certify_empty_after_read_absence(tmp_path, monkeypatch):
    from agent_worktrees.repos_configuration_sources import mapping

    monkeypatch.setattr(git_ops, "git", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    path = tmp_path / "config.yaml"
    path.write_text("marker: previously present\n", encoding="utf-8")
    original_read = Path.read_bytes
    reads = []

    def missing_at_read(candidate):
        if candidate == path:
            reads.append(candidate)
            raise FileNotFoundError("synthetic disappearance after selection")
        return original_read(candidate)

    monkeypatch.setattr(Path, "read_bytes", missing_at_read)
    assert mapping(path) == {}
    assert source.load_config_base(tmp_path, (Path("config.yaml"),), strict=True) == {}
    reads.clear()
    report = source.load_config_base_report(tmp_path, (Path("config.yaml"),), strict=True)
    assert report.value == {}
    assert reads == [path]
    # The strict owner returned no evidence distinguishing absence from parsed empty.
    assert report.authority.value == "indeterminate"


def test_inrepo_strict_mapping_propagates_nonabsence_read_error(tmp_path, monkeypatch):
    import errno
    from agent_worktrees import config_acquisition as acquisition
    from agent_worktrees.repos_configuration_sources import mapping

    monkeypatch.setattr(git_ops, "git", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    path = tmp_path / "config.yaml"
    path.write_text("marker: present\n", encoding="utf-8")
    original_read = Path.read_bytes
    failure = OSError(errno.ELOOP, "synthetic read failure")

    def unreadable(candidate):
        if candidate == path:
            raise failure
        return original_read(candidate)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    for load in (
        lambda: mapping(path),
        lambda: source.load_config_base(tmp_path, (Path("config.yaml"),), strict=True),
        lambda: source.load_config_base_report(tmp_path, (Path("config.yaml"),), strict=True),
    ):
        with pytest.raises(OSError) as caught:
            load()
        assert caught.value is failure
    assert acquisition.current_observations() is None
