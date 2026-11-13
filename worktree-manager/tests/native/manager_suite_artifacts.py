"""Fixture-only artifact materialization, delivery and default-HOME setup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
from typing import Any


def _write(path: Path, value: Any) -> None:
    pending = path.with_name(path.name + ".fixture-pending")
    pending.write_text(json.dumps(value, indent=2), encoding="utf-8")
    pending.replace(path)


def _task_inventory_script(home: Path) -> str:
    quoted = str(home).replace("'", "''")
    return (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        f"$caseHome='{quoted}'; $rows=@("
        "Get-ScheduledTask | ForEach-Object {"
        "$xml=Export-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath;"
        "if($xml.IndexOf($caseHome,[StringComparison]::OrdinalIgnoreCase) -ge 0){"
        "[pscustomobject]@{name=$_.TaskName;path=$_.TaskPath;state=[string]$_.State;xml=$xml}}}"
        "); ConvertTo-Json -InputObject $rows -Depth 4 -Compress"
    )


def _runtime_process_inventory_script(slots: dict[str, str]) -> str:
    document = json.dumps(slots).replace("'", "''")
    return (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        f"$slots=ConvertFrom-Json '{document}'; $rows=@("
        "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%.exe'\" | ForEach-Object {"
        "$process=$_; $line=[string]$process.CommandLine;"
        "foreach($slot in $slots.PSObject.Properties){"
        "if($line.IndexOf([string]$slot.Value,[StringComparison]::OrdinalIgnoreCase) -ge 0){"
        "[pscustomobject]@{component=$slot.Name;pid=$process.ProcessId;"
        "parent_pid=$process.ParentProcessId;"
        "cache_prefix=($line -match '(?:^|\\s)-X\\s+\"?pycache_prefix=');"
        "no_bytecode=($line -match '(?:^|\\s)-B(?:\\s|$)');"
        "module=if($line -match '(?:^|\\s)-m\\s+([a-zA-Z_][a-zA-Z0-9_.]*)')"
        "{$Matches[1]}else{$null}}}}}"
        "); ConvertTo-Json -InputObject $rows -Depth 4 -Compress"
    )


def _runtime_process_trace_script(home: Path, ready: Path, stop: Path, report: Path) -> str:
    quoted = [str(path).replace("'", "''") for path in (home, ready, stop, report)]
    window = str(home.parent / "process-trace-window").replace("'", "''")
    return (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        f"$homePrefix='{quoted[0]}\\';$readyPath='{quoted[1]}';"
        f"$stopPath='{quoted[2]}';$reportPath='{quoted[3]}';"
        f"$windowPath='{window}';$windowArmed=$false;"
        "$seen=[Collections.Generic.HashSet[string]]::new();"
        "$rows=[Collections.Generic.List[object]]::new();$scanErrors=0;$truncated=$false;"
        "$scanFailures=[Collections.Generic.List[object]]::new();"
        "$cimSession=$null;$cimSessionClosed=$null;"
        "$scanAttempts=0;$scanSuccesses=0;$scanTotalMs=0L;$scanMaxMs=0L;"
        "$queryAttempts=0;$queryTotalMs=0L;$queryMaxMs=0L;$sessionCreateMs=$null;"
        "function Get-CensusFailure($failure,$phase,$elapsed,$records){"
        "$exception=$failure.Exception;"
        "$isCim=$exception -is [Microsoft.Management.Infrastructure.CimException];"
        "$numericData=@{};$dataStatus='absent';"
        "if($isCim){try{$errorData=$exception.ErrorData;if($null -ne $errorData){"
        "$dataStatus='captured';"
        "foreach($name in @('CIMStatusCode','ErrorType','PerceivedSeverity',"
        "'ProbableCause','ErrorSourceFormat','error_Code','error_Category')){"
        "$property=$errorData.CimInstanceProperties[$name];"
        "if($null -ne $property -and $property.CimType -in @("
        "[Microsoft.Management.Infrastructure.CimType]::UInt16,"
        "[Microsoft.Management.Infrastructure.CimType]::UInt32) -and "
        "$null -ne $property.Value){$numericData[$name]=[uint32]$property.Value}}}"
        "}catch{$dataStatus='unavailable'}};"
        "$innerHresults=[Collections.Generic.List[int]]::new();"
        "$inner=$exception.InnerException;"
        "while($null -ne $inner -and $innerHresults.Count -lt 4){"
        "$innerHresults.Add($inner.HResult);$inner=$inner.InnerException};"
        "$embeddedHresult=$null;"
        "if($isCim -and $exception.Message -match '(?i)\\b0x(8[0-9a-f]{7})\\b'){"
        "$embeddedHresult=[Convert]::ToUInt32($Matches[1],16)};"
        "$classification=if($isCim){'cim-error'}else{'non-cim-error'};"
        "if($embeddedHresult -in @(0x800705B4L,0x8001011FL,0x80041069L,0x80338126L))"
        "{$classification='timeout'}"
        "elseif($embeddedHresult -in @(0x80070005L,0x80041003L))"
        "{$classification='access-denied'};"
        "[pscustomobject]@{phase=$phase;classification=$classification;"
        "type=$exception.GetType().FullName;hresult=$exception.HResult;"
        "embedded_hresult=$embeddedHresult;"
        "numeric_error_data=$numericData;error_data_status=$dataStatus;"
        "inner_hresults=@($innerHresults.ToArray());inner_chain_truncated=($null -ne $inner);"
        "error_category=[string]$failure.CategoryInfo.Category;"
        "elapsed_ms=$elapsed;records_at_failure=$records;"
        "native_error_code=if($isCim){[int]$exception.NativeErrorCode}else{$null};"
        "status_code=if($isCim){[uint32]$exception.StatusCode}else{$null}}};"
        "$deadline=[DateTime]::UtcNow.AddSeconds(3300);"
        "[IO.File]::WriteAllText($readyPath,'ready');"
        "try{while(-not [IO.File]::Exists($stopPath) -and [DateTime]::UtcNow -lt $deadline){"
        "if(-not $windowArmed){"
        "if([IO.File]::Exists($windowPath)){$windowArmed=$true}"
        "else{Start-Sleep -Milliseconds 100;continue}};"
        "$scanAttempts++;$phase='session-options';$queryStarted=$null;"
        "$scanStarted=[Diagnostics.Stopwatch]::StartNew();try{"
        "if($null -eq $cimSession){"
        "$cimOptions=New-CimSessionOption -Protocol Dcom;"
        "$phase='session-create';"
        "$cimSession=New-CimSession -SessionOption $cimOptions -OperationTimeoutSec 1;"
        "if($null -eq $cimSession){throw 'Local census session was not created'};"
        "$sessionCreateMs=$scanStarted.ElapsedMilliseconds};"
        "$phase='query-pipeline';$queryAttempts++;"
        "$queryStarted=[Diagnostics.Stopwatch]::StartNew();"
        "Get-CimInstance Win32_Process -CimSession $cimSession -Filter \"Name LIKE 'python%.exe'\" "
        "-Property ProcessId,ParentProcessId,CreationDate,CommandLine "
        "-OperationTimeoutSec 5 | ForEach-Object {"
        "$process=$_;$line=[string]$process.CommandLine;"
        "$start=$line.IndexOf($homePrefix,[StringComparison]::OrdinalIgnoreCase);"
        "if($start -lt 0){return};"
        "$tail=$line.Substring($start+$homePrefix.Length);"
        "if($tail -notmatch '^\\.(agent-[a-z-]+|worktree-manager)\\\\'){return};"
        "$component=$Matches[1];"
        "$birth=if($process.CreationDate){$process.CreationDate.ToUniversalTime().Ticks}else{$null};"
        "$key=[string]$process.ProcessId+':'+[string]$birth;"
        "if($seen.Contains($key)){return};"
        "if($rows.Count -ge 512){$truncated=$true;return};"
        "[void]$seen.Add($key);"
        "$module=if($line -match '(?:^|\\s)-m\\s+([a-zA-Z_][a-zA-Z0-9_.]*)')"
        "{$Matches[1]}else{$null};"
        "$rows.Add([pscustomobject]@{component=$component;pid=$process.ProcessId;"
        "parent_pid=$process.ParentProcessId;created_ticks=$birth;"
        "observed_ticks=[DateTime]::UtcNow.Ticks;"
        "cache_prefix=($line -match '(?:^|\\s)-X\\s+\"?pycache_prefix=');"
        "no_bytecode=($line -match '(?:^|\\s)-B(?:\\s|$)');module=$module})"
        "};$scanSuccesses++}catch{$scanErrors++;if($scanFailures.Count -lt 8){"
        "$scanFailures.Add((Get-CensusFailure $_ $phase "
        "$scanStarted.ElapsedMilliseconds $rows.Count))}}finally{"
        "$elapsed=$scanStarted.ElapsedMilliseconds;$scanTotalMs+=$elapsed;"
        "$scanMaxMs=[Math]::Max($scanMaxMs,$elapsed);"
        "if($null -ne $queryStarted){$queryMs=$queryStarted.ElapsedMilliseconds;"
        "$queryTotalMs+=$queryMs;$queryMaxMs=[Math]::Max($queryMaxMs,$queryMs)}};"
        "Start-Sleep -Milliseconds 100}}finally{if($null -ne $cimSession){"
        "$closeStarted=[Diagnostics.Stopwatch]::StartNew();"
        "try{Remove-CimSession -CimSession $cimSession;$cimSessionClosed=$true}"
        "catch{$scanErrors++;$cimSessionClosed=$false;if($scanFailures.Count -lt 8){"
        "$scanFailures.Add((Get-CensusFailure $_ 'session-close' "
        "$closeStarted.ElapsedMilliseconds $rows.Count))}}}};"
        "$result=@{schema='worktree-manager.native-process-trace.v1';"
        "records=@($rows.ToArray());scan_errors=$scanErrors;truncated=$truncated;"
        "scan_failures=@($scanFailures.ToArray());"
        "timing=@{scan_attempts=$scanAttempts;scan_successes=$scanSuccesses;"
        "scan_total_ms=$scanTotalMs;scan_max_ms=$scanMaxMs;"
        "query_pipeline_attempts=$queryAttempts;query_pipeline_total_ms=$queryTotalMs;"
        "query_pipeline_max_ms=$queryMaxMs;session_create_ms=$sessionCreateMs};"
        "cim_session_created=($null -ne $cimSession);cim_session_closed=$cimSessionClosed;"
        "stop_observed=[IO.File]::Exists($stopPath);window_armed=$windowArmed};"
        "[IO.File]::WriteAllText($reportPath,"
        "(ConvertTo-Json -InputObject $result -Depth 4),[Text.UTF8Encoding]::new($false));"
        "if(-not $result.stop_observed -or $scanErrors -gt 0){exit 1}"
    )


def _env(case: Path) -> dict[str, str]:
    # Keep Windows/toolchain discovery, but never inherit a host installation,
    # credential, profile/cell, pytest task suppression or per-runtime root.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(
        ("AGENT_", "WORKTREE_", "COPILOT_", "CE_UPDATE_", "GIT_", "UV_", "PYTEST_"),
    ) and key.upper() not in {
        "GH_TOKEN", "GITHUB_TOKEN", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    }}
    home = case / "h"
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "TEMP": str(home / "scratch"), "TMP": str(home / "scratch"),
        "TMPDIR": str(home / "scratch"), "PYTHONUTF8": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        "UV_PYTHON_DOWNLOADS": "never", "CE_UPDATE_AUTH": "on",
        "SUITE_FIXTURE": str(case),
        "PATH": str(case / "tools") + os.pathsep + os.environ["PATH"],
    })
    for key in ("HOME", "APPDATA", "LOCALAPPDATA", "TEMP"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


_MATERIALIZE = """
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'tools'))
import accumulate_bumps as a
import materialize_main as m
names = [p.parent.name for p in (root / 'plugins').glob('*/plugin.json')
         if __import__('json').loads(p.read_text()) .get('runtimeScope', 'none') != 'none']
