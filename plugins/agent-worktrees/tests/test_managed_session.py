"""Admission precedes policy and lifecycle, independent of global installation."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import pytest
from agent_procutil import no_window_kwargs

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WRITE_ALIASES = (
    "str_replace", "str_replace_editor", "str_replace_based_edit_tool",
    "write", "write_file", "insert", "new_file", "multi_edit",
)
PATCH_NATIVE_CASES = (
    "patch-input", "patch-patch", "patch-command", "patch-json", "patch-freeform",
    "patch-crlf", "patch-headers", "patch-union", "patch-batch", "patch-duplicates",
    "patch-limit", "patch-field-bound", "patch-real-policy",
)
PATCH_PROTECTED_CASES = (
    "patch-protected-input", "patch-protected-patch", "patch-protected-command",
    "patch-protected-move", "patch-protected-direct", "patch-protected-structured",
    "patch-protected-batch", "patch-protected-freeform", "patch-protected-commented-json",
)
NATIVE_WRITE_PHASES = {
    "command-entry", "helper-loaded", "input-complete", "output-complete",
    "script-entry", "membership-start", "membership-complete",
    "native-proof-start", "policy-start", "prepared-event-json", "prepared-paths",
    "prepared-marker-opened", "prepared-marker-json", "prepared-marker-decoded",
    "json-entry", "json-utility-load", "json-utility-ready", "json-web-load",
    "json-resolver-type", "json-resolver-create", "json-serializer-create", "json-deserialize",
    "command-complete", "admission-start", "admission-complete", "read-proof-start",
    "read-proof-complete", "source-proof-start", "source-proof-complete", "policy-complete",
    "runtime-start", "runtime-complete",
}
NATIVE_WRITE_PHASE_WRITER = (
    "function Write-NativeWriteFixturePhase([string]$Name) { "
    "[Console]::Error.WriteLine('NATIVE_WRITE_FIXTURE_PHASE ' + "
    "[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString("
    "[Globalization.CultureInfo]::InvariantCulture) + ' ' + $Name) }; "
    "Write-NativeWriteFixturePhase 'command-entry'; "
)
NATIVE_WRITE_LOAD_WRITER = """
function Write-NativeWriteFixtureAssemblyState([string]$Name) {
    $utility = 0
    $web = 0
    foreach ($assembly in [AppDomain]::CurrentDomain.GetAssemblies()) {
        $assemblyName = $assembly.GetName().Name
        if ($assemblyName -ceq 'Microsoft.PowerShell.Commands.Utility') { $utility = 1 }
        if ($assemblyName -ceq 'System.Web.Extensions') { $web = 1 }
    }
    $process = [Diagnostics.Process]::GetCurrentProcess()
    try { $cpu = $process.TotalProcessorTime.Ticks }
    finally { $process.Dispose() }
    [Console]::Error.WriteLine('NATIVE_WRITE_FIXTURE_PHASE ' +
        [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString(
            [Globalization.CultureInfo]::InvariantCulture) + ' ' + $Name +
        ' cpu_ticks=' + $cpu.ToString([Globalization.CultureInfo]::InvariantCulture) +
        ' utility=' + $utility.ToString() + ' web=' + $web.ToString())
}
"""
# Prior reader retained to exercise additive-declaration compatibility.
NATIVE_READ_V1 = r"""
function Test-NativePreToolSources([bool]$WriteProof = $false) {
    try {
        $policy = Read-PreToolJson ([IO.Path]::Combine($PSScriptRoot, 'hook-read-policy.json')) 4096
        if ($policy.schema -isnot [string] -or $policy.schema -cne 'agent-worktrees.native-read-policy' -or
            ($policy.version -isnot [int] -and $policy.version -isnot [long]) -or $policy.version -ne 1 -or
            $policy.guards -isnot [Collections.IDictionary]) { return $false }
        $names = @('statelessness_guard.py', 'cross_repo_guard.py', 'anchor_write_guard.py', 'pr_supersede_guard.py')
        if ($policy.guards.Count -ne $names.Count) { return $false }
        $sources = $policy.guards
        if ($WriteProof) {
            $dependencies = $policy.nativeWriteDependencies
            $extra = @('tool_call_forms.py', 'guarded_anchor_map.py')
            if ($dependencies -isnot [Collections.IDictionary] -or $dependencies.Count -ne $extra.Count) {
                return $false
            }
            $sources = @{}
            foreach ($guard in $names) { $sources[$guard] = $policy.guards[$guard] }
            foreach ($dependency in $extra) { $sources[$dependency] = $dependencies[$dependency] }
            $names += $extra
        }
        foreach ($guard in $names) {
            $expected = $sources[$guard]
            if ($expected -isnot [string] -or $expected -cnotmatch '^[0-9a-f]{64}$') { return $false }
            $path = [IO.Path]::Combine($PSScriptRoot, $guard)
            $info = [IO.FileInfo]::new($path)
            if (-not $info.Exists -or $info.Length -gt 131072 -or
                ($info.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $false }
            $text = [IO.File]::ReadAllText($path, $utf8).Replace("`r`n", "`n")
            $hasher = [Security.Cryptography.SHA256]::Create()
            try {
                $actual = ([BitConverter]::ToString($hasher.ComputeHash($utf8.GetBytes($text)))).Replace('-', '').ToLowerInvariant()
            } finally { $hasher.Dispose() }
            if ($actual -cne $expected) { return $false }
        }
        return $true
    } catch { return $false }
}
function Test-NativePreToolRead([object]$Payload, [bool]$PreparedMembership = $false) {
    if (($env:COPILOT_EXTENSIONS_CONTEXT -and -not $PreparedMembership) -or
        $Payload -isnot [Collections.IDictionary]) { return $false }
    if ($Payload.ContainsKey('toolCalls')) {
        if (-not $PreparedMembership) { return $false }
        $calls = Get-NativePreToolBatch $Payload['toolCalls']
        if ($null -eq $calls) { return $false }
        foreach ($call in $calls) {
            if ($call['name'] -cnotin @('view', 'rg', 'glob')) { return $false }
        }
        return Test-NativePreToolSources -WriteProof $true
    }
    $name = $Payload.toolName
    if (-not $name) { $name = $Payload.tool_name }
    if ($name -isnot [string] -or $name -cnotin @('view', 'rg', 'glob')) { return $false }
    return Test-NativePreToolSources
}
"""


def native_write_fixture_stderr(stderr):
    phases, errors = [], []
    for line in stderr.splitlines(keepends=True):
        match = re.fullmatch(
            rb"NATIVE_WRITE_FIXTURE_PHASE ([0-9]{1,20}) ([a-z-]+)"
            rb"( cpu_ticks=[0-9]{1,20} utility=[01] web=[01])?\r?\n", line,
        )
        if (
            match and match[2].decode("ascii") in NATIVE_WRITE_PHASES
            and (not match[3] or match[2] in {b"json-web-load", b"json-resolver-type"})
        ):
            phases.append(
                f"{match[1].decode('ascii')} {match[2].decode('ascii')}"
                + (match[3].decode("ascii") if match[3] else "")
            )
        else:
            errors.append(line)
    return "\n".join(phases), b"".join(errors)


def instrument_prepared_admission(source):
    prepared_function = "function Test-PreparedManagedWorktreesSession {"
    prefix, prepared = source.split(prepared_function, 1)
    opened = "        $openedLength = $stream.Length"
    assert prepared.count(opened) == 1
    prepared = prepared.replace(
        opened, "Write-NativeWriteFixturePhase 'prepared-marker-opened'\n" + opened, 1,
    )
    source = prefix + prepared_function + prepared
    for boundary, statement in (
        ("prepared-event-json", "        $event = ConvertFrom-ManagedWorktreesJson $Raw"),
        ("json-entry", "    if (-not $Text.TrimStart().StartsWith('{')) { throw 'Expected a JSON object' }"),
        ("json-utility-load", "    if (-not $script:managedWorktreesUtilityAssembly) {"),
        ("json-utility-ready", "    if ($PSVersionTable.PSVersion.Major -ge 6) {"),
        ("json-web-load", "        [void][Reflection.Assembly]::Load('System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35')"),
        ("json-resolver-type", "        $resolverType = $script:managedWorktreesUtilityAssembly.GetType("),
        ("json-resolver-create", "        $resolver = [Activator]::CreateInstance($resolverType, $true)"),
        ("json-serializer-create", "        $script:managedWorktreesJson = [System.Web.Script.Serialization.JavaScriptSerializer]::new($resolver)"),
        ("json-deserialize", "    return ,($script:managedWorktreesJson.DeserializeObject($Text))"),
        ("prepared-paths", "        $workspace = Get-PreparedWorktreesPath $cwd"),
        ("prepared-marker-json", "        $value = ConvertFrom-ManagedWorktreesJson $text.TrimStart([char]0xfeff)"),
        ("prepared-marker-decoded", "        $scope = $value['scope']"),
    ):
        assert statement in source
        writer = (
            "Write-NativeWriteFixtureAssemblyState"
            if boundary in {"json-web-load", "json-resolver-type"} else "Write-NativeWriteFixturePhase"
        )
        source = source.replace(statement, f"{writer} '{boundary}'\n" + statement, 1)
    return NATIVE_WRITE_LOAD_WRITER + source


def qualify_complete_native_entry(
    scripts, entry, executable, event, env, workspace, capsys, *, literal_read=False,
):
    entry_started = time.monotonic()
    shell_event = "toolCalls" not in event and event.get("toolName") == "powershell"
    source_names = (
        "pre-tool-hook.ps1", "managed-session.ps1", "hook-read-policy.json",
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py",
        "pr_supersede_guard.py", "tool_call_forms.py", "guarded_anchor_map.py",
    )
    if shell_event:
        source_names += (
            "hook_policy_process.py", "hook_client.py", "hook_pre_policy.py", "hook_pre_relevance.py",
        )
        for name in source_names:
            if name not in {"pre-tool-hook.ps1", "managed-session.ps1"}:
                assert (scripts / name).read_bytes() == (SCRIPTS / name).read_bytes()
    source_digests = {
        name: hashlib.sha256((SCRIPTS / name).read_bytes()).hexdigest() for name in source_names
    }
    original = {
        name: (scripts / name).read_bytes() for name in ("pre-tool-hook.ps1", "managed-session.ps1")
    }
    contract = (scripts / "hook-read-policy.json").read_bytes()
    assert contract == (SCRIPTS / "hook-read-policy.json").read_bytes()
    instrumented = {name: content.decode("utf-8") for name, content in original.items()}
    for filename, name, parameters, phase in (
        ("managed-session.ps1", "Test-ManagedWorktreesSession",
         "[Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw, [ref]$Prepared, [ref]$Facts",
         "admission"),
        ("pre-tool-hook.ps1", "Test-NativePreToolRead",
         "[object]$Payload, [bool]$PreparedMembership = $false", "read-proof"),
        ("pre-tool-hook.ps1", "Test-NativePreToolSources",
         "[bool]$WriteProof = $false, [string]$ShellCommand", "source-proof"),
        ("pre-tool-hook.ps1", "Invoke-PreToolPolicy",
         "[string]$Python, [string]$Payload, [ref]$Diagnostic", "policy"),
    ):
        declaration = f"function {name}"
        assert instrumented[filename].count(declaration) == 1
        wrapper = (
            f"function {name} {{\n    param({parameters})\n"
            f"    Write-NativeWriteFixturePhase '{phase}-start'\n"
            f"    try {{ return FixtureOriginal{name} @PSBoundParameters }}\n"
            f"    finally {{ Write-NativeWriteFixturePhase '{phase}-complete' }}\n}}\n"
        )
        instrumented[filename] = instrumented[filename].replace(
            declaration, wrapper + f"function FixtureOriginal{name}", 1,
        )
    script_entry = "$ErrorActionPreference = 'Stop'"
    assert instrumented["pre-tool-hook.ps1"].count(script_entry) == 1
    instrumented["pre-tool-hook.ps1"] = instrumented["pre-tool-hook.ps1"].replace(
        script_entry, script_entry + "\nWrite-NativeWriteFixturePhase 'script-entry'", 1,
    )
    if shell_event:
        for phase, statement in (
            ("runtime-start", "    try { Set-Location -LiteralPath $env:USERPROFILE -ErrorAction Stop }"),
            ("runtime-complete", "    $script:preToolBoundary = 'policy worker execution'"),
        ):
            assert instrumented["pre-tool-hook.ps1"].count(statement) == 1
            instrumented["pre-tool-hook.ps1"] = instrumented["pre-tool-hook.ps1"].replace(
                statement, f"Write-NativeWriteFixturePhase '{phase}'\n" + statement, 1,
            )
    writer = "$global:nativeEntryClock=[Diagnostics.Stopwatch]::StartNew(); " + NATIVE_WRITE_PHASE_WRITER.replace(
        "[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()", "$global:nativeEntryClock.ElapsedMilliseconds",
    )
    samples = []
    target = workspace / "efforts" / "private.md"
    carrier = None
    if shell_event:
        carrier = "toolArgs" if "toolArgs" in event else "tool_input"
        command = f'Set-Content "{target}" -Value "leak"'
        protected = {**event, carrier: command if isinstance(event[carrier], str) else {"command": command}}
    else:
        protected = {**event, "toolCalls": [
            *event["toolCalls"], {"name": "edit", "args": {"path": "efforts/private.md"}},
        ]}

    def report_progress(variant, state):
        with capsys.disabled():
            print(json.dumps({"complete_native_entry_progress": {
                "shell": str(executable), "cell": bool(env.get("COPILOT_EXTENSIONS_CONTEXT")),
                "argument_carrier": (
                    carrier + (":raw" if isinstance(event[carrier], str) else ":object")
                    if carrier else None
                ),
                "variant": variant, "state": state,
                "helper_seconds": time.monotonic() - entry_started,
            }}), flush=True)

    assert not target.exists()
    try:
        for variant, payload in (
            ("phase-free-before", event), ("attributed-read", event),
            ("phase-free-after", event), ("attributed-protected", protected),
        ):
            attributed = variant.startswith("attributed-")
            native_literal = literal_read and variant == "attributed-read"
            selected_contract = contract
            if shell_event and variant != "attributed-protected" and not native_literal:
                prior = json.loads(contract)
                if any(key in prior for key in ("nativeShellReadCommands", "nativeShellReadDependencies")):
                    prior.pop("nativeShellReadCommands", None)
                    prior.pop("nativeShellReadDependencies", None)
                    selected_contract = (json.dumps(prior, indent=2) + "\n").encode("utf-8")
            (scripts / "hook-read-policy.json").write_bytes(selected_contract)
            for name, content in original.items():
                (scripts / name).write_bytes(
                    instrumented[name].encode("utf-8") if attributed else content,
                )
            tail = (
                "Write-NativeWriteFixturePhase 'command-complete'; " if attributed else ""
            ) + (
                "[Console]::Error.WriteLine('NATIVE_ENTRY_BUILD ' + $PSVersionTable.PSVersion.ToString()); "
                "[Console]::Error.WriteLine('NATIVE_ENTRY_MANAGEMENT ' + "
                "[int][bool](Get-Module -Name Microsoft.PowerShell.Management))"
            )
            command_entry = entry
            if attributed:
                command_entry = entry.replace(
                    "catch { [Console]::Error.WriteLine($e) }",
                    "catch { [Console]::Error.WriteLine($_.ToString()); [Console]::Error.WriteLine($e) }",
                )
            command = (writer if attributed else "") + "try { " + command_entry + " } finally { " + tail + " }"
            report_progress(variant, "command-start")
            started = time.monotonic()
            try:
                result = subprocess.run(
                    [executable, "-NoProfile", "-NonInteractive", "-Command", command],
                    input=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    capture_output=True, timeout=10, cwd=workspace, env=env, **no_window_kwargs(),
                )
            except subprocess.TimeoutExpired as error:
                raise AssertionError(
                    f"{variant} exceeded the unchanged 10s child limit; "
                    f"stdout={error.stdout!r}; stderr={error.stderr!r}"
                ) from error
            elapsed = time.monotonic() - started
            builds, management, stderr = [], [], []
            for line in result.stderr.splitlines(keepends=True):
                match = re.fullmatch(rb"NATIVE_ENTRY_BUILD ([0-9][0-9A-Za-z.+-]{0,127})\r?\n", line)
                module = re.fullmatch(rb"NATIVE_ENTRY_MANAGEMENT ([01])\r?\n", line)
                if match:
                    builds.append(match[1].decode("ascii"))
                elif module:
                    management.append(int(module[1]))
                else:
                    stderr.append(line)
            progress, errors = native_write_fixture_stderr(b"".join(stderr))
            phases = [(int(tick), name) for tick, name in (line.split(" ", 1) for line in progress.splitlines())]
            sample = {
                "variant": variant, "outer_seconds": elapsed,
                "shell_version": builds[0] if len(builds) == 1 else None,
                "management_module_loaded": bool(management[0]) if len(management) == 1 else None,
                "contract_sha256": hashlib.sha256(selected_contract).hexdigest(),
                "literal_proof_present": "nativeShellReadCommands" in json.loads(selected_contract),
                "phases_monotonic_ms": phases,
                "stdout": result.stdout.decode("utf-8", errors="backslashreplace"), "validated": False,
            }
            samples.append(sample)
            assert result.returncode == 0 and not errors, (variant, result.stdout, result.stderr)
            assert len(builds) == 1, (variant, builds, result.stderr)
            assert len(management) == 1, (variant, management, result.stderr)
            assert bool(management[0]) is (
                shell_event and not native_literal or variant == "attributed-protected"
            ), (variant, sample)
            assert [tick for tick, _ in phases] == sorted(tick for tick, _ in phases), phases
            names = [name for _, name in phases]
            if attributed:
                required = [
                    "command-entry", "script-entry", "admission-start", "admission-complete",
                    "read-proof-start",
                ]
                if shell_event and not native_literal:
                    required += [
                        "source-proof-start", "source-proof-complete", "read-proof-complete",
                        "runtime-start", "runtime-complete",
                        "policy-start", "policy-complete",
                    ]
                elif variant == "attributed-read":
                    required += ["source-proof-start", "source-proof-complete", "read-proof-complete"]
                else:
                    required += [
                        "read-proof-complete", "source-proof-start", "source-proof-complete",
                        "policy-start", "policy-complete",
                    ]
                required += ["command-complete"]
                assert names == required, (variant, phases)
            else:
                assert not phases, (variant, phases)
            if variant == "attributed-protected":
                assert json.loads(result.stdout)["permissionDecision"] == "deny", result.stdout
            elif shell_event and not native_literal:
                assert json.loads(result.stdout) == {}, result.stdout
            else:
                assert result.stdout == b"", result.stdout
            assert not target.exists()
            assert (scripts / "hook-read-policy.json").read_bytes() == selected_contract
            sample["validated"] = True
            report_progress(variant, "validated")
    finally:
        for name, content in original.items():
            (scripts / name).write_bytes(content)
        (scripts / "hook-read-policy.json").write_bytes(contract)
        with capsys.disabled():
            print(json.dumps({"complete_native_entry": {
                "shell": str(executable),
                "executable_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
                "registered_command_sha256": hashlib.sha256(entry.encode("utf-8")).hexdigest(),
                "source_sha256": source_digests, "cell": bool(env.get("COPILOT_EXTENSIONS_CONTEXT")),
                "event_kind": "literal-shell-read" if literal_read else "shell-policy" if shell_event else "native-read",
                "argument_carrier": (
                    carrier + (":raw" if isinstance(event[carrier], str) else ":object")
                    if carrier else None
                ),
                "samples": samples, "complete": len(samples) == 4 and all(sample["validated"] for sample in samples),
                "limits": "Fixture timing only; phases include instrumentation. Outer-minus-last-phase includes startup/exit and marker/module-observer overhead, not isolated startup. Ordered controls are not subtractive attribution or a latency distribution.",
            }}))


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_native_write_fixture_stderr_preserves_errors(newline):
    phase = b"NATIVE_WRITE_FIXTURE_PHASE 123 command-entry" + newline
    before = b"NATIVE_WRITE_FIXTURE_PHASE 124 json-web-load cpu_ticks=250000 utility=1 web=0" + newline
    after = b"NATIVE_WRITE_FIXTURE_PHASE 126 json-resolver-type cpu_ticks=500000 utility=1 web=1" + newline
    errors = (
        newline + b"real error\xff" + newline
        + b"NATIVE_WRITE_FIXTURE_PHASE 124 unknown-stage" + newline
        + b"NATIVE_WRITE_FIXTURE_PHASE invalid json-entry" + newline
        + b"NATIVE_WRITE_FIXTURE_PHASE 127 json-web-load cpu_ticks=NaN utility=1 web=0" + newline
        + b"NATIVE_WRITE_FIXTURE_PHASE 128 command-entry cpu_ticks=1 utility=1 web=0" + newline
        + b"NATIVE_WRITE_FIXTURE_PHASE 129 json-web-load cpu_ticks=1 utility=1 web=2" + newline
        + b"NATIVE_WRITE_FIXTURE_PHASE 130 json-web-load cpu_ticks=" + b"1" * 21 + b" utility=1 web=0" + newline
        + b"NATIVE_WRITE_FIXTURE_PHASE 125 json-entry"
    )
    assert native_write_fixture_stderr(phase + before + after + errors) == (
        "123 command-entry\n124 json-web-load cpu_ticks=250000 utility=1 web=0\n"
        "126 json-resolver-type cpu_ticks=500000 utility=1 web=1", errors,
    )


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows timeout diagnostics")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_native_write_fixture_stderr_survives_timeout(tmp_path, shell):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    command = (
        NATIVE_WRITE_PHASE_WRITER + NATIVE_WRITE_LOAD_WRITER
        + "Write-NativeWriteFixtureAssemblyState 'json-web-load'; "
        + "[Console]::Error.WriteLine('real fixture error'); [Threading.Thread]::Sleep(60000)"
    )
    with pytest.raises(subprocess.TimeoutExpired) as captured:
        subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            input=b"", capture_output=True, timeout=10, cwd=tmp_path, **no_window_kwargs(),
        )
    progress, errors = native_write_fixture_stderr(captured.value.stderr)
    assert re.fullmatch(
        r"[0-9]+ command-entry\n[0-9]+ json-web-load cpu_ticks=[0-9]+ utility=[01] web=[01]", progress,
    ), progress
    assert errors == b"real fixture error\r\n"
    assert captured.value.stdout == b""


def load(name):
    spec = importlib.util.spec_from_file_location(f"admission_test_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse", "sessionStart", "sessionEnd"])
def test_unmanaged_event_never_dispatches(kind, tmp_path, monkeypatch, capsys):
    client = load("hook_client")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("COPILOT_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "sessionId": "unmanaged", "cwd": str(tmp_path), "toolName": "edit",
        "toolArgs": {"path": "source.py"},
    })))

    def forbidden(*args, **kwargs):
        pytest.fail("unmanaged event reached policy or lifecycle activation")

    monkeypatch.setattr(client, "decide", forbidden)
    monkeypatch.setattr(client, "_enrich_session_payload", forbidden)
    monkeypatch.setattr(client, "_enrich_session_end_payload", forbidden)
    assert client.main([kind]) == 0
    output = capsys.readouterr()
    assert output.out.strip() in {"", "{}"}
    assert not output.err
    assert not (tmp_path / ".copilot").exists()


def fixture_project(tmp_path):
    anchor = tmp_path / "anchor-\u00e9"
    git_dir = anchor / ".git"
    for folder in ("objects", "refs"):
        (git_dir / folder).mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    registry = tmp_path / ".agent-worktrees"
    registry.mkdir()
    (registry / "projects.yaml").write_text('projects:\n  example:\n    config_dir: "~/.example"\n')
    (registry / "repos.yaml").write_text(
        "repos:\n  example:\n    class: worktree\n"
        + f"    {'windows' if os.name == 'nt' else 'linux'}: {json.dumps(str(anchor))}\n"
    )
    return anchor, registry


def fixture_linked_worktree(tmp_path, anchor, *, relative=False):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    admin = anchor / ".git" / "worktrees" / "example-worktree"
    admin.mkdir(parents=True)
    (admin / "HEAD").write_text("ref: refs/heads/work\n")
    (admin / "commondir").write_text("../..\n")
    (admin / "gitdir").write_text(
        (os.path.relpath(worktree / ".git", admin) if relative else str(worktree / ".git")) + "\n",
        encoding="utf-8",
    )
    (worktree / ".git").write_text("gitdir: " + str(admin) + "\n", encoding="utf-8")
    return worktree, admin


def fixture_checkout(path):
    for folder in ("objects", "refs"):
        (path / ".git" / folder).mkdir(parents=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    return path


def test_positive_membership_is_cached_but_registration_alone_is_not(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    payload = {"sessionId": "owned", "cwd": str(anchor)}
    assert module.admitted(payload, home=tmp_path)
    original = module._entries
    monkeypatch.setattr(module, "_entries", lambda *args: pytest.fail("warm membership reparsed registries"))
    assert module.admitted(payload, home=tmp_path)
    monkeypatch.setattr(module, "_entries", original)
    (registry / "projects.yaml").write_text("projects: {}\n")
    assert not module.admitted(payload, home=tmp_path)
    (registry / "projects.yaml").write_text("projects:\n  example: {}\n")
    assert module.admitted(payload, home=tmp_path)
    assert not module.admitted({**payload, "cwd": str(tmp_path)}, home=tmp_path)
    assert not module.admitted({**payload, "sessionId": "../bad"}, home=tmp_path)


def test_native_session_association_avoids_warm_git_traversal(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    session_dir = tmp_path / ".copilot" / "session-state" / "owned"
    session_dir.mkdir(parents=True)
    (session_dir / "agent-worktrees.json").write_text(json.dumps({
        "version": 2, "session_id": "owned",
        "relations": [{"role": "controller", "project": "example"}],
    }))
    unrelated = fixture_checkout(tmp_path / "unrelated")
    assert not module.admitted({"sessionId": "owned", "cwd": str(unrelated)}, home=tmp_path)
    payload = {"sessionId": "owned", "cwd": str(anchor)}
    assert module.admitted(payload, home=tmp_path)
    marker = session_dir / "agent-worktrees" / "managed-session.json"
    assert marker.is_file()
    original = module.repository_identity
    monkeypatch.setattr(module, "repository_identity", lambda *a, **k: pytest.fail("warm Git traversal"))
    assert module.admitted(payload, home=tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    monkeypatch.setattr(module, "repository_identity", original)
    assert not module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("relative", [False, True])
def test_linked_worktree_requires_the_adopted_common_directory(tmp_path, monkeypatch, relative):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, _ = fixture_project(tmp_path)
    worktree, admin = fixture_linked_worktree(tmp_path, anchor, relative=relative)
    nested = worktree / "src"
    nested.mkdir()
    payload = {"sessionId": "owned", "cwd": str(nested)}
    assert module.admitted(payload, home=tmp_path)
    (admin / "commondir").write_text(str(tmp_path / "foreign") + "\n")
    assert not module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("tags", ["[selected, local]", "\n    - selected\n    - local"])
def test_generated_registry_admission_needs_no_yaml_install(tmp_path, monkeypatch, tags):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    anchor, registry = fixture_project(tmp_path)
    with (registry / "repos.yaml").open("a") as stream:
        stream.write(
            f"    tags: {tags}\n"
            "    metadata:\n      nested: [ignored]\n"
            f"  unrelated:\n    class: reference\n    tags: {tags}\n"
        )
    assert module.admitted({"sessionId": "fresh", "cwd": str(anchor)}, home=tmp_path)


@pytest.mark.parametrize("container", [False, True])
@pytest.mark.parametrize("implementation", ["standalone", "runtime"])
def test_admission_shares_kernel_and_container_platform_detection(
    tmp_path, monkeypatch, container, implementation,
):
    from types import SimpleNamespace

    module = load("managed_session")
    if implementation == "runtime":
        from machine_transport import detect_platform

        monkeypatch.setattr(module, "detect_platform", detect_platform)
    detector = sys.modules[module.detect_platform.__module__]
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    if container:
        monkeypatch.setenv("WSL_DISTRO_NAME", "inherited")
    else:
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(detector, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(detector, "os", SimpleNamespace(
        uname=lambda: SimpleNamespace(release="6.6-microsoft-standard-WSL2"),
    ))

    class Probe:
        def __init__(self, path):
            self.path = path

        def is_file(self):
            return False

        def read_text(self, **kwargs):
            return "0::/docker/example" if container else "0::/user.slice"

    monkeypatch.setattr(detector, "Path", Probe)
    anchor, registry = fixture_project(tmp_path)
    other = tmp_path / "other"
    selected = "linux" if container else "wsl"
    paths = {name: str(anchor if name == selected else other) for name in ("windows", "linux", "wsl")}
    (registry / "repos.yaml").write_text(json.dumps({"repos": {"example": paths}}))
    assert module.admitted({"sessionId": "classified", "cwd": str(anchor)}, home=tmp_path)


def test_sdk_cell_cache_revokes_changed_resolver_ownership(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(*args, **(kwargs | no_window_kwargs())),
    ))
    anchor, legacy = fixture_project(tmp_path)
    payload = fixtures._payload(tmp_path, "selected")
    for name in ("managed_session.py", "execution_platform.py", "managed_project_identity.py",
                 "remote_identity.py", "guarded_anchor_map.py",
                 "hook_policy_process.py", "hook_process_identity.py"):
        shutil.copyfile(SCRIPTS / name, payload / "scripts" / name)
    context, registry = fixtures._stamp(
        tmp_path / "durable", payload, marketplace="selected", repository="example/selected",
    )
    for name in ("projects.yaml", "repos.yaml"):
        shutil.copyfile(legacy / name, registry / name)
    namespace = json.loads(context.read_text())["namespaceReceipt"]
    driver = tmp_path / "cell-cache.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import {readFileSync, unlinkSync, writeFileSync} from "node:fs";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, python, workingDirectory, namespace, manifest] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot, python});
const input = {sessionId: "cell-cache", workingDirectory};
assert.equal(await check(input), true);
for (const path of [namespace, manifest]) {
  const saved = readFileSync(path);
  for (const damage of ["remove", "corrupt"]) {
    if (damage === "remove") unlinkSync(path);
    else writeFileSync(path, "{}");
    assert.equal(check.current(input), false, "resolver ownership invalidates warm admission");
    assert.equal(await check(input), false);
    writeFileSync(path, saved);
    assert.equal(await check(input), true, "restored ownership can admit the same session");
  }
}
""")
    env = environment(tmp_path, anchor)
    env.update(COPILOT_EXTENSIONS_CONTEXT=str(context), COPILOT_PLUGIN_ROOT=str(payload),
               AGENT_WORKTREES_PAYLOAD_ROOT=str(payload))
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(payload / "scripts"), sys.executable, str(anchor), namespace, str(payload / "plugin.json")],
        capture_output=True, text=True, timeout=15, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_linked_admission_cache_tracks_failed_owner_and_common_metadata(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    anchor, registry = fixture_project(tmp_path)
    worktree, admin = fixture_linked_worktree(tmp_path, anchor)
    unrelated = tmp_path / "unrelated" / ".git"
    for folder in ("objects", "refs"):
        (unrelated / folder).mkdir(parents=True)
    (unrelated / "HEAD").write_text("ref: refs/heads/main\n")
    with (registry / "projects.yaml").open("a") as stream:
        stream.write(f"  other:\n    anchor: {json.dumps(str(unrelated.parent))}\n")
    driver = tmp_path / "linked-cache.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import {mkdirSync, readFileSync, rmdirSync, unlinkSync, writeFileSync} from "node:fs";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, python, workingDirectory, anchor, admin] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot, python});
const input = {sessionId: "linked-cache", workingDirectory};
const head = join(anchor, ".git", "HEAD");
const text = readFileSync(head);
unlinkSync(head);
assert.equal(await check(input), false);
writeFileSync(head, text);
assert.equal(await check(input), true, "repairing a failed anchor probe invalidates a negative");
assert.equal(check.current(input), true);
for (const name of ["objects", "refs"]) {
  const path = join(anchor, ".git", name);
  rmdirSync(path);
  assert.equal(check.current(input), false);
  assert.equal(await check(input), false, "shared metadata removal revokes admission");
  mkdirSync(path);
  assert.equal(await check(input), true, "restoring shared metadata invalidates a negative");
}
const backlink = join(admin, "gitdir");
const linkText = readFileSync(backlink);
unlinkSync(backlink);
assert.equal(await check(input), false);
writeFileSync(backlink, linkText);
assert.equal(await check(input), true, "repairing a failed backlink probe invalidates a negative");
unlinkSync(head);
assert.equal(check.current(input), false);
assert.equal(await check(input), false, "anchor HEAD remains a live ownership input");
""")
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(SCRIPTS), sys.executable, str(worktree), str(anchor), str(admin)],
        capture_output=True, text=True, timeout=15,
        env=environment(tmp_path, worktree), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cache_is_scoped_to_session_and_selected_registry(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    payload = {"sessionId": "first", "cwd": str(anchor)}
    assert module.admitted(payload, home=tmp_path)
    calls = []
    original = module._entries
    monkeypatch.setattr(module, "_entries", lambda *args: calls.append(args) or original(*args))
    assert module.admitted({**payload, "sessionId": "second"}, home=tmp_path)
    assert len(calls) == 2
    other = tmp_path / "other-installation"
    other.mkdir()
    (other / "projects.yaml").write_text("projects: {}\n")
    monkeypatch.setattr(module, "resolve_registry_root", lambda **kwargs: other)
    assert not module.admitted(payload, home=tmp_path)
    assert not (other / "cache").exists()
    assert (registry / "cache").is_dir()


def test_persistent_membership_does_not_cross_execution_platforms(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    (registry / "repos.yaml").write_text(json.dumps({
        "repos": {"example": {"wsl": str(anchor), "linux": str(tmp_path / "other")}},
    }))
    payload = {"sessionId": "same-session", "cwd": str(anchor)}
    monkeypatch.setattr(module, "detect_platform", lambda: "wsl")
    assert module.admitted(payload, home=tmp_path)
    monkeypatch.setattr(module, "detect_platform", lambda: "linux")
    assert not module.admitted(payload, home=tmp_path)
    monkeypatch.setattr(module, "detect_platform", lambda: "wsl")
    assert module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("syntax", ["json", "yaml", "quoted-yaml"])
def test_machine_anchor_override_precedes_registered_anchor_and_invalidates_reuse(tmp_path, monkeypatch, syntax):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    anchor, _ = fixture_project(tmp_path)
    override = fixture_checkout(tmp_path / "override")
    config = tmp_path / ".example" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        json.dumps({"repos": {"example": {"anchor": str(override)}}}) if syntax == "json" else
        (
            f'"repos":\n  example:\n    "anchor": {json.dumps(str(override))}\n'
            if syntax == "quoted-yaml" else
            f"repos:\n  example:\n    anchor: {json.dumps(str(override))}\n"
        )
    )
    payload = {"sessionId": "override", "cwd": str(anchor)}
    assert not module.admitted(payload, home=tmp_path)
    payload["cwd"] = str(override)
    assert module.admitted(payload, home=tmp_path)
    config.write_text("repos: null\n")
    assert not module.admitted(payload, home=tmp_path)
    payload["cwd"] = str(anchor)
    assert module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("source", ["registry", "machine"])
@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("indent", [2, 4])
def test_commented_yaml_ownership_paths_preserve_quotes_hashes_and_indentation(
    tmp_path, monkeypatch, source, quote, indent,
):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    stale, registry = fixture_project(tmp_path)
    current = fixture_checkout(tmp_path / "current # retained")
    if source == "registry":
        path, field = registry / "repos.yaml", "windows" if os.name == "nt" else "linux"
    else:
        path, field = tmp_path / ".example" / "config.yaml", "anchor"
        path.parent.mkdir()
    value = json.dumps(str(current)) if quote == '"' else "'" + str(current).replace("'", "''") + "'"
    path.write_text(
        "repos: # ownership\n" + " " * indent + "example: # project\n"
        + " " * (indent * 2) + field + ": " + value + " # operator comment\n"
    )
    payload = {"sessionId": "commented-ownership", "cwd": str(stale)}
    assert not module.admitted(payload, home=tmp_path)
    assert module.admitted({**payload, "cwd": str(current)}, home=tmp_path)


def test_quoted_yaml_platform_override_precedes_execution_platform_without_yaml(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.setattr(module, "detect_platform", lambda: "linux")
    stale, registry = fixture_project(tmp_path)
    current = fixture_checkout(tmp_path / "current")
    (registry / "repos.yaml").write_text(json.dumps({
        "repos": {"example": {"linux": str(stale), "wsl": str(current)}},
    }))
    config = tmp_path / ".example" / "config.yaml"
    config.parent.mkdir()
    config.write_text('"platform": wsl\n')
    payload = {"sessionId": "quoted-platform", "cwd": str(stale)}
    assert not module.admitted(payload, home=tmp_path)
    assert module.admitted({**payload, "cwd": str(current)}, home=tmp_path)


@pytest.mark.parametrize("scalar", ["null", "~", "Null", "NULL", "", "json"])
def test_per_anchor_null_clears_to_registry_without_yaml(tmp_path, monkeypatch, scalar):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setitem(sys.modules, "yaml", None)
    anchor, _ = fixture_project(tmp_path)
    config = tmp_path / ".example" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        json.dumps({"repos": {"example": {"anchor": None}}}) if scalar == "json" else
        f"repos:\n  example:\n    anchor: {scalar}\n"
    )
    assert module.admitted({"sessionId": "cleared", "cwd": str(anchor)}, home=tmp_path)


def test_wsl_does_not_inherit_linux_only_registration(tmp_path, monkeypatch):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(module, "detect_platform", lambda: "wsl")
    anchor, registry = fixture_project(tmp_path)
    (registry / "repos.yaml").write_text(json.dumps({
        "repos": {"example": {"linux": str(anchor)}},
    }))
    payload = {"sessionId": "wsl-only", "cwd": str(anchor)}
    assert not module.admitted(payload, home=tmp_path)
    config = tmp_path / ".example" / "config.yaml"
    config.parent.mkdir()
    config.write_text(json.dumps({"repos": {"example": {"anchor": str(anchor)}}}))
    assert module.admitted(payload, home=tmp_path)


@pytest.mark.parametrize("without_yaml", [False, True])
@pytest.mark.parametrize("platform", ["windows", "linux", "wsl"])
def test_stale_project_anchor_never_supplies_registry_ownership(
    tmp_path, monkeypatch, without_yaml, platform,
):
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    if without_yaml:
        monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.setattr(module, "detect_platform", lambda: platform)
    stale, registry = fixture_project(tmp_path)
    current = fixture_checkout(tmp_path / "current")
    (registry / "projects.yaml").write_text(json.dumps({
        "projects": {"example": {"anchor": str(stale)}},
    }))
    repos = registry / "repos.yaml"
    repos.write_text(json.dumps({"repos": {"example": {}}}))
    payload = {"sessionId": "stale-anchor", "cwd": str(stale)}
    assert not module.admitted(payload, home=tmp_path)
    repos.write_text(json.dumps({"repos": {"example": {platform: str(current)}}}))
    assert not module.admitted(payload, home=tmp_path)
    assert module.admitted({**payload, "cwd": str(current)}, home=tmp_path)
    repos.write_text(json.dumps({"repos": {"example": {}}}))
    assert not module.admitted({**payload, "cwd": str(current)}, home=tmp_path)
    assert not module.admitted(payload, home=tmp_path)


def test_namespaced_anchor_override_uses_canonical_repository_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import test_registry_paths as fixtures
    from agent_worktrees import project_state

    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(*args, **(kwargs | no_window_kwargs())),
    ))
    module = load("managed_session")
    anchor, legacy = fixture_project(tmp_path)
    override = fixture_checkout(tmp_path / "override")
    payload_root = fixtures._payload(tmp_path, "selected")
    context, registry = fixtures._stamp(
        tmp_path / "durable", payload_root, marketplace="selected", repository="example/selected",
    )
    (registry / "projects.yaml").write_text((legacy / "projects.yaml").read_text())
    remote = "https://github.com:443/Example/Repository.git"
    (registry / "repos.yaml").write_text(json.dumps({
        "repos": {"example": {"remote": remote,
                              "windows" if os.name == "nt" else "linux": str(anchor)}},
    }))
    fixtures._select(monkeypatch, context, payload_root)
    identity = project_state._identity("example", remote=remote)
    generated = module.project_identity("example", remote, module.resolve_registry_context())
    assert generated.state_root == identity.state_root
    identity.state_root.mkdir(parents=True)
    receipt = identity.repository_root / "identity.json"
    value = project_state._receipt(identity)
    receipt.write_text(json.dumps(value))
    (identity.state_root / "config.yaml").write_text(json.dumps({
        "repos": {"example": {"anchor": str(override)}},
    }))
    payload = {"sessionId": "cell-override", "cwd": str(anchor)}
    assert not module.admitted(payload)
    payload["cwd"] = str(override)
    assert module.admitted(payload)
    receipt.write_text(json.dumps({**value, "normalizedRemote": "network:example.invalid/other"}))
    assert not module.admitted(payload)
    receipt.write_text(json.dumps(value))
    assert module.admitted(payload)


def test_sdk_rechecks_new_ownership_fragments_and_explicit_precedence(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    anchor, _ = fixture_project(tmp_path)
    other = fixture_checkout(tmp_path / "other")
    config = tmp_path / ".example" / "config.yaml"
    fragments = config.parent / "config.d"
    fragments.mkdir(parents=True)
    driver = tmp_path / "config-cache.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import {unlinkSync, writeFileSync} from "node:fs";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, python, workingDirectory, other, config, fragments] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot, python});
const input = {sessionId: "config-cache", workingDirectory};
assert.equal(await check(input), true);
const fragment = join(fragments, "operator.yaml");
writeFileSync(fragment, JSON.stringify({repos: {example: {anchor: other}}}));
assert.equal(check.current(input), false);
assert.equal(await check(input), false, "conditional ownership is not guessed before activation");
assert.equal(await check({...input, workingDirectory: other}), false);
writeFileSync(config, JSON.stringify({repos: {example: {anchor: other}}}));
assert.equal(await check({...input, workingDirectory: other}), true,
  "the explicit machine configuration wins");
unlinkSync(config);
unlinkSync(fragment);
assert.equal(await check(input), true);
""")
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(SCRIPTS), sys.executable, str(anchor), str(other), str(config), str(fragments)],
        capture_output=True, text=True, timeout=15,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_installation_cells_do_not_share_admission_or_fall_back(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(*args, **(kwargs | no_window_kwargs())),
    ))
    anchor, legacy = fixture_project(tmp_path)
    cells = []
    for label in ("first", "second"):
        payload = fixtures._payload(tmp_path, label)
        for name in ("managed_session.py", "execution_platform.py", "managed_project_identity.py",
                     "remote_identity.py", "guarded_anchor_map.py"):
            shutil.copyfile(SCRIPTS / name, payload / "scripts" / name)
        context, registry = fixtures._stamp(
            tmp_path / "durable", payload,
            marketplace=label, repository=f"example/{label}",
        )
        (registry / "projects.yaml").write_text(
            (legacy / "projects.yaml").read_text() if label == "first" else "projects: {}\n"
        )
        (registry / "repos.yaml").write_text((legacy / "repos.yaml").read_text())
        cells.append((payload, context, registry))
    for index in (0, 1, 0):
        payload, context, registry = cells[index]
        env = environment(tmp_path, anchor)
        env.update(
            COPILOT_PLUGIN_ROOT=str(payload), AGENT_WORKTREES_PAYLOAD_ROOT=str(payload),
            COPILOT_EXTENSIONS_CONTEXT=str(context),
        )
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(payload / "scripts" / "managed_session.py")],
            input=json.dumps({"sessionId": "same-session", "cwd": str(anchor)}),
            text=True, capture_output=True, timeout=10, env=env, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["admitted"] is (index == 0), result.stdout
        assert (registry / "cache" / "managed-sessions").exists() is (index == 0)
        if index == 0:
            association = json.loads(next((registry / "cache" / "managed-sessions").glob("*.json")).read_text())
            view = association["installation"]
            assert view["version"] == 1
            assert view["pluginRoot"] == str(registry)
            assert set(view["files"]) == {
                view["namespaceReceipt"], view["installReceipt"], str(payload / "plugin.json"),
            }
    assert not (legacy / "cache").exists()


