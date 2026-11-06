"""Pure controls for optional copied-payload diagnostics; no native actors."""

import asyncio
from dataclasses import replace
import gc
import importlib.util
import json
from pathlib import Path
import secrets
import shutil
import sys
from types import ModuleType, SimpleNamespace
import weakref

import pytest

from agent_mcp import config, execution_context, incoming
from agent_mcp.config import AuthSpec, BridgeConfig, ServerSpec

_fixture_spec = importlib.util.spec_from_file_location(
    "mcp_incoming_fixture_diagnostics", Path(__file__).with_name("incoming_diagnostics.py"),
)
fixture = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(fixture)


@pytest.mark.parametrize("difference", ["none", "environment", "config", "cwd"])
def test_actual_matcher_and_digest_unchanged(tmp_path, monkeypatch, difference):
    package = tmp_path / "src" / "agent_mcp"
    package.mkdir(parents=True)
    source = Path(execution_context.__file__).read_text(encoding="utf-8")
    (package / "execution_context.py").write_text(source, encoding="utf-8")
    for name in ("ipc.py", "serve.py", "__main__.py"):
        shutil.copyfile(Path(execution_context.__file__).with_name(name), package / name)
    fixture.instrument_payload(tmp_path, tmp_path / "events", "unit")
    monkeypatch.setitem(sys.modules, "agent_mcp._fixture_diagnostics", fixture)
    original = ModuleType("agent_mcp._fixture_original")
    observed = ModuleType("agent_mcp._fixture_observed")
    for module, text in (
        (original, source),
        (observed, (package / "execution_context.py").read_text(encoding="utf-8")),
    ):
        module.__package__ = "agent_mcp"
        exec(compile(text, "fixture-context", "exec"), module.__dict__)
    key = secrets.token_hex(32)
    environment = {"FIXTURE_CREDENTIAL": "synthetic-sensitive-value", "STABLE_INPUT": "same"}
    cwd = ["/fixture/caller"]
    cfg = BridgeConfig(server=ServerSpec(type="stdio"), auth=AuthSpec(kind="none"))
    selected = [cfg]
    monkeypatch.setattr(config, "load_config", lambda *a, **k: selected[0])
    monkeypatch.setattr(incoming, "launch_generation", lambda: "fixture-generation")
    monkeypatch.setattr(incoming, "_ping", lambda handle: {
        "execution_context": {"contract": execution_context._CONTRACT, "key": key},
    })
    for module in (original, observed):
        module._KEY = key
        module.semantic_environment = lambda cfg: dict(environment)
        module.os = SimpleNamespace(getcwd=lambda: cwd[0])
    expected = original.request_fields("fixture", "bridge")
    actual = observed.request_fields("fixture", "bridge")
    assert actual["execution_context"] == expected["execution_context"]
    assert set(actual) == set(expected) | {fixture.FIELD}
    if difference == "environment":
        environment["FIXTURE_CREDENTIAL"] = "different-sensitive-value"
    elif difference == "config":
        selected[0] = replace(cfg, timeout=41.0)
    elif difference == "cwd":
        cwd[0] = "/fixture/server"
    loader = lambda *a, **k: selected[0]
    if difference == "none":
        assert original.load_config("bridge", expected, loader, repository_root=tmp_path) is cfg
        assert observed.load_config("bridge", actual, loader, repository_root=tmp_path) is cfg
    else:
        for module, request in ((original, expected), (observed, actual)):
            with pytest.raises(execution_context.SharingUnavailable):
                # The copied module owns its own exception class.
                try:
                    module.load_config("bridge", request, loader, repository_root=tmp_path)
                except module.SharingUnavailable as exc:
                    raise execution_context.SharingUnavailable(str(exc)) from None
    records = fixture.read_records(tmp_path / "events")
    assert records[-1]["event"] == ("context_accepted" if difference == "none" else "context_refused")
    assert records[-1]["different_environment"] == (["FIXTURE_CREDENTIAL"] if difference == "environment" else [])
    assert records[-1]["different_config"] == (["/config/timeout"] if difference == "config" else [])
    assert records[-1]["cwd_equal"] is (difference != "cwd")
    encoded = json.dumps(records)
    for private in (key, "synthetic-sensitive-value", "different-sensitive-value",
                    expected["execution_context"]["digest"], "/fixture/caller", "/fixture/server"):
        assert private not in encoded
    assert "key_tag" not in encoded and fixture.FIELD not in encoded
    assert source == Path(execution_context.__file__).read_text(encoding="utf-8")
    for path in package.glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    with pytest.raises(AssertionError):
        fixture.instrument_payload(tmp_path, tmp_path / "events", "unit")


