"""Opt-in hosted HTTP acquisition/shared-engine composition with native stores."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

import httpx
import numpy as np
import pytest
from test_native_delta_matrix import FixtureClient, RoundFixture, measure_round, no_lifecycle

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.indexing.paged_crawl import decode_acquisition
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.delta import SourceDelta
from agent_index.sources.github import GitHubConnector
from agent_index.store.source_layout import LAYOUT

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]
SOURCE = "github:acme/widgets"
SPEC = engine.SourceSpec(SOURCE, type="github")
URL = "https://api.github.com/repos/acme/widgets"
BASE = "2026-01-01T00:00:00+00:00"
INITIAL = "2026-01-02T00:00:00+00:00"
NEXT = "2026-01-03T00:00:00+00:00"
FINAL = "2026-01-04T00:00:00+00:00"


def item(number, **changes):
    return {
        "number": number, "title": f"Item {number}", "body": "Original useful body.",
        "comments": 2 if number == 1 else 0, "updated_at": INITIAL,
        "repository_url": URL, "url": URL + f"/issues/{number}", "state": "open",
        **({"pull_request": {}} if number == 2 else {}), **changes,
    }


@dataclass
class Hosted:
    config: IndexConfig
    records: dict[int, dict] = field(default_factory=lambda: {n: item(n) for n in (1, 2)})
    listed: tuple[int, ...] = (1, 2)
    revisions: dict[int, str] = field(default_factory=dict)
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    account: int = 123
    interrupted: bool = False
    truncated: bool = False
    since: str = "2025-12-31T23:59:00Z"

    def handle(self, request):
        assert "if-none-match" not in request.headers
        path, params = request.url.path, dict(request.url.params)
        self.requests.append((path, params))
        if path == "/user":
            return httpx.Response(200, json={"id": self.account})
        if path == "/repos/acme/widgets/issues":
            assert params == {
                "state": "all", "sort": "updated", "direction": "asc",
                "since": self.since, "per_page": "100", "page": "1",
            }
            return httpx.Response(
                200, json=[{
                    **self.records[n], "updated_at": self.revisions.get(
                        n, self.records[n]["updated_at"],
                    ),
                } for n in self.listed],
                headers={"Link": f'<{URL}/issues?page=2>; rel="next"'} if self.truncated else {},
            )
        number = int(path.split("/issues/")[1].split("/")[0])
        assert number in self.records
        if path.endswith("/comments"):
            assert params["per_page"] == "100"
            if self.interrupted and number == 2:
                raise RuntimeError("interrupted comment acquisition")
            page = int(params["page"])
            if number == 2:
                assert page == 1
                return httpx.Response(200, json=[])
            assert page in (1, 2)
            return httpx.Response(200, json=[{
                "id": page, "body": f"Useful comment {page}.",
                "updated_at": INITIAL, "user": {"login": "contributor"},
            }], headers=(
                {"Link": f'<{URL}/issues/1/comments?page=2>; rel="next"'} if page == 1 else {}
            ))
        assert path == f"/repos/acme/widgets/issues/{number}" and params == {}
        return httpx.Response(200, json=self.records[number])

    def connector(self):
        return GitHubConnector(
            SOURCE, token="fixture", min_interval_s=0, transport=httpx.MockTransport(self.handle),
        )

    def run(self, monkeypatch, *, failure=None):
        provider = self.connector()
        try:
            with monkeypatch.context() as patch:
                patch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: provider)
                patch.setattr(
                    PathIndex, "get_paths_by_family",
                    lambda *_a, **_k: pytest.fail("ordinary hosted round scanned corpus paths"),
                )
                result = engine.run_reindex(
                    source=SOURCE, config=self.config, source_specs=[SPEC],
                    embedding_runtime=EmbeddingRuntime(
                        lambda _: FixtureClient(), no_lifecycle, no_lifecycle, manage_engines=False,
                    ),
                )
            if failure is None:
                assert "sources_failed" not in result, result
            else:
                assert len(result["sources_failed"]) == 1, result
                assert failure in result["sources_failed"][0]["error"], result
            return result
        finally:
            provider._client.close()

    def entries(self):
        provider = self.connector()
        try:
            return [
                (provider._pull_entry if n == 2 else provider._issue_entry)(
                    record, [{
                        "id": page, "body": f"Useful comment {page}.",
                        "updated_at": INITIAL, "user": {"login": "contributor"},
                    } for page in (1, 2)] if n == 1 else [],
                ) for n, record in self.records.items()
            ]
        finally:
            provider._client.close()


def projection(fixture):
    store = fixture.store()
    owners = [store.content_store, *(store.get_vector_table(m) for m in store.model_ids)]
    return (
        [owner._get_or_create_table().version for owner in owners],
        store._source_domain.receipt() if fixture.selected else None,
        store.content_store.fts_current() if fixture.selected else None,
    )


def hosted_chunk_ids(store):
    return {
        cid for family, path in (
            (SOURCE + ":issues", "issues/1.md"), (SOURCE + ":pulls", "pulls/2.md"),
        ) for cid in store.content_store.get_chunk_ids_by_file(family, path)
    }


def assert_reference(fixture, hosted, directory, monkeypatch):
    entries = hosted.entries()
    reference = RoundFixture(replace(fixture.config, data_dir=directory), fixture.selected)
    provider = type("Reference", (), {
        "source_name": SOURCE, "recovery_identity": ("reference",),
        "discover_delta": lambda *_a, **_k: SourceDelta(upserts=entries, commit="rebuilt"),
    })()
    with monkeypatch.context() as patch:
        patch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: provider)
        result = engine.run_reindex(
            source=SOURCE, config=reference.config, source_specs=[SPEC],
            embedding_runtime=EmbeddingRuntime(
                lambda _: FixtureClient(), no_lifecycle, no_lifecycle, manage_engines=False,
            ),
        )
    assert "sources_failed" not in result, result
    actual, clean = fixture.store(), reference.store()
    for entry in entries:
        family = entry.source
        ids = actual.content_store.get_chunk_ids_by_file(family, entry.path)
        clean_ids = clean.content_store.get_chunk_ids_by_file(family, entry.path)
        assert ids and set(ids) == set(clean_ids)
        records = actual.content_store.get_by_ids(ids)
        expected = clean.content_store.get_by_ids(clean_ids)
        for cid in ids:
            assert records[cid] == expected[cid]
        for model in actual.model_ids:
            left = actual.get_vector_table(model).get_vectors(ids, strict=True)
            right = clean.get_vector_table(model).get_vectors(clean_ids, strict=True)
            assert left.keys() == right.keys() == set(ids)
            for cid in ids:
                np.testing.assert_array_equal(left[cid], right[cid])


@pytest.mark.skipif(sys.platform != "linux", reason="native process I/O qualification is Linux-only")
@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
@pytest.mark.parametrize("model_count", [1, 2])
def test_hosted_timestamp_overlap_at_three_corpus_sizes(
    tmp_path, monkeypatch, capsys, selected, model_count,
):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    profiles = {
        model: ModelProfile(model, "fixture", dim=2, table_name=f"vectors_{model}")
        for model in ("a", "b")[:model_count]
    }
    baselines: dict[str, dict[str, Any]] = {}
    measurements = {}
    for size in (100, 10_000, 100_000):
        fixture = RoundFixture(IndexConfig(
            data_dir=tmp_path / str(size), model_profiles=profiles, cluster_enabled=False,
        ), selected, SourceDelta(commit="unrelated"))
        with monkeypatch.context() as patch:
            patch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: fixture)
            fixture.run()
        unchanged = fixture.seed(size, monkeypatch=monkeypatch)
        hosted = Hosted(fixture.config)
        state = IndexState.load(fixture.config.state_file)
        provider = hosted.connector()
        scope = recovery_scope(engine.source_scope_hash(SPEC), provider)
        provider._client.close()
        state.sources[SOURCE] = SourceState(last_commit=BASE, acquisition_scope=scope)
        state.stored_families[SOURCE] = SOURCE
        state.save(fixture.config.state_file)
        hosted.run(monkeypatch)
        seen_ids = hosted_chunk_ids(fixture.store())
        assert len(seen_ids) == 7
        checkpoint = INITIAL
        journal = PendingCrawls(PathIndex(fixture.config.data_dir / "path_index.db"))
        saved = None
        measurements[size] = {}
        for name in (
            "noop", "overlap", "equal-time-edit", "concurrent-newer", "catch-up", "skew",
            "interrupted", "wrong-account", "resume", "truncated",
        ):
            failure = None
            acquired, changed = 0, 0
            rows, embedded = 0, 0
            expected_http = [("/user", {})]
            list_request = name not in {"wrong-account", "resume"}
            hosted.listed = (1, 2)
            if name == "noop":
                hosted.listed = ()
            elif name == "overlap":
                acquired = 2
            elif name == "equal-time-edit":
                hosted.records[1] = item(1, body="Equal timestamp replacement.")
                acquired, changed = 2, 1
                rows = embedded = 1
            elif name == "concurrent-newer":
                hosted.listed = (1,)
                hosted.revisions[1] = INITIAL
                hosted.records[1] = item(1, body="Concurrent newer replacement.", updated_at=NEXT)
                acquired = changed = 1
                rows, embedded = 5, 2
            elif name == "catch-up":
                hosted.listed = (1,)
                hosted.revisions.clear()
                acquired = 1
            elif name == "skew":
                hosted.listed = (2,)
                hosted.records[2] = item(
                    2, body="Edit within the overlap.", updated_at="2026-01-02T23:59:30+00:00",
                )
                acquired = changed = 1
                rows = embedded = 2
            elif name == "interrupted":
                hosted.records = {
                    n: item(n, body=f"Final replacement {n}.", updated_at=FINAL) for n in (1, 2)
                }
                hosted.interrupted = True
                failure = "interrupted comment acquisition"
            elif name == "wrong-account":
                hosted.account = 456
                failure = "different source/account configuration"
            elif name == "resume":
                hosted.account, hosted.interrupted = 123, False
                acquired = changed = 2
                rows, embedded = 7, 4
            elif name == "truncated":
                hosted.truncated = True
                failure = "exceeds 100 identities"
            hosted.since = (
                datetime.fromisoformat(checkpoint) - timedelta(seconds=60)
            ).isoformat().replace("+00:00", "Z")
            if list_request:
                expected_http.append(("/repos/acme/widgets/issues", {
                    "state": "all", "sort": "updated", "direction": "asc",
                    "since": hosted.since, "per_page": "100", "page": "1",
                }))
            numbers = (2,) if name == "resume" else (
                () if name in {"wrong-account", "truncated"} else hosted.listed
            )
            for n in numbers:
                path = f"/repos/acme/widgets/issues/{n}"
                expected_http.append((path, {}))
                for page in ((1, 2) if n == 1 else (1,)):
                    expected_http.append((path + "/comments", {"per_page": "100", "page": str(page)}))
            before = projection(fixture)
            before_state = IndexState.load(fixture.config.state_file).sources[SOURCE]
            hosted.requests.clear()
            measurement = measure_round(
                lambda: hosted.run(monkeypatch, failure=failure), monkeypatch,
            )
            assert hosted.requests == expected_http, (name, hosted.requests)
            counts = measurement["counts"]
            assert counts["upserts_acquired"] == acquired
            assert counts["files_chunked"] == changed
            assert counts["content_rows_upserted"] == rows, (name, counts)
            assert counts["cluster_pass_attempts"] == counts["cluster_pass_completed"] == 0
            assert counts["http"]["attempts"] == len(expected_http)
            assert counts["http"]["retries"] == counts["http"]["not_modified"] == 0
            if changed:
                assert set(counts["models"]) == set(profiles)
            assert all(value["chunks_embedded"] == value["vector_rows_upserted"] == embedded
                       for value in counts["models"].values()), (name, counts)
            if not changed:
                assert projection(fixture) == before
                assert counts["fts_ensure_attempts"] == 0
            if name == "catch-up":
                checkpoint = NEXT
            elif name == "resume":
                checkpoint = FINAL
            current = IndexState.load(fixture.config.state_file).sources[SOURCE]
            assert current.last_commit == checkpoint
            if failure:
                assert current == before_state
            if name == "interrupted":
                saved = journal.read_payload(SOURCE, scope)
                progress = decode_acquisition(saved, SOURCE)
                assert progress.base_commit == NEXT and progress.manifest.commit == FINAL
                assert len(progress.manifest.upserts) == 1
                assert progress.manifest.upserts[0].path == "issues/1.md"
                assert json.loads(progress.cursor)["index"] == 1
            elif name == "wrong-account":
                assert journal.read_payload(SOURCE, scope) == saved
            else:
                assert journal.sources() == []
            if name not in {"interrupted", "wrong-account"}:
                assert_reference(
                    fixture, hosted, tmp_path / f"reference-{size}-{name}", monkeypatch,
                )
            store = fixture.store()
            current_ids = hosted_chunk_ids(store)
            assert len(current_ids) == 7
            retired_ids = list(seen_ids - current_ids)
            seen_ids.update(current_ids)
            assert store.content_store.source_counts() == {
                "sample:repo": size, SOURCE + ":issues": 5, SOURCE + ":pulls": 2,
            }
            sentinels = [unchanged[0].chunk_id, unchanged[-1].chunk_id]
            assert set(store.content_store.get_by_ids(sentinels)) == set(sentinels)
            for model in profiles:
                table = store.get_vector_table(model)
                assert table.count() == size + len(current_ids)
                assert table.get_vectors(retired_ids, strict=True) == {}
                vectors = table.get_vectors(sentinels, strict=True)
                assert vectors.keys() == set(sentinels)
                for vector in vectors.values():
                    np.testing.assert_array_equal(vector, [1, 1])
            if size == 100:
                baselines[name] = measurement
            else:
                assert counts == baselines[name]["counts"], (size, name, measurement)
                assert measurement["native"] == baselines[name]["native"], (size, name, measurement)
            measurements[size][name] = measurement
            with capsys.disabled():
                print("NATIVE_HOSTED_WORK " + json.dumps({
                    "size": size, "layout": "source-aware" if selected else "legacy",
                    "models": model_count, "case": name, "http_requests": len(hosted.requests),
                    "native": measurement["native"], "io": measurement["io"],
                }, sort_keys=True))

    for size, rounds in measurements.items():
        for name, measurement in rounds.items():
            chunks = max(1, measurement["counts"]["chunks_generated"])
            assert measurement["native"]["rows_scanned"] < 32 * chunks, (size, name, measurement)
            assert measurement["native"]["read_iops"] < 32 * chunks, (size, name, measurement)
            assert measurement["io"]["rchar"] < (
                baselines[name]["io"]["rchar"] + chunks * 1024 * 1024
            ), (size, name, measurement)
            assert measurement["io"]["wchar"] < (
                baselines[name]["io"]["wchar"] + 512 * 1024
            ), (size, name, measurement)
