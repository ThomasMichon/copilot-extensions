"""Real process-boundary publication concurrency and destination pinning."""

from pathlib import Path
from collections.abc import Iterator
import os
import subprocess
import sys
from typing import Any

import pytest

import agent_procutil
from agent_worktrees import git_ops, pr_publish
from agent_worktrees.github_credentials import GitAuthArgs
from agent_procutil import no_window_kwargs


def git(root: Path, *args: str) -> str:
    return git_ops.git("-c", "safe.bareRepository=all", *args, cwd=root).stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "source"
    remote = tmp_path / "remote.git"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "test")
    git(root, "config", "user.email", "test@example.test")
    git(root, "commit", "--allow-empty", "-qm", "initial")
    git(root, "init", "--bare", "-q", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    return root, remote


@pytest.mark.parametrize("remote_name", ["origin", "fork=alice"])
@pytest.mark.parametrize("explicit_pushurl", [False, True])
def test_pinned_transport_survives_remote_repoint_and_retains_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote_name: str, explicit_pushurl: bool,
) -> None:
    root, remote = repository(tmp_path)
    if remote_name != "origin":
        git(root, "remote", "rename", "origin", remote_name)
    if explicit_pushurl:
        git(root, "config", f"remote.{remote_name}.pushurl", str(remote))
    other = tmp_path / "other.git"
    git(root, "init", "--bare", "-q", str(other))
    marker = tmp_path / "hook-ran"
    hook = root / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\n[ \"$1\" = '{remote_name}' ] || exit 1\n"
                    f'[ "$(git remote get-url --push "$1")" = \'{remote}\' ] || exit 1\n'
                    f'[ "$(git remote get-url "$1")" = \'{remote}\' ] || exit 1\n'
                    'read local_ref local_sha remote_ref remote_sha\n'
                    '[ "$local_ref" = HEAD ] || exit 1\n'
                    f"echo ran > '{marker.as_posix()}'\n", encoding="utf8")
    hook.chmod(0o755)
    original = git_ops.push

    def repoint_then_push(name: str, refspec: str, **kwargs: Any) -> git_ops.PushResult:
        with pr_publish.publish_lock(str(root), acquire_timeout=1):
            git(root, "remote", "set-url", remote_name, str(other))
        return original(name, refspec, **kwargs)

    monkeypatch.setattr(git_ops, "push", repoint_then_push)
    result = pr_publish.push_checked(None, remote_name, "HEAD:refs/heads/first", cwd=str(root))
    assert result, result.stderr
    assert marker.exists()
    assert git(remote, "rev-parse", "refs/heads/first") == git(root, "rev-parse", "HEAD")
    assert git_ops.git("show-ref", "--verify", "refs/heads/first", cwd=other,
                       check=False).returncode != 0
    assert git_ops.git("rev-parse", "--verify", "refs/remotes/" + remote_name + "/first",
                       cwd=root, check=False).returncode != 0


@pytest.mark.timeout(30)
def test_unrelated_publications_overlap_at_real_pre_push_boundary(tmp_path: Path) -> None:
    root, remote = repository(tmp_path)
    second = tmp_path / "second"
    git(root, "worktree", "add", "-qb", "second", str(second))
    markers = tmp_path / "barrier"
    markers.mkdir()
    hook = root / ".git" / "hooks" / "pre-push"
    helper = tmp_path / "barrier.py"
    helper.write_text(
        "import pathlib,sys,time\n"
        "root=pathlib.Path(sys.argv[1]); name=sys.argv[2]\n"
        "(root/name).write_text('ready')\n"
        "deadline=time.monotonic()+10\n"
        "while len(list(root.iterdir()))<2:\n"
        " if time.monotonic()>deadline: raise SystemExit('publication serialized')\n"
        " time.sleep(.02)\n",
        encoding="utf8",
    )
    # Use a shell hook solely to call the isolated barrier; no host services.
    hook.write_text(
        "#!/bin/sh\n"
        f"exec '{Path(sys.executable).as_posix()}' '{helper.as_posix()}' "
        f"'{markers.as_posix()}' \"$PUBLISH_TEST_NAME\"\n",
        encoding="utf8",
    )
    hook.chmod(0o755)
    script = (
        "import sys\nfrom agent_worktrees import pr_publish\n"
        "r=pr_publish.push_checked(None,'origin',"
        "'HEAD:refs/heads/'+sys.argv[2],cwd=sys.argv[1])\n"
        "if not r: raise SystemExit(r.stderr)\n"
    )
    children = []
    dependency_source = str(Path(agent_procutil.__file__).resolve().parent.parent)
    pythonpath = os.pathsep.join(filter(None, (dependency_source, os.environ.get("PYTHONPATH", ""))))
    try:
        for path, branch in [(root, "one"), (second, "two")]:
            children.append(subprocess.Popen(
                [sys.executable, "-c", script, str(path), branch],
                env={**os.environ, "PUBLISH_TEST_NAME": branch, "PYTHONPATH": pythonpath},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                **no_window_kwargs(),
            ))
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, stderr or stdout
        assert git(remote, "rev-parse", "refs/heads/one") == git(root, "rev-parse", "HEAD")
        assert git(remote, "rev-parse", "refs/heads/two") == git(root, "rev-parse", "HEAD")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=5)


