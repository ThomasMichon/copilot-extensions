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
    "edit.md", "prefix[1].md",
    pytest.param(
        ":path.md",
        marks=pytest.mark.skipif(os.name == "nt", reason="Win32 interprets colons as data streams"),
    ),
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
def test_configured_static_account_identity_survives_credential_resolution_failure(
    repo, monkeypatch, token,
):
    monkeypatch.setattr(engine, "_resolve_gh_token", lambda account: token)
    spec = engine.SourceSpec(
        "git:sample", repo_path=str(repo), auth_account="selected", ref="refs/heads/main",
    )
    kwargs = engine._connector_kwargs(spec)
    assert kwargs["hint_account"] == "selected"
    assert kwargs["hint_credentials_available"] is bool(token)
    source = GitRepoConnector(spec.name, **kwargs, fetch=False)
    assert source.recovery_identity[-1] == "selected"
    base = git(repo, "rev-parse", "HEAD")
    if token:
        assert require_hint_connector(source, hints((base, base))) == source.discover_hints
    else:
        monkeypatch.setattr(source, "_resolve", forbidden)
        with pytest.raises(ValueError, match="resolved configured account credentials"):
            require_hint_connector(source, hints((base, base)))


def test_token_resolution_failure_replays_pending_hints_and_polls_without_scope_repair(
    repo, tmp_path, monkeypatch,
):
    from agent_index.indexing.crawl import collect_source_changes
    from agent_index.indexing.pending_crawl import PendingCrawls, recover_source

    spec = engine.SourceSpec(
        "git:sample", repo_path=str(repo), auth_account="selected", ref="refs/heads/main",
    )
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("delivered\n")
    target = commit(repo)
    monkeypatch.setattr(engine, "_resolve_gh_token", lambda _: "synthetic-token")
    first = GitRepoConnector(spec.name, **engine._connector_kwargs(spec), fetch=False)
    scope = recovery_scope(engine.source_scope_hash(spec), first)
    state = IndexState(sources={spec.name: SourceState(
        last_commit=base, acquisition_scope=scope,
    )})
    paths = PathIndex(tmp_path / "paths.db")
    pending = PendingCrawls(paths)
    hint = collect_source_changes(
        spec.name, state=state, path_index=paths, full=False, source_aware=False,
        connector=first, source_scope=engine.source_scope_hash(spec),
        change_hints=hints((base, target)),
    )
    pending.save(hint, scope=scope, full=False)
    monkeypatch.setattr(engine, "_resolve_gh_token", lambda _: None)
    fallback = GitRepoConnector(spec.name, **engine._connector_kwargs(spec), fetch=False)
    assert recovery_scope(engine.source_scope_hash(spec), fallback) == scope
    assert fallback._token is None
    seen = []

    def apply(*, manifest, full):
        assert full is False
        seen.append(manifest)
        state.sources[spec.name].last_commit = manifest.commit
        return (0, 0, 0)

    recover_source(
        spec.name, scope=scope, state=state, state_file=tmp_path / "state.json",
        paths=paths, full=False, family=spec.name, apply=apply,
        acquire=lambda: collect_source_changes(
            spec.name, state=state, path_index=paths, full=False, source_aware=False,
            connector=fallback, source_scope=engine.source_scope_hash(spec),
        ),
    )
    assert seen[0] == hint and len(seen) == 2 and seen[1].commit == target
    assert pending.sources() == []
    saved = IndexState.load(tmp_path / "state.json")
    assert saved.sources[spec.name].last_commit == target
    saved.require_acquisition_scope(spec.name, scope, full=False)


@pytest.fixture(params=[False, True], ids=["legacy", "source-aware"])
def native_git(repo, tmp_path, monkeypatch, request):
    from test_consumer_runtime import Client
    from agent_index.index_config import IndexConfig, ModelProfile
    from agent_index.indexing.embedding_runtime import EmbeddingRuntime
    from agent_index.store.multi_model_store import MultiModelStore
    from agent_index.store.source_layout import LAYOUT

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if request.param else "")
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

    return config, spec, run, events, callbacks


def test_native_engine_replays_pending_git_hints_then_refreshes_canonical_revert(
    repo, monkeypatch, native_git,
):
    from agent_index.indexing.pending_crawl import PendingCrawls
    from agent_index.store.multi_model_store import MultiModelStore

    config, spec, run, events, callbacks = native_git
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
    scope = recovery_scope(engine.source_scope_hash(spec), connector(repo))
    assert PendingCrawls(paths).read_payload(spec.name, scope) is None
    assert spec.name in PendingCrawls(paths).sources()
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


