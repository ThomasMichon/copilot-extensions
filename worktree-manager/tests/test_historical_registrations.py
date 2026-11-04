"""Offline owner-admitted registration tests; never create host tasks/services."""
from __future__ import annotations

from contextlib import nullcontext
import copy
import importlib.util
import json
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from worktree_manager import historical_registrations as adapter

REPO = Path(__file__).resolve().parents[2]
OWNER_PATH = REPO / "plugins/agent-index/scripts/historical_registration_owner.py"
NATIVE_PATH = OWNER_PATH.with_name("historical-registration-owner.ps1")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def owner():
    return load(OWNER_PATH, "test_index_historical_owner")


def snapshot(owner, root, name="agent-index"):
    ns = "{" + owner.NS + "}"
    task = ET.Element(ns + "Task", {"version": "1.4"})
    def put(path, value):
        node = task
        for segment in path.split("/"):
            child = node.find(ns + segment)
            if child is None:
                child = ET.SubElement(node, ns + segment)
            node = child
        node.text = value
    values = {
        "RegistrationInfo/URI": "\\" + name,
        "Triggers/LogonTrigger/Enabled": "true",
        "Principals/Principal/UserId": "S-1-5-21-1234",
        "Principals/Principal/LogonType": "InteractiveToken",
        "Principals/Principal/RunLevel": "LeastPrivilege",
        "Settings/DisallowStartIfOnBatteries": "false",
        "Settings/StopIfGoingOnBatteries": "false",
        "Settings/StartWhenAvailable": "true",
        "Settings/ExecutionTimeLimit": "PT0S",
        "Settings/RestartOnFailure/Interval": "PT1M",
        "Settings/RestartOnFailure/Count": "3",
        "Actions/Exec/Command": "conhost.exe",
        "Actions/Exec/Arguments": '--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + str(PureWindowsPath(root) / owner.FAMILIES[name]) + '"',
    }
    for key, value in values.items():
        put(key, value)
    return {"name": name, "path": "\\" + name, "xml": ET.tostring(task, encoding="unicode"),
            "securityDescriptor": "O:S-1-5-21-1234", "sid": "S-1-5-21-1234",
            "user": "EXAMPLE\\operator", "state": 3}


@pytest.mark.parametrize("mutation", ["foreign-action", "foreign-principal", "extra-action", "extra-trigger", "extra-setting", "elevated", "name-only", "malformed"])
def test_archive_rejects_name_only_foreign_and_edited_tasks(owner, mutation):
    root = r"C:\Users\example\.agent-index"
    observed = snapshot(owner, root)
    assert owner.matches_archive(observed, root, "agent-index")
    replacements = {
        "foreign-action": ("conhost.exe", "other.exe"),
        "foreign-principal": ("S-1-5-21-1234", "S-1-5-21-foreign"),
        "extra-action": ("</ns0:Actions>", "<ns0:ComHandler/></ns0:Actions>"),
        "extra-trigger": ("</ns0:Triggers>", "<ns0:BootTrigger/></ns0:Triggers>"),
        "extra-setting": ("</ns0:Settings>", "<ns0:Unknown>true</ns0:Unknown></ns0:Settings>"),
        "elevated": ("LeastPrivilege", "HighestAvailable"),
        "name-only": (str(PureWindowsPath(root) / "service.ps1"), r"C:\foreign\service.ps1"),
    }
    if mutation == "malformed":
        observed["xml"] = "<!DOCTYPE Task><Task/>"
    elif mutation.startswith("extra-"):
        tree = ET.fromstring(observed["xml"])
        ns = "{" + owner.NS + "}"
        parent, child = {"extra-action": ("Actions", "ComHandler"),
                         "extra-trigger": ("Triggers", "BootTrigger"),
                         "extra-setting": ("Settings", "Unknown")}[mutation]
        ET.SubElement(tree.find(ns + parent), ns + child).text = "true"
        observed["xml"] = ET.tostring(tree, encoding="unicode")
    else:
        old, new = replacements[mutation]
        assert old in observed["xml"]
        observed["xml"] = observed["xml"].replace(old, new)
    assert not owner.matches_archive(observed, root, "agent-index")