def test_remote_rewrite_rule_cannot_redirect_captured_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, remote = repository(tmp_path)
    other = tmp_path / "redirect.git"
    git(root, "init", "--bare", "-q", str(other))
    original = git_ops.push

    def rewrite_then_push(name: str, refspec: str, **kwargs: Any) -> git_ops.PushResult:
        git(root, "config", f"url.{other}.pushInsteadOf", str(remote))
        git(root, "config", f"url.{other}.insteadOf", str(remote))
        return original(name, refspec, **kwargs)

    monkeypatch.setattr(git_ops, "push", rewrite_then_push)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/pinned", cwd=str(root))
    assert result, result.stderr
    assert git(remote, "rev-parse", "refs/heads/pinned") == git(root, "rev-parse", "HEAD")


def test_same_branch_stale_expected_object_is_rejected(tmp_path: Path) -> None:
    root, remote = repository(tmp_path)
    old = git(root, "rev-parse", "HEAD")
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/shared", cwd=str(root))
    git(root, "commit", "--allow-empty", "-qm", "first update")
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/shared", cwd=str(root),
                                   force_with_lease_expect=old)
    first = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-qc", "stale-writer", old)
    git(root, "commit", "--allow-empty", "-qm", "stale writer")
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/shared", cwd=str(root),
                                     force_with_lease_expect=old)
    assert not result
    assert git(remote, "rev-parse", "refs/heads/shared") == first


def test_stale_lease_recovery_observes_captured_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, remote = repository(tmp_path)
    old = git(root, "rev-parse", "HEAD")
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/shared", cwd=str(root))
    git(root, "commit", "--allow-empty", "-qm", "published before observation")
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/shared", cwd=str(root))
    published = git(root, "rev-parse", "HEAD")
    other = tmp_path / "other.git"
    git(root, "init", "--bare", "-q", str(other))
    original = git_ops.push
    attempts: list[dict[str, Any]] = []

    def repoint_then_report_stale(name: str, refspec: str, **kwargs: Any) -> git_ops.PushResult:
        attempts.append(kwargs)
        if len(attempts) == 1:
            git(root, "remote", "set-url", name, str(other))
            return git_ops.PushResult(ok=False, stderr="stale info")
        return original(name, refspec, **kwargs)

    monkeypatch.setattr(git_ops, "push", repoint_then_report_stale)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/shared", cwd=str(root),
                                     force_with_lease_expect=old)
    assert result, result.stderr
    assert len(attempts) == 2 and "force_with_lease_expect" not in attempts[1]
    assert git(remote, "rev-parse", "refs/heads/shared") == published
    assert git_ops.git("show-ref", "--verify", "refs/heads/shared", cwd=other,
                       check=False).returncode != 0


def test_multiple_equivalent_push_urls_and_invalid_source(tmp_path: Path) -> None:
    root, remote = repository(tmp_path)
    git(root, "config", "--add", "remote.origin.pushurl", str(remote))
    git(root, "config", "--add", "remote.origin.pushurl", str(remote))
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/multiple", cwd=str(root))
    assert result, result.stderr
    result = pr_publish.push_checked(None, "origin", "missing:refs/heads/multiple", cwd=str(root))
    assert not result and result.stderr


