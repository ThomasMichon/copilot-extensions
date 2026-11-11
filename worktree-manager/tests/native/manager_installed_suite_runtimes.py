"""Fresh suite construction through the installed Manager's ordinary update.

The caller owns the disposable Windows VM, frozen payload delivery and native
settlement. This companion neither selects an upgrade baseline nor grants source
admission. Its transport commit identifies fixture bytes, not a released source.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import tomllib

from packaging.version import Version


_PORTFOLIO = """
import json,sys
from agent_worktrees import config,reconcile,update_runtime,update_runtime_plan
config.set_active_project(sys.argv[1])
payload=reconcile.core_installed_payload_dir('agent-worktrees')
engine,modules,registered=update_runtime_plan.plan_portfolio(
    payload,'windows',None,force=True,
    targets=update_runtime._registered_plugin_targets(),force_runtimes=())
steps=([engine] if engine is not None else [])+list(modules)+list(registered)
print(json.dumps({step.name:{
    'eligible':step.eligible,'status':step.status,
    'root':str(step.runtime_root) if step.runtime_root else None,
    'payload':str(step.payload)} for step in steps}))
"""

_ORIGIN = """
import importlib,importlib.metadata,json,sys
module=importlib.import_module(sys.argv[1])
print(json.dumps({'prefix':sys.prefix,'module':module.__file__,
                  'version':importlib.metadata.version(sys.argv[2])}))
"""

_READINESS = """
import json,sys,time
from agent_procutil.owned_process import OwnedExecutionBudget,run_caller_owned
from agent_worktrees import application_admission,config
config.set_active_project(sys.argv[1])
selected=config.load_config()
if selected.default_repo.require_application_readiness is not True:
    raise RuntimeError('Required application readiness was not enabled')
budget=OwnedExecutionBudget.from_timeout(60,cleanup_reserve=5)
prefix,environment=application_admission._selected_runtime(budget=budget)
result=run_caller_owned(
    [*prefix,'installation-repair','--project-root',sys.argv[2],
     '--required','--caller-owned-readiness'],
    cwd=sys.argv[2],env=environment,budget=budget,
    timeout=budget.execution_deadline-time.monotonic(),
    max_stdout_bytes=1024*1024,max_stderr_bytes=65536)
print(json.dumps({'prefix':prefix,'accepted':application_admission._ready(result,sys.argv[2]),
                  'readiness':json.loads(result.stdout)}))
"""

_READINESS_DETAILS = """
import json,sys,time
from pathlib import Path
from agent_machines.installation_repair import _binding_inputs,_invoke,current_platform
from agent_machines.installation_repair_admission import admit_host
from agent_machines.installation_repair_process import caller_owned_readiness
requested=set(json.loads(sys.argv[2]))
deadline=time.monotonic()+30
host=admit_host(Path(sys.argv[1]),readiness_only=True)
platform=current_platform()
modules=[module for module in host.report.modules if module.qualified_id in requested]
_inputs,owners=_binding_inputs(host,modules,platform)
with caller_owned_readiness(True):
    for module in modules:
        owner=owners.get(module.owner.owner_id)
        if owner is None or owner.refusal:
            print(json.dumps({'module':module.qualified_id,'status':'owner-not-admitted'}))
            continue
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise TimeoutError('Readiness diagnostics exhausted their existing stage budget')
        result=_invoke(module,owner,platform,readonly=True,timeout=min(10,remaining))
        print(json.dumps({'module':module.qualified_id,'status':result.status,
                          'exitCode':result.exit_code,
                          'stdout':result.stdout.decode('utf-8-sig',errors='replace')}),flush=True)
