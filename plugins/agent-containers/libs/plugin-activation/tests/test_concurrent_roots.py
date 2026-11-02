from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest
from dropin_registry import ScanAuthority
from plugin_resolve import RepoPluginSettings

from plugin_activation import resolver
from plugin_activation._config_io import _SettingsLoad
from plugin_activation._installed_root import _Candidate
from plugin_activation._memo import MarketplaceMemo, memo_get


@pytest.fixture
def sources(monkeypatch, tmp_path):
    names = [f"p{i}@example" for i in range(8)]
    settings = _SettingsLoad(
        settings=RepoPluginSettings(enabled={name: True for name in names}),
        authority=ScanAuthority.COMPLETE,
    )
    monkeypatch.setattr(resolver, "_user_settings", lambda *a, **kw: settings)
    monkeypatch.setattr(resolver, "_installed_root", lambda *a: _Candidate())
    monkeypatch.setattr(
        resolver, "_local_root",
        lambda source, *a, **kw: _Candidate(root=tmp_path / source.split("@")[0]),
    )
    return names


def test_parallel_roots_overlap_with_a_fixed_bound_and_identical_results(monkeypatch, sources, tmp_path):
    serial = resolver.resolve_active_plugins(home=tmp_path, include_projects=False)
    started = Event()
    lock = Lock()
    active = peak = 0

    def resolve(source, *a, **kw):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 4:
                started.set()
        try:
            assert started.wait(3), "independent root reads were serialized"
            return _Candidate(root=tmp_path / source.split("@")[0])
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(resolver, "_local_root", resolve)
    parallel = resolver.resolve_active_plugins(
        home=tmp_path, include_projects=False, concurrent_roots=True,
    )
    assert parallel == serial
    assert peak == 4
    assert active == 0


@pytest.mark.parametrize("concurrent", [False, True])
def test_named_scope_never_schedules_excluded_sources(monkeypatch, sources, tmp_path, concurrent):
    called = []

    def resolve(source, *a, **kw):
        called.append(source)
        return _Candidate(root=tmp_path / source.split("@")[0])

    monkeypatch.setattr(resolver, "_local_root", resolve)
    result = resolver.resolve_active_plugins(
        home=tmp_path, include_projects=False, plugin_names=("p0", "p2"),
        concurrent_roots=concurrent,
    )
    assert set(called) == {"p0@example", "p2@example"}
    assert set(result.active) == set(called)


def test_failed_scan_joins_every_started_root_read(monkeypatch, sources, tmp_path):
    sibling_started, failed, release, finished = (Event() for _ in range(4))

    def resolve(source, *a, **kw):
        if source == "p0@example":
            assert sibling_started.wait(3)
            failed.set()
            raise RuntimeError("root unavailable")
        sibling_started.set()
        assert release.wait(3)
        finished.set()
        return _Candidate(root=tmp_path / source.split("@")[0])

    monkeypatch.setattr(resolver, "_local_root", resolve)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            resolver.resolve_active_plugins, home=tmp_path, include_projects=False,
            plugin_names=("p0", "p1"), concurrent_roots=True,
        )
        try:
            assert failed.wait(3)
            assert not pending.done()
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="root unavailable"):
            pending.result(timeout=3)
    assert finished.is_set()


def test_parallel_marketplace_memo_loads_shared_data_once():
    memo = MarketplaceMemo.concurrent()
    started, release = Event(), Event()
    calls = []
    marker = object()

    def load():
        calls.append("load")
        started.set()
        assert release.wait(3)
        return marker

    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = [
            executor.submit(memo_get, memo.marketplaces, Path("example"), load)
            for _ in range(4)
        ]
        try:
            assert started.wait(3)
        finally:
            release.set()
        assert all(future.result(timeout=3) is marker for future in pending)
    assert calls == ["load"]


def test_parallel_file_resolution_retains_identity_findings_and_fresh_disablement(tmp_path):
    home = tmp_path / "home"
    market = tmp_path / "market"
    settings_path = home / ".copilot" / "settings.json"
    settings = {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "directory", "path": str(market)}},
        },
        "enabledPlugins": {
            "good@example": True, "missing@example": True, "wrong@example": True,
        },
    }
    documents = {
        settings_path: settings,
        market / ".claude-plugin" / "marketplace.json": {
            "name": "example", "plugins": [
                {"name": name, "source": f"./{name}"} for name in ("good", "missing", "wrong")
            ],
        },
        market / "good" / ".claude-plugin" / "plugin.json": {"name": "good"},
        market / "wrong" / ".claude-plugin" / "plugin.json": {"name": "different"},
    }
    for path, value in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    serial = resolver.resolve_active_plugins(home=home, include_projects=False)
    parallel = resolver.resolve_active_plugins(
        home=home, include_projects=False, concurrent_roots=True,
    )
    assert parallel == serial
    assert set(parallel.active) == {"good@example"}
    settings["enabledPlugins"]["good@example"] = False
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    refreshed = resolver.resolve_active_plugins(
        home=home, include_projects=False, concurrent_roots=True,
    )
    assert "good@example" not in refreshed.active
