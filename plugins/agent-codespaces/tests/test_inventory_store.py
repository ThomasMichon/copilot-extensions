"""Pure/injected-I/O contracts; no native lock, process or runtime activation."""
from __future__ import annotations

import io
import json
import stat
import sys
import types
from dataclasses import FrozenInstanceError, asdict, fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from single_instance_lease import OperationLockedError, OperationOwnershipError

from agent_codespaces import inventory_store as store


@pytest.fixture
def limits():
    # Calibration values, not production defaults.
    return store.Limits(1024 * 1024, 1024, 4096)


def row(name="venue", state="Stopped", **changes):
    values = dict(
        name=name, state=state, observed_at=100.0, last_seen=90.0,
        producer_epoch=None, sequence=None, disconnected_at=None, error_code=None,
    )
    values.update(changes)
    return store.InventoryRow(**values)


def observation(rows=None, **changes):
    values = dict(
        account="a", source_generation="source-1", observed_at=100.0,
        complete=True, error_code=None, rows=(row(),) if rows is None else rows,
    )
    values.update(changes)
    return store.AccountObservation(**values)


def snapshot(accounts=None, **changes):
    values = dict(
        schema_version=1, source=store.SourceKey("installation", "namespace"),
        epoch="cache-1", revision=1,
        accounts=(observation(),) if accounts is None else accounts,
    )
    values.update(changes)
    return store.Snapshot(**values)


def document(value):
    return json.loads(json.dumps(asdict(value)))


@pytest.fixture
def filesystem(monkeypatch, limits):
    path = Path(__file__).parent / "virtual-parent" / "inventory.json"
    state = SimpleNamespace(
        path=path, data=None, serial=0, parent=1, missing_parent=False,
        unsafe=False, held=False, busy=False, reads=[], writes=[],
        locks=[], assertions=0, lose_at=None, assert_hook=None,
        write_hook=None, read_hook=None, exit_error=False,
    )

    def put(data):
        state.data = data
        state.serial += 1

    state.put = put

    def inspect(target):
        assert target == path
        if state.missing_parent:
            raise FileNotFoundError("synthetic parent missing")
        if state.unsafe:
            raise store.InventoryError("unsafe_parent")
        return ((state.parent,),), None if state.data is None else (state.serial, len(state.data))

    original_open = Path.open

    class Reader(io.BytesIO):
        def read(self, size=-1):
            assert size == limits.max_bytes + 1
            state.reads.append((size, state.held))
            if state.read_hook is not None:
                state.read_hook()
            return super().read(size)

    def open_file(target, mode="r", *args, **kwargs):
        if target != path:
            return original_open(target, mode, *args, **kwargs)
        assert mode == "rb"
        if state.data is None:
            raise FileNotFoundError("synthetic file missing")
        return Reader(state.data)

    class Lock:
        def __init__(self, target):
            assert target == path.with_name("inventory.json.lock")
            state.locks.append(target)

        def __enter__(self):
            if state.busy:
                raise OperationLockedError("synthetic busy")
            assert not state.held
            state.held = True
            return self

        def assert_owned(self):
            assert state.held
            state.assertions += 1
            if state.assert_hook is not None:
                state.assert_hook()
            if state.assertions == state.lose_at:
                raise OperationOwnershipError("synthetic custody lost")

        def __exit__(self, *args):
            state.held = False
            if state.exit_error:
                raise OperationOwnershipError("synthetic release uncertainty")

    def write(target, text):
        assert target == path and state.held and not state.missing_parent
        assert len(text.encode("utf-8")) <= limits.max_bytes
        state.writes.append(text)
        if state.write_hook is not None:
            state.write_hook(text)
        else:
            put(text.encode("utf-8"))

    monkeypatch.setattr(store, "_inspect_path", inspect)
    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(store, "OperationLock", Lock)
    monkeypatch.setattr(store, "atomic_write_text", write)
    return state


def read(fs, limits, source=None):
    return store.read_inventory(
        fs.path, expected_source=source or snapshot().source, limits=limits,
    )


def commit(fs, value, limits, expected=None):
    return store.commit_inventory(
        fs.path, value, expected_version=expected, limits=limits,
    )