def environment(home, anchor):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("COPILOT_", "AGENT_WORKTREES_", "WORKTREE_")):
            env.pop(key)
    env.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        COPILOT_PROJECT_DIR=str(anchor), COPILOT_PLUGIN_ROOT=str(SCRIPTS.parent),
        PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
    )
    return env


@pytest.mark.skipif(os.name != "nt", reason="Native Windows prepared membership")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("linked", [False, True])
def test_prepared_native_membership_reuses_original_association(tmp_path, monkeypatch, capsys, shell, linked):
    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"Required native membership shell {shell} is unavailable")
        pytest.skip(f"{shell} is unavailable")
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, _ = fixture_project(tmp_path)
    workspace = fixture_linked_worktree(tmp_path, anchor)[0] if linked else anchor
    workspace = workspace / "src"
    workspace.mkdir()
    session = tmp_path / ".copilot" / "session-state" / "prepared"
    session.mkdir(parents=True)
    payload = {"sessionId": "prepared", "cwd": str(workspace), "toolName": "view"}
    assert module.admitted(payload, home=tmp_path)
    admission = tmp_path / "managed-session.ps1"
    admission.write_text(
        instrument_prepared_admission((SCRIPTS / "managed-session.ps1").read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    command = (
        NATIVE_WRITE_PHASE_WRITER
        + ". $env:PREPARED_MEMBERSHIP_SCRIPT; Write-NativeWriteFixturePhase 'helper-loaded'; "
        "function Get-ManagedWorktreesPython { throw 'interpreter lookup is forbidden' }; "
        "$raw = [Console]::In.ReadToEnd(); Write-NativeWriteFixturePhase 'input-complete'; "
        "$admitted = Test-ManagedWorktreesSession -Raw $raw; Write-NativeWriteFixturePhase 'admission-complete'; "
        "[Console]::Out.Write($admitted.ToString()); Write-NativeWriteFixturePhase 'output-complete'"
    )
    env = environment(tmp_path, workspace)
    env["PREPARED_MEMBERSHIP_SCRIPT"] = str(admission)
    started_ms = time.time_ns() // 1_000_000
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=10,
            cwd=workspace, env=env, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as error:
        progress, diagnostic = native_write_fixture_stderr(error.stderr or b"")
        raise AssertionError(
            f"{shell} membership exceeded the original 10s child limit; start_ms={started_ms}; "
            f"phases={progress or 'no command entry'!r}; "
            f"stdout={repr(error.stdout)[:512]}; stderr={repr(diagnostic)[:512]}"
        ) from error
    progress, result.stderr = native_write_fixture_stderr(result.stderr)
    assert result.returncode == 0, result.stderr
    assert not result.stderr, result.stderr
    assert result.stdout == b"True", result.stdout + result.stderr
    phase_names = [line.split(" ", 1)[1] for line in progress.splitlines()]
    assert "prepared-marker-opened" in phase_names and "prepared-marker-json" in phase_names, progress
    assert phase_names.index("prepared-marker-opened") < phase_names.index("prepared-marker-json"), progress
    with capsys.disabled():
        print(json.dumps({
            "shell": shell, "linked": linked, "start_ms": started_ms,
            "phases": progress.splitlines(),
        }))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows registered pre-tool consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", ["read", "write", "revoked", "cold-read", "cold-write", "read-batch"])
def test_registered_pre_tool_entry_reuses_prepared_membership(tmp_path, monkeypatch, shell, case):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    workspace = fixture_linked_worktree(tmp_path, anchor)[0]
    session = tmp_path / ".copilot" / "session-state" / "pre-tool"
    session.mkdir(parents=True)
    payload = {
        "sessionId": "pre-tool", "cwd": str(workspace),
        "toolName": "view" if case in {"read", "cold-read", "read-batch"} else "edit",
        "toolArgs": {"path": "non-ascii-\u00e9.txt", "text": "quote'\"\\\n\t"},
    }
    if case == "read-batch":
        payload["toolCalls"] = [{"name": "edit", "args": payload["toolArgs"]}]
    if not case.startswith("cold-"):
        assert module.admitted(payload, home=tmp_path)
    plugin = tmp_path / "payload"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    if case.startswith("cold-"):
        shutil.copytree(SCRIPTS, scripts, dirs_exist_ok=True)
    for name in (
        "pre-tool-hook.ps1", "managed-session.ps1", "hook-read-policy.json",
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py",
        "pr_supersede_guard.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    observed = tmp_path / "policy-input"
    (scripts / "resolve-runtime.ps1").write_text(
        "$AwPy='" + sys.executable.replace("'", "''") + "'\n",
        encoding="utf-8",
    )
    shutil.copyfile(SCRIPTS / "hook_policy_process.py", scripts / "hook_policy_process_original.py")
    (scripts / "hook_policy_process.py").write_text(
        "import base64,json,sys\nfrom pathlib import Path\n"
        + "# Re-export the real admission helper's caller-lifetime watcher without executing policy on import.\n"
        + "from hook_policy_process_original import _watch_caller\n"
        + "if __name__ == '__main__':\n"
        + f"    with Path({str(observed)!r}).open('xb') as output: output.write(base64.b64decode(sys.stdin.readline()))\n"
        + "    print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'fixture policy'}))\n",
        encoding="utf-8",
    )
    if case == "revoked":
        (registry / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    env = environment(tmp_path, workspace)
    env["COPILOT_PLUGIN_ROOT"] = str(plugin)
    env.pop("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", None)
    entry = json.loads((SCRIPTS.parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    if case.startswith("cold-"):
        assert not (registry / "cache").exists()
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", entry["powershell"]],
        input=raw.encode("utf-8"), capture_output=True, timeout=10,
        cwd=workspace, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    if case.startswith("cold-"):
        assert (session / "agent-worktrees" / "managed-session.json").is_file()
    if case in {"write", "cold-write", "read-batch"}:
        assert observed.read_bytes() == raw.encode("utf-8")
        assert json.loads(result.stdout) == {
            "permissionDecision": "deny", "permissionDecisionReason": "fixture policy",
        }
    else:
        assert not observed.exists()
        assert result.stdout == b""


@pytest.mark.skipif(os.name != "nt", reason="Native Windows registered prepared-cell pre-tool consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", ["view", "rg", "glob", "guard-drift", "incomplete", "receipt", "write", "read-batch"])
def test_registered_prepared_cell_membership_read_requires_complete_native_proof(tmp_path, monkeypatch, shell, case):
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"{shell} is required")
        pytest.skip(f"{shell} is unavailable")
    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(
            *args, **(kwargs | no_window_kwargs() | {"timeout": 10}),
        ),
    ))
    anchor, legacy = fixture_project(tmp_path)
    workspace = fixture_linked_worktree(tmp_path, anchor)[0]
    plugin = fixtures._payload(tmp_path, "selected")
    scripts = plugin / "scripts"
    for name in (
        "pre-tool-hook.ps1", "managed-session.ps1", "managed_session.py",
        "execution_platform.py", "managed_project_identity.py", "remote_identity.py",
        "guarded_anchor_map.py", "hook_policy_process.py", "hook-read-policy.json",
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    context, registry = fixtures._stamp(
        tmp_path / "durable", plugin, marketplace="selected", repository="example/selected",
    )
    for name in ("projects.yaml", "repos.yaml"):
        (registry / name).write_text((legacy / name).read_text())
    fixtures._select(monkeypatch, context, plugin)
    session = tmp_path / ".copilot" / "session-state" / "pre-cell"
    session.mkdir(parents=True)
    event = {
        "sessionId": "pre-cell", "cwd": str(workspace),
        "toolName": case if case in {"view", "rg", "glob"} else "edit" if case == "write" else "view",
        "toolArgs": {"path": "non-ascii-\u00e9.txt", "text": "quote'\"\\\n\t"},
    }
    if case == "read-batch":
        event["toolCalls"] = [{"name": "edit", "args": event["toolArgs"]}]
    assert load("managed_session").admitted(event, home=tmp_path)
    marker = session / "agent-worktrees" / "managed-session.json"
    if case == "incomplete":
        value = json.loads(marker.read_text())
        value.pop("installation")
        marker.write_text(json.dumps(value))
    elif case == "receipt":
        context.write_text(context.read_text() + "\n")
    elif case == "guard-drift":
        with (scripts / "anchor_write_guard.py").open("a", encoding="utf-8") as output:
            output.write("\n# changed guard implementation\n")
    membership = tmp_path / "membership-interpreter"
    validation = tmp_path / "context-validator"
    policy = tmp_path / "policy-input"
    with (scripts / "managed-session.ps1").open("a", encoding="utf-8") as output:
        output.write(
            "\nfunction Get-ManagedWorktreesPython { "
            + "[IO.File]::WriteAllText('" + str(membership).replace("'", "''") + "', 'called'); "
            + "return '" + sys.executable.replace("'", "''") + "' }\n"
        )
    (scripts / "installation-context" / "installation-context.ps1").write_text(
        "[IO.File]::WriteAllText('" + str(validation).replace("'", "''") + "', 'called')\n"
        + "Write-Output '" + json.dumps({"pluginRoot": str(registry)}).replace("'", "''") + "'\n"
        + "$global:LASTEXITCODE = 0\n",
        encoding="utf-8",
    )
    (scripts / "resolve-runtime.ps1").write_text(
        "$AwPy='" + sys.executable.replace("'", "''") + "'\n", encoding="utf-8",
    )
    shutil.copyfile(scripts / "hook_policy_process.py", scripts / "hook_policy_process_original.py")
    (scripts / "hook_policy_process.py").write_text(
        "import base64,json,sys\nfrom pathlib import Path\n"
        + "from hook_policy_process_original import _watch_caller\n"
        + "if __name__ == '__main__':\n"
        + f"    Path({str(policy)!r}).write_bytes(base64.b64decode(sys.stdin.readline()))\n"
        + "    print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'fixture policy'}))\n",
        encoding="utf-8",
    )
    flag = tmp_path / "default-stop.json"
    flag.write_text('{"active":true}', encoding="utf-8")
    env = environment(tmp_path, workspace)
    env.update(
        COPILOT_EXTENSIONS_CONTEXT=str(context), COPILOT_PLUGIN_ROOT=str(plugin),
        AGENT_WORKTREES_PAYLOAD_ROOT=str(plugin), PRETOOL_KILLSWITCH_STATE_FILE=str(flag),
    )
    env.pop("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", None)
    entry = json.loads((SCRIPTS.parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]
    raw = json.dumps(event, ensure_ascii=False, indent=2).encode("utf-8")
    result = native_run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", entry["powershell"]],
        input=raw, capture_output=True, timeout=10,
        cwd=workspace, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert membership.exists() is (case in {"incomplete", "receipt"})
    policy_required = case not in {"view", "rg", "glob"}
    assert validation.exists() is policy_required, (result.stdout, result.stderr)
    assert policy.exists() is policy_required
    if policy_required:
        assert policy.read_bytes() == raw
        assert json.loads(result.stdout)["permissionDecision"] == "deny"
    else:
        assert result.stdout == b""
    assert not (legacy / "cache").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows registered file-local write consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "ordinary", "create", "path-alias", "json-args", "personal-state", "anchor-cwd",
    "anchor-target", "foreign", "nested", "batch", "structured", "drive-relative",
    "missing-map", "partial-map", "wrong-scope", "array-schema", "oversized-config",
    "guard-drift", "reader-drift", "forms-drift", "missing-dependencies", "git-pointer-aba",
    "delegated", "invalid-elevation", "noncanonical-map", "dot-segment-map", "real-policy", "old-admission",
    "cell-ordinary", "cell-create", "cell-personal-state", "cell-guard-drift",
    "personal-state-cr", "personal-state-crlf", "personal-state-dot-prefix",
    "personal-state-dot-file", "personal-state-dot-assignments",
    "personal-state-control-space", "ordinary-nested-dot-file",
    "ordinary-unrelated-inventory",
    "batch-mixed", "batch-json-args", "batch-protected", "batch-foreign",
    "batch-multiple-targets", "batch-structured", "batch-missing-args",
    "batch-null-args", "batch-extra-key", "batch-unknown", "batch-empty",
    "batch-not-list", "batch-limit", "batch-oversized", "batch-read-only",
    "batch-working-directory", "batch-real-policy", "batch-malformed",
    "batch-shadowed-keys",
    "batch-read-only-mixed", "cell-batch-read-only",
    "batch-read-only-limit", "batch-read-only-overflow", "batch-read-only-protected",
    "batch-read-only-unknown", "batch-read-only-shell", "batch-read-only-malformed",
    "batch-read-only-extra-key", "batch-read-only-missing-dependencies",
    "batch-read-only-forms-drift", "batch-read-only-old-admission",
    "batch-read-only-working-directory", "batch-read-only-real-policy",
    "cell-batch-read-only-real-policy",
    "batch-read-only-attribution", "cell-batch-read-only-attribution",
    "shell-entry-attribution", "cell-shell-entry-attribution",
    "literal-shell-entry-attribution", "cell-literal-shell-entry-attribution",
    "literal-shell-head", "cell-literal-shell-head",
    "literal-shell-args", "literal-shell-json", "literal-shell-ordinal",
    "literal-shell-old-proof", "cell-literal-shell-old-proof",
    "literal-shell-invalid-proof", "literal-shell-relevance-drift",
    "literal-shell-unprepared", "literal-shell-batch", "literal-shell-prior-reader",
    "literal-shell-key-command", "literal-shell-key-name",
    "literal-shell-key-args", "literal-shell-key-collision",
    "literal-shell-carrier-raw-attribution", "cell-literal-shell-carrier-raw-attribution",
    "literal-shell-carrier-input-attribution", "cell-literal-shell-carrier-input-attribution",
    "literal-shell-carrier-input-raw-attribution", "cell-literal-shell-carrier-input-raw-attribution",
    "literal-shell-carrier-primary-null", "literal-shell-carrier-primary-invalid",
    "literal-shell-carrier-primary-empty", "literal-shell-carrier-primary-safe",
    "literal-shell-carrier-primary-protected", "literal-shell-carrier-input-json",
    "literal-shell-carrier-input-batch",
    "cell-batch-mixed", "cell-batch-protected",
    "structured-files", "structured-changes", "structured-only", "structured-json",
    "structured-aliases", "structured-duplicates", "structured-limit",
    "structured-protected", "structured-foreign", "structured-nested",
    "structured-oversized", "structured-target-overflow", "structured-incomplete",
    "structured-invalid-list", "structured-nested-entries", "structured-patch",
    "batch-structured-safe", "cell-structured", "cell-batch-structured-safe",
    "structured-real-policy", "cell-structured-real-policy",
    "batch-target-limit", "batch-target-overflow",
    *[f"write-name-{name}" for name in WRITE_ALIASES],
    *[f"protected-write-name-{name}" for name in WRITE_ALIASES],
    "write-name-json", "write-name-structured", "write-name-batch",
    "protected-write-name-batch", "write-name-real-policy",
    "cell-write-name-real-policy", "cell-write-name-structured",
    "cell-protected-write-name-multi_edit",
    *PATCH_NATIVE_CASES, *PATCH_PROTECTED_CASES,
    "patch-invalid-field", "patch-targetless", "patch-fragment", "patch-malformed-json",
    "patch-unknown-header", "patch-overflow", "patch-foreign", "patch-nested", "patch-anchor",
    "patch-field-oversized", "patch-union-overflow", "patch-json-nonobject",
    "cell-patch-union", "cell-patch-protected-move", "cell-patch-real-policy",
])
def test_registered_file_local_write_reuses_original_guard_facts(tmp_path, monkeypatch, capsys, shell, case):
    from agent_worktrees import guarded_anchors

    cell = case.startswith("cell-")
    if cell:
        case = case.removeprefix("cell-")
    real_policy = case in {
        "real-policy", "batch-real-policy", "structured-real-policy", "write-name-real-policy",
        "patch-real-policy", "batch-read-only-real-policy",
        "batch-read-only-attribution",
        "shell-entry-attribution", "literal-shell-entry-attribution",
    } or case.startswith("literal-shell-carrier-") and case.endswith("-attribution")
    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"{shell} is required")
        pytest.skip(f"{shell} is unavailable")
    anchor, registry = fixture_project(tmp_path)
    workspace, admin = fixture_linked_worktree(tmp_path, anchor)
    if case == "anchor-cwd":
        workspace = anchor
    config = workspace / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text("stateless: false\n" if case == "anchor-cwd" else "stateless: true\n", encoding="utf-8")
    if case in {"personal-state-cr", "personal-state-crlf"}:
        newline = "\r" if case == "personal-state-cr" else "\r\n"
        config.write_bytes(f"default_branch: main{newline}stateless: true{newline}".encode("utf-8"))
    session = tmp_path / ".copilot" / "session-state" / "pre-write"
    session.mkdir(parents=True)
    event = {
        "sessionId": "pre-write", "cwd": str(workspace), "toolName": "edit",
        "toolArgs": {
            "path": {
                "personal-state-dot-prefix": ".efforts/private.md",
                "personal-state-dot-file": ".ownership.yml",
                "personal-state-dot-assignments": "..dev-assignments.yml",
                "ordinary-nested-dot-file": "source/.ownership.yml",
            }.get(case, "efforts/private.md" if case.startswith("personal-state") else "source-\u00e9.py"),
            "text": "quote'\"\\\n\t",
        },
    }
    if case == "create":
        event["toolName"] = "create"
    elif case == "path-alias":
        event["toolArgs"]["file_path"] = event["toolArgs"].pop("path")
    elif case == "json-args":
        event["toolArgs"] = json.dumps(event["toolArgs"])
    elif case == "anchor-target":
        event["toolArgs"]["path"] = str(anchor / "source.py")
    elif case == "foreign":
        event["toolArgs"]["path"] = str(fixture_checkout(tmp_path / "foreign") / "source.py")
    elif case == "nested":
        event["toolArgs"]["path"] = str(fixture_checkout(workspace / "nested") / "source.py")
    elif case == "batch":
        event["toolCalls"] = [{"name": "edit", "args": event["toolArgs"]}]
    elif case == "structured":
        event["toolArgs"]["edits"] = [{"path": "other.py"}]
    elif case.startswith("structured-"):
        args = event["toolArgs"]
        args["edits"] = [{"path": "other.py"}]
        if case in {"structured-files", "structured-changes"}:
            args[case.removeprefix("structured-")] = args.pop("edits")
        elif case == "structured-only":
            args.pop("path")
        elif case == "structured-json":
            event["toolArgs"] = json.dumps(args)
        elif case == "structured-aliases":
            args["edits"] = [{"file_path": "one.py", "targetFile": "two.py"}]
            args["files"] = [{"fileName": "three.py"}]
            args["changes"] = [{"filename": "four.py", "target_file": "five.py"}]
        elif case == "structured-duplicates":
            args["edits"] = [{"path": "other.py"}] * 128
        elif case == "structured-limit":
            args["edits"] = [{"path": f"other-{index}.py"} for index in range(127)]
        elif case == "structured-protected":
            args["edits"].append({"filePath": "efforts/private.md"})
        elif case == "structured-foreign":
            args["edits"].append({"path": str(fixture_checkout(tmp_path / "foreign") / "source.py")})
        elif case == "structured-nested":
            args["edits"].append({"path": str(fixture_checkout(workspace / "nested") / "source.py")})
        elif case in {"structured-oversized", "structured-target-overflow"}:
            args["edits"] = [
                {"path": f"other-{index}.py"}
                for index in range(129 if case == "structured-oversized" else 128)
            ]
        elif case == "structured-incomplete":
            args["edits"].append({})
        elif case == "structured-invalid-list":
            args["edits"] = {"path": "efforts/private.md"}
        elif case == "structured-nested-entries":
            args["edits"].append({"path": "last.py", "files": [{"path": "efforts/private.md"}]})
        elif case == "structured-patch":
            event["toolName"] = "apply_patch"
            args["input"] = "*** Update File: other.py\n*** Move to: final.py\n"
    elif case == "drive-relative":
        event["toolArgs"]["path"] = workspace.drive + "source.py"
    if "write-name-" in case:
        alias = case.removeprefix("protected-").removeprefix("write-name-")
        event["toolName"] = alias if alias in WRITE_ALIASES else "multi_edit"
        if case.startswith("protected-"):
            event["toolArgs"]["path"] = "efforts/private.md"
        if alias == "json":
            event["toolArgs"] = json.dumps(event["toolArgs"])
        elif alias in {"structured", "real-policy"}:
            event["toolArgs"] = {"files": [{"filePath": "one.py"}, {"targetFile": "two.py"}]}
        elif alias == "batch":
            event["toolName"] = "view"
            event["toolCalls"] = [
                {"name": "write", "args": {"path": "one.py"}},
                {"name": "rg", "args": {"pattern": "read"}},
                {"name": "multi_edit", "args": {"changes": [{"file_path": "two.py"}]}},
                {"name": "str_replace", "args": json.dumps(event["toolArgs"])},
            ]
    if case.startswith("patch-"):
        event["toolName"] = "apply_patch"

        def patch(*headers):
            return "*** Begin Patch\n" + "".join(f"*** {header}\n" for header in headers) + "*** End Patch\n"

        args = {"input": patch("Update File: source-\u00e9.py")}
        event["toolArgs"] = args
        if case in {"patch-patch", "patch-command"}:
            args[case.removeprefix("patch-")] = args.pop("input")
        elif case == "patch-json":
            event["toolArgs"] = json.dumps(args)
        elif case == "patch-json-nonobject":
            event["toolArgs"] = json.dumps(["not an argument object"])
        elif case == "patch-freeform":
            event["toolArgs"] = args["input"]
        elif case == "patch-crlf":
            args["input"] = args["input"].replace("\n", "\r\n")
        elif case == "patch-headers":
            args["input"] = patch("Add File: one.py", "Update File: two.py", "Move to: three.py", "Delete File: four.py")
        elif case in {"patch-union", "patch-real-policy"}:
            args.update(
                patch=patch("Add File: two.py"), command=patch("Delete File: three.py"),
                path="four.py", edits=[{"file_path": "five.py"}],
                files=[{"targetFile": "six.py"}], changes=[{"filename": "seven.py"}],
            )
        elif case in {"patch-batch", "patch-protected-batch"}:
            event.update(toolName="view", toolArgs={"path": "ignored.py"}, toolCalls=[
                {"name": "write", "args": {"path": "one.py"}},
                {"name": "view", "args": {"path": "read.txt"}},
                {"name": "apply_patch", "args": args},
                {"name": "apply_patch", "args": patch(
                    "Add File: efforts/private.md" if case == "patch-protected-batch" else "Add File: last.py",
                )},
            ])
        elif case.startswith("patch-protected-"):
            field = case.removeprefix("patch-protected-")
            if field in {"input", "patch", "command"}:
                args[field] = patch("Update File: safe.py", "Delete File: efforts/private.md")
            elif field == "move":
                args["input"] = patch("Update File: safe.py", "Move to: efforts/private.md")
            elif field == "direct":
                args["targetFile"] = "efforts/private.md"
            elif field == "freeform":
                event["toolArgs"] = patch("Update File: safe.py", "Move to: efforts/private.md")
            elif field == "commented-json":
                event["toolArgs"] = (
                    '{/*\n*** Delete File: efforts/private.md\n*/"input":'
                    + json.dumps(args["input"]) + "}"
                )
            else:
                args["changes"] = [{"filePath": "safe.py"}, {"path": "efforts/private.md"}]
        elif case == "patch-duplicates":
            args["input"] = patch(*(["Add File: same.py"] * 129))
        elif case in {"patch-limit", "patch-overflow"}:
            args["input"] = patch(*(
                f"Add File: source-{index}.py" for index in range(129 if case == "patch-overflow" else 128)
            ))
        elif case == "patch-union-overflow":
            args["input"] = patch(*(f"Add File: source-{index}.py" for index in range(127)))
            args.update(path="direct.py", changes=[{"path": "structured.py"}])
        elif case in {"patch-field-bound", "patch-field-oversized"}:
            size = 131073 if case == "patch-field-oversized" else 131072
            prefix = "*** Begin Patch\n*** Update File: safe.py\n+"
            suffix = "\n*** End Patch\n"
            args["input"] = prefix + "x" * (size - len(prefix) - len(suffix)) + suffix
        elif case == "patch-invalid-field":
            args["patch"] = ["*** Delete File: efforts/private.md"]
        elif case == "patch-targetless":
            args["input"] = "*** Begin Patch\n*** End Patch\n"
        elif case == "patch-fragment":
            args["input"] = "*** Update File: source.py\n"
        elif case == "patch-malformed-json":
            event["toolArgs"] = '{"input":'
        elif case == "patch-unknown-header":
            args["input"] = "*** Begin Patch\n*** Rename File: source.py\n*** End Patch\n"
        elif case == "patch-foreign":
            args["input"] = patch(f"Update File: {fixture_checkout(tmp_path / 'foreign') / 'source.py'}")
        elif case == "patch-nested":
            args["input"] = patch(f"Update File: {fixture_checkout(workspace / 'nested') / 'source.py'}")
        elif case == "patch-anchor":
            args["input"] = patch(f"Update File: {anchor / 'source.py'}")
    if case.startswith("batch-"):
        first = {"name": "create", "args": event["toolArgs"]}
        last = {"name": "edit", "args": {"path": "second.py"}}
        event["toolCalls"] = [first, {"name": "view", "args": {"path": "read.txt"}}, last]
        # A top-level read must not hide a later write or supply its arguments.
        event["toolName"] = "view"
        event["toolArgs"] = {"path": "ignored.py"}
        event["tool_input"] = {"path": "efforts/ignored.md"}
        if case == "batch-json-args":
            last["args"] = json.dumps(last["args"])
        elif case == "batch-protected":
            last["args"]["path"] = "efforts/private.md"
        elif case == "batch-foreign":
            last["args"]["path"] = str(fixture_checkout(tmp_path / "foreign") / "source.py")
        elif case == "batch-multiple-targets":
            last["args"]["file_path"] = "efforts/private.md"
        elif case == "batch-structured":
            last["args"]["edits"] = [{"path": "efforts/private.md"}]
        elif case == "batch-structured-safe":
            last["args"] = {"files": [{"path": "second.py"}, {"target_file": "third.py"}]}
        elif case in {"batch-target-limit", "batch-target-overflow"}:
            last["args"] = {"files": [
                {"path": f"other-{index}.py"}
                for index in range(127 if case == "batch-target-limit" else 128)
            ]}
        elif case == "batch-missing-args":
            last.pop("args")
        elif case == "batch-null-args":
            last["args"] = None
        elif case == "batch-extra-key":
            last["cwd"] = str(workspace)
        elif case == "batch-shadowed-keys":
            last.update(Keys=[], cwd=str(workspace))
        elif case == "batch-unknown":
            last["name"] = "unknown"
        elif case == "batch-malformed":
            event["toolCalls"][-1] = None
        elif case == "batch-empty":
            event["toolCalls"] = []
        elif case == "batch-not-list":
            event["toolCalls"] = {"name": "edit", "args": {"path": "source.py"}}
        elif case in {"batch-limit", "batch-oversized"}:
            event["toolCalls"] = [first] * (128 if case == "batch-limit" else 129)
        elif case == "batch-read-only":
            event["toolCalls"] = [{"name": "view", "args": {}}]
        elif case == "batch-working-directory":
            event["workingDirectory"] = event.pop("cwd")
        if case.startswith("batch-read-only-"):
            event["toolName"] = "powershell"
            event["toolArgs"] = {"command": "Set-Content efforts/private.md fixture"}
            event["toolCalls"] = [
                {"name": "view", "args": {"path": "efforts/private.md", "command": "write-looking data"}},
                {"name": "rg", "args": json.dumps({"pattern": "quoted'\"\\text"})},
                {"name": "glob", "args": None},
            ]
            if case in {"batch-read-only-limit", "batch-read-only-overflow"}:
                event["toolCalls"] = [event["toolCalls"][0]] * (
                    129 if case == "batch-read-only-overflow" else 128
                )
            elif case == "batch-read-only-protected":
                event["toolCalls"].append({"name": "edit", "args": {"path": "efforts/private.md"}})
            elif case == "batch-read-only-unknown":
                event["toolCalls"][-1]["name"] = "unknown"
            elif case == "batch-read-only-shell":
                event["toolCalls"][-1] = {
                    "name": "powershell", "args": {"command": "Get-Content source.py"},
                }
            elif case == "batch-read-only-malformed":
                event["toolCalls"][-1] = None
            elif case == "batch-read-only-extra-key":
                event["toolCalls"][-1].update(Keys=[], cwd=str(workspace))
            elif case == "batch-read-only-working-directory":
                event["workingDirectory"] = event.pop("cwd")
    if case in {"shell-entry-attribution", "literal-shell-entry-attribution"} or (
        case.startswith("literal-shell-carrier-") and case.endswith("-attribution")
    ):
        event.update(toolName="powershell", toolArgs={"command": "Get-Location"})
        if case != "shell-entry-attribution":
            event["toolArgs"]["description"] = "Read the current fixture location"
        if case == "literal-shell-carrier-raw-attribution":
            event["toolArgs"] = "Get-Location"
        elif case == "literal-shell-carrier-input-attribution":
            event["tool_input"] = event.pop("toolArgs")
        elif case == "literal-shell-carrier-input-raw-attribution":
            event.pop("toolArgs")
            event["tool_input"] = "Get-Location"
    elif case.startswith("literal-shell-"):
        event.update(toolName="powershell", toolArgs={"command": "Get-Location", "description": "Fixture read"})
        protected_command = f'Set-Content "{workspace / "efforts" / "private.md"}" -Value fixture'
        if case == "literal-shell-head":
            event.update(toolName="pwsh", toolArgs={
                "command": "pwd", "description": "Fixture location",
                "cmd": protected_command,
            })
        elif case == "literal-shell-args":
            event["toolArgs"]["command"] += " ."
        elif case == "literal-shell-json":
            event["toolArgs"] = json.dumps(event["toolArgs"])
        elif case == "literal-shell-ordinal":
            event["toolArgs"]["command"] += "\u00ad"
        elif case == "literal-shell-batch":
            event["toolCalls"] = [
                {"name": "view", "args": {"path": "read.txt"}},
                {"name": "powershell", "args": {"command": protected_command}},
            ]
        elif case == "literal-shell-key-command":
            event["toolArgs"] = {"Command": "Get-Location", "cmd": protected_command}
        elif case == "literal-shell-key-name":
            event.pop("toolName")
            event.update(ToolName="powershell", tool_name="edit")
            event["toolArgs"]["path"] = "efforts/private.md"
        elif case == "literal-shell-key-args":
            event["ToolArgs"] = event.pop("toolArgs")
            event["tool_input"] = {"command": protected_command}
        elif case == "literal-shell-key-collision":
            event["toolArgs"] = {"command": protected_command, "Command": "Get-Location"}
        elif case.startswith("literal-shell-carrier-primary-"):
            event["tool_input"] = {"command": protected_command}
            if case == "literal-shell-carrier-primary-null":
                event["toolArgs"] = None
            elif case == "literal-shell-carrier-primary-invalid":
                event["toolArgs"] = False
            elif case == "literal-shell-carrier-primary-empty":
                event["toolArgs"] = ""
            elif case == "literal-shell-carrier-primary-protected":
                event["toolArgs"] = protected_command
                event["tool_input"] = {"command": "Get-Location"}
        elif case == "literal-shell-carrier-input-json":
            event["tool_input"] = json.dumps(event.pop("toolArgs"))
        elif case == "literal-shell-carrier-input-batch":
            event.pop("toolArgs")
            event["tool_input"] = "Get-Location"
            event["toolCalls"] = [
                {"name": "view", "args": {"path": "read.txt"}},
                {"name": "powershell", "args": {"command": protected_command}},
            ]
    env = environment(tmp_path, workspace)
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        monkeypatch.delenv(key, raising=False)
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    payload_label = "payload-\u00e9 space'root" if case == "batch-read-only-attribution" else "payload"
    plugin = tmp_path / payload_label
    if cell:
        from types import SimpleNamespace
        import test_registry_paths as fixtures

        native_run = subprocess.run
        monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
            run=lambda *args, **kwargs: native_run(
                *args, **(kwargs | no_window_kwargs() | {"timeout": 10}),
            ),
        ))
        plugin = fixtures._payload(
            tmp_path, payload_label if case == "batch-read-only-attribution" else "selected",
        )
        with (registry / "repos.yaml").open("a", encoding="utf-8") as output:
            output.write('    remote: "https://github.com/example/project.git"\n')
        context, selected_registry = fixtures._stamp(
            tmp_path / "durable", plugin, marketplace="selected", repository="example/selected",
        )
        for name in ("projects.yaml", "repos.yaml"):
            (selected_registry / name).write_bytes((registry / name).read_bytes())
        registry = selected_registry
        fixtures._select(monkeypatch, context, plugin)
        from agent_worktrees import project_state

        project_state.ensure_project_state("example")
        env.update(
            COPILOT_EXTENSIONS_CONTEXT=str(context), AGENT_WORKTREES_PAYLOAD_ROOT=str(plugin),
            COPILOT_PLUGIN_ROOT=str(plugin),
        )
    if case == "delegated":
        from agent_worktrees import related

        related.write_related(workspace, related.RelatedConfig(
            related={"example": related.RelatedEntry(name="example", delegate="agent-bridge")},
        ))
    assert guarded_anchors.refresh([workspace], registry_root=registry)["published"] == 1
    reader = load("guarded_anchor_map")
    facts = reader.read_map(workspace, registry)
    assert facts.status == "ready" and not facts.posture_errors
    if case == "personal-state-control-space":
        config.write_bytes(b"default_branch: main\n\x1cstateless: true\n")
    assert load("managed_session").admitted(event, home=tmp_path)
    try:
        guard_events = load("tool_call_forms").tool_events(event)
    except ValueError:
        guard_events = []
    if case in {"structured-oversized", "structured-target-overflow", "patch-overflow", "patch-union-overflow"}:
        with pytest.raises(ValueError):
            load("tool_call_forms").write_targets(event["toolName"], event["toolArgs"])
        guard_events = []
    decisions = [
        load(name).decide(guard_event, env=env, home=tmp_path)
        for guard_event in guard_events
        for name in ("statelessness_guard", "cross_repo_guard", "anchor_write_guard", "pr_supersede_guard")
    ]
    assert any(value and value.get("permissionDecision") == "deny" for value in decisions) is (
        case.startswith(("personal-state", "protected-write-name-")) or case in PATCH_PROTECTED_CASES or case in {
            "anchor-cwd", "anchor-target", "delegated", "patch-anchor",
            "batch-protected", "batch-multiple-targets", "batch-structured",
            "structured-protected", "batch-read-only-protected", "literal-shell-batch",
            "literal-shell-key-command", "literal-shell-key-name",
            "literal-shell-key-args", "literal-shell-key-collision",
            "literal-shell-carrier-primary-protected", "literal-shell-carrier-input-batch",
        }
    ), decisions
    map_path = reader.map_path(workspace, registry)
    if case == "ordinary-unrelated-inventory":
        value = json.loads(map_path.read_text(encoding="utf-8"))
        for index in range(128):
            root = f"z:\\inventory-{index}"
            value["postures"].append({
                "name": f"inventory-{index}", "delegate": "none", "path": root,
                "posture": "worktree", "elevated": False,
                "identity": {"root": root, "git_dir": root + "\\.git", "common_dir": root + "\\.git"},
            })
        map_path.write_text(json.dumps(value), encoding="utf-8")
        assert reader.read_map(workspace, registry).status == "ready"
        assert load("cross_repo_guard").decide(event, env=env, home=tmp_path) is None
    elif case == "missing-map":
        map_path.unlink()
    elif case in {
        "partial-map", "wrong-scope", "array-schema", "invalid-elevation", "noncanonical-map", "dot-segment-map",
    }:
        value = json.loads(map_path.read_text(encoding="utf-8"))
        if case == "partial-map":
            value["posture_authority"] = "partial"
        elif case == "wrong-scope":
            value["source_root"] = str(anchor)
        elif case == "array-schema":
            value["schema"] = [value["schema"]]
        elif case == "invalid-elevation":
            value["postures"][0]["elevated"] = 0
        elif case == "noncanonical-map":
            value["postures"][0]["identity"]["common_dir"] = str(anchor / ".git").upper()
        else:
            value["postures"][0]["identity"]["common_dir"] += "\\."
        map_path.write_text(json.dumps(value), encoding="utf-8")
    elif case == "oversized-config":
        config.write_text("#" * 16385, encoding="utf-8")
    elif case == "git-pointer-aba":
        alternate = admin.with_name("example-worktrex")
        shutil.copytree(admin, alternate)
        marker = workspace / ".git"
        before = marker.stat()
        text = marker.read_text(encoding="utf-8")
        marker.write_text(text.replace("example-worktree", "example-worktrex"), encoding="utf-8")
        os.utime(marker, ns=(before.st_atime_ns, before.st_mtime_ns))

    scripts = plugin / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    if real_policy:
        for path in SCRIPTS.glob("*.py"):
            shutil.copyfile(path, scripts / path.name)
    for name in (
        "pre-tool-hook.ps1", "managed-session.ps1", "hook-read-policy.json",
        "statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py",
        "pr_supersede_guard.py", "guarded_anchor_map.py", "tool_call_forms.py",
        "hook_pre_relevance.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    drift = {
        "guard-drift": "anchor_write_guard.py", "reader-drift": "guarded_anchor_map.py",
        "forms-drift": "tool_call_forms.py", "batch-read-only-forms-drift": "tool_call_forms.py",
        "literal-shell-relevance-drift": "hook_pre_relevance.py",
    }.get(case)
    if drift:
        with (scripts / drift).open("a", encoding="utf-8") as output:
            output.write("\n# changed source\n")
    elif case in {"missing-dependencies", "batch-read-only-missing-dependencies"}:
        policy = scripts / "hook-read-policy.json"
        value = json.loads(policy.read_text(encoding="utf-8"))
        value.pop("nativeWriteDependencies", None)
        policy.write_text(json.dumps(value), encoding="utf-8")
    elif case in {"literal-shell-old-proof", "literal-shell-invalid-proof"}:
        policy = scripts / "hook-read-policy.json"
        value = json.loads(policy.read_text(encoding="utf-8"))
        if case == "literal-shell-old-proof":
            value.pop("nativeShellReadCommands")
            value.pop("nativeShellReadDependencies")
        else:
            value["nativeShellReadCommands"].append(False)
        policy.write_text(json.dumps(value), encoding="utf-8")
    observed = tmp_path / "policy-input"
    (scripts / "resolve-runtime.ps1").write_text(
        "$AwPy='" + sys.executable.replace("'", "''") + "'\n", encoding="utf-8",
    )
    if cell:
        (scripts / "installation-context" / "installation-context.ps1").write_text(
            "Write-Output '" + json.dumps({"pluginRoot": str(registry)}).replace("'", "''") + "'\n"
            + "$global:LASTEXITCODE = 0\n", encoding="utf-8",
        )
    if not real_policy:
        (scripts / "hook_policy_process.py").write_text(
            "import base64,json,sys\nfrom pathlib import Path\n"
            + f"Path({str(observed)!r}).write_bytes(base64.b64decode(sys.stdin.readline()))\n"
            + "print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'fixture policy'}))\n",
            encoding="utf-8",
        )
    membership = tmp_path / "membership-interpreter"
    inventory_probe = tmp_path / "inventory-probe"
    native_membership = tmp_path / "native-membership"
    if case == "ordinary-unrelated-inventory":
        admission = scripts / "managed-session.ps1"
        admission.write_text(admission.read_text(encoding="utf-8").replace(
            "return $hasGitMarker",
            "[IO.File]::WriteAllText('" + str(native_membership).replace("'", "''")
            + "', $hasGitMarker.ToString()); return $hasGitMarker",
        ), encoding="utf-8")
        with (scripts / "managed-session.ps1").open("a", encoding="utf-8") as output:
            output.write(
                "\n$script:originalPreparedPath = ${function:Get-PreparedWorktreesPath}\n"
                "function Get-PreparedWorktreesPath {\n    param([object]$Path)\n"
                "    if ($Path -is [string] -and $Path.StartsWith('z:\\inventory-')) {\n"
                + "        [IO.File]::WriteAllText('" + str(inventory_probe).replace("'", "''") + "', 'called')\n"
                "        throw 'Unexpected inventory filesystem probe'\n    }\n"
                "    return & $script:originalPreparedPath $Path\n}\n",
            )
    if case in {"old-admission", "batch-read-only-old-admission", "literal-shell-unprepared"}:
        admission = scripts / "managed-session.ps1"
        admission.write_text(
            admission.read_text(encoding="utf-8").replace(
                "function Test-ManagedWorktreesSession {", "function Test-OriginalManagedWorktreesSession {",
            )
            + "\nfunction Test-ManagedWorktreesSession {\n"
            "    param([Parameter(Mandatory = $true)][string]$Raw, [ref]$Prepared)\n"
            + (
                "    return Test-OriginalManagedWorktreesSession -Raw $Raw -Prepared $Prepared\n"
                if case == "old-admission" else
                "    $admitted = Test-OriginalManagedWorktreesSession -Raw $Raw\n"
                "    if ($null -ne $Prepared) { $Prepared.Value = $false }\n"
                "    return $admitted\n"
            ) + "}\n",
            encoding="utf-8",
        )
    with (scripts / "managed-session.ps1").open("a", encoding="utf-8") as output:
        output.write(
            "\nfunction Get-ManagedWorktreesPython { "
            + "[IO.File]::WriteAllText('" + str(membership).replace("'", "''") + "', 'called'); "
            + "return '" + sys.executable.replace("'", "''") + "' }\n"
        )
    env["COPILOT_PLUGIN_ROOT"] = str(plugin)
    entry = json.loads((SCRIPTS.parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["preToolUse"][0]
    if case in {"batch-read-only-attribution", "shell-entry-attribution", "literal-shell-entry-attribution"} or (
        case.startswith("literal-shell-carrier-") and case.endswith("-attribution")
    ):
        qualify_complete_native_entry(
            scripts, entry["powershell"], executable, event, env, workspace, capsys,
            literal_read=case.startswith("literal-shell-"),
        )
        assert not membership.exists()
        return
    native_entry = scripts / "pre-tool-hook.ps1"
    if case == "literal-shell-prior-reader":
        source = native_entry.read_text(encoding="utf-8")
        boundary = "\ntry {\n    $reader = [IO.StreamReader]"
        assert source.count(boundary) == 1
        native_entry.write_text(source.replace(boundary, "\n" + NATIVE_READ_V1 + boundary, 1), encoding="utf-8")
    # Fixture-only markers classify a timeout without retrying or extending the child.
    instrumented = native_entry.read_text(encoding="utf-8")
    for boundary, source in (
        ("script-entry", "$ErrorActionPreference = 'Stop'"),
        ("membership-start", "    $admitted = if ($supportsFacts) {"),
        ("membership-complete", "    if (-not $admitted) { exit 0 }"),
        ("native-proof-start", "function Test-NativePreToolWrite([object]$Payload, [object]$Facts) {"),
        ("policy-start", "function Invoke-PreToolPolicy([string]$Python, [string]$Payload, [ref]$Diagnostic) {"),
    ):
        assert instrumented.count(source) == 1
        marker = f"Write-NativeWriteFixturePhase '{boundary}'"
        instrumented = instrumented.replace(
            source, source + "\n" + marker if source.endswith("{") else marker + "\n" + source,
        )
    native_entry.write_text(instrumented, encoding="utf-8")
    admission = scripts / "managed-session.ps1"
    admission.write_text(
        instrument_prepared_admission(admission.read_text(encoding="utf-8")), encoding="utf-8",
    )
    raw = json.dumps(event, ensure_ascii=False, indent=2).encode("utf-8")
    started = time.monotonic()
    started_ms = time.time_ns() // 1_000_000
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", NATIVE_WRITE_PHASE_WRITER + entry["powershell"]],
            input=raw, capture_output=True, timeout=10,
            cwd=workspace, env=env, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as error:
        progress, diagnostic = native_write_fixture_stderr(error.stderr or b"")
        raise AssertionError(
            f"{shell} native write exceeded the original 10s child limit; start_ms={started_ms}; "
            f"phases={progress or 'no command entry'!r}; stdout={error.stdout!r}; stderr={diagnostic!r}"
        ) from error
    native_seconds = time.monotonic() - started
    progress, result.stderr = native_write_fixture_stderr(result.stderr)
    assert " command-entry" in progress, result.stderr
    phase_names = [line.split(" ", 1)[1] for line in progress.splitlines()]
    if "prepared-marker-json" in phase_names:
        assert "prepared-marker-opened" in phase_names, progress
        assert phase_names.index("prepared-marker-opened") < phase_names.index("prepared-marker-json"), progress
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert not membership.exists()
    if case == "ordinary-unrelated-inventory":
        assert native_membership.read_text() == "True"
    assert not inventory_probe.exists()
    policy_required = not (case.startswith(("ordinary", "write-name-")) or case in PATCH_NATIVE_CASES or case in {
        "create", "path-alias", "json-args", "real-policy", "batch",
        "batch-mixed", "batch-json-args", "batch-limit", "batch-working-directory", "batch-real-policy",
        "structured", "structured-files", "structured-changes", "structured-only", "structured-json",
        "structured-aliases", "structured-duplicates", "structured-limit", "batch-structured-safe",
        "structured-real-policy",
        "batch-target-limit",
        "batch-read-only", "batch-read-only-mixed", "batch-read-only-limit",
        "batch-read-only-working-directory", "batch-read-only-real-policy",
        "literal-shell-head",
        "literal-shell-carrier-primary-safe",
    })
    assert observed.exists() is policy_required, result.stdout
    if policy_required:
        assert observed.read_bytes() == raw
        assert json.loads(result.stdout)["permissionDecision"] == "deny"
    else:
        assert result.stdout == b""
    if case == "literal-shell-prior-reader":
        observed.unlink()
        read_event = {**event, "toolName": "view", "toolArgs": {"path": "read.txt"}}
        prior_read = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", NATIVE_WRITE_PHASE_WRITER + entry["powershell"]],
            input=json.dumps(read_event).encode("utf-8"), capture_output=True, timeout=10,
            cwd=workspace, env=env, **no_window_kwargs(),
        )
        _, prior_read.stderr = native_write_fixture_stderr(prior_read.stderr)
        assert prior_read.returncode == 0 and not prior_read.stderr and prior_read.stdout == b""
        assert not observed.exists()
    if case == "patch-protected-input":
        with capsys.disabled():
            print(json.dumps({
                "shell": shell, "case": case, "start_ms": started_ms,
                "native_entry_seconds": native_seconds, "phases": progress.splitlines(),
            }))
    if real_policy:
        contract = scripts / "hook-read-policy.json"
        value = json.loads(contract.read_text(encoding="utf-8"))
        value.pop("nativeWriteDependencies")
        contract.write_text(json.dumps(value), encoding="utf-8")
        started = time.monotonic()
        fallback = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", NATIVE_WRITE_PHASE_WRITER + entry["powershell"]],
            input=raw, capture_output=True, timeout=10,
            cwd=workspace, env=env, **no_window_kwargs(),
        )
        fallback_seconds = time.monotonic() - started
        _, fallback.stderr = native_write_fixture_stderr(fallback.stderr)
        assert fallback.returncode == 0 and not fallback.stderr, fallback.stdout + fallback.stderr
        assert json.loads(fallback.stdout) == {}
        protected = {**event, "toolArgs": {"path": "efforts/private.md"}}
        if case == "patch-real-policy":
            protected["toolArgs"] = {"input": patch("Update File: safe.py", "Move to: efforts/private.md")}
        if case in {"batch-real-policy", "batch-read-only-real-policy"}:
            protected["toolCalls"] = [
                *event["toolCalls"][:-1],
                {"name": "edit", "args": {"path": "efforts/private.md"}},
            ]
        denied = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", NATIVE_WRITE_PHASE_WRITER + entry["powershell"]],
            input=json.dumps(protected).encode("utf-8"), capture_output=True, timeout=10,
            cwd=workspace, env=env, **no_window_kwargs(),
        )
        _, denied.stderr = native_write_fixture_stderr(denied.stderr)
        assert denied.returncode == 0 and not denied.stderr, denied.stdout + denied.stderr
        assert json.loads(denied.stdout)["permissionDecision"] == "deny"
        assert not membership.exists()
        print(json.dumps({
            "shell": shell, "same_fixture": True, "native_entry_seconds": native_seconds,
            "real_policy_entry_seconds": fallback_seconds,
        }))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows prepared cell membership")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("case", [
    "current", "receipt", "payload", "source-witness", "scope", "legacy-association",
    "receipt-aba", "payload-aba", "payload-selection", "missing-files",
    "original-fallback", "root-versions", "root-snapshots", "root-state", "root-run",
    "root-logs", "root-cache", "root-launchers", "root-alias", "missing-roots",
    "directory-source", "directory-ancestor", "directory-fallback", "missing-source-kind",
    "payload-junction", "receipt-payload-junction", "receipt-plugin-junction",
    "receipt-namespace-junction", "missing-declared-paths",
    "receipt-payload-fallback", "receipt-plugin-fallback", "receipt-namespace-fallback",
])
def test_prepared_cell_membership_reuses_only_complete_current_owner_observation(
    tmp_path, monkeypatch, shell, case,
):
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    executable = shutil.which(shell)
    if not executable:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"Required native membership shell {shell} is unavailable")
        pytest.skip(f"{shell} is unavailable")
    native_run = subprocess.run
    monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
        run=lambda *args, **kwargs: native_run(
            *args, **(kwargs | no_window_kwargs() | {"timeout": 10}),
        ),
    ))
    anchor, legacy = fixture_project(tmp_path)
    workspace = fixture_linked_worktree(tmp_path, anchor)[0]
    payload_root = fixtures._payload(tmp_path, "selected")
    shutil.copyfile(SCRIPTS / "managed-session.ps1", payload_root / "scripts" / "managed-session.ps1")
    namespace_source = tmp_path / "source-parent" / "marketplace"
    source = None
    if case.startswith("directory-"):
        namespace_source.mkdir(parents=True)
        source = {"source": "directory", "path": str(namespace_source)}
    context, registry = fixtures._stamp(
        tmp_path / "durable", payload_root, marketplace="selected", repository="example/selected",
        source=source,
    )
    if case == "root-alias":
        inner = registry / "inner"
        inner.mkdir()
        alias = registry / "alias"
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(alias), str(inner)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        receipt = json.loads(context.read_text())
        receipt["roots"]["versions"] = "alias/versions"
        context.write_text(json.dumps(receipt))
    receipt_alias = tmp_path / "receipt-alias"
    if case.startswith(("receipt-payload-", "receipt-plugin-", "receipt-namespace-")):
        target = (
            payload_root if case.startswith("receipt-payload-") else
            registry if case.startswith("receipt-plugin-") else registry.parent.parent
        )
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(receipt_alias), str(target)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        receipt = json.loads(context.read_text())
        if case.startswith("receipt-payload-"):
            receipt["payload"]["root"] = str(receipt_alias)
        elif case.startswith("receipt-plugin-"):
            receipt["pluginRoot"] = str(receipt_alias)
        else:
            receipt["namespaceReceipt"] = str(receipt_alias / "namespace.json")
        context.write_text(json.dumps(receipt))
    for name in ("projects.yaml", "repos.yaml"):
        (registry / name).write_text((legacy / name).read_text())
    selected_payload = payload_root
    if case == "payload-junction":
        selected_payload = tmp_path / "payload-alias"
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(selected_payload), str(payload_root)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
    fixtures._select(monkeypatch, context, selected_payload)
    session = tmp_path / ".copilot" / "session-state" / "prepared-cell"
    session.mkdir(parents=True)
    event = {"sessionId": "prepared-cell", "cwd": str(workspace)}
    module = load("managed_session")
    resolution_errors = []
    original_resolve = module.resolve_registry_context

    def resolve_with_diagnostics(*args, **kwargs):
        try:
            return original_resolve(*args, **kwargs)
        except Exception as error:
            resolution_errors.append(repr(error))
            raise

    monkeypatch.setattr(module, "resolve_registry_context", resolve_with_diagnostics)
    observation = module.inspect(event, home=tmp_path)
    assert observation["admitted"], (resolution_errors, observation)
    marker = session / "agent-worktrees" / "managed-session.json"
    value = json.loads(marker.read_text())
    if case == "receipt":
        context.write_text(context.read_text() + "\n")
    elif case == "payload":
        manifest = payload_root / "plugin.json"
        manifest.write_text(manifest.read_text() + "\n")
    elif case == "source-witness":
        namespace = json.loads(context.read_text())["namespaceReceipt"]
        value["sources"].pop(namespace)
        value["inputs"].pop(namespace)
        marker.write_text(json.dumps(value))
    elif case == "scope":
        value["scope"]["registry"] = str(legacy)
        marker.write_text(json.dumps(value))
    elif case == "legacy-association":
        value.pop("installation", None)
        marker.write_text(json.dumps(value))
    elif case in {"receipt-aba", "payload-aba"}:
        path = context if case == "receipt-aba" else payload_root / "plugin.json"
        before = path.stat()
        path.write_bytes(path.read_bytes().replace(b"{", b"[", 1))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert path.stat().st_size == before.st_size
        assert path.stat().st_mtime_ns == before.st_mtime_ns
        assert not load("managed_session").admitted(event, home=tmp_path)
    elif case == "missing-files":
        value["installation"].pop("files")
        marker.write_text(json.dumps(value))
    elif case == "missing-roots":
        value["installation"].pop("roots")
        marker.write_text(json.dumps(value))
    elif case == "missing-source-kind":
        value["installation"].pop("sourceKind")
        marker.write_text(json.dumps(value))
    elif case == "missing-declared-paths":
        value["installation"].pop("declaredPaths")
        marker.write_text(json.dumps(value))
    elif case == "payload-junction":
        other = fixtures._payload(tmp_path, "other")
        selected_payload.rmdir()
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(selected_payload), str(other)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        assert not module.admitted(event, home=tmp_path)
    elif case in {"receipt-payload-junction", "receipt-plugin-junction", "receipt-namespace-junction"}:
        assert "installation" not in value
        other = fixtures._payload(tmp_path, "other") if case == "receipt-payload-junction" else (
            tmp_path / "other"
        )
        other.mkdir(exist_ok=True)
        receipt_alias.rmdir()
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(receipt_alias), str(other)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        assert not module.admitted(event, home=tmp_path)
    elif case in {
        "original-fallback", "directory-fallback", "receipt-payload-fallback",
        "receipt-plugin-fallback", "receipt-namespace-fallback",
    }:
        if case.startswith("receipt-"):
            assert "installation" not in value
        value.pop("installation", None)
        marker.write_text(json.dumps(value))
        for name in ("managed_session.py", "execution_platform.py", "managed_project_identity.py",
                     "remote_identity.py", "guarded_anchor_map.py", "hook_policy_process.py"):
            shutil.copyfile(SCRIPTS / name, payload_root / "scripts" / name)
    elif case.startswith("directory-"):
        path = namespace_source if case == "directory-source" else namespace_source.parent
        shutil.rmtree(path)
        external = tmp_path / "external"
        (external / "marketplace").mkdir(parents=True)
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(path), str(external)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        assert not module.admitted(event, home=tmp_path)
    elif case.startswith("root-"):
        path = registry / (case.removeprefix("root-") if case != "root-alias" else "alias")
        if case == "root-alias":
            path.rmdir()
        elif path.exists():
            shutil.rmtree(path)
        external = tmp_path / "external"
        external.mkdir()
        native_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(path), str(external)],
            check=True, capture_output=True, timeout=10, **no_window_kwargs(),
        )
        assert not module.admitted(event, home=tmp_path)
    env = environment(tmp_path, workspace)
    env.update(
        COPILOT_EXTENSIONS_CONTEXT=str(context), COPILOT_PLUGIN_ROOT=str(payload_root),
        AGENT_WORKTREES_PAYLOAD_ROOT=str(selected_payload),
        PREPARED_MEMBERSHIP_SCRIPT=str(payload_root / "scripts" / "managed-session.ps1"),
    )
    if case == "payload-selection":
        env["COPILOT_PLUGIN_ROOT"] = str(legacy)
    command = ". $env:PREPARED_MEMBERSHIP_SCRIPT; "
    fallback = case in {
        "original-fallback", "directory-fallback", "receipt-payload-fallback",
        "receipt-plugin-fallback", "receipt-namespace-fallback",
    }
    if fallback:
        env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    else:
        command += "function Get-ManagedWorktreesPython { throw 'interpreter lookup is forbidden' }; "
    command += (
        "[Console]::Out.Write((Test-ManagedWorktreesSession -Raw ([Console]::In.ReadToEnd())).ToString())"
    )
    result = native_run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(event), text=True, capture_output=True, timeout=10,
        cwd=workspace, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert result.stdout == ("True" if case == "current" or fallback else "False")
    if case == "original-fallback":
        assert len(json.loads(marker.read_text())["installation"]["files"]) == 3
    assert not (legacy / "cache").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows prepared membership")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("damage", [
    "withdraw", "nested", "git-link", "session", "registry", "context",
    "missing-witness", "malformed", "oversize", "wrong-type", "workspace-unicode",
    "session-array", "cwd-array", "working-directory-array",
])
def test_prepared_native_membership_rejects_changed_or_unavailable_inputs(
    tmp_path, monkeypatch, shell, damage,
):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    module = load("managed_session")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    anchor, registry = fixture_project(tmp_path)
    workspace, admin = fixture_linked_worktree(tmp_path, anchor)
    nested = workspace / "src"
    nested.mkdir()
    session = tmp_path / ".copilot" / "session-state" / "prepared"
    session.mkdir(parents=True)
    payload = {"sessionId": "prepared", "cwd": str(anchor if damage == "workspace-unicode" else nested)}
    assert module.admitted(payload, home=tmp_path)
    marker = session / "agent-worktrees" / "managed-session.json"
    env = environment(tmp_path, nested)
    if damage == "withdraw":
        (registry / "projects.yaml").write_text("projects: {}\n")
    elif damage == "nested":
        fixture_checkout(nested)
    elif damage == "git-link":
        (admin / "gitdir").write_text(str(tmp_path / "foreign" / ".git") + "\n")
    elif damage == "session":
        payload["sessionId"] = "another"
    elif damage == "registry":
        env["AGENT_HOME"] = str(tmp_path / "another")
    elif damage == "context":
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(tmp_path / "install.json")
    elif damage == "missing-witness":
        value = json.loads(marker.read_text())
        del value["inputs"][os.path.normcase(str(workspace / ".git"))]
        marker.write_text(json.dumps(value))
    elif damage == "malformed":
        marker.write_text("{")
    elif damage == "oversize":
        marker.write_bytes(b" " * (1024 * 1024 + 1))
    elif damage == "wrong-type":
        value = json.loads(marker.read_text())
        value["scope"]["session"] = ["prepared"]
        marker.write_text(json.dumps(value))
    elif damage == "workspace-unicode":
        other = fixture_checkout(tmp_path / "anchor-e\u0301")
        payload["cwd"] = str(other)
        assert not module.admitted(payload, home=tmp_path)
    elif damage == "session-array":
        payload["sessionId"] = ["prepared"]
        assert not module.admitted(payload, home=tmp_path)
    elif damage == "cwd-array":
        payload["cwd"] = [str(nested)]
        assert not module.admitted(payload, home=tmp_path)
    elif damage == "working-directory-array":
        payload["workingDirectory"] = [payload.pop("cwd")]
        assert not module.admitted(payload, home=tmp_path)
    env["PREPARED_MEMBERSHIP_SCRIPT"] = str(SCRIPTS / "managed-session.ps1")
    command = (
        ". $env:PREPARED_MEMBERSHIP_SCRIPT; "
        "$raw = [Console]::In.ReadToEnd(); "
        "[Console]::Out.Write((Test-PreparedManagedWorktreesSession -Raw $raw).ToString())"
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(payload), text=True, capture_output=True, timeout=10,
        cwd=nested, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "False", result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse", "sessionStart", "sessionEnd"])
@pytest.mark.parametrize("repo_class", ["worktree", "reference", None])
def test_real_python_entry_is_inert_for_registry_only_repo(kind, repo_class, tmp_path):
    anchor, registry = fixture_project(tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    repos = registry / "repos.yaml"
    if repo_class is None:
        repos.unlink()
        (registry / "projects.yaml").unlink()
    else:
        repos.write_text(repos.read_text().replace("class: worktree", "class: " + repo_class))
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "hook_client.py"), kind],
        input=json.dumps({"sessionId": "unmanaged", "cwd": str(anchor), "toolName": "edit",
                          "toolArgs": {"path": str(anchor / "source.py")}}),
        text=True, capture_output=True, timeout=10, cwd=anchor,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"", "{}"}
    assert not result.stderr
    assert not (tmp_path / ".copilot").exists()
    assert not (registry / "cache").exists()


