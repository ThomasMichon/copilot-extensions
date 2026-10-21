"""Real SQLite, independent signatures and kernel ownership; no candidate execution."""

from contextlib import contextmanager
import base64
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

import fleet_manager.client_admission as admission
from fleet_manager.planning import PlanError, canonical
from fleet_manager.release_selection import ReleaseSelection
from test_artifact_staging import primitive as primitive, stage
from test_client_distribution import FLAGS, run
from test_release_auth import artifact_fixture

REPOSITORY = Path(__file__).resolve().parents[2]


def identity():
    return {
        "schema": "fleet-manager.client-installation", "version": 1,
        "authority": "test-authority", "installation": "test-installation", "role": "client",
        "initialized_by": "test-adopter", "receipt": "init-1", "selection_owner": "test-adopter",
    }


def root_at(tmp_path):
    root = tmp_path / "private-client"
    root.mkdir(mode=0o700)
    return root


def selection(fixture):
    return {
        "schema": "fleet-manager.release-selection", "version": 1,
        "decision": "selected-client", "revision": 1, "owner": "test-adopter",
        "authority": "test-authority", "installation": "test-installation", "role": "client",
        "release_digest": hashlib.sha256(canonical(fixture["manifest"])).hexdigest(),
        "sequence": fixture["proof"]["payload"]["sequence"],
        "components": sorted(fixture["manifest"]["roles"]["client"]),
    }


@contextmanager
def retained(fixture, tmp_path):
    parent = tmp_path / "private-stage"
    parent.mkdir(mode=0o700, exist_ok=True)
    context, trusted = stage(fixture, parent)
    with context as frozen:
        yield frozen, trusted


def database_bytes(root):
    return (root / admission.DATABASE).read_bytes()


def test_explicit_empty_root_initialization_and_readonly_rerun(tmp_path, primitive):
    root = root_at(tmp_path)
    report = admission.initialize_client_admission(root, identity())
    before = database_bytes(root)
    assert report["status"] == "initialized" and report["mutations_performed"]
    assert report["admitted"] is None
    for result in (
        admission.initialize_client_admission(root, identity()),
        admission.inspect_client_admission(root, identity()),
    ):
        assert not result["mutations_performed"]
        assert not any(result[name] for name in ("constructed", "healthy", "active",
                                                 "activation_authorized", "live_readiness_verified"))
    assert database_bytes(root) == before
    with sqlite3.connect(root / admission.DATABASE) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM installation").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM admission").fetchone() == (0,)


