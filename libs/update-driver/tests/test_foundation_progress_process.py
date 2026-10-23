"""Stock PowerShell produces the same bounded progress without a Python child."""

import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

import agent_procutil as pu
from update_driver import InterruptedError, RequestSpec, TrustedRunner, enqueue, foundation_status
from update_driver import foundation_state as fs
from update_driver._directory import create_queue_directory
from update_driver.foundation_inputs import PACKAGES, SUPPORT, inspect

pytestmark = pytest.mark.skipif(os.name != "nt", reason="stock Windows native foundation progress")


@pytest.fixture
def native_source(area):
    common = area / "source"
    for directory, (name, dependencies) in PACKAGES.items():
        package = common / directory
        package.mkdir(parents=True)
        lines = [
            "[project]", f"name = {json.dumps(name)}", 'version = "1.0.0"',
            "dependencies = " + json.dumps(sorted(dependencies)), "[tool.uv.sources]",
        ]
        for dependency in sorted(dependencies):
            target = next(key for key, value in PACKAGES.items() if value[0] == dependency)
            lines.append(f'{dependency} = {{ path = "../{target}", editable = true }}')
        (package / "pyproject.toml").write_text("\n".join(lines), encoding="utf-8")
        (package / "source.py").write_text("pass\n", encoding="utf-8")
    plugin = common / "update-driver"
    (plugin / "scripts").mkdir()
    for name in SUPPORT:
        (plugin / "scripts" / name).write_text("# fixture\n", encoding="utf-8")
    # The native inventory must match Python's exclusion and case-sensitive sorting.
    (plugin / "__pycache__").mkdir()
    (plugin / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")
    (plugin / "Z.py").write_text("Z\n", encoding="utf-8")
    (plugin / "a.py").write_text("a\n", encoding="utf-8")
    (plugin / "\ue000.py").write_text("bmp\n", encoding="utf-8")
    (plugin / "\U00010000.py").write_text("astral\n", encoding="utf-8")
    return plugin


def command(root, plugin, mode):
    library = Path(__file__).resolve().parents[2]
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    fixture = Path(__file__).with_name("fixture_foundation_progress.ps1")
    return [
        str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
        str(root), str(plugin), str(library), mode,
    ]


def run_native(root, plugin, mode):
    env = dict(os.environ, PATH=str(Path(os.environ["SystemRoot"]) / "System32"))
    result = subprocess.run(
        command(root, plugin, mode), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=90, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_native_progress_roundtrip_matches_python_and_exact_source(native_source, area):
    root = area / "state"
    create_queue_directory(root)
    expected = inspect(native_source).digest
    run_native(root, native_source, "roundtrip")
    admitted = fs._validate(json.loads((root / "admitted.json").read_text()))
    assert admitted.state == "running" and admitted.source_hash == expected
    assert admitted.boot_id == fs.boot.kernel_boot_identity()
    assert fs._validate(json.loads((root / "observed.json").read_text())) == admitted
    for filename, state, code in [
        ("completed", "completed", 0), ("failed", "failed", 7),
        ("interrupted", "interrupted", None),
    ]:
        operation = fs._validate(json.loads((root / f"{filename}.json").read_text()))
        assert operation.state == state and operation.exit_code == code
    assert foundation_status(root=root).state == "interrupted"
    assert not (root / "queue.sqlite3").exists()
    assert not (root / "foundation").exists()
    assert not list(root.glob(".foundation-operation-*.tmp"))


def test_native_reader_differential_protocol_corpus(native_source, area):
    root = area / "state"
    create_queue_directory(root)
    corpus = root / "corpus"
    corpus.mkdir()
    base = {
        "schema": fs.SCHEMA, "version": fs.VERSION,
        **asdict(fs.FoundationOperation("a" * 32, "b" * 64, fs.boot.kernel_boot_identity(), "running", 1)),
    }
    candidates = [json.dumps(base)]
    for key, value in [
        ("version", True), ("version", 1.0), ("version", 2), ("state", "pending"),
        ("operation_id", "A" * 32), ("source_hash", "b" * 63),
        ("boot_id", "windows:00000000-0000-0000-0000-000000000000"),
        ("state", []), ("started_at", None), ("started_at", True),
        ("started_at", float("nan")), ("started_at", float("inf")),
        ("started_at", 10**500), ("exit_code", 0), ("error", "invalid reason"),
        ("settled_at", 1), ("settled_boot_id", base["boot_id"]),
    ]:
        candidates.append(json.dumps(dict(base, **{key: value})))
    candidates += [
        json.dumps(base)[:-1] + ', "version": 1}',
        json.dumps(dict(base, extra="unknown")),
        json.dumps({k: v for k, v in base.items() if k != "error"}),
        "[]", "null", "{", json.dumps(base) + "{}",
        json.dumps(base) + " " * fs.MAX_BYTES,
        "\ufeff" + json.dumps(base),
        json.dumps(base).replace('"version": 1', '"version": 01'),
        json.dumps(base).replace('"version": 1', '"version": +1'),
        json.dumps(base).replace('"version": 1', '"version": /* comment */ 1'),
        json.dumps(dict(base, __type="untrusted")),
        json.dumps(dict(base, __type={})),
        json.dumps(dict(base, __type=None)),
        '{"__type":"Example:#Example",' + json.dumps(base)[1:],
        '{"__type":"Example:#Example",' + json.dumps(dict(
            base, state="completed", exit_code=0, finished_at=2,
        ))[1:],
        json.dumps(base)[:-1] + ', "ver\\u0073ion": 1}',
    ]
    for timestamp in [1.1234567890123457, 9223372036854775809, 10**100 + 1]:
        candidates.append(json.dumps(dict(base, started_at=timestamp)))
    for state, code, reason in [
        ("completed", 0, None), ("failed", -7, "builder_failed"),
        ("interrupted", None, "owner_lost_custody"),
    ]:
        candidates.append(json.dumps(dict(
            base, state=state, exit_code=code, error=reason, finished_at=2,
        )))
    expected = {}
    for index, raw in enumerate(candidates):
        filename = f"{index:03}.json"
        (corpus / filename).write_bytes(raw.encode("utf-8"))
        (root / fs.FILENAME).write_bytes(raw.encode("utf-8"))
        try:
            expected[filename] = fs.read(root)
        except fs.FoundationStateError:
            expected[filename] = None
    run_native(root, native_source, "protocol")
    results = json.loads((root / "report.json").read_text())
    assert len(results) == len(candidates)
    for result in results:
        operation = expected[result["name"]]
        assert result["accepted"] == (operation is not None), result
        if operation is not None:
            assert fs._validate(json.loads(result["document"])) == operation


@pytest.mark.parametrize("filename", [
    "queue.sqlite3", "queue.sqlite3-wal", "queue.sqlite3-shm", "queue.sqlite3-journal",
])
def test_native_cold_admission_preserves_existing_queue_bytes(native_source, area, filename):
    root = area / "state"
    create_queue_directory(root)
    path = root / filename
    path.write_bytes(b"existing queue must never be interpreted, removed or replaced")
    before = path.read_bytes()
    run_native(root, native_source, "refuse")
    assert "queue-aware" in (root / "error.txt").read_text()
    assert path.read_bytes() == before and not (root / fs.FILENAME).exists()
    assert not (root / "foundation").exists()


def test_native_admission_preserves_real_pending_and_unsettled_requests(native_source, area):
    root = area / "state"
    for state in ("pending", "running", "interrupted", "blocked"):
        queued = enqueue(
            RequestSpec("native-existing-queue", argv=(state,)), root=root,
            runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
        )
        # Populate all states together without trying to execute the fixture commands.
        # Enqueue is done before installing the synthetic unsettled rows below.
        assert queued.state == "pending"
    with sqlite3.connect(root / "queue.sqlite3") as db:
        for row in db.execute("SELECT id,spec FROM requests").fetchall():
            state = json.loads(row[1])["argv"][0]
            db.execute("UPDATE requests SET state=? WHERE id=?", (state, row[0]))
    before = (root / "queue.sqlite3").read_bytes()
    run_native(root, native_source, "refuse")
    assert "queue-aware" in (root / "error.txt").read_text()
    assert (root / "queue.sqlite3").read_bytes() == before
    assert not (root / fs.FILENAME).exists()


def test_native_admission_refuses_lost_progress_and_missing_source_support(native_source, area):
    root = area / "state"
    create_queue_directory(root)
    (root / "foundation").mkdir()
    run_native(root, native_source, "refuse")
    assert "progress missing" in (root / "error.txt").read_text()
    assert not (root / fs.FILENAME).exists()
    (root / "foundation").rmdir()
    (native_source / "scripts" / SUPPORT[0]).unlink()
    run_native(root, native_source, "refuse")
    assert not (root / fs.FILENAME).exists()
    assert not (root / "foundation").exists()


@pytest.mark.parametrize("kind", ["source", "progress"])
def test_native_admission_refuses_linked_inputs(native_source, area, kind):
    root = area / "state"
    create_queue_directory(root)
    link = native_source / "source.py" if kind == "source" else root / fs.FILENAME
    target = area / "ordinary-target"
    target.write_bytes(link.read_bytes() if link.exists() else b'{"untrusted":"progress"}')
    link.unlink(missing_ok=True)
    try:
        link.symlink_to(target)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("native file symlink privilege unavailable")
        raise
    run_native(root, native_source, "refuse")
    assert "unlinked" in (root / "error.txt").read_text()
    assert link.is_symlink()
    assert not (root / "foundation").exists()


def test_actual_native_producer_loss_fences_pending_work(native_source, area):
    root = area / "state"
    create_queue_directory(root)
    with (root / "native.log").open("w") as log:
        parent, job = pu.spawn_sync_in_kill_on_close_job(
            command(root, native_source, "hold"), stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **pu.no_window_kwargs(),
        )
        try:
            deadline = time.monotonic() + 60
            while not (root / "native-ready").exists():
                assert parent.poll() is None, (root / "native.log").read_text()
                assert time.monotonic() < deadline, (root / "native.log").read_text()
                time.sleep(.02)
            admitted = foundation_status(root=root)
            before = (root / fs.FILENAME).read_bytes()
            contender = subprocess.run(
                command(root, native_source, "refuse"), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=90, **pu.no_window_kwargs(),
            )
            assert contender.returncode != 0, "another native caller acquired live execution custody"
            assert (root / fs.FILENAME).read_bytes() == before
            queued = enqueue(
                RequestSpec("native-producer-fixture"), root=root,
                runner=TrustedRunner((sys.executable, "-c", "pass"), str(root)),
            )
            assert queued.state == "pending"
            parent.kill()
            parent.wait(timeout=5)
            with pytest.raises(InterruptedError, match="foundation"):
                enqueue(
                    RequestSpec("native-producer-later"), root=root,
                    runner=TrustedRunner((sys.executable, "-c", "pass"), str(root)),
                )
            interrupted = foundation_status(root=root)
            assert interrupted.operation_id == admitted.operation_id
            assert interrupted.state == "interrupted" and interrupted.exit_code is None
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait(timeout=5)
            if job is not None:
                job.close()
