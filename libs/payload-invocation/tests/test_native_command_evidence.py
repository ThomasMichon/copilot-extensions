"""Native error-only emitters with absent application interpreters."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_command_evidence import ROOT, _recent, _run
from test_command_evidence import payload as command_evidence_payload

payload = command_evidence_payload

HOSTS = ["powershell", "pwsh"] if os.name == "nt" else ["bash"]


def test_bash_native_assets_parse_and_source_without_side_effects(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if os.name == "nt":
        git = shutil.which("git")
        candidates = [Path(git).parent.parent / "bin/bash.exe"] if git else []
        bash = next((str(path) for path in candidates if path.is_file()), None)
    if not bash:
        pytest.skip("Bash syntax/source interpreter unavailable")
    paths = (
        ROOT / "libs/agent-procutil/src/agent_procutil/private_paths.sh",
        ROOT / "libs/process-telemetry/src/process_telemetry/native/command-errors.sh",
    )
    home = tmp_path / "absent-home"
    environment = {**os.environ, "HOME": str(home), "PATH": str(tmp_path / "no-commands")}
    environment.pop("BASH_ENV", None)
    environment.pop("ENV", None)
    for index, path in enumerate(paths):
        source = tmp_path / f"source-only-{index}.sh"
        source.write_bytes(path.read_bytes())
        native_path = source.as_posix()
        for mode in (["-n", native_path], ["-c", '. "$1"', "source-check", native_path]):
            result = subprocess.run(
                [bash, "--noprofile", "--norc", *mode], input=b"", env=environment,
                capture_output=True, timeout=15, check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
            assert result.returncode == 0 and not result.stdout and not result.stderr, result.stderr
    assert not home.exists()


@pytest.fixture(params=HOSTS)
def native(
    payload: tuple[Path, dict[str, str]], tmp_path: Path, request: pytest.FixtureRequest,
) -> tuple[str, Path, Path, dict[str, str]]:
    if os.name != "nt" and sys.platform != "linux":
        pytest.skip("native Bash storage requires Linux GNU tools, flock and /proc")
    host = shutil.which(request.param)
    if not host:
        pytest.skip(f"{request.param} unavailable")
    spec = importlib.util.spec_from_file_location(
        "_native_evidence_producer", ROOT / "libs/payload-invocation/command_evidence.py",
    )
    assert spec and spec.loader
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    support = tmp_path / "native support"
    support.mkdir()
    for name, data in producer.native_files(ROOT).items():
        (support / name).write_bytes(data)
    directory = Path(payload[1]["HOME"]) / ".copilot-extensions/logs/diagnostics/commands"
    path = tmp_path / "native-only"
    path.mkdir()
    if os.name != "nt":
        for name in ("flock", "stat", "mkdir", "date", "tail", "mv"):
            executable = shutil.which(name)
            assert executable
            (path / name).symlink_to(executable)
    return host, support, directory, {**payload[1], "PATH": str(path)}


def _native(
    native: tuple[str, Path, Path, dict[str, str]], *, mode: str = "emit",
    result: str = "37", cause: str = "python-unavailable",
) -> subprocess.CompletedProcess[bytes]:
    host, support, directory, environment = native
    primary = "0" if mode == "import" else ("37" if result == "null" else result)
    if os.name == "nt":
        def quote(value: object) -> str:
            return "'" + str(value).replace("'", "''") + "'"
        script = f". {quote(support / 'command-errors.ps1')}; "
        if mode != "import":
            script += f"& $env:ComSpec /d /c {quote('exit ' + primary)}; $primary=$LASTEXITCODE; "
        if mode == "unsafe":
            script += (
                "$section=[Security.AccessControl.AccessControlSections]::Access; "
                f"$info=[IO.DirectoryInfo]::new({quote(directory)}); "
                "$original=if($PSVersionTable.PSEdition -eq 'Core'){"
                "[IO.FileSystemAclExtensions]::GetAccessControl($info,$section)"
                "}else{$info.GetAccessControl($section)}; "
                "$acl=[Security.AccessControl.DirectorySecurity]::new(); "
                "$acl.SetSecurityDescriptorSddlForm($original.GetSecurityDescriptorSddlForm($section),$section); "
                "$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new("
                "[Security.Principal.SecurityIdentifier]::new('S-1-1-0'),"
                "[Security.AccessControl.FileSystemRights]::ReadAndExecute,"
                "[Security.AccessControl.AccessControlType]::Allow)); "
                "if($PSVersionTable.PSEdition -eq 'Core'){"
                f"[IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new({quote(directory)}),$acl)"
                "}else{"
                f"([IO.DirectoryInfo]::new({quote(directory)})).SetAccessControl($acl)"
                "}; "
            )
        if mode != "import":
            exit_arg = "" if result == "null" else f"-ExitCode {result}"
            script += (
                f"Write-CommandErrorEvidence -Command native-fixture -Source fixture-source "
                f"-Cause {quote(cause)} {exit_arg} -Directory {quote(directory)}; "
                "if($LASTEXITCODE -ne $primary){throw 'Native diagnostics replaced observed result.'}; "
            )
        script += "[Console]::OpenStandardInput().CopyTo([Console]::OpenStandardOutput()); "
        script += "exit " + ("0" if mode == "import" else "$primary")
        command = [host, "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        script = '. "$1/command-errors.sh"; '
        if mode == "missing-bashpid":
            script += "unset BASHPID; set -u; "
        if mode == "closed-error":
            script += "exec 2>&-; set -e; "
        if mode == "custom-ifs":
            script += "IFS=:; "
        if mode != "import":
            script += 'if /bin/sh -c "exit $5"; then primary=0; else primary=$?; fi; '
            script += 'command_error_write native-fixture fixture-source "$3" "$4" "$2"; '
        script += "/bin/cat; exit " + ("0" if mode == "import" else '"$primary"')
        command = [host, "--noprofile", "--norc", "-c", script, "fixture",
                   str(support), str(directory), cause, result, primary]
    return subprocess.run(
        command, env=environment, input=b"\x00\xffnative stdin\r\n", capture_output=True,
        timeout=20, check=False, creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )


def test_native_import_is_inert_without_python_uv_or_store(
    native: tuple[str, Path, Path, dict[str, str]],
) -> None:
    result = _native(native, mode="import")
    assert result.returncode == 0 and not result.stderr
    assert result.stdout == b"\x00\xffnative stdin\r\n"
    assert not native[2].exists()


@pytest.mark.parametrize("observed", ["37", "0", "null"])
def test_native_writer_and_fresh_python_reader_interoperate_without_application_runtime(
    native: tuple[str, Path, Path, dict[str, str]], payload: tuple[Path, dict[str, str]],
    observed: str,
) -> None:
    result = _native(native, result=observed)
    assert result.returncode == (0 if observed == "0" else 37) and not result.stderr, result.stderr
    assert result.stdout == b"\x00\xffnative stdin\r\n"
    record, = _recent(payload)["records"]
    assert record["code"] == "python-unavailable"
    assert record["exit_code"] == (None if observed == "null" else int(observed))
    assert record["command"] == "native-fixture" and record["source"] == "fixture-source"
    assert record["phase"] == "entry" and record["pid"] > 0
    assert record["ts"].endswith("+00:00")  # Python 3.10's ISO decoder does not accept Z.
    assert "native stdin" not in json.dumps(record)


def test_native_and_sdk_writers_share_custody_and_permissions(
    native: tuple[str, Path, Path, dict[str, str]], payload: tuple[Path, dict[str, str]],
) -> None:
    command = ("run", "fixture-command", "fixture-source", "fixture", "--", "reported")
    assert _run(payload, *command).returncode == 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: _native(native), range(6)))
    assert all(result.returncode == 37 and not result.stderr for result in results), [
        result.stderr for result in results
    ]
    assert _run(payload, *command).returncode == 0
    evidence = _recent(payload)
    assert not evidence["unavailable"]
    assert len(evidence["records"]) == 8
    assert len({record["correlation_id"] for record in evidence["records"]}) == 8


def test_native_rotation_preserves_interrupted_evidence(
    native: tuple[str, Path, Path, dict[str, str]], payload: tuple[Path, dict[str, str]],
) -> None:
    assert not _native(native).stderr
    active = native[2] / "command-errors.jsonl"
    with active.open("ab") as target:
        target.write(b'{"interrupted":')
    result = _native(native)
    assert result.returncode == 37 and not result.stderr
    archives = list(native[2].glob("command-errors.*.jsonl"))
    assert len(archives) == 1 and archives[0].read_bytes().endswith(b'{"interrupted":')
    evidence = _recent(payload)
    assert len(evidence["records"]) == 2
    assert evidence["unavailable"]


def test_unknown_native_cause_degrades_without_retaining_opaque_text(
    native: tuple[str, Path, Path, dict[str, str]],
) -> None:
    result = _native(native, cause="opaque-private-canary")
    assert result.returncode == 37
    assert b"command evidence unavailable" in result.stderr
    assert b"opaque-private-canary" not in result.stderr
    assert result.stdout == b"\x00\xffnative stdin\r\n"
    assert not native[2].exists()


def test_native_unavailable_store_keeps_primary_result(
    native: tuple[str, Path, Path, dict[str, str]],
) -> None:
    native[2].parent.mkdir(parents=True)
    native[2].write_bytes(b"private retained fixture")
    result = _native(native)
    assert result.returncode == 37 and b"command evidence unavailable" in result.stderr
    assert native[2].read_bytes() == b"private retained fixture"


@pytest.mark.skipif(sys.platform != "linux", reason="Bash missing-identity refusal control")
def test_missing_bashpid_keeps_primary_result_without_storage(
    native: tuple[str, Path, Path, dict[str, str]],
) -> None:
    result = _native(native, mode="missing-bashpid")
    assert result.returncode == 37
    assert result.stdout == b"\x00\xffnative stdin\r\n"
    assert b"command evidence unavailable" in result.stderr
    assert b"unbound variable" not in result.stderr
    assert not native[2].exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native metadata and clock binding")
def test_native_metadata_parsing_does_not_depend_on_caller_ifs(
    native: tuple[str, Path, Path, dict[str, str]], payload: tuple[Path, dict[str, str]],
) -> None:
    result = _native(native, mode="custom-ifs")
    assert result.returncode == 37 and not result.stderr, result.stderr
    record, = _recent(payload)["records"]
    assert record["code"] == "python-unavailable"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native metadata and clock binding")
def test_newest_native_error_orders_after_sdk_error_in_the_same_second(
    native: tuple[str, Path, Path, dict[str, str]], payload: tuple[Path, dict[str, str]],
) -> None:
    assert _run(
        payload, "run", "fixture-command", "fixture-source", "fixture", "--", "reported",
    ).returncode == 0
    seed, = _recent(payload)["records"]
    second = datetime.now(UTC).replace(microsecond=0).isoformat()
    seed["ts"] = second.replace("+00:00", ".100000+00:00")
    (native[2] / "command-errors.jsonl").write_text(json.dumps(seed) + "\n", encoding="utf-8")
    date = Path(native[3]["PATH"]) / "date"
    original = date.resolve()
    date.unlink()
    base = second.removesuffix("+00:00")
    date.write_text(
        "#!/bin/sh\n"
        "case \"$2\" in\n"
        f"  *%6N*) printf '%s\\n' '{base}.900000+00:00';;\n"
        f"  +%Y-%m-%dT%H:%M:%S+00:00) printf '%s\\n' '{base}+00:00';;\n"
        f"  *) exec '{original}' \"$@\";;\n"
        "esac\n", encoding="utf-8",
    )
    date.chmod(0o700)
    result = _native(native)
    assert result.returncode == 37 and not result.stderr, result.stderr
    recent = _run(payload, "recent", "--limit", "1")
    assert recent.returncode == 0
    record, = json.loads(recent.stdout)["records"]
    assert record["command"] == "native-fixture" and record["ts"].endswith(".900000+00:00")


@pytest.mark.skipif(os.name == "nt", reason="closed-descriptor shell semantics require POSIX")
def test_native_unavailable_store_and_closed_stderr_keep_errexit_primary_result(
    native: tuple[str, Path, Path, dict[str, str]],
) -> None:
    native[2].parent.mkdir(parents=True)
    native[2].write_bytes(b"private retained fixture")
    result = _native(native, mode="closed-error")
    assert result.returncode == 37 and result.stdout == b"\x00\xffnative stdin\r\n"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native archive clock binding")
def test_native_archive_fraction_survives_bounded_mixed_writer_discovery(
    native: tuple[str, Path, Path, dict[str, str]], payload: tuple[Path, dict[str, str]],
) -> None:
    assert _run(
        payload, "run", "fixture-command", "fixture-source", "fixture", "--", "reported",
    ).returncode == 0
    seed, = _recent(payload)["records"]
    moment = datetime.now(UTC).replace(microsecond=0)
    base = moment.isoformat().removesuffix("+00:00")
    stamp = moment.strftime("%Y%m%dT%H%M%S")
    seed["ts"] = base + ".100000+00:00"
    date = Path(native[3]["PATH"]) / "date"
    original = date.resolve()
    date.unlink()
    date.write_text(
        "#!/bin/sh\ncase \"$2\" in\n"
        f"  +%Y-%m-%dT%H:%M:%S.%6N+00:00) printf '%s\\n' '{base}.900000+00:00';;\n"
        f"  +%Y%m%dT%H%M%S%6NZ) printf '%s\\n' '{stamp}900000Z';;\n"
        f"  +%Y%m%dT%H%M%S000000Z) printf '%s\\n' '{stamp}000000Z';;\n"
        f"  *) exec '{original}' \"$@\";;\nesac\n", encoding="utf-8",
    )
    date.chmod(0o700)
    active = native[2] / "command-errors.jsonl"
    active.write_bytes(b"")
    assert not _native(native).stderr
    for index in range(15):
        old = native[2] / f"command-errors.{stamp}500000Z-{index:032x}.jsonl"
        old.write_text(json.dumps(seed) + "\n", encoding="utf-8")
        old.chmod(0o600)
    with active.open("ab") as target:
        target.write(b'{"interrupted":')
    assert not _native(native).stderr
    # Model the retained archive being the newest completed evidence after
    # an interrupted attempt leaves the active segment empty.
    active.write_bytes(b"")
    recent = _run(payload, "recent", "--limit", "1")
    assert recent.returncode == 0
    record, = json.loads(recent.stdout)["records"]
    assert record["command"] == "native-fixture" and record["ts"].endswith(".900000+00:00")


def test_native_unsafe_store_is_not_repaired_or_appended(
    native: tuple[str, Path, Path, dict[str, str]], payload: tuple[Path, dict[str, str]],
) -> None:
    assert not _native(native).stderr
    active = native[2] / "command-errors.jsonl"
    before = active.read_bytes()
    if os.name != "nt":
        native[2].chmod(0o755)
    result = _native(native, mode="unsafe")
    assert result.returncode == 37 and b"command evidence unavailable" in result.stderr
    assert active.read_bytes() == before
    assert _recent(payload)["unavailable"]


def test_native_writer_obeys_an_actual_sdk_operation_lock(
    native: tuple[str, Path, Path, dict[str, str]], payload: tuple[Path, dict[str, str]],
) -> None:
    archive, environment = payload
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("_command_evidence/native_hold.py", (
            "import sys\n"
            "from _command_evidence.single_instance_lease.operation import OperationLock\n"
            "r=sys._command_error_recorder\n"
            "assert r.record('seed-error','Synthetic setup',exit_code=0)\n"
            "with OperationLock(r.directory/'.command-errors.lock'):\n"
            "    print('ready',flush=True)\n"
            "    sys.stdin.buffer.read(1)\n"
        ))
    command = [sys.executable, "-I", "-B", str(archive)]
    command += ["run", "fixture-command", "fixture-source", "_command_evidence.native_hold", "--"]
    with subprocess.Popen(
        command, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    ) as holder:
        try:
            assert holder.stdout and holder.stdout.readline().rstrip(b"\r\n") == b"ready"
            result = _native(native)
            assert result.returncode == 37 and b"command evidence unavailable" in result.stderr
        finally:
            _, stderr = holder.communicate(b"x", timeout=10)
        assert holder.returncode == 0, stderr
    record, = _recent(payload)["records"]
    assert record["code"] == "seed-error"


def test_installed_provider_locator_matches_canonical_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "_native_assets", ROOT / "libs/process-telemetry/src/process_telemetry/command_errors_assets.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    locations = {
        "process_telemetry": ROOT / "libs/process-telemetry/src/process_telemetry",
        "agent_procutil": ROOT / "libs/agent-procutil/src/agent_procutil",
    }
    monkeypatch.setattr(module, "files", locations.__getitem__)
    assets: dict[str, bytes] = module.native_support_files()
    producer_spec = importlib.util.spec_from_file_location(
        "_native_provider", ROOT / "libs/payload-invocation/command_evidence.py",
    )
    assert producer_spec and producer_spec.loader
    producer = importlib.util.module_from_spec(producer_spec)
    producer_spec.loader.exec_module(producer)
    assert assets == producer.native_files(ROOT)
