"""Fixed native installation and broker handoffs under the suite driver."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager
from functools import wraps

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
NATIVE_ENV = "AGENT_WORKTREES_NATIVE_INSTALLATION"
CONTEXT_KEYS = (
    "AGENT_HOME", "AGENT_WORKTREES_UPDATE_CONTEXT", "AGENT_WORKTREES_PAYLOAD_ROOT",
    "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "WORKTREE_MANAGER_ROOT",
    "WORKTREE_MANAGER_ENGINE_ARGV", "WORKTREE_MANAGER_ENGINE_CMD", "CE_UPDATE_AUTH",
    "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT", "COPILOT_PROJECT_DIR",
)
TRANSIENT_KEYS = (
    "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_PLUGIN_INSTALL_STAGED",
    "COPILOT_PLUGIN_STAGED_FROM", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", NATIVE_ENV,
)


def bootstrap(payload: Path):
    import installer_engine as engine

    required = {"agent-update-driver", "agent-single-instance-lease", "agent-procutil", "agent-zdd"}
    packages = {
        engine._package_name(package): package for package in engine._package_roots(payload)
        if engine._package_name(package) in required
    }
    if packages.keys() != required:
        raise RuntimeError("Worktrees installation requires its complete execution-driver source closure")
    sys.path[:0] = [str(packages[name] / "src") for name in sorted(required)]
    from update_driver import core

    return core


@contextmanager
def custody(*, required: bool = False):
    from update_driver import core, borrowed_custody, participant, participant_environment
    from update_driver.participant import ENVIRONMENT_KEY

    owner = borrowed_custody(root=core.default_root())
    if owner is None:
        inherited = participant_environment()
        if inherited:
            owner = participant(root=core.default_root(), witness=inherited[ENVIRONMENT_KEY])
    if owner is None:
        if required:
            raise RuntimeError("Worktrees installation requires live driver custody")
        yield None
    else:
        with owner, owner.execution_scope():
            yield owner


@contextmanager
def required_custody():
    if os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        yield None
    else:
        if (SCRIPTS.parent / "pyproject.toml").is_file():
            bootstrap(SCRIPTS.parent)
        with custody(required=True) as owner:
            yield owner


def owned(function):
    @wraps(function)
    def execute(*args, **kwargs):
        with required_custody():
            return function(*args, **kwargs)

    return execute


def child_environment() -> dict[str, str]:
    driver = sys.modules.get("update_driver")
    inherited = driver.participant_environment() if driver is not None else {}
    return {**os.environ, **inherited}


def adopt_broker(root: Path, slot: Path) -> None:
    """Adopt before publication or callbacks, with the complete slot retained."""
    if os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        return
    import versioned_runtime as runtime
    from update_driver import core

    with custody(required=True) as owner:
        owner.assert_owned()
        python = runtime.slot_python(root, slot.name)
        if python is None:
            raise RuntimeError("Constructed Worktrees has no broker adoption interpreter")
        command = [
            str(python), "-I", "-m", "update_driver.windows_broker",
            "--runtime-root", str(root), "--slot", str(slot),
            "--execution-root", str(core.default_root()),
        ]
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
            env=child_environment(),
        )
        try:
            stdout, stderr = process.communicate()
        except BaseException:
            _settle(process.communicate)
            raise
        if process.returncode:
            if stderr:
                print(stderr, end="", file=sys.stderr, flush=True)
            raise RuntimeError(f"Constructed Worktrees broker adoption failed ({process.returncode})")
        receipt = json.loads(stdout)
        if not isinstance(receipt, dict) or set(receipt) != {"adopted"} or receipt["adopted"] is not True:
            raise RuntimeError("Constructed Worktrees returned no broker adoption receipt")
        owner.assert_owned()


def _settle(wait):
    while True:
        try:
            return wait()
        except (KeyboardInterrupt, SystemExit):
            continue


def _source_digest(payload: Path) -> str:
    import installer_engine as engine

    return engine.runtime.fingerprint_source(engine._source_files(engine._package_roots(payload)))


def _native_arguments(args) -> list[str]:
    values = ["-Action", args.action, "-InstallDir", str(args.root.resolve())]
    for option in ("project_name", "bootstrap_python", "owner"):
        value = getattr(args, option)
        if value is not None:
            values.extend(["-" + "".join(word.title() for word in option.split("_")), str(value)])
    for option in ("force", "zero_downtime", "provision_missing_runtime"):
        if getattr(args, option):
            values.append("-" + "".join(word.title() for word in option.split("_")))
    return values


def _execute(args) -> int:
    from update_driver import core

    with custody(required=True) as owner:
        request = core.status(owner.request_id, root=owner.root)
        arguments = _native_arguments(args)
        if request.state != "running":
            raise RuntimeError("Native installer has no running execution owner")
        if request.spec.backend == "engine-native-install":
            if (
                request.spec.argv != tuple(arguments)
                or request.spec.source != _source_digest(args.payload)
                or request.spec.project != str(args.root.resolve())
            ):
                raise RuntimeError("Native installation changed after driver admission")
        elif request.spec.backend not in {
            "manager", "engine", "engine-stage-update", "engine-reconcile-plugins",
        }:
            raise RuntimeError("Execution owner does not authorize native Worktrees installation")
        owner.assert_owned()
        if args.check_native:
            return 0
        environment = child_environment()
        environment[NATIVE_ENV] = "1"
        process = subprocess.Popen(
            [str(args.shell), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(args.payload / "scripts" / "install.ps1"),
             *arguments],
            env=environment, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            code = process.wait()
        except BaseException:
            _settle(process.wait)
            raise
        owner.assert_owned()
        return code


def _foreground(args, driver) -> int:
    if any(os.environ.get(key) for key in (
        "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION",
    )):
        raise RuntimeError("Native driver admission must precede publication custody")
    arguments = _native_arguments(args)
    digest = _source_digest(args.payload)
    command = [
        sys.executable, "-I", "-S", "-B", "-X", "utf8", str(SCRIPTS / "runtime_install_driver.py"),
        "--payload", str(args.payload), "--root", str(args.root),
        "--shell", str(args.shell), "--action", args.action, "--participant",
    ]
    for option in ("project_name", "bootstrap_python", "owner"):
        value = getattr(args, option)
        if value is not None:
            command.extend(["--" + option.replace("_", "-"), str(value)])
    for option in ("force", "zero_downtime", "provision_missing_runtime"):
        if getattr(args, option):
            command.append("--" + option.replace("_", "-"))
    context = {key: os.environ[key] for key in CONTEXT_KEYS if key in os.environ}
    request = driver.enqueue(
        driver.RequestSpec(
            "engine-native-install", tuple(arguments), project=str(args.root.resolve()),
            cwd=str(Path.cwd().resolve()), source=digest, context=tuple(context.items()),
        ),
        runner=driver.TrustedRunner(
            tuple(command), str(Path.cwd().resolve()), context_keys=(*CONTEXT_KEYS, *TRANSIENT_KEYS),
        ),
    )
    driver.drain()
    result = driver.wait(request.id, output=sys.stdout)
    if result.state != "completed" or result.exit_code is None:
        raise RuntimeError(f"Native Worktrees installation did not complete: {result.state}")
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--shell", required=True, type=Path)
    parser.add_argument("--action", required=True, choices=(
        "install", "update", "provision", "repair", "dev", "dev-release",
    ))
    parser.add_argument("--project-name")
    parser.add_argument("--bootstrap-python", type=Path)
    parser.add_argument("--owner", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--zero-downtime", action="store_true")
    parser.add_argument("--provision-missing-runtime", action="store_true")
    parser.add_argument("--participant", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check-native", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("Native Worktrees driver entry requires Windows")
    args.payload = args.payload.resolve(strict=True)
    args.root = args.root.resolve()
    args.shell = args.shell.resolve(strict=True)
    if args.shell.name.lower() not in {"pwsh.exe", "powershell.exe"}:
        parser.error("Native Worktrees installation requires an exact PowerShell executable")
    if args.payload != SCRIPTS.parent:
        parser.error("Native installation uses only this executing payload")
    driver = bootstrap(args.payload)
    with custody() as owner:
        if owner is None:
            if args.participant or args.check_native:
                raise RuntimeError("Native installation participant requires live driver custody")
            return _foreground(args, driver)
        return _execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
