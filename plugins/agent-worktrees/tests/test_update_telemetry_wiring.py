"""``update`` records phases/targets through the shared update-telemetry lib
and maps its result onto the shared exit-code table."""

from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import update_telemetry as ut

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import installer
from agent_worktrees import reconcile, update_cli, update_events, update_gate, update_options
from agent_worktrees import update_runtime as runtime

pytestmark = [pytest.mark.guard, pytest.mark.usefixtures("payload_subprocess_double")]


@pytest.fixture
def declare_human_producer(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "tools"))
    import installer_engine_ref as contract

    def declare(script):
        shutil.copyfile(
            root / "libs" / "installer-engine" / contract.HUMAN_HELPER,
            script.parent / contract.HUMAN_HELPER,
        )
        script.with_suffix(".human-output.json").write_text(
            contract.human_output_json(script), encoding="utf-8",
        )
    declare.prologue = contract.HUMAN_PROLOGUE
    return declare


def test_failed_phase_completion_write_cannot_report_done(monkeypatch):
    import io

    from update_telemetry.human import HumanOperation, current_operation

    class Sink(io.StringIO):
        failed = False

        def write(self, text):
            if "[duration " in text and not self.failed:
                self.failed = True
                raise OSError("completion sink unavailable")
            return super().write(text)

    monkeypatch.setattr(update_events, "_run", None)
    monkeypatch.setattr(update_events, "_human_phase_failed", False)
    sink = Sink()
    with HumanOperation("update/engine", stream=sink) as operation:
        update_events.enter("runtime_reconcile")
        update_events.record("runtime_reconcile", "fixture", "installer exited 23")
        with pytest.raises(OSError, match="completion sink unavailable"):
            update_events.finish(1)
        operation.complete("failed", "exit 1")
    assert "runtime_reconcile done" not in sink.getvalue()
    assert "runtime_reconcile failed" in sink.getvalue()
    assert current_operation() is None


def test_installer_process_extraction_preserves_runtime_seams():
    from agent_worktrees import update_installer_process

    assert runtime._installer_timeout is update_installer_process._installer_timeout
    assert runtime.installer_deadline_status is update_installer_process.installer_deadline_status
    assert runtime._run_human_installer is update_installer_process._run_human_installer


def test_installer_capability_exports_share_the_canonical_owner():
    from update_telemetry import capabilities

    from agent_worktrees import installer_capabilities

    for name in installer_capabilities.__all__:
        assert getattr(installer_capabilities, name) is getattr(capabilities, name)