def test_explicit_arguments_and_no_runtime_activation():
    with pytest.raises(TypeError):
        store.Limits()
    with pytest.raises(TypeError):
        store.Policy()
    assert not any(name in vars(store) for name in ("RUNTIME_DIR", "DEFAULT_LIMITS", "DEFAULT_POLICY"))


def test_fresh_module_execution_performs_no_runtime_filesystem_io(monkeypatch):
    source = Path(store.__file__).read_text(encoding="utf-8")
    module = types.ModuleType("inventory_store_import_unit")
    monkeypatch.setitem(sys.modules, module.__name__, module)

    def unexpected(*args, **kwargs):
        pytest.fail("module import attempted runtime filesystem I/O")

    monkeypatch.setattr(Path, "open", unexpected)
    monkeypatch.setattr(Path, "mkdir", unexpected)
    monkeypatch.setattr(Path, "resolve", unexpected)
    monkeypatch.setattr("builtins.open", unexpected)
    exec(compile(source, "<inventory-store-pure-import>", "exec"), module.__dict__)
    assert module.Limits(1024, 10, 100).max_rows == 10


@pytest.mark.parametrize("bad", [True, False, -1, 0, 1.0, float("inf")])
def test_limits_are_strict_positive_integers(bad):
    with pytest.raises(store.InventoryError):
        store.Limits(bad, 1, 1)


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf"), -(float("inf"))])
def test_policy_requires_finite_nonnegative_explicit_values(bad):
    with pytest.raises(store.InventoryError):
        store.Policy(bad, 60, 0)


def test_roundtrip_is_deeply_immutable_and_does_not_alias_caller_document(limits):
    original = document(snapshot())
    restored = store._from_document(original, limits)
    original["accounts"][0]["rows"][0]["name"] = "mutated"
    original["accounts"][0]["rows"].clear()
    original["source"]["namespace"] = "elsewhere"
    assert restored == snapshot()
    with pytest.raises(FrozenInstanceError):
        restored.accounts[0].rows[0].state = "Available"
    assert store._decode(store._encode_text(restored, limits).encode(), limits) == restored
    with pytest.raises(store.InventoryError, match="mutable"):
        observation(rows=[row()])
    with pytest.raises(store.InventoryError, match="mutable"):
        snapshot(accounts=[observation()])


def test_missing_valid_empty_and_source_proven_empty_are_distinct(filesystem, limits):
    fs = filesystem
    assert read(fs, limits).status == "absent"
    empty = snapshot(accounts=())
    fs.put(store._encode_text(empty, limits).encode())
    assert read(fs, limits) == store.ReadResult("valid", empty)
    proven = snapshot(accounts=(observation(rows=()),))
    fs.put(store._encode_text(proven, limits).encode())
    assert read(fs, limits).snapshot.accounts[0].complete is True
    # The model exposes no global absence/control verdict, even for this account.
    status = store.classify_status(
        proven.accounts[0], "missing", now_epoch=101,
        live_transport_epoch=None, policy=store.Policy(60, 300, 30),
    )
    assert status.label == "unknown" and status.reasons == ("not_observed",)


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 1.0), ("revision", True),
    ("revision", 0), ("revision", -1), ("revision", 1.0),
    ("revision", 1 << 63), ("epoch", ""), ("epoch", " "), ("accounts", {}),
])
def test_strict_root_types(filesystem, limits, field, value):
    fs = filesystem
    data = document(snapshot())
    data[field] = value
    fs.put(json.dumps(data).encode())
    assert read(fs, limits).status == "corrupt"


@pytest.mark.parametrize("change", [
    lambda d: d.update(credentials="forbidden"),
    lambda d: d["source"].update(extra="forbidden"),
    lambda d: d["accounts"][0].update(token="forbidden"),
    lambda d: d["accounts"][0]["rows"][0].update(digest="forbidden"),
    lambda d: d["accounts"][0].update(complete=1),
    lambda d: d["accounts"][0].update(error_code="raw diagnostic text"),
    lambda d: d["accounts"][0]["rows"][0].update(sequence=True),
    lambda d: d["accounts"][0]["rows"][0].update(observed_at=True),
    lambda d: d["accounts"][0]["rows"][0].update(last_seen=float("nan")),
    lambda d: d["accounts"][0]["rows"][0].update(disconnected_at=float("inf")),
])
def test_whitelist_and_nested_types_fail_without_payload_diagnostics(filesystem, limits, change):
    data = document(snapshot())
    change(data)
    filesystem.put(json.dumps(data).encode())
    result = read(filesystem, limits)
    assert result.status == "corrupt" and result.snapshot is None
    assert "forbidden" not in repr(result) and "diagnostic text" not in repr(result)


