"""The ordered source fence describes parsed bytes, not a later working tree."""

from __future__ import annotations

import json
from subprocess import CompletedProcess

import pytest

from agent_dispatch import configuration_layers_adapter as adapter
from agent_dispatch import registrar_source_revision as revisions
from agent_dispatch.registrar import load_declaration
from agent_dispatch.registrar_discovery import Pointer, discover_trusted

REVISION = "a" * 40
BLOB = "b" * 40


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "source"
    directory = root / ".copilot-extensions" / "agent-dispatch" / "registrar"
    directory.mkdir(parents=True)
    (root / ".git").mkdir()
    document = directory / "worker.json"
    text = json.dumps({"name": "worker"}) + "\n"
    document.write_text(text, encoding="utf-8")
    relative = document.relative_to(root).as_posix()
    replies = {
        ("rev-parse", "--show-toplevel"): str(root) + "\n",
        ("rev-parse", "--verify", "HEAD"): REVISION + "\n",
        ("status",): "",
        ("ls-tree",): f"100644 blob {BLOB}\t{relative}\0",
        ("cat-file", "blob", BLOB): text,
        ("merge-base",): "",
    }
    calls = []

    def run(argv, **kwargs):
        assert "--no-replace-objects" in argv and "--no-optional-locks" in argv
        assert "--literal-pathspecs" in argv and kwargs["timeout"] == 5.0
        assert kwargs["max_output"] == 1024 * 1024
        assert "GIT_DIR" not in kwargs["env"] and "GIT_CONFIG_KEY_0" not in kwargs["env"]
        args = tuple(argv[argv.index("-C") + 2:])
        calls.append(args)
        value = replies.get(args, replies.get(args[:1]))
        if callable(value):
            value = value()
        if isinstance(value, Exception):
            raise value
        return None if value is None else CompletedProcess(argv, 0, value, "")

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign"))
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setattr(revisions, "run_background_capture", run)
    monkeypatch.setattr(adapter, "configuration_report", lambda root: None)
    return root, directory, document, replies, calls


def _observe(root):
    result = discover_trusted([Pointer("repository", str(root), kind="repo")])
    return result, result.source_observations[0]


@pytest.mark.parametrize("layered", [False, True])
def test_discovery_captures_parsed_documents_at_one_revision(source, monkeypatch, layered):
    root, _, _, replies, _ = source
    if layered:
        report = {
            "schema": "agent-worktrees.configuration", "version": 1,
            "context": {
                "repository_id": "example", "execution_machine": "test-machine",
                "installation_cell": "test-cell", "binding": "unbound", "base_root": str(root),
            },
        }
        monkeypatch.setattr(adapter, "configuration_report", lambda root: report)
    replies[("cat-file", "blob", BLOB)] = '{"name": "worker"}\r\n'
    result, observation = _observe(root)
    assert [item.name for item in result.declarations] == ["worker"]
    assert observation.revision_state == "ordered" and observation.revision == REVISION


@pytest.mark.parametrize("failure", [
    "dirty", "untracked", "ignored", "tree-unavailable", "blob-unavailable", "unread-file",
    "parsed-edit-restored", "head-changed", "foreign-root", "invalid-mode",
])
def test_failed_proof_preserves_finite_discovery_but_never_orders_it(source, failure):
    root, _, document, replies, _ = source
    if failure in {"dirty", "untracked", "ignored"}:
        replies[("status",)] = {"dirty": " M", "untracked": "??", "ignored": "!!"}[failure] + " worker.json\0"
    elif failure == "tree-unavailable":
        replies[("ls-tree",)] = None
    elif failure == "blob-unavailable":
        replies[("cat-file", "blob", BLOB)] = None
    elif failure == "unread-file":
        replies[("ls-tree",)] += replies[("ls-tree",)].replace("worker.json", "other.json")
    elif failure == "parsed-edit-restored":
        # Status sees a restored clean file, but the parser consumed different bytes.
        document.write_text('{"name": "edited"}', encoding="utf-8")
    elif failure == "head-changed":
        heads = iter([REVISION + "\n", "c" * 40 + "\n"])
        replies[("rev-parse", "--verify", "HEAD")] = lambda: next(heads)
    elif failure == "foreign-root":
        replies[("rev-parse", "--show-toplevel")] = str(root.parent) + "\n"
    else:
        replies[("ls-tree",)] = replies[("ls-tree",)].replace("100644", "120000")
    result, observation = _observe(root)
    assert len(result.declarations) == 1 and observation.status == "complete"
    assert observation.revision_state == "unavailable" and observation.revision is None


