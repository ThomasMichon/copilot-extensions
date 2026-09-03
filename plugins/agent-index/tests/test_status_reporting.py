"""Regression tests for status reporting robustness (dotfiles issue #1531).

`status` must never fabricate ``chunks: 0`` when the service/store cannot be
measured, and a per-source histogram failure must never zero a valid count.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import httpx

from agent_index import __main__ as cli
from agent_index import server


class _FakeSearch:
    def __init__(self, rows):
        self._rows = rows

    def select(self, _cols):
        return self

    def limit(self, _n):
        return self

    def to_list(self):
        return list(self._rows)


class _FakeTable:
    def __init__(self, count, *, rows=None, search_raises=False):
        self._count = count
        self._rows = rows or []
        self._search_raises = search_raises

    def count_rows(self):
        return self._count

    def search(self):
        if self._search_raises:
            raise RuntimeError("histogram exploded")
        return _FakeSearch(self._rows)


class _FakeDB:
    def __init__(self, tables):
        self._tables = tables

    def table_names(self):
        return list(self._tables)

    def open_table(self, name):
        return self._tables[name]


def _install_fake_lancedb(monkeypatch, tmp_path, tables) -> None:
    lance_dir = tmp_path / "lance"
    lance_dir.mkdir(parents=True, exist_ok=True)

    fake_lancedb = SimpleNamespace(connect=lambda _p: _FakeDB(tables))
    monkeypatch.setitem(sys.modules, "lancedb", fake_lancedb)
    monkeypatch.setattr(server, "data_dir", lambda: tmp_path)


def test_histogram_failure_does_not_zero_valid_count(monkeypatch, tmp_path) -> None:
    tables = {"chunks": _FakeTable(29937, search_raises=True)}
    _install_fake_lancedb(monkeypatch, tmp_path, tables)

    result = server._index_status(include_sources=True)

    assert result["chunks"] == 29937
    assert result["available"] is True
    assert result["sources"] == {}  # histogram failed, but count survived


def test_sources_are_lazy_by_default(monkeypatch, tmp_path) -> None:
    rows = [{"source": "git:repo"}, {"source": "git:repo"}, {"source": "docs"}]
    tables = {"chunks": _FakeTable(3, rows=rows)}
    _install_fake_lancedb(monkeypatch, tmp_path, tables)

    without = server._index_status()
    assert without["chunks"] == 3
    assert without["sources"] == {}

    with_hist = server._index_status(include_sources=True)
    assert with_hist["chunks"] == 3
    assert with_hist["sources"] == {"git:repo": 2, "docs": 1}


def test_count_failure_reports_unknown_not_zero(monkeypatch, tmp_path) -> None:
    class _Boom(_FakeTable):
        def count_rows(self):
            raise RuntimeError("cannot count")

    tables = {"chunks": _Boom(0)}
    _install_fake_lancedb(monkeypatch, tmp_path, tables)

    result = server._index_status()

    assert result["chunks"] is None  # unknown, never a fabricated 0
    assert result["available"] is None
    assert result["tables"]["chunks"] is None


def test_missing_lance_dir_is_measured_empty(monkeypatch, tmp_path) -> None:
    fake_lancedb = SimpleNamespace(connect=lambda _p: _FakeDB({}))
    monkeypatch.setitem(sys.modules, "lancedb", fake_lancedb)
    monkeypatch.setattr(server, "data_dir", lambda: tmp_path)  # no lance/ dir

    result = server._index_status()

    assert result["chunks"] == 0  # genuinely absent index
    assert result["available"] is False


def test_absent_content_table_is_measured_empty(monkeypatch, tmp_path) -> None:
    # Store reads fine but the content table simply isn't there yet.
    tables = {"other": _FakeTable(5)}
    _install_fake_lancedb(monkeypatch, tmp_path, tables)

    result = server._index_status()

    assert result["chunks"] == 0  # readable store, empty => 0, not "unknown"
    assert result["available"] is False


def test_status_payload_unreachable_reports_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setattr("agent_index.transport.plan_route", lambda: ("host", None))
    monkeypatch.setattr(cli, "client_url", lambda: "http://127.0.0.1:9")

    class _FailingClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, _url):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(cli.httpx, "Client", _FailingClient)

    payload = cli._status_payload()

    assert payload["running"] is False
    assert payload["index"]["chunks"] is None  # never a fabricated 0
    assert payload["index"]["available"] is None
    assert payload["index"]["unreachable"] is True
    assert payload["schema"] == "agent-index.lifecycle"
    assert payload["runtime"]["state"] == "ready"


def test_status_payload_no_endpoint_reports_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setattr("agent_index.transport.plan_route", lambda: ("host", None))
    monkeypatch.setattr(cli, "client_url", lambda: "")

    payload = cli._status_payload()

    assert payload["running"] is False
    assert payload["index"]["chunks"] is None
    assert payload["index"]["unreachable"] is True
    assert payload["schema"] == "agent-index.lifecycle"
    assert payload["runtime"]["state"] == "ready"


def test_status_payload_reports_draining_as_not_ready(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")
    monkeypatch.setattr("agent_index.transport.plan_route", lambda: ("host", None))
    monkeypatch.setattr(cli, "client_url", lambda: "http://127.0.0.1:4444")
    monkeypatch.setattr(
        cli,
        "_routing_endpoint_for_url",
        lambda _url: SimpleNamespace(
            base_url="http://127.0.0.1:4444",
            pid=123,
            version="9.9.9",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_owned_service_status",
        lambda *_args, **_kwargs: {
            "plugin": "agent-index",
            "installationId": "cell-a/agent-index",
            "version": "9.9.9",
            "pid": 123,
            "instanceToken": "exact-token",
            "promoted": True,
            "status": "draining",
        },
    )

    class _Response:
        @staticmethod
        def json():
            return {
                "installationId": "cell-a/agent-index",
                "version": "9.9.9",
                "pid": 123,
                "instanceToken": "exact-token",
                "promoted": True,
                "draining": True,
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @staticmethod
        def get(_url):
            return _Response()

    monkeypatch.setattr(cli.httpx, "Client", _Client)

    payload = cli._status_payload()

    assert payload["state"] == "draining"
    assert payload["running"] is False
    assert payload["configured"] is True


def test_status_payload_without_role_is_setup_required(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_INDEX_ROLE", raising=False)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))

    payload = cli._status_payload()

    assert payload["state"] == "setup_required"
    assert payload["setup_required"] is True
    assert payload["role"] is None
    assert payload["running"] is False
    assert payload["schema_version"] == 1
    assert payload["version"] == cli.__version__