@pytest.mark.parametrize("raw", [
    b"", b"{", b"[]", b'{"schema_version":1,"schema_version":1}',
    b'{"source":{"installation":"a","installation":"b"}}',
    b'{"revision":123456789012345678901234567890}', b"\xff",
])
def test_invalid_and_duplicate_json_is_never_empty_success(filesystem, limits, raw):
    filesystem.put(raw)
    assert read(filesystem, limits).status == "corrupt"


def test_unknown_schema_and_wrong_source_are_not_overwritten(filesystem, limits):
    fs = filesystem
    data = document(snapshot())
    data["schema_version"] = 2
    fs.put(json.dumps(data).encode())
    assert read(fs, limits).status == "unsupported"
    assert commit(fs, snapshot(), limits).status == "refused"
    fs.put(store._encode_text(snapshot(source=store.SourceKey("other", "namespace")), limits).encode())
    before = fs.data
    assert read(fs, limits).status == "wrong_source"
    assert commit(fs, snapshot(), limits).status == "refused"
    assert fs.data == before and not fs.writes


def test_read_is_capped_before_json_parse(filesystem, limits, monkeypatch):
    fs = filesystem
    fs.put(b"x" * (limits.max_bytes + 50))
    monkeypatch.setattr(store.json, "loads", lambda *a, **k: pytest.fail("must not parse oversized bytes"))
    assert read(fs, limits).status == "too_large"
    assert fs.reads == [(limits.max_bytes + 1, False)]


def test_nonfinite_exponent_and_nested_duplicate_keys_are_refused(filesystem, limits):
    raw = store._encode_text(snapshot(), limits).encode()
    for invalid in (
        raw.replace(b'"observed_at":100.0', b'"observed_at":1e999', 1),
        raw.replace(b'"name":"venue"', b'"name":"venue","name":"other"', 1),
    ):
        filesystem.put(invalid)
        assert read(filesystem, limits).status == "corrupt"


def test_exact_total_utf8_budget_includes_structural_bytes(limits):
    value = snapshot()
    encoded = store._encode_text(value, limits)
    size = len(encoded.encode())
    assert store._encode_text(value, store.Limits(size, 1024, 4096)) == encoded
    with pytest.raises(store.InventoryLimitError, match="byte_limit"):
        store._encode_text(value, store.Limits(size - 1, 1024, 4096))


def test_independent_row_field_and_utf8_limits_and_explicit_larger_budget(limits):
    many = snapshot(accounts=(observation(rows=tuple(row(f"v{i}") for i in range(1025))),))
    with pytest.raises(store.InventoryLimitError):
        store._encode_text(many, limits)
    larger = store.Limits(4 * 1024 * 1024, 4096, 4096)
    assert store._decode(store._encode_text(many, larger).encode(), larger) == many
    exact = snapshot(accounts=(observation(rows=(row("é" * 2048),)),))
    assert store._decode(store._encode_text(exact, limits).encode(), limits) == exact
    excessive = replace(exact, accounts=(observation(rows=(row("é" * 2049),)),))
    with pytest.raises(store.InventoryLimitError):
        store._encode_text(excessive, limits)
    with pytest.raises(store.InventoryLimitError):
        store._decode(store._encode_text(many, larger).encode(), limits)


def test_row_limit_is_independent_of_account_header_count():
    budget = store.Limits(10000, 1, 4096)
    first = snapshot()
    result = store.apply_account_observation(
        first, observation(account="b", rows=()), limits=budget,
    )
    assert len(result.accounts) == 2 and sum(len(item.rows) for item in result.accounts) == 1
    assert store._decode(store._encode_text(result, budget).encode(), budget) == result


