"""A retry finishes acquired work before accepting a newer upstream cursor."""

from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest

from agent_index.indexing import engine
from agent_index.indexing.manifest import CrawlManifest, CrawlStats
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls, recover_source
from agent_index.indexing.pending_crawl import recovery_scope
from agent_index.indexing.state import IndexState
from agent_index.sources.base import FileEntry
from agent_index.sources.base import nonsecret_target
from agent_index.sources.delta import SourceDelta
from agent_index.sources.git_repo import GitRepoConnector
from agent_index.sources.github import GitHubConnector
from agent_index.sources.azure_devops import AzureDevOpsConnector


def item(path: str, content: str) -> FileEntry:
    return FileEntry(path, content, "markdown", "forge:repo")


def test_restart_replays_pinned_delta_before_newer_net_delta_can_omit_reverted_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    state_file = tmp_path / "state.json"
    config = SimpleNamespace(model_profiles={}, stream_batch_size=2, state_file=state_file)
    state = IndexState()
    upstream = "old"
    calls: list[str | None] = []
    contents: dict[str, str] = {}
    fail_second = False

    def discover(last: str | None, **kwargs: object) -> SourceDelta:
        calls.append(last)
        if upstream == "old":
            return SourceDelta(upserts=[item("first.md", "original")], commit="old")
        if upstream == "middle":
            return SourceDelta(
                upserts=[item("first.md", "temporary"), item("second.md", "second")],
                commit="middle",
            )
        # first.md is unchanged between old and latest, so a fresh net delta
        # from old would omit it despite the partial middle writes.
        entries = [item("second.md", "latest")]
        if last == "middle":
            entries.append(item("first.md", "original"))
        return SourceDelta(upserts=entries, commit="latest")

    connector = SimpleNamespace(
        source_name="forge:repo", discover_delta=discover, recovery_identity=("fixture-target",),
    )
    monkeypatch.setattr("agent_index.sources.get_connector", lambda *a, **k: connector)
    monkeypatch.setattr(engine, "selected_layout", lambda: None)
    chunker = SimpleNamespace(
        chunk=lambda content, path, **k: [SimpleNamespace(path=path, content=content)] * 2,
    )
    monkeypatch.setattr("agent_index.chunking.get_chunker", lambda path: (chunker, "text"))

    def embed(batch: list[SimpleNamespace], *args: object, **kwargs: object) -> int:
        if fail_second and batch[0].path == "second.md":
            raise RuntimeError("fixture interruption")
        for chunk in batch:
            contents[chunk.path] = chunk.content
        return len(batch)

    monkeypatch.setattr(engine, "_embed_and_store_batch", embed)
    store = SimpleNamespace(delete_stale_by_file=lambda *a, **k: 0)

    def run() -> None:
        engine._index_source(
            "alias", config=config, state=state, path_index=paths,
            multi_store=store, multi_clients={}, full=False,
        )

    run()
    upstream, fail_second = "middle", True
    with pytest.raises(RuntimeError, match="interruption"):
        run()
    assert contents["first.md"] == "temporary"
    assert IndexState.load(state_file).sources["alias"].last_commit == "old"
    # A fresh process sees the durable acquired target, not mutable provider input.
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState.load(state_file)
    calls.clear()
    upstream, fail_second = "latest", False
    run()
    assert calls == ["middle"]
    assert contents == {"first.md": "original", "second.md": "latest"}
    assert state.sources["alias"].last_commit == "latest"
    assert state.total_chunks == 4
    assert PendingCrawls(paths).load("alias", "alias") is None


