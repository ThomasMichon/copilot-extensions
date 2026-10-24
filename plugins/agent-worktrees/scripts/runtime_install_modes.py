"""Explicit runtime modes whose Python owner holds whole-operation cutover."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path

import runtime_install as lifecycle
import tomllib


_MODE_OWNER_ABI = 1
_CONTEXT_CHILD_TIMEOUT = 480


def _retained_control(
    root: Path, admitted: lifecycle.engine.FrozenInputs, custody: ExitStack,
    *, mode: str, wait_timeout: float, child_timeout: float,
) -> tuple[Path, int, str] | None:
    current = lifecycle.runtime.current_version(root)
    previous = lifecycle.runtime.read_last_known_good(root)
    if mode == "repair":
        versions = [previous] if previous != current else []
    elif mode == "dev-release":
        claim = lifecycle.runtime.read_dev_claim(root, strict=True)
        versions = [claim["previous_version"]] if claim is not None else []
    else:
        versions = [current, previous]
    probe = """
import importlib, importlib.util, json, pathlib, sys
slot = pathlib.Path(sys.argv[1]).resolve()
assert pathlib.Path(sys.prefix).resolve() == slot, "control interpreter differs"
package = importlib.util.find_spec("agent_worktrees")
assert package is not None and isinstance(package.origin, str), "control package is missing"
assert pathlib.Path(package.origin).resolve().is_relative_to(slot), "control package escaped"
candidate_spec = importlib.util.find_spec("agent_worktrees.runtime_candidate")
if candidate_spec is None:
    print(json.dumps({"version": 1, "compatible": False}))
    raise SystemExit(0)
assert isinstance(candidate_spec.origin, str), "control health module has no ordinary origin"
assert pathlib.Path(candidate_spec.origin).resolve().is_relative_to(slot), "control health escaped"
candidate = importlib.import_module("agent_worktrees.runtime_candidate")
assert pathlib.Path(candidate.__file__).resolve().is_relative_to(slot), "control import escaped"
abi = getattr(candidate, "MODE_OWNER_ABI", None)
compatible = type(abi) is int and abi == int(sys.argv[2])
if compatible:
    assert (slot / ".runtime-input-origins.json").is_file(), "control has no origin receipt"
    candidate.verify_runtime_candidate()
    candidate.verify_installed_origins()