def test_serialization_stops_at_total_cap_without_materializing_document(monkeypatch):
    value = snapshot(accounts=(observation(rows=tuple(row(f"v{i}") for i in range(100))),))
    budget = store.Limits(2048, 1000000, 4096)
    original = store._tokens
    visited = []

    def counted(item):
        if type(item) is store.InventoryRow:
            visited.append(item.name)
        yield from original(item)

    monkeypatch.setattr(store, "_tokens", counted)
    with pytest.raises(store.InventoryLimitError, match="byte_limit"):
        store._encode_text(value, budget)
    assert 0 < len(visited) < 100
    # A huge field is rejected by length before JSON/string allocation.
    monkeypatch.setattr(store.json, "dumps", lambda *a, **k: pytest.fail("oversize field reached encoder"))
    huge = snapshot(accounts=(observation(rows=(row("x" * 10000),)),))
    with pytest.raises(store.InventoryLimitError):
        store._encode_text(huge, store.Limits(20, 1000000, 1000000))


@pytest.mark.parametrize("value", [
    snapshot(accounts=(observation(rows=(row(), row())),)),
    snapshot(accounts=(observation(account="a"), observation(account="A"))),
])
def test_duplicate_identities_are_refused(value, limits):
    with pytest.raises(store.InventoryError, match="duplicate"):
        store._encode_text(value, limits)
    with pytest.raises(store.InventoryError, match="duplicate"):
        store._from_document(document(value), limits)


def test_same_venue_under_different_accounts_is_not_deduplicated(limits):
    value = snapshot(accounts=(observation(account="a"), observation(account="b")))
    assert len(store._decode(store._encode_text(value, limits).encode(), limits).accounts) == 2


def test_cas_creation_increment_and_fresh_reread_under_custody(filesystem, limits):
    fs = filesystem
    first = snapshot()
    result = commit(fs, first, limits)
    assert result == store.CommitResult("committed", "verified", first.version)
    second = replace(first, revision=2)
    assert commit(fs, second, limits, first.version).status == "committed"
    assert json.loads(fs.data)["revision"] == 2
    assert all(held for _, held in fs.reads)
    assert len(fs.writes) == 2 and len(fs.locks) == 2
    before = fs.data
    assert commit(fs, second, limits, first.version).status == "conflict"
    assert fs.data == before and len(fs.writes) == 2


@pytest.mark.parametrize("change", [
    {"epoch": "replacement-epoch"},
    {"source": store.SourceKey("replacement-installation", "namespace")},
    {"source": store.SourceKey("installation", "replacement-namespace")},
])
def test_equal_revision_after_replacement_cannot_aba(filesystem, limits, change):
    fs = filesystem
    original = snapshot()
    replacement = replace(original, **change)
    fs.put(store._encode_text(replacement, limits).encode())
    before = fs.data
    assert commit(fs, replace(original, revision=2), limits, original.version).status in {"conflict", "refused"}
    assert fs.data == before and not fs.writes


@pytest.mark.parametrize("change", [
    {"revision": 1}, {"revision": 3}, {"epoch": "another-epoch"},
])
def test_candidate_increment_is_exactly_one_without_epoch_reset(filesystem, limits, change):
    fs = filesystem
    first = snapshot()
    fs.put(store._encode_text(first, limits).encode())
    before = fs.data
    assert commit(fs, replace(first, **change), limits, first.version).status == "refused"
    assert fs.data == before and not fs.writes


def test_initial_commit_cannot_claim_existing_or_noninitial_version(filesystem, limits):
    fs = filesystem
    assert commit(fs, snapshot(revision=2), limits).status == "conflict"
    fs.put(store._encode_text(snapshot(), limits).encode())
    before = fs.data
    assert commit(fs, snapshot(), limits).status == "conflict"
    assert fs.data == before and not fs.writes


def test_busy_is_nonblocking_without_write_or_retry(filesystem, limits):
    fs = filesystem
    fs.busy = True
    assert commit(fs, snapshot(), limits) == store.CommitResult("busy", "locked")
    assert len(fs.locks) == 1 and not fs.reads and not fs.writes


@pytest.mark.parametrize("condition", ["corrupt", "unsafe", "missing-parent", "oversize"])
def test_precommit_refusal_preserves_file_and_never_provisions(filesystem, limits, condition):
    fs = filesystem
    fs.put(b"corrupt" if condition == "corrupt" else store._encode_text(snapshot(), limits).encode())
    before = fs.data
    fs.unsafe = condition == "unsafe"
    fs.missing_parent = condition == "missing-parent"
    value = snapshot(revision=2)
    if condition == "oversize":
        value = replace(value, accounts=(observation(rows=(row("x" * 4097),)),))
    assert commit(fs, value, limits, snapshot().version).status == "refused"
    assert fs.data == before and not fs.writes