"""


def expected_runtimes(profile: dict) -> set[str]:
    if profile["repository_config"].get("require_application_readiness") is not True:
        raise ValueError("The complete suite must require actual application readiness")
    inventory = profile["inventory"]
    marketplaces = profile["settings"]["extraKnownMarketplaces"]
    if len(marketplaces) != 1:
        raise ValueError("The complete suite requires one frozen marketplace")
    marketplace = next(iter(marketplaces))
    if profile["settings"]["enabledPlugins"] != {
        name + "@" + marketplace: True for name in inventory
    }:
        raise ValueError("The complete frozen suite must remain enabled")
    names = {
        name for name, row in inventory.items() if row["runtime_scope"] != "none"
    }
    if not {"agent-worktrees", "agent-machines"} <= names:
        raise ValueError("The complete suite requires Worktrees and Machines runtimes")
    return names


def validate_portfolio(profile: dict, portfolio: dict, payloads: dict, home: Path) -> None:
    names = expected_runtimes(profile)
    if not isinstance(portfolio, dict) or not names <= set(portfolio):
        raise ValueError("The installed planner did not select the complete runtime portfolio")
    for name in set(portfolio) - names:
        row = portfolio[name]
        if (
            profile["inventory"].get(name, {}).get("runtime_scope") != "none"
            or row.get("eligible") is not False
            or row.get("status") not in ("payload-only", "SKIPPED (payload-only)")
            or row.get("root") is not None
            or Path(row.get("payload", "")).resolve() != Path(payloads[name]["path"]).resolve()
        ):
            raise ValueError(f"{name}: the installed planner returned an unexpected non-runtime")
    for name in sorted(names):
        row = portfolio[name]
        if (
            row.get("eligible") is not True
            or row.get("status") not in ("", "SKIPPED (current)")
            or not isinstance(row.get("root"), str)
            or not Path(row["root"]).is_absolute()
            or not Path(row["root"]).resolve().is_relative_to(home.resolve())
            or Path(row.get("payload", "")).resolve() != Path(payloads[name]["path"]).resolve()
        ):
            raise ValueError(f"{name}: the installed planner selected an ineligible or foreign runtime")


def validate_origin(origin: dict, expected_prefix: Path, version: str) -> None:
    prefix = Path(origin["prefix"])
    module = Path(origin["module"])
    if (
        not prefix.is_absolute() or not module.is_absolute()
        or prefix.resolve() != expected_prefix.resolve()
        or not module.resolve().is_relative_to(prefix.resolve())
        or Version(origin["version"]) != Version(version)
    ):
        raise ValueError("The runtime import/version did not come from its selected installed slot")


def observe_manager(
    artifact, candidate_commit, *, root, environment, project, diagnostics, run,
):
    """Verify fresh installed bytes without manufacturing source admission."""
    from manager_installed_suite_payloads import _PROVENANCE
    from manager_suite_fixture import _python, require_venue

    require_venue()
    _PROVENANCE["verify"](artifact, candidate_commit)
    home = Path(environment["USERPROFILE"])
    if (
        not home.is_absolute() or not home.resolve().is_relative_to(project.parent.resolve())
        or root.resolve() != (home / ".worktree-manager").resolve()
    ):
        raise ValueError("Manager observation requires its private fixture installation")
    generation = (root / "current-version").read_text(encoding="utf-8").strip()
    if not generation or Path(generation).name != generation or generation in {".", ".."}:
        raise ValueError("Manager observation requires one selected generation")
    slot = root / "versions" / generation
    if not slot.resolve().is_relative_to(root.resolve()):
        raise ValueError("Manager observation escaped its private installation")
    python = _python(root)
    payload = artifact / "worktree-manager"
    metadata = tomllib.loads((payload / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    observed = json.loads(run(
        [python, "-I", "-B", "-c", _ORIGIN, "worktree_manager", metadata["name"]],
        environment, project, diagnostics, "suite-manager-origin", timeout=30,
    ))
    validate_origin(observed, python.parent.parent, metadata["version"])
    module = Path(observed["module"])
    package = payload / "src" / "worktree_manager"
    modules = list(package.rglob("*.py"))
    if not modules or module.name != "__init__.py":
        raise ValueError("Manager observation requires its frozen candidate package")
    for path in modules:
        installed = module.parent / path.relative_to(package)
        if not installed.is_file() or installed.read_bytes() != path.read_bytes():
            raise ValueError("Manager installed different candidate module bytes")
    records = {}
    for name in (".install-complete.json", ".runtime-input-origins.json"):
        data = (slot / name).read_bytes()
        document = json.loads(data)
        if not isinstance(document, dict) or not document:
            raise ValueError("Manager has no completed constructor records")
        records[name] = hashlib.sha256(data).hexdigest()
    _PROVENANCE["verify"](artifact, candidate_commit)
    return {
        "candidate_commit": candidate_commit, "source_admission": "not-granted",
        "generation": generation, **observed, "constructor_records_present": True,
        "constructor_records": records, "candidate_bytes_verified": True,
        "verified_module_count": len(modules),
    }


def install_runtime_closure(
    artifact: Path, candidate_commit: str, *, payload_closure: dict,
    manager: Path, copilot: Path, environment: dict, project: Path,
    diagnostics: Path, deadline: float, run,
) -> dict:
    """Run the real normal updater, then observe every selected runtime."""
    from manager_installed_suite_payloads import _PROVENANCE, selected_profile
    from manager_suite_fixture import require_venue

    require_venue()
    if time.monotonic() >= deadline:
        raise TimeoutError("Suite construction exhausted the existing preparation budget")
    _PROVENANCE["verify"](artifact, candidate_commit)
    profile, payloads = payload_closure["profile"], payload_closure["payloads"]
    names = expected_runtimes(profile)
    if payload_closure["candidate_commit"] != candidate_commit or set(payloads) != set(profile["inventory"]):
        raise ValueError("Runtime construction requires the complete exact-candidate payload closure")
    home = Path(environment["USERPROFILE"])
    if not home.is_absolute() or not home.resolve().is_relative_to(project.parent.resolve()):
        raise ValueError("Suite construction requires the private fixture HOME")
    if not manager.is_absolute() or not manager.resolve().is_relative_to(project.parent.resolve()):
        raise ValueError("Suite construction requires its privately installed Manager")
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        if key in environment and Path(environment[key]).resolve() != home.resolve():
            raise ValueError("Suite construction has conflicting HOME ownership")
    for key, value in {
        "AGENT_WORKTREES_STATUS_MONITOR": "0",
        "AGENT_TEST_MODE": "1",
        "COPILOT_OFFLINE": "true",
    }.items():
        if environment.get(key) == value:
            raise ValueError("Full suite construction cannot suppress its ordinary consumers")
    selected_cli = shutil.which("copilot", path=environment["PATH"])
    if not selected_cli or Path(selected_cli).resolve() != copilot.resolve():
        raise ValueError("The normal updater must resolve the pinned actual Copilot CLI")
    if hashlib.sha256(copilot.read_bytes()).hexdigest() != payload_closure["copilot_sha256"]:
        raise ValueError("The native Copilot executable changed after payload acquisition")
    settings = project / ".github" / "copilot" / "settings.json"
    if json.loads(settings.read_text(encoding="utf-8")) != profile["settings"]:
        raise ValueError("Runtime construction lost the complete frozen tool profile")
    transport = payload_closure["transport"]
    if (
        selected_profile(
            artifact, candidate_commit, profile["repository_config"], transport=transport,
        ) != profile
        or not Path(transport["repository"]).resolve().is_relative_to(project.parent.resolve())
    ):
        raise ValueError("Runtime construction requires its private frozen transport/profile")
    template = Path(transport["checkout_template"])
    if (
        template != project.parent / "suite-checkout-template"
        or template.is_symlink()
        or (template / "info" / "attributes").read_bytes() != b"* -text\n"
    ):
        raise ValueError("Runtime construction lost its byte-preserving Git checkout template")

    def verify_inputs(phase):
        for name, row in payloads.items():
            payload = Path(row["path"])
            if not payload.resolve().is_relative_to(home.resolve()):
                raise ValueError(f"{name}: runtime construction lost its frozen installed payload")
            actual = _PROVENANCE["content_manifest"](payload)
            expected = _PROVENANCE["content_manifest"](artifact / "plugins" / name)
            if actual != expected:
                from manager_suite_diagnostics import _snapshot_difference

                try:
                    (diagnostics / f"suite-runtime-payload-difference-{name}-{phase}.json").write_text(
                        json.dumps(_snapshot_difference(expected, actual), sort_keys=True),
                        encoding="utf-8",
                    )
                except OSError as error:
                    print(f"{name}: payload difference diagnostic unavailable: {error}", file=sys.stderr)
                raise ValueError(f"{name}: runtime construction lost its frozen installed payload")
        observed = run(
            ["git", "--git-dir", transport["repository"], "rev-parse", "HEAD", "HEAD^{tree}"],
            environment, project, diagnostics, "suite-runtime-transport-" + phase,
            timeout=min(30, remaining()),
        )
        if observed.splitlines() != [transport["commit"], transport["tree"]]:
            raise ValueError("The frozen transport changed during runtime construction")
        if hashlib.sha256(copilot.read_bytes()).hexdigest() != payload_closure["copilot_sha256"]:
            raise ValueError("The actual CLI changed during runtime construction")

    environment = {
        **environment, "GIT_TEMPLATE_DIR": transport["checkout_template"],
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    }
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("Suite construction exhausted the existing preparation budget")
        return value

    verify_inputs("before")
    run(
        [manager, "-I", "-B", "-m", "worktree_manager", "source", "set",
         "--repo", transport["url"], "--ref", "main"],
        environment, project, diagnostics, "suite-manager-source", timeout=min(30, remaining()),
    )
    run(
        [manager, "-I", "-B", "-m", "worktree_manager", "update", "--project", project.name],
        environment, project, diagnostics, "suite-runtime-update", timeout=remaining(),
    )
    selected_engine = json.loads(run(
        [manager, "-I", "-B", "-c",
         "import json,os;from worktree_manager.engine_client import installed_engine_command;"
         "print(json.dumps(installed_engine_command(dict(os.environ))))"],
        environment, project, diagnostics, "suite-current-engine", timeout=min(30, remaining()),
    ))
    if (
        not isinstance(selected_engine, list) or not selected_engine
        or not Path(selected_engine[0]).resolve().is_relative_to(home.resolve())
    ):
        raise ValueError("The normal updater did not leave a privately selected installed engine")
    engine = Path(selected_engine[0])
    portfolio = json.loads(run(
        [engine, "-I", "-B", "-c", _PORTFOLIO, project.name],
        environment, project, diagnostics, "suite-runtime-portfolio", timeout=min(60, remaining()),
    ))
    validate_portfolio(profile, portfolio, payloads, home)
    observations = {}
    for name in sorted(names):
        root = Path(portfolio[name]["root"])
        generation = (root / "current-version").read_text(encoding="utf-8").strip()
        if not generation or Path(generation).name != generation or generation in {".", ".."}:
            raise ValueError(f"{name}: invalid installed generation")
        slot = root / "versions" / generation
        if not slot.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"{name}: selected slot escapes its installation")
        python = slot / ".venv" / "Scripts" / "python.exe"
        # Native application owners may use the established venv-as-slot layout.
        if not python.is_file():
            python = slot / "Scripts" / "python.exe"
        invocation = json.loads(
            (Path(payloads[name]["path"]) / "payload-invocation.json").read_text(encoding="utf-8"),
        )
        command = (invocation.get("commands") or [invocation])[0]
        metadata = tomllib.loads(
            (Path(payloads[name]["path"]) / "pyproject.toml").read_text(encoding="utf-8"),
        )["project"]
        origin = json.loads(run(
            [python, "-I", "-B", "-c", _ORIGIN, command["module"].split(".")[0], metadata["name"]],
            environment, project, diagnostics, "suite-runtime-origin-" + name,
            timeout=min(30, remaining()),
        ))
        validate_origin(origin, python.parent.parent, metadata["version"])
        observations[name] = {"root": str(root), "generation": generation, **origin}
    if engine.resolve() != (
        Path(observations["agent-worktrees"]["prefix"]) / "Scripts" / "python.exe"
    ).resolve():
        raise ValueError("The engine observer is not the selected installed engine incarnation")
    selection = json.loads(run(
        [engine, "-I", "-B", "-c", _READINESS, project.name, str(project)],
        environment, project, diagnostics, "suite-machines-readiness", timeout=min(75, remaining()),
    ))
    prefix = selection["prefix"]
    if (
        not isinstance(prefix, list) or not prefix
        or Path(prefix[0]).resolve() != (
            Path(observations["agent-machines"]["prefix"]) / "Scripts" / "python.exe"
        ).resolve()
    ):
        raise ValueError("Application readiness selected a different Machines installation")
    if selection["accepted"] is not True:
        failed = [
            row["module"] for row in selection["readiness"].get("outcomes", [])
            if (row.get("observedAfter") or row.get("observedBefore") or {}).get("state")
            not in {"ready", "configuration-empty"}
        ]
        if failed:
            from agent_procutil.owned_process import OwnedCleanupError

            try:
                run(
                    [prefix[0], "-I", "-B", "-c", _READINESS_DETAILS, str(project), json.dumps(failed)],
                    environment, project, diagnostics, "suite-readiness-failures",
                    timeout=min(45, remaining()),
                )
            except OwnedCleanupError:
                raise
            except Exception as error:
                raise ValueError(
                    "The selected Machines owner did not satisfy required application readiness"
                ) from error
        raise ValueError("The selected Machines owner did not satisfy required application readiness")
    verify_inputs("after")
    _PROVENANCE["verify"](artifact, candidate_commit)
    return {
        "candidate_commit": candidate_commit, "runtime_installation": "observed",
        "profile": profile, "runtimes": observations, "machines": selection["readiness"],
        "upgrade": "not-exercised", "source_admission": "not-granted",
    }