@pytest.mark.parametrize("shell", ["powershell", "pwsh", "bash"])
@pytest.mark.real_installer_processes
def test_human_installer_frames_native_streams_once_and_retains_failure(
    shell, tmp_path, capsys, declare_human_producer,
):
    """One real child per available native host, not a cold-host outcome matrix."""
    from update_telemetry.human import HumanOperation

    if (shell == "bash") == (os.name == "nt"):
        pytest.skip("native host belongs to the other platform")
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    canonical = "[12:00:01.120Z] update/nested running [elapsed 3.1s]"
    diagnostic = "".join(map(chr, (0x00e9, 0x03bb, 0x4e2d)))
    if shell == "bash":
        script = tmp_path / "installer.sh"
        script.write_text(
            "printf '%s\\n' 'native detail' '' "
            "'https://user:private@example.test/a' "
            f"'{canonical}'\nprintf '%s\\n' '{diagnostic}'\n"
            f"printf '%s\\n' '{diagnostic}' >&2\n"
            "printf '%s\\n' diagnostic >&2\nexit 23\n",
            encoding="utf-8",
        )
        argv = [executable, str(script)]
    else:
        script = tmp_path / "installer.ps1"
        script.write_text(
            "$priorEncoding = [Console]::OutputEncoding\n"
            "$priorInputEncoding = $OutputEncoding.CodePage\n"
            "Set-StrictMode -Version 2.0\n"
            "$ErrorActionPreference = 'Stop'\n\n"
            + declare_human_producer.prologue + "\n"
            + "Write-Host 'native detail'\nWrite-Output ''\n"
            "if ($OutputEncoding.CodePage -ne $priorInputEncoding) { "
            "throw 'Native stdin encoding changed' }\n"
            "$native = & $env:FIXTURE_PYTHON -I -c "
            "'import sys;sys.stdout.buffer.write(bytes([130,10]))'\n"
            "if ($native -cne $priorEncoding.GetString([byte[]]@(130))) { "
            "throw 'Native decoder policy changed' }\n"
            "$diagnostic = -join ([char]0x00e9, [char]0x03bb, [char]0x4e2d)\n"
            "[Console]::Error.WriteLine('encoding=' + [Console]::Out.Encoding.WebName)\n"
            "Write-Host $diagnostic\n[Console]::Error.WriteLine($diagnostic)\n"
            "Write-Output 'https://user:private@example.test/a'\n"
            f"Write-Output '{canonical}'\n"
            "[Console]::Error.WriteLine('diagnostic')\nexit 23\n",
            encoding="utf-8",
        )
        declare_human_producer(script)
        argv = [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    with HumanOperation("update/engine") as flow:
        rc = runtime._run_engine_installer(
            argv, tmp_path, 20, dict(os.environ, FIXTURE_PYTHON=sys.executable),
        )
        flow.complete("failed", f"exit {rc}")
    assert rc == 23
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private" not in captured.err
    lines = captured.err.splitlines()
    assert lines.count(canonical) == 1
    assert all(line.startswith("[") for line in lines)
    assert "native detail [elapsed " in captured.err
    assert captured.err.count(diagnostic) == 2, (
        [line for line in lines if "encoding=" in line],
        [ord(character) for character in captured.err if ord(character) > 127],
    )
    assert "diagnostic [elapsed " in captured.err
    assert "failed installer exited 23 [duration " in captured.err
    assert "done exit 0" not in captured.err


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("ending", ["explicit", "native-failure", "throw", "policy-refused"])
@pytest.mark.parametrize("receipt_owned", [False, True])
@pytest.mark.real_installer_processes
def test_human_producer_preserves_file_invocation(
    shell, ending, receipt_owned, tmp_path, capsys, declare_human_producer,
):
    from agent_worktrees.installer_capabilities import HUMAN_UTF8_ENV
    from update_telemetry.human import HumanOperation

    if os.name != "nt":
        pytest.skip("Windows native File contract")
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is not installed")
    script = tmp_path / "original target.ps1"
    receipt = tmp_path / "receipt.json"
    literal = "space ' ; $ ` & " + "".join(map(chr, (0xe9, 0x3bb, 0x4e2d)))
    end = {
        "explicit": "exit 23",
        "native-failure": "& $env:FIXTURE_PYTHON -I -c 'import sys;sys.exit(17)'",
        "throw": "throw 'synthetic failure'",
        "policy-refused": "exit 23",
    }[ending]
    script.write_text(
        "param([string]$Literal, [string]$Receipt)\n"
        "Set-StrictMode -Version 2.0\n"
        "$ErrorActionPreference = 'Stop'\n\n"
        + declare_human_producer.prologue + "\n"
        + "@{ path=$PSCommandPath; root=$PSScriptRoot; literal=$Literal; "
        "remaining=$args.Count; policy=[string]$env:PSExecutionPolicyPreference } | "
        "ConvertTo-Json -Compress | Set-Content -LiteralPath $Receipt -Encoding UTF8\n"
        + end + "\n", encoding="ascii",
    )
    declare_human_producer(script)
    policy = "Restricted" if ending == "policy-refused" else "Bypass"
    argv = [host, "-NoProfile", "-ExecutionPolicy", policy, "-File",
            str(script), "-Literal", literal, "-Receipt", str(receipt)]
    environment = dict(os.environ, FIXTURE_PYTHON=sys.executable)
    environment.pop(HUMAN_UTF8_ENV, None)
    baseline = subprocess.run(
        argv, env=environment, capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    original = (
        json.loads(receipt.read_text(encoding="utf-8-sig"))
        if receipt.is_file() else None
    )
    if receipt.is_file():
        receipt.unlink()
    with HumanOperation("update") as flow:
        options = (
            {"receipt_owned": True, "deadline_s": 10, "cwd": tmp_path,
             "target_version": "2.0.0", "receipt": lambda: "1.0.0"}
            if receipt_owned else {"timeout": 10}
        )
        candidate = runtime._run_human_installer(
            argv, label="producer", env=environment, **options,
        )
        code = candidate.rc if receipt_owned else candidate.returncode
        flow.complete("done" if code == 0 and not receipt_owned else "failed")
    if ending == "policy-refused":
        assert code == baseline.returncode != 0
        assert original is None and not receipt.exists()
        assert HUMAN_UTF8_ENV not in environment
        assert capsys.readouterr().out == ""
        return
    assert code == baseline.returncode == {
        "explicit": 23, "native-failure": 0, "throw": 1,
    }[ending]
    assert json.loads(receipt.read_text(encoding="utf-8-sig")) == original
    assert original == {
        "path": str(script), "root": str(tmp_path), "literal": literal,
        "remaining": 0, "policy": "Bypass",
    }
    assert HUMAN_UTF8_ENV not in environment
    captured = capsys.readouterr()
    assert captured.out == ""
    if receipt_owned:
        assert candidate.status != "OK" and not candidate.receipt_complete
        assert "done exit 0" not in captured.err


@pytest.mark.parametrize("declaration", ["absent", "stale"])
def test_legacy_human_producer_is_not_captured(
    declaration, tmp_path, monkeypatch, capsys, declare_human_producer,
):
    from agent_worktrees import output
    from agent_worktrees.installer_capabilities import HUMAN_UTF8_ENV
    from update_telemetry.human import HumanOperation

    # This adapter contract is platform-independent; the host process itself
    # is already covered by the native File tests above.
    script = tmp_path / "original.ps1"
    script.write_text(
        "Set-StrictMode -Version 2.0\n"
        "$ErrorActionPreference = 'Stop'\n\n"
        + declare_human_producer.prologue + "\n", encoding="ascii",
    )
    if declaration == "stale":
        declare_human_producer(script)
        with script.open("a", encoding="ascii") as stream:
            stream.write("# changed producer\n")
    argv = ["powershell.exe", "-NoProfile", "-File", str(script), "literal ; $ value"]
    environment = dict(os.environ)
    environment[HUMAN_UTF8_ENV] = "1"
    seen = {}

    def original_runner(command, **kwargs):
        seen.update(kwargs)
        assert command is argv
        assert "stdout" not in kwargs and "stderr" not in kwargs
        assert HUMAN_UTF8_ENV not in kwargs["env"]
        return subprocess.CompletedProcess(command, 23)

    monkeypatch.setattr(runtime.subprocess, "run", original_runner)
    monkeypatch.setattr(output, "native_output",
                        lambda: pytest.fail("legacy native stream must not be decoded"))
    with HumanOperation("update"):
        result = runtime._run_human_installer(
            argv, label="legacy", env=environment, timeout=10,
        )
    assert result.returncode == 23
    assert seen["timeout"] == 10
    assert environment[HUMAN_UTF8_ENV] == "1"
    assert capsys.readouterr().err.count("retaining inherited installer streams") == 1


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_legacy_native_bytes_and_file_arguments_pass_through(shell, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows native inherited-stream boundary")
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is not installed")
    script = tmp_path / "legacy.ps1"
    receipt = tmp_path / "argv.json"
    script.write_text(
        "param([string]$Literal,[string]$Receipt)\n"
        "@{ literal=$Literal; path=$PSCommandPath; root=$PSScriptRoot } | "
        "ConvertTo-Json -Compress | Set-Content -LiteralPath $Receipt -Encoding UTF8\n"
        "$bytes=[byte[]]@(0x82,0,255,13,10)\n"
        "[Console]::OpenStandardOutput().Write($bytes,0,$bytes.Length)\n"
        "[Console]::OpenStandardError().Write($bytes,0,$bytes.Length)\nexit 23\n",
        encoding="ascii",
    )
    argv = [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-Literal", "space ; $ & " + chr(0x4e2d), "-Receipt", str(receipt)]
    baseline = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=10,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    original = json.loads(receipt.read_text(encoding="utf-8-sig"))
    root = Path(__file__).resolve().parents[3]
    paths = [root / "plugins" / "agent-worktrees" / "src", *root.glob("libs/*/src")]
    # Windows inherited handles are not pytest's redirected CRT descriptors.
    # Capture the real parent process boundary instead of mocking past it.
    driver = (
        f"import sys;sys.path[:0]={[str(path) for path in paths]!r};"
        "import subprocess;"
        "from agent_worktrees.update_runtime import _run_human_installer;"
        "from update_telemetry.human import HumanOperation\n"
        "with HumanOperation('update') as flow:\n"
        f" result=_run_human_installer({argv!r},label='legacy',timeout=10,stdin=subprocess.DEVNULL,"
        f"creationflags={subprocess.CREATE_NO_WINDOW})\n"
        " flow.complete('failed',str(result.returncode))\n"
        "raise SystemExit(result.returncode)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c", driver],
        capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == baseline.returncode == 23
    assert result.stdout == baseline.stdout == bytes((0x82, 0, 255, 13, 10))
    assert baseline.stderr in result.stderr
    assert json.loads(receipt.read_text(encoding="utf-8-sig")) == original
    assert result.stderr.count(b"retaining inherited installer streams") == 1


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_real_unrequested_entrypoint_smoke_needs_no_output_helper(shell, tmp_path):
    from agent_worktrees.installer_capabilities import HUMAN_UTF8_ENV

    if os.name != "nt":
        pytest.skip("Windows native entrypoint")
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is not installed")
    source = Path(__file__).resolve().parents[1]
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(source / "scripts" / "install.ps1", scripts / "install.ps1")
    shutil.copyfile(source / "plugin.json", payload / "plugin.json")
    home = tmp_path / "home"
    home.mkdir()
    environment = dict(
        os.environ, USERPROFILE=str(home), HOME=str(home),
        COPILOT_PLUGIN_INSTALL_SMOKE="1", COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP="0",
    )
    for key in (HUMAN_UTF8_ENV, "COPILOT_EXTENSIONS_CONTEXT",
                "COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD"):
        environment.pop(key, None)
    result = subprocess.run(
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(scripts / "install.ps1"), "status"],
        env=environment, cwd=tmp_path, capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((home / ".agent-worktrees" / "smoke.json").read_text("utf-8-sig"))
    assert receipt["ran_from"] == str(scripts)
    assert not (scripts / "installer-human-output.ps1").exists()
    (home / ".agent-worktrees" / "smoke.json").unlink()
    environment[HUMAN_UTF8_ENV] = "1"
    requested = subprocess.run(
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(scripts / "install.ps1"), "status"],
        env=environment, cwd=tmp_path, capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert requested.returncode != 0
    assert not (home / ".agent-worktrees" / "smoke.json").exists()


@pytest.mark.parametrize("telemetry", ["no-run", "missing-library", "broken-store"])
def test_human_required_failure_without_telemetry(telemetry, monkeypatch, capsys):
    from update_telemetry import run as run_mod
    from update_telemetry.human import HumanOperation, current_operation

    if telemetry == "no-run":
        monkeypatch.setattr(ut.Run, "from_env_or_new", lambda *a: None)
    elif telemetry == "missing-library":
        monkeypatch.setattr(update_events, "_ut", None)
    else:
        def unavailable(*args):
            raise OSError("store unavailable")
        monkeypatch.setattr(run_mod, "append_line", unavailable)
    with HumanOperation("update") as human:
        update_events.start()
        update_events.enter("runtime_reconcile")
        update_events.record("runtime_reconcile", "agent-example", "installer exited 23")
        update_events.enter("binstubs")
        assert update_events.finish(1) == 1
        human.complete("failed", "exit 1")
    captured = capsys.readouterr()
    assert "update/runtime_reconcile failed [duration " in captured.err
    assert "update/runtime_reconcile done [duration " not in captured.err
    assert current_operation() is None


@pytest.mark.parametrize("flag", ["--json", "--quiet"])
def test_update_entrypoint_does_not_invent_protocol_flags(flag, monkeypatch, capsys):
    monkeypatch.setattr(m, "_resolve_active_project", lambda _project: ("fixture", None))
    monkeypatch.setattr(
        m, "_cmd_update_in_plugin",
        lambda *a: pytest.fail("unsupported option reached the effectful updater"),
    )
    with pytest.raises(SystemExit) as error:
        m.main(["update", "--no-manager", flag])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments" in captured.err
    assert update_events._run is None


def test_reconcile_apply_entrypoint_retains_json_stdout(tmp_path, monkeypatch, capsys):
    from update_telemetry.human import current_operation

    summary = {"action": "continue", "executed": []}

    def apply(repo, *, log, **kwargs):
        log("synthetic reconciliation diagnostic")
        return summary

    monkeypatch.setattr(reconcile, "apply_plan", apply)
    monkeypatch.setattr(m, "_resolve_active_project", lambda _project: ("fixture", None))
    assert m.main(["reconcile-plugins", "--repo", str(tmp_path), "--apply"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == summary
    assert captured.err == "synthetic reconciliation diagnostic\n"
    assert current_operation() is None


@pytest.fixture(autouse=True)
def _update_home(tmp_path, monkeypatch):
    for key in (ut.ENV_RUN_ID, ut.ENV_EVENTS, ut.ENV_OWNER):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "update-home"
    monkeypatch.setenv(ut.ENV_HOME, str(home))
    yield home
    assert update_events._run is None, "every update run is finished"


def _status(home: Path) -> dict:
    return json.loads((home / "update-status.json").read_text(encoding="utf-8"))


def _events(home: Path) -> list[dict]:
    status = _status(home)
    return ut.read_events(Path(status["run"]["events_path"]))[0]


def _args(**overrides) -> argparse.Namespace:
    values = dict(recreate_venv=False, skip_modules=None, no_anchor_sync=True,
                  no_prune_pivots=True, force=False, no_manager=True)
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "agent-worktrees"
    (plugin_dir / "scripts").mkdir(parents=True)
    (plugin_dir / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\n")
    installer_rc = {"value": 0}
    deployed = {"version": "1.0.0"}

    def run(argv, *a, **k):
        installer = "install.sh" in " ".join(map(str, argv))
        rc = installer_rc["value"] if installer else 0
        if rc == "timeout":
            raise subprocess.TimeoutExpired(argv, k.get("timeout"))
        if installer and rc == 0:
            deployed["version"] = "1.0.1"  # a successful installer publishes its receipt
        return subprocess.CompletedProcess(argv, rc, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(m, "_registered_plugin_targets", lambda: {})
    monkeypatch.setattr(m, "_update_registered_plugins", lambda targets: None)
    monkeypatch.setattr(m, "_update_modules", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_installed_plugin_dir", lambda: plugin_dir)
    monkeypatch.setattr(m, "_project_update_context", lambda: plugin_dir)
    monkeypatch.setattr(m, "_invocation_update_context", lambda: None)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.0.1")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda *a, **k: deployed["version"])
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment",
        lambda *a, **k: (dict(os.environ), tmp_path / "aw-runtime"),
    )
    return installer_rc


def test_phase_events_emitted(wired, _update_home):
    assert m.cmd_update(_args()) == 0
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.VERIFIED_CURRENT
    assert status["run"]["exit_code"] == 0
    phases = [p["phase"] for p in status["run"]["phases"]]
    assert phases == [
        "payload_refresh", "runtime_preflight", "engine_runtime", "module_update",
        "runtime_reconcile", "binstubs"]
    events = _events(_update_home)
    assert {e["proc"] for e in events} == {"engine"}
    starts = [e["phase"] for e in events if e["component"] == "run" and e["event"] == "start"]
    ends = [e["phase"] for e in events if e["component"] == "run" and e["event"] == "end"]
    assert sorted(starts) == sorted(ends)
    aw = status["plugins"]["agent-worktrees@copilot-extensions"]
    assert aw["outcome"] == ut.VERIFIED_CURRENT and aw["changed"] is True
    assert not any(key in os.environ for key in (ut.ENV_RUN_ID, ut.ENV_EVENTS))


def test_installer_75_is_blocked_by_lock(wired, _update_home):
    wired["value"] = 75
    assert m.cmd_update(_args()) == 5
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.BLOCKED_BY_LOCK
    assert status["plugins"]["agent-worktrees@copilot-extensions"]["notes"] == [
        "installer_exit_75"]
    assert update_events.classify_status("installer exited 76")[0] == ut.COMMAND_FAILED


def test_failed_step_keeps_legacy_failure_exit(wired, monkeypatch, _update_home, capsys):
    monkeypatch.setattr(m, "_update_modules", lambda *a, **k: False)
    assert m.cmd_update(_args()) == 1
    assert _status(_update_home)["last_failure"]["outcome"] == ut.COMMAND_FAILED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "update/engine failed exit 1 [duration " in captured.err
    assert "update/engine done exit 0" not in captured.err


@pytest.mark.parametrize("mixed", [False, True])
def test_required_auth_exit_does_not_depend_on_optional_telemetry(wired, monkeypatch, mixed):
    from agent_worktrees import update_auth as auth, update_auth_sources as sources

    monkeypatch.setattr(update_events, "_ut", None)
    engine = auth.source(
        "marketplace:copilot-extensions", "https://github.com/team/engine",
    )
    private = auth.source("marketplace:private", "https://github.com/team/private")
    monkeypatch.setattr(sources, "marketplaces", lambda _: [engine, private])

    def report(items):
        return auth.AuthReport([
            auth.SourceAuth(
                item, [], status="anonymous" if mixed and item.id == engine.id else "auth_required",
            )
            for item in items
        ])

    def registered(targets):
        update_events.record("payload_refresh", "required-other", "plugin update failed")
        return False

    monkeypatch.setattr(auth, "report", report)
    monkeypatch.setattr(m, "_update_registered_plugins", registered)
    assert m.cmd_update(_args()) == (1 if mixed else 4)
    assert update_events._run is None
    monkeypatch.setattr(
        auth, "report",
        lambda items: auth.AuthReport([
            auth.SourceAuth(item, [], status="anonymous") for item in items
        ]),
    )
    assert m.cmd_update(_args()) == 1


def test_payload_failure_does_not_claim_an_unattempted_anchor_fallback(
    wired, monkeypatch, tmp_path, _update_home,
):
    from agent_worktrees import update_auth as auth, update_auth_sources as sources

    anchor = auth.source(
        "anchor:fixture", "https://github.com/team/anchor",
        purpose="anchor", cwd=tmp_path / "anchor",
    )
    monkeypatch.setattr(sources, "anchors", lambda: [anchor])
    monkeypatch.setattr(
        sources, "marketplaces",
        lambda cwd: [auth.source(
            "marketplace:copilot-extensions", "https://github.com/team/core", cwd=cwd,
        )],
    )
    monkeypatch.setattr(
        auth, "report",
        lambda items: auth.AuthReport([
            auth.SourceAuth(
                item, [],
                status="fixable_by_fallback" if item.purpose == "anchor" else "anonymous",
                expected_account="fixture",
                alternative={"method": "gh"} if item.purpose == "anchor" else None,
            )
            for item in items
        ]),
    )
    monkeypatch.setattr(m, "_update_registered_plugins", lambda targets: False)
    monkeypatch.setattr(
        m, "_fast_forward_project_anchors",
        lambda: pytest.fail("payload failure must stop before anchor acquisition"),
    )
    assert m.cmd_update(_args(no_anchor_sync=False)) == 1
    assert _status(_update_home)["auth"]["fallback_used"] == []


@pytest.mark.parametrize("error, expected", [
    ("fatal: Authentication failed", 4),
    ("fatal: unable to resolve host", 1),
])
def test_disabled_auth_preflight_retains_anchor_failure_without_telemetry(
    wired, monkeypatch, error, expected,
):
    from agent_worktrees import update_auth_context

    monkeypatch.setenv("CE_UPDATE_AUTH", "off")
    monkeypatch.setattr(update_events, "_ut", None)
    monkeypatch.setattr(
        m, "_fast_forward_project_anchors",
        lambda: update_auth_context.anchor_failure("fixture", error),
    )
    assert m.cmd_update(_args(no_anchor_sync=False)) == expected
    assert update_events._run is None


@pytest.mark.parametrize("failure", ["engine-exit", "engine-timeout", "engine-os", "registered"])
def test_required_acquisition_failure_precedes_all_runtime_mutation(
    wired, monkeypatch, _update_home, failure,
):
    run = subprocess.run

    def acquisition(argv, *args, **kwargs):
        if "plugin" in argv:
            if failure == "engine-timeout":
                raise subprocess.TimeoutExpired(argv, 120)
            if failure == "engine-os":
                raise PermissionError(errno.EACCES, "fixture acquisition denied")
            if failure == "engine-exit":
                return subprocess.CompletedProcess(argv, 1, "", "fixture acquisition failed")
        return run(argv, *args, **kwargs)

    def registered(targets):
        if failure == "registered":
            update_events.record("payload_refresh", "required-example", "plugin update failed")
            return False
        pytest.fail("Engine acquisition failure reached another payload operation")

    def mutation(*args, **kwargs):
        pytest.fail("Acquisition failure reached runtime planning or local mutation")

    monkeypatch.setattr(subprocess, "run", acquisition)
    monkeypatch.setattr(m, "_update_registered_plugins", registered)
    for name in ("_find_installed_plugin_dir", "_update_modules",
                 "_reconcile_registered_runtimes", "_fast_forward_project_anchors",
                 "_prune_stale_pivots_after_update"):
        monkeypatch.setattr(m, name, mutation)
    monkeypatch.setattr(reconcile, "runtime_installer_environment", mutation)
    from agent_worktrees import update_integration

    monkeypatch.setattr(update_integration, "finish", mutation)
    assert m.cmd_update(_args(no_anchor_sync=False, no_prune_pivots=False)) != 0
    status = _status(_update_home)
    assert [phase["phase"] for phase in status["run"]["phases"]] == ["payload_refresh"]
    assert status["run"]["outcome"] != ut.VERIFIED_CURRENT


def test_target_selection_precedes_acquisition_and_self_refresh_is_not_repeated(
    wired, monkeypatch,
):
    active = m._PluginActivation.ACTIVE
    selected = {
        "agent-worktrees": m._RegisteredPluginTarget(None, active),
        "context-handoff": m._RegisteredPluginTarget(None, active),
    }
    calls = []
    original_run = subprocess.run

    def collect():
        calls.append("collect")
        return selected

    def acquire(argv, *args, **kwargs):
        if "plugin" in argv:
            assert calls == ["collect"]
            calls.append("engine")
            selected.clear()
            selected["later"] = m._RegisteredPluginTarget(None, active)
        return original_run(argv, *args, **kwargs)

    def registered(targets):
        assert calls == ["collect", "engine"]
        assert set(targets) == {"context-handoff"}
        calls.append("registered")
        return True

    def runtimes(*args, **kwargs):
        assert set(kwargs["targets"]) == {"agent-worktrees", "context-handoff"}
        return True

    monkeypatch.setattr(m, "_registered_plugin_targets", collect)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: Path("/payload") / name)
    monkeypatch.setattr(reconcile, "manifest_runtime_scope", lambda directory: "none")
    monkeypatch.setattr(subprocess, "run", acquire)
    monkeypatch.setattr(m, "_update_registered_plugins", registered)
    monkeypatch.setattr(m, "_update_modules", runtimes)
    monkeypatch.setattr(m, "_reconcile_registered_runtimes", runtimes)
    assert m.cmd_update(_args()) == 0
    assert calls == ["collect", "engine", "registered"]


def test_real_failed_acquisition_process_never_enters_runtime_stage(
    monkeypatch, tmp_path, _update_home,
):
    marker = tmp_path / "acquired"
    (tmp_path / "plugin").write_text(
        "from pathlib import Path\n"
        "Path('acquired').write_text('attempted')\n"
        "raise SystemExit(1)\n", encoding="utf-8",
    )
    monkeypatch.setattr(m, "_resolve_copilot", lambda: sys.executable)
    monkeypatch.setattr(m, "_invocation_update_context", lambda: tmp_path)
    monkeypatch.setattr(m, "_registered_plugin_targets", lambda: {})
    monkeypatch.setattr(m, "_update_registered_plugins", lambda targets: True)
    monkeypatch.setattr(
        m, "_find_installed_plugin_dir",
        lambda: pytest.fail("Real acquisition failure reached runtime planning"),
    )
    assert m.cmd_update(_args()) == 1
    assert marker.read_text(encoding="utf-8") == "attempted"
    assert _status(_update_home)["run"]["outcome"] == ut.COMMAND_FAILED


def test_auth_required_exit_4(_update_home):
    update_events.start("engine")
    update_events._run.target("auth_report", "source:example.com/owner", ut.AUTH_REQUIRED)
    assert update_events.finish(1) == 4
    assert _status(_update_home)["run"]["outcome"] == ut.AUTH_REQUIRED


def test_telemetry_failure_never_changes_result(wired, monkeypatch, tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    monkeypatch.setenv(ut.ENV_HOME, str(blocker / "home"))
    assert m.cmd_update(_args()) == 0


@pytest.fixture
def registered_runtime(tmp_path, monkeypatch):
    payload = tmp_path / "agent-example"
    (payload / "scripts").mkdir(parents=True)
    (payload / "scripts" / "install.sh").write_text("#!/bin/sh\n")
    (payload / "plugin.json").write_text(json.dumps({"name": "agent-example"}))
    environment = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_CONTEXT"}
    root = tmp_path / "runtime"
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: payload)
    monkeypatch.setattr(reconcile, "manifest_runtime_scope", lambda path: "universal")
    monkeypatch.setattr(reconcile, "payload_version", lambda path: "2.0.0")
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "1.0.0")
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, root))

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(runtime.subprocess, "run", timeout)
    return root


_SLOT_STATES = [None, "complete-active", "complete-not-active"]


def _write_slot(root: Path, version: str, state: str | None) -> None:
    """A pre-existing completed slot proves nothing about this attempt."""
    if state is None:
        return
    slot = root / "versions" / version
    slot.mkdir(parents=True)
    (slot / ".install-complete.json").write_text(json.dumps(
        {"version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": 1}))
    active = version if state == "complete-active" else "0.0.1"
    (root / "current-version").write_text(active)


@pytest.mark.parametrize("slot", _SLOT_STATES)
def test_registered_runtime_timeout_is_timeout_whatever_the_slot(
    registered_runtime, _update_home, slot,
):
    _write_slot(registered_runtime, "2.0.0", slot)
    update_events.start("engine")
    status = runtime._reconcile_one_runtime("agent-example", "linux", force=False)
    assert status == "timed out (deadline hit during build)"
    assert update_events.finish(1) == 6
    target = _status(_update_home)["plugins"]["agent-example@copilot-extensions"]
    assert target["outcome"] == ut.TIMEOUT


def test_timeout_without_slot_exits_6(registered_runtime, _update_home):
    update_events.start("engine")
    assert runtime._reconcile_one_runtime(
        "agent-example", "linux", force=False).startswith("timed out")
    assert update_events.finish(1) == 6


def test_force_runtime_option_parses_forwards_and_selects(monkeypatch, tmp_path):
    parser = argparse.ArgumentParser()
    update_cli.add_parsers(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(
        ["update", "--force-runtime", "agent-a", "--force-runtime", "agent-b"])
    assert update_options.forced_runtimes(args) == {"agent-a", "agent-b"}
    assert update_cli._update_flags(args) == [
        "--force-runtime", "agent-a", "--force-runtime", "agent-b"]

    seen = {}
    monkeypatch.setattr(m, "_module_names", lambda plugin_dir: set())
    monkeypatch.setattr(
        m, "_reconcile_one_runtime",
        lambda name, plat, *, force, gate_context=None: (seen.__setitem__(name, force), "OK")[1],
    )
    targets = {
        name: runtime._RegisteredPluginTarget(None, runtime._PluginActivation.ACTIVE)
        for name in ("agent-a", "agent-c")
    }
    assert runtime._reconcile_registered_runtimes(
        tmp_path, "linux", targets=targets, force_runtimes=frozenset({"agent-a"}))
    assert seen == {"agent-a": True, "agent-c": False}


@pytest.mark.parametrize("slot", _SLOT_STATES)
def test_engine_installer_timeout_exits_6(wired, tmp_path, _update_home, slot):
    wired["value"] = "timeout"
    _write_slot(tmp_path / "aw-runtime", "1.0.1", slot)
    assert m.cmd_update(_args()) == 6
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.TIMEOUT
    phase = {p["phase"]: p for p in status["run"]["phases"]}["engine_runtime"]
    assert phase["outcome"] == ut.TIMEOUT
    aw = status["plugins"]["agent-worktrees@copilot-extensions"]
    assert aw["outcome"] == ut.TIMEOUT


@pytest.mark.parametrize("slot", _SLOT_STATES)
def test_module_installer_timeout_is_timeout_whatever_the_slot(
    tmp_path, monkeypatch, _update_home, slot,
):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    (plugin / "modules.json").write_text(json.dumps(
        {"modules": [{"name": "agent-example", "source": "agent-example"}]}))
    module = tmp_path / "agent-example"
    (module / "scripts").mkdir(parents=True)
    (module / "scripts" / "install.sh").write_text("#!/bin/sh\n")
    (module / "plugin.json").write_text(
        json.dumps({"name": "agent-example", "runtimeScope": "universal"}))
    root = tmp_path / "module-runtime"
    _write_slot(root, "3.0.0", slot)
    environment = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_CONTEXT"}
    monkeypatch.setattr(reconcile, "payload_version", lambda path: "3.0.0")
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "2.0.0")
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, root))
    monkeypatch.setattr(reconcile, "_zero_downtime_update", lambda path: False)

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(runtime.subprocess, "run", timeout)
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        None, runtime._PluginActivation.ACTIVE)}
    update_events.start("engine")
    ok = runtime._update_modules(plugin, "linux", None, targets=targets)
    code = update_events.finish(0 if ok else 1)
    target = _status(_update_home)["plugins"]["agent-example@copilot-extensions"]
    assert ok is False and code == 6
    assert target["outcome"] == ut.TIMEOUT