def test_custody_and_parent_rechecked_immediately_before_helper(filesystem, limits):
    fs = filesystem
    fs.assert_hook = lambda: setattr(fs, "parent", fs.parent + 1)
    assert commit(fs, snapshot(), limits).status == "refused"
    assert not fs.writes and fs.data is None


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_custody_loss_never_claims_success_or_rolls_back(filesystem, limits, stage):
    fs = filesystem
    fs.lose_at = stage
    result = commit(fs, snapshot(), limits)
    assert result.status == ("refused" if stage == 1 else "unknown")
    assert len(fs.writes) == (0 if stage == 1 else 1)
    if stage != 1:
        assert json.loads(fs.data)["revision"] == 1


@pytest.mark.parametrize("failure", ["before-replace", "after-replace", "third-writer", "parent-replaced", "release"])
def test_postcommit_uncertainty_never_rolls_back_another_writer(filesystem, limits, failure):
    fs = filesystem
    first = snapshot()
    fs.put(store._encode_text(first, limits).encode())
    replacement = store._encode_text(snapshot(epoch="another-owner"), limits).encode()
    before = fs.data

    def write(text):
        if failure == "before-replace":
            raise OSError("synthetic failed replacement")
        fs.put(replacement if failure == "third-writer" else text.encode())
        if failure == "after-replace":
            raise OSError("synthetic outcome unknown")
        if failure == "parent-replaced":
            fs.parent += 1

    fs.write_hook = write
    fs.exit_error = failure == "release"
    assert commit(fs, replace(first, revision=2), limits, first.version).status == "unknown"
    assert len(fs.writes) == 1
    if failure == "third-writer":
        assert fs.data == replacement
    elif failure == "before-replace":
        assert fs.data == before
    else:
        assert json.loads(fs.data)["revision"] == 2


def test_changed_read_is_unknown_not_absence(filesystem, limits):
    fs = filesystem
    fs.put(store._encode_text(snapshot(), limits).encode())
    fs.read_hook = lambda: fs.put(b"replacement")
    assert read(fs, limits).status == "unknown"


def test_unknown_commit_cannot_advance_revision_twice_on_retry(filesystem, limits):
    fs = filesystem
    first = snapshot()
    fs.put(store._encode_text(first, limits).encode())

    def uncertain(text):
        fs.put(text.encode())
        raise OSError("synthetic post-write uncertainty")

    fs.write_hook = uncertain
    second = replace(first, revision=2)
    assert commit(fs, second, limits, first.version).status == "unknown"
    fs.write_hook = None
    assert commit(fs, second, limits, first.version).status == "conflict"
    assert json.loads(fs.data)["revision"] == 2 and len(fs.writes) == 1


def test_actual_path_inspection_is_absolute_plain_and_existing(monkeypatch, limits):
    def unexpected(*args, **kwargs):
        pytest.fail("relative path performed filesystem I/O")

    monkeypatch.setattr(Path, "lstat", unexpected)
    result = store.read_inventory("relative.json", expected_source=snapshot().source, limits=limits)
    assert result.status == "unsafe_path"
    assert store.commit_inventory(
        "relative.json", snapshot(), expected_version=None, limits=limits,
    ).status == "refused"


@pytest.mark.parametrize("kind", ["plain", "missing", "parent-link", "file-link", "parent-reparse", "file-reparse"])
def test_path_guards_use_lstat_not_followed_link_facts(monkeypatch, kind):
    path = Path(__file__).parent / "virtual-parent" / "inventory.json"
    monkeypatch.setattr(Path, "resolve", lambda self, **kwargs: self)

    def info(target):
        is_file = target == path
        if kind == "missing" and not is_file:
            raise FileNotFoundError("synthetic missing parent")
        linked = (kind == "parent-link" and target == path.parent) or (kind == "file-link" and is_file)
        reparse = (kind == "parent-reparse" and target == path.parent) or (kind == "file-reparse" and is_file)
        return SimpleNamespace(
            st_mode=stat.S_IFLNK if linked else stat.S_IFREG if is_file else stat.S_IFDIR,
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT if reparse else 0,
            st_dev=1, st_ino=2, st_size=10, st_mtime_ns=1, st_ctime_ns=1,
        )

    monkeypatch.setattr(Path, "lstat", info)
    if kind == "plain":
        assert store._inspect_path(path)[1] == (1, 2, 10, 1, 1)
    elif kind == "missing":
        with pytest.raises(FileNotFoundError):
            store._inspect_path(path)
    else:
        with pytest.raises(store.InventoryError, match="unsafe"):
            store._inspect_path(path)


