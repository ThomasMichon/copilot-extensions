"""Fresh-source recovery composition; application and execution owners do the work.

The public bootstrap fetches this toolkit from upstream Main, independently of
the installation source. This module never changes a source policy or tears down
an incumbent, and never falls back to an installed dispatcher.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import uuid
from urllib.parse import urlsplit

REPOSITORY = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/ThomasMichon/copilot-extensions.git"
REQUEST_SCHEMA = "copilot-extensions.recovery-request"
RESULT_SCHEMA = "copilot-extensions.recovery-result"


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate recovery request key: {key}")
        value[key] = item
    return value


def _absolute(value, name, *, nullable=True):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or "\0" in value or not Path(value).is_absolute():
        raise ValueError(f"{name} requires an absolute path")
    return Path(value)


def read_request(path: Path) -> dict:
    _absolute(str(path), "request", nullable=False)
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("Recovery request must be an ordinary, bounded JSON file")
    value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique)
    if (
        not isinstance(value, dict)
        or value.get("schema") != REQUEST_SCHEMA or type(value.get("version")) is not int
        or value["version"] not in (1, 2)
    ):
        raise ValueError("Unsupported recovery request")
    fields = {"schema", "version", "operation", "selection", "manager_root", "project"}
    if value["version"] == 2:
        fields.add("repository_root")
    if set(value) != fields or value["operation"] not in ("preview", "install"):
        raise ValueError("Unsupported recovery request")
    modes = ("main", "commit", "configured-main-pin")
    if value["version"] == 2:
        if value["operation"] != "preview":
            raise ValueError("Repository suite recovery currently supports preview only")
        _absolute(value["repository_root"], "repository_root")
        modes = ("repository",)
    choice = value["selection"]
    if (
        not isinstance(choice, dict) or set(choice) != {"mode", "commit", "checkout"}
        or choice["mode"] not in modes
    ):
        raise ValueError("Unsupported recovery source selection")
    if choice["mode"] == "commit":
        if not isinstance(choice["commit"], str) or not re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})", choice["commit"],
        ):
            raise ValueError("Commit recovery requires a full lowercase commit object ID")
    elif choice["commit"] is not None:
        raise ValueError("Only commit selection accepts a commit")
    _absolute(choice["checkout"], "checkout")
    _absolute(value["manager_root"], "manager_root")
    if value["project"] is not None and (
        not isinstance(value["project"], str) or not value["project"]
        or any(ord(char) < 32 for char in value["project"])
    ):
        raise ValueError("project must be a nonempty string or null")
    return value


def source_modules(repository: Path) -> None:
    """Load only the fresh toolkit's declared bootstrap dependency closure."""
    scripts = repository / "worktree-manager/scripts"
    sys.path[:0] = [str(scripts), str(repository / "tools")]
    import installer_engine

    if not Path(installer_engine.__file__).resolve().is_relative_to(repository.resolve()):
        raise ValueError("Recovery toolkit imported an installed installer instead of fresh source")
    packages = tuple(dict.fromkeys((
        *installer_engine._package_roots(repository / "worktree-manager"),
        *installer_engine._package_roots(repository / "plugins/agent-worktrees"),
    )))
    sys.path[:0] = [str(package / "src") for package in packages]
    sys.path.insert(0, str(repository / "plugins/agent-worktrees/src"))
    import agent_procutil
    import agent_worktrees
    import worktree_manager

    for module in (agent_procutil, agent_worktrees, worktree_manager):
        if not Path(module.__file__).resolve().is_relative_to(repository.resolve()):
            raise ValueError("Recovery toolkit imported an installed application instead of fresh source")


def build_dependencies() -> None:
    """Expose ordinary tool dependencies without executing installed startup hooks."""
    for key in ("purelib", "platlib"):
        path = sysconfig.get_path(key)
        if path not in sys.path:
            sys.path.append(path)


