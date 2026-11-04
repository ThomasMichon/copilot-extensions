"""Real lineage transactions and legacy custody; no generation-installation claim."""

import copy
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from fleet_manager import client_admission as admission, client_generations as generations
from fleet_manager.planning import PlanError, canonical
from fleet_manager.private_state import private_directory
from test_artifact_staging import primitive as primitive  # noqa: F401 - pytest fixture
from test_client_admission import (
    database_bytes, identity, independently_signed_variants as independently_signed_variants,
    retained, root_at, selection,
)
from test_client_construction import target
from test_client_distribution import FLAGS
from test_release_auth import artifact_fixture

REPOSITORY = Path(__file__).resolve().parents[2]
FIRST = "1" * 32
SECOND = "2" * 32


def lineage_at(tmp_path):
    root = tmp_path / "private-lineage"
    private_directory(root)
    return root


def state(root, *, fences=False):
    with admission._root(root) as (path, database_root, unchanged):
        with admission._connection(path, database_root=database_root, readonly=True) as connection:
            connection.execute("BEGIN")
            result = generations._read(connection, identity(), generations._location(path))
            unchanged()
            return result if fences else result[:3]


def reserve(root, fixture, staged, trusted, **kwargs):
    return generations._reserve_generation(
        root, identity(), kwargs.pop("selection", selection(fixture)), staged,
        generation=kwargs.pop("generation", FIRST), request=kwargs.pop("request", "forward-1"),
        current_policy=kwargs.pop("current_policy", lambda: trusted),
        current_target=kwargs.pop("current_target", lambda: target(fixture)),
        clock=kwargs.pop("clock", lambda: 150), **kwargs,
    )