names.append('worktree-manager')
changes = {}
for name in names:
    old = a.read_consumer_version(name)
    new = old
    for _ in range(int(sys.argv[2])):
        new = a.bump_version(new, 'dev')
    changes[name] = (old, new)
assert set(a.apply(changes)) == set(names)
log = m.materialize(root, canonical_root=root)
assert not any(line.startswith('SKIP ') for line in log), log
"""

_DELIVERY = r"""
import json, os, shutil, sys
from pathlib import Path
case = Path(os.environ['SUITE_FIXTURE'])
kind, *args = sys.argv[1:]
if kind == 'gh':
    if args[:2] == ['auth', 'status']:
        raise SystemExit(0)
    if args[:1] == ['api']:
        print(json.dumps({'login': 'fixture', 'private': False, 'visibility': 'public'}))
        raise SystemExit(0)
elif kind == 'copilot':
    source = case / 'published'
    roster = {entry['name']: entry['source'] for entry in
              json.loads((source / '.github' / 'plugin' / 'marketplace.json').read_text())['plugins']}
    if args[:2] in (['plugin', 'update'], ['plugin', 'install']):
        name = args[2].split('@')[0]
        assert name in roster, ('unexpected acquisition', args)
        origin = (source / roster[name]).resolve()
        assert origin.is_relative_to(source / 'plugins'), ('unexpected payload source', name)
        target = Path.home() / '.copilot' / 'installed-plugins' / 'copilot-extensions' / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(origin, target)
        config = Path.home() / '.copilot' / 'config.json'
        value = json.loads(config.read_text())
        version = json.loads((origin / 'plugin.json').read_text())['version']
        for item in value['installedPlugins']:
            if item['name'] == name:
                item['version'] = version
                break
        else:
            value['installedPlugins'].append({
                'name': name, 'marketplace': 'copilot-extensions', 'version': version, 'enabled': True,
            })
        config.write_text(json.dumps(value))
        print('Controlled published artifact delivered')
        raise SystemExit(0)
    if args[:3] == ['plugin', 'marketplace', 'browse']:
        print('\n'.join(roster))
        raise SystemExit(0)
    if args[:3] in (['plugin', 'marketplace', 'update'], ['plugin', 'marketplace', 'add']):
        raise SystemExit(0)