def create_selected_cell(tmp_path, owner, pytestconfig, *,
                         launcher_paths=("service.ps1",),
                         launcher_content="explicitly adopted legacy Index launcher\n",
                         governance_profile=None):
    # The canonical fixture module defines this marker at import time. Register
    # its actual declaration here, not in unrelated global pytest configuration.
    if not any(value.split(":", 1)[0].strip() == "installation_context_smoke"
               for value in pytestconfig.getini("markers")):
        pytestconfig.addinivalue_line(
            "markers", "installation_context_smoke: canonical installation-context smoke checks",
        )
    fixtures = load(REPO / "libs/installation-context/tests/test_installation_mode_governance.py", "index_registration_governance_fixtures")
    fixtures.PLUGIN_ID = "agent-index"
    governance = fixtures._load_module()
    layout = fixtures._cell_layout(tmp_path)
    receipt = json.loads(layout["install"].read_text(encoding="utf-8"))
    receipt["payload"]["root"] = str(OWNER_PATH.parent.parent)
    layout["install"].write_text(json.dumps(receipt), encoding="utf-8")
    profile = tmp_path / "profile"
    profile.mkdir()
    root = profile / ".agent-index"
    root.mkdir()
    launcher = root / "service.ps1"
    paths = [root / Path(value.replace("\\", "/")) for value in launcher_paths]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(launcher_content, encoding="utf-8")
    selected_profile = governance_profile or profile
    environment = fixtures._profile_environment(selected_profile)
    adopted = governance.attribute_legacy_state(
        context=layout["install"], expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id="agent-index", legacy_root=root,
        legacy_items=[{"kind": "path", "identity": ".agent-index/" + path.relative_to(root).as_posix(),
                       "path": str(path)} for path in paths],
        legacy_lock=nullcontext(), durable_home=layout["durable"], environment={},
        os_profile=selected_profile, platform=environment["platform"], wsl_distro=environment["wslDistro"],
    )
    assert adopted["reason"] == "legacy-attributed"
    selection = {"context": str(layout["install"]), "marketplace_id": layout["marketplace_id"],
                 "namespace_generation": layout["namespace_generation"], "install_generation": layout["install_generation"],
                 "activation_generation": adopted["activationGeneration"], "durable_home": str(layout["durable"])}
    class Governance:
        @staticmethod
        def historical_registration_retirement_custody(**kwargs):
            return governance.historical_registration_retirement_custody(
                **kwargs, environment={}, os_profile=selected_profile,
                platform=environment["platform"], wsl_distro=environment["wslDistro"],
            )
    return root, selection, Governance, layout, governance, launcher


@pytest.fixture
def selected_cell(tmp_path, owner, pytestconfig):
    return create_selected_cell(tmp_path, owner, pytestconfig)


class NativeFixture:
    def __init__(self, observed):
        self.observed = observed
        self.decisions = []
        self.result = {"status": "applied", "reason": "selected-legacy-registration-retired"}
        self.before_decision = lambda: None
    def __call__(self, root, name):
        return self
    def __enter__(self):
        self.initial = {"stage": "observed", "snapshot": copy.deepcopy(self.observed)}
        return self
    def __exit__(self, *exc):
        return False
    def decide(self, action, fingerprint=None):
        self.before_decision()
        self.decisions.append(action)
        return self.result


def test_selected_adopted_retained_inert_registration_can_retire(owner, selected_cell):
    root, selection, governance, *_ = selected_cell
    native = NativeFixture(snapshot(owner, root))
    finding = owner.operate(root, selection, "agent-index", governance=governance, native_factory=native)
    assert finding["status"] == "candidate"
    assert native.decisions == ["preserve"]
    result = owner.operate(root, selection, "agent-index", finding, governance=governance, native_factory=native)
    assert result["status"] == "applied"
    assert native.decisions == ["preserve", "remove"]