@pytest.mark.parametrize(("status", "expected"), [
    ("plugin update failed: Access is denied. (os error 5)",
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: Permission denied", (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: The process cannot access the file because it is being "
     "used by another process. (os error 32)", (ut.BLOCKED_BY_LOCK, ut.SHARING_VIOLATION)),
    ("installer exited 75", (ut.BLOCKED_BY_LOCK, "installer_exit_75")),
    ("timed out", (ut.TIMEOUT, "timeout")),
    ("plugin update failed: [Errno 5] Input/output error", (ut.COMMAND_FAILED, ut.IO_ERROR)),
    ("plugin update failed: [WinError 33] The process cannot access the file because "
     "another process has locked a portion of the file", (ut.BLOCKED_BY_LOCK, "lock_violation")),
    ("plugin update failed: (os error 33)", (ut.BLOCKED_BY_LOCK, "lock_violation")),
    ("plugin update failed: [WinError 32] sharing", (ut.BLOCKED_BY_LOCK, ut.SHARING_VIOLATION)),
    ("plugin update failed: [WinError 5] Zugriff verweigert",
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: Zugriff verweigert (os error 5)",
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: [WinError 50] Anfrage nicht unterstuetzt", (ut.COMMAND_FAILED, None)),
    ("plugin update failed: (os error 330)", (ut.COMMAND_FAILED, None)),
])
def test_status_classification_needs_contention_evidence(monkeypatch, status, expected):
    monkeypatch.setattr(update_events, "_WINDOWS", True)  # Windows-shaped messages
    assert update_events.classify_status(status) == expected


@pytest.mark.parametrize(("status", "windows", "expected"), [
    ("plugin update failed: [Errno 13] Zugriff verweigert", False,
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: [Errno 1] Vorgang nicht zulaessig", False,
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: [Errno 5] Ein-/Ausgabefehler", False,
     (ut.COMMAND_FAILED, ut.IO_ERROR)),
    ("plugin update failed: [Errno 130] Andere", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: Fehler (os error 5)", False, (ut.COMMAND_FAILED, ut.IO_ERROR)),
    ("plugin update failed: Fehler (os error 5)", True,
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: Fehler (os error 13)", False,
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: Fehler (os error 33)", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: child process exited 75", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: rpc deadline exceeded", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: package not found", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: 'copilot' CLI not found on PATH (x)", False,
     (ut.CONFIGURATION_LIMITATION, "configuration")),
])
def test_status_classification_is_locale_and_host_aware(monkeypatch, status, windows, expected):
    monkeypatch.setattr(update_events, "_WINDOWS", windows)
    assert update_events.classify_status(status) == expected


def test_inactive_module_stays_advisory(tmp_path, monkeypatch, _update_home):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    (plugin / "modules.json").write_text(json.dumps(
        {"modules": [{"name": "agent-example", "source": "agent-example"}]}))
    inactive = runtime._RegisteredPluginTarget(None, runtime._PluginActivation.INACTIVE)
    update_events.start("engine")
    update_events.enter("payload_refresh")
    update_events.record("payload_refresh", "agent-example@copilot-extensions",
                         "plugin state error: boom", required=False)
    update_events.enter("module_update")
    assert runtime._update_modules(plugin, "linux", None, targets={"agent-example": inactive})
    assert update_events.finish(0) == 0
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.VERIFIED_CURRENT
    entry = status["plugins"]["agent-example@copilot-extensions"]
    assert entry["required"] is False
    assert [a["component"] for a in status["advisories"]] == ["agent-example@copilot-extensions"]


def _phase_outcomes(home: Path) -> dict[str, str]:
    return {p["phase"]: p["outcome"] for p in _status(home)["run"]["phases"]}


@pytest.mark.parametrize("engine", [False, True])
def test_recovered_payload_preserves_attempt_history_without_failing_run(
    monkeypatch, _update_home, engine,
):
    from agent_worktrees import update_acquisition

    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: Path("/payload"))
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    attempts = []
    def run(argv, **kwargs):
        attempts.append(list(argv))
        return subprocess.CompletedProcess(
            argv, 1 if len(attempts) == 1 else 0, "", "ECONNRESET",
        )
    monkeypatch.setattr(subprocess, "run", run)
    update_events.start("engine")
    update_events.enter("payload_refresh")
    status = update_acquisition.acquire(
        "agent-example", "copilot-extensions", "copilot", engine=engine,
    )
    update_events.record("payload_refresh", "agent-example@copilot-extensions", status)
    assert update_events.finish(0) == 0
    document = _status(_update_home)
    assert document["run"]["outcome"] == ut.VERIFIED_CURRENT
    target = document["plugins"]["agent-example@copilot-extensions"]
    assert target["required"] and target["outcome"] == ut.VERIFIED_CURRENT
    assert "ECONNRESET" in target["advisory_detail"]
    events = [event for event in _events(_update_home)
              if event["component"] == "agent-example@copilot-extensions"]
    assert len(events) == 2
    assert events[0]["details"]["required"] is False
    assert events[1]["details"]["required"] is True


def test_missing_engine_installer_is_configuration_exit_3(wired, tmp_path, _update_home):
    (tmp_path / "agent-worktrees" / "scripts" / "install.sh").unlink()
    assert m.cmd_update(_args()) == 3
    assert _phase_outcomes(_update_home)["runtime_preflight"] == ut.CONFIGURATION_LIMITATION


def test_invalid_engine_deadline_is_configuration_exit_3(wired, monkeypatch, _update_home):
    monkeypatch.setenv("AGENT_WORKTREES_INSTALL_DEADLINE_SEC", "not-a-number")
    assert m.cmd_update(_args()) == 3
    assert _phase_outcomes(_update_home)["runtime_preflight"] == ut.CONFIGURATION_LIMITATION


def test_missing_installed_payload_fails_its_phase(wired, monkeypatch, _update_home):
    monkeypatch.setattr(m, "_find_installed_plugin_dir", lambda: None)
    assert m.cmd_update(_args()) == 1
    assert _phase_outcomes(_update_home)["payload_refresh"] == ut.COMMAND_FAILED


def test_unreadable_modules_manifest_fails_preflight(wired, monkeypatch, tmp_path,
                                                    _update_home):
    (tmp_path / "agent-worktrees" / "modules.json").write_text("{not json")
    monkeypatch.setattr(m, "_update_modules", runtime._update_modules)
    assert m.cmd_update(_args()) == 1
    phases = _phase_outcomes(_update_home)
    assert phases["runtime_preflight"] == ut.COMMAND_FAILED
    assert "engine_runtime" not in phases
    assert "module_update" not in phases
    assert _status(_update_home)["last_failure"]["summary"].startswith("modules.json")


def _advisories(home: Path) -> dict[str, str]:
    return {a["component"]: a["summary"] for a in _status(home)["advisories"]}


def test_required_binstub_failure_blocks_the_update(wired, monkeypatch, _update_home):
    def boom():
        raise RuntimeError("binstub dir unwritable")

    monkeypatch.setattr(installer, "reconcile_binstubs", boom)
    assert m.cmd_update(_args()) != 0
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.COMMAND_FAILED
    assert _phase_outcomes(_update_home)["binstubs"] == ut.COMMAND_FAILED
    assert "binstub dir unwritable" in status["last_failure"]["summary"]
    assert "project-commands" not in _advisories(_update_home)


@pytest.mark.parametrize("failure", ["import", "scan"])
def test_prune_failure_is_an_advisory(wired, monkeypatch, _update_home, failure):
    import sys

    from agent_worktrees.picker_support import pivots

    if failure == "import":
        monkeypatch.setitem(sys.modules, "agent_worktrees.picker_support.pivots", None)
        monkeypatch.delattr("agent_worktrees.picker_support.pivots", raising=False)
    else:
        def boom(**_kw):
            raise RuntimeError("registry scan failed")

        monkeypatch.setattr(pivots, "scan_pivot_registry", boom)
    monkeypatch.setattr(m, "_prune_stale_pivots_after_update",
                        update_cli._prune_stale_pivots_after_update)
    assert m.cmd_update(_args(no_prune_pivots=False)) == 0
    assert "pivot prune failed" in _advisories(_update_home)["pivots"]


def test_unknown_force_runtime_is_a_configuration_error(wired, monkeypatch, _update_home):
    ran = []
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda argv, *a, **k: (ran.append(argv),
                                                                   real_run(argv, *a, **k))[1])
    assert m.cmd_update(_args(force_runtime=["agent-typo", "agent-worktrees"])) == 3
    assert not any("install.sh" in " ".join(map(str, argv)) for argv in ran)
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.CONFIGURATION_LIMITATION
    assert "agent-typo" in status["last_failure"]["summary"]
    assert "agent-worktrees" not in status["last_failure"]["summary"].split("not found:")[1]