raise SystemExit('Unexpected external delivery request: ' + repr((kind, args)))
"""


def _configuration(case: Path, declared: dict[str, Any]) -> None:
    home, project = case / "h", case / "project"
    machine = socket.gethostname().split(".")[0].strip().lower()
    state, config = home / ".agent-worktrees", home / ".example"
    state.mkdir(parents=True, exist_ok=True)
    config.mkdir()
    _write(config / "config.yaml", {
        "repo_name": "example", "machine": machine, "terminal_profiles": [],
        "repos": {"example": {"anchor": str(project), "default_branch": "main",
                             "pr": {"enabled": False}}},
    })
    _write(state / "projects.yaml", {"schema_version": 2, "projects": {"example": {
        "config_dir": str(config), "display_name": "Example",
    }}})
    _write(state / "repos.yaml", {"schema_version": 1, "repos": {"example": {
        "class": "worktree", "remote": str(project), "windows": str(project),
    }}})
    # Explicit native host role: do not silently turn the heavy Index producer
    # into a client-only or dependency-suppressed component fixture.
    index = home / ".agent-index"
    index.mkdir()
    (index / "config.yaml").write_text("role: host\n", encoding="utf-8")
    index_repo = project / ".copilot-extensions" / "agent-index" / "config.yaml"
    index_repo.parent.mkdir(parents=True)
    index_repo.write_text(f"indexers:\n  - machine: {machine}\n", encoding="utf-8")
    if "agent-vault" in declared:
        _write(project / ".agent-vault.json", {"enabled": True})
    if "agent-codespaces" in declared:
        codespaces = home / ".agent-codespaces"
        codespaces.mkdir()
        _write(codespaces / "config.yaml", {"windows_scheduled_task": "enabled"})
    _write(project / "services.yaml", {"plugins": [
        {"name": name, "deploy_machines": [machine]}
        for name, row in declared.items() if row["scope"] == "machine-gated"
    ]})
    settings = {
        "enabledPlugins": {name + "@copilot-extensions": True for name in declared},
        "extraKnownMarketplaces": {"copilot-extensions": {"source": {
            "source": "github", "repo": "ThomasMichon/copilot-extensions",
        }}},
    }
    project_settings = project / ".github" / "copilot" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    _write(project_settings, settings)
    copilot = home / ".copilot"
    copilot.mkdir()
    _write(copilot / "settings.json", settings)
    _write(copilot / "config.json", {"installedPlugins": [
        {"name": name, "marketplace": "copilot-extensions", "version": row["version"],
         "enabled": True} for name, row in declared.items()
    ]})
    for name in declared:
        shutil.copytree(case / "published" / "plugins" / name,
                        copilot / "installed-plugins" / "copilot-extensions" / name)
    terminal = home / "AppData" / "Local" / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState"
    terminal.mkdir(parents=True)
    _write(terminal / "settings.json", {"profiles": {"list": []}})
    _write(terminal / "state.json", {"generatedProfiles": []})
    manager = home / ".worktree-manager"
    manager.mkdir()
    (manager / "config.toml").write_text(
        f"[source]\nrepo={json.dumps(str(case / 'published'))}\nref=\"main\"\n"
        "[maintenance]\nautomatic=false\n", encoding="utf-8",
    )


def _command(case: Path, name: str, *args: str, extension: str = "cmd") -> list[str]:
    entry = case / "h" / ".local" / "bin" / (name + ("." + extension if extension else ""))
    if not entry.is_file():
        raise RuntimeError(f"{name}: generated global command recognizer missing: {entry}")
    if extension == "ps1":
        return [str(shutil.which("pwsh")), "-NoProfile", "-File", str(entry), *args]
    if not extension:
        bash = shutil.which("bash")
        if bash is None:
            raise RuntimeError(f"{name}: native shell frontdoor requires bash")
        return [bash, str(entry), *args]
    return [os.environ["COMSPEC"], "/d", "/c", str(entry), *args]
