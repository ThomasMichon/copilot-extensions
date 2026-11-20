"""Pure receipt/refusal controls; directory and native-store proofs stay opt-in."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-index/src/agent_index"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    package = "_promotion_point_domain"
    for suffix in ("", ".indexing", ".store"):
        parent = ModuleType(package + suffix)
        parent.__path__ = []
        monkeypatch.setitem(sys.modules, parent.__name__, parent)
    modules = []
    for relative in ("indexing/change_hints", "indexing/point_hints", "store/source_layout"):
        name = f"{package}.{relative.replace('/', '.')}"
        spec = importlib.util.spec_from_file_location(name, SOURCE / f"{relative}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    hints, layout = modules[1:]
    claim = hints.PointChangeHints(
        source="sample:components", canonical_family="sample:points", account_id="account-a",
        binding=hints.PointBinding("https://example.test/project", "union-a", "context-a"),
        targets=(hints.WorkItemRevisionTarget("42", 7),),
    )
    receipt = {
        "layout": layout.LAYOUT, "protocol": 2, "state": "clean", "domain": "fixture",
        "versions": {}, "point_scope": {
            "source": claim.source, "canonical_family": claim.canonical_family,
            "account_id": claim.account_id, "binding": claim.binding.to_dict(),
        },
    }
    return SimpleNamespace(layout=layout, claim=claim, receipt=receipt)


def test_point_receipt_requires_complete_exact_scope_and_new_protocol(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, claim = api.layout, api.claim
    selected = layout.SourceDomain(Path("unused-point-domain"), point_scope=claim.scope)
    raw = json.dumps(api.receipt).encode()
    monkeypatch.setattr(layout, "_open_receipt", lambda _path: io.BytesIO(raw))
    assert selected.receipt() == api.receipt
    with pytest.raises(layout.SourceLayoutError, match="incompatible"):
        layout.SourceDomain(selected.path).receipt()
    for name in ("source", "canonical_family", "account_id"):
        changed = json.loads(json.dumps(api.receipt))
        changed["point_scope"][name] += "-other"
        raw = json.dumps(changed).encode()
        with pytest.raises(layout.SourceLayoutError, match="scope"):
            selected.receipt()
    for name, value in (
        ("endpoint", "https://other.test/project"), ("query_scope", "union-b"),
        ("query_context", "context-b"), ("representation", "combined"),
        ("representation_version", True), ("representation_version", 1.0),
    ):
        changed = json.loads(json.dumps(api.receipt))
        changed["point_scope"]["binding"][name] = value
        raw = json.dumps(changed).encode()
        with pytest.raises(layout.SourceLayoutError, match="scope"):
            selected.receipt()
    for changed_scope in (None, {}, {**api.receipt["point_scope"], "extra": "ignored"}):
        raw = json.dumps({**api.receipt, "point_scope": changed_scope}).encode()
        with pytest.raises(layout.SourceLayoutError, match="scope"):
            selected.receipt()
    for protocol in (1, True, 2.0, 3):
        raw = json.dumps({**api.receipt, "protocol": protocol}).encode()
        with pytest.raises(layout.SourceLayoutError, match="incompatible"):
            selected.receipt()
    for fields in ('"protocol": 1, "protocol": 2', '"protocol": 2, "protocol": 1'):
        raw = json.dumps(api.receipt).replace('"protocol": 2', fields).encode()
        for reader in (selected, layout.SourceDomain(selected.path)):
            with pytest.raises(layout.SourceLayoutError, match="duplicate"):
                reader.receipt()
    raw = json.dumps({**api.receipt, "protocol": 1, "future_metadata": {"kept": True}}).encode()
    assert layout.SourceDomain(selected.path).receipt()["future_metadata"] == {"kept": True}


def test_point_selection_and_receipt_save_never_admit_legacy_operations(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, claim = api.layout, api.claim
    selected = layout.SourceDomain(Path("unused-point-domain"), point_scope=claim.scope)
    for scope in ((), (*claim.scope[:3], {}), ("all", *claim.scope[1:]), ("", *claim.scope[1:])):
        with pytest.raises(layout.SourceLayoutError, match="exact typed"):
            layout.SourceDomain(selected.path, point_scope=scope)
    for operation in (selected.read, selected.operation, selected.write):
        with pytest.raises(layout.SourceLayoutError, match="not admitted"), operation(object()):
            pytest.fail("unqualified point operation entered")
    legacy = layout.SourceDomain(Path("unused-legacy-domain"))
    for operation in (legacy.read, legacy.operation):
        with pytest.raises(layout.SourceLayoutError, match="scope"), operation(
            object(), point_scope=claim.scope,
        ):
            pytest.fail("point selection entered a legacy domain")
    for changed in (
        {**api.receipt, "protocol": 1},
        {**api.receipt, "point_scope": {**api.receipt["point_scope"], "account_id": "other"}},
    ):
        with pytest.raises(layout.SourceLayoutError):
            selected.save(changed)
    raw = json.dumps(api.receipt).encode()
    monkeypatch.setattr(layout, "_open_receipt", lambda _path: io.BytesIO(raw))
    monkeypatch.setattr(layout, "_versions", lambda _db: {})
    guards: list[str] = []

    @contextmanager
    def exclusion() -> Iterator[None]:
        guards.append("enter")
        try:
            yield
        finally:
            guards.append("exit")

    monkeypatch.setattr(selected, "exclusion", exclusion)
    for operation in (selected.operation, selected.read):
        with operation(object(), point_scope=claim.scope):
            for default in (selected.operation, selected.read, selected.write):
                with pytest.raises(layout.SourceLayoutError, match="not admitted"), default(object()):
                    pytest.fail("explicit selection leaked to a default operation")
            with pytest.raises(layout.SourceLayoutError, match="scope"), operation(
                object(), point_scope=("other", *claim.scope[1:]),
            ):
                pytest.fail("scope widened inside selected exclusion")
            raw = json.dumps({**api.receipt, "state": "dirty"}).encode()
        with pytest.raises(layout.SourceLayoutError, match="incomplete"), operation(
            object(), point_scope=claim.scope,
        ):
            pytest.fail("dirty point projection returned")
        raw = json.dumps(api.receipt).encode()
    assert guards.count("enter") == guards.count("exit") == 4


def test_point_model_binding_shape_refuses_ambiguous_table_ownership(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = api.layout
    selected = layout.SourceDomain(Path("unused-point-domain"), point_scope=api.claim.scope)
    binding: dict[str, Any] = {
        "protocol": 1, "content_table": "chunks",
        "models": {"first": {"table_name": "vectors_first", "dim": 2}},
    }
    raw = json.dumps({**api.receipt, "point_models": binding}).encode()
    monkeypatch.setattr(layout, "_open_receipt", lambda _path: io.BytesIO(raw))
    assert selected.receipt()["point_models"] == binding
    for changed in (
        None, {}, {**binding, "protocol": True}, {**binding, "protocol": 2},
        {**binding, "extra": "ignored"}, {**binding, "content_table": ""},
        {**binding, "models": {"first": {"table_name": "chunks", "dim": 2}}},
        {**binding, "models": {**binding["models"], "alias": binding["models"]["first"]}},
        *({**binding, "models": {"first": {"table_name": "vectors_first", "dim": dim}}}
          for dim in (0, -1, True, 2.0, "2")),
    ):
        raw = json.dumps({**api.receipt, "point_models": changed}).encode()
        with pytest.raises(layout.SourceLayoutError, match="invalid point model"):
            selected.receipt()


def test_point_model_binding_save_rechecks_retained_ownership_before_effects(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = api.layout
    selected = layout.SourceDomain(Path("unused-point-domain"), point_scope=api.claim.scope)
    selected._local.exclusion_owned = True
    binding: dict[str, Any] = {
        "protocol": 1, "content_table": "chunks",
        "models": {"first": {"table_name": "vectors_first", "dim": 2}},
    }
    unbound = dict(api.receipt)
    retained = {**api.receipt, "point_models": binding}
    raw = json.dumps(retained).encode()
    monkeypatch.setattr(layout, "_open_receipt", lambda _path: io.BytesIO(raw))
    monkeypatch.setattr(Path, "exists", lambda path: path == selected.path / layout.MARKER)
    for value in (unbound, {**retained, "point_models": {**binding, "models": {}}}):
        with pytest.raises(layout.SourceLayoutError, match="changed or removed"):
            selected.save(value)
    raw = json.dumps(unbound).encode()
    with pytest.raises(layout.SourceLayoutError, match="empty owning writer"):
        selected.save(retained)
    selected._local.depth = 1
    selected._local.db = object()
    monkeypatch.setattr(layout, "_versions", lambda _db: {"native": 1})
    with pytest.raises(layout.SourceLayoutError, match="empty owning writer"):
        selected.save(retained)


def test_nested_default_reads_recheck_receipt_before_returning_legacy_results(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = api.layout
    domain = layout.SourceDomain(Path("unused-default-domain"))
    raw = json.dumps({**api.receipt, "protocol": 1}).encode()
    monkeypatch.setattr(layout, "_open_receipt", lambda _path: io.BytesIO(raw))
    monkeypatch.setattr(layout, "_versions", lambda _db: {})
    calls: list[str] = []

    class Reader:
        _source_domain = domain
        _db: Any = object()

        def direct(self) -> list[str]:
            calls.append("direct")
            return ["legacy"]

        def lazy(self) -> Iterator[str]:
            calls.append("lazy")
            yield "legacy"

    reader = Reader()
    read_direct = layout.source_read(Reader.direct)
    read_lazy = layout.source_read(Reader.lazy)
    for flag in ("depth", "reading", "operation"):
        setattr(domain._local, flag, True)
        try:
            with domain.read(object()):
                assert read_direct(reader) == ["legacy"]
                assert list(read_lazy(reader)) == ["legacy"]
                raw = json.dumps(api.receipt).encode()
                before = list(calls)
                with pytest.raises(layout.SourceLayoutError):
                    read_direct(reader)
                with pytest.raises(layout.SourceLayoutError):
                    list(read_lazy(reader))
                assert calls == before
                raw = json.dumps({**api.receipt, "protocol": 1}).encode()
        finally:
            setattr(domain._local, flag, False)


def test_unmarked_handle_refuses_later_selection_before_read_or_write(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marked = False
    checked: list[str] = []

    class FixturePath:
        def __init__(self, value: str) -> None:
            checked.append(value)

        def __truediv__(self, _name: str) -> FixturePath:
            return self

        def exists(self) -> bool:
            return marked

    monkeypatch.setattr(api.layout, "Path", FixturePath)
    calls: list[str] = []
    reader = SimpleNamespace(
        _source_domain=None, _db_path="relative/default-domain",
        _content=SimpleNamespace(_db=SimpleNamespace(uri="/original/default-domain")),
    )

    def access(_self: Any) -> list[str]:
        calls.append("legacy")
        return ["legacy"]

    for decorator in (api.layout.source_read, api.layout.source_write):
        guarded = decorator(access)
        assert guarded(reader) == ["legacy"]
        marked = True
        before = list(calls)
        with pytest.raises(api.layout.SourceLayoutError, match="newly selected"):
            guarded(reader)
        assert calls == before
        assert checked[-1] == "/original/default-domain"
        marked = False