def repository_declaration(repository_root: Path | None, workspace: Path) -> dict:
    """Interpret adopter data using only the current toolkit's declared parser."""
    from worktree_manager import runtime_source_repository

    if repository_root is None:
        return runtime_source_repository.read(repository_root, default_repository=UPSTREAM)
    path = repository_root / ".copilot-extensions" / "config.yaml"
    runtime_source_repository._ordinary(path)
    if not path.exists():
        return runtime_source_repository.read(repository_root, default_repository=UPSTREAM)

    import tomllib
    import agent_procutil
    from installer_dependency_policy import retain_uv_install
    from installer_engine_process import run_owned

    manifest = REPOSITORY / "worktree-manager" / "pyproject.toml"
    requirements = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["dependencies"]
    yaml_requirements = [
        requirement for requirement in requirements
        if re.match(r"(?i)^pyyaml(?:\s|[<>=!~;\[]|$)", requirement)
    ]
    if not yaml_requirements:
        raise ValueError("Trusted recovery toolkit does not declare its YAML parser")
    uv = None
    current_directory = Path.cwd()
    runtime_source_repository._ordinary(current_directory)
    for location in os.get_exec_path():
        directory = Path(location.strip('"'))
        if (
            not directory.is_absolute() or ".." in directory.parts
            or str(directory).replace("\\", "/").startswith("//")
        ):
            continue
        try:
            runtime_source_repository._ordinary(directory)
            if directory.samefile(current_directory) or any(
                parent.samefile(repository_root) for parent in (directory, *directory.parents)
            ):
                continue
            candidate = directory / ("uv.exe" if os.name == "nt" else "uv")
            runtime_source_repository._ordinary(candidate)
        except (OSError, ValueError):
            continue
        if candidate.is_file() and os.access(candidate, os.X_OK):
            uv = str(candidate)
            break
    if uv is None:
        raise ValueError("Repository suite interpretation requires UV on an absolute host tool path")
    parser_root = workspace / "repository-parser"
    parser_root.mkdir(parents=True, exist_ok=False)
    python = Path(getattr(sys, "_base_executable", sys.executable))
    deadline = time.monotonic() + 120
    with retain_uv_install(
        uv, ["--python", str(python), "--target", str(parser_root),
             "--only-binary=:all:", *yaml_requirements],
        cwd=REPOSITORY / "worktree-manager", trusted_source_roots=(REPOSITORY,),
        deadline=deadline,
    ) as transaction:
        run_owned(
            list(transaction.argv), cwd=transaction.cwd, env=transaction.environment,
            timeout=120, deadline=deadline, procutil=agent_procutil, construction_fd=None,
        )
        try:
            result = run_owned(
                [str(python), "-I", "-S", "-B", str(Path(runtime_source_repository.__file__)),
                 "--repository-root", str(repository_root), "--default-repository", UPSTREAM,
                 "--parser-root", str(parser_root)],
                cwd=workspace, env=dict(os.environ), timeout=120, deadline=deadline,
                procutil=agent_procutil, construction_fd=None,
            )
        except subprocess.CalledProcessError as error:
            try:
                failure = json.loads(error.stdout, object_pairs_hook=_unique)["error"]
            except (ValueError, KeyError, TypeError):
                raise ValueError("Trusted repository suite reader failed") from error
            raise ValueError(str(failure)) from error
    return json.loads(result.stdout, object_pairs_hook=_unique)


