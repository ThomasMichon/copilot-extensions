"""Native shell failures under real construction and cleanup ownership."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SOURCE = Path(os.environ.get(
    "VERSIONED_RUNTIME_TEST_SOURCE",
    Path(__file__).resolve().parents[1] / "scripts" / "versioned_runtime.py",
))
BASH = os.environ.get("VERSIONED_RUNTIME_TEST_BASH", "bash")
spec = importlib.util.spec_from_file_location("failed_runtime", SOURCE)
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)


def shell(slot, code):
    if os.name == "nt":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        assert executable, "native PowerShell is required"
        path = str(slot / "partial").replace("'", "''")
        return [
            executable, "-NoProfile", "-NonInteractive", "-Command",
            f"[IO.File]::WriteAllText('{path}', 'partial'); exit {code}",
        ]
    return [
        BASH, "-c", 'printf partial > "$1/partial"; exit "$2"',
        "dependency", str(slot), str(code),
    ]


def run_builder(root, code, *, wait=False):
    body = (
        "import importlib.util, pathlib, subprocess, sys, os\n"
        f"s=importlib.util.spec_from_file_location('vr', {str(SOURCE)!r})\n"
        "vr=importlib.util.module_from_spec(s); s.loader.exec_module(vr)\n"
        f"root=pathlib.Path({str(root)!r})\n"
        "with vr.construction_lease(root, '2.0'):\n"
        " slot=vr.slot(root, '2.0', clean_incomplete=True)\n"
    )
    if wait:
        body += " print('owned', flush=True)\n input()\n"
    body += (
        f" result=subprocess.run({shell(root / 'versions' / '2.0', code)!r}, "
        "creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)\n"
        " if result.returncode:\n"
        "  vr.discard_failed_candidate(root, '2.0')\n"
        "  sys.exit(result.returncode)\n"
        " vr.mark_complete(root, '2.0', payload_hash='same')\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", body], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.fixture
def processes():
    owned = []
    yield owned
    for process in owned:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.guard
@pytest.mark.parametrize("previous", [False, True])
def test_failed_construction_native_shell_failure_and_retry(tmp_path, previous, processes):
    if previous:
        old = vr.slot(tmp_path, "1.0")
        (old / "live").write_text("old")
        vr.mark_complete(tmp_path, "1.0", payload_hash="old")
        vr.activate(tmp_path, "1.0")
    before = vr.current_version(tmp_path)
    failed = run_builder(tmp_path, 23)
    processes.append(failed)
    stdout, stderr = failed.communicate(timeout=15)
    assert failed.returncode == 23, (stdout, stderr)
    assert not vr.version_dir(tmp_path, "2.0").exists()
    assert vr.current_version(tmp_path) == before
    retry = run_builder(tmp_path, 0)
    processes.append(retry)
    stdout, stderr = retry.communicate(timeout=15)
    assert retry.returncode == 0, (stdout, stderr)
    assert vr.check_admission(tmp_path, "2.0", payload_hash="same") == vr.ADMIT_REUSE
    assert vr.current_version(tmp_path) == before
    if previous:
        assert (old / "live").read_text() == "old"


def test_failed_construction_contention_and_process_death(tmp_path, processes):
    process = run_builder(tmp_path, 23, wait=True)
    processes.append(process)
    assert process.stdout.readline().strip() == "owned"
    with pytest.raises(vr.ConstructionTimeout):
        with vr.construction_lease(tmp_path, "2.0", timeout=0):
            pytest.fail("a second owner was admitted")
    assert vr.toss_incomplete(tmp_path) == []
    process.kill()
    process.communicate(timeout=10)
    with vr.construction_lease(tmp_path, "2.0", timeout=1):
        assert vr.discard_failed_candidate(tmp_path, "2.0")
    assert not vr.version_dir(tmp_path, "2.0").exists()


@pytest.mark.parametrize("evidence", ["complete", "malformed", "selected", "foreign", "denied"])
def test_failed_construction_refusal_preserves_candidate(tmp_path, monkeypatch, evidence):
    slot = vr.slot(tmp_path, "2.0")
    (slot / "partial").write_text("unchanged")
    if evidence == "complete":
        vr.mark_complete(tmp_path, "2.0", payload_hash="same")
    elif evidence == "malformed":
        vr.marker_path(tmp_path, "2.0").write_text(json.dumps({"broken": True}))
    elif evidence == "selected":
        (tmp_path / "current-version").write_text("2.0")
    elif evidence == "denied":
        monkeypatch.setattr(vr, "_remove_slot", lambda *_args, **_kwargs: False)
    def validate(_slot):
        if evidence == "foreign":
            raise RuntimeError("ownership changed")
    with vr.construction_lease(tmp_path, "2.0"):
        with pytest.raises(RuntimeError):
            vr.discard_failed_candidate(tmp_path, "2.0", validate=validate)
    assert (slot / "partial").read_text() == "unchanged"


def test_failed_construction_requires_actual_owner(tmp_path):
    slot = vr.slot(tmp_path, "2.0")
    with pytest.raises(RuntimeError, match="held construction"):
        vr.discard_failed_candidate(tmp_path, "2.0")
    assert slot.is_dir()


def test_cell_construction_rejects_new_receipt_between_cleanup_and_provision(tmp_path, monkeypatch):
    tests = Path(__file__).resolve().parents[3] / "libs" / "installation-context" / "tests"
    monkeypatch.syspath_prepend(str(tests))
    spec = importlib.util.spec_from_file_location("construction_generation_fixtures", tests / "test_snapshot_provenance.py")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    layout = fixtures._receipt_layout(tmp_path)
    fixtures._stamp_with_python(layout)
    owned = fixtures._provision_slot_with_python(layout)
    slot = Path(owned["slotRoot"])
    (slot / "partial").write_text("failed older build")
    original = vr.discard_failed_candidate
    changed = []

    def concurrent_publication(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result
        receipt = Path(layout["install"])
        value = json.loads(receipt.read_bytes())
        value["generation"] += 1
        fixtures._write_json(receipt, value)
        changed.append(receipt.read_bytes())
        return result

    monkeypatch.setattr(vr, "discard_failed_candidate", concurrent_publication)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(layout["install"]))
    with pytest.raises(ValueError, match="receipt generations changed before runtime slot provisioning"):
        vr.construction_session(slot.parent.parent, slot.name, payload_hash="a" * 64)
    assert len(changed) == 1
    assert Path(layout["install"]).read_bytes() == changed[0]
    assert not slot.exists()


@pytest.mark.parametrize("refusal", [None, "digest", "cell-complete", "build-complete", "selected", "generation"])
def test_failed_construction_cell_receipt_cleanup(tmp_path, monkeypatch, refusal):
    tests = Path(__file__).resolve().parents[3] / "libs" / "installation-context" / "tests"
    monkeypatch.syspath_prepend(str(tests))
    fixture_spec = importlib.util.spec_from_file_location(
        "failed_cell_fixtures", tests / "test_snapshot_provenance.py",
    )
    fixtures = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixtures)
    layout = fixtures._receipt_layout(tmp_path)
    fixtures._stamp_with_python(layout)
    owned = fixtures._provision_slot_with_python(layout)
    context = fixtures._load_python_module()
    slot = Path(owned["slotRoot"])
    ownership = slot / ".runtime-slot-ownership.json"
    digest = hashlib.sha256(ownership.read_bytes()).hexdigest()
    (slot / "partial").write_text("partial")
    if refusal == "digest":
        digest = "0" * 64
    elif refusal == "cell-complete":
        (slot / ".runtime-slot-completion.json").write_text("{}")
    elif refusal == "build-complete":
        vr.mark_complete(slot.parent.parent, slot.name, payload_hash="same")
    elif refusal == "selected":
        (slot.parent.parent / "current-version").write_text(slot.name)
    arguments = dict(
        runtime=vr, context=str(layout["install"]), durable_home=str(layout["durable"]),
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id=layout["plugin_id"], snapshot_id="1.0.0",
        runtime_version="3.4.5", expected_ownership_sha256=digest,
        expected_namespace_generation=1, expected_install_generation=2,
        expected_payload_root=str(layout["payload"]), expected_payload_version="1.0.0",
    )
    with vr.construction_lease(slot.parent.parent, slot.name):
        if refusal:
            if refusal == "generation":
                arguments["expected_install_generation"] = 0
            expected = {
                "digest": "ownership changed",
                "cell-complete": "cell completion evidence",
                "build-complete": "completion evidence",
                "selected": "selected or in use",
                "generation": "receipt generations changed",
            }[refusal]
            with pytest.raises((RuntimeError, ValueError), match=expected):
                context.discard_failed_runtime_slot(**arguments)
            assert (slot / "partial").read_text() == "partial"
        else:
            assert context.discard_failed_runtime_slot(**arguments)
            assert not slot.exists()


def start_session(root, processes, version="2.0", owner=None):
    arguments = [
        sys.executable, "-I", str(SOURCE), "--root", str(root),
        "construction-session", version, "--payload-hash", "a" * 64,
    ]
    if owner:
        arguments += ["--dev-owner", owner]
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    processes.append(process)
    return process, json.loads(process.stdout.readline())


@pytest.mark.parametrize("lost_claim", [False, True])
def test_dev_generation_failed_session_preserves_working_generation(tmp_path, processes, lost_claim):
    owner = "worktree:dev-owner"
    old_version, new_version = "dev-" + "a" * 32, "dev-" + "b" * 32
    vr.claim_dev(tmp_path, owner, previous_version="1.0")
    old = vr.slot(tmp_path, old_version)
    (old / "live").write_text("working")
    vr.mark_complete(tmp_path, old_version, payload_hash="a" * 64, dev_owner=owner)
    vr.activate(tmp_path, old_version, dev_owner=owner, link_free=True)
    before = vr.marker_path(tmp_path, old_version).read_bytes()
    failed, admission = start_session(tmp_path, processes, new_version, owner)
    assert admission == {"admission": "construct"}
    (vr.version_dir(tmp_path, new_version) / "partial").write_text("partial")
    if lost_claim:
        vr.claim_dev(tmp_path, "other", previous_version=old_version, force=True)
        command = json.dumps({"command": "complete", "payload_hash": "a" * 64}) + "\n"
    else:
        command = '{"command":"abort"}\n'
    stdout, stderr = failed.communicate(input=command, timeout=15)
    assert failed.returncode == 1, (stdout, stderr)
    assert not vr.version_dir(tmp_path, new_version).exists()
    assert vr.current_version(tmp_path) == old_version
    assert vr.read_last_known_good(tmp_path) == old_version
    assert vr.marker_path(tmp_path, old_version).read_bytes() == before
    assert (old / "live").read_text() == "working"


def test_failed_construction_attached_session_abort_and_transfer(tmp_path, processes):
    process, admission = start_session(tmp_path, processes)
    assert admission == {"admission": "construct"}
    slot = vr.version_dir(tmp_path, "2.0")
    result = subprocess.run(
        shell(slot, 23), check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 23
    with pytest.raises(vr.ConstructionTimeout):
        with vr.construction_lease(tmp_path, "2.0", timeout=0):
            pytest.fail("attached descriptor was not held during dependency construction")
    stdout, stderr = process.communicate(input='{"command":"abort"}\n', timeout=15)
    assert process.returncode == 1, (stdout, stderr)
    assert not slot.exists()
    retry, admission = start_session(tmp_path, processes)
    assert admission == {"admission": "construct"}
    retry.stdin.write(json.dumps({"command": "complete", "payload_hash": "a" * 64}) + "\n")
    retry.stdin.flush()
    assert json.loads(retry.stdout.readline()) == {"complete": True}
    with vr.construction_lease(tmp_path, "2.0", timeout=0):
        pass
    with pytest.raises(vr.RetentionTimeout):
        with vr._kernel_lease(tmp_path, "2.0", kind="retention", shared=False, timeout=0):
            pytest.fail("completion lost candidate retention")
    stdout, stderr = retry.communicate(input='{"command":"settled"}\n', timeout=15)
    assert retry.returncode == 0, (stdout, stderr)
    with vr._kernel_lease(tmp_path, "2.0", kind="retention", shared=False, timeout=0):
        pass


def test_construction_lost_admission_reader_reclaims_owned_candidate(tmp_path, monkeypatch):
    class ClosedReader:
        def write(self, _value):
            raise BrokenPipeError("parent closed admission channel")

    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", ClosedReader())
        with pytest.raises(BrokenPipeError):
            vr.construction_session(tmp_path, "2.0", payload_hash="a" * 64)
    assert not vr.version_dir(tmp_path, "2.0").exists()
    with vr.construction_lease(tmp_path, "2.0", timeout=0):
        pass


@pytest.mark.parametrize("code", [0, 23])
def test_failed_construction_native_pipe_adapter(tmp_path, processes, code):
    root = tmp_path / "runtime with spaces"
    root.mkdir()
    helper = vr.construction_shell_helpers("ps1" if os.name == "nt" else "sh")
    if os.name == "nt":
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        script = (
            "$ErrorActionPreference='Stop'\n" + helper
            + "\ntry {\n"
            + f"$reuse=Start-RuntimeConstruction -Python {quote(sys.executable)} "
            + f"-Helper {quote(SOURCE)} -Root {quote(root)} -Version '2.0' "
            + "-PayloadHash ('a'*64) -LinkName 'current'\n"
            + f"[IO.File]::WriteAllText({quote(root / 'versions' / '2.0' / 'partial')}, 'partial')\n"
            + (f"exit {code}\n" if code else "Complete-RuntimeConstruction -PayloadHash ('a'*64)\n")
            + "} finally { Stop-RuntimeConstruction }\n"
        )
        path = tmp_path / "constructor.ps1"
        path.write_text(script, encoding="utf-8")
        command = [
            shutil.which("pwsh") or shutil.which("powershell"),
            "-NoProfile", "-NonInteractive", "-File", str(path),
        ]
    else:
        import shlex
        script = (
            "set -euo pipefail\n" + helper
            + "\n_runtime_construction_begin "
            + " ".join(shlex.quote(str(value)) for value in (
                sys.executable, SOURCE, root, "2.0", "a" * 64, "current",
            )) + "\n"
            + f"printf partial > {shlex.quote(str(root / 'versions' / '2.0' / 'partial'))}\n"
            + (f"exit {code}\n" if code else f"_runtime_construction_complete {'a' * 64}\n")
        )
        path = tmp_path / "constructor.sh"
        path.write_text(script, encoding="utf-8")
        command = [BASH, str(path)]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    processes.append(process)
    stdout, stderr = process.communicate(timeout=20)
    if code:
        assert process.returncode != 0, (stdout, stderr)
        assert not vr.version_dir(root, "2.0").exists()
    else:
        assert process.returncode == 0, (stdout, stderr)
        assert vr.is_complete(root, "2.0", expect_hash="a" * 64)
    with vr.construction_lease(root, "2.0", timeout=0):
        pass


@pytest.mark.guard
def test_construction_signals_exit_through_owned_cleanup():
    helper = vr.construction_shell_helpers("sh")
    assert "trap 'exit 130' INT" in helper
    assert "trap 'exit 143' TERM" in helper
    assert helper.index("trap 'exit 143' TERM") < helper.index("read -r reply")
    finish = helper.index('_runtime_construction_finish ||')
    assert finish < helper.index('eval "$_RUNTIME_CONSTRUCTION_PREVIOUS_EXIT"', finish)


@pytest.mark.guard
def test_construction_adapter_uses_bash_three_syntax():
    helper = vr.construction_shell_helpers("sh")
    assert "coproc RUNTIME_CONSTRUCTION" not in helper
    assert "exec {_RUNTIME_CONSTRUCTION" not in helper
    assert "< <(" not in helper
    assert '} &\n    _RUNTIME_CONSTRUCTION_PID="$!"' in helper
    assert "BASH_VERSINFO" not in helper
    assert "owner_args" not in helper
    assert "mkfifo -m 600" in helper and "chmod 700" in helper
    assert 'exec $write_fd>&-' in helper
    bash = shutil.which(BASH) if os.name != "nt" else None
    if os.name == "nt" and shutil.which("git"):
        candidate = Path(shutil.which("git")).resolve().parent.parent / "bin" / "bash.exe"
        if candidate.is_file():
            bash = str(candidate)
    if not bash:
        pytest.skip("no local Bash parser available")
    result = subprocess.run(
        [bash, "-n"], input=helper, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name == "nt", reason="requires native POSIX FIFOs and descriptors")
@pytest.mark.parametrize("outcome", ["complete", "reuse", "abort", "eof", "malformed"])
def test_bash_three_adapter_holds_leases_and_preserves_descriptors(tmp_path, processes, outcome):
    import shlex

    root = tmp_path / "runtime ' with spaces"
    root.mkdir()
    if outcome == "reuse":
        vr.slot(root, "2.0")
        vr.mark_complete(root, "2.0", payload_hash="a" * 64)
    incoming, outgoing = tmp_path / "incoming", tmp_path / "outgoing"
    owned_jobs = tmp_path / "owned-jobs"
    incoming.write_text("caller-owned\n")
    held = (
        "import importlib.util,pathlib,sys; "
        "s=importlib.util.spec_from_file_location('vr',sys.argv[1]); "
        "vr=importlib.util.module_from_spec(s); s.loader.exec_module(vr); "
        "root=pathlib.Path(sys.argv[2]); reuse=sys.argv[3]=='reuse'; "
        "lease=(vr._kernel_lease(root,'2.0',kind='retention',shared=False,timeout=0) "
        "if reuse else vr.construction_lease(root,'2.0',timeout=0)); "
        "\ntry:\n lease.__enter__()\nexcept (vr.ConstructionTimeout,vr.RetentionTimeout): pass\n"
        "else:\n lease.__exit__(None,None,None); raise AssertionError('ownership was not held')\n"
        "assert not list(root.glob('.construction-channel.*'))"
    )
    probe = " ".join(shlex.quote(str(value)) for value in (
        sys.executable, "-I", "-c", held, SOURCE, root, outcome,
    ))
    begin = "_runtime_construction_begin " + " ".join(shlex.quote(str(value)) for value in (
        sys.executable, SOURCE, root, "2.0", "a" * 64, "current",
    ))
    tail = {
        "complete": (
            f"_runtime_construction_complete {'a' * 64}\n_runtime_construction_finish\n"
            + begin + '\n[[ "$_RUNTIME_CONSTRUCTION_REUSE" == 1 ]]\n_runtime_construction_finish\n'
        ),
        "reuse": "_runtime_construction_finish\n",
        "abort": "exit 23\n",
        "eof": (
            'eval "exec $_RUNTIME_CONSTRUCTION_WRITE>&-"\n'
            '_RUNTIME_CONSTRUCTION_WRITE=""\n'
            'IFS= read -r reply <&"$_RUNTIME_CONSTRUCTION_READ"\n'
            '[[ "$reply" == \'{"aborted": true}\' ]]\n'
            'rc=0; wait "$_RUNTIME_CONSTRUCTION_PID" || rc=$?\n[[ "$rc" == 1 ]]\n'
            '_RUNTIME_CONSTRUCTION_PID=""\n_runtime_construction_channels_close\n'
        ),
        "malformed": (
            'printf \'not-json\\n\' >&"$_RUNTIME_CONSTRUCTION_WRITE"\n'
            'rc=0; _runtime_construction_finish || rc=$?\n[[ "$rc" != 0 ]]\n'
        ),
    }[outcome]
    script = (
        "set -euo pipefail\n"
        + f"exec 10<{shlex.quote(str(incoming))} 11>{shlex.quote(str(outgoing))}\n"
        + "trap 'printf EXIT_CALLBACK' EXIT\n"
        + vr.construction_shell_helpers("sh") + "\n" + begin + "\n" + begin + "\n"
        + f"jobs -p > {shlex.quote(str(owned_jobs))}\n"
        + f'grep -Fxq -- "$_RUNTIME_CONSTRUCTION_PID" {shlex.quote(str(owned_jobs))}\n'
        + '[[ -z "$_RUNTIME_CONSTRUCTION_BOOTSTRAP" ]]\n'
        + probe + "\n"
        + 'IFS= read -r caller <&10\nprintf "%s\\n" "$caller" >&11\n'
        + tail
        + "printf survived >&11\n"
    )
    path = tmp_path / "portable-constructor.sh"
    path.write_text(script, encoding="utf-8")
    process = subprocess.Popen(
        [BASH, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    processes.append(process)
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == (23 if outcome == "abort" else 0), (stdout, stderr)
    assert stdout == "EXIT_CALLBACK"
    assert outgoing.read_text() == "caller-owned\n" + ("" if outcome == "abort" else "survived")
    assert not list(root.glob(".construction-channel.*"))
    assert vr.is_complete(root, "2.0", expect_hash="a" * 64) == (outcome in ("complete", "reuse"))
    if outcome not in ("complete", "reuse"):
        assert not vr.version_dir(root, "2.0").exists()
    with vr.construction_lease(root, "2.0", timeout=0):
        pass
    with vr._kernel_lease(root, "2.0", kind="retention", shared=False, timeout=0):
        pass


@pytest.mark.skipif(os.name == "nt", reason="requires native POSIX parent-death EOF")
def test_bash_three_adapter_parent_death_reclaims_candidate(tmp_path, processes):
    import shlex

    root = tmp_path / "runtime"
    script = (
        "set -euo pipefail\n" + vr.construction_shell_helpers("sh")
        + "\n_runtime_construction_begin "
        + " ".join(shlex.quote(str(value)) for value in (
            sys.executable, SOURCE, root, "2.0", "a" * 64, "current",
        ))
        + "\nprintf 'owned\\n'\nIFS= read -r ignored\n"
    )
    path = tmp_path / "parent-constructor.sh"
    path.write_text(script, encoding="utf-8")
    process = subprocess.Popen(
        [BASH, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    processes.append(process)
    assert process.stdout.readline().strip() == "owned"
    assert vr.version_dir(root, "2.0").exists()
    assert not list(root.glob(".construction-channel.*"))
    process.kill()
    process.communicate(timeout=20)
    with vr.construction_lease(root, "2.0", timeout=1):
        assert not vr.version_dir(root, "2.0").exists()
    assert not list(root.glob(".construction-channel.*"))


@pytest.mark.skipif(os.name == "nt", reason="requires native POSIX signal delivery")
@pytest.mark.parametrize("signal,code", [("INT", 130), ("TERM", 143)])
def test_construction_signal_settles_before_install_lock_release(tmp_path, processes, signal, code):
    import shlex

    root = tmp_path / "runtime"
    old = vr.slot(root, "1.0")
    (old / "live").write_text("working")
    vr.mark_complete(root, "1.0", payload_hash="old")
    vr.activate(root, "1.0", link_free=True)
    before = vr.marker_path(root, "1.0").read_bytes()
    lock = tmp_path / "install.lock"
    lock.mkdir()
    released = tmp_path / "released"
    proof = (
        "import importlib.util,pathlib,sys; "
        "s=importlib.util.spec_from_file_location('vr',sys.argv[1]); "
        "vr=importlib.util.module_from_spec(s); s.loader.exec_module(vr); "
        "root=pathlib.Path(sys.argv[2]); "
        "assert not vr.version_dir(root,'2.0').exists(); "
        "lease=vr.construction_lease(root,'2.0',timeout=0); "
        "lease.__enter__(); lease.__exit__(None,None,None); "
        "pathlib.Path(sys.argv[3]).rmdir(); "
        "pathlib.Path(sys.argv[4]).write_text('released-after-settlement')"
    )
    cleanup = " ".join(shlex.quote(str(value)) for value in (
        sys.executable, "-I", "-c", proof, SOURCE, root, lock, released,
    ))
    script = (
        "set -euo pipefail\n"
        + f"trap {shlex.quote(cleanup)} EXIT\n"
        + "trap 'echo EARLY_RELEASE; exit 99' INT TERM\n"
        + vr.construction_shell_helpers("sh")
        + "\n_runtime_construction_begin "
        + " ".join(shlex.quote(str(value)) for value in (
            sys.executable, SOURCE, root, "2.0", "a" * 64, "current",
        ))
        + f"\nprintf partial > {shlex.quote(str(root / 'versions' / '2.0' / 'partial'))}\n"
        + f"kill -s {signal} \"$$\"\nprintf CONTINUED\n"
    )
    path = tmp_path / "signal-constructor.sh"
    path.write_text(script, encoding="utf-8")
    process = subprocess.Popen(
        [BASH, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    processes.append(process)
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == code, (stdout, stderr)
    assert "EARLY_RELEASE" not in stdout and "CONTINUED" not in stdout
    assert released.read_text() == "released-after-settlement"
    assert not lock.exists()
    assert vr.current_version(root) == "1.0"
    assert vr.marker_path(root, "1.0").read_bytes() == before
    assert (old / "live").read_text() == "working"
