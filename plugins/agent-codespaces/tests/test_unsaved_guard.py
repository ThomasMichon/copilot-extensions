"""GitHub's stale "unsaved changes" delete refusal: audit every checkout on the
box and force only that refusal when all of them are verified clean."""

from __future__ import annotations

import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from agent_codespaces import lifecycle, unsaved_guard
from agent_codespaces.unsaved_guard import CheckoutAudit, CheckoutState, UnsavedWorkError

NAME = "probable-space-x1y2z3"
REFUSAL = (f"unable to confirm: codespace {NAME} has unsaved changes "
           "(use --force to override)")


# --- parsing ----------------------------------------------------------------

def test_refusal_detection_is_exact():
    assert unsaved_guard.is_unsaved_changes_refusal(REFUSAL)
    assert unsaved_guard.is_unsaved_changes_refusal(f"\n{REFUSAL}\n", NAME)
    assert not unsaved_guard.is_unsaved_changes_refusal(REFUSAL, "another-space")
    assert not unsaved_guard.is_unsaved_changes_refusal("HTTP 404: Not Found")
    assert not unsaved_guard.is_unsaved_changes_refusal("")
    # an incidental mention inside some other failure never counts
    assert not unsaved_guard.is_unsaved_changes_refusal(
        f"{REFUSAL}\nerror deleting codespace: HTTP 500")
    assert not unsaved_guard.is_unsaved_changes_refusal(
        f"HTTP 502: codespace {NAME} has unsaved changes")


def test_parse_audit_requires_completion_marker():
    out = "CHECKOUT\t0\t0\t0\t/workspaces/a\n"
    assert unsaved_guard.parse_audit(out).known is False
    # the marker must be the final line, not merely present (e.g. in a path)
    sneaky = "CHECKOUT\t0\t0\t0\t/workspaces/CHECKOUT_AUDIT=1\n"
    assert unsaved_guard.parse_audit(sneaky).known is False
    truncated = "CHECKOUT_AUDIT=1\nCHECKOUT\t0\t0\t0\t/workspaces/a\n"
    assert unsaved_guard.parse_audit(truncated).known is False
    assert unsaved_guard.parse_audit(None).known is False


def test_parse_audit_per_checkout():
    out = "\n".join([
        "CHECKOUT\t0\t0\t0\t/workspaces/a",
        "CHECKOUT\t1\t0\t0\t/workspaces/a.worktrees/w1",
        "SAMPLE\t/workspaces/a.worktrees/w1\t?? notes.txt",
        "CHECKOUT\t0\t2\t1\t/workspaces/b",
        "CHECKOUT_ERR\t/workspaces/c",
        "CHECKOUT_AUDIT=1",
    ])
    audit = unsaved_guard.parse_audit(out)
    assert audit.known and not audit.all_clean
    assert [c.path for c in audit.dirty_checkouts] == [
        "/workspaces/a.worktrees/w1", "/workspaces/b", "/workspaces/c",
    ]
    assert audit.dirty_checkouts[0].sample == ["?? notes.txt"]
    clean = unsaved_guard.parse_audit("CHECKOUT\t0\t0\t0\t/workspaces/a\nCHECKOUT_AUDIT=1")
    assert clean.all_clean


def test_checkout_error_is_never_overwritten_by_a_clean_record():
    out = "\n".join([
        "CHECKOUT_ERR\t/workspaces/a",  # e.g. `git worktree list` failed
        "CHECKOUT\t0\t0\t0\t/workspaces/a",
        "CHECKOUT_AUDIT=1",
    ])
    audit = unsaved_guard.parse_audit(out)
    assert not audit.all_clean
    assert [c.path for c in audit.dirty_checkouts] == ["/workspaces/a"]


def test_empty_audit_is_not_clean():
    assert not CheckoutAudit(known=True, checkouts=[]).all_clean


def test_error_lists_each_dirty_checkout():
    audit = CheckoutAudit(known=True, checkouts=[
        CheckoutState("/w/a"),
        CheckoutState("/w/b", dirty=True, sample=[" M x.py"]),
        CheckoutState("/w/c", ahead=3),
    ])
    msg = str(UnsavedWorkError(NAME, audit))
    assert "/w/b: uncommitted changes" in msg and " M x.py" in msg
    assert "/w/c: 3 commit(s) on no remote" in msg and "/w/a" not in msg
    assert "could not audit" in str(UnsavedWorkError(NAME, CheckoutAudit(False, error="ssh down")))