@pytest.mark.parametrize("change", ["edit", "delete", "add", "rename"])
def test_native_hint_then_unhinted_revert_is_refreshed_before_poll_checkpoint(
    repo, native_git, monkeypatch, change,
):
    from agent_index.indexing.pending_crawl import PendingCrawls
    from agent_index.store.multi_model_store import MultiModelStore

    config, spec, run, _, _ = native_git
    base = git(repo, "rev-parse", "HEAD")
    assert "sources_failed" not in run(full=True)
    if change == "delete":
        (repo / "edit.md").unlink()
    elif change == "rename":
        (repo / "edit.md").rename(repo / "new.md")
    elif change == "add":
        (repo / "new.md").write_text("hinted addition\n")
    else:
        (repo / "edit.md").write_text("hinted content\n")
    middle = commit(repo)
    run(change_hints=hints((base, middle)))
    journal = PendingCrawls(PathIndex(config.data_dir / "path_index.db"))
    assert spec.name in journal.sources()
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == base
    first_store = MultiModelStore(config.lance_dir)
    if change in {"delete", "rename"}:
        assert first_store.get_document(spec.name, "edit.md") is None
    elif change == "edit":
        assert first_store.get_document(spec.name, "edit.md").content == "hinted content\n"
    else:
        assert first_store.get_document(spec.name, "new.md").content == "hinted addition\n"
    if change == "rename":
        (repo / "new.md").rename(repo / "edit.md")
    elif change == "add":
        (repo / "new.md").unlink()
    else:
        (repo / "edit.md").write_text("edit.md\n")
    target = commit(repo)
    for name in ("discover", "discover_changed", "list_paths", "_tracked_paths", "_commit_shas"):
        monkeypatch.setattr(GitRepoConnector, name, forbidden)
    assert "sources_failed" not in run()
    current = MultiModelStore(config.lance_dir).get_document(spec.name, "edit.md")
    assert current.content == "edit.md\n"
    if change != "add":
        assert current.metadata["commit"] == target
    assert MultiModelStore(config.lance_dir).get_document(spec.name, "new.md") is None
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == target
    assert spec.name not in journal.sources()
    version = MultiModelStore(config.lance_dir).content_store._get_or_create_table().version
    assert run()["chunks_total"] == 0
    assert MultiModelStore(config.lance_dir).content_store._get_or_create_table().version == version


@pytest.mark.parametrize("failure", ["state", "completion"])
def test_native_poll_failure_preserves_refresh_for_pinned_restart(
    repo, native_git, monkeypatch, failure,
):
    from agent_index.indexing.pending_crawl import PendingCrawls
    from agent_index.store.multi_model_store import MultiModelStore

    config, spec, run, _, _ = native_git
    base = git(repo, "rev-parse", "HEAD")
    assert "sources_failed" not in run(full=True)
    (repo / "edit.md").write_text("delivered\n")
    middle = commit(repo)
    run(change_hints=hints((base, middle)))
    (repo / "edit.md").write_text("edit.md\n")
    target = commit(repo)
    pending = PendingCrawls(PathIndex(config.data_dir / "path_index.db"))
    failed = []
    if failure == "state":
        save = IndexState.save

        def fail_state(self, path):
            if self.sources[spec.name].last_commit == target and not failed:
                failed.append(True)
                raise RuntimeError("interrupted state publication")
            return save(self, path)

        monkeypatch.setattr(IndexState, "save", fail_state)
    else:
        clear = PendingCrawls.clear

        def fail_completion(self, source, **kwargs):
            if not failed:
                failed.append(True)
                raise RuntimeError("interrupted journal completion")
            return clear(self, source, **kwargs)

        monkeypatch.setattr(PendingCrawls, "clear", fail_completion)
    assert "sources_failed" in run()
    scope = recovery_scope(engine.source_scope_hash(spec), connector(repo))
    assert pending.load(spec.name, scope) is not None
    assert pending.refresh_candidates(spec.name, scope, spec.name) is not None
    (repo / "edit.md").write_text("newest canonical\n")
    latest = commit(repo)
    assert "sources_failed" not in run()
    assert pending.load(spec.name, scope) is None
    assert pending.refresh_candidates(spec.name, scope, spec.name) is None
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == latest
    assert MultiModelStore(config.lance_dir).get_document(spec.name, "edit.md").content == (
        "newest canonical\n"
    )


def test_explicit_full_reconciliation_covers_an_already_deleted_hint_candidate(repo, native_git):
    from agent_index.indexing.pending_crawl import PendingCrawls
    from agent_index.store.multi_model_store import MultiModelStore

    config, spec, run, _, _ = native_git
    base = git(repo, "rev-parse", "HEAD")
    assert "sources_failed" not in run(full=True)
    (repo / "edit.md").unlink()
    target = commit(repo)
    run(change_hints=hints((base, target)))
    pending = PendingCrawls(PathIndex(config.data_dir / "path_index.db"))
    assert spec.name in pending.sources()
    assert "sources_failed" not in run(full=True)
    assert pending.sources() == []
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == target
    assert MultiModelStore(config.lance_dir).get_document(spec.name, "edit.md") is None