def test_real_managed_pretool_preserves_anchor_write_protection(tmp_path):
    anchor, _ = fixture_project(tmp_path)
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "hook_client.py"), "preToolUse"],
        input=json.dumps({"sessionId": "managed", "cwd": str(anchor), "toolName": "edit",
                          "toolArgs": {"path": str(anchor / "source.py")}}),
        text=True, capture_output=True, timeout=10, cwd=anchor,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["permissionDecision"] == "deny"
    assert "anchor-write-guard" in result.stdout


@pytest.mark.parametrize("entry", ["python", "powershell"])
@pytest.mark.timeout(120)
def test_native_batch_entry_and_live_scoped_grants(tmp_path, record_testsuite_property, entry):
    anchor, registry = fixture_project(tmp_path)
    caller, _ = fixture_linked_worktree(tmp_path, anchor)
    reader = load("guarded_anchor_map")
    targets = [fixture_checkout(tmp_path / name) for name in ("first", "second")]
    rows = [{
        "name": target.name, "delegate": "agent-bridge", "path": reader.canonical(target),
        "locus": None, "identity": reader.repository_identity(target),
    } for target in targets]
    for index in range(98):
        path = reader.canonical(tmp_path / f"inventory-{index}")
        rows.append({
            "name": f"inventory-{index}", "delegate": "agent-bridge", "path": path,
            "locus": None,
            "identity": {"root": path, "git_dir": path + os.sep + ".git",
                         "common_dir": path + os.sep + ".git"},
        })
    path = reader.map_path(caller, registry)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema": "agent-worktrees.guarded-anchors", "version": 1,
        "source_root": reader.canonical(caller),
        "source_identity": reader.repository_identity(caller),
        "registry_root": reader.canonical(registry), "inventory_authority": "complete",
        "generation": "native-entry-fixture", "roots": rows,
    }))
    env = environment(tmp_path, caller)
    for key in ("CROSS_REPO_GUARD", "CROSS_REPO_GUARD_MODE"):
        env.pop(key, None)
    command = [sys.executable, "-B", str(SCRIPTS / "hook_client.py"), "preToolUse"]
    if entry == "powershell":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            pytest.skip("PowerShell is unavailable")
        payload = tmp_path / "payload"
        shutil.copytree(SCRIPTS, payload / "scripts")
        (payload / "scripts" / "resolve-runtime.ps1").write_text(
            "param([string]$TraceMode)\n$AwPy = '" + sys.executable.replace("'", "''") + "'\n",
            encoding="utf-8-sig",
        )
        env["COPILOT_PLUGIN_ROOT"] = str(payload)
        hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
        command = [powershell, "-NoProfile", "-NonInteractive", "-Command",
                   hooks["hooks"]["preToolUse"][0]["powershell"]]
    session = tmp_path / ".copilot" / "session-state" / "native-entry"
    session.mkdir(parents=True)
    event = {"sessionId": "native-entry", "cwd": str(caller), "toolCalls": [
        {"id": "read", "name": "view", "args": {"path": str(targets[0] / "source.py")}},
        {"id": "write", "name": "apply_patch", "args": (
            f"*** Begin Patch\n*** Update File: {targets[0] / 'source.py'}\n*** End Patch\n"
        )},
    ]}
    timings = []

    def invoke(value):
        started = time.monotonic()
        result = subprocess.run(
            command, input=json.dumps(value), text=True, capture_output=True,
            timeout=15, cwd=caller, env=env, **no_window_kwargs(),
        )
        timings.append(round((time.monotonic() - started) * 1000, 2))
        print(f"{entry} {timings[-1]} ms: {result.stdout} {result.stderr}")
        assert result.returncode == 0, result.stderr
        decision = json.loads(result.stdout or "{}")
        assert "could not evaluate" not in decision.get("additionalContext", ""), result.stderr
        return decision

    assert invoke({**event, "cwd": str(tmp_path)}).get("permissionDecision") is None
    denied = invoke(event)
    assert denied["permissionDecision"] == "deny"
    assert "allow-edits first" in denied["permissionDecisionReason"]
    assert (session / "agent-worktrees" / "managed-session.json").is_file()
    grants = registry / "allow-edits.json"
    grants.write_text(json.dumps({
        "grants": {"first": {"expires_at_ms": (time.time() + 600) * 1000}},
    }))
    assert invoke(event).get("permissionDecision") is None
    mixed = {**event, "toolCalls": [*event["toolCalls"], {
        "id": "other", "name": "apply_patch", "args": (
            f"*** Begin Patch\n*** Delete File: {targets[1] / 'source.py'}\n*** End Patch\n"
        ),
    }]}
    assert "allow-edits second" in invoke(mixed)["permissionDecisionReason"]
    grants.write_text(json.dumps({
        "grants": {"first": {"expires_at_ms": (time.time() - 10) * 1000}},
    }))
    assert invoke(event)["permissionDecision"] == "deny"
    grants.unlink()
    assert invoke(event)["permissionDecision"] == "deny"
    assert invoke({**event, "toolCalls": event["toolCalls"][:1]}).get("permissionDecision") is None
    record_testsuite_property(f"native_batch_entry_ms:{entry}", json.dumps(timings))
    record_testsuite_property(f"native_batch_inventory:{entry}", len(rows))