@pytest.mark.parametrize("mutation", ["current", "foreign-cell", "changed-generation", "missing-adoption", "unadopted-launcher", "running", "queued"])
def test_owner_preserves_current_foreign_or_unsettled_registration(owner, selected_cell, mutation):
    root, selection, governance, layout, _, launcher = selected_cell
    native = NativeFixture(snapshot(owner, root))
    activation = Path(layout["plugin_root"]) / "installation-activation.json"
    tombstone = root / ".installation-ownership.json"
    if mutation == "current":
        value = json.loads(activation.read_text(encoding="utf-8"))
        value["mode"] = "legacy"
        activation.write_text(json.dumps(value), encoding="utf-8")
    elif mutation == "foreign-cell":
        selection["marketplace_id"] = "foreign"
    elif mutation == "changed-generation":
        selection["activation_generation"] += 1
    elif mutation == "missing-adoption":
        tombstone.unlink()
    elif mutation == "unadopted-launcher":
        value = json.loads(tombstone.read_text(encoding="utf-8"))
        value["attribution"]["items"] = []
        tombstone.write_text(json.dumps(value), encoding="utf-8")
    else:
        native.observed["state"] = 4 if mutation == "running" else 2
    result = owner.operate(root, selection, "agent-index", governance=governance, native_factory=native)
    assert result["status"] == "preserved"
    assert "remove" not in native.decisions


@pytest.mark.parametrize("mutation", ["task", "receipt", "launcher"])
def test_scan_to_apply_replacement_never_retires(owner, selected_cell, mutation):
    root, selection, governance, layout, _, launcher = selected_cell
    native = NativeFixture(snapshot(owner, root))
    finding = owner.operate(root, selection, "agent-index", governance=governance, native_factory=native)
    assert finding["status"] == "candidate"
    if mutation == "task":
        native.observed["securityDescriptor"] += "D:"
    elif mutation == "receipt":
        path = Path(layout["plugin_root"]) / "installation-activation.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    else:
        launcher.write_text("edited launcher\n", encoding="utf-8")
    result = owner.operate(root, selection, "agent-index", finding, governance=governance, native_factory=native)
    assert result["status"] == "preserved"
    assert "remove" not in native.decisions


def test_selected_receipts_revalidated_inside_live_custody(owner, selected_cell):
    root, selection, governance, layout, _, _ = selected_cell
    with governance.historical_registration_retirement_custody(
        context=selection["context"], expected_marketplace_id=selection["marketplace_id"],
        expected_plugin_id="agent-index", expected_namespace_generation=selection["namespace_generation"],
        expected_install_generation=selection["install_generation"], expected_activation_generation=selection["activation_generation"],
        legacy_root=root, required_paths=[root / "service.ps1"], durable_home=selection["durable_home"],
        payload_root=OWNER_PATH.parent.parent,
    ) as custody:
        path = Path(layout["plugin_root"]) / "installation-activation.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="changed"):
            custody["assert_current"]()


