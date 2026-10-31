"""Native upgrade boundaries with controlled construction, not installed-package proof."""

import os
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest

import agent_procutil as pu
from single_instance_lease.operation import OperationLock
from update_driver import (
    RequestSpec, TrustedRunner, enqueue, foundation_state as fs, foundation_status, status, wait,
)
from update_driver.foundation_inputs import PACKAGES, inspect

import test_foundation_producer_process as producer_fixtures

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual native Windows upgrade custody")
producer_source = producer_fixtures.producer_source
command = producer_fixtures.command


def installed_runtime(producer_source, area, target, version):
    installed = area / "retained-inputs"
    shutil.copytree(producer_source.parent, installed)
    slot = target / "versions" / version
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "venv", "--copies", "--without-pip", str(slot)],
        capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    # The fixture explicitly supplies source mapping; hosted qualification uses real retained packages.
    (bootstrap / "runtime_launcher.py").write_text(
        "import runpy,sys\n"
        f"sys.path[:0] = {[str(installed / name / 'src') for name in PACKAGES]!r}\n"
        "assert sys.argv[1] == '-m'\n"
        "module = sys.argv[2]\n"
        "sys.argv = sys.argv[2:]\n"
        "runpy.run_module(module, run_name='__main__')\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(
        '{"version": "' + version + '", "completed_at": "fixture", "pid": 0}',
    )
    (target / "current-version").write_text(slot.name)
    return slot, installed


@pytest.fixture
def retained(producer_source, area):
    root = area / "state"
    root.mkdir()
    digest = inspect(producer_source).digest
    with OperationLock(root / "execution.lock") as owned:
        admitted = fs.begin(root, digest, owned)
        fs.finish(root, admitted, owned, exit_code=0)
    slot, installed = installed_runtime(
        producer_source, area, root / "foundation", "foundation-" + digest,
    )
    (producer_source / "upgrade.txt").write_text("different source, same package version")
    return root, slot, installed


@pytest.fixture
def product_runtime(producer_source, area, request):
    root = area / "state"
    root.mkdir()
    slot, installed = installed_runtime(
        producer_source, area, area / "product", getattr(request, "param", "1.0.0"),
    )
    return root, slot, installed


def worker_command(slot, root):
    return [
        str(slot / "Scripts" / "python.exe"), "-I", "-S", "-B", "-X", "utf8",
        str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
        "-m", "update_driver", "worker", "--root", str(root),
    ]


def logs(root):
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")[-12000:]
        for path in root.glob("*.log")
    )


def observe(predicate, root, timeout=90):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, logs(root)
        time.sleep(.02)


@pytest.mark.parametrize("product_runtime", [
    "1.0.0", "1.24.271-dev123456+source." + "a" * 64 + ".repair." + "b" * 32,
], indirect=True)
def test_first_foundation_waits_for_custody_and_preserves_pending_queue(
    producer_source, product_runtime,
):
    root, slot, _installed = product_runtime
    request = enqueue(
        RequestSpec("first-foundation-pending"), root=root,
        runner=TrustedRunner(
            (sys.executable, "-c", "from pathlib import Path; Path('unexpected-runner').touch()"),
            str(root),
        ),
    )
    before = (root / "queue.sqlite3").read_bytes()
    selector = slot.parent.parent / "current-version"
    selection = selector.read_bytes()
    env = dict(os.environ, FOUNDATION_FIXTURE_PYTHON=sys.executable)
    with (root / "processes.log").open("w") as log:
        process = None
        try:
            with OperationLock(root / "execution.lock"):
                process = subprocess.Popen(
                    [*command(producer_source, root), "-AdmissionRuntimeRoot", str(slot.parent.parent)],
                    env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    **pu.no_window_kwargs(),
                )
                time.sleep(2)
                assert process.poll() is None, logs(root)
                assert not (root / fs.FILENAME).exists()
                assert not (root / "prerequisite-started").exists()
            assert process.wait(timeout=90) == 0, logs(root)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=15)
    assert foundation_status(root=root).state == "completed"
    assert status(request.id, root=root) == request
    assert (root / "queue.sqlite3").read_bytes() == before
    assert selector.read_bytes() == selection
    assert not (root / "unexpected-runner").exists()
    assert (root / "builder-finished").exists()