print(json.dumps({"version": 1, "compatible": compatible}))
"""
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for version in dict.fromkeys(versions):
        if version is None or version == lifecycle.runtime.DEV_VERSION:
            continue
        with ExitStack() as trial:
            try:
                slot, descriptor = trial.enter_context(
                    lifecycle.runtime.retention_custody(root, version, timeout=wait_timeout)
                )
            except FileNotFoundError:
                print(f"Control runtime {version}: no longer installed", flush=True)
                continue
            marker = lifecycle.runtime.read_marker(root, version)
            if marker is None:
                raise lifecycle.InstallationRefused(
                    f"Control runtime {version} has malformed or missing completion evidence"
                )
            payload_hash = marker.get("payload_hash")
            if not isinstance(payload_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", payload_hash):
                print(f"Control runtime {version}: no full admitted content identity", flush=True)
                continue
            if lifecycle.runtime.check_admission(root, version, payload_hash=payload_hash) != (
                lifecycle.runtime.ADMIT_REUSE
            ):
                raise lifecycle.InstallationRefused(f"Control runtime {version} failed admission")
            python = lifecycle.runtime.slot_python(root, version)
            if python is None:
                raise lifecycle.InstallationRefused(
                    f"Control runtime {version} has no installed interpreter"
                )
            try:
                completed = lifecycle.engine.run_owned(
                    [str(python), "-I", "-B", "-c", probe, str(slot), str(_MODE_OWNER_ABI)],
                    cwd=Path(root.anchor), env=environment, timeout=child_timeout,
                    procutil=lifecycle.engine._procutil(admitted), construction_fd=descriptor,
                    runtime_root=root, runtime_version=version,
                    runtime_path=Path(lifecycle.runtime.__file__),
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                if error.stdout:
                    print(error.stdout, end="", file=sys.stderr, flush=True)
                if error.stderr:
                    print(error.stderr, end="", file=sys.stderr, flush=True)
                raise
            result = json.loads(completed.stdout)
            if (
                not isinstance(result, dict) or set(result) != {"version", "compatible"}
                or type(result["version"]) is not int or result["version"] != 1
                or type(result["compatible"]) is not bool
            ):
                raise lifecycle.InstallationRefused("Installed control returned invalid health evidence")
            if not result["compatible"]:
                print(f"Control runtime {version}: incompatible mode-owner ABI", flush=True)
                continue
            print(
                f"Control runtime {version}: installed mode-owner ABI {_MODE_OWNER_ABI} verified",
                file=sys.stderr, flush=True,
            )
            custody.enter_context(trial.pop_all())
            return python, descriptor, version
    return None


def run(
    root: Path,
    plugin: Path,
    *,
    payload: Path,
    uv: Path,
    base_python: Path,
    mode: str,
    owner: Path | None = None,
    wait_timeout: float = 180,
    child_timeout: float = 180,
    expected_source_identity: dict | None = None,
    frontend: dict | None = None,
) -> dict:
    """Bootstrap installed control code without selecting a replacement slot."""
    governance = lifecycle.Governance(root, payload)
    root = governance.root
    if mode in ("dev", "dev-release"):
        owner = _resolve_owner(plugin, owner, source_required=mode == "dev")
    staging = root / ".installer-inputs"
    staging.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((plugin / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    source_identity = lifecycle.runtime_source.read_source_identity(
        plugin, version, plugin="agent-worktrees",
    )
    if expected_source_identity is not None and (
        lifecycle.runtime_source.manifest_fields(source_identity) != expected_source_identity
    ):
        raise lifecycle.InstallationRefused("Source identity changed after frontend runtime selection")
    provenance = lifecycle._source_provenance(plugin, version, source_identity)
    if "deployment_source" in provenance:
        deployment_source = json.loads(provenance["deployment_source"])
        deployment_source["path"] = governance.payload.as_posix()
        deployment_source["kind"] = (
            "marketplace" if "/.copilot/installed-plugins/" in governance.payload.as_posix() else "local"
        )
        if deployment_source["kind"] == "marketplace":
            deployment_source.update(commit=None, branch=None, dirty=None)
        provenance["deployment_source"] = json.dumps(deployment_source)
    with tempfile.TemporaryDirectory(dir=staging, prefix="mode-owner-") as temporary:
        control_root = Path(temporary)
        with ExitStack() as custody:
            with governance.held(timeout=wait_timeout):
                if source_identity is not None:
                    plugin = lifecycle.runtime_source.publish_source_snapshot(plugin, root, source_identity)
                admitted = lifecycle.engine.freeze_inputs(plugin, control_root / "admitted-inputs")
                retained = (
                    _retained_control(
                        root, admitted, custody, mode=mode,
                        wait_timeout=wait_timeout, child_timeout=child_timeout,
                    )
                    if os.name == "nt" else None
                )
                if retained is None:
                    control = custody.enter_context(
                        lifecycle.engine.numbered_candidate(
                            control_root,
                            "control",
                            plugin=admitted.plugin,
                            uv=uv,
                            base_python=base_python,
                            health_module="agent_worktrees.runtime_candidate",
                            health_callable="verify_runtime_candidate",
                            construction_callable="agent_worktrees.runtime_candidate:prepare_runtime_candidate",
                            construction_data=provenance,
                            process_retention=True,
                            expected_source_identity=lifecycle.runtime_source.manifest_fields(source_identity),
                            expected_payload_hash=admitted.payload_hash,
                            wait_timeout=wait_timeout,
                            child_timeout=child_timeout,
                        )
                    )
                    control_python = lifecycle.runtime.slot_python(control_root, "control")
                    control_script = control.frozen_plugin / "scripts" / "runtime_install.py"
                else:
                    control_python = retained[0]
                    control_script = admitted.plugin / "scripts" / "runtime_install.py"
            result_path = control_root / "result.json"
            argv = [
                str(control_python),
                "-I",
                "-B",
                str(control_script),
                "--root",
                str(root),
                "--plugin",
                str(admitted.plugin),
                "--payload",
                str(governance.payload),
                "--uv",
                str(uv),
                "--base-python",
                str(base_python),
                "--result",
                str(result_path),
                "--mode",
                mode,
                "--mode-owner",
                "--payload-hash",
                admitted.payload_hash,
                "--governance-generation",
                json.dumps(governance.signature()),
                "--wait-timeout",
                str(wait_timeout),
                "--child-timeout",
                str(child_timeout),
            ]
            if retained is not None:
                argv.extend(["--construction-provenance", json.dumps(provenance)])
            if frontend is not None:
                content = admitted.plugin
                argv.extend(["--frontend", json.dumps(frontend),
                             "--publication-source-root", str(content)])
            if owner is not None:
                argv.extend(["--owner", str(owner)])
            environment = dict(os.environ)
            for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
                environment.pop(name, None)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            if retained is not None:
                try:
                    completed = lifecycle.engine.run_owned(
                        argv, cwd=Path(root.anchor), env=environment,
                        timeout=_CONTEXT_CHILD_TIMEOUT,
                        procutil=lifecycle.engine._procutil(admitted),
                        construction_fd=retained[1],
                        runtime_root=root, runtime_version=retained[2],
                        runtime_path=Path(lifecycle.runtime.__file__),
                        allow_breakaway=True,
                        retention_scope=control_root / "retention-scope",
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    if error.stdout:
                        print(error.stdout, end="", file=sys.stderr, flush=True)
                    if error.stderr:
                        print(error.stderr, end="", file=sys.stderr, flush=True)
                    raise
                if completed.stdout:
                    print(completed.stdout, end="", flush=True)
                if completed.stderr:
                    print(completed.stderr, end="", file=sys.stderr, flush=True)
                return json.loads(result_path.read_text(encoding="utf-8"))
            process = subprocess.Popen(
                argv,
                executable=argv[0] if os.name == "nt" else None,
                env=environment,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                process.wait()
            except BaseException:
                # Control code and both generations remain available until the
                # actual owner settles; never delete its interpreter underneath it.
                process.wait()
                raise
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, argv)
            return json.loads(result_path.read_text(encoding="utf-8"))


def run_owned(
    root: Path,
    plugin: Path,
    *,
    payload: Path,
    uv: Path | None = None,
    base_python: Path | None = None,
    mode: str,
    expected_governance: dict,
    expected_payload_hash: str | None = None,
    construction_provenance: dict[str, str] | None = None,
    owner: Path | None = None,
    wait_timeout: float = 180,
    child_timeout: float = 180,
    frontend: dict | None = None,
    publication_source_root: Path | None = None,
) -> dict:
    if mode not in ("repair", "dev", "dev-release"):
        raise lifecycle.InstallationRefused(f"Unsupported whole-operation runtime mode: {mode}")
    if mode != "dev-release" and (uv is None or base_python is None):
        raise lifecycle.InstallationRefused("Runtime construction requires uv and its base interpreter")
    fields = ("version", "source", "commit", "branch", "deployment_source")
    if construction_provenance is not None and (
        not isinstance(expected_payload_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_payload_hash) is None
        or not isinstance(construction_provenance, dict)
        or set(construction_provenance) != set(fields)
        or any(
            not isinstance(construction_provenance[field], str)
            or not construction_provenance[field] for field in fields
        )
    ):
        raise lifecycle.InstallationRefused(
            "Retained control requires complete frozen construction provenance"
        )
    control_slot = Path(sys.prefix).resolve()
    lifecycle._load_installed(control_slot)
    from agent_worktrees import status_monitor_cutover as cutover

    governance = lifecycle.Governance(root, payload)
    if governance.signature() != expected_governance:
        raise lifecycle.InstallationRefused(
            "Installation governance changed before mode ownership"
        )
    root = governance.root
    candidate_plugin = publication_source_root if publication_source_root is not None else plugin
    os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(governance.payload)
    with ExitStack() as custody:
        # Keep namespace custody across construction and nested settlement;
        # a manager already holds these locks before starting its child.
        custody.enter_context(governance.held(timeout=wait_timeout))
        guard = cutover._acquire_cutover_lock(
            root / "status-monitor-routing",
            timeout_s=wait_timeout,
        )
        custody.callback(guard.release)
        if mode == "dev-release":
            return _release(
                root,
                plugin,
                governance,
                custody,
                guard,
                control_slot,
                owner=owner,
                wait_timeout=wait_timeout,
                child_timeout=child_timeout,
            )
        with governance.held(timeout=wait_timeout):
            previous = lifecycle.runtime.current_version(root)
            if previous is not None:
                custody.enter_context(
                    lifecycle.runtime.retention_lease(root, previous, timeout=wait_timeout)
                )
            package_version = tomllib.loads(
                (plugin / "pyproject.toml").read_text(encoding="utf-8"),
            )["project"]["version"]
            if mode == "dev":
                if owner is None:
                    raise lifecycle.InstallationRefused(
                        "Dev construction requires an attributable owner"
                    )
                lifecycle.runtime.claim_dev(root, str(owner), previous_version=previous)
                version = lifecycle.runtime.DEV_VERSION
            else:
                source_identity = lifecycle.runtime_source.read_source_identity(
                    candidate_plugin, package_version, plugin="agent-worktrees",
                )
                generation_base = (
                    source_identity.runtime_version if source_identity is not None else package_version
                )
                separator = "." if "+" in generation_base else "+"
                version = f"{generation_base}{separator}repair.{uuid.uuid4().hex}"
            from agent_worktrees._build_info import BUILD_INFO

            if construction_provenance is not None:
                provenance = dict(construction_provenance)
            else:
                provenance = {field: BUILD_INFO[field] for field in fields}
            if provenance["version"] != package_version:
                raise lifecycle.InstallationRefused(
                    "Frozen control provenance differs from candidate package version"
                )
            candidate_arguments = dict(
                plugin=candidate_plugin,
                uv=uv,
                base_python=base_python,
                health_module="agent_worktrees.runtime_candidate",
                health_callable="verify_runtime_candidate",
                construction_callable="agent_worktrees.runtime_candidate:prepare_runtime_candidate",
                construction_data=provenance,
                process_retention=True,
                wait_timeout=wait_timeout,
                child_timeout=child_timeout,
            )
            if mode != "dev":
                candidate_arguments["expected_source_identity"] = lifecycle.runtime_source.manifest_fields(source_identity)
            if expected_payload_hash is not None:
                candidate_arguments["expected_payload_hash"] = expected_payload_hash
            candidate_context = (
                lifecycle.engine.dev_candidate(root, owner=str(owner), **candidate_arguments)
                if mode == "dev"
                else lifecycle.engine.numbered_candidate(root, version, **candidate_arguments)
            )
            candidate = custody.enter_context(candidate_context)
            if mode == "dev":
                version = candidate.slot.name
        result = lifecycle.settle_candidate(
            root,
            version,
            payload=governance.payload,
            payload_hash=candidate.payload_hash,
            expected_governance=governance.signature(),
            previous=previous,
            wait_timeout=wait_timeout,
            cutover_guard=guard,
            lifecycle_slot=control_slot,
            package_version=package_version,
            dev_owner=str(owner) if mode == "dev" else None,
            frontend=frontend,
            publication_payload_hash=expected_payload_hash,
            publication_source_root=(
                (publication_source_root if publication_source_root is not None else candidate.frozen_plugin)
                if frontend is not None else None
            ),
            child_timeout=child_timeout, base_python=base_python, governance=governance,
        )
        result["reused"] = candidate.reused
        return result


def _resolve_owner(payload: Path, owner: Path | None, *, source_required: bool) -> Path:
    if owner is not None and not source_required:
        return owner.resolve()
    result = subprocess.run(
        [
            "git",
            "-C",
            str(owner if owner is not None else payload),
            "rev-parse",
            "--show-toplevel",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    resolved = Path(result.stdout.strip()).resolve(strict=True)
    if owner is not None and resolved != owner.resolve(strict=True):
        raise lifecycle.InstallationRefused("Dev owner must be the worktree's repository root")
    if source_required and not payload.resolve(strict=True).is_relative_to(resolved):
        raise lifecycle.InstallationRefused("Dev inputs must come from their claiming worktree")
    return resolved


def _release(
    root: Path,
    plugin: Path,
    governance,
    custody: ExitStack,
    guard,
    control_slot: Path,
    *,
    owner: Path | None,
    wait_timeout: float,
    child_timeout: float,
) -> dict:
    with governance.held(timeout=wait_timeout):
        claim = lifecycle.runtime.read_dev_claim(root, strict=True)
        if claim is None:
            raise lifecycle.InstallationRefused("No dev claim is available to restore")
        if owner is None or claim["owner"] != str(owner):
            raise lifecycle.runtime.DevClaimConflict(claim)
        previous = claim.get("previous_version")
        if previous is None or previous == lifecycle.runtime.DEV_VERSION:
            raise lifecycle.InstallationRefused(
                "Dev claim has no immutable rollback target; repair before releasing it"
            )
        _slot, descriptor = custody.enter_context(
            lifecycle.runtime.retention_custody(
                root,
                previous,
                timeout=wait_timeout,
            )
        )
        selected = lifecycle.runtime.current_version(root)
        if selected is not None and selected != previous:
            custody.enter_context(
                lifecycle.runtime.retention_lease(root, selected, timeout=wait_timeout)
            )
        marker = lifecycle.runtime.read_marker(root, previous)
        if marker is None or not marker.get("payload_hash"):
            raise lifecycle.InstallationRefused(
                "Dev rollback target has no completed attributable build"
            )
        import agent_procutil
        from agent_worktrees import runtime_candidate

        probe = (
            "import importlib.metadata,importlib.util,json,pathlib,sys; "
            "from agent_worktrees import __main__ as core; "
            "assert pathlib.Path(core.__file__).resolve().is_relative_to("
            "pathlib.Path(sys.prefix).resolve()); "
            "core._load_full_command_surface(); "
            "spec=importlib.util.spec_from_file_location('_retained_origin_health',sys.argv[1]); "
            "health=importlib.util.module_from_spec(spec); spec.loader.exec_module(health); "
            "health.verify_installed_origins(); "
            "print(json.dumps(importlib.metadata.version('agent-worktrees')))"
        )
        environment = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            environment.pop(name, None)
        health = lifecycle.engine.run_owned(
            [str(lifecycle.runtime.slot_python(root, previous)), "-I", "-B", "-c", probe,
             str(Path(runtime_candidate.__file__).resolve())],
            cwd=Path(root.anchor), env=environment, procutil=agent_procutil,
            construction_fd=descriptor, timeout=child_timeout,
            runtime_root=root, runtime_version=previous,
            runtime_path=Path(lifecycle.runtime.__file__),
        )
        package_version = json.loads(health.stdout)
    # Keep the claim's original rollback reference until promotion and actual
    # daemon retirement have both settled.
    result = lifecycle.settle_candidate(
        root,
        previous,
        payload=governance.payload,
        payload_hash=marker["payload_hash"],
        expected_governance=governance.signature(),
        previous=selected,
        wait_timeout=wait_timeout,
        cutover_guard=guard,
        lifecycle_slot=control_slot,
        package_version=package_version,
        governance=governance,
        dev_owner=str(owner),
    )
    with governance.held(timeout=wait_timeout):
        expected_claim = dict(claim)
        if lifecycle.runtime.is_dev_generation(previous):
            expected_claim["selected_generation"] = previous
        if lifecycle.runtime.read_dev_claim(root, strict=True) != expected_claim:
            raise lifecycle.InstallationRefused("Dev claim changed before its settled release")
        lifecycle.runtime.release_dev(root, str(owner))
    result["reused"] = True
    return result