# --- the real audit script against real git checkouts ----------------------

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(path, remotes):
    remote = remotes / f"{path.name}.git"
    remote.mkdir(parents=True)
    _git("init", "-q", "--bare", str(remote), cwd=remotes)
    _git("init", "-q", "-b", "main", str(path), cwd=path.parent)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "t", cwd=path)
    (path / "f.txt").write_text("x\n")
    _git("add", "f.txt", cwd=path)
    _git("commit", "-qm", "init", cwd=path)
    _git("remote", "add", "origin", str(remote), cwd=path)
    _git("push", "-q", "origin", "main", cwd=path)


def _run_audit(root, extra=()):
    cmd = unsaved_guard.audit_command(workspace_root=str(root), extra_dirs=extra)
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
    return unsaved_guard.parse_audit(out.stdout)


@pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")),
                    reason="needs git + bash")
def test_audit_script_covers_nested_repos_and_linked_worktrees(tmp_path):
    remote = tmp_path / "remotes"
    root = tmp_path / "workspaces"
    (root / "group").mkdir(parents=True)
    main_repo, nested = root / "main", root / "group" / "nested"
    main_repo.mkdir()
    nested.mkdir()
    _repo(main_repo, remote)
    _repo(nested, remote)
    linked = tmp_path / "elsewhere" / "wt"
    _git("worktree", "add", "-q", "-b", "feature", str(linked), cwd=main_repo)

    audit = _run_audit(root)
    assert audit.known and audit.all_clean
    paths = {c.path for c in audit.checkouts}
    assert {str(main_repo), str(nested)} <= {p for p in paths}
    assert any(p.endswith("elsewhere/wt") for p in paths)

    (linked / "scratch.txt").write_text("unsaved\n")
    # repo config (shared by the linked worktree) must not hide untracked work
    _git("config", "status.showUntrackedFiles", "no", cwd=main_repo)
    (nested / "f.txt").write_text("y\n")
    _git("commit", "-qam", "local only", cwd=nested)
    audit = _run_audit(root)
    dirty = {c.path.rsplit("/", 2)[-1]: c for c in audit.dirty_checkouts}
    assert set(dirty) == {"wt", "nested"}
    assert dirty["wt"].dirty and "?? scratch.txt" in dirty["wt"].sample
    assert dirty["nested"].ahead == 1 and dirty["nested"].unpushed_branches == 1
    assert not any(c.path == str(main_repo) for c in audit.dirty_checkouts)


@pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")),
                    reason="needs git + bash")
def test_audit_script_fails_closed_on_git_errors_and_odd_paths(tmp_path):
    remote = tmp_path / "remotes"
    root = tmp_path / "work space"
    root.mkdir()
    odd = root / "back\\slash"
    odd.mkdir()
    _repo(odd, remote)
    broken = root / "broken"
    broken.mkdir()
    _repo(broken, remote)
    # corrupt the object store so rev-list / status fail -> must not read clean
    for obj in (broken / ".git" / "objects").rglob("*"):
        if obj.is_file():
            obj.chmod(0o600)
            obj.write_bytes(b"garbage")
    linked = tmp_path / "outside" / "wt\\odd"
    _git("worktree", "add", "-q", "-b", "side", str(linked), cwd=odd)
    (linked / "unsaved.txt").write_text("x\n")
    audit = _run_audit(root)
    assert audit.known
    by_name = {c.path.rsplit("/", 1)[-1]: c for c in audit.checkouts}
    assert by_name["back\\slash"].clean  # backslash path emitted verbatim
    # a linked worktree with a backslash in its path is found (-z, unquoted)
    assert by_name["wt\\odd"].dirty
    assert not by_name["broken"].clean
    assert not audit.all_clean


@pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")),
                    reason="needs git + bash")
@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="needs POSIX permissions enforced (non-root)")
def test_audit_script_blocks_on_missing_or_unreadable_checkouts(tmp_path):
    remotes = tmp_path / "remotes"
    root = tmp_path / "ws"
    root.mkdir()
    repo = root / "repo"
    repo.mkdir()
    _repo(repo, remotes)
    hidden_parent = tmp_path / "locked"
    linked = hidden_parent / "wt"
    _git("worktree", "add", "-q", "-b", "side", str(linked), cwd=repo)
    gone = tmp_path / "gone-wt"
    _git("worktree", "add", "-q", "-b", "gone", str(gone), cwd=repo)
    shutil.rmtree(gone)  # stale entry, not yet pruned
    opaque = root / "opaque"
    opaque.mkdir()
    hidden_parent.chmod(0o000)
    opaque.chmod(0o000)
    try:
        audit = _run_audit(root)
    finally:
        hidden_parent.chmod(0o755)
        opaque.chmod(0o755)
    errs = {c.path for c in audit.checkouts if c.error}
    assert {str(linked), str(gone), str(opaque)} <= errs
    assert not audit.all_clean