def test_eligible_force_runtime_redeploys(wired, monkeypatch, _update_home):
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "1.0.1")
    assert m.cmd_update(_args(force_runtime=["agent-worktrees"])) == 0
    aw = _status(_update_home)["plugins"]["agent-worktrees@copilot-extensions"]
    assert aw["changed"] is True


def _payload(root: Path, name: str, scope: str | None) -> Path:
    path = root / name
    path.mkdir(parents=True)
    manifest = {"name": name} if scope is None else {"name": name, "runtimeScope": scope}
    (path / "plugin.json").write_text(json.dumps(manifest))
    return path


@pytest.fixture
def eligibility(tmp_path, monkeypatch):
    plugin = tmp_path / "plugins" / "agent-worktrees"
    plugin.mkdir(parents=True)
    modules = ["mod-active", "mod-inactive", "mod-noscope", "mod-payload-only", "mod-gated"]
    (plugin / "modules.json").write_text(json.dumps(
        {"modules": [{"name": n, "source": n} for n in modules]}))
    for name, scope in (("mod-active", "universal"), ("mod-inactive", "universal"),
                        ("mod-noscope", None), ("mod-payload-only", "none"),
                        ("mod-gated", "machine-gated")):
        _payload(tmp_path / "plugins", name, scope)
    installed = {name: _payload(tmp_path / "installed", name, scope)
                 for name, scope in (("reg-active", "universal"), ("reg-payload-only", "none"),
                                     ("reg-inactive", "universal"))}
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", installed.get)
    monkeypatch.setattr(update_gate, "_machine_gate_blocks",
                        lambda name, scope, context: scope == "machine-gated")
    active = runtime._RegisteredPluginTarget(None, runtime._PluginActivation.ACTIVE)
    inactive = runtime._RegisteredPluginTarget(None, runtime._PluginActivation.INACTIVE)
    targets = {
        "mod-active": active, "mod-inactive": inactive, "mod-noscope": active,
        "mod-payload-only": active, "mod-gated": active, "reg-active": active,
        "reg-payload-only": active, "reg-inactive": inactive, "reg-missing": active,
    }
    return plugin, targets


