"""Explicit maintenance of provider-declared local runtime prerequisites."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any, cast

from agent_procutil.owned_process import OwnedCleanupError

from . import _worktrees, driver, host_restore, mesh

def selected_dependencies(cwd: Path, machines: Path | None = None) -> tuple[str, ...]:
    """Resolve only drivers selected by the fleet, through normal activation."""
    if machines is not None:
        topology = driver.resolve_machines_file(cwd, machines)
    else:
        try:
            own = _worktrees.validate_context()
            selected_path = mesh.find_machines_file(cwd)
            if selected_path is None:
                external, status = _worktrees.state_root(cwd, own)
                if external is None:
                    if status == "not-required":
                        return ()
                    raise driver.DriverError(
                        f"external topology lookup is {status}; no empty fleet was assumed. "
                        "A standalone caller can supply --machines."
                    )
                selected_path = mesh._machines_file_at(external)
            if selected_path is None:
                raise driver.DriverError("no machines.yaml in the selected topology source")
            topology = selected_path.resolve()
        except (OSError, ValueError) as error:
            raise driver.DriverError(f"cannot resolve selected topology: {error}") from error
    requested: set[str] = set()
    modules: dict[driver.Driver, dict[str, Any]] = {}

    def include(selected: driver.Driver, key: str, entry: Any, stack: set[driver.Driver]) -> None:
        if selected in stack:
            raise driver.DriverError("selected driver composition contains a cycle")
        if selected not in modules:
            if len(modules) >= 256:
                raise driver.DriverError("selected driver composition exceeds its bound")
            path = driver.find_module(selected, cwd)
            modules[selected] = driver.load_module(path, selected)
        module = modules[selected]
        declaration = module.get("runtimeDependencies", [])
        if (
            not isinstance(declaration, list) or len(declaration) > 32
            or any(not isinstance(item, str) or not 1 <= len(item) <= 256 for item in declaration)
        ):
            raise driver.DriverError(f"driver {selected.label} runtimeDependencies is invalid")
        requested.update(declaration)
        composed = module.get("composesOutboundDrivers", False)
        if type(composed) is not bool:
            raise driver.DriverError(f"driver {selected.label} composesOutboundDrivers must be a boolean")
        if composed:
            outbound = entry["ssh"].get("outbound", [])
            if not isinstance(outbound, list) or len(outbound) > 32:
                raise driver.DriverError(f"machine '{key}' ssh.outbound must be a bounded driver list")
            for index, reference in enumerate(outbound):
                child = driver.parse_driver(
                    f"{key} outbound[{index}]", {"ssh": {"driver": reference}},
                )
                if child is None:
                    raise driver.DriverError(f"machine '{key}' ssh.outbound has an empty driver")
                include(child, key, entry, stack | {selected})

    for key, entry in driver.load_machines(topology).items():
        selected = driver.parse_driver(key, entry)
        if selected is not None:
            include(selected, key, entry, set())
    if len(requested) > 256:
        raise driver.DriverError("selected driver runtime dependencies exceed their bound")
    return tuple(sorted(requested))


def repair_command() -> tuple[list[str], dict[str, str]]:
    """Resolve the existing same-source Machines runtime without provisioning."""
    payload = host_restore.payload_root()
    spec = importlib.util.spec_from_file_location(
        "_ssh_dependency_runtime", payload / "scripts" / "snapshot_runtime.py",
    )
    if spec is None or spec.loader is None:
        raise driver.DriverError("canonical runtime resolver is unavailable")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return cast(
        tuple[list[str], dict[str, str]],
        adapter.peer_command(payload, _worktrees.validate_context(), "agent-machines"),
    )


def _request(command: list[str], environment: dict[str, str], cwd: Path, arguments: list[str]) -> dict[str, Any]:
    result = _worktrees.run_caller_owned(
        [*command, *arguments], cwd=cwd, env=environment, timeout=900,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=65536,
    )
    try:
        report = json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        detail = (
            "inspect before retrying an unknown apply"
            if "--apply" in arguments else "no apply was requested"
        )
        raise driver.DriverError(f"installation owner returned no valid result; {detail}") from error
    if result.returncode or not isinstance(report, dict):
        failure = report if isinstance(report, dict) else {"status": "invalid-result"}
        raise driver.DriverError(
            "installation owner refused dependency maintenance; inspect before retrying: "
            + json.dumps(failure, sort_keys=True)[-3000:]
        )
    return report


def _maintain(
    cwd: Path, *, machines: Path | None = None, apply: bool = False,
) -> tuple[dict[str, Any], bool]:
    attempted = False
    try:
        requested = selected_dependencies(cwd, machines)
        if not requested:
            return driver.make_result("ready", "no selected driver runtime dependencies"), attempted
        command, environment = repair_command()
        environment = dict(environment)
        environment["AGENT_MACHINES_NO_SELFPROVISION"] = "1"
        environment["COPILOT_PROJECT_DIR"] = str(cwd)
        try:
            capability = _request(
                command[:-2], environment, cwd,
                ["-c", "import importlib.resources,json\n"
                 "try:\n"
                 " value=importlib.resources.files('agent_machines').joinpath("
                 "'installation-repair-v1.json').read_text(encoding='utf-8')\n"
                 "except (FileNotFoundError,OSError):\n value='{}'\n"
                 "print(value)\n"],
            )
        except driver.DriverError as error:
            raise driver.DriverError(
                "installation owner lacks explicit refresh support; update its owning installation"
            ) from error
        if (
            capability.get("schema") != "copilot-extensions.installation-repair"
            or capability.get("version") != 1 or capability.get("refresh") != 1
        ):
            raise driver.DriverError("installation owner lacks explicit refresh support; update its owning installation")
        base = ["installation-repair", "--project-root", str(cwd), "--json"]
        discovered = _request(command, environment, cwd, base)
        available = discovered.get("available")
        if not isinstance(available, list) or any(not isinstance(item, str) for item in available):
            raise driver.DriverError("installation owner returned an invalid module inventory")
        selection: list[str] = []
        for dependency in requested:
            matches = [item for item in available if item.endswith("::" + dependency)]
            if len(matches) != 1:
                raise driver.DriverError(f"dependency {dependency} has no unique active installation owner")
            selection.extend(["--module", matches[0]])
        plan = _request(command, environment, cwd, [*base, *selection])
        if (
            plan.get("schema") != "copilot-extensions.installation-repair"
            or plan.get("version") != 1 or not isinstance(plan.get("planDigest"), str)
            or not plan["planDigest"] or not plan.get("selected")
        ):
            raise driver.DriverError("installation owner returned no selected repair plan")
        if not apply:
            return driver.make_result("planned", "selected driver dependencies planned; no installer invoked"), attempted
        outcomes = plan.get("outcomes")
        if not isinstance(outcomes, list) or not outcomes:
            raise driver.DriverError("installation owner returned no dependency readiness outcomes")
        if any(not isinstance(row, dict) or row.get("restart") != "none" for row in outcomes):
            raise driver.DriverError(
                "automatic driver dependency refresh supports only restart-free closures; "
                "use the installation owner's explicit refresh, restart confirmation and "
                "non-refresh continuation. No dependency installer was invoked."
            )
        attempted = True
        completed = _request(
            command, environment, cwd,
            [*base, *selection, "--apply", "--refresh", "--expected-plan-digest", plan["planDigest"]],
        )
        if completed.get("status") != "ready" or completed.get("requiredSatisfied") is not True:
            raise driver.DriverError("selected driver dependencies did not converge; inspect installation-repair")
        return driver.make_result("applied", "selected driver runtime dependencies converged"), attempted
    except OwnedCleanupError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, driver.DriverError) as error:
        return driver.make_result("failed", str(error)[:driver.MAX_TEXT]), attempted


def maintain(
    cwd: Path, *, machines: Path | None = None, apply: bool = False,
) -> dict[str, Any]:
    """Converge selected prerequisites only through the canonical repair owner."""
    return _maintain(cwd, machines=machines, apply=apply)[0]


def command(args: argparse.Namespace) -> int:
    """Print the existing driver-result protocol for explicit maintenance."""
    roots: list[Path] = (
        [Path.cwd()] if args.project_root is None else
        args.project_root if isinstance(args.project_root, list) else [args.project_root]
    )
    results: list[dict[str, Any]] = []
    for root in tuple(dict.fromkeys(path.resolve() for path in roots)):
        result, attempted = _maintain(root, machines=args.machines, apply=args.apply)
        results.append(result)
        if result["state"] == "failed" and attempted:
            break
    failures = [result["detail"] for result in results if result["state"] == "failed"]
    if failures:
        result = driver.make_result("failed", " | ".join(failures)[:driver.MAX_TEXT])
    else:
        states = {result["state"] for result in results}
        state = "applied" if "applied" in states else "planned" if "planned" in states else "ready"
        result = driver.make_result(state, f"checked {len(results)} SSH repository context(s)")
    print(json.dumps(result) if args.json else driver.render_human(result, "driver-dependencies"))
    return driver.exit_code(result)