def test_journal_survives_state_save_failure_before_fresh_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    manifest = CrawlManifest(
        source="alias", accounting_family="forge:repo", upserts=[item("one.md", "old")],
        commit="middle", stats=CrawlStats(files_changed=1),
    )
    journal.save(manifest, scope="scope", full=False)
    order: list[str] = []
    state = IndexState()
    state_file = tmp_path / "state.json"

    def apply(*, manifest: CrawlManifest, full: bool) -> tuple[int, int, int]:
        state.mark_source_indexed("alias", manifest.commit, 2)
        order.append("apply")
        return 2, 0, 1

    def fail_save(path: Path) -> None:
        raise OSError("fixture cursor write failure")

    with monkeypatch.context() as patch:
        patch.setattr(state, "save", fail_save)
        with pytest.raises(OSError, match="cursor write"):
            recover_source(
                "alias", scope="scope", state=state, state_file=state_file,
                paths=paths, full=False, acquire=lambda: pytest.fail("acquired before cursor save"),
                apply=apply,
            )
    assert journal.load("alias", "scope") == (manifest, False)

    def acquire() -> CrawlManifest:
        assert IndexState.load(state_file).sources["alias"].last_commit == "middle"
        assert journal.load("alias", "scope") is None
        order.append("acquire")
        return CrawlManifest(source="alias", commit="latest")

    recover_source(
        "alias", scope="scope", state=state, state_file=state_file,
        paths=paths, full=False, acquire=acquire, apply=apply,
    )
    assert order == ["apply", "apply", "acquire", "apply"]
    assert IndexState.load(state_file).sources["alias"].last_commit == "latest"


def test_journal_scope_or_corruption_refuses_before_effects(tmp_path: Path) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    manifest = CrawlManifest(source="alias", accounting_family="forge:repo", upserts=[item("a", "a")])
    journal.save(manifest, scope="old-account", full=True)
    with pytest.raises(ValueError, match="configuration"):
        journal.load("alias", "new-account")
    with sqlite3.connect(tmp_path / "paths.db") as conn:
        conn.execute("UPDATE pending_crawls SET payload = '{}'")
    with pytest.raises(ValueError, match="envelope"):
        journal.load("alias", "old-account")


