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


def test_selected_reconciliation_requires_exact_scope_and_existing_physical_seal(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, claim = api.layout, api.claim
    selected = layout.SourceDomain(Path("unused-point-domain"), point_scope=claim.scope)
    receipt = {
        **api.receipt, "state": "dirty", "uncertain_effect": True,
        "reconciliation": {"owner": "publication", "token": "retained", "physical": {}},
    }
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(selected, "receipt", lambda: dict(receipt))
    monkeypatch.setattr(layout, "_versions", lambda _db: {})

    @contextmanager
    def exclusion() -> Iterator[None]:
        yield

    def save(value: dict[str, Any]) -> None:
        saved.append(dict(value))
        receipt.clear()
        receipt.update(value)

    monkeypatch.setattr(selected, "exclusion", exclusion)
    monkeypatch.setattr(selected, "save", save)
    for suffix, fields in (
        ("logical_projection", {
            "physical_tables": lambda *args: {},
            "commit_projection": lambda *args, **kwargs: {},
        }),
        ("mutation_result", {"record_write": lambda *args: None}),
    ):
        name = f"{layout.__package__}.{suffix}"
        stub = ModuleType(name)
        for key, value in fields.items():
            setattr(stub, key, value)
        monkeypatch.setitem(sys.modules, name, stub)
    for scope, owner, token in (
        (None, "publication", "retained"),
        (("foreign", *claim.scope[1:]), "publication", "retained"),
        (claim.scope, "other-owner", "retained"),
        (claim.scope, "publication", "other-token"),
    ):
        with pytest.raises(layout.SourceLayoutError), selected.reconcile(
            object(), owner=owner, token=token, point_scope=scope,
        ):
            pytest.fail("unqualified reconciliation entered")
        assert not saved
    with selected.reconcile(
        object(), owner="publication", token="retained", point_scope=claim.scope,
    ):
        assert selected.writing
    assert not selected.writing and receipt["state"] == "clean"
    assert "reconciliation" not in receipt and "uncertain_effect" not in receipt


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


def test_point_vector_effect_requires_current_model_binding_and_nested_owned_writer(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("numpy", "pyarrow"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    name = "_promotion_point_domain.store.vector_table"
    spec = importlib.util.spec_from_file_location(name, SOURCE / "store/vector_table.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    owner = module.VectorTable.__new__(module.VectorTable)
    events: list[object] = []
    binding: dict[str, Any] = {
        "content_table": "chunks", "models": {"first": {"table_name": "vectors_first", "dim": 2}},
    }

    @contextmanager
    def writer(db: object, *, point_scope: object) -> Iterator[None]:
        assert db is owner._db and point_scope == api.claim.scope
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    domain = SimpleNamespace(
        writing=True, receipt=lambda: {"point_models": binding}, _write=writer,
    )
    owner._db = object()
    owner._source_domain = domain
    owner._model_id = "first"
    owner._content_table = "chunks"
    owner._table_name = "vectors_first"
    owner._dim = 2
    canonical, table = {"one": {"source": api.claim.canonical_family}}, object()

    def preflight(*_args: Any, **_kwargs: Any) -> Any:
        events.append("input")
        return canonical, table

    def effect(*_args: Any, **kwargs: Any) -> int:
        assert kwargs["canonical"] is canonical and kwargs["table"] is table
        events.append("effect")
        return 1

    monkeypatch.setattr(owner, "_point_upsert_input", preflight)
    monkeypatch.setattr(owner, "_write_vectors", effect)
    for writing, scope in ((False, api.claim.scope), (True, None)):
        domain.writing = writing
        with pytest.raises(api.layout.SourceLayoutError, match="owning source writer"):
            owner.upsert_point(["one"], object(), point_scope=scope)
        assert events == []
    domain.writing = True
    binding["models"]["first"]["table_name"] = "vectors_other"
    with pytest.raises(api.layout.SourceLayoutError, match="model binding"):
        owner.upsert_point(["one"], object(), point_scope=api.claim.scope)
    assert events == ["enter", "exit"]
    events.clear()
    binding["models"]["first"]["table_name"] = "vectors_first"
    assert owner.upsert_point(["one"], object(), point_scope=api.claim.scope) == 1
    assert events == ["enter", "input", "effect", "exit"]
    events.clear()
    binding["models"].clear()
    with pytest.raises(api.layout.SourceLayoutError, match="model binding"):
        owner.upsert_point([], object(), point_scope=api.claim.scope)
    assert events == ["enter", "exit"]


def test_point_content_preflights_every_model_before_native_effects(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    package = "_promotion_point_domain.store"
    chunk = SimpleNamespace(chunk_id="one", source=api.claim.canonical_family, language="python")
    row = {"chunk_id": chunk.chunk_id, "source": chunk.source, "language": chunk.language}

    def indexed(owner: Any, _ids: list[str], **_kwargs: Any) -> list[dict[str, Any]]:
        events.append("read:" + owner._table_name)
        return [{**row, "language": "markdown"}]

    def projection(*_args: Any, **kwargs: Any) -> None:
        assert kwargs["tables"] == [first._table, second._table]
        events.append("projection")

    for suffix, attributes in (
        ("chunk_id_index", {"indexed_chunk_rows": indexed}),
        ("content_mutations", {"coalesce_chunks": lambda chunks: chunks, "content_record": lambda _chunk: row}),
        ("repo_filter", {"_sql_str": lambda value: value}),
        ("source_projection", {"sync_content_projection": projection}),
    ):
        stub = ModuleType(package + "." + suffix)
        for key, value in attributes.items():
            setattr(stub, key, value)
        monkeypatch.setitem(sys.modules, stub.__name__, stub)
    modules = []
    for suffix in ("scalar_index", "point_content"):
        name = package + "." + suffix
        spec = importlib.util.spec_from_file_location(name, SOURCE / f"store/{suffix}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    scalar, module = modules
    table = SimpleNamespace(
        delete=lambda _predicate: events.append("delete"),
        add=lambda _records: events.append("add"),
    )
    canonical = SimpleNamespace(_table_name="chunks", _table=table)
    first = SimpleNamespace(_table_name="first", _table=SimpleNamespace(name="first"))
    second = SimpleNamespace(_table_name="second", _table=SimpleNamespace(name="second"))

    @contextmanager
    def writer(_db: object, *, point_scope: object) -> Iterator[None]:
        assert point_scope == api.claim.scope
        events.append("writer")
        yield
        events.append("exit")

    @contextmanager
    def mutation(*_args: Any) -> Iterator[None]:
        events.append("effect")
        yield

    domain = SimpleNamespace(writing=True, _write=writer, bucket_mutation=mutation)
    content = SimpleNamespace(_source_domain=domain, db=object(), _table_name="chunks")
    monkeypatch.setattr(module, "_prepared_tables", lambda *_args, **_kwargs: (canonical, [first, second]))
    exhausted = True

    def inspect(native: object, *, column: str) -> Any:
        return scalar.ScalarIndexState(
            1, True, 0, 32 if exhausted and native is second._table else 1,
        ), None

    monkeypatch.setattr(module, "inspect_index", inspect)
    with pytest.raises(scalar.ScalarIndexPreparationRequired, match="reserved"):
        module.upsert_point_content(content, [chunk], point_scope=api.claim.scope)
    assert events == ["writer", "read:chunks", "read:first", "read:second"]
    events.clear()
    exhausted = False
    assert module.upsert_point_content(content, [chunk], point_scope=api.claim.scope) == 1
    assert events == [
        "writer", "read:chunks", "read:first", "read:second",
        "effect", "delete", "add", "projection", "exit",
    ]


def test_point_content_inventory_reuses_ledger_qualification_without_adopting_other_tables(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = "_promotion_point_domain.store"
    events: list[str] = []
    schema = SimpleNamespace(equals=lambda _other: True)
    names = {"chunks", "vectors"}
    receipt: dict[str, Any] = {
        "versions": dict.fromkeys(names, 1),
        "point_models": {
            "content_table": "chunks", "models": {"first": {"table_name": "vectors", "dim": 2}},
        },
    }

    def qualify(_domain: object, _db: object) -> None:
        events.append("ledger")
        if "component_ordering_evidence" not in names or receipt.get("component_ordering") != "bound":
            raise api.layout.SourceLayoutError("unqualified ledger")

    def open_table(name: str) -> SimpleNamespace:
        events.append("open:" + name)
        return SimpleNamespace(schema=schema)

    db = SimpleNamespace(uri=Path("unused-point-domain"), list_tables=lambda: SimpleNamespace(tables=names),
                         open_table=open_table)
    domain = SimpleNamespace(path=db.uri, receipt=lambda: receipt, _local=SimpleNamespace(db=db))
    content = SimpleNamespace(_source_domain=domain, _table_name="chunks", db=db)
    for suffix, attributes in (
        ("chunk_id_index", {"indexed_chunk_rows": None}),
        ("content_mutations", {"coalesce_chunks": None, "content_record": None}),
        ("repo_filter", {"_sql_str": None}),
        ("scalar_index", {"MAX_SEGMENTS": 32, "ScalarIndexPreparationRequired": ValueError,
                          "ScalarIndexState": object, "inspect_index": None, "require_budget": None}),
        ("source_projection", {"sync_content_projection": None}),
        ("component_ledger", {"BINDING": "component_ordering", "TABLE": "component_ordering_evidence",
                              "qualified_component_table": qualify}),
        ("content_store", {"_CONTENT_SCHEMA": schema, "ContentStore": lambda *_args, **_kwargs: SimpleNamespace()}),
        ("vector_table", {"VectorTable": lambda _db, **kwargs: SimpleNamespace(
            table_name=kwargs["table_name"], _schema=schema,
        )}),
    ):
        stub = ModuleType(package + "." + suffix)
        for key, value in attributes.items():
            setattr(stub, key, value)
        monkeypatch.setitem(sys.modules, stub.__name__, stub)
    spec = importlib.util.spec_from_file_location(package + ".point_content", SOURCE / "store/point_content.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._prepared_tables(content, point_scope=api.claim.scope)
    assert events == ["open:chunks", "open:vectors"]
    events.clear()
    names.add("component_ordering_evidence")
    receipt["versions"] = dict.fromkeys(names, 1)
    with pytest.raises(api.layout.SourceLayoutError, match="unqualified ledger"):
        module._prepared_tables(content, point_scope=api.claim.scope)
    assert events == ["ledger"]
    events.clear()
    receipt["component_ordering"] = "bound"
    module._prepared_tables(content, point_scope=api.claim.scope)
    assert events == ["ledger", "open:chunks", "open:vectors"]
    for obstruction in ("unknown-table", "missing-inventory", "missing-data"):
        events.clear()
        with monkeypatch.context() as patch:
            if obstruction == "unknown-table":
                patch.setattr(db, "list_tables", lambda: SimpleNamespace(tables=names | {"unknown"}))
                patch.setitem(receipt, "versions", dict.fromkeys(names | {"unknown"}, 1))
            elif obstruction == "missing-inventory":
                patch.setitem(receipt, "versions", {"chunks": 1, "vectors": 1})
            else:
                patch.setattr(db, "list_tables", lambda: SimpleNamespace(tables=names - {"vectors"}))
                patch.setitem(receipt, "versions", dict.fromkeys(names - {"vectors"}, 1))
            with pytest.raises(api.layout.SourceLayoutError, match="prepared bound tables"):
                module._prepared_tables(content, point_scope=api.claim.scope)
        assert events == []


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