@pytest.mark.parametrize("failure", ["refused", "unknown", "unavailable", "other", None])
def test_warm_decision_result_exception_and_no_replay_preserved(failure):
    records = []
    diagnostic = fixture.Diagnostics(records.append)
    errors = {
        "refused": execution_context.SharingUnavailable("synthetic private detail"),
        "unknown": incoming.OutcomeUnknown("synthetic private detail"),
        "unavailable": OSError("synthetic private detail"),
        "other": RuntimeError("synthetic private detail"),
    }
    calls = []
    result = {"ok": True, "result": {"identity": "synthetic private result"}}

    async def original(*args, **kwargs):
        calls.append((args, kwargs))
        if failure is not None:
            raise errors[failure]
        return result

    wrapped = diagnostic.warm(original, execution_context.SharingUnavailable, incoming.OutcomeUnknown)
    if failure:
        with pytest.raises(type(errors[failure])) as caught:
            asyncio.run(wrapped("request", option=True))
        assert caught.value is errors[failure]
    else:
        assert asyncio.run(wrapped("request", option=True)) is result
    assert calls == [(("request",), {"option": True})]
    assert records == [{"event": {
        "refused": "warm_refused", "unknown": "warm_unknown_outcome",
        "unavailable": "warm_unavailable", "other": "warm_other_failure", None: "warm_accepted",
    }[failure]}]


def test_pool_reuse_reopen_and_custody_unchanged():
    records = []
    diagnostic = fixture.Diagnostics(records.append)

    class Pool:
        def __init__(self):
            self._entries = {}

    pool = Pool()
    calls = []

    async def original(pool, key, cfg):
        calls.append(key)
        return pool._entries.setdefault(key, object())

    async def exercise(subject):
        wrapped = diagnostic.pool(original)
        first = await wrapped(subject, "fixture", None)
        assert await wrapped(subject, "fixture", None) is first
        subject._entries.clear()
        assert await wrapped(subject, "fixture", None) is not first

    asyncio.run(exercise(pool))
    assert calls == ["fixture"] * 3
    assert records == [{"event": name} for name in ("pool_open", "pool_reuse", "pool_reopen")]
    reference = weakref.ref(pool)
    del pool
    gc.collect()
    assert reference() is None and not diagnostic.seen


def test_direct_selection_preserves_sync_async_boundary():
    records = []
    diagnostic = fixture.Diagnostics(records.append)
    result = object()

    async def original():
        return result

    assert diagnostic.attempt(lambda: None)() is None
    assert asyncio.run(diagnostic.call(original)()) is result
    assert diagnostic.attempt(lambda: 1)() == 1
    assert asyncio.run(diagnostic.call(original)()) is result
    assert diagnostic.attempt(lambda **kwargs: None)(no_serve=True) is None
    assert asyncio.run(diagnostic.call(original)()) is result
    assert records == [{"event": event} for event in (
        "direct_selected", "direct_fallback", "warm_path_returned", "direct_execution",
        "direct_selected", "direct_execution",
    )]


def test_missing_invalid_or_different_key_is_unknown_and_sink_never_serializes_maps(tmp_path):
    diagnostic = fixture.Diagnostics(fixture.file_sink(tmp_path, "unit"))
    key = secrets.token_hex(32)
    data = json.dumps({"config": {}, "environment": {"TOKEN": "synthetic-secret"}, "cwd": "/fixture"}).encode()
    diagnostic.capture(data, key)
    remote = diagnostic.snapshot.get()
    diagnostic.emit("context_accepted", **diagnostic.compare(remote))
    for candidate in (None, {}, {**remote, "key_tag": "0" * 64}):
        assert diagnostic.compare(candidate)["comparison"] == "unknown"
    diagnostic.emit("context_accepted", snapshot=remote)
    diagnostic.emit("context_accepted", different_environment=["bad\nsynthetic-secret"])
    diagnostic.capture(b"not-json", key)
    assert diagnostic.compare(remote)["comparison"] == "unknown"
    records = fixture.read_records(tmp_path)
    assert len(records) == 1
    output = json.dumps(records)
    assert key not in output and "synthetic-secret" not in output and "key_tag" not in output
    assert fixture.actor_environment({"CONTROL": "1", "control": "1", "STABLE": "same"}, "CONTROL") == {"STABLE": "same"}


def test_private_payload_uses_canonical_producer_owner(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "mcp_incoming_fixture_installer", Path(__file__).with_name("test_incoming_installer.py"),
    )
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

    original = Path(execution_context.__file__).read_bytes()
    payload, tripwire = installer.payload_copy(
        tmp_path / "copy", "old", diagnostics=tmp_path / "events",
    )
    assert not tripwire.exists()
    assert (payload / "src" / "agent_mcp" / "_fixture_diagnostics.py").is_file()
    profile = json.loads((payload / "scripts" / "uninstall-profile.json").read_text(encoding="utf-8"))
    owner = sys.modules["fixture_footprint_old"]
    assert profile["producers"] == {
        scope: owner.producer_digest(payload, scope) for scope in profile["producers"]
    }
    assert Path(execution_context.__file__).read_bytes() == original