def test_eligible_runtimes_follow_the_reconcile_plan(eligibility):
    plugin, targets = eligibility
    assert update_options.eligible_runtimes(_args(), targets, plugin) == {
        "agent-worktrees", "mod-active", "reg-active"}
    skipped = _args(skip_modules=["mod-active"])
    assert "mod-active" not in update_options.eligible_runtimes(skipped, targets, plugin)
    assert update_options.eligible_runtimes(_args(skip_modules=[]), targets, plugin) == {
        "agent-worktrees"}


@pytest.mark.parametrize("name", [
    "mod-inactive", "mod-noscope", "mod-payload-only", "mod-gated",
    "reg-payload-only", "reg-inactive", "reg-missing",
])
def test_force_runtime_that_would_be_skipped_is_exit_3(eligibility, _update_home, name):
    plugin, targets = eligibility
    update_events.start("engine")
    assert not update_options.check_forced_runtimes(
        _args(force_runtime=[name]), targets, plugin)
    assert update_events.finish(1) == 3
    assert name in _status(_update_home)["last_failure"]["summary"]


def test_force_runtime_for_a_deployable_target_passes(eligibility):
    plugin, targets = eligibility
    assert update_options.check_forced_runtimes(
        _args(force_runtime=["mod-active", "reg-active"]), targets, plugin)