def selection(request: dict, manager_root: Path, *, declaration: dict | None = None):
    from worktree_manager.runtime_source_config import RuntimeSource, configured_source

    policy = configured_source(manager_root)
    choice = request["selection"]
    if choice["mode"] == "repository":
        if declaration is None:
            raise ValueError("Repository suite recovery requires an interpreted declaration")
        reference = declaration["reference"]
        repository = declaration["repository"]
        if reference in ("main", "refs/heads/main"):
            channel, native_ref = "main", ""
        elif re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", reference):
            channel, native_ref = "exact", reference
        elif reference.startswith("refs/tags/"):
            channel, native_ref = "stable", reference[len("refs/tags/"):]
        else:
            raise ValueError(
                "Repository suite recovery supports Main, full commit IDs and refs/tags/<tag>; "
                "other symbolic selections require native source support"
            )
        try:
            selected = RuntimeSource(channel, repo=repository, ref=native_ref)
        except ValueError:
            raise ValueError(
                "Repository suite source requires a credential-free locator and valid native reference"
            ) from None
        if policy.channel != "marketplace" and policy != selected:
            raise ValueError("Repository suite recovery would override an independent runtime source policy")
    elif choice["mode"] == "configured-main-pin":
        if policy.channel != "exact":
            raise ValueError("Configured Main pin recovery requires an existing exact runtime-source policy")
        selected = policy
    elif choice["mode"] == "main":
        if policy.channel not in ("marketplace", "main"):
            raise ValueError(
                "Main recovery would override an existing source policy; explicitly select "
                "its configured-main-pin or an exact commit instead"
            )
        if policy.channel == "main" and policy.repo != UPSTREAM:
            raise ValueError("Main recovery would override an existing repository policy")
        selected = RuntimeSource("main", repo=UPSTREAM)
    else:
        selected = RuntimeSource("exact", repo=UPSTREAM, ref=choice["commit"])
    # A caller checkout is only an object source. Main ancestry is still fetched
    # from the selected repository, never trusted from the caller's local refs.
    repository = selected.repo
    if choice["checkout"] is not None:
        selected = RuntimeSource(
            selected.channel, repo=choice["checkout"], ref=selected.ref,
        )
    return selected, repository


def entry(payload: Path) -> dict | None:
    from agent_worktrees.update_runtime_source import source_install_entry

    return source_install_entry(payload, "windows" if os.name == "nt" else "posix")


