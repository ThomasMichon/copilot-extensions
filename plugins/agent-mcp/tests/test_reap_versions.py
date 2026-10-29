"""MCP retirement policy and actual native pinned-tree settlement."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("reap_versions_under_test", SCRIPTS / "reap_versions.py")
rv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rv)


def _row(pid, parent, image, *, created=None):
    return {
        "pid": pid, "ppid": parent, "image": str(image), "argv": [str(image)],
        "command": str(image), "created": created if created is not None else pid,
    }


@pytest.fixture
def owners(tmp_path, monkeypatch):
    rows = {
        os.getpid(): _row(os.getpid(), 0, sys.executable, created=1),
        111: _row(111, os.getpid(), tmp_path / "versions/1.0/bin/python"),
        112: _row(112, 111, tmp_path / "upstream"),
        222: _row(222, os.getpid(), tmp_path / "versions/2.0/bin/python"),
    }
    state = {
        "rows": rows, "paused": [], "terminated": [], "resumed": [], "exited": set(),
        "custodies": 0,
    }

    class Pin:
        def __init__(self, pid, birth):
            self.pid, self.birth = pid, birth

        def __enter__(self):
            return self

        def __exit__(self, *_):
            if not self.exited():
                state["resumed"].append(self.pid)

        def exited(self):
            return self.pid in state["terminated"] or self.pid in state["exited"]

        def pause(self):
            state["paused"].append(self.pid)

        def terminate(self):
            if not self.exited():
                state["terminated"].append(self.pid)

    def custody(root):
        state["custodies"] += 1
        return nullcontext(type("Custody", (), {"pin": staticmethod(Pin)})())

    monkeypatch.setattr(rv, "RetirementCustody", custody)
    state["pin_type"] = Pin
    monkeypatch.setattr(rv, "birth_identity", lambda pid: f"linux:boot:ns:{rows[pid]['created']}")
    monkeypatch.setattr(rv, "_inspect", lambda root: {
        pid: row for pid, row in rows.items() if pid not in state["terminated"]
    })
    monkeypatch.setattr(rv, "current_version", lambda *a: "2.0")
    monkeypatch.setattr(rv, "list_versions", lambda root: ["1.0", "2.0"])
    return state


def test_reap_settles_original_tree_and_spares_current_and_parent(tmp_path, owners):
    reaped = rv.reap_stale(tmp_path)
    assert owners["paused"] == [111, 112]
    assert owners["terminated"] == [111, 112]
    assert owners["resumed"] == []
    assert owners["custodies"] == 1
    assert reaped == [{"version": "1.0", "pid": 111, "terminated": True}]


def test_reap_exclusion_protects_descendants(tmp_path, owners):
    assert rv.reap_stale(tmp_path, exclude_pids={111}) == []
    assert owners["paused"] == []
    assert owners["custodies"] == 0


def test_fresh_recovery_cannot_call_a_retained_old_owner_settled(tmp_path, owners):
    with pytest.raises(rv.RetirementRefused, match="protected from retirement"):
        rv.reap_stale(tmp_path, exclude_pids={111}, fresh=True)
    assert owners["paused"] == []


@pytest.mark.parametrize("kind", ["current", "excluded", "transport"])
def test_protected_child_refuses_before_pausing(tmp_path, owners, kind):
    rows = owners["rows"]
    options = {}
    if kind == "current":
        rows[222]["ppid"] = 111
    elif kind == "excluded":
        options["exclude_pids"] = {112}
    else:
        rows[112]["image"] = rows[112]["argv"][0] = "ssh.exe"
    with pytest.raises(rv.RetirementRefused, match="protected"):
        rv.reap_stale(tmp_path, **options)
    assert owners["paused"] == owners["terminated"] == []


def test_pid_reuse_cannot_be_readmitted(tmp_path, owners, monkeypatch):
    monkeypatch.setattr(rv, "birth_identity", lambda pid: "linux:boot:ns:999")
    with pytest.raises(rv.RetirementRefused, match="identity changed"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == owners["terminated"] == []


def test_spawn_and_exit_before_pin_cannot_report_settlement(tmp_path, owners, monkeypatch):
    original = rv.birth_identity

    def birth(pid):
        if pid == 111:
            owners["rows"].pop(111)
            owners["rows"][112]["ppid"] = 0
            owners["rows"][333] = _row(333, 0, tmp_path / "orphaned upstream")
            raise ProcessLookupError(pid)
        return original(pid)

    monkeypatch.setattr(rv, "birth_identity", birth)
    with pytest.raises(rv.RetirementRefused, match="disappeared before descendant scope was frozen"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == owners["terminated"] == []
    assert 333 in owners["rows"]


def test_exited_pin_cannot_seed_a_reused_pids_children(tmp_path, owners, monkeypatch):
    original = rv._inspect

    def inspect(root):
        if owners["paused"]:
            owners["exited"].add(111)
            owners["rows"][111] = _row(111, 0, tmp_path / "unrelated replacement", created=500)
            owners["rows"][333] = _row(333, 111, tmp_path / "unrelated child", created=501)
        return original(root)

    monkeypatch.setattr(rv, "_inspect", inspect)
    with pytest.raises(rv.RetirementRefused, match="exited before descendant scope was confirmed"):
        rv.reap_stale(tmp_path)
    assert 333 not in owners["paused"]
    assert owners["terminated"] == []
    assert owners["resumed"] == [112]


@pytest.mark.parametrize("image", ["ssh.exe", "/usr/bin/ssh (deleted)"])
def test_pinned_exec_to_protected_transport_is_resumed_not_terminated(tmp_path, owners, monkeypatch, image):
    original = rv._inspect

    def inspect(root):
        if owners["paused"]:
            owners["rows"][111]["image"] = image
        return original(root)

    monkeypatch.setattr(rv, "_inspect", inspect)
    with pytest.raises(rv.RetirementRefused, match="protected"):
        rv.reap_stale(tmp_path)
    assert owners["terminated"] == []
    assert owners["resumed"] == [112, 111]


def test_missing_excluded_root_cannot_drop_its_descendant_protection(tmp_path, owners):
    owners["rows"][111]["ppid"] = 999
    with pytest.raises(rv.RetirementRefused, match="excluded process root is absent"):
        rv.reap_stale(tmp_path, exclude_pids={999})
    assert owners["paused"] == []


def test_incomplete_selected_image_refuses_before_pausing(tmp_path, owners):
    owners["rows"][111]["image"] = ""
    owners["rows"][111]["inspectionIncomplete"] = True
    with pytest.raises(rv.RetirementRefused, match="cannot be fully inspected"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == []


def test_incomplete_unrelated_image_is_not_retirement_authority_or_a_global_veto(tmp_path, owners):
    owners["rows"][333] = _row(333, 0, tmp_path / "unrelated")
    owners["rows"][333]["image"] = ""
    owners["rows"][333]["inspectionIncomplete"] = True
    records = rv.reap_stale(tmp_path)
    assert records == [{"version": "1.0", "pid": 111, "terminated": True}]
    assert owners["terminated"] == [111, 112]


def test_parent_pid_reuse_refuses_the_observed_tree(tmp_path, owners):
    owners["rows"][112]["created"] = 110
    with pytest.raises(rv.RetirementRefused, match="parent identity"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == []


def test_uninspectable_kernel_child_refuses_before_pausing(tmp_path, owners, monkeypatch):
    owners["rows"][111]["children"] = [999]
    original = rv.birth_identity
    monkeypatch.setattr(rv, "birth_identity", lambda pid: "linux:boot:ns:999" if pid == 999 else original(pid))
    with pytest.raises(rv.RetirementRefused, match="child cannot be inspected"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == []


def test_rooted_process_paths_cannot_traverse_a_foreign_version_directory(tmp_path, owners):
    directory = tmp_path / "versions"
    directory.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    try:
        (directory / "1.0").symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip("Native symlink creation unavailable")
    with pytest.raises(rv.inspection.UninstallRefused, match="Redirected"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == []


def test_exit_failure_resumes_remaining_originals(tmp_path, owners, monkeypatch):
    pin_type = owners["pin_type"]

    def fail(_):
        raise rv.RetirementRefused("Process exit was not confirmed")

    monkeypatch.setattr(pin_type, "terminate", fail)
    with pytest.raises(rv.RetirementRefused, match="exit was not confirmed"):
        rv.reap_stale(tmp_path)
    assert owners["resumed"] == [112, 111]
    assert owners["terminated"] == []


def test_child_discovered_after_parent_pause_is_retired(tmp_path, owners, monkeypatch):
    original = rv._inspect
    calls = []

    def inspect(root):
        calls.append(True)
        if len(calls) == 2:
            owners["rows"][113] = _row(113, 112, tmp_path / "late upstream")
        return original(root)

    monkeypatch.setattr(rv, "_inspect", inspect)
    rv.reap_stale(tmp_path)
    assert owners["paused"] == owners["terminated"] == [111, 112, 113]


def test_protected_late_child_resumes_without_termination(tmp_path, owners, monkeypatch):
    original = rv._inspect

    def inspect(root):
        if owners["paused"]:
            owners["rows"][113] = _row(113, 112, "copilot.exe")
        return original(root)

    monkeypatch.setattr(rv, "_inspect", inspect)
    with pytest.raises(rv.RetirementRefused, match="protected"):
        rv.reap_stale(tmp_path)
    assert owners["terminated"] == []
    assert owners["resumed"] == [112, 111]


def test_late_current_slot_child_is_not_paused_or_terminated(tmp_path, owners, monkeypatch):
    original = rv._inspect

    def inspect(root):
        if owners["paused"]:
            owners["rows"][333] = _row(333, 112, tmp_path / "versions/2.0/bin/python")
        return original(root)

    monkeypatch.setattr(rv, "_inspect", inspect)
    with pytest.raises(rv.RetirementRefused, match="protected"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == [111, 112]
    assert owners["terminated"] == []
    assert owners["resumed"] == [112, 111]


@pytest.mark.parametrize("when", ["initial", "after-pause"])
def test_current_image_cannot_be_hidden_by_stale_argv0(tmp_path, owners, monkeypatch, when):
    original = rv._inspect

    def inspect(root):
        if when == "initial" or owners["paused"]:
            owners["rows"][111]["image"] = str(tmp_path / "versions/2.0/bin/python")
        return original(root)

    monkeypatch.setattr(rv, "_inspect", inspect)
    if when == "initial":
        assert rv.reap_stale(tmp_path) == []
        assert owners["paused"] == []
    else:
        with pytest.raises(rv.RetirementRefused, match="protected"):
            rv.reap_stale(tmp_path)
        assert owners["resumed"] == [112, 111]
    assert owners["terminated"] == []


@pytest.mark.parametrize("name", [".venv", "venv", "tool"])
def test_fresh_recovery_settles_known_legacy_without_old_receipt(tmp_path, owners, name):
    owners["rows"][111]["argv"] = [str(tmp_path / name / "bin/python")]
    owners["rows"][111]["image"] = sys.executable
    assert rv.reap_stale(tmp_path) == []
    assert rv.reap_stale(tmp_path, fresh=True)[0]["version"] == "legacy"
    assert owners["terminated"] == [111, 112]


@pytest.mark.parametrize("declared", [False, True])
def test_fresh_process_inventory_follows_the_maintained_manifest(tmp_path, owners, monkeypatch, declared):
    original = rv.footprint.parse

    def parse(text, plugin):
        manifest = original(text, plugin)
        effect = {
            **manifest.effects["legacy-runtime-processes"],
            "entries": ["new-declared-code"] if declared else [],
        }
        return replace(manifest, effects={**manifest.effects, "legacy-runtime-processes": effect})

    monkeypatch.setattr(rv.footprint, "parse", parse)
    owners["rows"][111]["argv"] = [str(tmp_path / "new-declared-code/python")]
    owners["rows"][111]["image"] = sys.executable
    records = rv.reap_stale(tmp_path, fresh=True)
    assert bool(records) is declared
    assert owners["terminated"] == ([111, 112] if declared else [])


def test_explicit_no_reap_cannot_claim_old_owner_silence(tmp_path, owners):
    with pytest.raises(rv.RetirementRefused, match="explicitly disabled"):
        rv.reap_stale(tmp_path, fresh=True, check_only=True)
    assert owners["paused"] == []
    del owners["rows"][111]
    assert rv.reap_stale(tmp_path, fresh=True, check_only=True) == []


def test_idle_fresh_reaper_does_not_require_current_owner_retirement_custody(tmp_path, owners):
    del owners["rows"][111]
    owners["rows"][222]["children"] = [999]
    assert rv.reap_stale(tmp_path, fresh=True) == []
    assert owners["paused"] == []
    assert owners["custodies"] == 0


def test_missing_current_is_not_silently_reported_as_settled(tmp_path, owners, monkeypatch):
    monkeypatch.setattr(rv, "current_version", lambda *a: None)
    with pytest.raises(rv.RetirementRefused, match="installer-selected"):
        rv.reap_stale(tmp_path)
    assert owners["paused"] == []


def test_reap_matches_normalized_current(tmp_path, owners, monkeypatch):
    monkeypatch.setattr(rv, "current_version", lambda *a: "1-0")
    assert rv.reap_stale(tmp_path)[0]["pid"] == 222
    assert owners["terminated"] == [222]


@pytest.mark.parametrize(
    "state,threads,later_threads,later_birth,flags,failure",
    [("Z", 1, 1, 200, 0, None), ("Z", 2, 2, 200, 0, None),
     ("Z", 1, 2, 200, 0, None), ("R", 1, 1, 200, 0, None),
     ("Z", 1, 1, 201, 0, None), ("S", 1, 1, 200, 0x00200000, None),
     ("S", 1, 1, 201, 0x00200000, None), ("S", 1, 1, 200, 0x00000040, None),
     ("Z", 2, 2, 200, 0, "image"), ("Z", 1, 1, 200, 0, "image"),
     ("Z", 1, 1, 201, 0, "image"), ("Z", 2, 2, 200, 0, "cmdline"),
     ("Z", 2, 2, 200, 0, "identity")],
)
def test_linux_empty_command_census_requires_confirmed_group_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, threads: int,
    later_threads: int, later_birth: int, flags: int, failure: str | None,
) -> None:
    proc = tmp_path / "proc"

    def stat(pid: int, status: str, count: int, birth: int) -> str:
        fields = [status, "0", *(["0"] * 15), str(count), "0", str(birth)]
        fields[6] = str(flags if pid == 200 else 0)
        return f"{pid} (fixture) " + " ".join(fields)

    for pid, command in ((100, b"inspector\0"), (200, b"")):
        directory = proc / str(pid)
        (directory / "task").mkdir(parents=True)
        (directory / "stat").write_text(
            stat(pid, "R" if pid == 100 else state, 1 if pid == 100 else threads, pid),
        )
        (directory / "cmdline").write_bytes(b"runtime\0" if pid == 200 and failure else command)
    original_read = Path.read_text
    reads = 0

    def read(path: Path, *args, **kwargs) -> str:
        nonlocal reads
        if path == proc / "200/stat":
            reads += 1
            if reads == 2:
                if failure == "identity":
                    raise FileNotFoundError(path)
                return stat(200, state, later_threads, later_birth)
        return original_read(path, *args, **kwargs)

    original_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if failure == "cmdline" and path == proc / "200/cmdline":
            raise FileNotFoundError(path)
        return original_bytes(path)

    def readlink(path: Path) -> str:
        if failure == "image" and path == proc / "200/exe":
            raise FileNotFoundError(path)
        return "/usr/bin/python"

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(rv.inspection, "Path", lambda path: proc if str(path) == "/proc" else Path(path))
    monkeypatch.setattr(rv.inspection, "os", SimpleNamespace(
        name="posix", getuid=lambda: proc.stat().st_uid, getpid=lambda: 100,
        fsdecode=os.fsdecode, readlink=readlink,
    ))
    monkeypatch.setattr(rv.inspection, "sys", SimpleNamespace(platform="linux"))

    def census() -> list[dict]:
        return rv.inspection.inspect_processes(SCRIPTS.parent, tmp_path, "", None, descendants=True)

    if later_birth != 200:
        with pytest.raises(rv.inspection.UninstallRefused, match="identity changed"):
            census()
    elif not (flags & 0x00200000) and (state != "Z" or later_threads != 1):
        with pytest.raises(rv.inspection.UninstallRefused, match="live tasks"):
            census()
    else:
        assert [row["pid"] for row in census()] == [100]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Native Linux thread-group census")
@pytest.mark.parametrize("exit_phase", ["before-census", "during-image-read"])
def test_real_dead_main_thread_cannot_hide_a_live_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_phase: str,
) -> None:
    interpreter = tmp_path / "versions/1.0/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(getattr(sys, "_base_executable", sys.executable))
    process = subprocess.Popen(
        [str(interpreter), "-c",
         "import ctypes,sys,threading,time\n"
         "threading.Thread(target=lambda:time.sleep(60)).start()\n"
         "print('ready',flush=True)\n"
         "sys.stdin.read(1)\n"
         "ctypes.CDLL(None).pthread_exit(None)\n"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        rows = rv._inspect(tmp_path)
        assert rv._version(rows[process.pid], tmp_path, {"1.0", "2.0"}) == "1.0"
        proc = Path(f"/proc/{process.pid}")

        def exit_main() -> None:
            process.stdin.write("x")
            process.stdin.flush()
            deadline = time.monotonic() + 5
            while (proc / "cmdline").read_bytes() and time.monotonic() < deadline:
                time.sleep(.005)
            assert (proc / "cmdline").read_bytes() == b""
            fields = (proc / "stat").read_text().rsplit(") ", 1)[1].split()
            assert fields[0] == "Z" and int(fields[17]) > 1
            assert process.poll() is None

        if exit_phase == "before-census":
            exit_main()
        else:
            original_readlink = os.readlink

            def readlink(path: Path) -> str:
                if path == proc / "exe":
                    exit_main()
                return original_readlink(path)

            monkeypatch.setattr(rv.inspection.os, "readlink", readlink)
        monkeypatch.setattr(rv, "current_version", lambda *a: "2.0")
        with pytest.raises(rv.inspection.UninstallRefused, match="live tasks"):
            rv.reap_stale(tmp_path, fresh=True)
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


def test_real_tree_exit_spares_original_parent(tmp_path):
    if os.name != "nt" and not sys.platform.startswith("linux"):
        pytest.skip("External identity-pinned retirement requires Windows or Linux")
    from agent_procutil._daemon_instance_identity import birth_identity, identity_state

    src = (
        "import os,subprocess,sys,time\n"
        "child='import ctypes,sys,time\\n"
        "if sys.platform.startswith(\"linux\"): assert ctypes.CDLL(None).prctl(15,b\"worker-\\\\xff\",0,0,0)==0\\n"
        "print(\"ready\",flush=True)\\n"
        "time.sleep(60)'\n"
        "c=subprocess.Popen([sys.executable,'-c',child],"
        "stdout=subprocess.PIPE,text=True,"
        "creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)\n"
        "assert c.stdout.readline().strip()=='ready'\n"
        "c.stdout.close()\n"
        "print(c.pid,flush=True)\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-c", src, str(tmp_path)],
        stdout=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    child = None
    try:
        child = int(process.stdout.readline())
        child_birth = birth_identity(child)
        rows = rv._inspect(tmp_path)
        assert process.pid in rows and child in rows
        expected = rv._descendants(rows, {process.pid})
        auxiliaries = expected - {process.pid, child}
        assert all(Path(rows[pid]["image"]).name.casefold() == "conhost.exe" for pid in auxiliaries)
        births = {pid: rv._birth(rows[pid]) for pid in expected}
        retired = rv._retire(
            tmp_path, rows, {process.pid}, {os.getpid()}, check_only=False,
        )
        assert retired == expected
        assert process.wait(timeout=5) is not None
        assert all(identity_state(pid, birth) in {"dead", "reused"} for pid, birth in births.items())
        assert birth_identity(os.getpid())
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child is not None and "child_birth" in locals():
            from agent_procutil._daemon_instance_identity import stop_identity
            if identity_state(child, child_birth) == "matching":
                stop_identity(child, child_birth)
        process.stdout.close()