@pytest.mark.parametrize("case", [
    "no-selection", "foreign-selection", "incomplete", "missing-admission", "protocol",
    "schema", "interrupted", "foundation-artifacts", "foundation-record",
])
def test_first_foundation_refuses_unsupported_runtime_or_history_before_acquisition(
    producer_source, product_runtime, case,
):
    root, slot, installed = product_runtime
    request = enqueue(
        RequestSpec("first-foundation-refusal"), root=root,
        runner=TrustedRunner((sys.executable, "-c", "raise SystemExit(99)"), str(root)),
    )
    runtime = slot.parent.parent
    if case == "no-selection":
        (runtime / "current-version").unlink()
        (runtime / "last-known-good").write_text(slot.name)
    elif case == "foreign-selection":
        (runtime / "current-version").write_text("..\\foreign")
    elif case == "incomplete":
        (slot / ".install-complete.json").write_text("{}")
    elif case == "missing-admission":
        (installed / "update-driver" / "src" / "update_driver" / "foundation_admission.py").unlink()
    elif case == "protocol":
        core = installed / "update-driver" / "src" / "update_driver" / "core.py"
        content = core.read_text()
        assert "SCHEMA = 4" in content
        core.write_text(content.replace("SCHEMA = 4", "SCHEMA = 999", 1))
    elif case == "schema":
        with sqlite3.connect(root / "queue.sqlite3") as db:
            db.execute("PRAGMA user_version=999")
    elif case == "interrupted":
        with sqlite3.connect(root / "queue.sqlite3") as db:
            db.execute("UPDATE requests SET state='interrupted'")
    elif case == "foundation-artifacts":
        (root / "foundation").mkdir()
    elif case == "foundation-record":
        with OperationLock(root / "execution.lock") as owned:
            admitted = fs.begin(root, inspect(producer_source).digest, owned)
            fs.finish(root, admitted, owned, exit_code=7, error="fixture_failure")
    before = (root / "queue.sqlite3").read_bytes()
    progress = (root / fs.FILENAME).read_bytes() if (root / fs.FILENAME).exists() else None
    result = subprocess.run(
        [*command(producer_source, root), "-AdmissionRuntimeRoot", str(runtime)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90,
        **pu.no_window_kwargs(),
    )
    assert result.returncode == 125, result.stdout + result.stderr + logs(root)
    assert (root / "queue.sqlite3").read_bytes() == before
    assert ((root / fs.FILENAME).read_bytes() if (root / fs.FILENAME).exists() else None) == progress
    if case not in {"schema", "interrupted", "foundation-artifacts"}:
        assert status(request.id, root=root) == request
    assert not (root / "bootstrap").exists()
    assert not (root / "prerequisite-started").exists()


def test_product_admission_never_reuses_an_existing_matching_foundation(producer_source, retained):
    root, slot, _installed = retained
    (producer_source / "upgrade.txt").unlink()
    before = (root / fs.FILENAME).read_bytes()
    result = subprocess.run(
        [*command(producer_source, root), "-AdmissionRuntimeRoot", str(slot.parent.parent)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90,
        **pu.no_window_kwargs(),
    )
    assert result.returncode == 125, result.stdout + result.stderr + logs(root)
    assert "only supported before the first retained foundation" in result.stderr
    assert (root / fs.FILENAME).read_bytes() == before
    assert not (root / "prerequisite-started").exists()


@pytest.mark.parametrize("case", ["legacy-empty", "legacy-pending", "running", "interrupted", "blocked", "schema"])
def test_native_upgrade_refuses_legacy_or_unsettled_queue_without_mutation(
    producer_source, retained, case,
):
    root, _slot, installed = retained
    if case == "legacy-empty" or case == "legacy-pending":
        (installed / "update-driver" / "src" / "update_driver" / "foundation_admission.py").unlink()
    enqueue(
        RequestSpec("upgrade-refusal-fixture"), root=root,
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(root)),
    )
    with sqlite3.connect(root / "queue.sqlite3") as db:
        if case == "legacy-empty":
            db.execute("DELETE FROM requests")
        elif case == "schema":
            db.execute("PRAGMA user_version=999")
        elif case in {"running", "interrupted", "blocked"}:
            db.execute("UPDATE requests SET state=?", (case,))
    before = (root / "queue.sqlite3").read_bytes()
    progress = (root / fs.FILENAME).read_bytes()
    selector = (root / "foundation" / "current-version").read_bytes()
    result = subprocess.run(
        command(producer_source, root), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=90, **pu.no_window_kwargs(),
    )
    assert result.returncode == 125, result.stdout + result.stderr + logs(root)
    assert "Queue-aware foundation admission unavailable" in result.stderr
    assert (root / "queue.sqlite3").read_bytes() == before
    assert (root / fs.FILENAME).read_bytes() == progress
    assert (root / "foundation" / "current-version").read_bytes() == selector
    assert not (root / "prerequisite-started").exists()
    assert not (root / "bootstrap").exists()


def test_native_upgrade_waits_for_active_request_and_preserves_retained_pending_worker(
    producer_source, retained,
):
    root, slot, _installed = retained
    script = (
        "from pathlib import Path; import time\n"
        f"root=Path({str(root)!r})\n"
        "(root/'active-started').write_text('running')\n"
        "deadline=time.monotonic()+90\n"
        "while not (root/'release-active').exists():\n"
        " assert time.monotonic()<deadline\n"
        " time.sleep(.02)\n"
        "(root/'active-finished').write_text('once')\n"
        "raise SystemExit(7)\n"
    )
    active = enqueue(
        RequestSpec("upgrade-active-fixture"), root=root,
        runner=TrustedRunner((sys.executable, "-c", script), str(root)),
    )
    children = []
    env = dict(
        os.environ, FOUNDATION_FIXTURE_PYTHON=sys.executable, FOUNDATION_FIXTURE_HOLD="1",
    )
    with (root / "processes.log").open("w") as log:
        try:
            old_worker = subprocess.Popen(
                worker_command(slot, root), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, **pu.no_window_kwargs(),
            )
            children.append(old_worker)
            observe(lambda: (root / "active-started").exists(), root)
            previous = foundation_status(root=root)
            producer = subprocess.Popen(
                command(producer_source, root), env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, **pu.no_window_kwargs(),
            )
            children.append(producer)
            time.sleep(2)
            assert producer.poll() is None
            assert foundation_status(root=root) == previous
            assert not (root / "prerequisite-started").exists()
            (root / "release-active").touch()
            assert old_worker.wait(timeout=30) == 0, logs(root)
            assert wait(active.id, root=root, timeout=1).exit_code == 7
            observe(lambda: (root / "builder-started").exists(), root)
            admitted = foundation_status(root=root)
            assert admitted.state == "running" and admitted.operation_id != previous.operation_id
            assert admitted.source_hash == inspect(producer_source).digest
            pending = enqueue(
                RequestSpec("upgrade-pending-fixture"), root=root,
                runner=TrustedRunner((sys.executable, "-c", "raise SystemExit(9)"), str(root)),
            )
            retained_worker = subprocess.Popen(
                worker_command(slot, root), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, **pu.no_window_kwargs(),
            )
            children.append(retained_worker)
            time.sleep(1)
            assert retained_worker.poll() is None and status(pending.id, root=root).state == "pending"
            (root / "release-builder").touch()
            assert producer.wait(timeout=60) == 0, logs(root)
            assert retained_worker.wait(timeout=30) == 0, logs(root)
            result = wait(pending.id, root=root, timeout=1)
            assert result.state == "completed" and result.exit_code == 9
            assert status(active.id, root=root).exit_code == 7
            assert foundation_status(root=root).operation_id == admitted.operation_id
            assert foundation_status(root=root).state == "completed"
        finally:
            for process in children:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=15)
