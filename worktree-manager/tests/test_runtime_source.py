"""Real standalone source acquisition, with no runtime or marketplace mutation."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import runtime_source as source
from worktree_manager import runtime_source_config as config
from worktree_manager import source_config
from worktree_manager.__main__ import main


def git(path: Path, *arguments: str) -> str:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.longpaths=true",
         "-C", str(path), *arguments],
        capture_output=True, text=True, check=False, env=environment, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit(path: Path, text: str) -> str:
    (path / "payload.txt").write_text(text, encoding="utf-8")
    git(path, "add", ".")
    git(path, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "commit", "-qm", text)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    first = commit(repo, "release one")
    git(repo, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "tag", "-a", "v1.0", "-m", "release", first, "--no-sign")
    git(repo, "switch", "-qc", "dev")
    development = commit(repo, "merged development")
    git(repo, "switch", "-q", "main")
    return repo, first, development


def test_policy_defaults_do_not_inherit_manager_source(tmp_path):
    root = tmp_path / "manager"
    source_config.set_source(repo="https://example.invalid/manager.git", ref="canary", root=root)
    assert config.configured_source(root) == config.RuntimeSource()
    config.set_source(config.RuntimeSource("dev", allow_dev=True), root=root)
    assert config.configured_source(root).channel == "dev"
    assert source_config.configured_source(root) == ("https://example.invalid/manager.git", "canary")
    source_config.reset_source(root=root)
    assert config.configured_source(root).channel == "dev"


def test_policy_roundtrip_preserves_other_manager_tables(tmp_path):
    root = tmp_path / "manager"
    root.mkdir()
    path = root / "config.toml"
    path.write_text('[maintenance]\nenabled = false\n', encoding="utf-8")
    selection = config.RuntimeSource("stable", "https://example.invalid/source.git", "v1.0")
    config.set_source(selection, root=root)
    assert config.configured_source(root) == selection
    config.set_source(config.RuntimeSource(), root=root)
    assert config.configured_source(root).channel == "marketplace"
    assert source_config.tomllib.loads(path.read_text(encoding="utf-8"))["maintenance"] == {
        "enabled": False,
    }
    assert not list(root.glob(".runtime-source-*.toml"))


@pytest.mark.parametrize("arguments", [
    {"channel": "dev"}, {"channel": "main", "allow_dev": True},
    {"channel": "exact", "ref": "main"},
    {"channel": "stable", "ref": "../tag"},
    {"channel": "stable", "ref": "v1.lock/name"},
    {"channel": "main", "ref": "dev"},
    {"channel": []}, {"allow_dev": "true"},
    {"repo": "https://user:synthetic@example.invalid/source.git"},
    {"repo": "ext::arbitrary-helper"},
    {"repo": "../ambient-checkout"},
])
def test_invalid_policy_is_refused(arguments):
    with pytest.raises(ValueError):
        config.RuntimeSource(**arguments)


@pytest.mark.parametrize("repo", [
    "https://[2001:db8::1]/tools.git",
    "ssh://git@[2001:db8::1]/tools.git",
])
def test_ipv6_source_locator_is_not_a_git_remote_helper(repo):
    assert config.RuntimeSource("main", repo).repo == repo


def test_credential_refusal_uses_real_url_password_not_a_masked_placeholder():
    from urllib.parse import urlsplit

    credentials = "sample-user" + ":" + "sample-password"
    repo = "https" + "://" + credentials + "@example.invalid/source.git"
    assert urlsplit(repo).password == "sample-password"
    with pytest.raises(ValueError, match="credential-free"):
        config.RuntimeSource(repo=repo)


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative locator")
def test_windows_drive_relative_source_is_not_an_absolute_local_repository():
    with pytest.raises(ValueError, match="absolute path"):
        config.RuntimeSource(repo="C:relative")


@pytest.mark.parametrize("body", [
    "malformed = [", 'runtime_source = "dev"',
    '[runtime_source]\nchannel = "dev"\n',
    '[runtime_source]\nunknown = true\n',
])
def test_bad_config_is_not_silently_replaced_with_marketplace(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        config.configured_source(tmp_path)
    assert path.read_text(encoding="utf-8") == body


def test_main_dev_exact_and_tag_resolve_without_moving_source_files(repository, tmp_path):
    repo, release, development = repository
    root = tmp_path / "manager"
    baseline = None
    for selection, expected in [
        (config.RuntimeSource("main", str(repo)), release),
        (config.RuntimeSource("dev", str(repo), allow_dev=True), development),
        (config.RuntimeSource("stable", str(repo), "v1.0"), release),
        (config.RuntimeSource("exact", str(repo), release), release),
    ]:
        with source.acquire(selection, root=root) as resolved:
            assert resolved.commit == expected
            assert resolved.tree == git(repo, "rev-parse", f"{expected}^{{tree}}")
            assert resolved.checkout != repo
            assert (resolved.checkout / ".git").is_dir()
            snapshot = (resolved.checkout, git(resolved.checkout, "rev-parse", "HEAD"),
                        (resolved.checkout / "payload.txt").read_bytes())
            baseline = baseline or snapshot
            assert snapshot == baseline
            assert not git(resolved.checkout, "status", "--porcelain=v1", "--ignored")
    assert git(repo, "rev-parse", "HEAD") == release
    assert not (root / "versions").exists()
    assert not (root / "current-version").exists()
    assert not (root / "config.toml").exists()


def test_advancing_branch_is_resolved_once_and_source_head_stays_fixed(repository, tmp_path):
    repo, release, _development = repository
    selection = config.RuntimeSource("main", str(repo))
    with source.acquire(selection, root=tmp_path / "manager") as first:
        advanced = commit(repo, "release two")
        assert first.commit == release
    with source.acquire(selection, root=tmp_path / "manager") as second:
        assert second.commit == advanced
        assert git(second.checkout, "rev-parse", "HEAD") == release
        assert first.document()["commit"] == release


@pytest.mark.parametrize("kind", ["tracked", "untracked", "ignored", "origin", "owner"])
def test_invalid_incumbent_source_is_refused_without_repair(repository, tmp_path, kind):
    repo, _release, _development = repository
    selection = config.RuntimeSource("main", str(repo))
    with source.acquire(selection, root=tmp_path / "manager") as resolved:
        checkout = resolved.checkout
    if kind == "tracked":
        (checkout / "payload.txt").write_text("local edit", encoding="utf-8")
    elif kind in {"untracked", "ignored"}:
        (checkout / "extra.txt").write_text("local file", encoding="utf-8")
        if kind == "ignored":
            (checkout / ".git" / "info" / "exclude").write_text("extra.txt\n", encoding="utf-8")
    elif kind == "origin":
        git(checkout, "remote", "set-url", "origin", str(tmp_path / "different-repository"))
    else:
        (checkout / ".git" / "runtime-source-owner.json").write_text("{}", encoding="utf-8")
    with pytest.raises(source.SourceAcquisitionError):
        with source.acquire(selection, root=tmp_path / "manager"):
            pytest.fail("invalid source must not be admitted")
    assert not (tmp_path / "manager" / "current-version").exists()


def test_exact_unmerged_commit_has_no_main_fallback(repository, tmp_path):
    repo, _release, development = repository
    selection = config.RuntimeSource("exact", str(repo), development)
    with pytest.raises(source.SourceAcquisitionError, match="not merged"):
        with source.acquire(selection, root=tmp_path / "manager"):
            pytest.fail("unmerged commit must not be admitted")


def test_consumer_mutation_is_reported(repository, tmp_path):
    repo, _release, _development = repository
    with pytest.raises(source.SourceAcquisitionError, match="modified"):
        with source.acquire(config.RuntimeSource("main", str(repo)), root=tmp_path / "manager") as resolved:
            (resolved.checkout / "payload.txt").write_text("changed", encoding="utf-8")


def test_marketplace_never_acquires_or_creates_source_state(tmp_path):
    with pytest.raises(source.SourceAcquisitionError, match="Marketplace"):
        with source.acquire(config.RuntimeSource(), root=tmp_path / "manager"):
            pytest.fail("marketplace has no source checkout")
    assert not (tmp_path / "manager").exists()


@pytest.mark.parametrize("deadline", [True, 0, float("nan"), float("inf")])
def test_source_deadline_refuses_before_git_or_state_writes(tmp_path, monkeypatch, deadline):
    monkeypatch.setattr(source, "_git", lambda *args, **options: pytest.fail("invalid deadline ran Git"))
    with pytest.raises(source.SourceAcquisitionError, match="deadline"):
        with source.acquire(
            config.RuntimeSource("main", str(tmp_path / "upstream")),
            root=tmp_path / "manager", deadline=deadline,
        ):
            pytest.fail("invalid deadline reached consumption")
    assert not (tmp_path / "manager").exists()


def test_source_git_child_uses_original_shared_deadline(repository, monkeypatch):
    actual = source.subprocess.run
    deadline = time.monotonic() + 5
    timeouts = []

    def run(command, **options):
        timeouts.append(options["timeout"])
        return actual(command, **options)

    monkeypatch.setattr(source.subprocess, "run", run)
    assert source._git(repository[0], "rev-parse", "HEAD", deadline=deadline) == repository[1]
    assert len(timeouts) == 1 and 0 < timeouts[0] <= 5


def test_unavailable_configured_source_does_not_fall_back_to_cwd(repository, tmp_path, monkeypatch):
    repo, _release, _development = repository
    monkeypatch.chdir(repo)
    with pytest.raises(source.SourceAcquisitionError, match="Git operation failed"):
        with source.acquire(
            config.RuntimeSource("main", str(tmp_path / "missing")), root=tmp_path / "manager",
        ):
            pytest.fail("a valid ambient checkout is not a fallback")


def test_worktree_cannot_replace_owned_checkout(repository):
    repo, _release, _development = repository
    selection = config.RuntimeSource("main", str(repo))
    # Git for Windows' worktree creator has a separate fixed-size GIT_DIR limit.
    # Keep this refusal fixture short; the ordinary clone tests exercise long paths.
    with tempfile.TemporaryDirectory(prefix="rs-wt-") as temporary:
        root = Path(temporary) / "manager"
        with source.acquire(selection, root=root) as resolved:
            location = resolved.checkout
        location.rename(location.with_name("retained"))
        git(repo, "worktree", "add", "--detach", str(location), "main")
        with pytest.raises(source.SourceAcquisitionError, match="standalone"):
            with source.acquire(selection, root=root):
                pytest.fail("a developer worktree cannot become the maintained source")


def test_cli_resolve_reports_exact_source_without_installing(repository, tmp_path, monkeypatch, capsys):
    repo, _release, development = repository
    root = tmp_path / "manager"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    assert main(["runtime-source", "set", "dev", "--allow-dev", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert main(["runtime-source", "resolve"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["commit"] == development
    assert document["branch"] == "dev"
    assert document["selection"]["allow_dev"] is True
    assert not (root / "current-version").exists()


def test_cli_is_default_off_and_dev_requires_consent(tmp_path, monkeypatch, capsys):
    root = tmp_path / "manager"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    assert main(["runtime-source"]) == 0
    assert json.loads(capsys.readouterr().out)["channel"] == "marketplace"
    assert not root.exists()
    assert main(["runtime-source", "set", "dev"]) == 1
    assert "explicit --allow-dev" in capsys.readouterr().err
    assert not root.exists()
    assert main(["runtime-source", "set", "dev", "--allow-dev"]) == 0
    assert config.configured_source(root).channel == "dev"
    assert main(["runtime-source", "reset"]) == 0
    assert config.configured_source(root).channel == "marketplace"


@pytest.mark.parametrize("repair", [False, True])
def test_update_policy_precedes_engine_repair_without_aggregate_telemetry(
    tmp_path, monkeypatch, capsys, repair,
):
    from worktree_manager import __main__ as manager
    from worktree_manager import update_bootstrap, update_events

    root = tmp_path / "manager"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    config.set_source(config.RuntimeSource("dev", allow_dev=True), root=root)
    monkeypatch.setattr(
        update_events, "UpdateRun",
        lambda: pytest.fail("pre-bootstrap policy refusal must not open aggregate telemetry"),
    )
    monkeypatch.setattr(
        update_bootstrap, "prepare_update",
        lambda _rest: pytest.fail("runtime source preflight must precede engine repair"),
    )
    rest = ["--repair-engine", str(tmp_path / "fresh")] if repair else []
    assert manager._cmd_update(rest) == 1
    assert "No update was run" in capsys.readouterr().err


@pytest.mark.parametrize("repair", [False, True])
def test_bootstrap_update_policy_precedes_manager_self_install(tmp_path, monkeypatch, capsys, repair):
    import sys
    from types import SimpleNamespace

    from worktree_manager import self_install

    root = tmp_path / "manager"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    config.set_source(config.RuntimeSource("dev", allow_dev=True), root=root)
    installer = self_install._runtime_installer()
    monkeypatch.setattr(
        self_install, "self_install",
        lambda *args, **kwargs: pytest.fail("source preflight must precede Manager installation"),
    )
    monkeypatch.setitem(sys.modules, "update_entry", SimpleNamespace(
        validate_manager_ownership=lambda *args, **kwargs: pytest.fail(
            "source preflight must precede bootstrap ownership/repair",
        ),
    ))
    arguments = ["--root", str(root), "--payload", str(self_install.running_payload_dir())]
    if repair:
        arguments.append("--repair-update")
    assert installer.main([*arguments, "--", "update"]) == 1
    assert "No update was run" in capsys.readouterr().err


def test_real_update_error_record_requires_explicit_missing_argument(tmp_path, monkeypatch):
    from worktree_manager import update_events, update_orchestration

    root = tmp_path / "manager"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    config.set_source(config.RuntimeSource("dev", allow_dev=True), root=root)
    monkeypatch.setattr(update_events, "_ut", None)
    monkeypatch.setattr(update_events, "HumanOperation", None)
    run = update_events.UpdateRun()
    assert update_orchestration.run_update_steps([], run, version="1.0") == 1
    assert run.finish(1) == 1


def test_git_keeps_approved_global_and_scoped_auth_without_repository_override(
    repository, tmp_path, monkeypatch,
):
    repo, release, _development = repository
    global_config = tmp_path / "gitconfig"
    global_config.write_text('[credential]\nhelper = approved-global-helper\n', encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "approved-scoped-helper")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "foreign-worktree"))
    assert source._git(repo, "config", "--get-all", "credential.helper") == (
        "approved-global-helper\napproved-scoped-helper"
    )
    assert source._git(repo, "rev-parse", "HEAD") == release


def test_clone_pins_origin_despite_user_default_remote(repository, tmp_path, monkeypatch):
    repo, release, _development = repository
    global_config = tmp_path / "gitconfig"
    global_config.write_text('[clone]\ndefaultRemoteName = upstream\n', encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    with source.acquire(config.RuntimeSource("main", str(repo)), root=tmp_path / "manager") as selected:
        assert selected.commit == release
        assert source._git(selected.checkout, "remote") == "origin"


def test_failed_initial_checkout_does_not_poison_retry(repository, tmp_path, monkeypatch):
    repo, release, _development = repository
    selection = config.RuntimeSource("main", str(repo))
    original = source._git

    def failed_checkout(path, *arguments):
        if arguments[:1] == ("checkout",):
            raise source.SourceAcquisitionError("synthetic checkout interruption")
        return original(path, *arguments)

    monkeypatch.setattr(source, "_git", failed_checkout)
    with pytest.raises(source.SourceAcquisitionError, match="synthetic checkout interruption"):
        with source.acquire(selection, root=tmp_path / "manager"):
            pytest.fail("interrupted initialization cannot be admitted")
    monkeypatch.setattr(source, "_git", original)
    with source.acquire(selection, root=tmp_path / "manager") as selected:
        assert selected.commit == release
        assert not list(selected.checkout.parent.glob(".acquire-*"))


def test_queued_source_choice_cannot_change_before_participant_execution(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    import update_driver

    from worktree_manager import update_driver_cli

    root = tmp_path / "manager"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    original = update_driver_cli._source_key()
    config.set_source(config.RuntimeSource("dev", allow_dev=True), root=root)
    request = SimpleNamespace(
        state="running", spec=SimpleNamespace(
            backend="manager", source=original,
            context=(("WORKTREE_MANAGER_ROOT", str(root)),),
        ),
    )

    class Custody:
        request_id = "synthetic-request"
        root = tmp_path

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            pytest.fail("changed source must be rejected before executing the participant")

    monkeypatch.setattr(update_driver, "borrowed_custody", Custody)
    monkeypatch.setattr(update_driver, "status", lambda *args, **kwargs: request)
    assert update_driver_cli.cmd_update_request([]) == 1
    assert "source changed after submission" in capsys.readouterr().err