def portfolio(source: Path) -> dict[str, dict]:
    """Read the selected native marketplace roster, not an external package list."""
    from standalone_python_artifacts import COMPONENTS

    catalog = json.loads((source / ".github/plugin/marketplace.json").read_text(encoding="utf-8"))
    result = {}
    for row in catalog["plugins"]:
        name = row["name"]
        if not isinstance(name, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
            raise ValueError("Selected marketplace has an invalid plugin identifier")
        payload = source / "plugins" / name
        manifest = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
        if manifest.get("runtimeScope", "none") == "none":
            continue
        capability = entry(payload)
        result[name] = {
            "payload": str(payload), "flow": "prepared-install-source" if capability else "legacy-install-flow",
        }
    for name, relative in COMPONENTS.items():
        payload = source / relative
        if payload.is_dir() and entry(payload) is not None:
            result[name] = {"payload": str(payload), "flow": "prepared-install-source"}
    return result


def native_inputs(snapshot: Path, component: str) -> tuple[Path, dict]:
    import install_source

    with install_source.project_inputs(snapshot, component) as (selected, wheels):
        native = install_source.native_selection(selected, wheels)
        payload = selected.payload
    if entry(payload) is None:
        raise ValueError(f"{component} does not advertise native prepared installation")
    return payload, native


def descriptor(repository: str) -> dict:
    if Path(repository).is_absolute():
        return {"source": "directory", "path": repository}
    if repository.startswith("git@") and ":" in repository:
        host, path = repository[4:].split(":", 1)
        repository = "ssh://git@" + host + "/" + path
    return {
        "source": "github" if urlsplit(repository).hostname == "github.com" else "git",
        "url": repository,
    }


def prepare_new(
    resolved, workspace: Path, names: list[str], uv: str, repository: str,
) -> dict[str, dict]:
    from local_install_source import build_projects, compose
    from local_install_profile import prepare_registry_lock
    from standalone_python_artifacts import COMPONENTS

    output = workspace / ("wheels-" + uuid.uuid4().hex)
    record = compose(
        resolved.checkout, resolved.commit, resolved.branch_tip, output, finalized_main=True,
        source_descriptor=descriptor(repository),
    )
    projects = tuple("standalone:" + name if name in COMPONENTS else name for name in names)
    build_projects(output, record, projects=projects)
    result = {}
    for name in names:
        prepared = prepare_registry_lock(
            output, workspace / ("profile-" + name + "-" + uuid.uuid4().hex),
            uv=uv, deadline=time.monotonic() + 1800, prepare_profile=True, component=name,
        )
        if prepared.get("install_ready") is not True:
            raise ValueError(f"{name} native preparation returned incomplete inputs")
        snapshot = Path(prepared["snapshot"])
        payload, native = native_inputs(snapshot, name)
        result[name] = {
            "payload": str(payload), "identity": native["identity"],
            "install_source": str(snapshot), "native": native,
        }
    return result


def prepare_legacy(resolved, workspace: Path, name: str) -> dict:
    from prepare_runtime_source import prepare
    from versioned_runtime_source import manifest_fields

    prepared = prepare(
        resolved.checkout, resolved.commit, name, workspace / "legacy", branch="main",
    )
    return {"payload": str(prepared.payload), "identity": manifest_fields(prepared.identity)}


def _run(arguments: list[str], *, environment: dict[str, str] | None = None) -> int:
    from agent_procutil import no_window_kwargs
    from update_driver import participant_environment

    result = subprocess.run(
        arguments, env={**(environment or os.environ), **participant_environment()}, stdin=subprocess.DEVNULL,
        stdout=sys.stderr, stderr=sys.stderr, check=False, **no_window_kwargs(),
    )
    return result.returncode


def manager_handoff(payload: Path) -> dict:
    native = payload / "scripts/runtime_install.py"
    if not native.is_file() or native.is_symlink():
        raise ValueError("Selected Manager has no ordinary native installation participant")
    text = native.read_text(encoding="utf-8")
    capabilities = ["--install-only", "--source-digest"]
    if entry(payload) is not None:
        capabilities.append("--install-source")
    if not all(flag in text for flag in capabilities):
        raise ValueError("Selected Manager lacks its native bounded installation participant")
    return {"script": "scripts/runtime_install.py", "capabilities": capabilities}


def install_manager(record: dict, manager_root: Path) -> int:
    from agent_procutil import no_window_kwargs
    from update_driver import participant_environment

    payload = Path(record["payload"])
    handoff = manager_handoff(payload)
    native = payload / handoff["script"]
    probe = subprocess.run(
        [
            sys.executable, "-I", "-S", "-B", "-X", "utf8", "-c",
            "import runpy,sys; from pathlib import Path; "
            "print(runpy.run_path(sys.argv[1])['_source_digest'](Path(sys.argv[2])))",
            str(native), str(payload),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, **participant_environment()}, timeout=120, check=False,
        **no_window_kwargs(),
    )
    if probe.returncode:
        raise ValueError(f"Selected Manager source inspection failed ({probe.returncode}): {probe.stderr.strip()}")
    digest = probe.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Selected Manager returned no canonical source digest")

    arguments = [
        sys.executable, "-I", "-S", "-B", "-X", "utf8", str(native),
        "--root", str(manager_root), "--payload", str(payload),
        "--install-only", "--source-digest", digest,
    ]
    if "install_source" in record:
        arguments += ["--install-source", record["install_source"]]
    return _run(arguments)


def native_engine(project: str | None, arguments: list[str]) -> int:
    """Run the fresh execution owner, not the selected older engine's dispatcher."""
    from agent_worktrees.__main__ import main

    return main([*(["--project", project] if project else []), "update", *arguments])


def native_inventory(project: str | None) -> dict:
    from worktree_manager.installation_updates import _engine_rows

    output = io.StringIO()
    with redirect_stdout(output):
        code = native_engine(project, ["--inventory", "--json"])
    if code:
        raise ValueError(f"Native source inventory failed ({code}): {output.getvalue()}")
    value = json.loads(output.getvalue(), object_pairs_hook=_unique)
    list(_engine_rows(value))
    return value


def footprint_preview(project: str | None) -> dict:
    try:
        build_dependencies()
        return {"status": "observed", "inventory": native_inventory(project)}
    except (ImportError, OSError, RuntimeError, ValueError, SystemExit) as error:
        return {"status": "unavailable", "error": str(error)}


def native_suite(project: str | None, packet: dict) -> int:
    return native_engine(project, [
        "--no-manager", "--phase", "runtimes", "--runtime-sources", json.dumps(packet),
    ])


def apply(request: dict, resolved, workspace: Path, source: Path, rows: dict, report: dict) -> int:
    """Compose the native participant, repair, inventory and execution APIs."""
    from agent_worktrees.update_runtime_source import PreparedRuntimeSource
    from worktree_manager import update_bootstrap
    from worktree_manager.runtime_source_repair import qualify_engine, fresh_engine_plan
    from worktree_manager.runtime_source_update import _targets

    report["stage"] = "native-preparation"
    if "COPILOT_EXTENSIONS_CONTEXT" in os.environ:
        raise ValueError("Prepared-source recovery requires the native legacy installation scope")
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("Native source construction requires uv; no host tooling was provisioned")
    manager_root = Path(report["manager_root"])
    os.environ["WORKTREE_MANAGER_ROOT"] = str(manager_root)
    records = {}
    initial = ["worktree-manager", "agent-worktrees"]
    new = [
        name for name in initial
        if entry(source / ("worktree-manager" if name == "worktree-manager" else "plugins/" + name))
    ]
    if new:
        prepared = prepare_new(resolved, workspace, new, uv, report["repository"])
        if set(prepared) != set(new):
            raise ValueError("Native prepared installation omitted an advertised target")
        records.update(prepared)
    for name in initial:
        if name not in new:
            records[name] = prepare_legacy(resolved, workspace, name)
    engine_record = records["agent-worktrees"]
    qualify_engine(
        Path(engine_record["payload"]),
        **({"prepared": True} if "install_source" in engine_record else {}),
    )
    engine = PreparedRuntimeSource(
        Path(engine_record["payload"]), json.dumps(engine_record["identity"]),
        Path(engine_record["install_source"]) if "install_source" in engine_record else None,
        json.dumps(engine_record["native"], sort_keys=True) if "native" in engine_record else None,
    )
    report["stage"] = "payload-acquisition"
    code = native_engine(request["project"], ["--no-manager", "--phase", "payloads"])
    report["effects"].append({"stage": report["stage"], "exit_code": code})
    if code:
        return code
    fresh = fresh_engine_plan(REPOSITORY, engine)
    report["stage"] = "manager-installation"
    rc = install_manager(records["worktree-manager"], manager_root)
    report["effects"].append({"stage": report["stage"], "exit_code": rc})
    if rc:
        return rc
    if fresh is None:
        report["stage"] = "engine-repair"
        repaired = update_bootstrap._repair_installation(REPOSITORY, prepared_source=engine)
        code = repaired.exit_code
    else:
        from agent_worktrees.update_runtime import _run_engine_installer

        report["stage"] = "engine-installation"
        code = _run_engine_installer(
            list(fresh.argv), fresh.payload, fresh.deadline_s, fresh.environment,
            runtime_root=fresh.runtime_root, target_version=fresh.target_version, source_step=fresh,
        )
    report["effects"].append({"stage": report["stage"], "exit_code": code})
    if code:
        return code
    report["stage"] = "suite-preparation"
    inventory = native_inventory(request["project"])
    names = _targets(inventory, [])
    if any(rows.get(name, {}).get("flow") == "prepared-install-source" for name in names) and (
        inventory["runtime_sources"].get("prepared_install_source") != 1
    ):
        raise ValueError("Selected engine does not support prepared native suite inputs")
    missing_new = [
        name for name in names if name not in records
        and rows.get(name, {}).get("flow") == "prepared-install-source"
    ]
    if missing_new:
        prepared = prepare_new(resolved, workspace, missing_new, uv, report["repository"])
        if set(prepared) != set(missing_new):
            raise ValueError("Native prepared installation omitted an advertised suite target")
        records.update(prepared)
    for name in names:
        if name not in rows:
            raise ValueError(f"Selected source has no native runtime for inventory target {name}")
        if name not in records:
            records[name] = prepare_legacy(resolved, workspace, name)
    packet = {
        "schema": "copilot-extensions.prepared-runtime-sources",
        "version": 2 if any("install_source" in records[name] for name in names) else 1,
        "sources": {name: records[name] for name in names},
    }
    report["targets"] = {name: rows[name]["flow"] for name in names}
    report["stage"] = "suite-execution"
    rc = native_suite(request["project"], packet)
    report["effects"].append({"stage": report["stage"], "exit_code": rc})
    return rc


def admitted_apply(request, resolved, workspace, source, rows, report) -> int:
    """Borrow the native finite execution owner without replaying another request."""
    import runtime_install
    from worktree_manager.update_driver_cli import _CONTEXT_KEYS, request_context

    driver = runtime_install._execution_driver(REPOSITORY / "worktree-manager")
    invocation = str(Path.cwd())
    plan = workspace / "execution.json"
    content = json.dumps({
        "schema": "copilot-extensions.source-recovery-execution", "version": 1,
        "request": request, "resolved": resolved.document(), "source": str(source),
        "rows": rows, "report": report, "workspace": str(workspace),
        "invocation_cwd": invocation,
    }, sort_keys=True).encode("utf-8")
    if len(content) > (1 << 20):
        raise ValueError("Native recovery execution plan exceeds its document budget")
    plan.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    report["stage"] = "native-admission"
    result = driver.run_explicit(
        driver.RequestSpec(
            "source-recovery", ("install", resolved.commit, resolved.tree),
            project=request["project"] or "", cwd=invocation, source=digest,
            context=tuple({
                **request_context(capture_engine=False), "WORKTREE_MANAGER_ROOT": report["manager_root"],
            }.items()),
        ),
        runner=driver.TrustedRunner(
            (
                sys.executable, "-I", "-S", "-B", "-X", "utf8", str(Path(__file__).resolve()),
                "--execute-plan", str(plan), "--plan-sha256", digest,
            ),
            invocation, context_keys=_CONTEXT_KEYS,
        ),
    )
    report["driver_request"] = result.id
    report["native_exit_code"] = result.exit_code
    driver.wait(result.id, output=sys.stderr)
    if result.state != "completed" or result.exit_code is None:
        raise ValueError(f"Native recovery execution is {result.state}; settlement remains with its owner")
    with result.log_path.open("rb") as stream:
        stream.seek(max(0, result.log_path.stat().st_size - (1 << 20)))
        lines = stream.read().splitlines()
    if not lines:
        raise ValueError("Native recovery returned no outcome receipt")
    outcome = json.loads(lines[-1], object_pairs_hook=_unique)
    if (
        not isinstance(outcome, dict) or outcome.get("schema") != RESULT_SCHEMA
        or outcome.get("selection") != report["selection"]
        or outcome.get("exit_code") != result.exit_code
        or outcome.get("driver_request", result.id) != result.id
    ):
        raise ValueError("Native recovery outcome differs from the admitted selection")
    report.update(outcome, driver_request=result.id)
    return result.exit_code


def execute_plan(path: Path, digest: str, report: dict) -> int:
    import runtime_install
    from worktree_manager.runtime_source import ResolvedSource
    from worktree_manager.runtime_source_config import RuntimeSource

    _absolute(str(path), "execution plan", nullable=False)
    if path.is_symlink() or path.stat().st_size > (1 << 20):
        raise ValueError("Native recovery execution plan must be ordinary and bounded")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Native recovery execution plan changed after admission")
    plan = json.loads(content, object_pairs_hook=_unique)
    if (
        not isinstance(plan, dict) or plan.get("schema") != "copilot-extensions.source-recovery-execution"
        or type(plan.get("version")) is not int or plan["version"] != 1
        or plan["request"]["operation"] != "install"
    ):
        raise ValueError("Unsupported native recovery execution plan")
    driver = runtime_install._execution_driver(REPOSITORY / "worktree-manager")
    with runtime_install._installation_custody() as custody:
        if custody is None:
            raise ValueError("Source recovery participant requires live native execution custody")
        admitted = driver.status(custody.request_id, root=custody.root)
        if admitted.spec.backend != "source-recovery" or admitted.spec.source != digest:
            raise ValueError("Native recovery custody belongs to a different request")
        custody.assert_owned()
        if Path.cwd().resolve() != Path(plan["invocation_cwd"]).resolve():
            raise ValueError("Native recovery invocation context changed after admission")
        report.update(plan["report"])
        report["driver_request"] = custody.request_id
        value = plan["resolved"]
        resolved = ResolvedSource(
            RuntimeSource(**value["selection"]), Path(value["checkout"]),
            value["commit"], value["tree"], value["branch_tip"],
        )
        return apply(
            plan["request"], resolved, Path(plan["workspace"]),
            Path(plan["source"]), plan["rows"], report,
        )


def recover(request: dict, workspace: Path, report: dict) -> int:
    from local_install_source import compose
    from worktree_manager.runtime_source import acquire
    from worktree_manager.self_install import default_root

    manager_root = Path(request["manager_root"]) if request["manager_root"] else default_root().absolute()
    report["manager_root"] = str(manager_root)
    report["stage"] = "source-selection"
    declaration = None
    if request["version"] == 2:
        report["stage"] = "repository-interpretation"
        repository_root = request["repository_root"]
        declaration = repository_declaration(
            Path(repository_root) if repository_root is not None else None, workspace,
        )
    report["stage"] = "source-selection"
    selected, repository = selection(request, manager_root, declaration=declaration)
    if declaration is not None:
        report["repository_policy"] = declaration
    report["repository"] = repository
    report["stage"] = "source-acquisition"
    with acquire(selected, root=workspace / "acquisition", branch_repository=repository) as resolved:
        output = workspace / "selected"
        report["stage"] = "source-materialization"
        record = compose(
            resolved.checkout, resolved.commit, resolved.branch_tip, output, finalized_main=True,
            source_descriptor=descriptor(repository),
        )
        report["selection"] = {
            key: record[key] for key in ("source_commit", "source_tree", "main_head")
        }
        source = output / "source"
        report["stage"] = "native-handoff-inspection"
        rows = portfolio(source)
        manager_entry = entry(source / "worktree-manager")
        report["manager_flow"] = "prepared-install-source" if manager_entry else "legacy-install-flow"
        report["manager_handoff"] = manager_handoff(source / "worktree-manager")
        report["available_targets"] = {name: row["flow"] for name, row in rows.items()}
        report["footprint"] = footprint_preview(request["project"])
        report["stage"] = "preview"
        if request["operation"] == "preview":
            return 0
        return admitted_apply(request, resolved, workspace, source, rows, report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--request", type=Path)
    group.add_argument("--execute-plan", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--plan-sha256", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    report = {
        "schema": RESULT_SCHEMA, "version": 1, "stage": "request",
        "effects": [], "cleanup_authorized": False,
    }
    try:
        if args.execute_plan is not None:
            if not args.plan_sha256 or re.fullmatch(r"[0-9a-f]{64}", args.plan_sha256) is None:
                raise ValueError("Native recovery execution requires its admitted plan digest")
            source_modules(REPOSITORY)
            build_dependencies()
            report["exit_code"] = execute_plan(args.execute_plan, args.plan_sha256, report)
            print(json.dumps(report, sort_keys=True), flush=True)
            return report["exit_code"]
        if args.plan_sha256 is not None:
            raise ValueError("Native plan digest requires its participant entry")
        request = read_request(args.request)
        report["operation"] = request["operation"]
        report["stage"] = "toolkit"
        source_modules(REPOSITORY)
        from prepare_runtime_source import _git

        report["toolkit"] = {
            "commit": _git(REPOSITORY, "rev-parse", "HEAD"),
            "tree": _git(REPOSITORY, "rev-parse", "HEAD^{tree}"),
            "checkout": str(REPOSITORY),
        }
        if request["operation"] == "preview":
            context = tempfile.TemporaryDirectory(prefix="recovery-preview-")
        else:
            from worktree_manager.self_install import default_root
            from worktree_manager.runtime_source import _ordinary

            root = Path(request["manager_root"]) if request["manager_root"] else default_root().absolute()
            _ordinary(root)
            report["stage"] = "source-selection"
            selection(request, root)
            workspace = root / "runtime-sources" / "recovery" / uuid.uuid4().hex
            report["stage"] = "input-workspace"
            workspace.mkdir(parents=True, exist_ok=False)
            report["retained_inputs"] = str(workspace)
            context = nullcontext(str(workspace))
        with context as directory:
            rc = recover(request, Path(directory), report)
        report["exit_code"] = rc
    except KeyboardInterrupt as error:
        report.update(exit_code=130, error="Recovery interrupted; native custody owns settlement")
        if getattr(error, "request_id", None):
            report["driver_request"] = error.request_id
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError, KeyError, TypeError) as error:
        report.update(exit_code=1, error=str(error))
        if getattr(error, "request_id", None):
            report["driver_request"] = error.request_id
    print(json.dumps(report, sort_keys=True), flush=True)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
