"""Finite Index-owned historical task retirement, independent of install profiles.

Archive: agent-index native New-TaskSpec/Register-UserModeTask at source
51aa74b2b6, scripts/install.ps1:2295-2356; legacyFootprint tasks at
payload-invocation.json:41-51. These are producer evidence, not obsolescence.
Only explicit selected-cell adoption with retained-inert disposition admits
retirement. Global task names alone never grant ownership.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import queue
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET

DETECTOR = "index-adopted-legacy-tasks-v1"
REVISION = 1
FAMILIES = {"agent-index": "service.ps1", "agent-index-engine": r"engine\engine.ps1"}
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
MAX_NATIVE_BYTES = 512 * 1024


def native_fingerprint(snapshot):
    value = "\n".join(str(snapshot[key]) for key in ("xml", "securityDescriptor", "sid", "state"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def matches_archive(snapshot, legacy_root, name):
    """Strict finite XML codec; unknown nodes, duplicate actions and edits hold."""
    if name not in FAMILIES or snapshot.get("name") != name or snapshot.get("path") != "\\" + name:
        return False
    sid, user = snapshot.get("sid"), snapshot.get("user")
    if not isinstance(sid, str) or not sid.startswith("S-1-") or not isinstance(user, str) or not user:
        return False
    raw = snapshot.get("xml")
    if not isinstance(raw, str) or len(raw) > MAX_NATIVE_BYTES or "<!" in raw:
        return False
    try:
        task = ET.fromstring(raw)
    except ET.ParseError:
        return False
    if (task.text or "").strip():
        return False
    if task.tag != "{" + NS + "}Task" or set(task.attrib) != {"version"} or task.attrib["version"] not in {"1.2", "1.3", "1.4"}:
        return False
    leaves = {}
    def walk(node, prefix):
        if not node.tag.startswith("{" + NS + "}") or (node.tail or "").strip():
            raise ValueError("Foreign task XML")
        key = node.tag.split("}", 1)[1]
        path = prefix + key
        attributes = {"Principals/Principal": {"id": "Author"}, "Actions": {"Context": "Author"}}
        if node.attrib and node.attrib != attributes.get(path):
            raise ValueError("Foreign task XML attributes")
        children = list(node)
        if children:
            if (node.text or "").strip() or len({child.tag for child in children}) != len(children):
                raise ValueError("Ambiguous task XML")
            for child in children:
                walk(child, path + "/")
        else:
            leaves[path] = node.text or ""
    try:
        for child in task:
            walk(child, "")
        if len({child.tag for child in task}) != len(list(task)):
            return False
    except ValueError:
        return False
    launcher = str(PureWindowsPath(legacy_root) / FAMILIES[name])
    expected = {
        "Principals/Principal/LogonType": "InteractiveToken",
        "Settings/DisallowStartIfOnBatteries": "false",
        "Settings/StopIfGoingOnBatteries": "false",
        "Settings/StartWhenAvailable": "true",
        "Settings/ExecutionTimeLimit": "PT0S",
        "Settings/RestartOnFailure/Interval": "PT1M",
        "Settings/RestartOnFailure/Count": "3",
        "Actions/Exec/Command": "conhost.exe",
        "Actions/Exec/Arguments": '--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + launcher + '"',
    }
    optional = {
        "Triggers/LogonTrigger": "",
        "Triggers/LogonTrigger/Enabled": "true",
        "Principals/Principal/RunLevel": "LeastPrivilege",
        "Settings/IdleSettings/Duration": "PT10M",
        "Settings/IdleSettings/WaitTimeout": "PT1H",
        "RegistrationInfo/URI": "\\" + name,
        "Settings/MultipleInstancesPolicy": "IgnoreNew",
        "Settings/AllowHardTerminate": "true",
        "Settings/RunOnlyIfNetworkAvailable": "false",
        "Settings/IdleSettings/StopOnIdleEnd": "true",
        "Settings/IdleSettings/RestartOnIdle": "false",
        "Settings/AllowStartOnDemand": "true", "Settings/Enabled": "true",
        "Settings/Hidden": "false", "Settings/RunOnlyIfIdle": "false",
        "Settings/WakeToRun": "false", "Settings/Priority": "7",
        "Settings/DisallowStartOnRemoteAppSession": "false", "Settings/Volatile": "false",
        "Actions/Exec/WorkingDirectory": "",
    }
    variants = {"Settings/UseUnifiedSchedulingEngine": {"false", "true"}}
    if not {"Triggers/LogonTrigger", "Triggers/LogonTrigger/Enabled"} & leaves.keys():
        return False
    identity = {"Principals/Principal/UserId", "RegistrationInfo/Author", "Triggers/LogonTrigger/UserId"}
    if "Principals/Principal/UserId" not in leaves:
        return False
    for key in identity & leaves.keys():
        if leaves[key].casefold() not in {sid.casefold(), user.casefold()}:
            return False
    for key, value in expected.items():
        if leaves.get(key) != value:
            return False
    # The producer's registration timestamp is not an authority field. The full
    # XML (including it) is nevertheless pinned from observation through apply.
    if "RegistrationInfo/Date" in leaves:
        from datetime import datetime
        try:
            datetime.fromisoformat(leaves["RegistrationInfo/Date"].replace("Z", "+00:00"))
        except ValueError:
            return False
    allowed = expected.keys() | optional.keys() | variants.keys() | identity | {"RegistrationInfo/Date"}
    return all(key in allowed and (key not in optional or value == optional[key])
               and (key not in variants or value in variants[key])
               for key, value in leaves.items())


def _governance():
    path = Path(__file__).parent / "installation-context" / "installation_context.py"
    spec = importlib.util.spec_from_file_location("_index_historical_governance", path)
    if spec is None or spec.loader is None:
        raise ValueError("Index installation-context owner is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NativeTaskOwner:
    """One native owner holds the existing Index mutex across the handshake."""
    def __init__(self, root, name):
        self.root, self.name = root, name
        self.process = None
        self.lines = queue.Queue()

    def __enter__(self):
        host = shutil.which("pwsh")
        if not host or os.name != "nt":
            raise ValueError("Windows Task Scheduler owner is unavailable")
        script = Path(__file__).with_name("historical-registration-owner.ps1")
        self.process = subprocess.Popen(
            [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
        )
        def read():
            try:
                for line in self.process.stdout:
                    if len(line.encode("utf-8")) > MAX_NATIVE_BYTES:
                        self.lines.put(ValueError("Native task response exceeds limit"))
                        return
                    self.lines.put(json.loads(line))
            except (ValueError, OSError) as exc:
                self.lines.put(exc)
            finally:
                self.lines.put(ValueError("Native registration owner ended without confirmation"))
        threading.Thread(target=read, daemon=True).start()
        # Drain stderr independently: neither pipe may block the native owner.
        threading.Thread(target=lambda: self.process.stderr.read(), daemon=True).start()
        self.send({"legacyRoot": str(self.root), "name": self.name})
        try:
            self.initial = self.receive()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def receive(self):
        try:
            value = self.lines.get(timeout=30)
        except queue.Empty:
            raise ValueError("Native registration owner deadline exceeded; effects unconfirmed") from None
        if isinstance(value, Exception):
            raise ValueError(str(value))
        if not isinstance(value, dict):
            raise ValueError("Invalid native task response")
        return value

    def decide(self, action, fingerprint=None):
        self.send({"action": action, "sha256": fingerprint})
        response = self.receive()
        if response.get("stage") != "result" or not isinstance(response.get("result"), dict):
            raise ValueError("Native registration effect is unconfirmed")
        return response["result"]

    def __exit__(self, *exc):
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=10)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()


def operate(root, selection, name, finding=None, *, governance=None, native_factory=None):
    resource = name
    base = {"detector": DETECTOR, "revision": REVISION, "family": "registrations", "resource": resource}
    if name not in FAMILIES:
        return {**base, "status": "preserved", "reason": "unsupported-registration-family"}
    if os.name != "nt" and native_factory is None:
        return {**base, "status": "preserved", "reason": "unsupported-platform:windows-task-scheduler-only"}
    root = Path(root)
    launcher = root / Path(FAMILIES[name].replace("\\", "/"))
    effect_admitted = False
    try:
        owner = governance or _governance()
        custody_owner = getattr(owner, "historical_registration_retirement_custody", None)
        if not callable(custody_owner):
            raise ValueError("Index installation-context retirement owner is unavailable")
        with (native_factory or NativeTaskOwner)(root, name) as native:
            first = native.initial
            if first.get("stage") == "result":
                result = first.get("result", {})
                if result.get("status") == "already-absent":
                    return {**base, "status": "already-absent" if finding else "preserved", "reason": "native-registration-absent"}
                return {**base, "status": "failed" if finding else "preserved", "reason": str(result.get("reason", "native-inspection-unavailable"))}
            snapshot = first.get("snapshot")
            if first.get("stage") != "observed" or not isinstance(snapshot, dict):
                raise ValueError("Native registration observation is invalid")
            if snapshot.get("state") not in (1, 3) or not matches_archive(snapshot, str(root), name):
                native.decide("preserve")
                return {**base, "status": "preserved", "reason": "current-foreign-edited-or-unsettled-registration"}
            with custody_owner(
                context=selection["context"], expected_marketplace_id=selection["marketplace_id"],
                expected_plugin_id="agent-index",
                expected_namespace_generation=selection["namespace_generation"],
                expected_install_generation=selection["install_generation"],
                expected_activation_generation=selection["activation_generation"],
                legacy_root=root, required_paths=[launcher], durable_home=selection["durable_home"],
                payload_root=Path(__file__).parent.parent,
                maintenance_token=selection.get("maintenance_token"),
            ) as custody:
                current = {**base, "status": "candidate", "reason": "explicitly-adopted-retained-inert-native-task",
                           "sha256": native_fingerprint(snapshot), "selection_sha256": custody["sha256"]}
                if finding is None:
                    native.decide("preserve")
                    return current
                if finding != current:
                    native.decide("preserve")
                    return {**base, "status": "preserved", "reason": "observation-or-selection-changed"}
                custody["assert_current"]()
                effect_admitted = True
                result = native.decide("remove", current["sha256"])
                custody["assert_current"]()
                if result.get("status") not in {"applied", "already-absent", "preserved", "failed"}:
                    raise ValueError("Native registration effect is unconfirmed")
                return {**base, **result}
    except (OSError, ValueError, RuntimeError, KeyError, ImportError) as exc:
        return {**base, "status": "failed" if effect_admitted else "preserved", "reason": str(exc)}


def inspect(root, selection):
    return [operate(root, selection, name) for name in FAMILIES]


def apply(root, selection, finding):
    return operate(root, selection, finding.get("resource"), finding)