@pytest.mark.parametrize("mutation", ["unchanged", "xml", "security", "running", "queued"])
def test_actual_native_owner_revalidates_before_task_deletion(tmp_path, mutation):
    host = shutil.which("pwsh")
    if not host:
        pytest.skip("PowerShell required for contained native caller contract")
    # The actual native caller executes against a contained in-memory Scheduler
    # double. No COM connection, scheduled-task creation, UAC, or host scan.
    script = r"""
$ErrorActionPreference = 'Stop'
. $args[0]
$script:removed = 0
$script:snapshot = @{name='agent-index';path='\agent-index';xml='<original/>';securityDescriptor='owner';sid='S-1-5-21-1234';state=3}
function Enter-IndexStampLock {
    param($Scope, $LockRoot, [switch]$RequireGlobal)
    if ($Scope -cne 'Transaction' -or -not $RequireGlobal) { throw 'Wrong native serialization owner' }
    $mutex = [PSCustomObject]@{}
    $mutex | Add-Member ScriptMethod ReleaseMutex {}
    $mutex | Add-Member ScriptMethod Dispose {}
    return $mutex
}
function Get-IndexHistoricalTaskSnapshot { param($Name); return $script:snapshot }
function Remove-IndexHistoricalTask { param($Name); $script:removed++; $script:snapshot=$null }
function Invoke-IndexHistoricalTaskRetirement {
    param($Name, $Expected)
    Remove-IndexHistoricalTask -Name $Name
    return @{status='applied'; reason='selected-legacy-registration-retired'}
}
$mutation = $args[1]
$result = Invoke-IndexHistoricalRegistration -Request @{name='agent-index';legacyRoot=$args[2]} -Admit {
    param($observed)
    $fingerprint = Get-IndexHistoricalTaskFingerprint $observed
    switch ($mutation) {
        'xml' { $script:snapshot.xml='<foreign/>' }
        'security' { $script:snapshot.securityDescriptor='foreign-owner' }
        'running' { $script:snapshot.state=4 }
        'queued' { $script:snapshot.state=2 }
    }
    return @{action='remove';sha256=$fingerprint}
}
@{status=$result.status;removed=$script:removed} | ConvertTo-Json -Compress
"""
    wrapper = tmp_path / "contained-registration-contract.ps1"
    wrapper.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(wrapper), str(NATIVE_PATH), mutation, str(tmp_path)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed == ({"status": "applied", "removed": 1} if mutation == "unchanged"
                        else {"status": "preserved", "removed": 0})


def test_adapter_rejects_unsupported_or_preserved_selection_without_loading_owner(monkeypatch):
    monkeypatch.setattr(adapter, "_owner", lambda *_: pytest.fail("must not load a native owner"))
    result = adapter.apply(Path.cwd(), {}, {"resource": "unrelated", "status": "candidate"})
    assert result["status"] == "preserved"
    assert result["reason"] == "unsupported-registration-selection"


def test_old_owner_without_native_custody_never_probes_or_mutates(owner):
    def forbidden(*args):
        pytest.fail("missing retirement owner must not probe native Scheduler")
    result = owner.operate(Path.cwd(), {}, "agent-index", governance=object(), native_factory=forbidden)
    assert result["status"] == "preserved"
    assert result["reason"] == "Index installation-context retirement owner is unavailable"

@pytest.mark.parametrize("missing", [True, False])
def test_native_snapshot_walks_wrapped_missing_task_hresult(tmp_path, missing):
    host = shutil.which("pwsh")
    if not host:
        pytest.skip("PowerShell required for contained native caller contract")
    script = r"""
$ErrorActionPreference = 'Stop'
. $args[0]
$code = if ($args[1] -ceq 'True') { -2147024894 } else { -2147024891 }
$script:nativeError = [Runtime.InteropServices.COMException]::new('native-task-error', $code)
$script:folder = [PSCustomObject]@{}
$script:folder | Add-Member ScriptMethod GetTask {
    param($Name)
    throw [InvalidOperationException]::new('wrapped-task-error', $script:nativeError)
}
$script:scheduler = [PSCustomObject]@{}
$script:scheduler | Add-Member ScriptMethod Connect {}
$script:scheduler | Add-Member ScriptMethod GetFolder { param($Path); return $script:folder }
function New-Object {
    param($ComObject)
    if ($ComObject -cne 'Schedule.Service') { throw 'Unexpected COM target' }
    return $script:scheduler
}
try {
    $snapshot = Get-IndexHistoricalTaskSnapshot -Name 'agent-index'
    @{absent=($null -eq $snapshot); failed=$false} | ConvertTo-Json -Compress
} catch {
    @{absent=$false; failed=$true; reason=$_.Exception.Message} | ConvertTo-Json -Compress
}
"""
    wrapper = tmp_path / "contained-missing-task.ps1"
    wrapper.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(wrapper), str(NATIVE_PATH), str(missing)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    if missing:
        assert observed == {"absent": True, "failed": False}
    else:
        assert observed["failed"] is True and observed["absent"] is False
        assert "wrapped-task-error" in observed["reason"]


