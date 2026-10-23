"""Fresh graph evidence gates creation-history reads without weakening resets."""

from types import SimpleNamespace

import pytest
import test_finalize_landing as landing_support

from agent_worktrees import finalize_open_pr_gate, finalize_ref, git_ops

_landing_seed = landing_support._landing_seed
_git = landing_support._git


@pytest.fixture
def env(tmp_path, _landing_seed):
    return landing_support._copy_landing_env(_landing_seed, tmp_path)


@pytest.mark.parametrize("feature", ["", "worktree/task"])
def test_a_tracked_ref_cannot_have_commits_beyond_the_identical_current_ref(monkeypatch, feature):
    monkeypatch.setattr(
        git_ops, "git", lambda *args, **kwargs: pytest.fail("identical refs need no Git probe"),
    )
    assert not finalize_open_pr_gate._tracked_branch_ahead(
        feature, "task", "worktree/task", cwd="unused",
    )


def test_a_different_tracked_branch_still_blocks_unpublished_work(env):
    landing_support._commit(env.clone, "a.txt", "unpublished\n")
    _git("checkout", "-q", "-b", "current", "origin/main", cwd=env.clone)
    assert finalize_open_pr_gate._tracked_branch_ahead(
        "", env.worktree_id, "current", cwd=str(env.clone),
    )


def test_zero_extra_commits_use_real_graph_evidence_without_creation_history(env, monkeypatch):
    head = _git("rev-parse", "HEAD", cwd=env.clone)
    monkeypatch.setattr(
        finalize_ref, "creation_point",
        lambda *args, **kwargs: pytest.fail("the real graph already proves no extra commits"),
    )
    assert finalize_open_pr_gate._extra_commit_count(
        f"worktree/{env.worktree_id}", head, "origin/main", cwd=str(env.clone),
    ) == 0
    _git("checkout", "-q", "-b", "current", "origin/main", cwd=env.clone)
    assert not finalize_open_pr_gate._tracked_branch_ahead(
        "", env.worktree_id, "current", cwd=str(env.clone),
    )


@pytest.mark.parametrize("interrupted", [False, True])
def test_report_history_snapshot_never_survives_into_a_later_decision(
    env, monkeypatch, interrupted,
):
    branch = f"worktree/{env.worktree_id}"
    head = _git("rev-parse", "HEAD", cwd=env.clone)
    original = git_ops.git
    reads = []

    def observed(*args, **kwargs):
        if args[:3] == ("reflog", "show", "--format=%H%x09%gs"):
            reads.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", observed)

    def report():
        with finalize_ref.report_history_snapshot():
            assert finalize_ref.creation_point(branch, str(env.clone)) == head
            assert finalize_ref.creation_point(
                branch, str(env.clone), published_bases=("refs/remotes/origin/dev",),
            ) == head
            if interrupted:
                raise RuntimeError("report interrupted")

    if interrupted:
        with pytest.raises(RuntimeError, match="report interrupted"):
            report()
    else:
        report()
    assert len(reads) == 1

    _git("reflog", "expire", "--expire=now", branch, cwd=env.clone)
    assert finalize_ref.creation_point(branch, str(env.clone)) == ""
    with finalize_ref.report_history_snapshot():
        assert finalize_ref.creation_point(branch, str(env.clone)) == ""
    assert len(reads) == 3


@pytest.mark.parametrize("mutation", ["expire", "delete", "shadow"])
def test_reset_tip_batch_rechecks_remote_proof_after_a_report(env, mutation):
    _git("checkout", "-q", "--detach", cwd=env.clone)
    tip = landing_support._sync_worktree_branch_to_a_newer_main(env)
    branch = f"worktree/{env.worktree_id}"
    with finalize_ref.report_history_snapshot():
        assert finalize_ref.creation_point(branch, str(env.clone)) == tip
    if mutation == "expire":
        _git("reflog", "expire", "--expire=now", "refs/remotes/origin/main", cwd=env.clone)
    elif mutation == "delete":
        _git("update-ref", "-d", "refs/remotes/origin/main", cwd=env.clone)
    else:
        _git("branch", "origin/main", tip, cwd=env.clone)
    assert finalize_ref.creation_point(branch, str(env.clone)) != tip


@pytest.mark.parametrize(
    "returncode,stdout",
    [(1, ""), (0, ""), (0, "unknown"), (0, "-1"), (0, "1\n2"), (0, "\u00b2")],
)
def test_unreadable_extra_commit_counts_never_authorize_finalization(
    monkeypatch, returncode, stdout,
):
    monkeypatch.setattr(git_ops, "ref_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        git_ops, "git",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=stdout),
    )
    assert finalize_open_pr_gate._extra_commit_count(
        "tracked", "published", "upstream", cwd="unused",
    ) is None
    assert finalize_open_pr_gate._tracked_branch_ahead(
        "", "task", "other", cwd="unused",
    )


