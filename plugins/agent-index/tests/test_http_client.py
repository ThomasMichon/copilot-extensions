from __future__ import annotations

from agent_index import client


def test_clusters_omits_unspecified_filters(monkeypatch):
    captured = {}

    class FakeResponse:
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    class FakeHttpClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def request(self, method, url, *, json, params):
            captured.update(
                method=method,
                url=url,
                json=json,
                params=params,
            )
            return FakeResponse()

    monkeypatch.setattr(client.httpx, "Client", FakeHttpClient)

    client.AgentIndexClient("http://indexer").clusters(
        source=None,
        bucket=None,
        model=None,
        exact_dupes_only=False,
        limit=50,
    )

    assert captured["params"] == {
        "exact_dupes_only": False,
        "limit": 50,
    }