@pytest.mark.parametrize(("status", "expected"), [
    ("agent-x: invalid installer deadline 'x'; expected integer seconds",
     (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("installation context invalid: bad receipt", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("machine detection failed: no registry", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("machine detection failed", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("invalid runtimeScope", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("'copilot' CLI not found on PATH (x)", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("agent-x: something unexpected", (ut.COMMAND_FAILED, None)),
    ("uninstall timed out", (ut.TIMEOUT, "timeout")),
    ("install exited 75", (ut.BLOCKED_BY_LOCK, "installer_exit_75")),
    ("plugin install exited 75", (ut.COMMAND_FAILED, None)),
    ("update exited 3", (ut.COMMAND_FAILED, None)),
    ("uninstall exited 1", (ut.COMMAND_FAILED, None)),
    ("payload not installed", (ut.COMMAND_FAILED, None)),
    ("SKIPPED (not allowed on this machine)", (ut.SKIPPED, None)),
])
def test_every_produced_status_has_its_documented_outcome(status, expected):
    assert update_events.classify_status(status) == expected


def test_payload_install_retry_timeout_is_a_timeout(monkeypatch):
    from agent_worktrees import activation_preservation

    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: Path("/inst/x"))
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(
                            argv, 3, "", "Plugin agent-x is not installed",
                        ))

    def timeout(argv, identity, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

    monkeypatch.setattr(activation_preservation, "run_install_preserving_activation", timeout)
    status = m._update_one_plugin_payload("agent-x", "copilot-extensions")
    assert status == "timed out"
    assert update_events.classify_status(status) == (ut.TIMEOUT, "timeout")



def test_missing_copilot_cli_is_configuration_exit_3(wired, monkeypatch, _update_home):
    real_run = subprocess.run

    def run(argv, *a, **k):
        if "plugin" in map(str, argv):
            raise FileNotFoundError(2, "No such file or directory", str(argv[0]))
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", run)
    assert m.cmd_update(_args()) == 3
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.CONFIGURATION_LIMITATION
    assert _phase_outcomes(_update_home)["payload_refresh"] == ut.CONFIGURATION_LIMITATION
    assert "not found" in status["plugins"]["agent-worktrees@copilot-extensions"]["detail"]


def _module_payload(tmp_path, monkeypatch):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    (plugin / "modules.json").write_text(json.dumps(
        {"modules": [{"name": "agent-example", "source": "agent-example"}]}))
    module = tmp_path / "agent-example"
    (module / "scripts").mkdir(parents=True)
    (module / "scripts" / "install.sh").write_text("#!/bin/sh\n")
    (module / "plugin.json").write_text(
        json.dumps({"name": "agent-example", "runtimeScope": "universal"}))
    environment = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_CONTEXT"}
    monkeypatch.setattr(reconcile, "payload_version", lambda path: "3.0.0")
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "2.0.0")
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, tmp_path / "module-runtime"))
    monkeypatch.setattr(reconcile, "_zero_downtime_update", lambda path: False)
    launched = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: launched.append(a))
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        None, runtime._PluginActivation.ACTIVE)}
    return plugin, targets, launched


