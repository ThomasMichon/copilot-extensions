"""Exercise update gates, receipt judgment and monitor outcomes without an installed runtime."""

from __future__ import annotations

import ast
import codecs
import dataclasses
import functools
import importlib.util
import io
import math
import os
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


class ContractViolation(RuntimeError):
    pass


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation(message)


def _function(path: Path, name: str, namespace: dict) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    _expect(len(definitions) == 1, f"{path.name}: expected one {name} definition")
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def _module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ContractViolation(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check_monitor_outcome(source: Path) -> list[str]:
    """Exercise the legacy monitor adapter with a fake transition boundary."""
    try:
        _check_monitor_outcome(source)
    except (RuntimeError, OSError, UnicodeError, SyntaxError, TypeError, AttributeError,
            KeyError, NameError, ValueError) as error:
        return [f"update monitor outcome contract: {error}"]
    return []


def _check_monitor_outcome(source: Path) -> None:
    environ: dict[str, str] = {}
    environment = SimpleNamespace(environ=environ, getenv=environ.get)
    state: dict = {}
    calls = []

    def activate(*, monitor_was_live):
        calls.append(monitor_was_live)
        if state.get("error"):
            raise RuntimeError("fixture transition failure")
        return state["summary"]

    adapter = _function(source / "status_monitor_cutover.py", "installer_after_update", {
        "os": environment, "activate_after_update": activate, "print": lambda *_args, **_kwargs: None,
    })
    cases = (
        ("disabled", {"enabled": False, "action": "noop", "reason": "disabled"}, True),
        ("not-live", {"enabled": True, "action": "noop", "reason": "no-live-monitor"}, True),
        ("unrouted", {"enabled": True, "action": "noop", "reason": "no-routed-monitor"}, False),
        ("busy", {"enabled": True, "action": "noop", "reason": "cutover-busy:123"}, False),
        ("refused", {"enabled": True, "action": "refused", "result": {"ok": False}}, False),
        ("rolled-back", {"enabled": True, "action": "cutover",
                         "result": {"ok": False, "rolled_back": True}}, False),
        ("missing-result", {"enabled": True, "action": "cutover"}, False),
        ("invalid-success", {"enabled": True, "action": "cutover", "result": {"ok": "true"}}, False),
        ("cutover", {"enabled": True, "action": "cutover", "result": {"ok": True}}, True),
        ("restart-failed", {"enabled": True, "action": "restart", "restart": {"spawned": False}}, False),
        ("restart-missing", {"enabled": True, "action": "restart"}, False),
        ("restart-current", {"enabled": True, "action": "restart",
                             "restart": {"already_current": True}}, True),
        ("restart-spawned", {"enabled": True, "action": "restart", "restart": {"spawned": True}}, True),
        ("unknown", {"enabled": True, "action": "unknown"}, False),
        ("unknown-success", {"enabled": True, "action": "unknown", "result": {"ok": True}}, False),
    )
    for live in ("0", "1"):
        environment.environ["AGENT_WORKTREES_MONITOR_WAS_LIVE"] = live
        for label, summary, succeeded in cases:
            state.update(summary=summary, error=False)
            calls.clear()
            status = adapter()
            _expect(type(status) is int and (status == 0) == succeeded,
                    f"{label}: monitor transition outcome was misreported")
            _expect(calls == [live == "1"], f"{label}: monitor liveness was not forwarded")

    state["error"] = True
    calls.clear()
    try:
        status = adapter()
    except RuntimeError as error:
        _expect(str(error) == "fixture transition failure", "unexpected monitor exception")
    else:
        _expect(type(status) is int and status != 0, "monitor exception became success")
    _expect(calls == [True], "monitor exception did not reach the transition boundary")


def check_receipt_judgment(source: Path) -> list[str]:
    """Exercise the actual targeted-update receipt path with a fake child boundary."""
    try:
        _check_receipt_judgment(source)
    except (ContractViolation, OSError, UnicodeError, SyntaxError, TypeError, AttributeError, KeyError,
            NameError, ValueError) as error:
        return [f"update receipt contract: {error}"]
    return []


def _check_receipt_judgment(source: Path) -> None:
    package_name = "_install_receipt_contract"
    package = ModuleType(package_name)
    package.__path__ = []
    reconcile = ModuleType(f"{package_name}.reconcile")
    update_driver = ModuleType("update_driver")
    update_driver.participant_environment = dict
    state = {}
    root = Path("receipt-contract-runtime")

    def deployed(name, *, root):
        _expect(name == "agent-example" and root == Path("receipt-contract-runtime"),
                "receipt callback used the wrong runtime")
        return state["deployed"]

    reconcile.runtime_deployed_version = deployed
    reconcile._versions_equal = _function(source / "reconcile.py", "_versions_equal", {})
    tree = ast.parse((source / "installer_run.py").read_text(encoding="utf-8"))
    watchdogs = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == "_WATCHDOG_EXIT" for target in targets):
            watchdogs.append(node.value)
    _expect(len(watchdogs) == 1, "installer watchdog exit is missing or ambiguous")
    watchdog = ast.literal_eval(watchdogs[0])
    _expect(type(watchdog) is int and watchdog == 124, "installer watchdog exit changed")
    with patch.dict(sys.modules, {
        package_name: package, reconcile.__name__: reconcile, update_driver.__name__: update_driver,
    }):
        judge = _function(source / "installer_run.py", "_judge", {
            "__package__": package_name, "_WATCHDOG_EXIT": watchdog,
        })
        calls = []

        def spawn(argv, cwd, env, capture, **_kwargs):
            _expect(argv == ["installer"] and capture, "targeted installer used the wrong child")
            calls.append(argv)
            return SimpleNamespace(returncode=state["rc"]), None

        class Capture:
            def __init__(self, *_args, **_kwargs):
                pass

            def wait(self, timeout, **_kwargs):
                if state["expired"]:
                    raise subprocess.TimeoutExpired("installer", timeout)

            def deliver(self):
                pass

        def unowned(*_args, **_kwargs):
            raise ContractViolation("targeted installer bypassed the receipt owner")

        def result(status, rc, version, complete, duration):
            return SimpleNamespace(
                status=status, rc=rc, receipt_version=version, receipt_complete=complete, duration_s=duration,
            )

        runner_namespace = {
            "__package__": package_name, "codecs": codecs, "io": io, "math": math, "os": os,
            "subprocess": subprocess, "sys": sys, "tempfile": tempfile, "time": time,
            "ExitStack": ExitStack, "InstallerRun": result, "_WATCHDOG_EXIT": watchdog,
            "_spawn": spawn, "SyncProcessOutput": Capture, "_kill_tree": lambda *_args: None,
            "contained_test_mode": lambda: True, "_judge": judge,
        }
        _function(source / "installer_run.py", "_run_installer", runner_namespace)
        owned = _function(source / "installer_run.py", "run_installer", runner_namespace)
        process = SimpleNamespace(run=unowned, SubprocessError=subprocess.SubprocessError)
        output = SimpleNamespace(
            header=lambda *_args: None, info=lambda *_args: None,
            current_operation=lambda: None,
            human_operation=lambda *_args, **_kwargs: nullcontext(None),
            native_output=lambda: nullcontext(None),
        )
        adapter = _function(source / "update_installer_process.py", "_run_human_installer", {
            "installer_run": SimpleNamespace(run_installer=owned), "subprocess": process,
            "output": output, "powershell_file_target": lambda _argv: None,
        })
        execute = _function(source / "update_runtime.py", "_execute_runtime_step", {
            "__package__": package_name, "update_runtime_plan": SimpleNamespace(RuntimeUpdateStep=object),
            "functools": functools, "_run_human_installer": adapter, "output": output,
            "subprocess": process,
        })
        step = SimpleNamespace(
            status="", name="agent-example", source=None, runtime_root=root, project_root=None,
            payload=Path("receipt-contract-payload"), source_target=None, artifact=Path("receipt-contract-payload"),
            environment={}, deadline_s=60, target_version="2", argv=("installer",), fallback_argv=(),
        )
        cases = (
            ("current", 0, False, "2", "2", "OK"),
            ("normalized", 0, False, "2.0.dev1", "2.0-dev1", "OK"),
            ("missing", 0, False, None, "2", "runtime not at target"),
            ("stale", 0, False, "1", "2", "runtime not at target"),
            ("busy", 75, False, "2", "2", "installer exited 75"),
            ("watchdog", 124, False, "2", "2", "timed out"),
            ("deadline", None, True, None, "2", "timed out"),
        )
        for label, rc, expired, version, target, expected in cases:
            state.update(rc=rc, expired=expired, deployed=version)
            step.target_version = target
            calls.clear()
            status = execute(step)
            _expect(len(calls) == 1 and status.startswith(expected),
                    f"{label}: targeted installer outcome ignored its receipt or failure")


