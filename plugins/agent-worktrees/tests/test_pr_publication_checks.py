"""Real-Git parity for built-in publication evidence, without provider mutations."""
from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from agent_worktrees import config as cfg, git_ops
from agent_worktrees import pr_publication_checks as checks
from agent_worktrees.pr_publication_config import PublicationChecksConfig, parse_publication_checks


def git(root, *args):
    return git_ops.git(*args, cwd=root, isolated_repository=True, timeout=15, kill_tree=True).stdout.strip()


@pytest.fixture
def branch(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    lens = root / ".checks" / "lenses" / "runtime"
    lens.mkdir(parents=True)
    (lens / "SKILL.md").write_text("---\nname: runtime\n---\nReview runtime changes.\n", "utf-8")
    (root / "a.txt").write_text("one\n", "utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "checkout", "-qb", "change")
    (root / "a.txt").write_text("two\n", "utf-8")
    git(root, "commit", "-qam", "change")
    return root


POLICY = PublicationChecksConfig(review_lenses_path=".checks/lenses")
BASE = "origin/main"


def record(root, policy=POLICY):
    return checks.record_self_review(root, BASE, policy, lenses=["runtime"], summary="checked", fixed="")


def test_receipt_is_local_exact_and_whitespace_sensitive(branch):
    assert not checks.before_publish(branch, BASE, POLICY)["ok"]
    out = record(branch)
    receipt = json.loads(Path(out["receipt"]).read_text("utf-8"))
    assert receipt["source_commit"] == git(branch, "rev-parse", "HEAD")
    assert receipt["binding"]["base_ref"] == BASE
    assert receipt["binding"]["policy_hash"] == out["policy_hash"]
    assert not git(branch, "status", "--porcelain")
    assert checks.before_publish(branch, BASE, POLICY)["ok"]
    (branch / "a.txt").write_text("two \n", "utf-8")
    git(branch, "commit", "-qam", "whitespace")
    assert not checks.before_publish(branch, BASE, POLICY)["ok"]


def test_unrelated_base_merge_preserves_exact_review(branch):
    record(branch)
    git(branch, "checkout", "-q", "main")
    (branch / "other.txt").write_text("base change\n", "utf-8")
    git(branch, "add", ".")
    git(branch, "commit", "-qm", "elsewhere")
    git(branch, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(branch, "checkout", "-q", "change")
    git(branch, "merge", "-q", "--no-edit", "origin/main")
    assert checks.before_publish(branch, BASE, POLICY)["ok"]


def test_optional_ssh_binding_preserves_legacy_receipt_identity_when_unset(branch):
    legacy = asdict(POLICY)
    del legacy["ssh_url"]
    expected = checks._hash({"config": legacy, "lenses": checks._lenses(branch, POLICY)})
    receipt = record(branch)
    assert receipt["policy_hash"] == expected
    assert checks.before_publish(branch, BASE, POLICY)["ok"]
    bound = replace(POLICY, ssh_url="git@forge.invalid:example/project.git")
    assert not checks.before_publish(branch, BASE, bound)["ok"]
    assert record(branch, bound)["policy_hash"] != expected


def test_dirty_expected_head_policy_and_base_fail_closed(branch):
    record(branch)
    assert not checks.before_publish(branch, BASE, POLICY, expected_head="HEAD~1")["ok"]
    assert not checks.before_publish(branch, "missing", POLICY)["ok"]
    assert not checks.before_publish(branch, BASE, replace(POLICY, guidance="new policy"))["ok"]
    git(branch, "update-ref", "refs/remotes/alternate/main", "origin/main")
    assert not checks.before_publish(branch, "alternate/main", POLICY)["ok"]
    (branch / "a.txt").write_text("uncommitted", "utf-8")
    assert not checks.before_publish(branch, BASE, POLICY)["ok"]
    with pytest.raises(checks.CheckError, match="uncommitted"):
        record(branch)


def test_lens_content_change_invalidates_review(branch):
    record(branch)
    (branch / ".checks" / "lenses" / "runtime" / "SKILL.md").write_text("New lens\n", "utf-8")
    git(branch, "commit", "-qam", "change lens")
    assert not checks.before_publish(branch, BASE, POLICY)["ok"]


def test_latin1_runtime_bytes_cannot_reuse_an_earlier_receipt(branch):
    source = b"# coding: latin-1\nVALUE = 'caf\xe9'\n"
    compile(source, "latin.py", "exec")
    (branch / "latin.py").write_bytes(source)
    git(branch, "add", ".")
    git(branch, "commit", "-qm", "Latin-1 behavior")
    reviewed = record(branch)
    changed = source.replace(b"\xe9", b"\xf6")
    compile(changed, "latin.py", "exec")
    (branch / "latin.py").write_bytes(changed)
    git(branch, "commit", "-qam", "Different Latin-1 behavior")
    result = checks.before_publish(branch, BASE, POLICY)
    assert not result["ok"]
    assert result["diff_hash"] != reviewed["diff_hash"]
    scope = checks.scope_report(branch, BASE, POLICY, since=reviewed["head"])
    assert scope["since"]["changed"] == ["latin.py"]


def test_lens_and_configuration_fingerprints_do_not_replace_bytes(branch):
    lens = branch / ".checks" / "lenses" / "runtime" / "SKILL.md"
    lens.write_bytes(b"Review caf\xe9\n")
    git(branch, "commit", "-qam", "Latin-1 lens")
    first = record(branch)
    lens.write_bytes(b"Review caf\xf6\n")
    git(branch, "commit", "-qam", "Different lens bytes")
    second = checks.before_publish(branch, BASE, POLICY)
    assert second["policy_hash"] != first["policy_hash"] and not second["ok"]
    other_config = checks.before_publish(branch, BASE, replace(POLICY, guidance="caf\u00e9"))
    changed_config = checks.before_publish(branch, BASE, replace(POLICY, guidance="caf\u00f6"))
    assert other_config["policy_hash"] != changed_config["policy_hash"]


def test_fingerprint_reads_use_the_bounded_binary_runner(branch, monkeypatch):
    calls = []
    run = checks.push_timeout.run_bounded

    def capture(command, **kwargs):
        calls.append(kwargs)
        return run(command, **kwargs)

    monkeypatch.setattr(checks.push_timeout, "run_bounded", capture)
    record(branch)
    assert calls and all(call["binary"] is True and call["timeout"] == 30 for call in calls)


@pytest.mark.parametrize("text", ["{", "[]", '{"version":1}', '{"version":1,"binding":{}}'])
def test_corrupt_receipt_is_not_a_pass(branch, text):
    path = Path(record(branch)["receipt"])
    path.write_text(text, "utf-8")
    assert not checks.before_publish(branch, BASE, POLICY)["ok"]
    assert path.read_text("utf-8") == text


def test_receipt_head_is_checked_and_linked_worktrees_do_not_share_it(branch, tmp_path):
    path = Path(record(branch)["receipt"])
    receipt = json.loads(path.read_text())
    receipt["source_commit"] = git(branch, "rev-parse", "origin/main")
    path.write_text(json.dumps(receipt), "utf-8")
    assert not checks.before_publish(branch, BASE, POLICY)["ok"]
    record(branch)
    sibling = tmp_path / "sibling"
    git(branch, "worktree", "add", "-qb", "sibling", str(sibling), "HEAD")
    assert not checks.before_publish(sibling, BASE, POLICY)["ok"]


def test_record_rejects_empty_and_unknown_reviews(branch):
    for lenses, summary in [([], "checked"), (["unknown"], "checked"), (["runtime"], "")]:
        with pytest.raises(checks.CheckError):
            checks.record_self_review(branch, BASE, POLICY, lenses=lenses, summary=summary)
    git(branch, "checkout", "-q", "main")
    with pytest.raises(checks.CheckError, match="no branch diff"):
        record(branch)


def test_scope_is_merge_base_bound_and_task_paths_only_narrow(branch):
    (branch / "sub").mkdir()
    (branch / "sub" / "b.txt").write_text("new\n", "utf-8")
    git(branch, "add", ".")
    git(branch, "commit", "-qm", "add sub file")
    policy = replace(POLICY, self_review=False, allowed_paths=("a.txt",))
    result = checks.scope_report(branch, BASE, policy, allowed_paths=["**"])
    assert result["outside_manifest"] == ["sub/b.txt"]
    rows = {row["path"]: row for row in result["files"]}
    assert rows["sub/b.txt"]["new"] and rows["sub/b.txt"]["subject"] == "add sub file"
    assert rows["a.txt"]["added"] == rows["a.txt"]["deleted"] == 1
    assert checks.scope_report(branch, BASE, replace(policy, allowed_paths=()))["outside_manifest"]
    assert checks.scope_report(branch, BASE, replace(policy, allowed_paths=None))["ok"]
    git(branch, "checkout", "-q", "main")
    (branch / "unrelated.txt").write_text("unrelated\n", "utf-8")
    git(branch, "add", ".")
    git(branch, "commit", "-qm", "base moved")
    git(branch, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(branch, "checkout", "-q", "change")
    assert "unrelated.txt" not in {row["path"] for row in checks.scope_report(branch, BASE, policy)["files"]}


def test_globs_are_segment_aware():
    assert checks._allowed("src/a.py", ("src/*.py",))
    assert not checks._allowed("src/deep/a.py", ("src/*.py",))
    assert checks._allowed("src/a.py", ("src/**/*.py",))
    assert checks._allowed("src/deep/a.py", ("src/",))
    assert not checks._allowed("srcOther/a.py", ("src/",))


def test_scope_since_includes_binary_prefix_lines_and_removed_files(branch):
    (branch / "b.bin").write_bytes(b"\x00a")
    (branch / "prefix.txt").write_text("++ old;\n", "utf-8")
    git(branch, "add", ".")
    git(branch, "commit", "-qm", "additional scope")
    previous = git(branch, "rev-parse", "HEAD")
    (branch / "b.bin").write_bytes(b"\x00b")
    (branch / "prefix.txt").write_text("++ new;\n", "utf-8")
    git(branch, "checkout", "origin/main", "--", "a.txt")
    (branch / "joined.txt").write_text("joined", "utf-8")
    git(branch, "add", ".")
    git(branch, "commit", "-qm", "review fixes")
    result = checks.scope_report(branch, BASE, POLICY, since=previous)
    assert result["since"]["changed"] == ["b.bin", "prefix.txt"]
    assert result["since"]["left"] == ["a.txt"]
    assert result["since"]["joined"] == ["joined.txt"]
    assert result["progress"].startswith("PROGRESS scope=files:3")


def test_scope_since_ignores_harmless_rebase_with_moving_hunks(branch):
    git(branch, "checkout", "-q", "main")
    text = "".join(f"line {n}\n" for n in range(20))
    (branch / "long.txt").write_text(text, "utf-8")
    git(branch, "add", ".")
    git(branch, "commit", "-qm", "long base")
    git(branch, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(branch, "checkout", "-qb", "long-change")
    (branch / "long.txt").write_text(text.replace("line 18\n", "line changed\n"), "utf-8")
    git(branch, "commit", "-qam", "change distant line")
    previous = git(branch, "rev-parse", "HEAD")
    git(branch, "checkout", "-q", "main")
    (branch / "long.txt").write_text("header\n" + text, "utf-8")
    git(branch, "commit", "-qam", "base header")
    git(branch, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(branch, "checkout", "-q", "long-change")
    git(branch, "rebase", "main")
    scope = checks.scope_report(branch, BASE, POLICY, since=previous)["since"]
    assert scope["joined"] == scope["left"] == scope["changed"] == []


def test_pinned_refspec_preserves_branch_destination_and_requires_one_for_sha(branch):
    head = git(branch, "rev-parse", "HEAD")
    assert checks.pin_refspec(branch, "change", head) == head + ":refs/heads/change"
    assert checks.pin_refspec(branch, "HEAD:refs/heads/other", head) == head + ":refs/heads/other"
    with pytest.raises(checks.CheckError, match="explicit destination"):
        checks.pin_refspec(branch, head, head)


def test_ambiguous_merge_base_fails_closed(branch):
    base = git(branch, "rev-parse", "origin/main")
    first = git(branch, "rev-parse", "HEAD")
    tree = git(branch, "rev-parse", "HEAD^{tree}")
    second = git(branch, "commit-tree", tree, "-p", base, "-m", "second parent")
    left = git(branch, "commit-tree", tree, "-p", first, "-p", second, "-m", "left merge")
    right = git(branch, "commit-tree", tree, "-p", second, "-p", first, "-m", "right merge")
    git(branch, "reset", "--hard", left)
    assert "ambiguous" in checks.before_publish(branch, right, POLICY)["error"]


@pytest.mark.parametrize("raw", [
    {"command": ["anything"]}, {"features": {"unknown": True}}, {"features": {"self_review": 1}},
    {"review_lenses_path": "../elsewhere"}, {"review_lenses_path": None},
    {"allowed_paths": "all"}, {"allowed_paths": ["/absolute"]}, {"allowed_paths": ["C:\\escape"]},
    {"allowed_paths": ["a", "a"]}, {"guidance": []},
    {"features": {"scope_report": False}, "allowed_paths": []},
    {"ssh_url": None}, {"ssh_url": "https://forge.invalid/owner/repo"},
    {"ssh_url": "ssh://git:fixture-value@forge.invalid/owner/repo"},
    {"ssh_url": "ssh://forge.invalid:invalid/owner/repo"},
    {"ssh_url": "git@forge.invalid:owner/repo?private=value"},
    {"ssh_url": "git@forge.invalid:owner/repo\n"},
    {"ssh_url": "C:\\local\\repo"}, {"ssh_url": "git@forge.invalid:owner/repo\x00"},
])
def test_strict_config_rejects_extensions_and_malformed_policy(raw):
    with pytest.raises(ValueError):
        parse_publication_checks(raw)


def test_config_is_opt_in_and_related_entries_use_same_parser(tmp_path):
    from agent_worktrees import related
    assert cfg._parse_pr({}).publication_checks is None
    raw = {"features": {"self_review": False}, "allowed_paths": ["src/"]}
    entry = related._parse_related_pr({"publication_checks": raw})
    assert cfg._parse_pr(entry).publication_checks.allowed_paths == ("src/",)
    assert checks.before_publish(tmp_path / "missing", "missing", None)["ok"]
    legacy = cfg._parse_pr({
        "labels": ["one", " ", 2], "required_body_sections": "Summary",
        "hold_labels": ["paused", ""], "wip_title_prefixes": "WIP",
    })
    assert legacy.labels == ("one", "2")
    assert legacy.required_body_sections == ("Summary",)
    assert legacy.hold_labels == ("paused",)
    assert legacy.wip_title_prefixes == ("WIP",)


def test_cli_dispatch_records_and_verifies_without_raw_provider(branch, monkeypatch, capsys):
    from types import SimpleNamespace
    from agent_worktrees import pr_cli, pr_config, pr_read_context
    git(branch, "remote", "add", "origin", "https://github.com/example/project.git")
    repo = SimpleNamespace(remote="origin", default_branch="main",
                           pr=SimpleNamespace(publication_checks=POLICY, provider="github", api_base=""))
    monkeypatch.setattr(pr_read_context, "load_read_config", lambda path: object())
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda config, slug: SimpleNamespace(resolved=True, repo_config=repo))
    argv = ["checks", "example/project", "record-self-review", "--workspace", str(branch),
            "--lenses", "runtime", "--summary", "checked", "--json"]
    assert pr_cli.cmd_pr_watch_dispatch(argv) == 0
    assert json.loads(capsys.readouterr().out)["progress"].startswith("PROGRESS review=")
    assert pr_cli.cmd_pr_watch_dispatch(
        ["checks", "example/project", "verify-self-review", "--workspace", str(branch), "--json"]
    ) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert pr_cli.cmd_pr_watch_dispatch(
        ["checks", "example/project", "lenses", "--workspace", str(branch), "--json"]
    ) == 0
    assert json.loads(capsys.readouterr().out)["lenses"] == ["runtime"]
