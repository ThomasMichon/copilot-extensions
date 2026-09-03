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

        def request(self, method, url, *, json, params, headers):
            captured.update(
                method=method,
                url=url,
                json=json,
                params=params,
                headers=headers,
            )
            return FakeResponse()

    monkeypatch.setattr(client.httpx, "Client", FakeHttpClient)

    api = client.AgentIndexClient(
        "http://indexer",
        instance_token="instance",
        transaction_token="transaction",
    )
    api.clusters(
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
    assert captured["headers"]["X-Agent-Index-Installation-Id"] == ""
    assert captured["headers"]["X-Agent-Index-Instance-Token"] == "instance"
    assert captured["headers"]["X-Agent-Index-Transaction-Token"] == "transaction"

    api.promote()
    assert captured["method"] == "POST"
    assert captured["url"] == "http://indexer/promote"
