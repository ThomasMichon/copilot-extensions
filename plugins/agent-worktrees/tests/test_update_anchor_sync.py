"""Tests for the ``update`` anchor fast-forward step.

``_fast_forward_project_anchors`` closes the anchor-sync lag: after ``update``
refreshes the plugin payload, the managed repo's anchor checkout (source of
truth for in-repo ``.agent-worktrees/config.yaml`` bindings) is fast-forwarded
too -- but only when it is on the default branch, clean, and strictly behind.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import git_ops


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", f"add {name}", cwd=repo)


@pytest.fixture
def anchor_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Return (seed, anchor): a clone tracking origin/master plus a seed
    checkout used to advance origin so the anchor falls behind."""
    origin_bare = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "master", str(origin_bare), cwd=tmp_path)

    seed = tmp_path / "seed"
    _git("clone", str(origin_bare), str(seed), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "Test", cwd=seed)
    _commit(seed, "base.txt", "v1")
    _git("push", "origin", "master", cwd=seed)

    anchor = tmp_path / "anchor"
    _git("clone", str(origin_bare), str(anchor), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=anchor)
    _git("config", "user.name", "Test", cwd=anchor)
    return seed, anchor


def _advance_origin(seed: Path, name: str = "next.txt") -> None:
    _commit(seed, name, "more")
    _git("push", "origin", "master", cwd=seed)


def _install_config(monkeypatch: pytest.MonkeyPatch, anchor: Path) -> None:
    repo = cfg.RepoConfig(
        anchor=str(anchor),
        worktree_root=str(anchor.parent / "wt"),
        default_branch="master",
        remote="origin",
    )
    config = cfg.Config(
        srcroot=str(anchor.parent),
        machine="test",
        platform="linux",
        repo_name="anchor",
        repos={"anchor": repo},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)


def test_clean_behind_anchor_is_fast_forwarded(anchor_repo, monkeypatch):
    seed, anchor = anchor_repo
    _advance_origin(seed)
    _install_config(monkeypatch, anchor)

    m._fast_forward_project_anchors()

    # The commit pushed to origin is now present in the anchor checkout.
    assert (anchor / "next.txt").exists()


@pytest.mark.parametrize("auth_mode", ["on", "off"])
def test_non_https_anchor_sync_never_uses_callers_repository(
    anchor_repo, monkeypatch, auth_mode,
):
    from agent_worktrees import update_auth_context as auth_context
    from agent_worktrees import update_auth_sources

    seed, anchor = anchor_repo
    caller = anchor.parent / "caller"
    _git("clone", str(anchor.parent / "origin.git"), str(caller), cwd=anchor.parent)
    _git("checkout", "-b", "feature", cwd=caller)
    caller_head = git_ops.git("rev-parse", "HEAD", cwd=caller, isolated_repository=True).stdout
    _advance_origin(seed)
    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(cfg, "active_project", lambda: "anchor")
    monkeypatch.setattr(update_auth_sources, "marketplaces", lambda _: [])
    monkeypatch.setenv("CE_UPDATE_AUTH", auth_mode)
    monkeypatch.setenv("GIT_DIR", str(caller / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(caller))
    monkeypatch.setenv("GIT_INDEX_FILE", str(caller / ".git" / "index"))

    with auth_context.session() as session:
        if session is not None:
            session.prepare(None, include_anchors=True)
            assert session.anchor_reports == {}, "local remotes have no HTTPS auth report"
        m._fast_forward_project_anchors()

    assert git_ops.git("rev-parse", "HEAD", cwd=caller, isolated_repository=True).stdout == caller_head
    assert git_ops.git(
        "branch", "--show-current", cwd=caller, isolated_repository=True,
    ).stdout.strip() == "feature"
    assert not (caller / "next.txt").exists()
    assert (anchor / "next.txt").exists(), "the intended anchor must still be fast-forwarded"


def test_repository_scope_without_auth_preserves_legacy_account_policy(monkeypatch, tmp_path):
    from agent_worktrees import update_auth_context as auth_context

    monkeypatch.setenv("CE_UPDATE_AUTH", "off")
    monkeypatch.setattr(git_ops, "_remote_url", lambda *a, **k: "https://example.test/team/repo")
    seen = []
    monkeypatch.setattr(
        git_ops, "_auth_config_args_for_url",
        lambda url: seen.append(url) or ["legacy-auth-policy-fixture"],
    )
    with auth_context.session():
        with auth_context.repository_scope(tmp_path):
            assert not auth_context.managed_repository()
            assert git_ops._auth_config_args("origin", cwd=tmp_path) == ["legacy-auth-policy-fixture"]
    assert seen == ["https://example.test/team/repo"]


def test_up_to_date_anchor_is_left_alone(anchor_repo, monkeypatch):
    _seed, anchor = anchor_repo
    _install_config(monkeypatch, anchor)

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(anchor),
        capture_output=True, text=True,
    ).stdout.strip()
    m._fast_forward_project_anchors()
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(anchor),
        capture_output=True, text=True,
    ).stdout.strip()
    assert head_before == head_after