def test_failed_atomic_state_publication_preserves_prior_cursor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_index.indexing import state as state_module

    path = tmp_path / "state.json"
    state = IndexState()
    state.mark_source_indexed("alias", "old", 2)
    state.save(path)
    before = path.read_bytes()
    state.mark_source_indexed("alias", "new", 3)

    def fail_replace(*args: object) -> None:
        raise OSError("fixture atomic replacement failure")

    monkeypatch.setattr(state_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement"):
        state.save(path)
    assert path.read_bytes() == before
    assert IndexState.load(path).sources["alias"].last_commit == "old"
    assert not list(tmp_path.glob(".state.json.*.tmp"))


@pytest.mark.parametrize("via_environment", [False, True])
def test_unchanged_spec_refuses_pending_replay_after_effective_git_target_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, via_environment: bool,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    config = SimpleNamespace(
        model_profiles={}, stream_batch_size=2, state_file=tmp_path / "state.json",
    )
    state = IndexState()
    source_spec = engine.SourceSpec("git")
    monkeypatch.setattr(GitRepoConnector, "_derive_repo_name", lambda self: "sample")
    monkeypatch.setattr(GitRepoConnector, "_git_quiet", lambda *a: "")
    monkeypatch.setattr(
        GitRepoConnector, "discover_delta",
        lambda *a, **k: SourceDelta(
            upserts=[FileEntry("one.md", "pending", "markdown", "git:sample")], commit="middle",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "agent_index.sources.get_connector",
        lambda source, **kwargs: GitRepoConnector(source, **kwargs),
    )
    monkeypatch.setattr(engine, "selected_layout", lambda: None)
    attempts: list[str] = []

    def chunk_fail(*args: object, **kwargs: object) -> list[object]:
        attempts.append("chunk")
        raise RuntimeError("fixture interruption")

    monkeypatch.setattr(
        "agent_index.chunking.get_chunker", lambda path: (SimpleNamespace(chunk=chunk_fail), "text"),
    )

    def run(target: str) -> None:
        kwargs = {} if via_environment else {"repo_path": str(tmp_path / target)}
        if via_environment:
            monkeypatch.setenv("AGENT_INDEX_GIT_REPO", str(tmp_path / target))
        engine._index_source(
            source_spec.name, source_scope="unchanged-spec-and-account", config=config,
            state=state, path_index=paths, multi_store=None, multi_clients={},
            full=False, connector_kwargs=kwargs,
        )

    with pytest.raises(RuntimeError, match="interruption"):
        run("old-target")
    with pytest.raises(ValueError, match="configuration"):
        run("new-target")
    assert attempts == ["chunk"]
    assert source_spec == engine.SourceSpec("git")
    assert state.sources == {}


def test_recovery_target_drops_url_credentials_but_refuses_ambiguous_semantics() -> None:
    assert nonsecret_target("https://example:placeholder@example.test/repo.git") == (
        "https://example.test/repo.git"
    )
    with pytest.raises(ValueError, match="ambiguous"):
        nonsecret_target("https://example.test/repo.git?target=other")


@pytest.mark.parametrize("scheme", ["ssh", "git+ssh", "ssh+git"])
def test_ssh_account_change_refuses_pending_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scheme: str,
) -> None:
    monkeypatch.setattr(GitRepoConnector, "_derive_repo_name", lambda self: "sample")
    remote = f"{scheme}://account_a@example.test/~/repo.git"
    monkeypatch.setattr(GitRepoConnector, "_git_quiet", lambda *a: remote)
    connector = GitRepoConnector(repo_path=tmp_path)
    old_scope = recovery_scope("same-spec", connector)
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    manifest = CrawlManifest(
        source="git", accounting_family="git:sample",
        upserts=[FileEntry("a.md", "old", "markdown", "git:sample")],
    )
    journal.save(manifest, scope=old_scope, full=False)
    remote = f"{scheme}://account_b@example.test/~/repo.git"
    with pytest.raises(ValueError, match="configuration"):
        journal.load("git", recovery_scope("same-spec", connector))
    assert nonsecret_target(remote) == remote
    password_remote = remote.replace("account_b@", "account_b:temporary-password@")
    assert nonsecret_target(password_remote) == remote


@pytest.mark.parametrize("pending_family", ["forge:repo", "forge:repo:issues"])
def test_pending_alias_blocks_sibling_but_not_an_independent_family(
    tmp_path: Path, pending_family: str,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    manifest = CrawlManifest(
        source="retired-alias", accounting_family=pending_family,
        upserts=[FileEntry("a.md", "middle", "markdown", pending_family)],
    )
    journal.save(manifest, scope="original", full=False)
    effects: list[str] = []
    for family in ("forge:repo", "forge:repo:issues", "forge:repo:issues:comments"):
        with pytest.raises(PendingCrawlConflict, match="retired-alias"):
            recover_source(
                "active-alias", scope="current", family=family, state=IndexState(),
                state_file=tmp_path / "state.json", paths=paths, full=False,
                acquire=lambda: effects.append("acquire"),
                apply=lambda **kwargs: effects.append("apply"),
            )
    assert effects == []
    journal.assert_family_available("retired-alias", "forge:repo")
    journal.assert_family_available("independent", "forge:other")
    journal.assert_family_available("prefix-sibling", "forge:repoextra")
    journal.assert_family_available("case-sibling", "FORGE:repo")


@pytest.mark.parametrize("change", ["endpoint", "account", "queries"])
def test_ado_effective_scope_change_refuses_pending_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    account = "account-a"
    monkeypatch.setattr(AzureDevOpsConnector, "_get_authenticated_user_id", lambda self: account)

    def connector(endpoint: str = "https://example.test") -> AzureDevOpsConnector:
        return AzureDevOpsConnector(
            "ado:org/project", token="fixture-placeholder", api_base=endpoint,
            work_item_queries=[{"wiql": "SELECT [System.Id] FROM WorkItems"}],
            pull_request_queries=[],
        )

    before = connector()
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    manifest = CrawlManifest(
        source=before.source_name, accounting_family=before.source_name,
        upserts=[FileEntry("a.md", "pending", "markdown", before.source_name)],
    )
    journal.save(manifest, scope=recovery_scope("same-spec", before), full=False)
    after = connector("https://other.example.test" if change == "endpoint" else before.api_base)
    if change == "account":
        account = "account-b"
    if change == "queries":
        after._work_item_queries = [{"saved_query_id": "different-query"}]
    with pytest.raises(ValueError, match="configuration"):
        journal.load(before.source_name, recovery_scope("same-spec", after))


@pytest.mark.parametrize("change", ["endpoint", "account", "since"])
def test_github_effective_scope_change_refuses_pending_replay(tmp_path: Path, change: str) -> None:
    account = 1
    probes: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user"
        probes.append(str(request.url))
        return httpx.Response(200, json={"id": account})

    def connector(endpoint: str = "https://example.test", since: str | None = None):
        return GitHubConnector(
            "github:org/repo", api_base=endpoint, since=since,
            token="fixture-placeholder", transport=httpx.MockTransport(transport), min_interval_s=0,
        )

    before = connector()
    old_scope = recovery_scope("same-spec", before)
    assert recovery_scope("same-spec", before) == old_scope
    assert len(probes) == 1
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    manifest = CrawlManifest(
        source=before.source_name, accounting_family=before.source_name,
        upserts=[FileEntry("a.md", "pending", "markdown", before.source_name)],
    )
    journal.save(manifest, scope=old_scope, full=False)
    if change == "account":
        account = 2
    after = connector(
        endpoint="https://other.example.test" if change == "endpoint" else before.api_base,
        since="2026-01-01T00:00:00Z" if change == "since" else None,
    )
    with pytest.raises(ValueError, match="configuration"):
        journal.load(before.source_name, recovery_scope("same-spec", after))


def test_github_installation_credentials_can_crawl_and_fence_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes: list[httpx.Request] = []
    monkeypatch.setattr(time, "sleep", lambda delay: pytest.fail("permission denial retried"))

    def transport(request: httpx.Request) -> httpx.Response:
        probes.append(request)
        if request.url.path == "/user":
            return httpx.Response(403, json={"message": "Resource not accessible by integration"})
        if request.url.path == "/installation/repositories":
            assert request.url.params["per_page"] == "1"
            return httpx.Response(200, json={"total_count": 2, "repositories": [{"id": 1}]})
        assert request.url.path == "/repos/org/repo/issues"
        return httpx.Response(200, json=[])

    def connector(token: str) -> GitHubConnector:
        value = GitHubConnector(
            "github:org/repo", token=token, min_interval_s=0,
            transport=httpx.MockTransport(transport),
        )
        return value

    before = connector("installation-fixture")
    scope = recovery_scope("spec", before)
    assert recovery_scope("spec", before) == scope
    assert before.discover() == []
    assert len(probes) == 3
    assert "installation-fixture" not in repr(before.recovery_identity)
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    manifest = CrawlManifest(
        source=before.source_name, accounting_family=before.source_name,
        upserts=[FileEntry("a.md", "pending", "markdown", before.source_name)],
    )
    journal.save(manifest, scope=scope, full=False)
    assert journal.load(before.source_name, recovery_scope("spec", connector("installation-fixture")))
    with pytest.raises(ValueError, match="configuration"):
        journal.load(before.source_name, recovery_scope("spec", connector("other-installation-fixture")))


@pytest.mark.parametrize("status", [401, 403, 404])
def test_github_failed_credentials_never_become_anonymous(status: int) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "Denied"})

    connector = GitHubConnector(
        "github:org/repo", token="invalid-fixture", min_interval_s=0,
        transport=httpx.MockTransport(transport),
    )
    connector._http._max_retries = 0
    with pytest.raises(httpx.HTTPStatusError):
        recovery_scope("spec", connector)
    assert connector._recovery_account is None


