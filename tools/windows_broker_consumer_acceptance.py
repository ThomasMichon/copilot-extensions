"""Real maintenance consumers in an explicitly admitted disposable Windows profile."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

from windows_broker_protocol_acceptance import admit, fixture_environment

REPOSITORY = Path(__file__).resolve().parents[1]


def _observe(predicate, *, timeout: float = 45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError("real consumer observation deadline expired")


def qualify(root: Path) -> dict:
    admit(root, True)
    sys.path[:0] = [
        str(REPOSITORY / "libs" / name / "src")
        for name in ("update-driver", "agent-procutil", "single-instance-lease", "zdd")
    ]
    import agent_procutil as pu
    from agent_procutil.owned_process import run_caller_owned
    from update_driver import core
    from agent_procutil import windows_broker_installation as installation
    from agent_procutil import windows_task_broker as broker
    from zdd.private_protocol import Deadline
    from windows_broker_desktop import DesktopWitness
    import prepare_runtime_source as preparation

    profile = root / "profile"
    profile.mkdir()
    queue = profile / ".copilot-extensions" / "update-driver"
    environment = fixture_environment(dict(os.environ), control=True)
    environment.update(
        HOME=str(profile), USERPROFILE=str(profile), AGENT_HOME=str(profile),
        APPDATA=str(profile / "AppData" / "Roaming"),
        LOCALAPPDATA=str(profile / "AppData" / "Local"),
        UV_PYTHON=sys.executable, UV_PYTHON_DOWNLOADS="never",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1", GCM_INTERACTIVE="never",
        PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
    )
    for name in ("APPDATA", "LOCALAPPDATA"):
        Path(environment[name]).mkdir(parents=True)
    projects = []
    settings = {
        "enabledPlugins": {
            "agent-worktrees@copilot-extensions": True,
            "agent-ssh@copilot-extensions": True,
        },
        "extraKnownMarketplaces": {
            "copilot-extensions": {
                "source": {"source": "github", "repo": "ThomasMichon/copilot-extensions"},
            },
        },
    }
    shell, git = shutil.which("pwsh"), shutil.which("git")
    if not shell or not git or not shutil.which("uv"):
        raise RuntimeError("real consumer qualification requires native PowerShell, Git and UV")
    commands = 0

    def run(arguments, *, cwd=None, timeout=300):
        nonlocal commands
        commands += 1
        result = run_caller_owned(
            arguments, env=environment, cwd=cwd or root, timeout=timeout,
            max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
        )
        (root / f"command-{commands}.log").write_text(
            result.stdout + result.stderr, encoding="utf-8",
        )
        if result.returncode:
            requests = []
            if (queue / "queue.sqlite3").is_file():
                for request in core.status(root=queue):
                    requests.append({
                        "id": request.id, "backend": request.spec.backend, "state": request.state,
                        "exit_code": request.exit_code, "error": request.error,
                        "log": request.log_path.read_text(encoding="utf-8", errors="replace")[-16384:]
                        if request.log_path.is_file() else None,
                    })
                (root / f"command-{commands}-requests.json").write_text(
                    json.dumps(requests, indent=2), encoding="utf-8",
                )
            raise RuntimeError(
                f"real consumer command {commands} failed ({result.returncode}): "
                + (result.stdout + result.stderr)[-16384:]
                + "\nDurable request evidence: " + json.dumps(requests)
            )
        return result.stdout

    for index in range(2):
        project = root / f"project-{index}"
        project.mkdir()
        run([git, "init", "-q", "--initial-branch", "main", str(project)])
        target = project / ".github" / "copilot" / "settings.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(settings), encoding="utf-8")
        run([git, "-C", str(project), "add", ".github"])
        run([
            git, "-C", str(project), "-c", "user.name=Consumer fixture",
            "-c", "user.email=consumer-fixture@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-qm", "Initial private consumer fixture",
        ])
        projects.append(project)
    owners = {}
    for name in ("agent-worktrees", "agent-ssh", "worktree-manager"):
        payload = profile / ".copilot" / "installed-plugins" / "copilot-extensions" / name
        payload.parent.mkdir(parents=True, exist_ok=True)
        preparation._materialize(REPOSITORY, name, payload)
        owners[name] = payload
    runtime = profile / ".agent-worktrees"
    peer_runtime = profile / ".agent-ssh"
    manager_runtime = profile / ".worktree-manager"
    environment["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] = str(projects[0])

    def lifecycle(name, action):
        if name == "worktree-manager":
            return [
                sys.executable, "-I", "-B", str(owners[name] / "scripts" / "runtime_install.py"),
                "--root", str(manager_runtime), "--payload", str(owners[name]),
                *(["--uninstall", "--apply", "--json"] if action == "uninstall" else ["--", "--version"]),
            ]
        command = [
            shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(owners[name] / "scripts" / "install.ps1"), action,
            "-InstallDir", str(runtime if name == "agent-worktrees" else peer_runtime),
        ]
        if name == "agent-worktrees":
            command += ["-BootstrapPython", sys.executable]
        return command

    witness = DesktopWitness(root)
    positive = witness.qualify(pu)
    witness.start()
    cleanup = {}
    outcome = {}
    config = None
    manager_config = None
    command = None
    registered = []
    try:
        # No contained-test switch: the real installer owns cold construction and Task adoption.
        run(lifecycle("agent-worktrees", "provision"), cwd=projects[0])
        version = (runtime / "current-version").read_text().strip()
        slot = runtime / "versions" / version
        interpreter = slot / "Scripts" / "python.exe"
        manifest = json.loads((runtime / "deploy-manifest.json").read_text())
        if Path(manifest["venv"]) != slot:
            raise RuntimeError("cold Worktrees construction did not publish its actual runtime")
        config = installation.registration(runtime, slot)
        if not broker.task_operation("inspect", config, Deadline(10))["installed"]:
            raise RuntimeError("cold Worktrees installation did not adopt its broker")
        command = [str(interpreter), "-I", "-B", "-X", "utf8", "-m", "agent_worktrees"]
        for index, project in enumerate(projects):
            name = f"broker-fixture-{index}"
            registered.append((name, project))
            run([
                *command, "register", name, "--repo-dir", str(project),
                "--default-branch", "main", "--headless", "--no-agent",
            ], cwd=project)

        def submit(arguments, project):
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [*command, *arguments], cwd=project, env=environment,
                require_job=True, allow_breakaway=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                **pu.no_window_kwargs(),
            )
            if job is None:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError("real submitter caller Job unavailable")
            try:
                stdout, stderr = process.communicate(timeout=45)
                if process.returncode:
                    raise RuntimeError("real detached consumer refused: " + stdout + stderr)
                return json.loads(stdout)["request_id"]
            finally:
                job.terminate_and_wait(5)
                job.close()
                process.wait(timeout=5)

        automatic = ["launch-update", "automatic", "--no-update", "--with-reconcile", "--detach"]
        peer_runtime.mkdir(exist_ok=True)
        # Hold the real installer's own exclusion file, not a substituted planner or executor.
        with (peer_runtime / ".install.lock").open("a+b"):
            started = time.monotonic()
            first = submit(automatic, projects[0])
            handoff_seconds = time.monotonic() - started

            def installer_entered():
                request = core.status(first, root=queue)
                if request.state in {"completed", "interrupted", "blocked"}:
                    raise RuntimeError(
                        "automatic maintenance ended before its installer gate: "
                        + request.log_path.read_text(encoding="utf-8", errors="replace")[-16384:]
                    )
                return request.state == "running" and request.log_path.is_file() and (
                    "provision: agent-ssh [runtime-missing]" in request.log_path.read_text(
                        encoding="utf-8", errors="replace",
                    )
                )

            _observe(installer_entered)
            pending = submit(automatic, projects[0])
            equivalent = submit(automatic, projects[0])
            distinct = submit(automatic, projects[1])
            manual = submit([
                "update", "--phase", "runtimes", "--plugin", "agent-worktrees",
                "--force", "--detach", "--json",
            ], projects[0])
            if pending != equivalent or len({first, pending, distinct, manual}) != 4:
                raise RuntimeError("real consumer identity did not coalesce or preserve distinct scope")
            for _sample in range(10):
                if core.status(first, root=queue).state != "running" or any(
                    core.status(identifier, root=queue).state != "pending"
                    for identifier in (pending, distinct, manual)
                ):
                    raise RuntimeError("manual update or another scope overlapped gated maintenance")
                if (peer_runtime / "deploy-manifest.json").exists():
                    raise RuntimeError("real peer installer crossed its held mutation gate")
                time.sleep(0.1)
        requests = []
        for identifier in (first, pending, distinct, manual):
            request = core.wait(identifier, root=queue, timeout=300)
            if request.state != "completed" or request.exit_code != 0:
                raise RuntimeError(
                    f"real consumer request {identifier} failed: "
                    + request.log_path.read_text(encoding="utf-8", errors="replace")[-16384:]
                )
            requests.append(request)
        first_result = json.loads(requests[0].log_path.with_suffix(".result.json").read_text())
        executed = first_result["executed"][0]["result"]["executed"]
        if not any(item["service"] == "agent-ssh" and item["ok"] for item in executed):
            raise RuntimeError("automatic maintenance did not run its real owning installer")
        if not (peer_runtime / "deploy-manifest.json").is_file():
            raise RuntimeError("automatic maintenance did not publish the real peer runtime")
        if any(after.started_at < before.finished_at for before, after in zip(requests, requests[1:])):
            raise RuntimeError("a later request began before the prior mutation settled")
        from windows_broker_picker_acceptance import qualify as qualify_picker

        run(lifecycle("worktree-manager", "install"), cwd=projects[0])
        manager_version = (manager_runtime / "current-version").read_text().strip()
        manager_slot = manager_runtime / "versions" / manager_version
        manager_config = installation.registration(manager_runtime, manager_slot)
        if not broker.task_operation("inspect", manager_config, Deadline(10))["installed"]:
            raise RuntimeError("cold Manager construction did not adopt its broker")
        picker = qualify_picker(
            root, runtime, command, projects, manager_slot=manager_slot,
            environment=environment, run=run, procutil=pu,
        )
        outcome = {
            "cold_actual_worktrees_construction": True,
            "cold_installer_broker_adoption": True,
            "automatic_real_peer_construction": True,
            "handoff_before_installer_release_seconds": handoff_seconds,
            "equivalent_requests_coalesced": True, "distinct_scopes_completed": True,
            "active_dynamic_plan_not_blindly_coalesced": True,
            "actual_private_project_adoption": True,
            "manual_update_serialized": True, "all_submitter_jobs_closed_before_release": True,
            "credential_free_private_profile": True,
            "picker_recovery": picker,
            "cold_actual_manager_construction": True,
        }
    finally:
        # Canonical removal must prove finite scopes have settled; never force-clear their journal.
        body_failed = sys.exc_info()[0] is not None
        settled = False
        try:
            deadline = time.monotonic() + 180
            rows = core.status(root=queue) if (queue / "queue.sqlite3").is_file() else []
            for request in rows:
                if request.state in {"pending", "running"}:
                    request = core.wait(
                        request.id, root=queue, timeout=max(0.1, deadline - time.monotonic()),
                    )
                if request.state != "completed":
                    raise RuntimeError(f"owned request {request.id} remains {request.state}")
            settled = True
            cleanup["requests"] = True
        except Exception as error:
            cleanup["requests"] = str(error)
        if settled:
            try:
                for name, project in reversed(registered):
                    run([*command, "--project", name, "unregister"], cwd=project, timeout=60)
                cleanup["projects"] = True
            except Exception as error:
                cleanup["projects"] = str(error)
        for name, target in (
            (("worktree-manager", manager_runtime), ("agent-ssh", peer_runtime),
             ("agent-worktrees", runtime)) if settled else ()
        ):
            if not target.exists():
                continue
            try:
                run(lifecycle(name, "uninstall"), cwd=projects[0], timeout=180)
                cleanup[name] = not (
                    target / ("current-version" if name == "worktree-manager" else "deploy-manifest.json")
                ).exists()
                if not cleanup[name]:
                    raise RuntimeError(f"{name} canonical uninstall retained its deployed manifest")
            except Exception as error:
                cleanup[name] = str(error)
        for component, captured in (("broker-task", config), ("manager-broker-task", manager_config)):
            if captured is None:
                continue
            try:
                # Removal deleted the entrypoint. Inspect the captured owned definition
                # through the canonical read-only native adapter, never activation admission.
                inspection = run_caller_owned([
                    str(Path(os.environ["SystemRoot"]) / r"System32\WindowsPowerShell\v1.0\powershell.exe"),
                    "-NoProfile", "-NonInteractive", "-File",
                    str(Path(broker.__file__).with_name("windows_broker_task.ps1")),
                    "-Action", "inspect",
                ], env=environment, timeout=10, max_stdout_bytes=65536, max_stderr_bytes=65536,
                    input_text=json.dumps({"config": captured.task()}))
                if inspection.returncode:
                    raise RuntimeError("owned Task absence inspection failed: " + inspection.stderr[-4096:])
                cleanup[component] = json.loads(inspection.stdout) == {
                    "installed": False, "accepting": False,
                }
            except Exception as error:
                cleanup[component] = str(error)
        (root / "cleanup.json").write_text(json.dumps(cleanup), encoding="utf-8")
        desktop = witness.finish()
        (root / "desktop.json").write_text(json.dumps({
            "positive": positive, "observation": desktop,
        }), encoding="utf-8")
        expected = {
            "requests": True, "projects": True, "agent-ssh": True,
            "agent-worktrees": True, "broker-task": True,
            "worktree-manager": True, "manager-broker-task": True,
        }
        if any(value is not True for value in cleanup.values()) or (not body_failed and cleanup != expected):
            raise RuntimeError("real consumer cleanup was not confirmed: " + json.dumps(cleanup))
    return {**outcome, "cleanup": cleanup, "desktop": desktop}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ephemeral-ci-consent", action="store_true")
    args = parser.parse_args(argv)
    temporary = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    admit(temporary / "ce-broker-probe-consumers-admission", args.ephemeral_ci_consent)
    root = Path(tempfile.mkdtemp(prefix="ce-broker-probe-consumers-", dir=temporary))
    result = {"source": subprocess.check_output(
        ["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"], text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).strip()}
    try:
        result.update(qualify(root), qualified=True)
    except Exception as error:
        result.update(qualified=False, failure="".join(traceback.format_exception(error))[-32768:])
    (root / "consumer-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