def test_graph_count_itself_verifies_all_refs_without_existence_probes(env, monkeypatch):
    branch = f"worktree/{env.worktree_id}"
    head = _git("rev-parse", "HEAD", cwd=env.clone)
    base = "refs/remotes/origin/main"
    (env.clone / "missing").write_text("a path is not a revision\n")
    monkeypatch.setattr(
        git_ops, "ref_exists",
        lambda *args, **kwargs: pytest.fail("the graph query must validate its own inputs"),
    )
    assert finalize_open_pr_gate._extra_commit_count(
        branch, head, base, cwd=str(env.clone),
    ) == 0
    for ref, published, upstream in [
        ("missing", head, base), (branch, "missing", base),
        (branch, head, "missing"), ("--all", head, base), ("", head, base),
    ]:
        assert finalize_open_pr_gate._extra_commit_count(
            ref, published, upstream, cwd=str(env.clone),
        ) is None


@pytest.mark.parametrize("count", [None, 0, 1])
def test_merged_block_message_uses_only_verified_graph_counts(monkeypatch, count):
    record = SimpleNamespace(worktree_id="task", pr=SimpleNamespace(head_sha="head"), prs=[])
    monkeypatch.setattr(
        git_ops, "ref_exists",
        lambda *args, **kwargs: pytest.fail("the graph query already verifies the refs"),
    )
    calls = []

    def counted(ref, head, upstream, *, cwd):
        calls.append((ref, head, upstream, cwd))
        return count

    monkeypatch.setattr(finalize_open_pr_gate, "_extra_commit_count", counted)
    message = finalize_open_pr_gate.merged_pr_block_message(
        record, "worktree/task", "upstream", cwd="unused",
    )
    assert ("carries further commits" in message) is (count == 1)
    assert ("can't be verified" in message) is (count != 1)
    assert calls and all(call == ("worktree/task", "head", "upstream", "unused") for call in calls)


@pytest.mark.parametrize("later", [[], ["later\tcommit: new work"]])
def test_creation_point_without_a_later_reset_needs_no_source_tip_reread(monkeypatch, later):
    monkeypatch.setattr(
        git_ops, "git", lambda *args, **kwargs: pytest.fail("there is no reset to validate"),
    )
    assert finalize_ref._later_resets_to_source(
        [*later, "created\tbranch: Created from origin/main"], "created", "origin/main", (), "unused",
    ) == "created"


def test_creation_point_reuses_only_current_call_ref_names_for_reset_proof(monkeypatch):
    created, reset = "a" * 40, "b" * 40
    calls = []
    state = {"shadow": False, "fail": False}

    def evidence(*args, **kwargs):
        calls.append(args)
        if args[0] == "rev-parse":
            return SimpleNamespace(returncode=0, stdout="refs/heads/task\n")
        if args[0] == "remote":
            return SimpleNamespace(returncode=0, stdout="origin\n")
        if args[0] == "for-each-ref":
            assert args == (
                "for-each-ref", "--format=%(refname)%09%(objectname)",
                "refs/heads/origin/dev", "refs/heads/origin/main", "refs/remotes/origin/",
            )
            rows = f"refs/remotes/origin/dev\t{created}\nrefs/remotes/origin/main\t{reset}\n"
            if state["shadow"]:
                rows += f"refs/heads/origin/main\t{reset}\n"
            return SimpleNamespace(returncode=int(state["fail"]), stdout=rows)
        assert args[:2] == ("reflog", "show")
        if args[2] == "--format=%H%x09%gs":
            rows = (
                f"{reset}\tbranch: Reset to origin/main\n"
                f"{created}\tbranch: Created from origin/dev\n"
            )
        else:
            expected = ("refs/remotes/origin/dev",) if state["shadow"] else (
                "refs/remotes/origin/dev", "refs/remotes/origin/main",
            )
            assert args[2:] == ("--format=%H", *expected, "--")
            rows = f"{created}\n" if state["shadow"] else f"{created}\n{reset}\n"
        return SimpleNamespace(returncode=0, stdout=rows)

    monkeypatch.setattr(git_ops, "git", evidence)
    bases = ("refs/remotes/origin/main", "refs/remotes/other/main")
    assert finalize_ref.creation_point("task", "unused", published_bases=bases) == reset
    state["shadow"] = True
    assert finalize_ref.creation_point("task", "unused", published_bases=bases) == created
    state["fail"] = True
    assert finalize_ref.creation_point("task", "unused", published_bases=bases) == ""
    assert sum(args[0] == "for-each-ref" for args in calls) == 3


