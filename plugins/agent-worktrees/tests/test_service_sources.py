"""Isolated real-Git service deployment contracts; no host installers run."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, service_sources, services_cli


def _git(repo, *args):
    return git_ops.git(
        "-c", "user.name=Example", "-c", "user.email=example@example.com",
        *args, cwd=repo, isolated_repository=True, no_hooks=True, timeout=20,
    ).stdout.strip()


@pytest.fixture(scope="module")
def repository_template(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("service-source-template")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-b", "main")
    (upstream / "base.txt").write_text("base\n", encoding="utf-8")
    (upstream / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-qm", "Initial")
    anchor = tmp_path / "anchor"
    _git(tmp_path, "clone", "-q", "--bare", str(upstream), str(anchor))
    service = upstream / "services" / "example"
    service.mkdir(parents=True)
    (service / "service.yaml").write_text(
        "name: example\ntype: scheduled-task\ndeployments:\n"
        "  testenv:\n    type: full\n",
        encoding="utf-8",
    )
    (service / "install.py").write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['SERVICE_TEST_RECEIPT']).write_text(json.dumps("
        "{'cwd': str(pathlib.Path.cwd()), 'action': sys.argv[1], "
        "'source': str(pathlib.Path(__file__).resolve())}))\n",
        encoding="utf-8",
    )
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-qm", "Add reviewed service")
    reviewed = _git(upstream, "rev-parse", "HEAD")
    return tmp_path, reviewed


@pytest.fixture
def repository(repository_template, tmp_path, monkeypatch):
    template, reviewed = repository_template
    upstream, anchor = tmp_path / "upstream", tmp_path / "anchor"
    shutil.copytree(template / "upstream", upstream)
    shutil.copytree(template / "anchor", anchor)
    _git(anchor, "remote", "set-url", "origin", str(upstream))
    receipt = tmp_path / "receipt.json"
    monkeypatch.setenv("SERVICE_TEST_RECEIPT", str(receipt))
    config = SimpleNamespace(
        machine="testenv", platform="linux",
        default_repo=SimpleNamespace(remote="origin", default_branch="main", service_paths=[]),
    )
    monkeypatch.setattr(service_sources.cfg, "project_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(services_cli.cfg, "load_config", lambda: config)
    monkeypatch.setattr(services_cli.worktree_identity, "_find_repo_dir", lambda: anchor)
    monkeypatch.setattr(services_cli.svc, "_preferred_installer_order", lambda: ("install.py",))
    monkeypatch.setattr(
        services_cli, "_installer_cmd",
        lambda installer, args: [
            getattr(sys, "_base_executable", sys.executable), "-I", "-S", str(installer), *args,
        ],
    )
    monkeypatch.setattr(
        services_cli, "subprocess",
        SimpleNamespace(
            TimeoutExpired=subprocess.TimeoutExpired,
            run=lambda *args, **kwargs: subprocess.run(
                *args, **kwargs, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ),
        ),
    )
    return SimpleNamespace(
        upstream=upstream, anchor=anchor, config=config, reviewed=reviewed, receipt=receipt,
    )


@pytest.mark.parametrize("batch", [False, True])
def test_bare_deploy_runs_reviewed_installer_not_active_worktree(repository, batch):
    r = repository
    active = r.anchor.parent / "active"
    _git(r.anchor, "worktree", "add", "-b", "unreviewed", str(active))
    (active / "wip.txt").write_text("never deploy\n", encoding="utf-8")
    _git(active, "add", ".")
    _git(active, "commit", "-qm", "Unreviewed")
    before = _git(active, "rev-parse", "HEAD")
    assert not (r.anchor / "services" / "example" / "service.yaml").exists()
    argv = ["--all", "install", "--force"] if batch else ["example", "install"]
    assert services_cli.cmd_services_dispatch(argv) == 0
    receipt = json.loads(r.receipt.read_text())
    source = Path(receipt["cwd"])
    assert source != active and source != r.anchor
    assert receipt["action"] == "install"
    assert Path(receipt["source"]).is_relative_to(source)
    assert _git(source, "rev-parse", "HEAD") == r.reviewed
    assert not (source / "wip.txt").exists()
    assert _git(r.anchor, "rev-parse", "--is-bare-repository") == "true"
    assert _git(active, "rev-parse", "HEAD") == before
    assert source.exists()  # Redirector/source provenance remains usable after return.


def test_resolve_normalizes_linked_worktree_to_bare_anchor(repository):
    """#7088: `resolve()` must normalize a linked worktree of a pure bare
    anchor to the anchor itself before anything else -- not stubbing
    `_find_repo_dir()` so this exercises the real CLI entry path a linked
    worktree hits."""
    r = repository
    service_sources.ensure_current(r.anchor, r.config)
    active = r.anchor.parent / "active-for-resolve"
    _git(r.anchor, "worktree", "add", str(active), r.reviewed)
    source = service_sources.resolve(active, r.config, deploy=True)
    assert source != active
    assert _git(source, "rev-parse", "HEAD") == r.reviewed
    assert _git(r.anchor, "rev-parse", "--is-bare-repository") == "true"


def test_bare_sync_suppresses_reference_transaction_hook(repository):
    """#7088: a deployment sync of a bare anchor must not let a configured
    ``reference-transaction`` hook run -- it could reject or side-effect the
    fetch/update-ref ref writes :func:`bare_anchor_sync.sync_bare_anchor`
    performs, same as the existing non-bare ``post-merge`` suppression."""
    r = repository
    hooks = r.anchor.parent / "bare-hooks"
    hooks.mkdir()
    marker = r.anchor.parent / "bare-hook-ran"
    hook = hooks / "reference-transaction"
    hook.write_text("#!/bin/sh\n" + f"touch '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(r.anchor, "config", "core.hooksPath", str(hooks))
    (r.upstream / "new.txt").write_text("reviewed update\n", encoding="utf-8")
    _git(r.upstream, "add", ".")
    _git(r.upstream, "commit", "-qm", "Reviewed update")
    service_sources.ensure_current(r.anchor, r.config)
    assert not marker.exists()
    assert _git(r.anchor, "rev-parse", "main") == _git(r.upstream, "rev-parse", "main")


def test_bare_list_status_and_cache_reuse(repository, capsys):
    r = repository
    service_sources.ensure_current(r.anchor, r.config)
    source = service_sources.resolve(r.anchor, r.config)
    capsys.readouterr()
    assert services_cli.cmd_services_dispatch(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "example"
    assert services_cli.cmd_services_dispatch(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "example"
    assert service_sources.resolve(r.anchor, r.config) == source
    assert not r.receipt.exists()


@pytest.mark.parametrize("failure", ["fetch", "diverged"])
@pytest.mark.parametrize("batch", [False, True])
def test_sync_failure_never_runs_installer(repository, failure, batch, capsys):
    r = repository
    if failure == "fetch":
        _git(r.anchor, "remote", "set-url", "origin", str(r.anchor.parent / "missing"))
    else:
        _git(r.anchor, "fetch", "origin", "refs/heads/main:refs/heads/main")
        _git(r.upstream, "reset", "--soft", "HEAD~1")
        (r.upstream / "other.txt").write_text("different\n", encoding="utf-8")
        _git(r.upstream, "add", ".")
        _git(r.upstream, "commit", "-qm", "Divergent")
    argv = ["--all", "install", "--force"] if batch else ["example", "install"]
    assert services_cli.cmd_services_dispatch(argv) == 1
    assert not r.receipt.exists()
    text = capsys.readouterr()
    assert "Cannot resolve service source" in text.err + text.out
    assert "Deploying from current" not in text.err + text.out


@pytest.mark.parametrize("mutation", ["tracked", "untracked", "head"])
def test_changed_pinned_checkout_refused_without_overwrite(repository, mutation):
    r = repository
    source = service_sources.resolve(r.anchor, r.config, deploy=True)
    if mutation == "tracked":
        (source / "base.txt").write_text("edited\n", encoding="utf-8")
    elif mutation == "untracked":
        (source / "local.txt").write_text("keep\n", encoding="utf-8")
    else:
        _git(source, "checkout", "--detach", "HEAD~1")
    with pytest.raises(RuntimeError, match="refusing to overwrite or deploy"):
        service_sources.resolve(r.anchor, r.config)
    assert not r.receipt.exists()


def test_ignored_installer_build_artifacts_do_not_break_repeated_discovery(repository):
    r = repository
    source = service_sources.resolve(r.anchor, r.config, deploy=True)
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "build.pyc").write_bytes(b"generated build artifact")
    assert service_sources.resolve(r.anchor, r.config) == source
    assert services_cli.cmd_services_dispatch(["status", "--json"]) == 0


def test_nonbare_sync_retains_checkout_and_rejects_local_commits(repository):
    r = repository
    ordinary = r.anchor.parent / "ordinary"
    _git(r.anchor.parent, "clone", "-q", str(r.upstream), str(ordinary))
    assert service_sources.resolve(ordinary, r.config, deploy=True) == ordinary
    hooks = r.anchor.parent / "hooks"
    hooks.mkdir()
    marker = r.anchor.parent / "hook-ran"
    hook = hooks / "post-merge"
    hook.write_text(
        "#!/bin/sh\n" + f"touch '{marker.as_posix()}'\n", encoding="utf-8",
    )
    hook.chmod(0o755)
    _git(ordinary, "config", "core.hooksPath", str(hooks))
    (r.upstream / "new.txt").write_text("reviewed update\n", encoding="utf-8")
    _git(r.upstream, "add", ".")
    _git(r.upstream, "commit", "-qm", "Reviewed update")
    assert service_sources.resolve(ordinary, r.config, deploy=True) == ordinary
    assert not marker.exists()
    assert (ordinary / "new.txt").read_text(encoding="utf-8") == "reviewed update\n"
    (ordinary / "local.txt").write_text("unmerged\n", encoding="utf-8")
    _git(ordinary, "add", ".")
    _git(ordinary, "commit", "-qm", "Local only")
    with pytest.raises(RuntimeError, match="local commits"):
        service_sources.resolve(ordinary, r.config, deploy=True)


def test_bare_converted_clone_keeps_configuration(repository):
    r = repository
    converted = r.anchor.parent / "converted"
    _git(r.anchor.parent, "clone", "-q", str(r.upstream), str(converted))
    _git(converted, "config", "core.bare", "true")
    source = service_sources.resolve(converted, r.config, deploy=True)
    assert source != converted
    assert _git(source, "rev-parse", "HEAD") == r.reviewed
    assert _git(converted, "config", "--get", "core.bare") == "true"


def test_bare_converted_clone_suppresses_post_index_change_hook(repository):
    """#7088: `no_hooks` must also cover the converted-bare file sync in
    `bare_anchor_files` (`update-index --refresh`, `read-tree -m -u`), not
    only the direct fetch/ref-update suppression in `sync_bare_anchor`."""
    r = repository
    converted = r.anchor.parent / "converted-hook"
    _git(r.anchor.parent, "clone", "-q", str(r.upstream), str(converted))
    _git(converted, "config", "core.bare", "true")
    hooks = r.anchor.parent / "converted-hooks"
    hooks.mkdir()
    marker = r.anchor.parent / "converted-hook-ran"
    hook = hooks / "post-index-change"
    hook.write_text("#!/bin/sh\n" + f"touch '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(converted, "config", "core.hooksPath", str(hooks))
    (r.upstream / "converted-update.txt").write_text("reviewed update\n", encoding="utf-8")
    _git(r.upstream, "add", ".")
    _git(r.upstream, "commit", "-qm", "Reviewed converted update")
    service_sources.ensure_current(converted, r.config)
    assert not marker.exists()
    assert (converted / "converted-update.txt").read_text(encoding="utf-8") == "reviewed update\n"


def test_lost_checkout_creation_race_revalidates_published_source(repository, monkeypatch, caplog):
    r = repository
    service_sources.ensure_current(r.anchor, r.config)
    real_git = service_sources._git

    def raced_git(repo, *args):
        result = real_git(repo, *args)
        if args[:2] == ("worktree", "add"):
            raise git_ops.GitError(["git", *args], 128, "checkout already published")
        return result

    monkeypatch.setattr(service_sources, "_git", raced_git)
    source = service_sources.resolve(r.anchor, r.config)
    assert _git(source, "rev-parse", "HEAD") == r.reviewed
    assert "validating an existing checkout" in caplog.text


def test_anchor_key_collision_refuses_foreign_checkout(repository, monkeypatch):
    r = repository
    monkeypatch.setattr(
        service_sources, "hashlib",
        SimpleNamespace(sha256=lambda value: SimpleNamespace(hexdigest=lambda: "a" * 64)),
    )
    source = service_sources.resolve(r.anchor, r.config, deploy=True)
    other = r.anchor.parent / "other-anchor"
    _git(r.anchor.parent, "clone", "-q", "--bare", str(r.anchor), str(other))
    with pytest.raises(RuntimeError, match="belongs to another anchor"):
        service_sources.resolve(other, r.config)
    assert _git(source, "rev-parse", "HEAD") == r.reviewed


def test_regular_file_in_source_slot_is_explicitly_refused(repository, monkeypatch):
    r = repository
    monkeypatch.setattr(
        service_sources, "hashlib",
        SimpleNamespace(sha256=lambda value: SimpleNamespace(hexdigest=lambda: "b" * 64)),
    )
    commit = _git(r.anchor, "rev-parse", "refs/heads/main")
    slot = service_sources.cfg.project_dir() / "service-sources" / ("b" * 16) / commit
    slot.parent.mkdir(parents=True)
    slot.write_text("preserve this file\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a real checkout directory"):
        service_sources.resolve(r.anchor, r.config)
    assert slot.read_text(encoding="utf-8") == "preserve this file\n"
