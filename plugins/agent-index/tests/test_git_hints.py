"""Actual Git event acquisition stays candidate-bounded and ref-authoritative."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace

import pytest
from test_git_delta import commit, forbidden, git
from test_git_delta import repo as repo

from agent_index.indexing import engine
from agent_index.indexing.change_hints import ChangeHints, MetadataRevision, RefRange
from agent_index.indexing.hinted_crawl import require_hint_connector
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.git_delta import GitReconciliationRequired
from agent_index.sources.git_repo import GitRepoConnector


def connector(repo):
    return GitRepoConnector(
        "git:sample", repo_path=repo, ref="refs/heads/main", fetch=False,
        hint_account="selected",
    )


def hints(*pairs):
    return ChangeHints(
        source="git:sample", canonical_family="git:sample", account_id="selected",
        ref_ranges=tuple(RefRange(*pair) for pair in pairs), event_ids=("delivery",),
    )


def test_distinct_and_duplicate_ranges_union_candidates_not_content_authority(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("intermediate\n")
    (repo / "remove.md").unlink()
    middle = commit(repo)
    (repo / "edit.md").write_text("edit.md\n")
    (repo / "remove.md").write_text("restored\n")
    target = commit(repo)
    source = connector(repo)
    envelope = hints((base, middle)).union(hints((middle, target), (base, middle)))
    assert len(envelope.ref_ranges) == 2
    reads = []
    read = source._git_bytes

    def measured(args):
        reads.append(args)
        return read(args)

    monkeypatch.setattr(source, "_git_bytes", measured)
    for name in ("discover", "list_paths", "_tracked_paths", "_commit_shas", "discover_changed"):
        monkeypatch.setattr(source, name, forbidden)
    assert require_hint_connector(source, envelope) == source.discover_hints
    delta = source.discover_hints(envelope, base)
    assert delta.commit == base and not delta.deletions
    files = {item.path: item for item in delta.upserts if item.language != "commit"}
    assert {key: item.content for key, item in files.items()} == {
        "edit.md": "edit.md\n", "remove.md": "restored\n",
    }
    assert all(item.metadata["commit"] == target for item in files.values())
    assert {item.path for item in delta.upserts if item.language == "commit"} == {
        f"commits/{middle}.txt", f"commits/{target}.txt",
    }
    assert len([args for args in reads if args[0] == "ls-tree"]) == 2
    assert len([args for args in reads if args[:2] == ["cat-file", "blob"]]) == 2


@pytest.mark.parametrize("filename", [
    "edit.md", "prefix[1].md", ":path.md",
    pytest.param(
        "tab\tand\nnewline.md",
        marks=pytest.mark.skipif(os.name == "nt", reason="Win32 forbids control-character filenames"),
    ),
])
def test_stale_range_uses_current_configured_ref_and_literal_paths(repo, filename):
    base = git(repo, "rev-parse", "HEAD")
    (repo / filename).write_text("old delivery\n")
    middle = commit(repo)
    (repo / filename).write_text("current canonical\n")
    target = commit(repo)
    delta = connector(repo).discover_hints(hints((base, middle)), base)
    item = next(item for item in delta.upserts if item.path == filename)
    assert item.content == "current canonical\n" and item.metadata["commit"] == target
    assert {item.path for item in delta.upserts if item.language == "commit"} == {
        f"commits/{middle}.txt",
    }
    assert delta.commit == base


def test_current_target_deletions_binary_and_oversized_content(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("event\n")
    (repo / "remove.md").write_text("event\n")
    (repo / "binary.md").write_text("event\n")
    middle = commit(repo)
    (repo / "edit.md").write_bytes(b"x" * 1_000_001)
    (repo / "remove.md").unlink()
    (repo / "binary.md").write_bytes(b"a\0b")
    commit(repo)
    source = connector(repo)
    sizes = source._git

    def measured(args):
        if args[:2] == ["cat-file", "-s"]:
            value = sizes(args)
            assert value.strip() in {"1000001", "3"}
            return value
        return sizes(args)

    monkeypatch.setattr(source, "_git", measured)
    delta = source.discover_hints(hints((base, middle)), base)
    assert {item.path for item in delta.deletions} == {"edit.md", "remove.md", "binary.md"}
    assert all(item.language == "commit" for item in delta.upserts)


@pytest.mark.parametrize("prior", [None, "prior-poll"])
def test_empty_range_retains_exact_polling_cursor_without_content_reads(repo, monkeypatch, prior):
    base = git(repo, "rev-parse", "HEAD")
    source = connector(repo)
    text = source._git

    def measured(args):
        assert args[0] != "cat-file"
        return text(args)

    monkeypatch.setattr(source, "_git", measured)
    delta = source.discover_hints(hints((base, base)), prior)
    assert delta.commit == prior and not delta.upserts and not delta.deletions


def test_candidate_replaced_by_directory_retires_only_old_file(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("delivered\n")
    middle = commit(repo)
    (repo / "edit.md").unlink()
    (repo / "edit.md").mkdir()
    (repo / "edit.md" / "new.md").write_text("not a hinted file\n")
    commit(repo)
    delta = connector(repo).discover_hints(hints((base, middle)), base)
    assert [item.path for item in delta.deletions] == ["edit.md"]
    assert all(item.language == "commit" for item in delta.upserts)


@pytest.mark.parametrize("case", [
    "source", "family", "account", "unbound", "item", "metadata", "empty",
    "symbolic", "option", "range-syntax",
])
def test_invalid_hints_refuse_before_any_git_io(repo, monkeypatch, case):
    base = git(repo, "rev-parse", "HEAD")
    source = connector(repo)
    envelope = hints((base, base))
    if case in {"source", "family", "account"}:
        field = {"source": "source", "family": "canonical_family", "account": "account_id"}[case]
        envelope = replace(envelope, **{field: "other"})
    elif case == "unbound":
        source = GitRepoConnector("git:sample", repo_path=repo, fetch=False)
    elif case == "item":
        envelope = replace(envelope, item_ids=("one.md",))
    elif case == "metadata":
        envelope = replace(envelope, metadata_revisions=(MetadataRevision("one.md", base),))
    elif case == "empty":
        envelope = replace(envelope, ref_ranges=())
    else:
        bad = {"symbolic": "HEAD", "option": "--output=out", "range-syntax": base + "..HEAD"}[case]
        envelope = hints((bad, base))
    monkeypatch.setattr(source, "_resolve", forbidden)
    monkeypatch.setattr(source, "_git", forbidden)
    monkeypatch.setattr(source, "_git_bytes", forbidden)
    with pytest.raises(ValueError):
        source.discover_hints(envelope, base)


@pytest.mark.parametrize("case", ["missing", "foreign", "reverse", "worktree"])
def test_unavailable_or_noncanonical_history_never_becomes_a_delete(repo, case):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("canonical\n")
    target = commit(repo)
    source = connector(repo)
    pair = (base, target)
    if case == "missing":
        pair = (base, "f" * 40)
    elif case == "foreign":
        git(repo, "checkout", "-b", "other", base)
        (repo / "other.md").write_text("foreign\n")
        pair = (base, commit(repo))
    elif case == "reverse":
        pair = (target, base)
    else:
        source = GitRepoConnector(
            "git:sample", repo_path=repo, fetch=False, hint_account="selected",
        )
    with pytest.raises(GitReconciliationRequired):
        source.discover_hints(hints(pair), base)


def test_ref_moves_after_range_read_but_all_content_remains_pinned(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("pinned\n")
    target = commit(repo)
    source = connector(repo)
    read = source._git_bytes

    def move(args):
        result = read(args)
        if args[0] == "diff":
            (repo / "edit.md").write_text("not pinned\n")
            commit(repo)
        return result

    monkeypatch.setattr(source, "_git_bytes", move)
    delta = source.discover_hints(hints((base, target)), base)
    assert next(item.content for item in delta.upserts if item.path == "edit.md") == "pinned\n"


@pytest.mark.parametrize("failure", ["tree", "blob", "cancel"])
def test_partial_acquisition_fails_without_cursor_publication(repo, monkeypatch, failure):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("changed\n")
    target = commit(repo)
    source = connector(repo)
    read = source._git_bytes

    def failed(args):
        if failure == "tree" and args[0] == "ls-tree":
            return b"truncated"
        if failure == "blob" and args[0] == "cat-file":
            raise subprocess.CalledProcessError(128, ["git", *args])
        return read(args)

    def cancel():
        if failure == "cancel":
            raise InterruptedError("cancelled")

    monkeypatch.setattr(source, "_git_bytes", failed)
    with pytest.raises((ValueError, subprocess.CalledProcessError, InterruptedError)):
        source.discover_hints(hints((base, target)), base, cancel_check=cancel)


@pytest.mark.parametrize("corpus_size", [100, 10_000, 100_000])
def test_git_hints_cost_only_unique_candidates_not_stored_corpus(repo, tmp_path, monkeypatch, corpus_size):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("event\n")
    target = commit(repo)
    source = connector(repo)
    source.current_commit()
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed_batch([
        (source.source_name, f"old-{index}.md", "old", 1) for index in range(corpus_size)
    ])
    monkeypatch.setattr(paths, "get_paths_by_family", forbidden)
    state = IndexState(sources={source.source_name: SourceState(last_commit=base)})
    state.sources[source.source_name].acquisition_scope = recovery_scope(source.source_name, source)
    commands = []
    text, binary = source._git, source._git_bytes

    def measured_text(args):
        commands.append(args)
        return text(args)

    def measured_binary(args):
        commands.append(args)
        return binary(args)

    monkeypatch.setattr(source, "_git", measured_text)
    monkeypatch.setattr(source, "_git_bytes", measured_binary)
    for name in ("list_paths", "_tracked_paths", "_commit_shas", "discover", "discover_changed"):
        monkeypatch.setattr(source, name, forbidden)
    manifest = engine._crawl_source(
        source.source_name, config=None, state=state, path_index=paths, full=False,
        connector=source, change_hints=hints((base, target), (base, target)),
    )
    assert manifest.commit == state.sources[source.source_name].last_commit == base
    assert manifest.stats.files_scanned == 2
    assert not manifest.deletions
    # Two ancestry probes, one diff, one commit range and three file probes.
    assert len(commands) == 7


def test_account_binding_is_part_of_recovery_identity_but_not_inferred(repo):
    selected = connector(repo)
    other = connector(repo)
    other._hint_account = "other"
    legacy = GitRepoConnector("git:sample", repo_path=repo, ref="refs/heads/main", fetch=False)
    assert recovery_scope("git:sample", selected) != recovery_scope("git:sample", other)
    assert selected.recovery_identity[:-1] == legacy.recovery_identity
    with pytest.raises(ValueError, match="explicit configured account"):
        require_hint_connector(legacy, hints((git(repo, "rev-parse", "HEAD"),) * 2))


@pytest.mark.parametrize("token", [None, "explicit-account-token"])
def test_configured_static_account_binds_only_after_explicit_credential_resolution(
    repo, monkeypatch, token,
):
    monkeypatch.setattr(engine, "_resolve_gh_token", lambda account: token)
    spec = engine.SourceSpec(
        "git:sample", repo_path=str(repo), auth_account="selected", ref="refs/heads/main",
    )
    kwargs = engine._connector_kwargs(spec)
    assert kwargs.get("hint_account") == ("selected" if token else None)


@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
def test_native_engine_replays_pending_git_hints_then_refreshes_canonical_revert(
    repo, tmp_path, monkeypatch, selected,
):
    from test_consumer_runtime import Client
    from agent_index.index_config import IndexConfig, ModelProfile
    from agent_index.indexing.embedding_runtime import EmbeddingRuntime
    from agent_index.indexing.pending_crawl import PendingCrawls
    from agent_index.store.multi_model_store import MultiModelStore
    from agent_index.store.source_layout import LAYOUT

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    monkeypatch.setattr(MultiModelStore, "compact", lambda self: {})
    config = IndexConfig(
        data_dir=tmp_path / "native", cluster_enabled=False,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2)},
    )
    spec = engine.SourceSpec("git:sample", repo_path=str(repo), ref="refs/heads/main")
    monkeypatch.setattr(engine, "_connector_kwargs", lambda _: {
        "repo_path": str(repo), "ref": "refs/heads/main", "fetch": False, "hint_account": "selected",
    })
    events = []
    callbacks = []

    class OwnedClient(Client):
        def embed_texts(self, texts):
            if callbacks:
                callbacks.pop(0)()
            return super().embed_texts(texts)

    runtime = EmbeddingRuntime(
        lambda profile: OwnedClient(profile.model_id, events),
        lambda *args: events.append("ensure") or True,
        lambda *args: events.append("stop"),
    )

    def run(**kwargs):
        return engine.run_reindex(
            source=spec.name, config=config, source_specs=[spec], embedding_runtime=runtime, **kwargs,
        )

    base = git(repo, "rev-parse", "HEAD")
    assert "sources_failed" not in run(full=True)
    (repo / "edit.md").write_text("interrupted event\n")
    middle = commit(repo)
    envelope = hints((base, middle))

    def interrupt():
        raise RuntimeError("interrupted embed")

    callbacks.append(interrupt)
    with pytest.raises(RuntimeError, match="interrupted embed"):
        run(change_hints=envelope)
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == base
    paths = PathIndex(config.data_dir / "path_index.db")
    assert spec.name in PendingCrawls(paths).sources()
    (repo / "edit.md").write_text("edit.md\n")
    target = commit(repo)
    observed = []
    acquire = GitRepoConnector.discover_hints

    def measured(self, value, prior, **kwargs):
        observed.append(value)
        return acquire(self, value, prior, **kwargs)

    measured.change_hints_versions = (1,)
    monkeypatch.setattr(GitRepoConnector, "discover_hints", measured)
    run(change_hints=envelope)
    assert observed == [envelope]
    assert spec.name not in PendingCrawls(paths).sources()
    store = MultiModelStore(config.lance_dir)
    document = store.get_document(spec.name, "edit.md")
    assert document.content == "edit.md\n" and document.metadata["commit"] == target
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == base
    version = store.content_store._get_or_create_table().version
    before = len(events)
    result = run(change_hints=envelope)
    assert result["chunks_total"] == result["work_counts"]["content_rows_upserted"] == 0
    assert result["work_counts"]["models"] == {}
    assert events[before:] == ["close:a"]
    assert store.content_store._get_or_create_table().version == version
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == base