@pytest.mark.parametrize("empty", [False, True], ids=["file-commit", "empty-commit"])
def test_native_hint_then_sibling_rewrite_refuses_poll_until_explicit_full_repair(
    repo, native_git, monkeypatch, empty,
):
    from agent_index.indexing.pending_crawl import PendingCrawls
    from agent_index.store.multi_model_store import MultiModelStore

    config, spec, run, events, _ = native_git
    base = git(repo, "rev-parse", "HEAD")
    assert "sources_failed" not in run(full=True)
    if not empty:
        (repo / "edit.md").write_text("hinted branch\n")
    hinted = commit(repo)
    assert "sources_failed" not in run(change_hints=hints((base, hinted)))
    journal = PendingCrawls(PathIndex(config.data_dir / "path_index.db"))
    scope = recovery_scope(engine.source_scope_hash(spec), connector(repo))
    retained = journal.refresh_candidates(spec.name, scope, spec.name)
    assert set(retained.history) == {f"commits/{hinted}.txt"}
    assert bool(retained.paths) is not empty
    git(repo, "checkout", "-b", "sibling", base)
    git(repo, "commit", "--allow-empty", "-m", "Sibling revision")
    target = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/heads/main", target)
    git(repo, "merge-base", "--is-ancestor", base, target)
    store = MultiModelStore(config.lance_dir)
    assert store.get_document(spec.name + ":commits", f"commits/{hinted}.txt") is not None
    version = store.content_store._get_or_create_table().version
    before = len(events)
    result = run()
    assert "sources_failed" in result
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == base
    assert store.content_store._get_or_create_table().version == version
    assert events[before:] == ["close:a"]
    assert journal.load(spec.name, scope) is None
    assert journal.refresh_candidates(spec.name, scope, spec.name) == retained
    assert store.get_document(spec.name + ":commits", f"commits/{hinted}.txt") is not None
    discover = GitRepoConnector.discover

    def interrupted(self, **kwargs):
        discover(self, **kwargs)
        raise RuntimeError("interrupted full snapshot")

    with monkeypatch.context() as patch:
        patch.setattr(GitRepoConnector, "discover", interrupted)
        assert "sources_failed" in run(full=True)
    assert journal.refresh_candidates(spec.name, scope, spec.name) == retained
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == base
    assert store.get_document(spec.name + ":commits", f"commits/{hinted}.txt") is not None
    assert "sources_failed" not in run(full=True)
    assert journal.sources() == []
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == target
    current = MultiModelStore(config.lance_dir)
    assert current.get_document(spec.name + ":commits", f"commits/{hinted}.txt") is None
    assert current.get_document(spec.name + ":commits", f"commits/{target}.txt") is not None
    assert current.get_document(spec.name, "edit.md").content == "edit.md\n"


def test_native_empty_hint_is_covered_by_reachable_ordinary_poll(repo, native_git, monkeypatch):
    from agent_index.indexing.pending_crawl import PendingCrawls
    from agent_index.store.multi_model_store import MultiModelStore

    config, spec, run, _, _ = native_git
    base = git(repo, "rev-parse", "HEAD")
    assert "sources_failed" not in run(full=True)
    target = commit(repo)
    assert "sources_failed" not in run(change_hints=hints((base, target)))
    journal = PendingCrawls(PathIndex(config.data_dir / "path_index.db"))
    scope = recovery_scope(engine.source_scope_hash(spec), connector(repo))
    retained = journal.refresh_candidates(spec.name, scope, spec.name)
    assert not retained.paths and set(retained.history) == {f"commits/{target}.txt"}
    for name in ("discover", "discover_changed", "list_paths", "_tracked_paths", "_commit_shas"):
        monkeypatch.setattr(GitRepoConnector, name, forbidden)
    assert "sources_failed" not in run()
    assert journal.sources() == []
    assert IndexState.load(config.state_file).sources[spec.name].last_commit == target
    assert MultiModelStore(config.lance_dir).get_document(
        spec.name + ":commits", f"commits/{target}.txt",
    ) is not None


def test_full_snapshot_covers_retained_history_even_when_path_store_already_lacks_it(repo, tmp_path):
    from agent_index.indexing.crawl import collect_source_changes
    from agent_index.indexing.pending_crawl import PendingCrawls

    base = git(repo, "rev-parse", "HEAD")
    hinted = commit(repo)
    paths = PathIndex(tmp_path / "paths.db")
    source = connector(repo)
    scope = recovery_scope(source.source_name, source)
    state = IndexState(sources={source.source_name: SourceState(
        last_commit=base, acquisition_scope=scope,
    )})
    hint = collect_source_changes(
        source.source_name, state=state, path_index=paths, full=False,
        source_aware=False, connector=source, change_hints=hints((base, hinted)),
    )
    journal = PendingCrawls(paths)
    journal.save(hint, scope=scope, full=False)
    journal.clear(source.source_name, manifest=hint, scope=scope)
    git(repo, "update-ref", "refs/heads/main", base)
    full = collect_source_changes(
        source.source_name, state=state, path_index=paths, full=True,
        source_aware=False, connector=connector(repo),
    )
    assert [(item.source, item.path) for item in full.deletions] == [
        (source.source_name + ":commits", f"commits/{hinted}.txt"),
    ]
    assert full.poll_refresh.history == hint.poll_refresh.history
    journal.save(full, scope=scope, full=True)
    assert journal.refresh_candidates(source.source_name, scope, source.source_name) is not None
    journal.clear(source.source_name, manifest=full, scope=scope)
    assert journal.sources() == []