def test_a_reset_after_earlier_local_work_still_reads_qualified_tips(monkeypatch):
    calls = []

    def evidence(*args, **kwargs):
        calls.append(args)
        if args[0] == "for-each-ref":
            return SimpleNamespace(
                returncode=0, stdout="refs/remotes/origin/dev\nrefs/remotes/origin/main\n",
            )
        return SimpleNamespace(returncode=0, stdout="published\n")

    monkeypatch.setattr(git_ops, "git", evidence)
    assert finalize_ref._later_resets_to_source(
        [
            "published\treset: moving to origin/main",
            "local\tcommit: earlier work",
            "created\tbranch: Created from origin/dev",
        ],
        "created", "origin/dev", ("refs/remotes/origin/main",), "unused",
    ) == "published"
    assert calls == [
        (
            "for-each-ref", "--format=%(refname)", "refs/heads/origin/dev",
            "refs/heads/origin/main", "refs/remotes/origin/dev", "refs/remotes/origin/main",
        ),
        (
            "reflog", "show", "--format=%H",
            "refs/remotes/origin/dev", "refs/remotes/origin/main", "--",
        ),
    ]


@pytest.mark.parametrize("failure", ["refs", "reflog", "missing", "shadow"])
def test_reset_tip_batch_never_uses_unavailable_or_shadowed_evidence(monkeypatch, failure):
    calls = []

    def evidence(*args, **kwargs):
        calls.append(args)
        if args[0] == "for-each-ref":
            names = "refs/remotes/origin/main\n"
            if failure == "missing":
                names = ""
            elif failure == "shadow":
                names += "refs/heads/origin/main\n"
            return SimpleNamespace(returncode=int(failure == "refs"), stdout=names)
        return SimpleNamespace(returncode=1, stdout="published\n")

    monkeypatch.setattr(git_ops, "git", evidence)
    assert finalize_ref._later_resets_to_source(
        [
            "published\treset: moving to origin/main",
            "created\tbranch: Created from origin/main",
        ],
        "created", "origin/main", (), "unused",
    ) == "created"
    assert len(calls) == (2 if failure == "reflog" else 1)


@pytest.mark.parametrize("stdout", ["", "unknown", "-1", "1\n2", "\u00b2"])
def test_bad_counts_after_creation_exclusion_remain_unknown(monkeypatch, stdout):
    monkeypatch.setattr(git_ops, "ref_exists", lambda *args, **kwargs: True)
    calls = []

    def count(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="1" if len(calls) == 1 else stdout)

    def created(ref, cwd, *, published_bases):
        assert published_bases == ("refs/remotes/origin/main", "refs/remotes/origin/dev")
        return "created"

    monkeypatch.setattr(git_ops, "git", count)
    monkeypatch.setattr(finalize_ref, "creation_point", created)
    assert finalize_open_pr_gate._extra_commit_count(
        "tracked", "head", ("refs/remotes/origin/main", "refs/remotes/origin/dev"), cwd="unused",
    ) is None
    assert calls == [
        (
            "rev-list", "--count", "--end-of-options", "tracked", "^head",
            "^refs/remotes/origin/main", "^refs/remotes/origin/dev", "--",
        ),
        (
            "rev-list", "--count", "--end-of-options", "tracked", "^head",
            "^refs/remotes/origin/main", "^refs/remotes/origin/dev", "^created", "--",
        ),
    ]


@pytest.mark.parametrize(
    "width,kind",
    [(40, "valid"), (64, "valid"), (40, "failure"), (40, "short"), (40, "missing-tab"), (40, "shadow")],
)
def test_creation_tip_batch_requires_full_ids_and_no_local_shadow(monkeypatch, width, kind):
    sha = "a" * width
    row = f"refs/remotes/origin/main\t{sha}\n"
    if kind == "short":
        row = "refs/remotes/origin/main\tabc\n"
    elif kind == "missing-tab":
        row = sha + "\n"
    elif kind == "shadow":
        row += f"refs/heads/origin/main\t{sha}\n"
    calls = []

    def evidence(*args, **kwargs):
        calls.append(args)
        if args[0] == "rev-parse":
            return SimpleNamespace(returncode=0, stdout="refs/heads/task\n")
        if args[0] == "remote":
            return SimpleNamespace(returncode=0, stdout="origin\n")
        if args[0] == "reflog":
            assert args[3] == "refs/heads/task"
            return SimpleNamespace(returncode=0, stdout=f"{sha}\tbranch: Created from origin/main\n")
        assert args == (
            "for-each-ref", "--format=%(refname)%09%(objectname)",
            "refs/heads/origin/main", "refs/remotes/origin/",
        )
        return SimpleNamespace(returncode=1 if kind == "failure" else 0, stdout=row)

    monkeypatch.setattr(git_ops, "git", evidence)
    assert finalize_ref.creation_point("task", "unused") == (sha if kind == "valid" else "")
    assert sum(args[0] == "for-each-ref" for args in calls) == 1