def test_invalid_query_does_not_reuse_ambient_admission(tmp_path):
    anchor, registry = fixture_project(tmp_path)
    env = environment(tmp_path, anchor)
    env["COPILOT_AGENT_SESSION_ID"] = "managed"
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "managed_session.py")],
        input="{", text=True, capture_output=True, timeout=5, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["admitted"] is False
    assert not (registry / "cache").exists()


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse", "sessionStart", "sessionEnd"])
def test_real_powershell_hook_does_not_activate_unmanaged_repo(kind, tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    anchor, registry = fixture_project(tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    command = hooks["hooks"][kind][0]["powershell"]
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps({"sessionId": "unmanaged", "cwd": str(anchor), "toolName": "edit",
                          "toolArgs": {"path": str(anchor / "source.py")}}),
        text=True, capture_output=True, timeout=15, cwd=anchor,
        env=environment(tmp_path, anchor), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"", "{}"}, result.stdout
    assert not result.stderr
    assert not (tmp_path / ".copilot").exists()


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe", "bash"])
@pytest.mark.parametrize("case", ["unmanaged", "managed", "missing-helper"])
def test_session_start_gates_older_client_before_fallback(shell, case, tmp_path, native_bash):
    executable = native_bash if shell == "bash" else shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    anchor, registry = fixture_project(tmp_path)
    if case == "unmanaged":
        (registry / "projects.yaml").write_text("projects: {}\n")
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "hook_client.py").unlink()
    if case == "missing-helper":
        (payload / "scripts" / "managed_session.py").unlink()
    legacy = registry / "bin"
    legacy.mkdir()
    marker = tmp_path / "older-client-ran"
    (legacy / "hook_client.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\nprint('{}')\n"
    )
    env = environment(tmp_path, anchor)
    env.update(COPILOT_PLUGIN_ROOT=str(payload), ADMISSION_TEST_PYTHON=sys.executable)
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    command = hooks["hooks"]["sessionStart"][0]["bash" if shell == "bash" else "powershell"]
    if shell == "bash":
        prefix = (
            'export HOME="$(cygpath -au "$HOME")"; '
            'export COPILOT_PLUGIN_ROOT="$(cygpath -au "$COPILOT_PLUGIN_ROOT")"; '
            'ADMISSION_TEST_PYTHON="$(cygpath -au "$ADMISSION_TEST_PYTHON")"; '
        ) if os.name == "nt" else ""
        command = prefix + 'python3() { "$ADMISSION_TEST_PYTHON" "$@"; }; ' + command
        args = [executable, "-c", command]
    else:
        args = [executable, "-NoProfile", "-NonInteractive", "-Command", command]
    result = subprocess.run(
        args, input=json.dumps({"sessionId": "older-client", "cwd": str(anchor)}),
        text=True, capture_output=True, timeout=15, cwd=anchor, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "{}", result.stdout
    assert not result.stderr
    assert marker.exists() is (case == "managed")


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_session_start_forwards_unicode_membership_query(shell, tmp_path):
    powershell = shutil.which(shell)
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    anchor, _ = fixture_project(tmp_path)
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "hook_client.py").write_text(
        "import json,sys\nfrom managed_session import admitted\n"
        "assert admitted(json.load(sys.stdin))\nprint('{}')\n"
    )
    env = environment(tmp_path, anchor)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command",
         hooks["hooks"]["sessionStart"][0]["powershell"]],
        input=json.dumps({"sessionId": "managed", "cwd": str(anchor)}, ensure_ascii=False),
        text=True, capture_output=True, encoding="utf-8", timeout=10,
        cwd=anchor, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("opaque", [
    {"Name": 1, "name": 2},
    {"": {"Name": 1, "name": 2}},
    {"__type": None, "Name": 1, "name": 2, "": 3},
    {"__type": "opaque", "Name": 1, "name": 2, "": 3},
])
def test_real_admission_accepts_opaque_json_keys(tmp_path, shell, opaque):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    anchor, registry = fixture_project(tmp_path)
    if os.name == "nt":
        (registry / "repos.yaml").write_text(json.dumps({
            "repos": {"example": {"windows": str(anchor).upper()}},
        }))
    event = {
        "sessionId": "json-keys", "cwd": str(anchor), "CWD": str(tmp_path),
        "toolArgs": opaque, "toolResult": opaque, "": "ignored", "Keys": "opaque",
    }
    helper = str(SCRIPTS / "managed-session.ps1").replace("'", "''")
    env = environment(tmp_path, anchor)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command",
         f". '{helper}'; Test-ManagedWorktreesSession ([Console]::In.ReadToEnd())"],
        input=json.dumps(event), capture_output=True, text=True, timeout=10,
        env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "True", result.stdout + result.stderr
    assert not result.stderr


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows admission serialization")
@pytest.mark.parametrize("shell,old_engine", [
    ("pwsh", False), ("pwsh", True), ("powershell.exe", False),
])
def test_admission_query_serialization_cold_fallback(tmp_path, shell, old_engine, capsys):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    samples = []
    for index, variant in enumerate(("cmdlet", "native", "cmdlet")):
        home = tmp_path / str(index)
        home.mkdir()
        anchor, registry = fixture_project(home)
        session = home / ".copilot" / "session-state" / "query"
        session.mkdir(parents=True)
        scripts = home / "payload" / "scripts"
        shutil.copytree(SCRIPTS, scripts)
        managed = scripts / "managed-session.ps1"
        managed.write_bytes(managed.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        source = managed.read_text(encoding="utf-8").replace("\r\n", "\n")
        original = "$frame = ConvertTo-Json -InputObject $query -Compress"
        candidate = (
            "$frame = if ($PSVersionTable.PSVersion.Major -ge 6) {\n"
            "            ConvertTo-PreparedWorktreesJson -InputObject $query -Depth 2\n"
            "        } else { ConvertTo-Json -InputObject $query -Compress }"
        )
        assert source.count(candidate) == 1
        if variant == "cmdlet":
            source = source.replace(candidate, original)
        for token in ("        $frame =", "        $python = Get-ManagedWorktreesPython"):
            assert source.count(token) == 1
        source = source.replace(
            "        $frame =",
            "        $script:serializationClock = [Diagnostics.Stopwatch]::StartNew()\n        $frame =",
        )
        source = source.replace(
            "        $python = Get-ManagedWorktreesPython",
            "        $script:serializationClock.Stop()\n"
            "        $script:queryFrame = $frame\n        $python = Get-ManagedWorktreesPython",
        )
        if old_engine:
            source = source.replace(
                "'Microsoft.PowerShell.Commands.JsonObject+ConvertToJsonContext'",
                "'UnavailablePreparedJsonContext'",
            )
        (scripts / "managed-session.ps1").write_text(source, encoding="utf-8")
        query = {"sessionId": "query", "cwd": str(anchor), "workingDirectory": str(home)}
        event = {
            **query, "CWD": str(home),
            "toolName": "edit", "toolArgs": {"text": "quote'\"\\\n\t\u00e9\U0001f680"},
            "__type": "opaque", "Keys": [], "": {"discard": True},
        }
        driver = home / "admission-comparison.ps1"
        driver.write_text(
            "$ErrorActionPreference='Stop'\n"
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false,$true)\n"
            ". $env:PREPARED_MEMBERSHIP_SCRIPT\n"
            "$script:jsonCalls=0; $script:pythonCalls=0\n"
            + r"""
function ConvertTo-Json {
    param([object]$InputObject, [switch]$Compress, [int]$Depth=2)
    $script:jsonCalls++
    & ($ExecutionContext.InvokeCommand.GetCommand('ConvertTo-Json',
        [Management.Automation.CommandTypes]::Cmdlet)) @PSBoundParameters
}
$script:originalPython=(Get-Command Get-ManagedWorktreesPython).ScriptBlock
function Get-ManagedWorktreesPython {
    $script:pythonCalls++
    & $script:originalPython
}
$raw=[Console]::In.ReadToEnd()
$clock=[Diagnostics.Stopwatch]::StartNew()
if (-not (Test-ManagedWorktreesSession $raw)) { throw 'Cold admission failed' }
$clock.Stop()
[Console]::Error.WriteLine(('admission_seconds='+$clock.Elapsed.TotalSeconds))
[Console]::Error.WriteLine(('serialization_seconds='+$script:serializationClock.Elapsed.TotalSeconds))
if ($script:pythonCalls -ne 1) { throw 'Cold admission did not use the real helper' }
if ($script:jsonCalls -ne [int]$env:EXPECTED_JSON_CALLS) { throw 'Unexpected JSON cmdlet count' }
if ($script:queryFrame.Contains("`r") -or $script:queryFrame.Contains("`n")) { throw 'Query framing is not compact' }
[Console]::Out.WriteLine($script:queryFrame)
$script:queryFrame=$null
if (-not (Test-ManagedWorktreesSession $raw)) { throw 'Warm admission failed' }
if ($script:pythonCalls -ne 1) { throw 'Warm admission rediscovered membership' }
if ($null -ne $script:queryFrame) { throw 'Warm admission serialized another query' }
""",
            encoding="utf-8",
        )
        env = environment(home, anchor)
        env.update(
            COPILOT_PLUGIN_ROOT=str(scripts.parent),
            PREPARED_MEMBERSHIP_SCRIPT=str(scripts / "managed-session.ps1"),
            AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON=sys.executable,
            EXPECTED_JSON_CALLS=str(int(variant == "cmdlet" or old_engine or shell == "powershell.exe")),
        )
        assert not (registry / "cache").exists()
        started = time.perf_counter()
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(driver)],
            input=json.dumps(event), capture_output=True, text=True, encoding="utf-8",
            timeout=10, cwd=anchor, env=env, **no_window_kwargs(),
        )
        elapsed = time.perf_counter() - started
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == query
        assert (registry / "cache" / "managed-sessions").is_dir()
        assert result.stderr.startswith("admission_seconds="), result.stderr
        timings = dict(line.split("=") for line in result.stderr.splitlines())
        assert set(timings) == {"admission_seconds", "serialization_seconds"}
        samples.append({
            "variant": variant, "process_seconds": elapsed,
            **{key: float(value) for key, value in timings.items()},
        })
    with capsys.disabled():
        print(json.dumps({"admission_query_serialization": {
            "shell": shell, "old_engine": old_engine, "samples": samples,
        }}))