def test_non_default_branch_anchor_is_skipped(anchor_repo, monkeypatch):
    seed, anchor = anchor_repo
    _advance_origin(seed)
    # Anchor is checked out on a feature branch, not master.
    _git("checkout", "-b", "feature", cwd=anchor)
    _install_config(monkeypatch, anchor)

    m._fast_forward_project_anchors()

    # Still on feature branch and the origin/master commit was NOT pulled in.
    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(anchor),
        capture_output=True, text=True,
    ).stdout.strip()
    assert current == "feature"
    assert not (anchor / "next.txt").exists()


def test_dirty_anchor_is_left_untouched(anchor_repo, monkeypatch):
    seed, anchor = anchor_repo
    _advance_origin(seed)
    (anchor / "base.txt").write_text("uncommitted change")
    _install_config(monkeypatch, anchor)

    m._fast_forward_project_anchors()

    # Fast-forward refused -- the new origin commit is not present.
    assert not (anchor / "next.txt").exists()


def test_no_config_is_non_fatal(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no config")

    monkeypatch.setattr(cfg, "load_config", _boom)
    # Must not raise.
    m._fast_forward_project_anchors()


def test_prepare_worktree_base_fetches_and_fast_forwards(anchor_repo):
    seed, anchor = anchor_repo
    _advance_origin(seed)

    prepared = git_ops.prepare_worktree_base(
        anchor,
        remote="origin",
        default_branch="master",
    )

    assert prepared.fetched is True
    assert prepared.fetch_error is None
    assert prepared.start_point == "origin/master"
    assert prepared.anchor.updated is True
    assert (anchor / "next.txt").exists()


def test_prepare_worktree_base_uses_fresh_remote_without_touching_dirty_anchor(
    anchor_repo,
):
    seed, anchor = anchor_repo
    _advance_origin(seed)
    (anchor / "base.txt").write_text("dirty")

    prepared = git_ops.prepare_worktree_base(
        anchor,
        remote="origin",
        default_branch="master",
    )

    assert prepared.start_point == "origin/master"
    assert prepared.anchor.reason == "dirty"
    assert not (anchor / "next.txt").exists()
    assert (anchor / "base.txt").read_text() == "dirty"


def test_prepare_worktree_base_honors_fast_forward_opt_out(anchor_repo):
    seed, anchor = anchor_repo
    _advance_origin(seed)

    prepared = git_ops.prepare_worktree_base(
        anchor,
        remote="origin",
        default_branch="master",
        fast_forward_anchor=False,
    )

    assert prepared.start_point == "origin/master"
    assert prepared.anchor.reason == "disabled"
    assert not (anchor / "next.txt").exists()


def test_prepare_worktree_base_uses_last_known_remote_ref_offline(anchor_repo):
    seed, anchor = anchor_repo
    _advance_origin(seed)
    _git("fetch", "origin", cwd=anchor)
    missing = anchor.parent / "missing-origin.git"
    _git("remote", "set-url", "origin", str(missing), cwd=anchor)

    prepared = git_ops.prepare_worktree_base(
        anchor,
        remote="origin",
        default_branch="master",
        fast_forward_anchor=False,
    )

    assert prepared.fetched is False
    assert prepared.fetch_error
    assert prepared.start_point == "origin/master"
    assert prepared.anchor.reason == "disabled"
    assert not (anchor / "next.txt").exists()


def test_resolve_remote_name_matches_registry_url(anchor_repo):
    _seed, anchor = anchor_repo
    remote_url = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(anchor),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert git_ops.resolve_remote_name(remote_url, cwd=anchor) == "origin"


# ── stale-anchor self-heal (catch-22 break) ─────────────────────────────────
# The interactive picker parses the anchor's machines.yaml to identify this
# machine; a stale anchor missing the self-entry crashes it, yet the anchor
# fast-forward that adds the entry otherwise runs only *after* a successful
# resolve. ``_heal_stale_anchor_if_self_missing`` breaks that loop by
# fast-forwarding the anchor first, but only when the self-entry is absent.


def _write_machines(repo: Path, keys: list[str]) -> None:
    lines = ["machines:"]
    for k in keys:
        lines += [f"  {k}:", f"    display_name: {k}", "    environment: Test"]
    (repo / "machines.yaml").write_text("\n".join(lines) + "\n")


def _make_origin_anchor(tmp_path: Path, base_keys: list[str]) -> tuple[Path, Path]:
    """(seed, anchor) sharing an origin; base commit carries machines(base_keys)."""
    origin_bare = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "master", str(origin_bare), cwd=tmp_path)

    seed = tmp_path / "seed"
    _git("clone", str(origin_bare), str(seed), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "Test", cwd=seed)
    (seed / "base.txt").write_text("v1")
    _write_machines(seed, base_keys)
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "base", cwd=seed)
    _git("push", "origin", "master", cwd=seed)

    anchor = tmp_path / "anchor"
    _git("clone", str(origin_bare), str(anchor), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=anchor)
    _git("config", "user.name", "Test", cwd=anchor)
    return seed, anchor