def check_machine_gate(source: Path) -> list[str]:
    """Run fresh synthetic-boundary cases against the actual source functions.

    Config/identity metadata, payload inventory and installer execution are fixtures;
    policy, gate decisions, both planners and the execution barrier are real.
    No source module can reach an installed agent_worktrees package.
    """
    try:
        _check_machine_gate(source)
    except (ContractViolation, OSError, UnicodeError, SyntaxError) as error:
        return [f"update machine-gate contract: {error}"]
    return []


def _check_machine_gate(source: Path) -> None:
    package_name = "_install_machine_gate_contract"
    package = ModuleType(package_name)
    package.__path__ = []
    config = ModuleType(f"{package_name}.config")
    identity = ModuleType(f"{package_name}.machine_identity")
    reconcile = ModuleType(f"{package_name}.reconcile")
    runtime = ModuleType(f"{package_name}.update_runtime")
    application = ModuleType(f"{package_name}.update_runtime_application")
    modules = {
        module.__name__: module
        for module in (package, config, identity, reconcile, runtime, application)
    }
    with patch.dict(sys.modules, modules), tempfile.TemporaryDirectory(
        prefix="update-machine-gate-contract-"
    ) as temporary:
        root = Path(temporary)
        payload = root / "payload"
        scripts = payload / "scripts"
        scripts.mkdir(parents=True)
        for extension in ("sh", "ps1"):
            (scripts / f"install.{extension}").write_text("exit 0\n", encoding="utf-8")
        anchor, context = root / "anchor", root / "enabling-repo"
        identity.runtime_policy_physical_machine = lambda *args, **kwargs: "physical-host"
        identity.repository_config = lambda repo_dir: None
        policy = _function(source / "update_gate.py", "runtime_allowed", {
            "__name__": reconcile.__name__, "__package__": package_name, "cfg": config,
        })
        gate = _module(source / "update_gate.py", f"{package_name}.update_gate")
        runtime._machine_gate_blocks = gate._machine_gate_blocks
        runtime.MACHINE_GATED_SKIP = gate.MACHINE_GATED_SKIP
        runtime.MachineDetectionError = gate.MachineDetectionError
        runtime._installer_timeout = lambda *args: 60
        application.plan_step = _function(source / "update_runtime_application.py", "plan_step", {
            "__name__": application.__name__, "__package__": package_name,
            "dataclasses": dataclasses,
        })
        planner = _module(source / "update_runtime_plan.py", f"{package_name}.update_runtime_plan")
        planner.shutil = SimpleNamespace(which=lambda name: name)
        namespace = {
            "__package__": package_name,
            "update_runtime_plan": planner,
            "functools": functools,
        }
        execute = _function(source / "update_runtime.py", "_execute_runtime_step", namespace)
        name = "agent-example"
        reconcile.core_installed_payload_dir = lambda name: payload
        reconcile.payload_version = lambda path: "2"
        reconcile.runtime_deployed_version = lambda *args, **kwargs: "1"
        reconcile._versions_equal = lambda first, second: first == second
        reconcile._zero_downtime_update = lambda path: False
        scenarios = (
            ("excluded", "machine-gated", {name: {"other"}}, True, None, False),
            ("omitted", "machine-gated", {}, True, None, False),
            ("allowed", "machine-gated", {name: {"local"}}, True, None, True),
            ("physical-allowed", "machine-gated", {name: {"physical-host"}}, True, None, True),
            ("unconfigured", "machine-gated", {}, False, None, True),
            ("universal", "universal", {}, True, None, True),
            ("detection-error", "machine-gated", {}, False, "detect", False),
            ("anchor-error", "machine-gated", {}, False, "anchor", False),
        )
        for kind in ("registered", "module"):
            for platform in ("windows", "linux"):
                for force in (False, True):
                    for explicit_context in (False, True):
                        for label, scope, allowed, present, failure, admitted in scenarios:
                            if failure == "anchor" and explicit_context:
                                continue
                            case = f"{kind}/{platform}/force={force}/context={explicit_context}/{label}"
                            _check_case(
                                case, planner, config, reconcile, namespace, execute, policy,
                                kind, platform, force, explicit_context, payload, anchor,
                                context, name, scope, allowed, present, failure, admitted,
                            )