@pytest.mark.skipif(os.name != "nt", reason="Windows runtime-only interpreter discovery")
@pytest.mark.parametrize("consumer", ["powershell.exe", "pwsh", "node"])
@pytest.mark.parametrize("cell", [False, True])
def test_admission_resolves_healthy_selected_runtime(
    tmp_path, monkeypatch, record_testsuite_property, consumer, cell,
):
    import venv
    from types import SimpleNamespace
    import test_registry_paths as fixtures

    executable = shutil.which(consumer)
    if not executable:
        pytest.skip(f"{consumer} is unavailable")
    anchor, legacy = fixture_project(tmp_path)
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "plugin.json").write_text('{"name":"agent-worktrees","version":"0.0.1-dev1"}')
    env = environment(tmp_path, anchor)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    runtime = legacy
    if cell:
        native_run = subprocess.run
        monkeypatch.setattr(fixtures, "subprocess", SimpleNamespace(
            run=lambda *args, **kwargs: native_run(*args, **(kwargs | no_window_kwargs())),
        ))
        context, runtime = fixtures._stamp(
            tmp_path / "durable", payload, marketplace="runtime-only", repository="example/runtime-only",
        )
        for name in ("projects.yaml", "repos.yaml"):
            shutil.copyfile(legacy / name, runtime / name)
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
        env["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(payload)
        (legacy / "projects.yaml").write_text("projects: {}\n")
    slot = runtime / "versions" / "0.0.1"
    venv.EnvBuilder(with_pip=False).create(slot / ".venv")
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "0.0.1", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }))
    (runtime / "current-version").write_text("0.0.1\n")
    env["PATH"] = str(Path(env["SYSTEMROOT"]) / "System32")
    assert shutil.which("python", path=env["PATH"]) is None
    env["FIXTURE_EXPECTED_PYTHON"] = str(slot / ".venv" / "Scripts" / "python.exe")
    if consumer == "node":
        driver = tmp_path / "runtime-only.mjs"
        driver.write_text("""
import assert from "node:assert/strict";
import childProcess from "node:child_process";
import {syncBuiltinESMExports} from "node:module";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, workingDirectory] = process.argv.slice(2);
let calls = 0;
const failures = [];
const original = childProcess.execFile;
childProcess.execFile = (...args) => {
  calls++;
  const callback = args.pop();
  return original(...args, (error, stdout, stderr) => {
    if (error) failures.push({code: error.code, killed: error.killed, signal: error.signal});
    callback(error, stdout, stderr);
  });
};
syncBuiltinESMExports();
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot});
for (const sessionId of ["runtime-only", "runtime-second"]) {
  const input = {sessionId, workingDirectory};
  assert.deepEqual(await Promise.all([check(input), check(input)]), [true, true],
    JSON.stringify({sessionId, failures}));
  const coldCalls = calls;
  assert.equal(await check(input), true);
  assert.equal(calls, coldCalls, "warm membership must not spawn another discovery helper");
}
assert.equal(calls, 4,
  "concurrent checks share runtime discovery and the admission subprocess");
""")
        command = [
            executable, str(driver),
            str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
            str(payload / "scripts"), str(anchor),
        ]
        frame = ""
    else:
        helper = str(payload / "scripts" / "managed-session.ps1").replace("'", "''")
        command = [
            executable, "-NoProfile", "-NonInteractive", "-Command",
            f". '{helper}'; "
            "if ((Get-ManagedWorktreesPython) -ne $env:FIXTURE_EXPECTED_PYTHON) "
            "{ throw 'selected runtime was not resolved' }; "
            "Test-ManagedWorktreesSession ([Console]::In.ReadToEnd())",
        ]
        frame = json.dumps({"sessionId": "runtime-only", "cwd": str(anchor)})
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=anchor, env=env, **no_window_kwargs(),
    )
    visible, foreground, observed = set(), set(), set()
    try:
        process.stdin.write(frame)
        process.stdin.close()
        process.stdin = None
        deadline = time.monotonic() + 15
        from test_status_monitor_windows import (
            _descendants, _foreground_state, _is_terminal_window,
            _process_snapshot, _window_snapshot,
        )

        while process.poll() is None:
            processes = _process_snapshot()
            descendants = _descendants(process.pid, processes)
            observed.update(pid for pid in descendants if pid != process.pid and
                            "python" in processes.get(pid, ("", 0))[0].lower())
            visible.update(hwnd for hwnd, window in _window_snapshot(processes).items()
                           if window[0] in descendants)
            active = _foreground_state(processes)
            if active[1] in descendants and _is_terminal_window(active):
                foreground.add(active[0])
            assert time.monotonic() < deadline, "runtime-only admission exceeded its fixture deadline"
            time.sleep(0.01)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert process.returncode == 0, stdout + stderr
    if consumer != "node":
        assert stdout.strip() == "True", stdout + stderr
    assert observed and not visible and not foreground
    record_testsuite_property(f"runtime_only_visible_windows:{consumer}:{cell}", len(visible))
    record_testsuite_property(f"runtime_only_foreground_transitions:{consumer}:{cell}", len(foreground))
    assert not (runtime / "logs").exists(), "passive resolver must not write boot traces"
    assert not (tmp_path / ".copilot").exists()
    if cell:
        assert not (legacy / "cache").exists(), "selected cell must not admit through legacy ownership"


