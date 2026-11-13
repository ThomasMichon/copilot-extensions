"""Python-owned Worktrees construction, publication and monitor settlement."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from contextlib import ExitStack, nullcontext
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import installer_engine as engine
import versioned_runtime as runtime
import versioned_runtime_source as runtime_source
import runtime_install_publication as helper_publication
import runtime_install_driver as install_driver
import runtime_input_verifier as verification
from runtime_install_profiles import runtime_profile_policy
from runtime_install_governance import Governance, InstallationRefused


def helper_publication_plan(path: Path, digest: str, *, prepare_only: bool = False) -> dict:
    """Validate data under the existing live lifecycle, without acquiring its locks."""
    publication = runtime._runtime_component("versioned_runtime_publication")
    before = publication.state_path(path, private=True)
    data = path.read_bytes()
    if (
        before is None or hashlib.sha256(data).hexdigest() != digest
        or publication.state_path(path, private=True) != before
    ):
        raise InstallationRefused("Helper publication plan changed")
    plan = json.loads(data)
    if not isinstance(plan, dict) or set(plan) != {
        "version", "root", "content", "owner", "scope", "pin", "content_hash",
        "python", "bootstrap", "package_version", "governance", "locks", "pid", "result",
        "copies", "copies_identity", "bindings",
        "shell",
    } or plan["version"] != 1 or plan["scope"] not in ("resolvers", "wrappers") or plan["shell"] not in ("sh", "ps1"):
        raise InstallationRefused("Invalid helper publication plan")
    for key in ("root", "content", "owner", "python", "bootstrap", "result", "copies"):
        if not isinstance(plan[key], str) or any(char in plan[key] for char in "\r\n\0"):
            raise InstallationRefused("Invalid helper publication path")
        if not Path(plan[key]).is_absolute():
            raise InstallationRefused("Helper publication paths must be absolute")
    if Path(plan["result"]).parent != path.parent:
        raise InstallationRefused("Helper acknowledgement is outside its owned plan")
    copies = publication.state_path(Path(plan["copies"]), private=True)
    if (Path(plan["copies"]).parent != path.parent or copies is None
            or [copies.device, copies.inode] != plan["copies_identity"]):
        raise InstallationRefused("Callback copy inventory is not its owner-created file")
    governance = Governance(
        Path(plan["root"]), Path(plan["owner"]),
        **({"source_descriptor": plan["governance"]["source_descriptor"]}
           if "source_descriptor" in plan["governance"] else {}),
        **({"runtime_profile_policy": plan["governance"]["runtime_profile_policy"]}
           if "runtime_profile_policy" in plan["governance"] else {}),
        **({"runtime_profile_mode": plan["governance"]["runtime_profile_mode"]}
           if "runtime_profile_mode" in plan["governance"] else {}),
        **({"runtime_input_verification": plan["governance"]["runtime_input_verification"],
            "prepare_only": prepare_only}
           if "runtime_input_verification" in plan["governance"] else {}),
    )
    if governance.signature() != plan["governance"]:
        raise InstallationRefused("Helper publication governance changed")
    if governance.context:
        receipt = governance.initial[1]
        specs = (
            (governance.durable_home / "marketplaces" / ".locks" /
             f"{receipt['marketplaceId']}.genesis", "genesis", None),
            (Path(receipt["cellRoot"]) / ".locks" / "agent-worktrees.install.lock",
             "install", "agent-worktrees"),
        )
        if not isinstance(plan["locks"], list) or len(plan["locks"]) != len(specs):
            raise InstallationRefused("Helper publication has no borrowed namespace custody")
        for (lock_path, kind, plugin), owner in zip(specs, plan["locks"]):
            lock = governance.helper._DirectoryLock(
                lock_path, kind=kind, marketplace_id=receipt["marketplaceId"], plugin_id=plugin,
            )
            lock.token, lock.acquired = owner["token"], True
            lock.assert_owned()
            if (
                lock._owner() != owner or owner["host"] != lock.host
                or owner["pid"] != plan["pid"] or not governance.helper._pid_is_live(owner["pid"])
            ):
                raise InstallationRefused("Borrowed namespace publication owner changed")
    else:
        helper = Path(plan["content"]) / "scripts" / "command_binding.py"
        spec = importlib.util.spec_from_file_location("_runtime_helper_binding", helper)
        if spec is None or spec.loader is None:
            raise InstallationRefused("Helper command-binding validator is unavailable")
        binding = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(binding)
        lease = binding._load_lifecycle().borrowed_lease(
            binding._rules(), governance.helper, payload_root=Path(plan["owner"]),
            plugin_id="agent-worktrees", legacy_root=governance.root,
            project_root=governance.project_root,
            **governance.binding_options,
        )
        if lease is None:
            raise InstallationRefused("Helper publication requires its existing borrowed lifecycle")
    content = Path(plan["content"])
    selected = runtime_source.read_source_identity(content, plan["package_version"])
    if runtime_source.manifest_fields(selected) != plan["pin"]:
        raise InstallationRefused("Helper publication source identity changed")
    fingerprint = runtime.fingerprint_source(engine._source_files(engine._package_roots(content)))
    if fingerprint != plan["content_hash"]:
        raise InstallationRefused("Frozen helper publication inputs changed")
    expected_bindings = helper_publication.declared_copies(
        content, governance.root, plan["scope"], plan["shell"], bool(governance.context),
    )
    if plan["bindings"] != expected_bindings:
        raise InstallationRefused("Callback copy bindings differ from their authorized scope")
    return plan


def _publish_helpers(
    governance: Governance, frontend: dict | None, content: Path, slot: Path,
    descriptor: int, *, timeout: float,
    base_python: Path,
    expected_payload_hash: str | None = None,
) -> helper_publication.HelperPublication | None:
    if frontend is None:
        return
    if expected_payload_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_payload_hash):
        raise InstallationRefused("Helper publication requires a full source SHA256 pin")
    if (
        set(frontend) != {"shell", "executable", "scope"}
        or frontend["shell"] not in ("sh", "ps1")
        or frontend["scope"] not in ("resolvers", "wrappers")
    ):
        raise InstallationRefused("Invalid native helper publisher")
    executable = Path(frontend["executable"])
    if not executable.is_absolute() or not executable.is_file() or executable.name.lower() not in (
        "bash", "bash.exe", "pwsh", "pwsh.exe", "powershell.exe", "powershell",
    ):
        raise InstallationRefused("Helper publisher requires an exact native shell")
    if os.name == "nt" and frontend["shell"] == "ps1" and executable.name.lower() in ("pwsh", "pwsh.exe"):
        content = Path(engine._native_retained_path(content))
    identity = _installed_identity(slot)
    source = json.loads(identity["build_info"]["deployment_source"])
    pin = {key: source[key] for key in ("runtime_version", "runtime_source") if key in source}
    staging = governance.root / ".installer-inputs"
    staging.mkdir(parents=True, exist_ok=True)
    directory = engine._private_retained_root(
        staging, name=f"helper-publication-{os.getpid()}-{uuid.uuid4().hex}",
    )
    with nullcontext(directory):
        plan_path, result_path = directory / "plan.json", directory / "result.json"
        publication = runtime._runtime_component("versioned_runtime_publication")
        copies_path = directory / "copies"
        with os.fdopen(os.open(copies_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600), "w+b") as stream:
            copies_state = publication.state_fd(stream.fileno())
        plan = {
            "version": 1, "root": str(governance.root), "content": str(content.resolve()),
            "owner": str(governance.payload), "scope": frontend["scope"], "pin": pin, "shell": frontend["shell"],
            "python": str(runtime.slot_python(governance.root, slot.name)),
            "bootstrap": str(base_python),
            "package_version": (
                pin["runtime_source"]["package_version"] if pin else identity["build_info"]["version"]
            ), "governance": governance.signature(),
            "locks": [dict(lock._owner()) for lock in governance.publication_locks],
            "pid": (
                governance.publication_locks[0]._owner()["pid"]
                if governance.publication_locks else os.getpid()
            ), "result": str(result_path),
            "copies": str(copies_path), "copies_identity": [copies_state.device, copies_state.inode],
            "bindings": helper_publication.declared_copies(
                content, governance.root, frontend["scope"], frontend["shell"], bool(governance.context),
            ),
            "content_hash": expected_payload_hash if expected_payload_hash is not None
            else runtime.fingerprint_source(engine._source_files(engine._package_roots(content))),
        }
        data = json.dumps(plan, sort_keys=True).encode()
        with os.fdopen(os.open(plan_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600), "w+b") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            plan_state = publication.state_fd(stream.fileno())
        digest = hashlib.sha256(data).hexdigest()
        entry = content / "scripts" / f"install.{frontend['shell']}"
        # Windows st_dev differs across Python versions; callbacks use the
        # plan owner's interpreter without weakening its file identity check.
        if frontend["shell"] == "ps1":
            entry_name = str(entry)
            if os.name == "nt" and executable.name.lower() in ("pwsh", "pwsh.exe"):
                if entry_name[:8].lower() == "\\\\?\\unc\\":
                    entry_name = "\\\\?\\UNC\\" + entry_name[8:]
                entry_name = runtime._normalize_windows_extended_path(entry_name)
                if not Path(entry_name).is_absolute():
                    raise InstallationRefused("PowerShell helper source is not an ordinary absolute path")
            argv = [str(executable), "-NoProfile", "-NonInteractive", "-File", entry_name,
                    "publish-runtime-helpers", "-InstallDir", str(governance.root),
                    "-BootstrapPython", sys._base_executable,
                    "-RuntimePublicationPlan", str(plan_path), "-RuntimePublicationDigest", digest]
        else:
            argv = [str(executable), entry.as_posix(), "publish-runtime-helpers",
                    "--install-dir", str(governance.root),
                    "--runtime-publication-python", sys._base_executable,
                    "--runtime-publication-plan", str(plan_path), "--runtime-publication-digest", digest]
        if governance.source_descriptor is not None:
            argv.extend([
                "-RuntimeSourceDescriptor" if frontend["shell"] == "ps1" else "--runtime-source-descriptor",
                json.dumps(governance.source_descriptor, sort_keys=True),
            ])
        environment = governance.publication_environment()
        environment["COPILOT_PLUGIN_STAGED_FROM"] = str(governance.payload)
        environment["COPILOT_PLUGIN_INSTALL_STAGED"] = (
            "context-install" if governance.context else "1"
        )
        environment["AGENT_WORKTREES_RUNTIME_INPUTS"] = str(content)
        import agent_procutil

        try:
            completed = engine.run_owned(
                argv, cwd=Path(governance.root.anchor), env=environment, timeout=timeout,
                procutil=agent_procutil, construction_fd=descriptor,
                runtime_root=governance.root, runtime_version=slot.name,
                runtime_path=Path(runtime.__file__),
            )
        except subprocess.CalledProcessError as error:
            if error.stderr:
                print(error.stderr, end="", file=sys.stderr, flush=True)
            raise
        if completed.stdout:
            print(completed.stdout, end="", flush=True)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr, flush=True)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result_state = publication.state_path(result_path, private=True)
        if result_state is None or result != {
            "version": 1, "prepared": True, "plan_sha256": digest,
            "file_identity": [result_state.device, result_state.inode],
        }:
            raise InstallationRefused("Native helper publication returned an invalid acknowledgement")
        return helper_publication.publish(
            plan, directory,
            {plan_path: (plan_state.device, plan_state.inode),
             copies_path: (copies_state.device, copies_state.inode),
             result_path: (result_state.device, result_state.inode)},
            validate_inputs=lambda: helper_publication_plan(plan_path, digest),
        )


def _load_installed(slot: Path) -> None:
    if Path(sys.prefix).resolve() != slot.resolve():
        raise InstallationRefused("Publication must execute under the retained candidate interpreter")
    import agent_worktrees

    if not Path(agent_worktrees.__file__).resolve().is_relative_to(slot.resolve()):
        raise InstallationRefused("Installer lifecycle resolved outside the retained candidate")
    from agent_worktrees import __main__ as core

    if not Path(core.__file__).resolve().is_relative_to(slot.resolve()):
        raise InstallationRefused("CLI health resolved outside the retained candidate")
    core._load_full_command_surface()


def _selected(root: Path) -> str | None:
    python = runtime.resolve_python(root)
    if python is None or not python.is_absolute() or ".." in python.parts:
        return None
    environment = python.parent.parent
    version = environment.parent.name if environment.name == ".venv" else environment.name
    return version if runtime.slot_python(root, version) == python else None


def _selector_snapshot(root: Path) -> dict[str, bytes | None]:
    result = {}
    for name in ("current-version", "last-known-good"):
        try:
            result[name] = (root / name).read_bytes()
        except FileNotFoundError:
            result[name] = None
    return result


def _restore_selectors(root: Path, version: str, snapshot: dict[str, bytes | None]) -> None:
    with runtime._publication_lease(root):
        observed = _selector_snapshot(root)
        for name, content in observed.items():
            if content not in (
                snapshot[name], (version + "\n").encode("utf-8"), (version + "\r\n").encode("utf-8"),
            ):
                raise InstallationRefused("Runtime selection changed before rollback")
        for name, content in snapshot.items():
            path = root / name
            if content is None:
                path.unlink(missing_ok=True)
            else:
                temporary = path.with_name(f"{path.name}.rollback-{os.getpid()}")
                temporary.write_bytes(content)
                os.replace(temporary, path)


def _monitor_identity(root: Path) -> tuple[int, str] | None:
    from agent_worktrees import locks, status_monitor_cutover as cutover
    from agent_worktrees.runtime_monitor_identity import AsyncRetirementUnsupported

    try:
        routed = cutover.routed_monitor_identity(root, require_async_retirement=True)
    except AsyncRetirementUnsupported as error:
        routed = error.identity
    except (OSError, ValueError, ConnectionError) as error:
        raise InstallationRefused(f"Asynchronous predecessor retirement is unavailable: {error}") from error
    if routed is not None:
        return routed[:2]
    lock_record = locks.read_lock(root / "status-monitor.lock")
    if locks.lock_is_live(lock_record):
        from agent_worktrees.runtime_monitor_retirements import retirement_only

        if retirement_only(root, lock_record):
            return None
        raise InstallationRefused("Live unrouted monitor cannot safely retire asynchronously")
    return None


def _candidate_serving(root: Path, slot: Path) -> bool:
    from agent_worktrees.runtime_monitor_identity import candidate_serving

    return candidate_serving(root, slot)


def _monitor_slot(root: Path, identity: tuple[int, str]) -> str | None:
    from agent_worktrees.runtime_monitor_identity import monitor_slot

    try:
        return monitor_slot(root, identity)
    except (OSError, ValueError) as error:
        raise InstallationRefused(str(error)) from error


def _settle_monitor(
    root: Path, slot: Path, summary: dict,
    predecessor: tuple[int, str] | None, handles: list[subprocess.Popen],
) -> bool:
    from agent_worktrees.runtime_monitor_settlement import settle_monitor

    return settle_monitor(
        root, slot, summary, predecessor, handles,
        candidate_serving=_candidate_serving, refused=InstallationRefused,
    )


def _source_provenance(
    payload: Path, version: str, source_identity: runtime_source.SourceIdentity | None = None,
) -> dict[str, str]:
    provenance = {"version": version, "source": payload.as_posix()}
    for field, arguments in (
        ("commit", ["rev-parse", "HEAD"]),
        ("branch", ["rev-parse", "--abbrev-ref", "HEAD"]),
    ):
        if source_identity is not None:
            provenance[field] = source_identity.source_commit if field == "commit" else "unknown"
            continue
        if shutil.which("git") is None:
            provenance[field] = "unknown"
            continue
        result = subprocess.run(
            ["git", "-C", str(payload), *arguments],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode != 0:
            if "not a git repository" not in result.stderr:
                raise InstallationRefused(f"Source provenance lookup failed: {result.stderr}")
            provenance[field] = "unknown"
        else:
            provenance[field] = result.stdout.strip()
    kind = "marketplace" if "/.copilot/installed-plugins/" in payload.as_posix() else "local"
    dirty = None
    if source_identity is None and kind == "local" and provenance["commit"] != "unknown":
        status = subprocess.run(
            ["git", "-C", str(payload), "status", "--porcelain", "--", str(payload)],
            capture_output=True, text=True, timeout=30, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        dirty = bool(status.stdout.strip())
    if source_identity is not None:
        provenance["commit"] = source_identity.source_commit
    provenance["deployment_source"] = json.dumps({
        "kind": kind, "path": payload.as_posix(), "repo": "copilot-extensions",
        "plugin": "agent-worktrees", "version": version,
        "commit": provenance["commit"] if kind == "local" else None,
        "branch": provenance["branch"] if kind == "local" else None, "dirty": dirty,
        **runtime_source.manifest_fields(source_identity),
    })
    return provenance


def _installed_identity(slot: Path) -> dict:
    return install_driver.installed_identity(slot, runtime, InstallationRefused)


@install_driver.owned
def install(
    root: Path, plugin: Path, *, payload: Path, uv: Path, base_python: Path,
    wait_timeout: float = 180, child_timeout: float = 180,
    expected_source_identity: dict | None = None,
    frontend: dict | None = None,
    recover_adopted_manager: bool = False,
    wheel_inputs: engine.WheelInstallInputs | None = None,
    native_wheel_inputs: engine.WheelInstallInputs | None = None,
    repair: bool = False,
    source_descriptor: dict | None = None,
    input_verifier=None,
) -> dict:
    if base_python.resolve(strict=True) != Path(sys.executable).resolve():
        raise InstallationRefused("The lifecycle driver must run under the selected base interpreter")
    if input_verifier is not None:
        if not isinstance(input_verifier, verification.LiveInputVerifier):
            raise InstallationRefused("Installation requires its live input-verifier owner")
        verification.require_source_contract(plugin)
    package_version = tomllib.loads(
        (plugin / "pyproject.toml").read_text(encoding="utf-8"),
    )["project"]["version"]
    source_identity = runtime_source.read_source_identity(
        plugin, package_version, plugin="agent-worktrees",
    )
    if expected_source_identity is not None and (
        runtime_source.manifest_fields(source_identity) != expected_source_identity
    ):
        raise InstallationRefused("Source identity changed after frontend runtime selection")
    version = source_identity.runtime_version if source_identity is not None else package_version
    wheel_options = {}
    if native_wheel_inputs is not None:
        if wheel_inputs is not None or source_identity is not None:
            raise InstallationRefused("Native release wheels cannot replace a Source association")
        version, wheel_options = install_driver.native_construction_options(plugin, native_wheel_inputs)
    policy = None
    if wheel_inputs is not None:
        if source_identity is None or not isinstance(wheel_inputs, engine.WheelInstallInputs):
            raise InstallationRefused("Closed source wheels require an attributed retained source selection")
        wheel_inputs.verify()
        receipt = wheel_inputs.receipt()
        policy = runtime_profile_policy(receipt, source_identity)
        profile = receipt["binding"].get("profile", receipt["binding"])
        if profile.get("mode") == "artifact":
            if policy is None or input_verifier is None:
                raise InstallationRefused("Artifact wheels require their live admission owner")
        elif profile.get("component") != "agent-worktrees" or profile.get("release") != {
            "identity": "source:" + source_identity.source_commit,
            "sha256": "sha256:" + source_identity.assembly_hash,
        } or profile.get("mode") != "source":
            raise InstallationRefused("Closed wheel profile differs from its Worktrees source")
        version += ".profile." + receipt["lock_id"][7:23]
        wheel_options = {
            "wheel_inputs": wheel_inputs,
            "local_packages": tuple(sorted(
                engine._package_name(package) for package in engine._package_roots(plugin)
                if engine._package_name(package) in receipt["distributions"]
            )),
        }
    if repair:
        if wheel_inputs is None and native_wheel_inputs is None:
            raise InstallationRefused("Direct source repair requires complete wheel inputs")
        version += ".repair." + uuid.uuid4().hex
    governance = Governance(
        root, payload,
        **({"source_descriptor": source_descriptor} if source_descriptor is not None else {}),
        **({"runtime_profile_policy": policy, "runtime_profile_mode": profile["mode"]}
           if policy is not None else {}),
        **({"runtime_input_verification": input_verifier.identity, "verify_inputs": input_verifier.check}
           if input_verifier is not None else {}),
    )
    os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(governance.payload)
    root = governance.root
    provenance = _source_provenance(governance.payload, package_version, source_identity)
    print(f"Runtime {version}: admitting frozen inputs", flush=True)
    with ExitStack() as custody:
        previous = _selected(root)
        if previous is not None:
            custody.enter_context(runtime.retention_lease(root, previous, timeout=wait_timeout))
        try:
            with governance.held(timeout=wait_timeout):
                if source_identity is not None:
                    plugin = runtime_source.publish_source_snapshot(plugin, root, source_identity)
                if source_identity is not None or native_wheel_inputs is not None:
                    version = install_driver.selected_repair(root, version, previous, source_identity, governance)
                candidate = custody.enter_context(engine.numbered_candidate(
                    root, version, plugin=plugin, uv=uv, base_python=base_python,
                    health_module="agent_worktrees.runtime_candidate", health_callable="verify_runtime_candidate",
                    construction_callable="agent_worktrees.runtime_candidate:prepare_runtime_candidate",
                    construction_data=provenance,
                    process_retention=True,
                    reuse_generations=source_identity is None and not wheel_options,
                    expected_source_identity=runtime_source.manifest_fields(source_identity),
                    wait_timeout=wait_timeout, child_timeout=child_timeout,
                    **({"validate_candidate": lambda _slot: governance.recheck()}
                       if policy is not None else {}),
                    **wheel_options,
                ))
        except engine.HealthRepairRequired:
            if source_identity is None:
                raise
            if wheel_inputs is not None:
                return install(
                    root, plugin, payload=payload, uv=uv, base_python=base_python,
                    wait_timeout=wait_timeout, child_timeout=child_timeout,
                    expected_source_identity=runtime_source.manifest_fields(source_identity),
                    frontend=frontend, recover_adopted_manager=recover_adopted_manager,
                    wheel_inputs=wheel_inputs, repair=True,
                    source_descriptor=source_descriptor,
                    input_verifier=input_verifier,
                )
            import runtime_install_modes as modes

            return modes.run(
                root, plugin, payload=payload, uv=uv, base_python=base_python, mode="repair",
                wait_timeout=wait_timeout, child_timeout=child_timeout,
                expected_source_identity=runtime_source.manifest_fields(source_identity),
                frontend=frontend, recover_adopted_manager=recover_adopted_manager,
            )
        version = candidate.slot.name
        print(
            f"Runtime {version}: candidate healthy (reused={candidate.reused}); "
            "retaining through publication", flush=True,
        )
        with tempfile.TemporaryDirectory(dir=root / ".installer-inputs", prefix="transition-") as temporary:
            result_path = Path(temporary) / "result.json"
            argv = [
                str(runtime.slot_python(root, version)), "-I", "-B",
                str(candidate.slot / ".runtime-bootstrap" / "runtime_install.py"),
                "--root", str(root), "--plugin", str(plugin), "--payload", str(payload),
                "--uv", str(uv), "--base-python", str(base_python), "--result", str(result_path),
                "--settle-slot", version, "--payload-hash", candidate.payload_hash,
                "--governance-generation", json.dumps(governance.signature()),
                "--wait-timeout", str(wait_timeout), "--package-version", package_version,
            ]
            if frontend is not None:
                content = plugin if source_identity is not None else candidate.frozen_plugin
                argv.extend(["--frontend", json.dumps(frontend),
                             "--publication-source-root", str(content),
                             "--child-timeout", str(child_timeout)])
            if previous is not None:
                argv.extend(["--previous-version", previous])
            if recover_adopted_manager:
                argv.append("--recover-adopted-manager")
            environment = install_driver.child_environment()
            for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
                environment.pop(name, None)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = verification.wait_for_settlement(argv, environment, verifier=input_verifier)
            stdout, stderr = completed.stdout, completed.stderr
            if stdout:
                print(stdout, end="", flush=True)
            if stderr:
                print(stderr, end="", file=sys.stderr, flush=True)
            if completed.returncode != 0:
                raise subprocess.CalledProcessError(completed.returncode, argv, stdout, stderr)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["reused"] = candidate.reused
            return result


@install_driver.owned
def settle_candidate(
    root: Path, version: str, *, payload: Path, payload_hash: str,
    expected_governance: dict, previous: str | None = None, wait_timeout: float = 180,
    cutover_guard=None, lifecycle_slot: Path | None = None, package_version: str | None = None,
    force_monitor_transition: bool = False,
    dev_owner: str | None = None,
    frontend: dict | None = None, publication_source_root: Path | None = None,
    publication_payload_hash: str | None = None,
    child_timeout: float = 180,
    base_python: Path | None = None,
    governance: Governance | None = None,
    recover_adopted_manager: bool = False,
    release_dev_claim: dict | None = None,
) -> dict:
    governance = governance if governance is not None else Governance(
        root, payload,
        **({"source_descriptor": expected_governance["source_descriptor"]}
           if "source_descriptor" in expected_governance else {}),
        **({"runtime_profile_policy": expected_governance["runtime_profile_policy"]}
           if "runtime_profile_policy" in expected_governance else {}),
        **({"runtime_profile_mode": expected_governance["runtime_profile_mode"]}
           if "runtime_profile_mode" in expected_governance else {}),
        **({"runtime_input_verification": expected_governance["runtime_input_verification"]}
           if "runtime_input_verification" in expected_governance else {}),
    )
    if governance.signature() != expected_governance:
        raise InstallationRefused("Installation governance changed before candidate handover")
    root = governance.root
    os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"] = str(governance.payload)
    with ExitStack() as custody:
        candidate_slot, descriptor = custody.enter_context(runtime.retention_custody(
            root, version, payload_hash=payload_hash,
        ))
        if previous is not None:
            custody.enter_context(runtime.retention_lease(root, previous))
        print(f"Runtime {version}: verifying publication under its own interpreter", flush=True)
        _load_installed(lifecycle_slot if lifecycle_slot is not None else candidate_slot)
        if recover_adopted_manager:
            from agent_worktrees.manager_recovery import recover

            recover()
        from agent_worktrees import (
            config as cfg, status_monitor_cutover as cutover, runtime_deployment as deployment,
        )
        from agent_worktrees.runtime_monitor_settlement import PublicationOutcomePending
        from agent_worktrees.runtime_monitor_identity import AsyncRetirementUnsupported

        identity = _installed_identity(candidate_slot)
        if package_version is not None:
            from packaging.version import Version

            if Version(identity["package_version"]) != Version(package_version):
                raise InstallationRefused("Retained runtime differs from the requested package version")
        manifest = deployment.manifest_bytes(identity, candidate_slot, payload_hash)

        monitor_scope = cfg.install_dir().resolve() == root
        with governance.held(timeout=wait_timeout), ExitStack() as cutover_custody:
            manifest = cutover_custody.enter_context(governance.manifest_publication(manifest))
            guard = cutover_guard
            if guard is None:
                guard = cutover._acquire_cutover_lock(root / "status-monitor-routing")
                cutover_custody.callback(guard.release)
            # Waiting does not extend borrowed management authority.
            governance.recheck()
            print(f"Runtime {version}: cutover authority acquired", flush=True)
            selected = _selected(root)
            if selected is not None and selected != previous:
                custody.enter_context(runtime.retention_lease(root, selected, timeout=wait_timeout))
            snapshot = _selector_snapshot(root)
            manifest_snapshot = deployment.read_manifest(root)
            outgoing_deferred = False
            if monitor_scope:
                from agent_worktrees import runtime_monitor_replacement as replacement
                from zdd import breadcrumb

                replacement.active(root)
                outgoing_deferred = replacement.recover(root, runtime=runtime)
                if breadcrumb.breadcrumb_path(root / "status-monitor-routing").exists():
                    outgoing_deferred = replacement.retain(root, runtime=runtime) or outgoing_deferred
            try:
                predecessor = _monitor_identity(root) if monitor_scope else None
            except AsyncRetirementUnsupported as error:
                predecessor = error.identity[:2]
            except InstallationRefused:
                replacement.retain(root, runtime=runtime)
                outgoing_deferred = True
                predecessor = None
            serving_slot = None
            if predecessor is not None:
                try:
                    serving_slot = _monitor_slot(root, predecessor)
                except InstallationRefused:
                    replacement.retain(root, runtime=runtime, predecessor={
                        "pid": predecessor[0], "start": predecessor[1],
                    })
                    outgoing_deferred = True
                if serving_slot is not None and serving_slot not in (version, previous, selected):
                    custody.enter_context(runtime.retention_lease(root, serving_slot, timeout=wait_timeout))
            try:
                already_serving = (
                    monitor_scope and not force_monitor_transition
                    and _candidate_serving(root, candidate_slot)
                )
            except (OSError, ValueError, ConnectionError):
                already_serving = False
            print(
                f"Runtime {version}: monitor scope={monitor_scope} "
                f"predecessor={predecessor} already-serving={already_serving}", flush=True,
            )
            governance.recheck()
            helpers = None
            if frontend is not None:
                if publication_source_root is None:
                    raise InstallationRefused("Native helper publisher has no immutable content root")
                if base_python is None:
                    raise InstallationRefused("Native helper publisher has no admitted base interpreter")
                helpers = _publish_helpers(
                    governance, frontend, publication_source_root, candidate_slot,
                    descriptor, timeout=child_timeout, base_python=base_python,
                    expected_payload_hash=publication_payload_hash,
                )
            handles: list[subprocess.Popen] = []
            summary = {"action": "interrupted", "self_retirement": True}
            retiring_dev = serving_slot == "dev" or (
                serving_slot is not None and runtime.is_dev_generation(serving_slot)
            )
            retirement_predecessor = (
                {
                    "pid": predecessor[0], "start": predecessor[1], "slot": serving_slot,
                    "release_claim": release_dev_claim if retiring_dev else None,
                } if predecessor is not None and not already_serving else None
            )
            deferred_release = release_dev_claim is not None and not retiring_dev and (
                outgoing_deferred or predecessor is not None and not already_serving
            )
            if deferred_release:
                replacement.retain(
                    root, runtime=runtime, release_claim=release_dev_claim,
                    predecessor=retirement_predecessor,
                )
                outgoing_deferred = True
            try:
                if retirement_predecessor is not None:
                    from agent_worktrees import runtime_monitor_retirements as retirements

                    retirements.reserve(root, retirement_predecessor, runtime=runtime, committed=False)
                if helpers is not None:
                    helpers.verify()
                governance.recheck()
                runtime.activate(root, version, link_free=True, dev_owner=dev_owner)
                print(f"Runtime {version}: selection published", flush=True)
                committed = True
                if monitor_scope and not already_serving:
                    summary = cutover.activate_after_update(
                        runtime_python=str(runtime.slot_python(root, version)),
                        monitor_was_live=predecessor is not None,
                        cutover_guard=guard,
                        spawned_handles=handles,
                        transition_custody=custody,
                        asynchronous_retirement=True,
                        retirement_predecessor=retirement_predecessor,
                    )
                    summary["self_retirement"] = True
                    if outgoing_deferred:
                        summary.setdefault("result", {})["retirement_pending"] = True
                    print(
                        f"Runtime {version}: monitor transition action={summary.get('action')} "
                        f"reason={summary.get('reason')} result={summary.get('result')}", flush=True,
                    )
                    committed = _settle_monitor(root, candidate_slot, summary, predecessor, handles)
                if not committed:
                    raise InstallationRefused("Monitor cutover rolled back; previous selection restored")
                if summary.get("action") == "cutover" and (summary.get("result") or {}).get("ok") is not True:
                    raise InstallationRefused(
                        f"Monitor activation failed after settlement: {(summary.get('result') or {}).get('error')}"
                    )
                if deferred_release:
                    replacement.settle_release(root, release_dev_claim, committed=True)
                deployment.publish(root, manifest)
            except PublicationOutcomePending:
                if helpers is not None:
                    helpers.keep()
                raise
            except BaseException:
                if summary.get("action") == "interrupted" and not handles:
                    summary["action"] = "preparing"
                announced = False
                while True:
                    try:
                        committed = False
                        if monitor_scope:
                            committed = _settle_monitor(
                                root, candidate_slot, summary, predecessor, handles,
                            )
                        if not committed:
                            _restore_selectors(root, version, snapshot)
                            deployment.restore(root, manifest_snapshot, manifest)
                            if retirement_predecessor is not None:
                                from agent_worktrees import runtime_monitor_retirements as retirements

                                retirements.cancel(root, retirement_predecessor, runtime=runtime)
                            if deferred_release:
                                replacement.settle_release(root, release_dev_claim, committed=False)
                        elif any(content not in (
                            (version + "\n").encode("utf-8"), (version + "\r\n").encode("utf-8"),
                        ) for content in _selector_snapshot(root).values()):
                            runtime.activate(root, version, link_free=True, dev_owner=dev_owner)
                        if committed:
                            if deferred_release:
                                replacement.settle_release(root, release_dev_claim, committed=True)
                            deployment.publish(root, manifest)
                        break
                    except PublicationOutcomePending:
                        if helpers is not None:
                            helpers.keep()
                        raise
                    except BaseException as error:
                        if not announced:
                            print(
                                f"Publication settlement interrupted ({type(error).__name__}: {error}); "
                                "retaining both generations until its actual outcome",
                                file=sys.stderr, flush=True,
                            )
                            announced = True
                        time.sleep(0.2)
                if helpers is not None:
                    if committed:
                        helpers.keep()
                    else:
                        helpers.rollback()
                raise
            else:
                if helpers is not None:
                    helpers.keep()
        return {
            "runtime_version": version,
            "package_version": identity["build_info"]["version"],
            "runtime_generation": identity["build_info"].get("runtime_generation"),
            "deployment_manifest_published": True,
            "helpers_published": helpers is not None,
            "retirement_pending": outgoing_deferred or (summary.get("result") or {}).get("retirement_pending") is True,
            "dev_claim_release_pending": (
                release_dev_claim is not None and (retiring_dev or outgoing_deferred)
                and (outgoing_deferred or (summary.get("result") or {}).get("retirement_pending") is True)
            ),
            "runtime_python": str(runtime.slot_python(root, version)),
            "payload_hash": payload_hash,
        }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--plugin", type=Path)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--base-python", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--wait-timeout", type=float, default=180)
    parser.add_argument("--child-timeout", type=float, default=180)
    parser.add_argument("--settle-slot")
    parser.add_argument("--payload-hash")
    parser.add_argument("--previous-version")
    parser.add_argument("--package-version")
    parser.add_argument("--expected-source-identity", type=json.loads)
    parser.add_argument("--frontend", type=json.loads)
    parser.add_argument("--recover-adopted-manager", action="store_true")
    parser.add_argument("--publication-source-root", type=Path)
    parser.add_argument("--inspect-helper-publication", type=Path)
    parser.add_argument("--complete-helper-publication", type=Path)
    parser.add_argument("--publication-digest")
    parser.add_argument("--governance-generation", type=json.loads)
    parser.add_argument("--mode", choices=("ordinary", "repair", "dev", "dev-release"), default="ordinary")
    parser.add_argument("--owner", type=Path)
    parser.add_argument("--mode-owner", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--install-source", type=Path)
    parser.add_argument("--wheel-profile", type=Path)
    parser.add_argument("--wheel-directory", type=Path)
    parser.add_argument("--input-timeout", type=install_driver.positive_input_timeout)
    parser.add_argument("--construction-provenance", type=json.loads, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    if arguments.input_timeout is not None and not (arguments.install_source or arguments.wheel_profile):
        parser.error("--input-timeout requires --install-source or --wheel-profile")
    required = ("root", "plugin") if (
        arguments.inspect_helper_publication or arguments.complete_helper_publication
    ) else ("root", "plugin", "payload", "uv", "base_python", "result")
    if any(getattr(arguments, name) is None for name in required):
        parser.error("required arguments: " + ", ".join("--" + name.replace("_", "-") for name in required))
    if arguments.wheel_profile is not None or arguments.wheel_directory is not None:
        if (
            arguments.wheel_profile is None or arguments.wheel_directory is None
            or arguments.install_source is not None or arguments.mode not in ("ordinary", "repair")
            or any((arguments.mode_owner, arguments.settle_slot,
                    arguments.inspect_helper_publication, arguments.complete_helper_publication))
        ):
            parser.error("closed wheels require a complete independent ordinary installation input")
        from install_source import wheel_inputs

        with wheel_inputs(
            arguments.wheel_profile, arguments.wheel_directory,
            deadline=time.monotonic() + (arguments.input_timeout or 600),
        ) as wheels:
            result = install(
                arguments.root, arguments.plugin, payload=arguments.payload,
                uv=arguments.uv, base_python=arguments.base_python,
                wait_timeout=arguments.wait_timeout, child_timeout=arguments.child_timeout,
                expected_source_identity=arguments.expected_source_identity,
                frontend=arguments.frontend,
                recover_adopted_manager=arguments.recover_adopted_manager,
                native_wheel_inputs=wheels, repair=arguments.mode == "repair",
            )
    elif arguments.install_source:
        if arguments.mode not in ("ordinary", "repair") or any((
            arguments.mode_owner, arguments.settle_slot,
            arguments.inspect_helper_publication, arguments.complete_helper_publication,
        )):
            parser.error("--install-source selects only ordinary installation or independent repair")
        from install_source import project_inputs

        input_timeout = 600 if arguments.input_timeout is None else arguments.input_timeout
        input_deadline = time.monotonic() + input_timeout
        with project_inputs(
            arguments.install_source, "agent-worktrees", deadline=input_deadline,
        ) as (selected, wheels):
            if runtime_source.read_source_identity(
                arguments.plugin, selected.identity.package_version, plugin="agent-worktrees",
            ) != selected.identity:
                raise InstallationRefused("Invocation payload differs from the selected installation source")
            result = install(
                arguments.root, selected.payload, payload=arguments.payload,
                uv=arguments.uv, base_python=arguments.base_python,
                wait_timeout=arguments.wait_timeout, child_timeout=arguments.child_timeout,
                expected_source_identity=arguments.expected_source_identity,
                frontend=arguments.frontend,
                recover_adopted_manager=arguments.recover_adopted_manager,
                wheel_inputs=wheels, repair=arguments.mode == "repair",
                **({"source_descriptor": selected.source_descriptor}
                   if getattr(selected, "source_descriptor", None) is not None else {}),
            )
    elif arguments.inspect_helper_publication or arguments.complete_helper_publication:
        path = arguments.inspect_helper_publication or arguments.complete_helper_publication
        plan = helper_publication_plan(path, arguments.publication_digest, prepare_only=True)
        if Path(plan["root"]).resolve() != arguments.root.resolve():
            raise InstallationRefused("Helper publication runtime root differs from its invocation")
        try:
            same_content = os.path.samefile(
                engine._native_retained_path(plan["content"]),
                engine._native_retained_path(arguments.plugin),
            )
        except OSError as error:
            raise InstallationRefused("Helper publication content root is unavailable") from error
        if not same_content:
            raise InstallationRefused("Helper publication content root differs from its invocation")
        if arguments.inspect_helper_publication:
            print(json.dumps(plan))
        else:
            destination = Path(plan["result"])
            with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                created = os.fstat(stream.fileno())
                json.dump({"version": 1, "prepared": True,
                           "plan_sha256": arguments.publication_digest,
                           "file_identity": [created.st_dev, created.st_ino]}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        return 0
    elif arguments.settle_slot:
        if arguments.payload_hash is None or arguments.governance_generation is None:
            parser.error("candidate handover requires its content and governance generations")
        result = settle_candidate(
            arguments.root, arguments.settle_slot, payload=arguments.payload,
            payload_hash=arguments.payload_hash, expected_governance=arguments.governance_generation,
            previous=arguments.previous_version, wait_timeout=arguments.wait_timeout,
            package_version=arguments.package_version,
            frontend=arguments.frontend, publication_source_root=arguments.publication_source_root,
            child_timeout=arguments.child_timeout,
            base_python=arguments.base_python,
            recover_adopted_manager=arguments.recover_adopted_manager,
        )
    elif arguments.mode != "ordinary":
        import runtime_install_modes as modes

        if arguments.mode_owner and arguments.governance_generation is None:
            parser.error("whole-operation owner requires its original governance generation")
        mode_arguments = (
            {"expected_governance": arguments.governance_generation}
            if arguments.mode_owner else {}
        )
        mode_arguments["recover_adopted_manager"] = arguments.recover_adopted_manager
        if arguments.mode_owner and arguments.payload_hash is not None:
            mode_arguments["expected_payload_hash"] = arguments.payload_hash
        if arguments.construction_provenance is not None:
            if not arguments.mode_owner:
                parser.error("construction provenance requires a whole-operation owner")
            mode_arguments["construction_provenance"] = arguments.construction_provenance
        if not arguments.mode_owner:
            mode_arguments["expected_source_identity"] = arguments.expected_source_identity
        if arguments.frontend is not None:
            mode_arguments["frontend"] = arguments.frontend
        if arguments.publication_source_root is not None:
            mode_arguments["publication_source_root"] = arguments.publication_source_root
        try:
            result = (modes.run_owned if arguments.mode_owner else modes.run)(
                arguments.root, arguments.plugin, payload=arguments.payload,
                uv=arguments.uv, base_python=arguments.base_python, mode=arguments.mode,
                owner=arguments.owner,
                wait_timeout=arguments.wait_timeout, child_timeout=arguments.child_timeout,
                **mode_arguments,
            )
        except runtime.DevClaimConflict as error:
            print(str(error), file=sys.stderr)
            return 2
    else:
        result = install(
            arguments.root, arguments.plugin, payload=arguments.payload,
            uv=arguments.uv, base_python=arguments.base_python,
            wait_timeout=arguments.wait_timeout, child_timeout=arguments.child_timeout,
            expected_source_identity=arguments.expected_source_identity,
            frontend=arguments.frontend,
            recover_adopted_manager=arguments.recover_adopted_manager,
        )
    temporary = arguments.result.with_name(f"{arguments.result.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(result) + "\n", encoding="utf-8")
    os.replace(temporary, arguments.result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        verification.close_clients()