def test_github_injected_client_endpoint_is_in_recovery_fence() -> None:
    def connector(endpoint: str) -> GitHubConnector:
        client = httpx.Client(
            base_url=endpoint,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"id": 1})),
        )
        return GitHubConnector(
            "github:org/repo", api_base="https://configured.test", client=client,
            token="user-fixture", min_interval_s=0,
        )

    assert recovery_scope("spec", connector("https://first.test")) != recovery_scope(
        "spec", connector("https://second.test"),
    )


def test_github_user_token_rotation_preserves_principal() -> None:
    def connector(token: str) -> GitHubConnector:
        return GitHubConnector(
            "github:org/repo", token=token, min_interval_s=0,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"id": 1})),
        )

    assert recovery_scope("spec", connector("first-fixture")) == recovery_scope(
        "spec", connector("renewed-fixture"),
    )


def test_github_installation_fence_uses_credential_of_successful_request() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(404, json={"message": "Not Found"})
        assert request.url.path == "/installation/repositories"
        return httpx.Response(200, json={"repositories": []})

    tokens = iter(["expired-fixture", "verified-fixture"])
    renewed = GitHubConnector(
        "github:org/repo", token_provider=lambda: next(tokens), min_interval_s=0,
        transport=httpx.MockTransport(transport),
    )
    static = GitHubConnector(
        "github:org/repo", token="verified-fixture", min_interval_s=0,
        transport=httpx.MockTransport(transport),
    )
    assert recovery_scope("spec", renewed) == recovery_scope("spec", static)