def test_distinct_whitespace_destinations_cannot_publish_together(tmp_path: Path) -> None:
    root, _remote = repository(tmp_path)
    for name in ("remote.git", " remote.git"):
        git(root, "init", "--bare", "-q", str(root / name))
        git(root, "config", "--add", "remote.origin.pushurl", name)
    assert pr_publish._identity("remote.git", str(root)) != pr_publish._identity(" remote.git", str(root))
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/rejected", cwd=str(root))
    assert not result and result.stderr == pr_publish.UNREADABLE_REMOTE
    for name in ("remote.git", " remote.git"):
        assert git_ops.git("show-ref", "--verify", "refs/heads/rejected", cwd=root / name,
                           check=False).returncode != 0


@pytest.mark.parametrize("name", [" remote.git", "remote.git "])
def test_whitespace_sensitive_destination_is_preserved(tmp_path: Path, name: str) -> None:
    if os.name == "nt" and name.endswith(" "):
        pytest.skip("Windows normalizes trailing whitespace in directory names")
    root, _remote = repository(tmp_path)
    remote = root / name
    git(root, "init", "--bare", "-q", str(remote))
    git(root, "remote", "set-url", "origin", name)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/exact", cwd=str(root))
    assert result, result.stderr
    assert not result.stderr
    assert git(remote, "rev-parse", "refs/heads/exact") == git(root, "rev-parse", "HEAD")
    assert git(root, "rev-parse", "refs/remotes/origin/exact") == git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("name", [" remote.git", "remote.git "])
def test_recorded_destination_verification_retains_fetch_whitespace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    if os.name == "nt" and name.endswith(" "):
        pytest.skip("Windows normalizes trailing whitespace in directory names")
    root, _remote = repository(tmp_path)
    git(root, "init", "--bare", "-q", str(root / name))
    git(root, "remote", "set-url", "origin", name)
    parsed: list[str] = []
    def slug(url: str) -> str:
        parsed.append(url)
        return "example/project"
    monkeypatch.setattr(git_ops, "slug_from_url", slug)
    assert pr_publish.push_slug("origin", cwd=str(root)) == "example/project"
    assert pr_publish.push_identity("origin", cwd=str(root)) == pr_publish._identity(name, str(root))
    assert parsed and all(url == name for url in parsed)


def test_post_push_refresh_lock_wait_is_short_and_failure_is_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    root, remote = repository(tmp_path)
    original = pr_publish.publish_lock
    waits = []

    @contextmanager
    def contended_refresh(cwd: str, **kwargs: Any) -> Iterator[None]:
        if "acquire_timeout" in kwargs:
            waits.append(kwargs["acquire_timeout"])
            raise pr_publish.PublishLockTimeout("tracking refresh lock busy")
        with original(cwd, **kwargs):
            yield

    monkeypatch.setattr(pr_publish, "publish_lock", contended_refresh)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/committed",
                                     cwd=str(root), timeout=600)
    assert result and "tracking refresh lock busy" in result.stderr
    assert waits == [1.0]
    assert git(remote, "rev-parse", "refs/heads/committed") == git(root, "rev-parse", "HEAD")


def test_native_push_does_not_update_tracking_before_guarded_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import git_push_transport

    root, _remote = repository(tmp_path)
    observed = []
    original = git_push_transport._refresh_tracking

    def refresh(*args: Any, **kwargs: Any) -> None:
        ref = git_ops.git("rev-parse", "--verify", "refs/remotes/origin/check",
                          cwd=root, check=False)
        observed.append(ref.returncode)
        return original(*args, **kwargs)

    monkeypatch.setattr(git_push_transport, "_refresh_tracking", refresh)
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/check", cwd=str(root))
    assert observed and observed[0] != 0
    assert git(root, "rev-parse", "refs/remotes/origin/check") == git(root, "rev-parse", "HEAD")


def test_forwarded_hook_rejection_prevents_publication(tmp_path: Path) -> None:
    root, remote = repository(tmp_path)
    hook = root / ".git" / "hooks" / "pre-push"
    hook.write_text('#!/bin/sh\n[ "$1" = origin ] || exit 42\n'
                    'echo "policy denied" >&2\nexit 1\n', encoding="utf8")
    hook.chmod(0o755)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/denied", cwd=str(root))
    assert not result and "policy denied" in result.stderr
    assert git_ops.git("show-ref", "--verify", "refs/heads/denied", cwd=remote,
                       check=False).returncode != 0