# Literal OS-exported defaults, independent of the current writer and XML builder.
NATIVE_EXPORTED_XML = r"""<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><URI>\agent-index</URI></RegistrationInfo>
  <Principals><Principal id="Author"><UserId>S-1-5-21-1234</UserId><LogonType>InteractiveToken</LogonType></Principal></Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure><Count>3</Count><Interval>PT1M</Interval></RestartOnFailure><StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings><Duration>PT10M</Duration><WaitTimeout>PT1H</WaitTimeout><StopOnIdleEnd>true</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers><LogonTrigger /></Triggers>
  <Actions Context="Author"><Exec><Command>conhost.exe</Command><Arguments>--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\example\.agent-index\service.ps1"</Arguments></Exec></Actions>
</Task>"""


@pytest.mark.parametrize("name,launcher", [("agent-index", "service.ps1"), ("agent-index-engine", r"engine\engine.ps1")])
def test_archive_accepts_literal_native_export_with_implicit_defaults(owner, name, launcher):
    root = r"C:\Users\example\.agent-index"
    observed = snapshot(owner, root, name)
    observed["xml"] = NATIVE_EXPORTED_XML.replace("\\agent-index</URI>", "\\" + name + "</URI>").replace("\\service.ps1", "\\" + launcher)
    assert owner.matches_archive(observed, root, name)


@pytest.mark.parametrize("old,new", [
    ("<LogonTrigger />", "<LogonTrigger><Enabled>false</Enabled></LogonTrigger>"),
    ("</LogonType>", "</LogonType><RunLevel>HighestAvailable</RunLevel>"),
    ("PT10M", "PT20M"), ("PT1H", "PT2H"),
    ("<LogonTrigger />", "<BootTrigger />"),
    ("<UseUnifiedSchedulingEngine>true", "<UseUnifiedSchedulingEngine>unknown"),
])
def test_native_export_defaults_do_not_admit_changed_or_unknown_shape(owner, old, new):
    root = r"C:\Users\example\.agent-index"
    observed = snapshot(owner, root)
    observed["xml"] = NATIVE_EXPORTED_XML.replace(old, new)
    assert not owner.matches_archive(observed, root, "agent-index")