def _run_modules(plugin, targets, home):
    update_events.start("engine")
    ok = runtime._update_modules(plugin, "linux", None, targets=targets)
    code = update_events.finish(0 if ok else 1)
    return ok, code, _status(home)


@pytest.mark.parametrize(("error", "code"), [
    (PermissionError(errno.EACCES, "Permission denied", "plugin.json"), ut.PERMISSION_DENIED),
    (OSError(errno.EIO, "Input/output error", "plugin.json"), ut.IO_ERROR),
], ids=["permission", "io"])
def test_module_unreadable_deadline_manifest_keeps_its_os_error_class(
    tmp_path, monkeypatch, _update_home, error, code,
):
    plugin, targets, launched = _module_payload(tmp_path, monkeypatch)

    def unreadable(*a, **k):
        raise error

    monkeypatch.setattr(runtime, "_installer_timeout", unreadable)
    ok, exit_code, status = _run_modules(plugin, targets, _update_home)
    target = status["plugins"]["agent-example@copilot-extensions"]
    assert ok is False and launched == []
    assert (exit_code, target["outcome"], target["error_code"]) == (1, ut.COMMAND_FAILED, code)
    assert "plugin.json" in target["detail"]


def test_module_installation_context_error_records_its_cause(
    tmp_path, monkeypatch, _update_home,
):
    plugin, targets, _launched = _module_payload(tmp_path, monkeypatch)

    def invalid(*a, **k):
        raise ValueError("receipt names https://user:hunter2@example.com/missing")

    monkeypatch.setattr(reconcile, "runtime_installer_environment", invalid)
    ok, exit_code, status = _run_modules(plugin, targets, _update_home)
    target = status["plugins"]["agent-example@copilot-extensions"]
    assert ok is False and exit_code == 3
    assert "example.com/missing" in target["detail"]
    assert "hunter2" not in json.dumps(status)
    assert "hunter2" not in Path(status["run"]["events_path"]).read_text(encoding="utf-8")