@pytest.mark.skipif(os.name != "nt", reason="Git Bash consumer on Windows")
def test_posix_session_end_uses_runtime_without_path_python(tmp_path, native_bash):
    import venv

    anchor, runtime = fixture_project(tmp_path)
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "hook_client.py").write_text(
        "import json,sys\nprint(json.dumps({'event':sys.argv[1],'payload':json.load(sys.stdin)}))\n"
    )
    slot = runtime / "versions" / "0.0.1"
    venv.EnvBuilder(with_pip=False).create(slot / ".venv")
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "0.0.1", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }))
    (runtime / "current-version").write_text("0.0.1\n")
    env = environment(tmp_path, anchor)
    converted = subprocess.run(
        [native_bash, "-c", 'cygpath -au "$1"', "fixture", str(tmp_path)],
        capture_output=True, text=True, timeout=5, env=env, **no_window_kwargs(),
    )
    assert converted.returncode == 0, converted.stderr
    env.update(HOME=converted.stdout.strip(), PATH="/usr/bin:/bin")
    event = {"sessionId": "runtime-only", "cwd": str(anchor), "reason": "user_exit"}
    result = subprocess.run(
        [native_bash, "-c",
         'if command -v python3 || command -v python; then exit 90; fi; bash "$1"',
         "fixture", str(payload / "scripts" / "deregister-session.sh")],
        input=json.dumps(event), capture_output=True, text=True, timeout=15,
        env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"event": "sessionEnd", "payload": event}
    assert not result.stderr
    assert not (runtime / "logs").exists()


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX extension process")
def test_posix_extension_resolves_runtime_without_path_python(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    anchor, runtime = fixture_project(tmp_path)
    slot = runtime / "versions" / "0.0.1"
    python = slot / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "0.0.1", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }))
    (runtime / "current-version").write_text("0.0.1\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("dirname", "basename", "awk", "tr", "cat", "sort", "find", "date", "head", "tail"):
        executable = shutil.which(name)
        assert executable, name
        (tools / name).symlink_to(executable)
    driver = tmp_path / "runtime-only.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, workingDirectory] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot});
assert.equal(await check({sessionId: "runtime-only", workingDirectory}), true);
""")
    env = environment(tmp_path, anchor)
    env["PATH"] = str(tools)
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(SCRIPTS), str(anchor)],
        capture_output=True, text=True, timeout=15, env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (runtime / "logs").exists()


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_real_powershell_managed_pretool_preserves_protection(tmp_path, record_testsuite_property, shell):
    powershell = shutil.which(shell)
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    anchor, _ = fixture_project(tmp_path)
    hooks = json.loads((SCRIPTS.parent / "hooks.json").read_text())
    env = environment(tmp_path, anchor)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    payload = tmp_path / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "param([string]$TraceMode)\n$AwPy = '" + sys.executable.replace("'", "''") + "'\n",
        encoding="utf-8-sig",
    )
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    event = json.dumps({"sessionId": "managed", "cwd": str(anchor), "toolName": "edit",
                        "toolArgs": {"path": str(anchor / "source.py")}})
    visible, foreground, observed, timings = set(), set(), set(), []
    for _ in range(2):
        started = time.monotonic()
        process = subprocess.Popen(
            [powershell, "-NoProfile", "-NonInteractive", "-Command",
             hooks["hooks"]["preToolUse"][0]["powershell"]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=anchor, env=env, **no_window_kwargs(),
        )
        try:
            process.stdin.write(event)
            process.stdin.close()
            process.stdin = None
            while process.poll() is None:
                if os.name == "nt":
                    from test_status_monitor_windows import (
                        _descendants, _foreground_state, _is_terminal_window,
                        _process_snapshot, _window_snapshot,
                    )

                    processes = _process_snapshot()
                    descendants = _descendants(process.pid, processes)
                    observed.update(pid for pid in descendants if pid != process.pid and
                                    "python" in processes.get(pid, ("", 0))[0].lower())
                    visible.update(hwnd for hwnd, window in _window_snapshot(processes).items()
                                   if window[0] in descendants)
                    active = _foreground_state(processes)
                    if active[1] in descendants and _is_terminal_window(active):
                        foreground.add(active[0])
                assert time.monotonic() - started < 15, "native hook exceeded its fixture deadline"
                time.sleep(0.01)
            stdout, stderr = process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        timings.append(round((time.monotonic() - started) * 1000, 2))
        assert process.returncode == 0, stderr
        assert json.loads(stdout).get("permissionDecision") == "deny", stdout + stderr
    if os.name == "nt":
        assert len(observed) >= 2, "both windowless cycles must observe actual Python descendants"
        assert not visible and not foreground
    record_testsuite_property(f"native_hook_cold_warm_ms:{shell}", json.dumps(timings))
    record_testsuite_property(f"visible_windows:{shell}", len(visible))
    record_testsuite_property(f"foreground_transitions:{shell}", len(foreground))


def test_extension_is_inert_until_adoption_and_stops_after_revocation(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    anchor, registry = fixture_project(tmp_path)
    (registry / "projects.yaml").write_text("projects: {}\n")
    payload = tmp_path / "payload"
    extensions = payload / "extensions" / "agent-worktrees"
    shutil.copytree(SCRIPTS.parent / "extensions" / "agent-worktrees", extensions)
    shutil.copytree(SCRIPTS, payload / "scripts")
    (payload / "plugin.json").write_text('{"name":"agent-worktrees"}')
    sdk = payload / "node_modules" / "@github" / "copilot-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({
        "type": "module", "exports": {".": "./index.mjs", "./extension": "./extension.mjs"},
    }))
    (sdk / "index.mjs").write_text("export const approveAll = () => {};\n")
    (sdk / "extension.mjs").write_text(
        "export const joinSession = async options => { globalThis.residentHooks = options.hooks; "
        "return { sessionId: 'fixture-session', "
        "on: (name, callback) => { globalThis.handlers[name] = callback; }, "
        "send: () => { throw new Error('unexpected nudge'); } }; };\n"
    )
    state = tmp_path / ".copilot" / "session-state" / "fixture-session"
    state.mkdir(parents=True)
    driver = tmp_path / "driver.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
globalThis.handlers = {};
const [extension, projects, sidecar] = process.argv.slice(2);
await import(pathToFileURL(extension));
const {createResidentPreToolHook} = await import(new URL("./resident-pre-tool.mjs", pathToFileURL(extension)));
let policies = 0;
const hook = createResidentPreToolHook({
  scriptsRoot: fileURLToPath(new URL("../../scripts/", pathToFileURL(extension))),
  invokePolicy: async () => { policies++; return {permissionDecision: "deny"}; },
});
const nested = process.env.COPILOT_PROJECT_DIR + "/src";
mkdirSync(nested);
const event = {sessionId: "fixture-session", workingDirectory: nested,
  toolName: "edit", toolArgs: {path: process.env.COPILOT_PROJECT_DIR + "/source.py"}};
async function pulse(text) {
  await handlers["assistant.intent"]({data: {intent: text}});
  await handlers["session.idle"]({});
}
await pulse("unmanaged");
assert.equal(existsSync(sidecar), false);
assert.deepEqual(await hook(event), {});
assert.deepEqual(await residentHooks.onPreToolUse(event), {});
assert.equal(policies, 0);
writeFileSync(projects, "projects:\\n  example: {}\\n");
const head = process.env.COPILOT_PROJECT_DIR + "/.git/HEAD";
const headText = readFileSync(head);
unlinkSync(head);
await pulse("incomplete");
assert.equal(existsSync(sidecar), false);
writeFileSync(head, headText);
await pulse("managed");
assert.equal(JSON.parse(readFileSync(sidecar)).intent, "managed");
assert.equal((await hook(event)).permissionDecision, "deny");
assert.equal((await residentHooks.onPreToolUse(event)).permissionDecision, "deny");
assert.equal(policies, 1);
for (const part of ["objects", "refs"]) mkdirSync(nested + "/.git/" + part, {recursive: true});
writeFileSync(nested + "/.git/HEAD", "ref: refs/heads/main\\n");
assert.deepEqual(await hook(event), {}, "a new nested repository invalidates membership");
assert.equal(policies, 1);
writeFileSync(projects, "projects: {}\\n");
await pulse("revoked");
assert.equal(JSON.parse(readFileSync(sidecar)).intent, "managed");
assert.deepEqual(await hook(event), {});
assert.deepEqual(await residentHooks.onPreToolUse(event), {});
assert.equal(policies, 1);
""")
    env = environment(tmp_path, anchor)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    env["AGENT_WORKTREES_RESIDENT_PRE_TOOL_PYTHON"] = sys.executable
    env["AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION"] = "resident"
    result = subprocess.run(
        [node, str(driver), str(extensions / "extension.mjs"),
         str(registry / "projects.yaml"), str(state / "substatus.json")],
        text=True, capture_output=True, timeout=15, cwd=anchor,
        env=env, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows venv redirector lifetime")
def test_admission_timeout_retires_the_actual_python_worker(tmp_path):
    from test_hook_policy_lifetime import _pin_descendant

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, scripts)
    marker = tmp_path / "worker.json"
    helper = scripts / "managed_session.py"
    source = helper.read_text()
    pause = (
        "    from hook_process_identity import process_start_time\n"
        "    import time\n"
        f"    marker = Path({str(marker)!r})\n"
        "    prepared = marker.with_suffix('.prepared')\n"
        "    prepared.write_text(json.dumps({'pid': os.getpid(), "
        "'birth': int(process_start_time(os.getpid()))}))\n"
        "    prepared.replace(marker)\n"
        "    time.sleep(60)\n"
    )
    assert source.count("        result = inspect(payload)\n") == 1
    helper.write_text(source.replace(
        "        result = inspect(payload)\n",
        "".join("    " + line + "\n" for line in pause.splitlines()) + "        result = inspect(payload)\n",
    ))
    driver = tmp_path / "timeout.mjs"
    driver.write_text("""
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, python, workingDirectory] = process.argv.slice(2);
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const check = createManagedSessionAdmission({scriptsRoot, python});
console.log(JSON.stringify(await check({sessionId: "timeout-fixture", workingDirectory})));
""")
    process = subprocess.Popen(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(scripts), sys.executable, str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=environment(tmp_path, tmp_path), **no_window_kwargs(),
    )
    close = None
    try:
        deadline = time.monotonic() + 10
        while not marker.is_file():
            assert process.poll() is None, process.communicate(timeout=5)
            assert time.monotonic() < deadline, "admission worker did not publish its native identity"
            time.sleep(0.01)
        worker = json.loads(marker.read_text())
        retired, close = _pin_descendant(worker["pid"], f"windows:{worker['birth']}")
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert json.loads(stdout) is False
        assert retired(), "actual admission interpreter survived its caller-owned timeout"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if close:
            close()