@pytest.mark.parametrize("race", ["launch-at-delete", "instance-at-disable", "delete-error", "foreign-edit", "already-disabled"])
def test_native_retirement_quiesces_scheduler_and_restores_on_no_delete(tmp_path, race):
    host = shutil.which("pwsh")
    if not host:
        pytest.skip("PowerShell required for contained scheduling-admission contract")
    script = r"""
$ErrorActionPreference = 'Stop'
. $args[0]
$script:race=$args[1]
$script:removed=$false; $script:blocked=$false; $script:count=0
$script:enabled=($script:race -cne 'already-disabled')
$script:xml='<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Settings/></Task>'
$script:task=[PSCustomObject]@{}
$script:task | Add-Member ScriptProperty Enabled { $script:enabled } {
    param($value)
    if (-not $value -and $script:race -ceq 'instance-at-disable') { $script:count=1 }
    if (-not $value -and $script:race -ceq 'foreign-edit') { $script:xml=$script:xml.Replace('<Settings/>','<Settings><Foreign/></Settings>') }
    $script:enabled=[bool]$value
}
$script:task | Add-Member ScriptMethod Run {
    param($Parameters)
    if (-not $script:enabled) { $script:blocked=$true; throw 'TASK_DISABLED' }
    $script:count++
}
$script:task | Add-Member ScriptMethod GetInstances { param($Flags); return @{Count=$script:count} }
$script:folder=[PSCustomObject]@{}
$script:folder | Add-Member ScriptMethod GetTask { param($Name); return $script:task }
$script:scheduler=[PSCustomObject]@{}
$script:scheduler | Add-Member ScriptMethod Connect {}
$script:scheduler | Add-Member ScriptMethod GetFolder { param($Path); return $script:folder }
function New-Object { param($ComObject); return $script:scheduler }
function Get-IndexHistoricalTaskSnapshot {
    param($Name)
    if ($script:removed) { return $null }
    $state=if ($script:enabled) { 3 } else { 1 }
    return @{name=$Name; path=('\'+$Name); xml=$script:xml; securityDescriptor='owner';
             sid='S-1-5-21-1234';state=$state;enabled=$script:enabled;instances=$script:count}
}
function Remove-IndexHistoricalTask {
    param($Name)
    if ($script:race -ceq 'launch-at-delete') {
        try { $null=$script:task.Run($null) } catch { if (-not $script:blocked) { throw } }
    }
    if ($script:race -ceq 'delete-error') { throw 'native-delete-error' }
    if ($script:enabled -or $script:count) { throw 'deleted-with-open-scheduling-or-instances' }
    $script:removed=$true
}
try {
    function Enter-IndexStampLock {
        param($Scope, $LockRoot, [switch]$RequireGlobal)
        $mutex=[PSCustomObject]@{}
        $mutex | Add-Member ScriptMethod ReleaseMutex {}
        $mutex | Add-Member ScriptMethod Dispose {}
        return $mutex
    }
    $result=Invoke-IndexHistoricalRegistration -Request @{name='agent-index';legacyRoot=$args[2]} -Admit {
        param($observed)
        return @{action='remove';sha256=(Get-IndexHistoricalTaskFingerprint $observed)}
    }
} catch { $result=@{status='failed';reason=$_.Exception.Message} }
@{status=$result.status;reason=$result.reason;removed=$script:removed;enabled=$script:enabled;blocked=$script:blocked} | ConvertTo-Json -Compress
"""
    wrapper = tmp_path / "contained-scheduling-admission.ps1"
    wrapper.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(wrapper), str(NATIVE_PATH), race, str(tmp_path)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    if race in {"launch-at-delete", "already-disabled"}:
        assert actual["status"] == "applied", actual
        assert actual["removed"] is True and actual["enabled"] is False
        assert actual["blocked"] is (race == "launch-at-delete")
    elif race == "instance-at-disable":
        assert actual["status"] == "preserved", actual
        assert actual["removed"] is False and actual["enabled"] is True
    else:
        assert actual["status"] == "failed", actual
        assert actual["removed"] is False
        assert actual["enabled"] is (race == "delete-error")
        assert ("restoration-unconfirmed" if race == "foreign-edit" else "native-delete-error") in actual["reason"]


@pytest.mark.parametrize("field,metadata,message", [
    ("items", None, "items must be a JSON array"),
    ("items", {}, "items must be a JSON array"),
    ("items", "", "items must be a JSON array"),
    ("legacyRoot", None, "adoption root is unproven"),
    ("legacyRoot", [], "adoption root is unproven"),
    ("legacyRoot", 42, "adoption root is unproven"),
])
def test_malformed_adoption_metadata_preserves_registration_without_native_effect(owner, selected_cell, field, metadata, message):
    root, selection, governance, *_ = selected_cell
    native = NativeFixture(snapshot(owner, root))
    finding = owner.operate(root, selection, "agent-index", governance=governance, native_factory=native)
    assert finding["status"] == "candidate"
    tombstone = root / ".installation-ownership.json"
    value = json.loads(tombstone.read_text(encoding="utf-8"))
    value["attribution"][field] = metadata
    tombstone.write_text(json.dumps(value), encoding="utf-8")
    for observed in (None, finding):
        held = owner.operate(root, selection, "agent-index", observed, governance=governance, native_factory=native)
        assert held["status"] == "preserved", held
        assert message in held["reason"]
    assert "remove" not in native.decisions