def test_engine_installation_context_error_records_its_cause(wired, monkeypatch, _update_home):
    def invalid(*a, **k):
        raise ValueError("receipt names https://user:hunter2@example.com/missing")

    monkeypatch.setattr(reconcile, "runtime_installer_environment", invalid)
    assert m.cmd_update(_args()) == 3
    status = _status(_update_home)
    assert "example.com/missing" in status["plugins"][
        "agent-worktrees@copilot-extensions"]["detail"]
    assert "example.com/missing" in status["last_failure"]["summary"]
    assert "hunter2" not in json.dumps(status)
    assert "hunter2" not in Path(status["run"]["events_path"]).read_text(encoding="utf-8")


def test_engine_unreadable_deadline_manifest_is_not_a_configuration_error(
    wired, monkeypatch, _update_home,
):
    def unreadable(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device", "plugin.json")

    monkeypatch.setattr(update_cli, "_installer_timeout", unreadable)
    assert m.cmd_update(_args()) == 1
    aw = _status(_update_home)["plugins"]["agent-worktrees@copilot-extensions"]
    assert aw["outcome"] == ut.COMMAND_FAILED and "No space left" in aw["detail"]


def test_registered_runtime_invalid_declared_deadline_is_configuration(
    registered_runtime, monkeypatch, tmp_path, _update_home,
):
    environment = {k: v for k, v in os.environ.items()
                   if k != "COPILOT_EXTENSIONS_CONTEXT" and not k.endswith("_DEADLINE_SEC")}
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, registered_runtime))
    (tmp_path / "agent-example" / "plugin.json").write_text(
        json.dumps({"name": "agent-example", "installerDeadlineSeconds": 0}))
    update_events.start("engine")
    status = runtime._reconcile_one_runtime("agent-example", "linux", force=False)
    assert status.startswith("invalid installer deadline: ")
    assert update_events.finish(1) == 3


@pytest.mark.parametrize(("cause", "exit_code", "code"), [
    ("[Errno 13] Permission denied: 'machines.yaml'", 1, ut.PERMISSION_DENIED),
    ("an explicit execution space is required", 3, "configuration"),
], ids=["permission", "configuration"])
def test_module_machine_detection_failure_records_its_cause(
    tmp_path, monkeypatch, _update_home, cause, exit_code, code,
):
    plugin, targets, launched = _module_payload(tmp_path, monkeypatch)
    monkeypatch.setattr(reconcile, "manifest_runtime_scope", lambda path: "machine-gated")

    def undetectable(*a, **k):
        raise runtime.MachineDetectionError(cause)

    monkeypatch.setattr(runtime, "_machine_gate_blocks", undetectable)
    ok, actual, status = _run_modules(plugin, targets, _update_home)
    target = status["plugins"]["agent-example@copilot-extensions"]
    assert ok is False and launched == [] and actual == exit_code
    assert target["error_code"] == code and cause in target["detail"]


def test_force_runtime_with_unreadable_modules_manifest_keeps_the_os_error(
    tmp_path, monkeypatch, _update_home,
):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    manifest = plugin / "modules.json"
    manifest.write_text(json.dumps({"modules": [{"name": "mod-a"}]}))
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == manifest:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: None)
    targets = {"mod-a": runtime._RegisteredPluginTarget(None, runtime._PluginActivation.ACTIVE)}
    update_events.start("engine")
    assert not update_options.check_forced_runtimes(
        _args(force_runtime=["mod-a"]), targets, plugin)
    assert update_events.finish(1) == 1
    assert _status(_update_home)["run"]["outcome"] == ut.COMMAND_FAILED
    forced = [e for e in _events(_update_home) if e["component"] == "force-runtime"]
    assert forced[-1]["error"]["code"] == ut.PERMISSION_DENIED
