"""Real Bash/awk/coreutils progress and source admission, with no Python on PATH."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from update_driver import foundation_state as fs
from update_driver.foundation_inputs import SUPPORT, inspect

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux native foundation")


@pytest.fixture
def tools(area):
    directory = area / "native-tools"
    directory.mkdir()
    for name in (
        "bash", "dirname", "uname", "readlink", "stat", "find", "sort", "sha256sum", "iconv",
        "cat", "mkdir", "sync", "awk", "date", "mktemp", "mv", "rm", "cp", "flock", "grep",
    ):
        source = shutil.which(name)
        assert source, f"required Linux native prerequisite unavailable: {name}"
        (directory / name).symlink_to(source)
    return directory


def run_native(root, plugin, mode, tools, *args, success=True):
    library = Path(__file__).resolve().parents[2]
    fixture = Path(__file__).with_name("fixture_foundation_linux.sh")
    result = subprocess.run(
        [str(tools / "bash"), str(fixture), str(root), str(plugin), str(library), mode, *args],
        env=dict(os.environ, PATH=str(tools)), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=180,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def document(**changes):
    return dict({
        "schema": fs.SCHEMA, "version": fs.VERSION,
        **asdict(fs.FoundationOperation(
            "a" * 32, "b" * 64, fs.boot.kernel_boot_identity(), "running", 1,
        )),
    }, **changes)


def test_cold_roundtrip_uses_actual_fd_custody_and_matches_python(native_source, area, tools):
    root = area / "state"
    root.mkdir(mode=0o700)
    expected = inspect(native_source).digest
    run_native(root, native_source, "roundtrip", tools)
    admitted = fs._validate(json.loads((root / "admitted.json").read_text()))
    assert admitted.source_hash == expected and admitted.boot_id == fs.boot.kernel_boot_identity()
    assert admitted.state == "running"
    for name, state, code in [("completed", "completed", 0), ("failed", "failed", -7)]:
        value = fs._validate(json.loads((root / f"{name}.json").read_text()))
        assert value.state == state and value.exit_code == code
    value = fs.read(root)
    assert value.state == "interrupted" and value.fenced and value.exit_code is None
    assert not (root / "queue.sqlite3").exists()
    assert not (root / "foundation").exists()
    assert not list(root.glob(".foundation-operation-*.tmp"))


def test_freeze_matches_fixed_byte_inventory_including_unusual_names(native_source, area, tools):
    root = area / "state"
    root.mkdir(mode=0o700)
    for name in ["line\nbreak.py", "trailing\n", "space name.py", ".hidden", "-option"]:
        (native_source / name).write_bytes(name.encode())
    expected = inspect(native_source)
    run_native(root, native_source, "freeze", tools)
    frozen = inspect(root / "frozen" / "update-driver")
    assert frozen.digest == expected.digest
    assert not (root / "frozen" / "update-driver" / "__pycache__").exists()
    for path in expected.files:
        assert (root / "frozen" / path.relative_to(native_source.parent)).read_bytes() == path.read_bytes()


def test_changed_source_preserves_running_fence_and_never_freezes(native_source, area, tools):
    root = area / "state"
    root.mkdir(mode=0o700)
    run_native(root, native_source, "stale-source", tools)
    assert fs.read(root).state == "running" and fs.read(root).fenced
    assert not (root / "frozen").exists()


@pytest.mark.parametrize("package", ["update-driver", "agent-procutil", "single-instance-lease"])
def test_ancestor_execution_root_never_freezes_into_source(native_source, area, tools, package):
    root = area
    destination = native_source.parent / package / "frozen"
    before = {
        path.relative_to(native_source.parent): path.read_bytes()
        for path in native_source.parent.rglob("*") if path.is_file()
    }
    run_native(root, native_source, "refuse-freeze", tools, str(destination), inspect(native_source).digest)
    after = {
        path.relative_to(native_source.parent): path.read_bytes()
        for path in native_source.parent.rglob("*") if path.is_file()
    }
    assert after == before
    assert not destination.exists() and not (root / fs.FILENAME).exists()


def test_mutations_refuse_without_actual_fd_custody(native_source, area, tools):
    root = area / "state"
    root.mkdir(mode=0o700)
    run_native(root, native_source, "foreign", tools, inspect(native_source).digest)
    assert not (root / fs.FILENAME).exists() and not (root / "frozen").exists()


@pytest.mark.parametrize("name", [
    "queue.sqlite3", "queue.sqlite3-wal", "queue.sqlite3-shm", "queue.sqlite3-journal",
])
def test_existing_queue_and_sidecars_are_never_opened_or_replaced(native_source, area, tools, name):
    root = area / "state"
    root.mkdir(mode=0o700)
    path = root / name
    path.write_bytes(b"existing canonical queue evidence")
    before = path.read_bytes()
    result = run_native(root, native_source, "refuse", tools)
    assert "queue-aware" in result.stderr
    assert path.read_bytes() == before and not (root / fs.FILENAME).exists()


@pytest.mark.parametrize("kind", ["lost-progress", "missing-support", "source-link", "progress-link"])
def test_incomplete_or_linked_inputs_fail_before_mutation(native_source, area, tools, kind):
    root = area / "state"
    root.mkdir(mode=0o700)
    if kind == "lost-progress":
        (root / "foundation").mkdir()
    elif kind == "missing-support":
        (native_source / "scripts" / SUPPORT[0]).unlink()
    elif kind == "source-link":
        (native_source / "linked").symlink_to(native_source / "source.py")
    else:
        target = area / "untouched.json"
        target.write_text(json.dumps(document()))
        (root / fs.FILENAME).symlink_to(target)
    run_native(root, native_source, "refuse", tools)
    if kind == "progress-link":
        assert target.read_text() == json.dumps(document())
    else:
        assert not (root / fs.FILENAME).exists()


def test_free_custody_reconciles_owner_loss_without_certifying_cleanup(native_source, area, tools):
    root = area / "state"
    root.mkdir(mode=0o700)
    (root / fs.FILENAME).write_text(json.dumps(document()))
    run_native(root, native_source, "reconcile", tools)
    value = fs.read(root)
    assert value.state == "interrupted" and value.fenced and value.exit_code is None
    assert value.error == "owner_lost_custody"
    before = (root / fs.FILENAME).read_bytes()
    run_native(root, native_source, "reconcile", tools)
    assert (root / fs.FILENAME).read_bytes() == before


@pytest.mark.parametrize("kind", ["same", "old", "other-platform"])
def test_recovery_requires_actual_different_same_platform_boot(native_source, area, tools, kind):
    root = area / "state"
    root.mkdir(mode=0o700)
    boot = {
        "same": fs.boot.kernel_boot_identity(),
        "old": "linux:11111111-1111-4111-8111-111111111111",
        "other-platform": "windows:11111111-1111-4111-8111-111111111111",
    }[kind]
    path = root / fs.FILENAME
    path.write_text(json.dumps(document(boot_id=boot)))
    before = path.read_bytes()
    run_native(root, native_source, "recover", tools, success=kind == "old")
    if kind != "old":
        assert path.read_bytes() == before
    else:
        value = fs.read(root)
        assert value.state == "interrupted" and not value.fenced and value.exit_code is None
        assert value.boot_id == boot and value.settled_boot_id == fs.boot.kernel_boot_identity()
        assert value.operation_id == "a" * 32 and value.source_hash == "b" * 64


def test_reader_status_is_read_only_and_absence_does_not_create(native_source, area, tools):
    root = area / "absent" / "nested"
    result = run_native(root, native_source, "read", tools)
    assert not root.exists() and not result.stdout
    file_parent = area / "file-parent"
    file_parent.write_text("not a directory")
    run_native(file_parent / "state", native_source, "read", tools, success=False)
    assert file_parent.read_text() == "not a directory"


@pytest.mark.parametrize("continuous", [False, True])
def test_status_retries_only_bounded_actual_snapshot_replacement(native_source, area, tools, continuous):
    root = area / "state"
    root.mkdir(mode=0o700)
    initial = document()
    selected = document(operation_id="c" * 32)
    (root / fs.FILENAME).write_text(json.dumps(initial))
    (root / "replacement.json").write_text(json.dumps(selected))
    mode = "replace-always" if continuous else "replace-once"
    result = run_native(root, native_source, mode, tools, success=not continuous)
    count = len((root / "replacements").read_text().splitlines())
    if continuous:
        assert "repeated observation" in result.stderr
        assert 3 <= count <= 6
    else:
        assert json.loads(result.stdout) == selected and count == 1
    assert not (root / "queue.sqlite3").exists()


@pytest.mark.parametrize("mode", ["reconcile", "recover", "near-limit-complete"])
def test_oversized_mutation_preserves_readable_near_limit_progress(native_source, area, tools, mode):
    root = area / "state"
    root.mkdir(mode=0o700)
    value = document(
        started_at="STAMP",
        boot_id="linux:11111111-1111-4111-8111-111111111111",
    )
    raw = json.dumps(value, separators=(",", ":")).encode()
    padding = 32760 - len(raw.replace(b'"STAMP"', b"0.1"))
    raw = raw.replace(b'"STAMP"', b"0." + b"0" * padding + b"1")
    assert len(raw) == 32760
    path = root / fs.FILENAME
    path.write_bytes(raw)
    original = fs.read(root)
    run_native(root, native_source, "read", tools)
    result = run_native(root, native_source, mode, tools, success=False)
    assert "encoded progress exceeds size bound" in result.stderr
    assert path.read_bytes() == raw and fs.read(root) == original
    assert not list(root.glob(".foundation-operation-*.tmp"))


def test_native_reader_differential_protocol_corpus(native_source, area, tools):
    root = area / "state"
    root.mkdir(mode=0o700)
    corpus = root / "corpus"
    corpus.mkdir()
    base = document()
    candidates = [json.dumps(base).encode()]
    for key, value in [
        ("version", True), ("version", 1.0), ("version", 2), ("state", "pending"),
        ("operation_id", "A" * 32), ("source_hash", "b" * 63),
        ("boot_id", "linux:00000000-0000-0000-0000-000000000000"),
        ("state", []), ("started_at", None), ("started_at", True),
        ("started_at", float("nan")), ("started_at", float("inf")),
        ("started_at", 10**500), ("exit_code", 0), ("error", "invalid reason"),
        ("settled_at", 1), ("settled_boot_id", base["boot_id"]),
        ("schema", fs.SCHEMA + "\n"), ("schema", fs.SCHEMA + "\0"),
        ("state", "running\n"), ("state", "running\u0080"),
    ]:
        candidates.append(json.dumps(dict(base, **{key: value})).encode())
    raw = json.dumps(base).encode()
    candidates += [
        raw + b" " * (fs.MAX_BYTES - len(raw)),
        raw[:-1] + b', "version": 1}', raw[:-1] + b', "ver\\u0073ion": 1}',
        json.dumps(dict(base, extra="unknown")).encode(),
        json.dumps({k: v for k, v in base.items() if k != "error"}).encode(),
        b"[]", b"null", b"{", raw + b"{}", raw + b" " * fs.MAX_BYTES,
        b"\xef\xbb\xbf" + raw, raw + b"\0", raw.replace(b'"running"', b'"runn\xffing"'),
        raw.replace(b'"version": 1', b'"version": 01'),
        raw.replace(b'"version": 1', b'"version": +1'),
        raw.replace(b'"version": 1', b'"version": /* comment */ 1'),
        raw.replace(b'"version"', b'"ver\\u0073ion"'),
        raw.replace(b'"running"', b'"runn\\u0069ng"'),
    ]
    for timestamp in [1.1234567890123457, 9223372036854775809, 10**100 + 1, -0.0, 1.7976931348623157e308]:
        candidates.append(json.dumps(dict(base, started_at=timestamp)).encode())
    for code in [-(1 << 63), (1 << 63) - 1, -(1 << 63) - 1, 1 << 63, -7.0, True]:
        candidates.append(json.dumps(dict(
            base, state="failed", exit_code=code, error="builder_failed", finished_at=2,
        )).encode())
    for state, code, reason in [
        ("completed", 0, None), ("failed", -7, "builder_failed"),
        ("interrupted", None, "owner_lost_custody"),
    ]:
        candidates.append(json.dumps(dict(
            base, state=state, exit_code=code, error=reason, finished_at=2,
        )).encode())
    candidates.append(json.dumps(dict(
        base, state="interrupted", error="owner_lost_custody", finished_at=2,
        settled_boot_id="linux:11111111-1111-4111-8111-111111111111", settled_at=3,
    )).encode())
    expected = {}
    for index, content in enumerate(candidates):
        name = f"{index:03}.json"
        (corpus / name).write_bytes(content)
        (root / fs.FILENAME).write_bytes(content)
        try:
            expected[name] = fs.read(root)
        except fs.FoundationStateError:
            expected[name] = None
    results = run_native(root, native_source, "protocol", tools)
    verdicts = dict(line.split() for line in results.stdout.splitlines())
    assert len(verdicts) == len(candidates)
    for name, operation in expected.items():
        assert (verdicts[name] == "yes") == (operation is not None), name
        if operation is not None:
            accepted = json.loads((corpus / name.replace(".json", ".native")).read_text())
            assert fs._validate(accepted) == operation
