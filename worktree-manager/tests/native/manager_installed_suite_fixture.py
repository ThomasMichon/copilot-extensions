"""Fresh, complete installed suite on the existing disposable Windows owner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import manager_installed_suite_payloads as payloads
import manager_installed_suite_runtimes as runtimes
import manager_suite_fixture as suite
import test_manager_installed_lifecycle as lifecycle
import test_manager_installed_tty as installed

RESULT_SCHEMA = "worktree-manager.installed-suite-lifecycle.v1"


def _run_owner(deadline):
    def run(argv, environment, cwd, diagnostics, label, *, timeout=None):
        from agent_procutil._caller_owned import run as owned_run

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("The installed suite exhausted its existing stage budget")
        result = owned_run(
            list(map(str, argv)), env=environment, cwd=cwd,
            timeout=min(remaining, timeout if timeout is not None else remaining),
            input_text=None, capture=True, allow_daemon_breakaway=True,
            max_stdout_bytes=4 * 1024 * 1024, max_stderr_bytes=12 * 1024 * 1024,
        )
        for suffix, text in (("stdout", result.stdout), ("stderr", result.stderr)):
            (diagnostics / f"{label}.{suffix}.txt").write_text(text, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(installed._command_failure(
                label, result.returncode,
                diagnostics / f"{label}.stdout.txt", diagnostics / f"{label}.stderr.txt",
            ))
        return result.stdout.strip()

    return run


def _install_engine(payload, root, environment, project, diagnostics, label, run):
    base = Path(getattr(sys, "_base_executable", sys.executable))
    run([
        base, "-I", "-S", "-B", payload / "scripts" / "runtime_install_driver.py",
        "--payload", payload, "--root", root, "--shell", shutil.which("pwsh"),
        "--action", "provision", "--bootstrap-python", base,
    ], environment, project, diagnostics, label)


def _environment(case, setup):
    env = lifecycle._case_environment(case, environment=suite._env(case))
    home = Path(env["USERPROFILE"])
    env["AGENT_HOME"] = str(home)
    env["PATH"] = str(home / ".local" / "bin") + os.pathsep + env["PATH"]
    env.update({
        "COPILOT_PROVIDER_TYPE": "openai", "COPILOT_PROVIDER_MODEL_ID": "gpt-4.1",
        "COPILOT_MODEL": "gpt-4.1", "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    })
    if setup.get("mux_namespace"):
        env["COPILOT_DRIVER_PSMUX_NAMESPACE"] = setup["mux_namespace"]
        env["PATH"] = str(case / "mux") + os.pathsep + env["PATH"]
    if setup.get("transport"):
        env["GIT_TEMPLATE_DIR"] = setup["transport"]["checkout_template"]
    return env


def _case(directory):
    case, setup = suite._case(directory, "fresh")
    if setup.get("identity") != lifecycle._identity():
        raise ValueError("Fresh suite evidence belongs to a different candidate/run")
    return case, setup


def _roles(case, profile):
    project = case / lifecycle.PROJECT
    machine = socket.gethostname().split(".")[0].strip().lower()
    aliases = [machine, "tty-fixture"]
    suite._write(project / "services.yaml", {"plugins": [
        {"name": name, "deploy_machines": aliases}
        for name, row in profile["inventory"].items()
        if row["runtime_scope"] == "machine-gated"
    ]})
    index = case / "h" / ".agent-index"
    index.mkdir()
    (index / "config.yaml").write_text("role: host\n", encoding="utf-8")
    index_repo = project / ".copilot-extensions" / "agent-index" / "config.yaml"
    index_repo.parent.mkdir(parents=True)
    suite._write(index_repo, {"indexers": [{"machine": name} for name in aliases]})
    suite._write(project / ".agent-vault.json", {"enabled": True})


def prepare(directory):
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from plugin_test_command_safety import private_mux_args
    from plugin_test_containment import SANDBOX_ENV
    from test_resume_pane_startup import _private_psmux

    suite.require_venue()
    directory = directory.resolve()
    if directory == suite.REPO or not directory.is_relative_to(suite.REPO):
        raise ValueError("Fresh suite storage must be below its source checkout")
    copilot = Path(os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE", ""))
    mux = Path(os.environ.get("PSMUX_TEST_BIN", ""))
    if not all(path.is_absolute() and path.is_file() for path in (copilot, mux)):
        raise ValueError("Select the pinned actual Copilot and PSMux executables")
    lifecycle._require_mux_venue()
    case = directory / "fresh"
    case.mkdir(parents=True, exist_ok=False)
    (case / "project").mkdir()
    (case / "diagnostics").mkdir()
    setup = {
        "schema": suite.SCHEMA, "mode": "fresh", "case_root": str(case),
        "ready": False, "identity": lifecycle._identity(),
    }
    suite._write(case / "setup.json", setup)
    budget = OwnedExecutionBudget.from_timeout(1770, cleanup_reserve=30)
    run = _run_owner(budget.execution_deadline)
    os.environ[SANDBOX_ENV] = str(directory)
    env = _environment(case, setup)
    namespace = private_mux_args(case)[1]
    _private_psmux(case / "mux", str(mux), namespace, env)
    setup["mux_namespace"] = namespace
    suite._write(case / "setup.json", setup)
    (case / "process-trace-window").write_text("fresh", encoding="utf-8")
    artifact = case / "suite-artifact"
    payloads._PROVENANCE["build"](artifact, source_root=suite.REPO)
    candidate = setup["identity"]["candidate_commit"]
    project = case / lifecycle.PROJECT
    project.mkdir()
    # Trusted CLI settings resolve from the Git root, not an arbitrary nested directory.
    run(["git", "init", project], env, project, case / "diagnostics", "suite-project-init", timeout=30)
    base = Path(getattr(sys, "_base_executable", sys.executable))
    bootstrap_payload = artifact / "plugins" / "agent-worktrees"
    # Discovery alone needs an engine; this separate root is never suite evidence.
    bootstrap = case / "bootstrap-engine"
    _install_engine(
        bootstrap_payload, bootstrap, env, project, case / "diagnostics",
        "suite-bootstrap-engine", run,
    )
    transport = payloads.prepare_git_transport(
        artifact, candidate, environment=env, project=project, diagnostics=case / "diagnostics",
        deadline=budget.execution_deadline, run=run,
    )
    with payloads.serve_git_transport(
        transport, environment=env, project=project, diagnostics=case / "diagnostics",
        deadline=budget.execution_deadline, run=run,
    ) as transport:
        profile = payloads.selected_profile(
            artifact, candidate,
            {
                "project": lifecycle.PROJECT, "expose_agent": True, "stateless": True,
                "launch": {"windows": [
                    str(copilot), "--experimental", "--screen-reader", "--no-auto-update",
                    "--disable-builtin-mcps", "--no-color",
                ]},
            },
            transport=transport,
        )
        config = project / ".agent-worktrees" / "config.yaml"
        config.parent.mkdir()
        suite._write(config, profile["repository_config"])
        settings = project / ".github" / "copilot" / "settings.json"
        settings.parent.mkdir(parents=True)
        suite._write(settings, profile["settings"])
        suite._write(Path(env["COPILOT_HOME"]) / "config.json", {
            "trustedFolders": [str(case)], "experimental": True,
        })
        closure = payloads.install_payload_closure(
            artifact, candidate, repository_config=profile["repository_config"],
            copilot=copilot, engine=suite._python(bootstrap), environment=env, project=project,
            diagnostics=case / "diagnostics", transport=transport, run=run,
        )
        engine_payload = Path(closure["payloads"]["agent-worktrees"]["path"])
        _install_engine(
            engine_payload, case / "h" / ".agent-worktrees", env, project,
            case / "diagnostics", "suite-installed-engine", run,
        )
        manager_payload = artifact / "worktree-manager"
        run([
            base, "-I", "-S", "-B", manager_payload / "scripts" / "runtime_install.py",
            "--root", case / "h" / ".worktree-manager", "--payload", manager_payload,
            "--", "--version",
        ], env, project, case / "diagnostics", "suite-installed-manager")
        engine = suite._python(case / "h" / ".agent-worktrees")
        manager = suite._python(case / "h" / ".worktree-manager")
        _roles(case, profile)
        lifecycle._project(case, env, engine, copilot, profile=profile, run=run)
        result = runtimes.install_runtime_closure(
            artifact, candidate, payload_closure=closure, manager=manager, copilot=copilot,
            environment=env, project=project, diagnostics=case / "diagnostics",
            deadline=budget.execution_deadline, run=run,
        )
        manager_origin, _manager = installed._installed(
            case / "h" / ".worktree-manager", manager_payload,
            "worktree-manager", env, case / "diagnostics", run=run,
        )
        setup.update({
            "transport": transport, "closure": closure, "runtime_closure": result,
            "manager_origin": manager_origin,
            "copilot": str(copilot), "mux": str(mux),
            "mux_sha256": hashlib.sha256(mux.read_bytes()).hexdigest(), "ready": True,
        })
        suite._write(case / "setup.json", setup)


def _verify_prepared(case, setup, run):
    if setup.get("ready") is not True:
        raise ValueError("Fresh suite preparation did not finish")
    closure = setup["closure"]
    candidate = setup["identity"]["candidate_commit"]
    artifact = case / "suite-artifact"
    if payloads.selected_profile(
        artifact, candidate, closure["profile"]["repository_config"],
        transport=setup["transport"],
    ) != closure["profile"]:
        raise ValueError("The complete frozen profile changed after preparation")
    for path, digest in (
        (setup["copilot"], closure["copilot_sha256"]),
        (setup["mux"], setup["mux_sha256"]),
    ):
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError("A pinned native executable changed after preparation")
    env = _environment(case, setup)
    if json.loads(
        (case / lifecycle.PROJECT / ".github" / "copilot" / "settings.json").read_text(),
    ) != closure["profile"]["settings"]:
        raise ValueError("The complete enabled profile changed after preparation")
    manager_origin, _manager = installed._installed(
        case / "h" / ".worktree-manager", artifact / "worktree-manager",
        "worktree-manager", env, case / "diagnostics", run=run,
    )
    if manager_origin != setup["manager_origin"]:
        raise ValueError("The installed Manager changed after preparation")
    for name, row in closure["payloads"].items():
        if payloads._PROVENANCE["content_manifest"](Path(row["path"])) != (
            payloads._PROVENANCE["content_manifest"](artifact / "plugins" / name)
        ):
            raise ValueError(f"{name}: the frozen installed payload changed")
    for name, row in setup["runtime_closure"]["runtimes"].items():
        root = Path(row["root"])
        if (root / "current-version").read_text().strip() != row["generation"]:
            raise ValueError(f"{name}: the selected generation changed")
        python = Path(row["prefix"]) / "Scripts" / "python.exe"
        invocation = json.loads(
            (Path(closure["payloads"][name]["path"]) / "payload-invocation.json").read_text(),
        )
        command = (invocation.get("commands") or [invocation])[0]
        metadata = tomllib.loads(
            (Path(closure["payloads"][name]["path"]) / "pyproject.toml").read_text(),
        )["project"]
        origin = json.loads(run(
            [python, "-I", "-B", "-c", runtimes._ORIGIN,
             command["module"].split(".")[0], metadata["name"]],
            env, case / lifecycle.PROJECT, case / "diagnostics",
            "suite-retained-origin-" + name, timeout=30,
        ))
        runtimes.validate_origin(origin, Path(row["prefix"]), metadata["version"])
    return env


def exercise(directory):
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from plugin_test_containment import SANDBOX_ENV

    case, setup = _case(directory)
    os.environ[SANDBOX_ENV] = str(directory.resolve())
    budget = OwnedExecutionBudget.from_timeout(870, cleanup_reserve=30)
    run = _run_owner(budget.execution_deadline)
    env = _verify_prepared(case, setup, run)
    cleanup = []
    with (
        payloads.serve_git_transport(
            setup["transport"], environment=env, project=case / lifecycle.PROJECT,
            diagnostics=case / "diagnostics", deadline=budget.execution_deadline, run=run,
        ),
        daemon_test_lifecycle(
            owner="installed-manager-suite", registry_root=case / "daemon-instances",
            deadline=budget.execution_deadline, cleanup_results=cleanup,
        ) as declaration,
        lifecycle._provider() as (port, requests),
    ):
        env.update(declaration)
        env["COPILOT_PROVIDER_BASE_URL"] = f"http://127.0.0.1:{port}"
        with lifecycle._Witness(
            case, env, suite._python(case / "h" / ".worktree-manager"),
            suite._python(case / "h" / ".agent-worktrees"), Path(setup["copilot"]),
            budget, requests, mux_binary=setup["mux"], run=run,
        ) as witness:
            actions = lifecycle._actions(witness)
    if any(row.status != "settled" for row in cleanup):
        raise RuntimeError("The private native lifecycle did not settle")
    _verify_prepared(case, setup, run)
    suite._write(case / "result.json", {
        "schema": RESULT_SCHEMA, "identity": setup["identity"], "mode": "fresh",
        "profile": "complete-installed-suite", "runtime_closure": setup["runtime_closure"],
        "manager_origin": setup["manager_origin"],
        "copilot_sha256": setup["closure"]["copilot_sha256"],
        "mux_sha256": setup["mux_sha256"], **actions,
    })


def settle(directory):
    case, setup = _case(directory)
    observed, errors = {}, []
    for name in suite.MARKERS:
        root = case / "h" / ("." + name)
        try:
            marker, _ = suite._record(root, name)
            with suite.closing_process(marker["pid"]) as process:
                created, _exited, code = process.times()
                if code == 259:
                    observed[name] = {"pid": marker["pid"], "created_ns": created}
        except (FileNotFoundError, ProcessLookupError):
            pass
        except OSError as error:
            if getattr(error, "winerror", None) not in {87, 1168}:
                errors.append(f"{name}: {type(error).__name__}: {error}")
        except Exception as error:
            errors.append(f"{name}: {type(error).__name__}: {error}")
    known = setup.get("before", {}).get("resident_observations", {})
    setup["before"] = {"resident_observations": {**known, **observed}}
    suite._write(case / "setup.json", setup)
    suite.settle(directory, "fresh")
    if errors:
        result = suite._read(case / "settlement.json")
        result["errors"].extend(errors)
        suite._write(case / "settlement.json", result)
        raise AssertionError("Native resident inventory was incomplete")


def assertions(directory):
    case, setup = _case(directory)
    result = suite._read(case / "result.json")
    validate_evidence(setup, result)


def validate_evidence(setup, result):
    if (
        result.get("schema") != RESULT_SCHEMA or result.get("identity") != setup["identity"]
        or result.get("mode") != "fresh" or result.get("profile") != "complete-installed-suite"
        or result.get("runtime_closure") != setup["runtime_closure"]
        or result.get("manager_origin") != setup["manager_origin"]
        or result.get("copilot_sha256") != setup["closure"]["copilot_sha256"]
        or result.get("mux_sha256") != setup["mux_sha256"]
        or result["runtime_closure"].get("runtime_installation") != "observed"
        or set(result["runtime_closure"]["runtimes"]) != runtimes.expected_runtimes(
            setup["closure"]["profile"],
        )
        or any(result.get(key) is not True for key in (
            "create", "open_existing", "detach_preserved", "normal_exit", "resume_continued",
        ))
        or any(
            not isinstance(result.get(key), list) or len(result[key]) != 2
            or any(type(value) is not int or value <= 0 for value in result[key])
            for key in ("native_before_open", "native_after_resume")
        )
        or result.get("native_before_open") == result.get("native_after_resume")
        or not result.get("conversation") or not result.get("worktree")
    ):
        raise ValueError("Incomplete or wrong-candidate full-suite lifecycle evidence")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    stages = parser.add_mutually_exclusive_group(required=True)
    for stage in ("prepare", "exercise", "settle", "assertions"):
        stages.add_argument("--" + stage, type=Path)
    parser.add_argument("--mode", choices=("fresh",), required=True)
    args = parser.parse_args()
    for stage in ("prepare", "exercise", "settle", "assertions"):
        directory = getattr(args, stage)
        if directory is not None:
            globals()[stage](directory)


if __name__ == "__main__":
    main()