def _check_case(
    case, planner, config, reconcile, namespace, execute, policy, kind, platform,
    force, explicit_context, payload, anchor, context, name, scope, allowed,
    present, failure, admitted,
) -> None:
    detected, gate_reads, presence_reads, consulted, prepared, invoked = [], [], [], [], [], []
    selected_config = []

    def load_config():
        if failure == "anchor":
            raise ValueError("synthetic anchor resolution failure")
        config = SimpleNamespace(configured_machine="local", default_repo=SimpleNamespace(anchor=anchor))
        selected_config.append(config)
        return config

    def detect_machine(repo, *, value=None, config=None):
        detected.append(repo)
        _expect(value == "local", f"{case}: configured machine identity was not supplied")
        _expect(config is selected_config[0], f"{case}: target configuration was not supplied")
        if failure == "detect":
            raise ValueError("synthetic machine detection failure")
        return "local"

    def runtime_allowed(*args, **kwargs):
        consulted.append((args, kwargs))
        return policy(*args, **kwargs)

    def load_gate(repo):
        gate_reads.append(repo)
        return allowed

    def gate_present(repo):
        presence_reads.append(repo)
        return present

    def environment(*args):
        prepared.append(args)
        return {}, payload

    def run(argv, **kwargs):
        invoked.append((argv, kwargs))
        return SimpleNamespace(status="OK")

    config.load_config, config.detect_machine = load_config, detect_machine
    config.detect_platform = lambda: platform
    reconcile.manifest_runtime_scope = lambda path: scope
    reconcile.load_runtime_gate = load_gate
    reconcile.gate_manifest_present = gate_present
    reconcile.runtime_allowed = runtime_allowed
    reconcile.runtime_installer_environment = environment
    namespace["_run_human_installer"] = run
    namespace["output"] = SimpleNamespace(header=lambda text: None, info=lambda text: None)
    options = {"force": force, "gate_context": context if explicit_context else None}
    step = (
        planner.plan_registered(name, platform, **options) if kind == "registered"
        else planner.plan_module(name, payload, platform, **options)
    )
    if scope == "machine-gated":
        if failure != "anchor":
            _expect(detected == [context if explicit_context else anchor], f"{case}: wrong repository")
        if not failure:
            expected_repo = [context if explicit_context else anchor]
            _expect(
                gate_reads == expected_repo and presence_reads == expected_repo,
                f"{case}: gate configuration came from the wrong repository",
            )
            _expect(
                consulted == [((scope, name, "local", allowed), {
                    "gate_present": present,
                    "repo_dir": context if explicit_context else anchor,
                    "config": selected_config[0],
                })],
                f"{case}: canonical policy or manifest-presence argument was bypassed",
            )
    else:
        _expect(
            not detected and not gate_reads and not presence_reads and not consulted,
            f"{case}: universal runtime consulted the gate",
        )
    if admitted:
        _expect(bool(step.argv) and not step.status, f"{case}: permitted update was blocked")
        _expect(step.deadline_s == 60 and len(prepared) == 1, f"{case}: invalid installer plan")
        _expect(execute(step) == "OK" and len(invoked) == 1, f"{case}: permitted installer not reached")
        _expect(invoked[0][0] == list(step.argv), f"{case}: different installer argv executed")
    else:
        expected = (
            step.status.startswith("machine detection failed:") and step.blocked
            if failure else step.status == "SKIPPED (not allowed on this machine)"
        )
        _expect(expected, f"{case}: refusal was not reported honestly")
        _expect(not step.argv and not step.fallback_argv, f"{case}: denied update has installer argv")
        _expect(not prepared, f"{case}: denied update prepared runtime state")
        _expect(execute(step) == step.status and not invoked, f"{case}: denied installer executed")
