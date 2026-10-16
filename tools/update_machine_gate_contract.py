"""Exercise update's machine gate without importing an installed runtime."""

from __future__ import annotations

import ast
import functools
import importlib.util
import sys
import tempfile
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


def check_machine_gate(source: Path) -> list[str]:
    """Run fresh synthetic-boundary cases against the actual source functions.

    Config discovery, payload inventory and installer execution are fixtures;
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
    reconcile = ModuleType(f"{package_name}.reconcile")
    runtime = ModuleType(f"{package_name}.update_runtime")
    modules = {module.__name__: module for module in (package, config, reconcile, runtime)}
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
        policy = _function(source / "reconcile.py", "runtime_allowed", {})
        gate = _module(source / "update_gate.py", f"{package_name}.update_gate")
        runtime._machine_gate_blocks = gate._machine_gate_blocks
        runtime.MACHINE_GATED_SKIP = gate.MACHINE_GATED_SKIP
        runtime.MachineDetectionError = gate.MachineDetectionError
        runtime._installer_timeout = lambda *args: 60
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

    def load_config():
        if failure == "anchor":
            raise ValueError("synthetic anchor resolution failure")
        return SimpleNamespace(default_repo=SimpleNamespace(anchor=anchor))

    def detect_machine(repo):
        detected.append(repo)
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
                consulted == [((scope, name, "local", allowed), {"gate_present": present})],
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