def test_self_missing_triggers_anchor_heal(tmp_path, monkeypatch):
    # Anchor knows only 'otherbox'; origin advances to add this host + a marker.
    seed, anchor = _make_origin_anchor(tmp_path, ["otherbox"])
    _write_machines(seed, ["otherbox", "testbox"])
    (seed / "next.txt").write_text("more")
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "add testbox", cwd=seed)
    _git("push", "origin", "master", cwd=seed)

    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(m._socket, "gethostname", lambda: "testbox")

    # Precondition: the stale anchor lacks this machine's self-entry.
    assert cfg.find_machine_entry(cfg.load_machines_yaml(anchor), "testbox") is None

    m._heal_stale_anchor_if_self_missing(cfg.load_config())

    # The anchor was fast-forwarded, so the self-entry now resolves.
    assert (anchor / "next.txt").exists()
    assert cfg.find_machine_entry(
        cfg.load_machines_yaml(anchor), "testbox") is not None


def test_self_present_skips_heal(tmp_path, monkeypatch):
    # This host is already in the anchor's machines.yaml -> no heal, no ff even
    # though origin has advanced (the marker must NOT be pulled in).
    seed, anchor = _make_origin_anchor(tmp_path, ["otherbox", "testbox"])
    (seed / "next.txt").write_text("more")
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "unrelated advance", cwd=seed)
    _git("push", "origin", "master", cwd=seed)

    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(m._socket, "gethostname", lambda: "testbox")

    m._heal_stale_anchor_if_self_missing(cfg.load_config())

    assert not (anchor / "next.txt").exists()


def test_heal_non_fatal_when_ff_raises(tmp_path, monkeypatch):
    _seed, anchor = _make_origin_anchor(tmp_path, ["otherbox"])
    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(m._socket, "gethostname", lambda: "testbox")

    def _boom() -> None:
        raise RuntimeError("ff failed")

    monkeypatch.setattr(m, "_fast_forward_project_anchors", _boom)

    config = cfg.load_config()
    # Must not raise, and returns the original config unchanged.
    assert m._heal_stale_anchor_if_self_missing(config) is config


def test_unreadable_registry_does_not_trigger_heal(tmp_path, monkeypatch):
    # A missing/malformed machines.yaml is treated as "present" so an unrelated
    # I/O error never fires the pull-forward (and its network fetch).
    _seed, anchor = _make_origin_anchor(tmp_path, ["otherbox"])
    (anchor / "machines.yaml").unlink()
    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(m._socket, "gethostname", lambda: "testbox")

    called = {"ff": 0}
    monkeypatch.setattr(
        m, "_fast_forward_project_anchors",
        lambda: called.__setitem__("ff", called["ff"] + 1))

    m._heal_stale_anchor_if_self_missing(cfg.load_config())
    assert called["ff"] == 0