def test_reducer_preserves_inactive_unseen_and_immutable_inputs(limits):
    old = snapshot(accounts=(observation(rows=(row("stopped"), row("active", "Available"))),))
    partial = observation(
        rows=(row("new", "Stopped", observed_at=110),),
        observed_at=110, complete=False, error_code="partial",
    )
    result = store.apply_account_observation(old, partial, limits=limits)
    assert [item.name for item in result.accounts[0].rows] == ["stopped", "active", "new"]
    assert result.accounts[0].complete is False
    assert len(old.accounts[0].rows) == 2 and len(partial.rows) == 1
    assert result.version == old.version  # Only an explicit commit candidate advances it.
    with pytest.raises(FrozenInstanceError):
        result.accounts[0].rows[0].name = "changed"


@pytest.mark.parametrize("complete,error", [(True, None), (False, "unavailable")])
def test_empty_observation_never_deletes_unseen_or_fabricates_absence(limits, complete, error):
    value = snapshot()
    incoming = observation(rows=(), observed_at=110, complete=complete, error_code=error)
    result = store.apply_account_observation(value, incoming, limits=limits)
    assert result.accounts[0].rows == value.accounts[0].rows
    assert result.accounts[0].complete is False
    assert result.accounts[0].error_code in {"partial", "unavailable"}


def test_unknown_rows_cannot_become_successful_empty_or_replace_positive(limits):
    incoming = observation(rows=(row(state="Unknown"),), complete=True)
    empty = snapshot(accounts=())
    result = store.apply_account_observation(empty, incoming, limits=limits)
    assert result.accounts[0].rows == () and result.accounts[0].complete is False
    old = snapshot()
    result = store.apply_account_observation(old, incoming, limits=limits)
    assert result.accounts[0].rows == old.accounts[0].rows


def test_reducer_checks_generation_order_bounds_and_cross_account_identity(limits):
    old = snapshot()
    for incoming in (observation(source_generation="replacement"), observation(observed_at=99)):
        with pytest.raises(store.InventoryError, match="generation_or_order"):
            store.apply_account_observation(old, incoming, limits=limits)
    result = store.apply_account_observation(old, observation(account="b"), limits=limits)
    assert len(result.accounts) == 2 and all(item.rows[0].name == "venue" for item in result.accounts)
    with pytest.raises(store.InventoryLimitError):
        store.apply_account_observation(old, observation(rows=(row("new"),)), limits=store.Limits(10000, 1, 4096))


@pytest.mark.parametrize("age,expected", [(0, "fresh"), (300, "fresh"), (301, "stale")])
def test_inactive_policy_is_explicit_and_does_not_evict(age, expected):
    source = observation()
    result = store.classify_status(
        source, "venue", now_epoch=100 + age, live_transport_epoch=None,
        policy=store.Policy(60, 300, 30),
    )
    assert result.label == expected and source.rows[0].state == "Stopped"
    assert {field.name for field in fields(result)} == {"label", "age", "reasons", "reconcile"}


def test_status_only_restart_disconnect_grace_clock_and_source_unknown():
    source = observation(rows=(row(
        state="Available", producer_epoch="stream-1", disconnected_at=110,
    ),), observed_at=110)
    policy = store.Policy(60, 300, 30)
    classify = lambda value, now, epoch: store.classify_status(
        value, "venue", now_epoch=now, live_transport_epoch=epoch, policy=policy,
    )
    assert classify(source, 120, "stream-1").label == "fresh"
    assert classify(source, 120, None).label == "last_known"
    assert classify(source, 141, None).label == "unknown"
    restarted = replace(source, rows=(replace(source.rows[0], disconnected_at=None),))
    assert classify(restarted, 120, None).label == "unknown"
    assert classify(source, 109, "stream-1").reasons == ("clock_uncertain",)
    partial = replace(source, complete=False, error_code="limit")
    assert classify(partial, 120, "stream-1").reasons == ("source_unknown",)
    assert classify(source, 161, "stream-1").label == "stale"