def test_head_change_during_blob_comparison_is_refused(source):
    root, _, _, replies, _ = source
    heads = iter([REVISION + "\n", REVISION + "\n", "c" * 40 + "\n"])
    replies[("rev-parse", "--verify", "HEAD")] = lambda: next(heads)
    assert _observe(root)[1].revision_state == "unavailable"


def test_repeated_different_parse_cannot_be_hidden_by_a_third_read(source):
    root, directory, document, _, _ = source
    started = revisions.begin_source_revision(root)
    reads = {}
    for text in ['{"name": "worker"}\n', '{"name": "other"}\n', '{"name": "worker"}\n']:
        revisions.record_document_read(reads, document, text)
    assert revisions.finish_source_revision(
        root, started, directory_reads={directory: "complete"}, document_reads=reads,
    ).state == "unavailable"


def test_complete_empty_source_has_proof_but_missing_source_does_not(source):
    root, directory, document, replies, _ = source
    document.unlink()
    replies[("ls-tree",)] = ""
    assert _observe(root)[1].revision_state == "ordered"
    directory.rmdir()
    assert _observe(root)[1].revision_state == "unavailable"


def test_external_overlay_is_not_covered_by_repository_revision(source, tmp_path):
    root, directory, document, _, _ = source
    reads = {}
    revisions.record_document_read(reads, document, document.read_text(encoding="utf-8"))
    assert revisions.finish_source_revision(
        root, revisions.begin_source_revision(root),
        directory_reads={directory: "complete", tmp_path / "overlay": "complete"},
        document_reads=reads,
    ).state == "unavailable"


def test_non_git_source_is_unordered_but_probe_failure_is_not(source):
    root, _, _, replies, _ = source
    replies[("rev-parse", "--verify", "HEAD")] = OSError("git unavailable")
    assert _observe(root)[1].revision_state == "unavailable"
    (root / ".git").rmdir()
    assert _observe(root)[1].revision_state == "unordered"


def test_unobserved_recipe_dependencies_do_not_gain_source_authority(source, monkeypatch):
    from agent_dispatch import registrar_discovery

    root, _, document, replies, _ = source
    text = '{"extends": "recipe.yaml", "name": "worker"}'
    document.write_text(text, encoding="utf-8")
    replies[("cat-file", "blob", BLOB)] = text
    monkeypatch.setattr(
        registrar_discovery, "expand_declaration_document",
        lambda *args, **kwargs: (load_declaration({"name": "worker"}),),
    )
    assert _observe(root)[1].revision_state == "unavailable"


def test_only_equal_or_proven_ancestor_revisions_advance(source):
    root, _, _, replies, calls = source
    assert revisions.source_revision_follows(root, None, REVISION)
    assert revisions.source_revision_follows(root, REVISION, REVISION)
    assert calls == []
    assert revisions.source_revision_follows(root, "c" * 40, REVISION)
    assert calls[-1] == ("merge-base", "--is-ancestor", "c" * 40, REVISION)
    replies[("merge-base",)] = None
    assert not revisions.source_revision_follows(root, "c" * 40, REVISION)
    assert not revisions.source_revision_follows(root, "--option", REVISION)


def test_ancestry_probe_uses_remaining_budget_and_never_starts_after_deadline(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(revisions.time, "monotonic", lambda: 10.0)

    def run(argv, **kwargs):
        calls.append(kwargs["timeout"])
        return CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(revisions, "run_background_capture", run)
    assert revisions.source_revision_follows(tmp_path, "c" * 40, REVISION, deadline=12.0)
    assert calls == [2.0]
    assert not revisions.source_revision_follows(tmp_path, "c" * 40, REVISION, deadline=10.0)
    assert not revisions.source_revision_follows(tmp_path, REVISION, REVISION, deadline=10.0)
    assert calls == [2.0]