def test_bare_anchor_is_synced_through_the_bare_path(anchor_repo, monkeypatch, capsys):
    # A worktree-class anchor is bare: the work-tree dirty check / merge can't
    # run there, so update must advance its ref (and files) via the bare path
    # instead of reporting it "dirty" and leaving it stale forever.
    seed, anchor = anchor_repo
    _git("config", "core.bare", "true", cwd=anchor)
    _advance_origin(seed)
    _install_config(monkeypatch, anchor)

    m._fast_forward_project_anchors()

    origin_head = subprocess.run(
        ["git", "rev-parse", "origin/master"], cwd=str(seed),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    anchor_head = subprocess.run(
        ["git", "-C", str(anchor), "rev-parse", "refs/heads/master"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert anchor_head == origin_head
    assert (anchor / "next.txt").exists()
    out = capsys.readouterr().out
    assert "synced" in out
    assert "dirty" not in out


def test_failed_anchor_fetch_is_reported(anchor_repo, monkeypatch, capsys):
    _seed, anchor = anchor_repo
    _git("remote", "set-url", "origin", str(anchor.parent / "missing-origin.git"), cwd=anchor)
    _install_config(monkeypatch, anchor)

    m._fast_forward_project_anchors()

    captured = capsys.readouterr()
    assert "fetch from origin failed" in captured.out + captured.err

def test_bare_anchor_sync_failure_is_reported_without_aborting_update(
    anchor_repo, monkeypatch, capsys
):
    from agent_worktrees import bare_anchor_sync

    _seed, anchor = anchor_repo
    _git("config", "core.bare", "true", cwd=anchor)
    _install_config(monkeypatch, anchor)

    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(["git", "fetch"], 180)

    monkeypatch.setattr(bare_anchor_sync, "sync_bare_anchor", _timeout)

    m._fast_forward_project_anchors()

    captured = capsys.readouterr()
    assert "error" in captured.out + captured.err

_SECRET_URL = "https://user:hunter2@example.invalid/repo.git"


@pytest.mark.parametrize(("detail", "level"), [
    ("abc..def; files=refreshed", "ok"),
    ("abc..def; files=refresh-failed: index locked", "warn"),
    ("abc..def; files=dirty", "warn"),
], ids=["refreshed", "refresh-failed", "dirty"])
def test_bare_anchor_file_status_decides_the_report(anchor_repo, monkeypatch, detail, level):
    # A `synced` ref with files left stale is not a clean sync (as `repos sync`).
    from agent_worktrees import bare_anchor_sync, output

    _seed, anchor = anchor_repo
    _git("config", "core.bare", "true", cwd=anchor)
    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(bare_anchor_sync, "sync_bare_anchor", lambda *a, **k: ("synced", detail))
    seen: list[tuple[str, str]] = []
    for name in ("ok", "warn", "info"):
        monkeypatch.setattr(output, name, lambda msg, _n=name: seen.append((_n, msg)))

    m._fast_forward_project_anchors()

    assert [n for n, msg in seen if str(anchor) in msg] == [level]


def test_bare_anchor_sync_detail_is_redacted(anchor_repo, monkeypatch, capsys):
    from agent_worktrees import bare_anchor_sync

    _seed, anchor = anchor_repo
    _git("config", "core.bare", "true", cwd=anchor)
    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(
        bare_anchor_sync, "sync_bare_anchor",
        lambda *a, **k: ("error", f"fatal: unable to access '{_SECRET_URL}'"),
    )

    m._fast_forward_project_anchors()

    captured = capsys.readouterr()
    assert "unable to access" in captured.out + captured.err
    assert "hunter2" not in captured.out + captured.err


def test_anchor_fetch_error_is_redacted(anchor_repo, monkeypatch, capsys):
    from types import SimpleNamespace

    _seed, anchor = anchor_repo
    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(
        git_ops, "fast_forward_worktree",
        lambda *a, **k: SimpleNamespace(
            fetch_error=f"fatal: unable to access '{_SECRET_URL}'",
            updated=False, reason="up-to-date", behind=0,
        ),
    )

    m._fast_forward_project_anchors()

    captured = capsys.readouterr()
    assert "fetch from origin failed" in captured.out + captured.err
    assert "hunter2" not in captured.out + captured.err


def test_update_redaction_works_without_the_telemetry_library(monkeypatch):
    from agent_worktrees import update_events

    monkeypatch.setattr(update_events, "_ut", None)
    text = update_events.redact(f"{_SECRET_URL}\nAuthorization: token-value")
    assert "hunter2" not in text and "token-value" not in text