def test_readonly_missing_state_and_foreign_initialization_leave_root_untouched(tmp_path, primitive):
    root = root_at(tmp_path)
    with pytest.raises(PlanError, match="without mutation"):
        admission.inspect_client_admission(root, identity())
    assert list(root.iterdir()) == []
    sentinel = root / "user-config"
    sentinel.write_bytes(b"foreign configuration")
    with pytest.raises(PlanError, match="nonempty foreign root"):
        admission.initialize_client_admission(root, identity())
    assert list(root.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"foreign configuration"


@pytest.mark.parametrize("field,value", [
    ("role", "gateway"), ("role", "node"), ("authority", "other-authority"),
    ("installation", "other-installation"), ("receipt", "other-receipt"),
    ("selection_owner", "other-adopter"),
])
def test_wrong_scope_or_initialization_receipt_never_changes_state(tmp_path, primitive, field, value):
    root = root_at(tmp_path)
    admission.initialize_client_admission(root, identity())
    before = database_bytes(root)
    changed = {**identity(), field: value}
    with pytest.raises(PlanError):
        admission.initialize_client_admission(root, changed)
    with pytest.raises(PlanError):
        admission.inspect_client_admission(root, changed)
    assert database_bytes(root) == before


def test_actual_signed_immutable_admission_and_fresh_idempotent_retry(tmp_path, primitive):
    root = root_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        result = admission.admit_client_release(
            root, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
        )
        before = database_bytes(root)
        repeated = admission.admit_client_release(
            root, identity(), selection(fixture), staged, trusted, clock=lambda: 151,
        )
        revoked = copy.deepcopy(trusted)
        revoked["keys"][0]["revoked"] = True
        with pytest.raises(PlanError, match="revoked"):
            admission.admit_client_release(
                root, identity(), selection(fixture), staged, revoked, clock=lambda: 151,
            )
    assert result["mutations_performed"] and not repeated["mutations_performed"]
    assert result["admitted"] == repeated["admitted"]
    assert database_bytes(root) == before
    persisted = admission.inspect_client_admission(root, identity())["admitted"]
    assert persisted["sequence"] == 10 and persisted["selection"] == selection(fixture)
    assert persisted["selection_digest"] == ReleaseSelection.from_dict(
        selection(fixture),
    ).selection_digest
    assert not result["active"] and not result["constructed"]


@pytest.mark.parametrize("field,value", [
    ("owner", "other-adopter"), ("installation", "other-installation"), ("role", "gateway"),
    ("release_digest", "b" * 64), ("sequence", 11), ("components", ["core"]),
])
def test_valid_signature_does_not_select_a_different_release_or_scope(
    tmp_path, primitive, field, value,
):
    root = root_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(root, identity())
    before = database_bytes(root)
    with retained(fixture, tmp_path) as (staged, trusted):
        changed = {**selection(fixture), field: value}
        with pytest.raises(PlanError):
            admission.admit_client_release(root, identity(), changed, staged, trusted, clock=lambda: 150)
        if field == "release_digest":
            forged = replace(staged, release_digest=value)
            with pytest.raises(PlanError, match="not selected"):
                admission.admit_client_release(
                    root, identity(), changed, forged, trusted, clock=lambda: 150,
                )
    assert database_bytes(root) == before
    assert admission.inspect_client_admission(root, identity())["admitted"] is None


def test_saved_report_closed_stage_or_changed_selection_is_not_retry_authority(tmp_path, primitive):
    root = root_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        result = admission.admit_client_release(
            root, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
        )
        before = database_bytes(root)
        changed = {**selection(fixture), "revision": 2}
        with pytest.raises(PlanError, match="content conflicts"):
            admission.admit_client_release(root, identity(), changed, staged, trusted, clock=lambda: 150)
        with pytest.raises(PlanError, match="live retained"):
            admission.admit_client_release(
                root, identity(), selection(fixture), result, trusted, clock=lambda: 150,
            )
    with pytest.raises(PlanError, match="retention has ended"):
        admission.admit_client_release(root, identity(), selection(fixture), staged, trusted, clock=lambda: 150)
    assert database_bytes(root) == before


@pytest.mark.parametrize("mutation", ["version", "identity", "receipt", "corrupt"])
def test_existing_corrupt_or_unsupported_state_is_never_reinitialized(tmp_path, primitive, mutation):
    root = root_at(tmp_path)
    admission.initialize_client_admission(root, identity())
    if mutation == "corrupt":
        (root / admission.DATABASE).write_bytes(b"not a database")
    else:
        with sqlite3.connect(root / admission.DATABASE) as connection:
            if mutation == "version":
                connection.execute("PRAGMA user_version = 99")
            elif mutation == "identity":
                connection.execute("UPDATE installation SET identity = '{}'")
            else:
                connection.execute("INSERT INTO admission VALUES (1, 10, '{}')")
    before = database_bytes(root)
    with pytest.raises(PlanError):
        admission.inspect_client_admission(root, identity())
    with pytest.raises(PlanError):
        admission.initialize_client_admission(root, identity())
    assert database_bytes(root) == before


def test_linked_state_is_refused_without_changing_target(tmp_path, primitive):
    root = root_at(tmp_path)
    target = tmp_path / "foreign"
    target.write_bytes(b"foreign state")
    try:
        (root / admission.DATABASE).symlink_to(target)
    except OSError:
        pytest.skip("native symlink creation is unavailable")
    with pytest.raises(PlanError):
        admission.initialize_client_admission(root, identity())
    assert target.read_bytes() == b"foreign state"
    assert not (root / ".construction-leases").exists()


@pytest.mark.parametrize("grant", ["root", "inherited-children"])
def test_shared_native_root_refuses_initialization_without_permission_repair(
    tmp_path, primitive, grant,
):
    root = root_at(tmp_path)
    if os.name == "nt":
        icacls = shutil.which("icacls")
        assert icacls
        access = "*S-1-1-0:" + ("(R)" if grant == "root" else "(OI)(CI)(IO)(R)")
        changed = run([icacls, str(root), "/grant", access])
        assert changed.returncode == 0, changed.stderr
    else:
        if grant == "inherited-children":
            pytest.skip("native Windows inheritable ACL boundary")
        root.chmod(0o750)
    with pytest.raises(PlanError, match="private|shared access grant"):
        admission.initialize_client_admission(root, identity())
    assert list(root.iterdir()) == []
    if os.name != "nt":
        assert root.stat().st_mode & 0o777 == 0o750


@pytest.mark.parametrize("state_file", ["database", "lease"])
def test_shared_native_state_refuses_admission_without_permission_repair(
    tmp_path, primitive, state_file,
):
    root = root_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(root, identity())
    before = database_bytes(root)
    path = (
        root / admission.DATABASE if state_file == "database"
        else next((root / ".construction-leases").iterdir())
    )
    if os.name == "nt":
        icacls = shutil.which("icacls")
        assert icacls
        changed = run([icacls, str(path), "/grant", "*S-1-1-0:(R)"])
        assert changed.returncode == 0, changed.stderr
        permissions = run([icacls, str(path)]).stdout
    else:
        path.chmod(0o640)
        permissions = path.stat().st_mode
    with retained(fixture, tmp_path) as (staged, trusted):
        with pytest.raises(PlanError, match="private|shared access grant"):
            admission.admit_client_release(
                root, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
            )
    assert database_bytes(root) == before
    after = run([icacls, str(path)]).stdout if os.name == "nt" else path.stat().st_mode
    assert after == permissions


def test_missing_persistent_ownership_file_cannot_create_a_second_lock_inode(tmp_path, primitive):
    root = root_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(root, identity())
    before = database_bytes(root)
    lease = next((root / ".construction-leases").iterdir())
    lease.unlink()
    with retained(fixture, tmp_path) as (staged, trusted):
        with pytest.raises(PlanError, match="ownership metadata is missing"):
            admission.admit_client_release(
                root, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
            )
    assert not lease.exists() and database_bytes(root) == before
    assert admission.inspect_client_admission(root, identity())["admitted"] is None


@pytest.mark.parametrize("operation", ["inspect", "initialize"])
def test_readonly_wal_refusal_preserves_database_and_directory(tmp_path, primitive, operation):
    root = root_at(tmp_path)
    admission.initialize_client_admission(root, identity())
    with sqlite3.connect(root / admission.DATABASE) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.close()
    assert not (root / (admission.DATABASE + "-wal")).exists()
    assert not (root / (admission.DATABASE + "-shm")).exists()
    before = database_bytes(root), sorted(path.name for path in root.iterdir())
    action = admission.inspect_client_admission if operation == "inspect" else admission.initialize_client_admission
    with pytest.raises(PlanError, match="WAL database header"):
        action(root, identity())
    assert before == (database_bytes(root), sorted(path.name for path in root.iterdir()))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-wide fcntl lock lifetime")
def test_overlapping_readers_retain_sqlite_lock_against_independent_writer(tmp_path, primitive):
    root = root_at(tmp_path)
    admission.initialize_client_admission(root, identity())
    script = """
import sqlite3,sys
c=sqlite3.connect(sys.argv[1],timeout=.1)
try:
    c.execute('BEGIN IMMEDIATE')
    c.execute('UPDATE installation SET identity=identity')
    c.commit()
except sqlite3.OperationalError as error:
    assert 'locked' in str(error)
    c.rollback()
    print('LOCKED')
else:
    print('COMMITTED')
finally:
    c.close()
"""
    with admission._root(root) as (path, database_root, unchanged):
        with admission._connection(path, database_root=database_root, readonly=True) as first:
            first.execute("BEGIN")
            admission._read_state(first, identity())
            with admission._connection(path, database_root=database_root, readonly=True) as second:
                second.execute("BEGIN")
                admission._read_state(second, identity())
            result = run([sys.executable, "-I", "-c", script, str(root / admission.DATABASE)])
            assert result.returncode == 0 and result.stdout.strip() == "LOCKED", result.stderr
        unchanged()
    result = run([sys.executable, "-I", "-c", script, str(root / admission.DATABASE)])
    assert result.returncode == 0 and result.stdout.strip() == "COMMITTED", result.stderr


@pytest.mark.parametrize("change,clock", [
    ({"minimum_sequence": 11}, 150), ({"valid_until": 150}, 150), ({}, 200),
])
def test_fresh_floor_policy_and_signature_expiry_refuse_even_an_idempotent_retry(
    tmp_path, primitive, change, clock,
):
    root = root_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(root, identity())
    with retained(fixture, tmp_path) as (staged, trusted):
        admission.admit_client_release(
            root, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
        )
        before = database_bytes(root)
        with pytest.raises(PlanError):
            admission.admit_client_release(
                root, identity(), selection(fixture), staged, {**trusted, **change},
                clock=lambda: clock,
            )
        updated = {**trusted, "revision": "r2"}
        retried = admission.admit_client_release(
            root, identity(), selection(fixture), staged, updated, clock=lambda: 151,
        )
    assert not retried["mutations_performed"]
    assert database_bytes(root) == before


def test_sqlite_commit_error_after_actual_commit_reports_uncertainty(tmp_path, primitive, monkeypatch):
    root = root_at(tmp_path)
    fixture = artifact_fixture(tmp_path)
    admission.initialize_client_admission(root, identity())
    original = sqlite3.connect

    class Uncertain(sqlite3.Connection):
        def commit(self):
            super().commit()
            raise sqlite3.OperationalError("reply lost after commit")

    monkeypatch.setattr(admission.sqlite3, "connect", lambda *a, **kw: original(*a, factory=Uncertain, **kw))
    with retained(fixture, tmp_path) as (staged, trusted):
        with pytest.raises(PlanError, match="outcome is unknown"):
            admission.admit_client_release(root, identity(), selection(fixture), staged, trusted, clock=lambda: 150)
    monkeypatch.setattr(admission.sqlite3, "connect", original)
    assert admission.inspect_client_admission(root, identity())["admitted"]["sequence"] == 10


def test_cli_initialize_inspect_and_missing_state_do_not_host(tmp_path, primitive, capsys):
    from fleet_manager.__main__ import main

    root = root_at(tmp_path)
    identity_file = tmp_path / "identity.json"
    identity_file.write_bytes(canonical(identity()))
    base = ["--root", str(root), "--identity", str(identity_file)]
    assert main(["client-state", "inspect", *base]) == 1
    assert not list(root.iterdir())
    absent_inputs = [
        part for name in ("selection", "release", "proof", "policy", "sources", "staging-parent")
        for part in ("--" + name, str(tmp_path / "not-created"))
    ]
    assert main(["client-state", "admit", *base, *absent_inputs]) == 1
    assert not list(root.iterdir())
    assert main(["client-state", "initialize", *base]) == 0
    assert main(["client-state", "inspect", *base]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["active"] is False for line in lines)
    assert not (root / "versions").exists()


def independently_sign(payloads, signing_root):
    node = shutil.which("node")
    openssl = shutil.which("openssl")
    assert node or openssl, "an independent Node or OpenSSL signer is required"
    script = """
const crypto = require('node:crypto');
const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
const payloads = JSON.parse(process.argv[1]);
const canon = value => JSON.stringify(Object.fromEntries(Object.entries(value).sort()));
const prefix = Buffer.from('fleet-manager.release-signature.v1\\0');
console.log(JSON.stringify({public_key:publicKey.export({type:'spki',format:'der'}).subarray(-32).toString('base64'),
proofs:payloads.map(payload=>({payload,signature:crypto.sign(null,Buffer.concat([prefix,Buffer.from(canon(payload))]),privateKey).toString('base64')}))}));
"""
    if node:
        result = run([node, "-e", script, json.dumps(payloads)])
        assert result.returncode == 0, result.stderr
        signed = json.loads(result.stdout)
    else:
        key = signing_root / "synthetic-key.pem"
        public = signing_root / "synthetic-public.der"
        for argv in (
            [openssl, "genpkey", "-algorithm", "ED25519", "-out", str(key)],
            [openssl, "pkey", "-in", str(key), "-pubout", "-outform", "DER", "-out", str(public)],
        ):
            result = run(argv)
            assert result.returncode == 0, result.stderr
        proofs = []
        for index, payload in enumerate(payloads):
            message = signing_root / f"message-{index}.bin"
            signature = signing_root / f"signature-{index}.bin"
            message.write_bytes(b"fleet-manager.release-signature.v1\0" + canonical(payload))
            result = run([
                openssl, "pkeyutl", "-sign", "-rawin", "-inkey", str(key),
                "-in", str(message), "-out", str(signature),
            ])
            assert result.returncode == 0, result.stderr
            proofs.append({
                "payload": payload, "signature": base64.b64encode(signature.read_bytes()).decode(),
            })
        signed = {
            "public_key": base64.b64encode(public.read_bytes()[-32:]).decode(),
            "proofs": proofs,
        }
    return signed


@pytest.fixture(scope="module")
def independently_signed_variants(tmp_path_factory):
    signing_root = tmp_path_factory.mktemp("signed-admission")
    fixture = artifact_fixture(signing_root)
    changed = copy.deepcopy(fixture["manifest"])
    changed["id"] = "different-same-sequence-release"
    payloads = []
    for sequence, manifest in ((9, fixture["manifest"]), (10, fixture["manifest"]),
                               (11, fixture["manifest"]), (10, changed)):
        payloads.append({
            **fixture["proof"]["payload"], "sequence": sequence,
            "release_digest": hashlib.sha256(canonical(manifest)).hexdigest(),
        })
    signed = independently_sign(payloads, signing_root)
    variants = []
    for item, manifest in zip(signed["proofs"], (fixture["manifest"], fixture["manifest"],
                                               fixture["manifest"], changed)):
        value = copy.deepcopy(fixture)
        value["manifest"] = copy.deepcopy(manifest)
        value["proof"] = {**value["proof"], **item}
        value["policy"]["keys"][0]["public_key"] = signed["public_key"]
        value["policy"]["minimum_sequence"] = 1
        variants.append(value)
    return variants


def test_real_higher_lower_and_same_sequence_conflicts_preserve_floor(
    tmp_path, primitive, independently_signed_variants,
):
    lower, first, higher, conflict = independently_signed_variants
    root = root_at(tmp_path)
    admission.initialize_client_admission(root, identity())
    with retained(first, tmp_path) as (staged, trusted):
        admission.admit_client_release(root, identity(), selection(first), staged, trusted, clock=lambda: 150)
    before = database_bytes(root)
    with retained(higher, tmp_path) as (staged, trusted):
        with pytest.raises(PlanError, match="not selected"):
            admission.admit_client_release(root, identity(), selection(first), staged, trusted, clock=lambda: 150)
    assert database_bytes(root) == before
    for fixture, message in ((lower, "below durable"), (conflict, "content conflicts")):
        with retained(fixture, tmp_path) as (staged, trusted):
            with pytest.raises(PlanError, match=message):
                admission.admit_client_release(root, identity(), selection(fixture), staged, trusted, clock=lambda: 150)
    with retained(higher, tmp_path) as (staged, trusted):
        admission.admit_client_release(root, identity(), selection(higher), staged, trusted, clock=lambda: 151)
    assert admission.inspect_client_admission(root, identity())["admitted"]["sequence"] == 11


CHILD = """
import contextlib, importlib.util, json, pathlib, sys, time
repo = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(repo / 'fleet-manager' / 'src'))
import fleet_manager.artifact_staging as staging
import fleet_manager.client_admission as admission
spec = importlib.util.spec_from_file_location('real_child_runtime', repo/'libs'/'versioned-runtime'/'versioned_runtime.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
@contextlib.contextmanager
def primitive():
    yield module
staging._primitive = primitive
value = json.loads(sys.stdin.read())
root = pathlib.Path(value['root'])
if value['operation'] == 'inspect':
    print(json.dumps(admission.inspect_client_admission(root,value['identity'])),flush=True)
elif value['operation'] == 'initialize':
    real_commit=admission._commit
    def interrupted_initialization(connection):
        if value['pause']=='after':
            real_commit(connection)
        print('INITIALIZATION_BOUNDARY',flush=True)
        time.sleep(30)
    if value['pause']=='publication':
        real_sync=admission._sync_directory
        def interrupted_publication(path):
            if (root/admission.DATABASE).exists():
                print('INITIALIZATION_BOUNDARY',flush=True)
                time.sleep(30)
            real_sync(path)
        admission._sync_directory=interrupted_publication
    else:
        admission._commit=interrupted_initialization
    print(json.dumps(admission.initialize_client_admission(root,value['identity'])),flush=True)
else:
    fixture = value['fixture']
    parent = pathlib.Path(value['parent'])
    with staging.stage_authenticated_artifacts(
        staging.canonical(fixture['manifest']),fixture['proof'],fixture['policy'],fixture['sources'],
        authority='test-authority',installation='test-installation',role='client',
        staging_parent=parent,clock=lambda:150,
    ) as retained:
        real_commit = admission._commit
        if value.get('pause'):
            def interrupted(connection):
                if value['pause'] == 'after':
                    real_commit(connection)
                print('COMMIT_BOUNDARY',flush=True)
                time.sleep(30)
            admission._commit = interrupted
        print(json.dumps(admission.admit_client_release(
            root,value['identity'],value['selection'],retained,fixture['policy'],clock=lambda:150,
        )),flush=True)
"""


def child_input(tmp_path, root, fixture, *, pause=None):
    parent = tmp_path / ("child-stage-" + (pause or "normal"))
    parent.mkdir(mode=0o700)
    return {"operation": "admit", "root": str(root), "identity": identity(),
            "fixture": fixture, "selection": selection(fixture), "parent": str(parent),
            "pause": pause}


@pytest.mark.parametrize("boundary", ["before", "after", "publication"])
def test_actual_initialization_death_can_retry_without_publishing_partial_state(
    tmp_path, primitive, boundary, monkeypatch,
):
    root = root_at(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(REPOSITORY)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **FLAGS,
    )
    try:
        process.stdin.write(json.dumps({
            "operation": "initialize", "root": str(root), "identity": identity(),
            "pause": boundary,
        }))
        process.stdin.close()
        assert process.stdout.readline().strip() == "INITIALIZATION_BOUNDARY"
        process.kill()
        assert process.wait(timeout=10) != 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
    assert (root / admission.DATABASE).exists() is (boundary == "publication")
    candidate = root / admission._initialization_name(identity())
    assert (candidate / admission.DATABASE).is_file() is (boundary != "publication")
    foreign = candidate / "foreign-state"
    foreign.write_bytes(b"must be retained")
    with pytest.raises(PlanError, match="foreign state"):
        admission.initialize_client_admission(root, identity())
    assert foreign.read_bytes() == b"must be retained"
    foreign.unlink()
    sync = admission._sync_directory
    clear = admission._clear_initialization
    synced = []

    def synced_directory(path):
        sync(path)
        synced.append(True)

    def clear_after_sync(path):
        if (root / admission.DATABASE).exists():
            assert synced, "published database acknowledged before the directory durability barrier"
        clear(path)

    monkeypatch.setattr(admission, "_sync_directory", synced_directory)
    monkeypatch.setattr(admission, "_clear_initialization", clear_after_sync)
    result = admission.initialize_client_admission(root, identity())
    assert result["status"] == "initialized" and result["mutations_performed"]
    assert not candidate.exists()
    assert admission.inspect_client_admission(root, identity())["admitted"] is None
    assert not admission.initialize_client_admission(root, identity())["mutations_performed"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory and lock-file durability barriers")
@pytest.mark.parametrize("fault", [None, "lock", "lease-directory"])
def test_initialization_syncs_ownership_metadata_before_database_publication(
    tmp_path, primitive, monkeypatch, fault,
):
    root = root_at(tmp_path)
    events = []
    inject = [True]
    sync = os.fsync
    replace_file = os.replace

    def observed_sync(descriptor):
        info = os.fstat(descriptor)
        lease_root = root / ".construction-leases"
        candidates = [("root", root), ("lease-directory", lease_root)]
        if lease_root.exists():
            candidates.extend(("lock", path) for path in lease_root.iterdir())
        for label, path in candidates:
            if path.exists() and (path.stat().st_dev, path.stat().st_ino) == (info.st_dev, info.st_ino):
                events.append(label)
                if label == fault and inject[0]:
                    inject[0] = False
                    raise OSError("injected ownership sync failure")
                break
        sync(descriptor)

    def observed_publication(source, destination):
        if Path(destination).name == admission.DATABASE:
            assert events.index("lock") < events.index("lease-directory") < events.index("root")
            events.append("publish")
        return replace_file(source, destination)

    monkeypatch.setattr(admission.os, "fsync", observed_sync)
    monkeypatch.setattr(admission.os, "replace", observed_publication)
    if fault:
        with pytest.raises(PlanError, match="durable state"):
            admission.initialize_client_admission(root, identity())
        assert not (root / admission.DATABASE).exists() and "publish" not in events
        events.clear()
    report = admission.initialize_client_admission(root, identity())
    assert report["status"] == "initialized"
    assert admission.inspect_client_admission(root, identity())["admitted"] is None


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_actual_process_death_at_commit_boundary_and_owned_retry(
    tmp_path, primitive, independently_signed_variants, boundary,
):
    fixture = independently_signed_variants[1]
    root = root_at(tmp_path)
    admission.initialize_client_admission(root, identity())
    value = child_input(tmp_path, root, fixture, pause=boundary)
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(REPOSITORY)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **FLAGS,
    )
    try:
        process.stdin.write(json.dumps(value))
        process.stdin.close()
        assert process.stdout.readline().strip() == "COMMIT_BOUNDARY"
        process.kill()
        assert process.wait(timeout=10) != 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
    with retained(fixture, tmp_path) as (staged, trusted):
        retried = admission.admit_client_release(
            root, identity(), selection(fixture), staged, trusted, clock=lambda: 150,
        )
    assert retried["mutations_performed"] is (boundary == "before")
    result = subprocess.run(
        [sys.executable, "-c", CHILD, str(REPOSITORY)], input=json.dumps({
            "operation": "inspect", "root": str(root), "identity": identity(),
        }), capture_output=True, text=True, timeout=30, **FLAGS,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["admitted"] == retried["admitted"]
    with sqlite3.connect(root / admission.DATABASE) as connection:
        rows = connection.execute("SELECT sequence, receipt FROM admission").fetchall()
    assert len(rows) == 1 and rows[0][0] == 10
    assert json.loads(rows[0][1]) == retried["admitted"]


def test_two_actual_processes_serialize_one_admission_and_one_retry(
    tmp_path, primitive, independently_signed_variants,
):
    fixture = independently_signed_variants[1]
    root = root_at(tmp_path)
    admission.initialize_client_admission(root, identity())
    processes = []
    try:
        for number in range(2):
            area = tmp_path / f"worker-{number}"
            area.mkdir(mode=0o700)
            value = child_input(area, root, fixture)
            process = subprocess.Popen(
                [sys.executable, "-c", CHILD, str(REPOSITORY)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **FLAGS,
            )
            process.stdin.write(json.dumps(value))
            process.stdin.close()
            process.stdin = None
            processes.append(process)
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert sorted(result["mutations_performed"] for result in results) == [False, True]
        assert results[0]["admitted"] == results[1]["admitted"]
        assert admission.inspect_client_admission(root, identity())["admitted"] == results[0]["admitted"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