# --- delete_codespace integration -------------------------------------------

@pytest.fixture
def gh(monkeypatch):
    calls: list[list[str]] = []
    results: list[SimpleNamespace] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return results.pop(0)

    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)
    monkeypatch.setattr(lifecycle, "account_for_codespace", lambda name: None)
    monkeypatch.setattr("agent_codespaces.account_binding.unbind", lambda name: None)
    return SimpleNamespace(calls=calls, results=results)


def _r(rc, err=""):
    return SimpleNamespace(returncode=rc, stdout="", stderr=err)


def test_stale_flag_with_all_checkouts_clean_forces_delete(gh, monkeypatch):
    audited = []
    monkeypatch.setattr(unsaved_guard, "audit_checkouts", lambda name, account=None, token=None: (
        audited.append(name) or CheckoutAudit(True, [CheckoutState("/w/a")])))
    gh.results[:] = [_r(1, REFUSAL), _r(0)]

    lifecycle.delete_codespace(NAME)

    assert audited == [NAME]
    assert "--force" not in gh.calls[0]
    assert gh.calls[1] == [*gh.calls[0], "--force"]


def test_dirty_checkout_refuses_and_names_it(gh, monkeypatch):
    monkeypatch.setattr(unsaved_guard, "audit_checkouts", lambda name, account=None, token=None: (
        CheckoutAudit(True, [CheckoutState("/w/a"), CheckoutState("/w/b", dirty=True)])))
    gh.results[:] = [_r(1, REFUSAL)]

    with pytest.raises(UnsavedWorkError) as exc:
        lifecycle.delete_codespace(NAME)
    assert "/w/b" in str(exc.value) and isinstance(exc.value, RuntimeError)
    assert len(gh.calls) == 1


def test_unauditable_box_refuses(gh, monkeypatch):
    monkeypatch.setattr(unsaved_guard, "audit_checkouts",
                        lambda name, account=None, token=None: CheckoutAudit(False, error="ssh down"))
    gh.results[:] = [_r(1, REFUSAL)]
    with pytest.raises(UnsavedWorkError, match="could not audit"):
        lifecycle.delete_codespace(NAME)
    assert len(gh.calls) == 1


def test_other_failures_and_explicit_force_skip_the_audit(gh, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not audit")
    monkeypatch.setattr(unsaved_guard, "audit_checkouts", boom)

    gh.results[:] = [_r(1, "HTTP 404: Not Found")]
    with pytest.raises(RuntimeError, match="404"):
        lifecycle.delete_codespace(NAME)

    gh.results[:] = [_r(1, REFUSAL)]
    with pytest.raises(RuntimeError, match="unsaved changes"):
        lifecycle.delete_codespace(NAME, force=True)
    assert gh.calls[-1][-1] == "--force"


def test_exact_token_is_reused_for_the_audit(gh, monkeypatch):
    seen = {}

    def audit(name, account=None, token=None):
        seen.update(account=account, token=token)
        return CheckoutAudit(True, [CheckoutState("/w/a")])

    monkeypatch.setattr(unsaved_guard, "audit_checkouts", audit)
    monkeypatch.setattr(lifecycle, "account_for_codespace",
                        lambda name: (_ for _ in ()).throw(AssertionError("no re-derive")))
    gh.results[:] = [_r(1, REFUSAL), _r(0)]
    lifecycle.delete_codespace(NAME, token="tok-123")
    assert seen == {"account": None, "token": "tok-123"}


def test_probe_builds_source_with_exact_token(monkeypatch):
    import asyncio

    import agent_codespaces.codespace_config as cc

    built = {}

    class FakeSource:
        def __init__(self, name, *, account=None, token=None):
            built.update(name=name, account=account, token=token)

    class FakeManager:
        async def ensure_connected(self, *a):
            return None

        async def exec_command(self, name, cmd, timeout=None):
            return SimpleNamespace(exit_code=0, stdout="CHECKOUT_AUDIT=1\n", stderr="")

        async def disconnect(self, name):
            return None

    import ssh_manager
    monkeypatch.setattr(cc, "CodespaceSource", FakeSource)
    monkeypatch.setattr(ssh_manager, "ConnectionManager", FakeManager)
    asyncio.run(unsaved_guard._probe(NAME, None, 5, "tok-123"))
    assert built == {"name": NAME, "account": None, "token": "tok-123"}


def test_audit_checkouts_degrades_to_unknown(monkeypatch):
    async def fail(name, account, timeout, token=None):
        raise OSError("no route")
    monkeypatch.setattr(unsaved_guard, "_probe", fail)
    audit = unsaved_guard.audit_checkouts(NAME, timeout=1)
    assert audit.known is False and "no route" in audit.error