def test_github_installation_identity_rejects_malformed_success() -> None:
    connector = GitHubConnector(
        "github:org/repo", token="fixture", min_interval_s=0,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                404 if request.url.path == "/user" else 200, json={},
            ),
        ),
    )
    with pytest.raises(ValueError, match="repositories"):
        recovery_scope("spec", connector)


@pytest.mark.parametrize("status,headers,message", [
    (403, {"Retry-After": "1"}, "Temporarily limited"),
    (403, {"X-RateLimit-Remaining": "0"}, "API rate limit exceeded"),
    (403, {}, "You have exceeded a secondary rate limit"),
    (403, {}, "Abuse detection mechanism"),
    (429, {"Retry-After": "1"}, "Slow down"),
    (503, {}, "Unavailable"),
])
def test_github_identity_probe_retains_throttle_and_transient_retries(
    monkeypatch: pytest.MonkeyPatch, status: int, headers: dict[str, str], message: str,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    probes: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        probes.append(request.url.path)
        if len(probes) == 1:
            return httpx.Response(status, headers=headers, json={"message": message})
        return httpx.Response(200, json={"id": 1})

    source = GitHubConnector(
        "github:org/repo", token="fixture", min_interval_s=0,
        transport=httpx.MockTransport(transport),
    )
    assert source.recovery_identity[3] == "1"
    assert probes == ["/user", "/user"]
    assert sleeps and all(delay > 0 for delay in sleeps)


def test_github_exhausted_throttle_does_not_probe_installation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    probes: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        probes.append(request.url.path)
        return httpx.Response(403, headers={"Retry-After": "1"}, json={})

    source = GitHubConnector(
        "github:org/repo", token="fixture", min_interval_s=0,
        transport=httpx.MockTransport(transport),
    )
    with pytest.raises(httpx.HTTPStatusError):
        recovery_scope("spec", source)
    assert probes == ["/user"] * 5
    assert source._recovery_account is None


def test_ado_injected_client_endpoint_is_in_recovery_fence() -> None:
    def connector(endpoint: str) -> AzureDevOpsConnector:
        client = httpx.Client(
            base_url=endpoint,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"authenticatedUser": {"id": "account-a"}}),
            ),
        )
        return AzureDevOpsConnector(
            "ado:org/project", api_base="https://configured.test", client=client,
            token="fixture", work_item_queries=[], pull_request_queries=[], min_interval_s=0,
        )

    assert recovery_scope("spec", connector("https://first.test")) != recovery_scope(
        "spec", connector("https://second.test"),
    )


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("source_aware", [False, True])
def test_gc_cannot_retire_state_while_a_crawl_is_pending(
    tmp_path: Path, stale: bool, source_aware: bool,
) -> None:
    from agent_index.indexing.gc import gc_configured_snapshot, gc_stale_sources
    from agent_index.indexing.state import SourceState

    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed("retired:family", "a.md", content_hash="old", chunk_count=2)
    state = IndexState(
        sources={"retired-alias": SourceState(last_commit="old", chunk_count=2)},
        stored_families={"retired-alias": "retired:family"}, total_chunks=2,
    )
    journal = PendingCrawls(paths)
    manifest = CrawlManifest(
        source="retired-alias", accounting_family="retired:family",
        upserts=[FileEntry("a.md", "middle", "markdown", "retired:family")],
    )
    journal.save(manifest, scope="original", full=False)
    store = SimpleNamespace(
        source_counts=lambda: {"retired:family": 2},
        delete_by_source_exact=lambda *a: pytest.fail("deleted before pending crawl completed"),
    )
    if stale:
        with pytest.raises(PendingCrawlConflict, match="original configuration"):
            gc_stale_sources(store, paths, state)
    else:
        result = gc_configured_snapshot(
            store, paths, state, configured_names=frozenset(), source_aware=source_aware,
        )
        assert result["deferred"] == result["pending_crawls"] == ["retired-alias"]
        assert result["purged"] == {}
    assert state.sources["retired-alias"].last_commit == "old"
    assert state.total_chunks == 2
    assert paths.get_content_hash("retired:family", "a.md") == "old"
    assert journal.load("retired-alias", "original") == (manifest, False)