def test_first_touch_and_exact_generation_retry_do_not_install_or_reset_legacy_protocol(
    tmp_path, primitive,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    assert generations._initialize_lineage(root, identity())["mutations_performed"]
    empty = database_bytes(root)
    assert not generations._initialize_lineage(root, identity())["mutations_performed"]
    assert database_bytes(root) == empty
    with retained(fixture, tmp_path) as (staged, trusted):
        first = reserve(root, fixture, staged, trusted)
        before = database_bytes(root)
        assert reserve(root, fixture, staged, trusted, clock=lambda: 151) == first
        assert database_bytes(root) == before
        second = reserve(root, fixture, staged, trusted, generation=SECOND, request="forward-2")
    admitted, origin, reservations = state(root)
    assert admitted["selection"] == selection(fixture)
    assert origin["legacy"] is None and reservations == {FIRST: first, SECOND: second}
    assert set(path.name for path in root.iterdir()) == {
        admission.DATABASE, ".construction-leases",
    }
    with pytest.raises(PlanError, match="unsupported"):
        admission.initialize_client_admission(root, identity())
    with pytest.raises(PlanError, match="unsupported"):
        admission.inspect_client_admission(root, identity())
    assert state(root)[2] == reservations


def test_new_request_fences_shared_publication_but_not_independent_same_release_preparation(
    tmp_path, primitive,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    assert state(root, fences=True)[3] == {
        "current_generation": None, "selected_generation": None, "retired": {},
    }
    with retained(fixture, tmp_path) as (staged, trusted):
        first = reserve(root, fixture, staged, trusted)
        second = reserve(root, fixture, staged, trusted, generation=SECOND, request="forward-2")
        admitted, _, reservations, fences = state(root, fences=True)
        assert fences == {
            "current_generation": SECOND, "selected_generation": None, "retired": {},
        }
        generations._current(admitted, reservations, second, fences, shared=True)
        with pytest.raises(PlanError, match="current shared publication"):
            generations._current(admitted, reservations, first, fences, shared=True)
        before = database_bytes(root)
        assert reserve(root, fixture, staged, trusted) == first
        assert database_bytes(root) == before
        old_root, proof, binding = generations._generation_root(root, identity(), first)
        with generations._generation_custody(
            root, old_root, proof, binding, identity(), first, staged,
            lambda: trusted, lambda: target(fixture), lambda: 150,
        ) as (_, _, checkpoint):
            checkpoint()
    assert state(root, fences=True)[3] == fences


def test_retirement_is_exact_durable_non_destructive_and_does_not_reset_forward_authority(
    tmp_path, primitive,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        first = reserve(root, fixture, staged, trusted)
        old_root, proof, binding = generations._generation_root(root, identity(), first)
        sentinel = old_root / "user-config"
        sentinel.write_bytes(b"preserved configuration\n")
        with generations._generation_custody(
            root, old_root, proof, binding, identity(), first, staged,
            lambda: trusted, lambda: target(fixture), lambda: 150,
        ) as (_, _, checkpoint):
            admitted = state(root)[0]
            retired = generations._retire_generation(root, identity(), first, clock=lambda: 160)
            before = database_bytes(root)
            assert generations._retire_generation(
                root, identity(), first, clock=lambda: 170,
            ) == retired
            assert database_bytes(root) == before
            for operation in (
                checkpoint, lambda: reserve(root, fixture, staged, trusted),
                lambda: generations._generation_root(root, identity(), first),
            ):
                with pytest.raises(PlanError, match="retired"):
                    operation()
                assert database_bytes(root) == before
            second = reserve(root, fixture, staged, trusted, generation=SECOND, request="forward-2")
            generations._generation_root(root, identity(), second)
        assert state(root)[0] == admitted
    assert sentinel.read_bytes() == b"preserved configuration\n"
    assert generations.client_construction._read(old_root / generations._OWNER) == binding
    assert state(root, fences=True)[3] == {
        "current_generation": SECOND, "selected_generation": None, "retired": {FIRST: retired},
    }


@pytest.mark.parametrize("after", [False, True])
def test_retirement_uncertain_commit_rereads_the_exact_tombstone_without_reopening(
    tmp_path, primitive, monkeypatch, after,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        first = reserve(root, fixture, staged, trusted)
    commit = admission._commit

    def interrupted(connection):
        if after:
            commit(connection)
        raise sqlite3.OperationalError("synthetic unknown retirement outcome")

    with monkeypatch.context() as patch:
        patch.setattr(admission, "_commit", interrupted)
        with pytest.raises(PlanError):
            generations._retire_generation(root, identity(), first, clock=lambda: 160)
    assert (FIRST in state(root, fences=True)[3]["retired"]) is after
    retired = generations._retire_generation(root, identity(), first, clock=lambda: 170)
    assert retired["retired_at"] == (160 if after else 170)
    assert state(root, fences=True)[3]["current_generation"] == FIRST


def test_retirement_refuses_changed_request_wrong_installation_and_selected_owner(
    tmp_path, primitive,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        first = reserve(root, fixture, staged, trusted)
    generations._generation_root(root, identity(), first)
    before = database_bytes(root)
    for owner, reservation in (
        (identity(), {**first, "request": "different-request"}),
        ({**identity(), "receipt": "different-installation"}, first),
    ):
        with pytest.raises(PlanError):
            generations._retire_generation(root, owner, reservation)
        assert database_bytes(root) == before
    # A state-only fixture exercises the refusal; it is not a publication receipt.
    with sqlite3.connect(root / admission.DATABASE) as connection:
        publication = {
            "schema": "fleet-manager.client-generation-frontdoor", "version": 1,
            "generation": FIRST, "request_digest": generations.digest(first),
            "publication": FIRST, "previous": None, "directory": generations._location(tmp_path),
            "target_digest": first["target_digest"],
        }
        connection.execute(
            "UPDATE lineage SET selected_generation = ?, selected_publication = ?, publication = ?",
            (FIRST, FIRST, canonical(publication).decode("utf-8")),
        )
    before = database_bytes(root)
    with pytest.raises(PlanError, match="owned shared withdrawal"):
        generations._retire_generation(root, identity(), first)
    assert database_bytes(root) == before
    with retained(fixture, tmp_path) as (staged, trusted):
        reserve(root, fixture, staged, trusted, generation=SECOND, request="forward-2")
    assert state(root, fences=True)[3]["selected_generation"] == FIRST


@pytest.mark.parametrize("change", [
    "request", "directory", "cycle", "unknown-generation", "selected-attempt", "selected-generation",
])
def test_malformed_publication_binding_never_authorizes_generation_retry(
    tmp_path, primitive, change,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        first = reserve(root, fixture, staged, trusted)
        second = reserve(root, fixture, staged, trusted, generation=SECOND, request="forward-2")
        generations._generation_root(root, identity(), first)
        generations._generation_root(root, identity(), second)
        value = {
            "schema": "fleet-manager.client-generation-frontdoor", "version": 1,
            "generation": FIRST, "request_digest": generations.digest(first),
            "publication": FIRST, "previous": None, "directory": generations._location(tmp_path),
            "target_digest": first["target_digest"],
        }
        selected, selected_publication = None, None
        if change == "request":
            value["request_digest"] = "0" * 64
        elif change == "directory":
            value["directory"]["path"] = ".."
        elif change == "cycle":
            value["previous"] = FIRST
        elif change == "unknown-generation":
            value["generation"] = "3" * 32
        else:
            selected = FIRST if change == "selected-attempt" else SECOND
            selected_publication = SECOND if change == "selected-attempt" else FIRST
        with sqlite3.connect(root / admission.DATABASE) as connection:
            connection.execute(
                "UPDATE lineage SET publication = ?, selected_generation = ?, selected_publication = ?",
                (canonical(value).decode("utf-8"), selected, selected_publication),
            )
        before = database_bytes(root)
        with pytest.raises(PlanError):
            reserve(root, fixture, staged, trusted, generation=SECOND, request="forward-2")
        assert database_bytes(root) == before


@pytest.mark.parametrize("change", [
    "missing-current", "unknown-current", "unknown-selected", "rootless-selected",
    "retired-selected", "wrong-generation", "wrong-request", "invalid-time", "extra-field",
])
def test_malformed_generation_fences_never_authorize_retry_or_repair(
    tmp_path, primitive, change,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        first = reserve(root, fixture, staged, trusted)
        with sqlite3.connect(root / admission.DATABASE) as connection:
            if change in {"missing-current", "unknown-current"}:
                connection.execute("UPDATE lineage SET current_generation = ?", (
                    None if change == "missing-current" else SECOND,
                ))
            elif change in {"unknown-selected", "rootless-selected"}:
                connection.execute("UPDATE lineage SET selected_generation = ?", (
                    SECOND if change == "unknown-selected" else FIRST,
                ))
            else:
                value = {
                    "schema": "fleet-manager.client-generation-retirement", "version": 1,
                    "generation": FIRST, "request_digest": generations.digest(first), "retired_at": 160,
                }
                if change == "retired-selected":
                    generations._generation_root(root, identity(), first)
                    connection.execute("UPDATE lineage SET selected_generation = ?", (FIRST,))
                elif change == "wrong-generation":
                    value["generation"] = SECOND
                elif change == "wrong-request":
                    value["request_digest"] = "0" * 64
                elif change == "invalid-time":
                    value["retired_at"] = True
                else:
                    value["extra"] = "unrecognized"
                connection.execute("UPDATE generations SET retired = ?", (
                    canonical(value).decode("utf-8"),
                ))
        before = database_bytes(root)
        with pytest.raises(PlanError):
            reserve(root, fixture, staged, trusted)
        with pytest.raises(PlanError):
            generations._retire_generation(root, identity(), first)
        assert database_bytes(root) == before


@pytest.mark.parametrize("change", ["request", "generation", "selection", "target"])
def test_retry_never_rebinds_a_generation_or_allocates_another_for_the_same_request(
    tmp_path, primitive, change,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        reserve(root, fixture, staged, trusted)
        before = database_bytes(root)
        changed = {
            "request": {"request": "another-request"},
            "generation": {"generation": SECOND},
            "selection": {"selection": {**selection(fixture), "revision": 2}},
            "target": {"current_target": lambda: {**target(fixture), "revision": 2}},
        }[change]
        with pytest.raises(PlanError):
            reserve(root, fixture, staged, trusted, **changed)
    assert database_bytes(root) == before and set(state(root)[2]) == {FIRST}


def test_legacy_import_preserves_exact_floor_and_survives_predecessor_disappearance(
    tmp_path, primitive,
):
    legacy = root_at(tmp_path)
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(legacy, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        admitted = admission.admit_client_release(
            legacy, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
        )["admitted"]
    before = database_bytes(legacy)
    report = generations._initialize_lineage(root, identity(), legacy_root=legacy)
    assert report["admitted"] == admitted and database_bytes(legacy) == before
    current, origin, _ = state(root)
    assert current == origin["legacy"]["admitted"] == admitted
    assert origin["legacy"]["database"] == list(
        generations.artifact_staging._identity((legacy / admission.DATABASE).stat()),
    )
    legacy.rename(tmp_path / "retained-old-root")
    assert not generations._initialize_lineage(
        root, identity(), legacy_root=legacy,
    )["mutations_performed"]
    with retained(fixture, tmp_path) as (staged, trusted):
        reserve(root, fixture, staged, trusted)
    assert state(root)[0] == admitted
    with pytest.raises(PlanError, match="different legacy origin"):
        generations._initialize_lineage(root, identity())


def test_empty_legacy_journal_is_valid_but_missing_or_wrong_identity_never_becomes_fresh(
    tmp_path, primitive,
):
    legacy = root_at(tmp_path)
    root = lineage_at(tmp_path)
    with pytest.raises(PlanError):
        generations._initialize_lineage(root, identity(), legacy_root=legacy)
    assert list(root.iterdir()) == []
    admission.initialize_client_admission(legacy, {**identity(), "receipt": "another-owner"})
    with pytest.raises(PlanError, match="identity mismatch"):
        generations._initialize_lineage(root, identity(), legacy_root=legacy)
    assert list(root.iterdir()) == []
    fresh = tmp_path / "empty-legacy"
    fresh.mkdir(mode=0o700)
    admission.initialize_client_admission(fresh, identity())
    generations._initialize_lineage(root, identity(), legacy_root=fresh)
    assert state(root)[0] is None and state(root)[1]["legacy"]["admitted"] is None


@pytest.mark.parametrize("change", ["copy", "missing-origin", "bad-origin", "lost-floor"])
def test_copied_or_invalid_lineage_refuses_without_reimport_or_mutation(
    tmp_path, primitive, change,
):
    legacy = root_at(tmp_path)
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(legacy, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        admission.admit_client_release(
            legacy, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
        )
    generations._initialize_lineage(root, identity(), legacy_root=legacy)
    if change == "copy":
        duplicate = tmp_path / "copied-lineage"
        shutil.copytree(root, duplicate)
        duplicate.chmod(0o700)
        root = duplicate
    else:
        with sqlite3.connect(root / admission.DATABASE) as connection:
            if change == "missing-origin":
                connection.execute("DELETE FROM lineage")
            elif change == "bad-origin":
                origin = state(root)[1]
                origin["legacy"]["admitted"] = {}
                connection.execute("UPDATE lineage SET origin = ?", (
                    canonical(origin).decode("utf-8"),
                ))
            else:
                connection.execute("DELETE FROM admission")
    before = database_bytes(root)
    with pytest.raises(PlanError):
        generations._initialize_lineage(root, identity(), legacy_root=legacy)
    assert database_bytes(root) == before


@pytest.mark.parametrize("boundary", ["before-commit", "after-commit", "after-publication"])
def test_initialization_interruption_never_publishes_origin_without_imported_floor(
    tmp_path, primitive, monkeypatch, boundary,
):
    legacy = root_at(tmp_path)
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(legacy, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        admitted = admission.admit_client_release(
            legacy, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
        )["admitted"]
    commit, sync = admission._commit, admission._sync_directory

    def interrupted_commit(connection):
        if boundary == "after-commit":
            commit(connection)
        raise sqlite3.OperationalError("synthetic interruption")

    def interrupted_sync(path):
        if (root / admission.DATABASE).exists():
            raise OSError("synthetic publication acknowledgement interruption")
        sync(path)

    with monkeypatch.context() as patch:
        if boundary == "after-publication":
            patch.setattr(admission, "_sync_directory", interrupted_sync)
        else:
            patch.setattr(admission, "_commit", interrupted_commit)
        with pytest.raises(PlanError):
            generations._initialize_lineage(root, identity(), legacy_root=legacy)
    assert (root / admission.DATABASE).exists() is (boundary == "after-publication")
    result = generations._initialize_lineage(root, identity(), legacy_root=legacy)
    assert result["admitted"] == admitted
    assert state(root)[0] == state(root)[1]["legacy"]["admitted"] == admitted


def test_higher_admission_keeps_history_and_refuses_lower_or_conflicting_new_requests(
    tmp_path, primitive, independently_signed_variants,
):
    lower, first, higher, conflict = independently_signed_variants
    root = lineage_at(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(first, tmp_path) as (staged, trusted):
        reserved = reserve(root, first, staged, trusted)
    before = database_bytes(root)
    for fixture in (lower, conflict):
        with retained(fixture, tmp_path) as (staged, trusted):
            with pytest.raises(PlanError):
                reserve(root, fixture, staged, trusted, generation=SECOND, request="forward-2")
        assert database_bytes(root) == before
    with retained(higher, tmp_path) as (staged, trusted):
        reserve(root, higher, staged, trusted, generation=SECOND, request="forward-2")
    admitted, _, reservations = state(root)
    assert admitted["sequence"] == 11 and reservations[FIRST] == reserved
    before = database_bytes(root)
    with retained(first, tmp_path) as (staged, trusted):
        with pytest.raises(PlanError, match="below durable"):
            reserve(root, first, staged, trusted)
    assert database_bytes(root) == before


def test_revoked_or_closed_stage_cannot_authorize_even_an_exact_generation_retry(
    tmp_path, primitive,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        reserve(root, fixture, staged, trusted)
        before = database_bytes(root)
        revoked = copy.deepcopy(trusted)
        revoked["keys"][0]["revoked"] = True
        with pytest.raises(PlanError, match="revoked"):
            reserve(root, fixture, staged, trusted, current_policy=lambda: revoked)
    with pytest.raises(PlanError, match="retention has ended"):
        reserve(root, fixture, staged, trusted)
    assert database_bytes(root) == before


def test_reservation_consumes_artifacts_only_before_central_writer_custody(
    tmp_path, primitive, monkeypatch,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    owned, read_bytes = admission._owned, generations.artifact_staging._bytes
    held = False
    reads = 0

    @contextmanager
    def guarded_owned(path):
        nonlocal held
        with owned(path) as custody:
            held = True
            try:
                yield custody
            finally:
                held = False

    def outside_only(path, limit):
        nonlocal reads
        assert not held, "artifact contents were read while holding central writer custody"
        reads += 1
        return read_bytes(path, limit)

    with retained(fixture, tmp_path) as (staged, trusted):
        monkeypatch.setattr(admission, "_owned", guarded_owned)
        monkeypatch.setattr(generations.artifact_staging, "_bytes", outside_only)
        first = reserve(root, fixture, staged, trusted)
        assert reads > 0
        reads = 0
        assert reserve(root, fixture, staged, trusted) == first
        assert reads > 0
    assert state(root)[2] == {FIRST: first}


@pytest.mark.parametrize("change", [
    "revoked", "expired", "policy-floor", "target", "closed",
])
@pytest.mark.parametrize("retry", [False, True])
def test_reservation_rechecks_current_inputs_after_central_custody_acquisition(
    tmp_path, primitive, monkeypatch, change, retry,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    owned = admission._owned
    with retained(fixture, tmp_path) as (staged, trusted):
        if retry:
            reserve(root, fixture, staged, trusted)
        before = database_bytes(root)
        current = {"policy": copy.deepcopy(trusted), "target": target(fixture), "time": 150}
        reached = False

        @contextmanager
        def changing_owned(path):
            nonlocal reached
            with owned(path) as custody:
                reached = True
                if change == "revoked":
                    current["policy"]["keys"][0]["revoked"] = True
                elif change == "expired":
                    current["time"] = fixture["proof"]["payload"]["expires_at"]
                elif change == "policy-floor":
                    current["policy"]["minimum_sequence"] = selection(fixture)["sequence"] + 1
                elif change == "target":
                    current["target"]["revision"] += 1
                else:
                    staged._close()
                yield custody

        monkeypatch.setattr(admission, "_owned", changing_owned)
        with pytest.raises(PlanError):
            reserve(
                root, fixture, staged, trusted, current_policy=lambda: current["policy"],
                current_target=lambda: current["target"], clock=lambda: current["time"],
            )
    assert reached and database_bytes(root) == before
    assert set(state(root)[2]) == ({FIRST} if retry else set())


@pytest.mark.parametrize("change", ["floor", "request", "generation"])
def test_reservation_uses_committed_competitor_state_not_its_artifact_preflight(
    tmp_path, primitive, monkeypatch, independently_signed_variants, change,
):
    _, fixture, higher, _ = independently_signed_variants
    root = lineage_at(tmp_path)
    generations._initialize_lineage(root, identity())
    owned = admission._owned
    committed = None
    before = None
    with retained(fixture, tmp_path) as (staged, trusted):
        @contextmanager
        def competing_owned(path):
            nonlocal before, committed
            with monkeypatch.context() as patch:
                patch.setattr(admission, "_owned", owned)
                if change == "floor":
                    with retained(higher, tmp_path) as (new_stage, policy):
                        committed = reserve(
                            root, higher, new_stage, policy,
                            generation=SECOND, request="competing-request",
                        )
                else:
                    committed = reserve(
                        root, fixture, staged, trusted,
                        generation=SECOND if change == "request" else FIRST,
                        request="forward-1" if change == "request" else "competing-request",
                    )
            before = database_bytes(root)
            with owned(path) as custody:
                yield custody

        monkeypatch.setattr(admission, "_owned", competing_owned)
        with pytest.raises(PlanError):
            reserve(root, fixture, staged, trusted)
    assert before is not None and database_bytes(root) == before
    assert list(state(root)[2].values()) == [committed]


def test_retained_authentication_cannot_be_reused_after_its_snapshot_scope(
    tmp_path, primitive,
):
    fixture = artifact_fixture(tmp_path)
    with retained(fixture, tmp_path) as (staged, trusted):
        with staged._retained_authentication(trusted, clock=lambda: 150) as (manifest, verify):
            assert manifest == canonical(fixture["manifest"])
            assert verify(trusted, clock=lambda: 150)["sequence"] == selection(fixture)["sequence"]
        with pytest.raises(PlanError, match="retention has ended"):
            verify(trusted, clock=lambda: 150)
        assert staged.reverify(trusted, clock=lambda: 150)["retention_held"]


def test_retained_authentication_refuses_retention_closed_by_current_clock(
    tmp_path, primitive,
):
    fixture = artifact_fixture(tmp_path)
    with retained(fixture, tmp_path) as (staged, trusted):
        with staged._retained_authentication(trusted, clock=lambda: 150) as (_, verify):
            def closed_clock():
                staged._close()
                return 150

            with pytest.raises(PlanError, match="retention or ownership changed"):
                verify(trusted, clock=closed_clock)


@pytest.mark.parametrize("after", [False, True])
def test_reservation_uncertain_commit_rereads_the_same_generation_not_another(
    tmp_path, primitive, monkeypatch, after,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    commit = admission._commit

    def interrupted(connection):
        if after:
            commit(connection)
        raise sqlite3.OperationalError("synthetic unknown reservation outcome")

    with retained(fixture, tmp_path) as (staged, trusted):
        with monkeypatch.context() as patch:
            patch.setattr(admission, "_commit", interrupted)
            with pytest.raises(PlanError):
                reserve(root, fixture, staged, trusted)
        assert (FIRST in state(root)[2]) is after
        assert state(root, fences=True)[3]["current_generation"] == (FIRST if after else None)
        reservation = reserve(root, fixture, staged, trusted)
    assert state(root)[2] == {FIRST: reservation}


_HOLD_LEGACY = """
import contextlib,importlib.util,json,pathlib,sys
repo=pathlib.Path(sys.argv[1])
sys.path.insert(0,str(repo/'fleet-manager'/'src'))
from fleet_manager import artifact_staging,client_admission as admission
spec=importlib.util.spec_from_file_location('generation_test_runtime',repo/'libs'/'versioned-runtime'/'versioned_runtime.py')
runtime=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=runtime
spec.loader.exec_module(runtime)
@contextlib.contextmanager
def primitive():
    yield runtime
artifact_staging._primitive=primitive
root=pathlib.Path(sys.argv[2])
identity=json.loads(sys.argv[3])
with admission._owned(root) as (path,database_root,unchanged):
    with admission._connection(path,database_root=database_root,readonly=False) as connection:
        connection.execute('BEGIN IMMEDIATE')
        admission._read_state(connection,identity)
        print('LEGACY_CONSTRUCTION_CUSTODY',flush=True)
        sys.stdin.readline()
        unchanged()
        print('LEGACY_STILL_OWNED',flush=True)
"""


_HOLD_GENERATION_FENCE = """
import contextlib,importlib.util,json,pathlib,sys
repo=pathlib.Path(sys.argv[1])
sys.path.insert(0,str(repo/'fleet-manager'/'src'))
from fleet_manager import artifact_staging,client_admission as admission,client_generations as generations
from fleet_manager.planning import PlanError
spec=importlib.util.spec_from_file_location('generation_test_runtime',repo/'libs'/'versioned-runtime'/'versioned_runtime.py')
runtime=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=runtime
spec.loader.exec_module(runtime)
@contextlib.contextmanager
def primitive():
    yield runtime
artifact_staging._primitive=primitive
root=pathlib.Path(sys.argv[2])
identity=json.loads(sys.argv[3])
reservation=json.loads(sys.argv[4])
generation=root/('generation-'+reservation['generation'])
with admission._owned(generation):
    print('GENERATION_CUSTODY',flush=True)
    sys.stdin.readline()
    with admission._root(root) as (path,database_root,unchanged):
        with admission._connection(path,database_root=database_root,readonly=True) as connection:
            connection.execute('BEGIN')
            admitted,_,reservations,fences=generations._read(connection,identity,generations._location(path))
            try:
                generations._current(admitted,reservations,reservation,fences,shared=True)
            except PlanError as error:
                print('FENCED: '+str(error),flush=True)
            else:
                raise AssertionError('delayed generation retained shared authority')
            unchanged()
"""


_RESERVE_PROCESS = """
import contextlib,importlib.util,json,pathlib,sys
repo=pathlib.Path(sys.argv[1])
sys.path[:0]=[str(repo/'fleet-manager'/'src'),str(repo/'fleet-manager'/'tests')]
from fleet_manager import artifact_staging
from test_client_generations import identity,retained,reserve
spec=importlib.util.spec_from_file_location('generation_test_runtime',repo/'libs'/'versioned-runtime'/'versioned_runtime.py')
runtime=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=runtime
spec.loader.exec_module(runtime)
@contextlib.contextmanager
def primitive():
    yield runtime
artifact_staging._primitive=primitive
root=pathlib.Path(sys.argv[2])
fixture=json.loads(sys.argv[3])
area=pathlib.Path(sys.argv[4])
generation=sys.argv[5]
with retained(fixture,area) as (staged,trusted):
    print('AUTHENTICATED_STAGE_READY',flush=True)
    sys.stdin.readline()
    result=reserve(root,fixture,staged,trusted,generation=generation,request='request-'+generation)
    print(json.dumps(result),flush=True)
"""


def test_independent_process_reservations_commit_one_current_request_and_retry_never_reclaims_it(
    tmp_path, primitive,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    processes, receipts = [], {}
    try:
        for generation in (FIRST, SECOND):
            area = tmp_path / generation
            area.mkdir(mode=0o700)
            process = subprocess.Popen(
                [sys.executable, "-I", "-c", _RESERVE_PROCESS, str(REPOSITORY), str(root),
                 json.dumps(fixture), str(area), generation],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **FLAGS,
            )
            processes.append(process)
        for process in processes:
            assert process.stdout.readline().strip() == "AUTHENTICATED_STAGE_READY"
        for process in processes:
            process.stdin.write("\n")
            process.stdin.flush()
        for process in processes:
            receipt = json.loads(process.stdout.readline())
            receipts[receipt["generation"]] = receipt
            assert process.wait(timeout=10) == 0, process.stderr.read()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
    admitted, _, reservations, fences = state(root, fences=True)
    assert reservations == receipts and set(reservations) == {FIRST, SECOND}
    winner = fences["current_generation"]
    loser = SECOND if winner == FIRST else FIRST
    before = database_bytes(root)
    with retained(fixture, tmp_path) as (staged, trusted):
        assert reserve(
            root, fixture, staged, trusted, generation=loser, request="request-" + loser,
        ) == receipts[loser]
    assert database_bytes(root) == before
    generations._current(admitted, reservations, receipts[winner], fences, shared=True)
    with pytest.raises(PlanError, match="current shared publication"):
        generations._current(admitted, reservations, receipts[loser], fences, shared=True)


@pytest.mark.parametrize("retire", [False, True])
def test_delayed_process_observes_current_and_retired_fences_without_old_custody_release(
    tmp_path, primitive, retire,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        first = reserve(root, fixture, staged, trusted)
        old_root, _, _ = generations._generation_root(root, identity(), first)
        owner = (old_root / generations._OWNER).read_bytes()
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _HOLD_GENERATION_FENCE, str(REPOSITORY), str(root),
             json.dumps(identity()), json.dumps(first)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, **FLAGS,
        )
        try:
            assert process.stdout.readline().strip() == "GENERATION_CUSTODY"
            second = reserve(
                root, fixture, staged, trusted, generation=SECOND, request="forward-2",
            )
            generations._generation_root(root, identity(), second)
            if retire:
                generations._retire_generation(root, identity(), first, clock=lambda: 160)
            before = database_bytes(root)
            assert process.poll() is None
            process.stdin.write("\n")
            process.stdin.flush()
            result = process.stdout.readline().strip()
            assert result.startswith("FENCED: ")
            assert ("retired" if retire else "current shared publication") in result
            assert process.wait(timeout=10) == 0, process.stderr.read()
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
    assert database_bytes(root) == before
    assert (old_root / generations._OWNER).read_bytes() == owner
    assert state(root, fences=True)[3]["current_generation"] == SECOND


def test_real_predecessor_writer_and_construction_lease_do_not_gate_import_or_reservation(
    tmp_path, primitive,
):
    legacy = root_at(tmp_path)
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(legacy, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        admitted = admission.admit_client_release(
            legacy, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
        )["admitted"]
    before = database_bytes(legacy)
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", _HOLD_LEGACY, str(REPOSITORY), str(legacy),
         json.dumps(identity())], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, **FLAGS,
    )
    try:
        assert process.stdout.readline().strip() == "LEGACY_CONSTRUCTION_CUSTODY"
        generations._initialize_lineage(root, identity(), legacy_root=legacy)
        with retained(fixture, tmp_path) as (staged, trusted):
            reserve(root, fixture, staged, trusted)
        assert process.poll() is None and state(root)[0] == admitted
        process.stdin.write("\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "LEGACY_STILL_OWNED"
        assert process.wait(timeout=10) == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
    assert database_bytes(legacy) == before


@pytest.mark.parametrize("boundary", ["intent-commit", "before-rename", "after-rename"])
def test_generation_root_retry_recovers_only_the_original_physical_intent(
    tmp_path, primitive, monkeypatch, boundary,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        reservation = reserve(root, fixture, staged, trusted)
    publication = primitive._runtime_component("versioned_runtime_publication")
    commit, rename = admission._commit, publication._publish_receipt

    def interrupted_commit(connection):
        commit(connection)
        raise sqlite3.OperationalError("reply lost after physical intent commit")

    def interrupted_rename(source, destination):
        if boundary == "after-rename":
            rename(source, destination)
        raise OSError("synthetic root publication interruption")

    with monkeypatch.context() as patch:
        if boundary == "intent-commit":
            patch.setattr(admission, "_commit", interrupted_commit)
        else:
            patch.setattr(publication, "_publish_receipt", interrupted_rename)
        with pytest.raises((PlanError, OSError, sqlite3.Error)):
            generations._generation_root(root, identity(), reservation)
    actual, proof, binding = generations._generation_root(root, identity(), reservation)
    assert actual.name == "generation-" + FIRST
    assert list(generations.artifact_staging._directory(actual)) == proof["object"]
    assert not (root / proof["temporary"]).exists()
    assert generations.client_construction._read(actual / generations._OWNER) == binding
    assert generations._generation_root(root, identity(), reservation) == (actual, proof, binding)
    assert not (actual / admission.DATABASE).exists()


@pytest.mark.parametrize("change", ["foreign-empty", "same-byte-replacement"])
def test_generation_root_never_overwrites_foreign_or_same_byte_directory(
    tmp_path, primitive, change,
):
    root = lineage_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    generations._initialize_lineage(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        reservation = reserve(root, fixture, staged, trusted)
    destination = root / ("generation-" + FIRST)
    if change == "same-byte-replacement":
        generations._generation_root(root, identity(), reservation)
        destination.rename(root / "retained-original-generation")
        shutil.copytree(root / "retained-original-generation", destination)
    else:
        destination.mkdir(mode=0o700)
    before = database_bytes(root)
    members = {path.name: path.read_bytes() for path in destination.iterdir()}
    with pytest.raises(PlanError, match="physical"):
        generations._generation_root(root, identity(), reservation)
    assert database_bytes(root) == before
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == members