@pytest.mark.skipif(os.name != "nt", reason="Windows application execution aliases")
def test_extension_admission_never_launches_windowsapps_aliases(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    aliases = tmp_path / "WindowsApps"
    aliases.mkdir()
    (aliases / "python.exe").write_text("fixture alias must never execute")
    driver = tmp_path / "aliases.mjs"
    driver.write_text("""
import assert from "node:assert/strict";
import childProcess from "node:child_process";
import {syncBuiltinESMExports} from "node:module";
import {dirname, delimiter, join} from "node:path";
import {pathToFileURL} from "node:url";
const [module, scriptsRoot, aliases, python, workingDirectory] = process.argv.slice(2);
const original = childProcess.execFile;
const calls = [];
childProcess.execFile = (...args) => { calls.push(args[0]); return original(...args); };
syncBuiltinESMExports();
const {createManagedSessionAdmission} = await import(pathToFileURL(module));
const environment = {...process.env, PATH: aliases};
delete environment.Path;
const input = {sessionId: "alias-fixture", workingDirectory};
assert.equal(await createManagedSessionAdmission({scriptsRoot, environment})(input), false);
assert.equal(calls.length, 1, "read-only runtime discovery may run without PATH Python");
assert.match(calls[0], /powershell.exe$/i);
calls.length = 0;
environment.PATH += delimiter + dirname(python);
assert.equal(await createManagedSessionAdmission({
  scriptsRoot, environment, python: join(aliases, "python.exe"),
})(input), false);
assert.deepEqual(calls, []);
assert.equal(await createManagedSessionAdmission({scriptsRoot, environment})(input), false);
assert.deepEqual(calls, [python]);
""")
    result = subprocess.run(
        [node, str(driver), str(SCRIPTS.parent / "extensions" / "agent-worktrees" / "managed-session.mjs"),
         str(SCRIPTS), str(aliases), sys.executable, str(tmp_path)],
        capture_output=True, text=True, timeout=10,
        env=environment(tmp_path, tmp_path), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