@pytest.mark.parametrize("full", [False, True])
def test_legacy_reconciliation_uses_canonical_not_requested_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, full: bool,
) -> None:
    from agent_index.indexing.crawl import collect_source_changes

    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed("forge:repo", "gone.md")
    paths.mark_indexed("forge:other", "keep.md")
    connector = SimpleNamespace(
        source_name="forge:repo", current_commit=lambda: "target",
        discover=lambda **k: [], discover_changed=lambda *a, **k: [],
        list_paths=lambda **k: {"forge:repo": set()},
    )
    manifest = collect_source_changes(
        "forge", state=IndexState(), path_index=paths, full=full, source_aware=False,
        connector=connector,
    )
    assert [(entry.source, entry.path) for entry in manifest.deletions] == [
        ("forge:repo", "gone.md"),
    ]
    journal = PendingCrawls(paths)
    journal.save(manifest, scope="scope", full=full)
    assert journal.load("forge", "scope") == (manifest, full)


def test_overlapping_journal_admission_is_atomic_and_winner_can_recover(tmp_path: Path) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    barrier = Barrier(2)

    def admit(source: str) -> tuple[str, bool]:
        manifest = CrawlManifest(
            source=source, accounting_family="forge:repo",
            upserts=[item("one.md", source)], commit="middle",
        )
        journal.assert_family_available(source, "forge:repo")
        barrier.wait(timeout=10)
        try:
            journal.save(manifest, scope="scope", full=False)
        except PendingCrawlConflict:
            return source, False
        return source, True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(admit, ("alias-one", "alias-two")))
    assert sum(admitted for source, admitted in results) == 1
    winner = next(source for source, admitted in results if admitted)
    assert journal.sources() == [winner]
    applied: list[str] = []
    state = IndexState()

    def apply(*, manifest: CrawlManifest, full: bool) -> tuple[int, int, int]:
        applied.extend(entry.content for entry in manifest.upserts)
        state.mark_source_indexed(winner, manifest.commit, 1)
        return len(manifest.upserts), 0, len(manifest.upserts)

    recover_source(
        winner, scope="scope", family="forge:repo", state=state,
        state_file=tmp_path / "state.json", paths=paths, full=False, apply=apply,
        acquire=lambda: CrawlManifest(source=winner, commit="latest"),
    )
    assert applied == [winner]
    assert journal.sources() == []