@pytest.mark.parametrize("source", ["HEAD", "topic", "refs/heads/topic"])
def test_pinned_hook_stdin_matches_native_source_ref(tmp_path: Path, source: str) -> None:
    root, _remote = repository(tmp_path)
    git(root, "branch", "topic")
    marker = tmp_path / "hook-input"
    hook = root / ".git" / "hooks" / "pre-push"
    hook.write_text(
        '#!/bin/sh\n[ "$1" = origin ] || exit 42\n'
        'read local_ref local_sha remote_ref remote_sha\n'
        f'printf "%s %s\\n" "$local_ref" "$local_sha" > \'{marker.as_posix()}\'\n',
        encoding="utf8",
    )
    hook.chmod(0o755)
    git(root, "push", "origin", f"{source}:refs/heads/native")
    native = marker.read_bytes()
    marker.unlink()
    result = pr_publish.push_checked(None, "origin", f"{source}:refs/heads/pinned", cwd=str(root))
    assert result, result.stderr
    assert marker.read_bytes() == native


def test_inherited_git_context_cannot_select_foreign_hook_lock_or_tracking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, remote = repository(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    git(foreign, "init", "-q")
    marker = tmp_path / "foreign-hook"
    foreign_hook = foreign / ".git" / "hooks" / "pre-push"
    foreign_hook.write_text(f"#!/bin/sh\necho wrong > '{marker.as_posix()}'\nexit 1\n",
                            encoding="utf8")
    foreign_hook.chmod(0o755)
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/isolated", cwd=str(root))
    assert result, result.stderr
    assert not marker.exists()
    assert not (foreign / ".git" / "agent-worktrees-publish.lock").exists()
    assert (root / ".git" / "agent-worktrees-publish.lock").exists()
    isolated = git_ops.git("rev-parse", "refs/heads/isolated", cwd=remote,
                          isolated_repository=True, check=False)
    # Explicit bare invocation avoids host safe.bareRepository configuration.
    if isolated.returncode:
        isolated = git_ops.git("--git-dir", str(remote), "rev-parse", "refs/heads/isolated",
                               cwd=root, isolated_repository=True)
    assert isolated.stdout.strip()


def test_tracking_uses_remote_result_when_local_source_moves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import git_push_transport

    root, remote = repository(tmp_path)
    published = git(root, "rev-parse", "HEAD")
    original = git_push_transport._refresh_tracking

    def advance_source_then_refresh(*args: Any, **kwargs: Any) -> None:
        git(root, "commit", "--allow-empty", "-qm", "not published")
        return original(*args, **kwargs)

    monkeypatch.setattr(git_push_transport, "_refresh_tracking", advance_source_then_refresh)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/observed", cwd=str(root))
    assert result, result.stderr
    assert result.published_head_sha == published
    assert git(root, "rev-parse", "refs/remotes/origin/observed") == published
    assert git(remote, "rev-parse", "refs/heads/observed") == published


def test_narrow_wildcard_tracking_mapping(tmp_path: Path) -> None:
    root, _remote = repository(tmp_path)
    git(root, "config", "remote.origin.fetch",
        "+refs/heads/release/*:refs/remotes/origin/release/*")
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/release/topic", cwd=str(root))
    assert git(root, "rev-parse", "refs/remotes/origin/release/topic") == git(root, "rev-parse", "HEAD")


def test_negative_fetch_mapping_excludes_tracking_ref(tmp_path: Path) -> None:
    root, remote = repository(tmp_path)
    git(root, "config", "--add", "remote.origin.fetch", "^refs/heads/private/*")
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/private/topic", cwd=str(root))
    assert git(remote, "rev-parse", "refs/heads/private/topic")
    assert git_ops.git("rev-parse", "--verify", "refs/remotes/origin/private/topic",
                       cwd=root, check=False).returncode != 0


def test_remote_receivepack_is_preserved(tmp_path: Path) -> None:
    root, remote = repository(tmp_path)
    # Failure proves the command was honored rather than silently replaced by
    # the default receive-pack on the synthetic remote.
    git(root, "config", "remote.origin.receivepack", "nonexistent-test-receive-pack")
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/custom", cwd=str(root))
    assert not result
    assert "nonexistent-test-receive-pack" in result.stderr
    assert git_ops.git("show-ref", "--verify", "refs/heads/custom", cwd=remote,
                       check=False).returncode != 0


@pytest.mark.parametrize("uploadpack", ["git-upload-pack", "nonexistent-test-upload-pack"])
def test_successful_push_refresh_does_not_depend_on_uploadpack(tmp_path: Path, uploadpack: str) -> None:
    root, remote = repository(tmp_path)
    git(root, "config", "remote.origin.uploadpack", uploadpack)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/custom", cwd=str(root))
    assert result, result.stderr
    published = git(remote, "rev-parse", "refs/heads/custom")
    tracked = git_ops.git("rev-parse", "--verify", "refs/remotes/origin/custom",
                          cwd=root, check=False)
    assert not result.stderr
    assert tracked.returncode == 0 and tracked.stdout.strip() == published


def test_probe_authentication_override_uses_same_plain_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees.git_push_transport import observed_ref, pinned_destination

    root, remote = repository(tmp_path)
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url",
                        lambda _url: GitAuthArgs(
                            ["-c", "protocol.file.allow=never"], allow_ambient_retry=True,
                        ))
    original = git_ops.git
    attempts = []

    def capture(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "push" in args or "ls-remote" in args:
            attempts.append(("push" if "push" in args else "probe",
                             "protocol.file.allow=never" in args))
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", capture)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/fallback", cwd=str(root))
    assert result and not result.stderr
    assert attempts == [("push", True), ("push", False)]
    with pinned_destination("origin", (str(remote),)):
        assert observed_ref("origin", (str(remote),), "refs/heads/fallback", root) == result.published_head_sha
    assert attempts == [("push", True), ("push", False), ("probe", True), ("probe", False)]
    assert git(root, "rev-parse", "refs/remotes/origin/fallback") == git(remote, "rev-parse", "refs/heads/fallback")


def test_equals_sign_destination_is_pinned_without_config_key_corruption(tmp_path: Path) -> None:
    root, remote = repository(tmp_path)
    renamed = remote.with_name("repo=mirror.git")
    remote.rename(renamed)
    git(root, "remote", "set-url", "origin", str(renamed))
    assert pr_publish.push_checked(None, "origin", "HEAD:refs/heads/equals", cwd=str(root))
    assert git(renamed, "rev-parse", "refs/heads/equals") == git(root, "rev-parse", "HEAD")


def test_pinned_transport_uses_https_auth_and_probe_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import git_push_transport

    seen_auth: list[str] = []
    seen_probe = []
    root, _remote = repository(tmp_path)
    original = git_ops.git
    def auth(url: str) -> list[str]:
        seen_auth.append(url)
        return []
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url", auth)

    def capture(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "push" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "ls-remote" in args:
            seen_probe.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, "", "")
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", capture)
    with git_push_transport.pinned_destination("origin", (
        "git@github.com:example/repo.git", "https://github.com/example/repo.git",
    ), (("proxy", "http://proxy.test"), ("proxyauthmethod", "basic"))):
        result = git_push_transport.push("origin", "HEAD:refs/heads/test", cwd=root)
        assert not seen_probe
        assert git_push_transport.observed_ref("origin", (
            "git@github.com:example/repo.git", "https://github.com/example/repo.git",
        ), "refs/heads/test", root) == ""
    assert result
    assert seen_auth[0] == "https://github.com/example/repo.git"
    assert seen_probe[0][1]["kill_tree"] is True
    assert any(".proxy=http://proxy.test" in str(arg) for arg in seen_probe[0][0])
    assert any(".proxyauthmethod=basic" in str(arg) for arg in seen_probe[0][0])


def test_post_push_refresh_timeout_is_warning_and_enters_without_clone_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import threading
    from agent_worktrees import git_push_transport

    root, remote = repository(tmp_path)
    locked = threading.Event()

    def timed_out_refresh(*args: Any, **kwargs: Any) -> None:
        assert kwargs["published_oid"]
        def acquire() -> None:
            with pr_publish.publish_lock(str(root), acquire_timeout=2):
                locked.set()
        worker = threading.Thread(target=acquire)
        worker.start()
        worker.join(5)
        assert locked.is_set(), "post-push refresh entered with the clone lock already held"
        raise subprocess.TimeoutExpired(
            ["git", "-c", "http.extraHeader=sensitive-test-canary", "update-ref"], 30,
        )

    monkeypatch.setattr(git_push_transport, "_refresh_tracking", timed_out_refresh)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/committed", cwd=str(root))
    assert result and "refresh failed: TimeoutExpired" in result.stderr
    assert "30 seconds" in result.stderr
    assert "sensitive-test-canary" not in result.stderr
    assert "sensitive-test-canary" not in caplog.text
    assert git(remote, "rev-parse", "refs/heads/committed")
