#!/usr/bin/env python3
"""Run a registered standalone distribution's tests with host containment."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from functools import partial
from pathlib import Path

from _admission_protocol import AlreadyRunningError, acquire
from plugin_test_containment import (
    ContainmentError,
    Limits,
    TestSandbox,
    UnreapedProcessError,
    isolated_environment,
    run_contained,
)
from standalone_consumers import (
    STANDALONE_CONSUMERS, local_dependency_source, standalone_build_resources,
)
from seasoned_uv import run_uv_install

REPO = Path(__file__).resolve().parent.parent
COMPONENT_ALIASES = {
    "agent-index-service": "services/index",
    "agent-dispatch-service": "services/dispatch",
    "agent-chat-service": "services/chat",
    "agent-pull-requests-service": "services/pr",
}


def extended_windows_path(path: str) -> str:
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _owned_file_links(root: Path) -> Counter:
    links = Counter()
    for directory, children, files in os.walk(root, followlinks=False):
        current = Path(directory)
        retained = []
        for name in children:
            info = (current / name).lstat()
            if (
                stat.S_ISDIR(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400
            ):
                retained.append(name)
        children[:] = retained
        for name in files:
            info = (current / name).lstat()
            if stat.S_ISREG(info.st_mode):
                links[(info.st_dev, info.st_ino)] += 1
    return links


def _remove_readonly_file(root: Path, function, path: str, exc_info, *, link_counts=None) -> None:
    error = exc_info[1]
    if (
        not isinstance(error, PermissionError)
        or getattr(error, "winerror", None) != 5
        or function not in {os.unlink, os.remove}
    ):
        raise error
    target = Path(path)
    info = target.lstat()
    if (
        not target.resolve().is_relative_to(root)
        or not stat.S_ISREG(info.st_mode)
        or info.st_mode & stat.S_IWRITE
    ):
        raise error
    if info.st_nlink > 1:
        if link_counts is None:
            link_counts = _owned_file_links(root)
        elif not link_counts:
            link_counts.update(_owned_file_links(root))
        if link_counts.get((info.st_dev, info.st_ino)) != info.st_nlink:
            raise error
    target.chmod(info.st_mode | stat.S_IWRITE)
    function(path)


def cleanup_sandbox(temporary: tempfile.TemporaryDirectory) -> None:
    deadline = time.monotonic() + 15
    link_counts = {}
    while True:
        try:
            if os.name == "nt" and isinstance(temporary, tempfile.TemporaryDirectory):
                allocated = Path(temporary.name)
                if allocated.exists() or allocated.is_symlink():
                    info = allocated.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise OSError("refusing redirected temporary cleanup root")
                    root = allocated.resolve()
                    extended = Path(extended_windows_path(str(root))).resolve()
                    shutil.rmtree(
                        str(extended),
                        onerror=partial(_remove_readonly_file, extended, link_counts=link_counts),
                    )
            temporary.cleanup()
            return
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32, 145}:
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def default_python(component: str) -> Path:
    root = REPO / ".test-venvs" / sys.platform / component
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def interpreter_environment(python: Path) -> Path:
    """Discover standard environment roots without executing the interpreter.

    A directory name alone cannot distinguish a venv from an opaque launcher.
    Custom environments with missing configuration need an explicit address.
    """
    parent = python.parent.resolve()
    if parent.name.lower() in {"bin", "scripts"} and (parent.parent / "pyvenv.cfg").is_file():
        return parent.parent.resolve()
    if (parent / "pyvenv.cfg").is_file():
        return parent
    current = Path(sys.executable)
    if python == current.parent.resolve() / current.name:
        return Path(sys.prefix).resolve()
    raise ValueError(
        "cannot discover this custom launcher's environment without executing it; "
        "provide --environment-root for its actual Python environment"
    )


def prepare(component: str, python: Path, *, smoke: bool = False) -> None:
    """Provision dependencies in an existing managed environment root.

    The caller must validate and establish that root, then hold its
    environment-scoped lease throughout preparation and subsequent test use.
    Core packages own their local dependency graph in both development and
    materialized layouts; injecting canonical copies adds conflicting sources.
    """
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    root = python.parent.parent
    if not python.is_file():
        before = root.stat()
        subprocess.run(
            ["uv", "venv", "--allow-existing", str(root)], cwd=REPO, check=True,
            capture_output=True, text=True,
            env={key: value for key, value in os.environ.items() if key != "UV_VENV_CLEAR"},
            **flags,
        )
        after = root.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("uv replaced the leased environment directory")
    sources: list[str] = []
    if component == "services/index":
        sources = [
            str(REPO / "plugins" / "agent-index") + ("" if smoke else "[store,server]"),
        ]
    elif component == "services/dispatch":
        sources = [str(REPO / "plugins" / "agent-dispatch")]
    elif component == "services/pr":
        sources = [
            str(REPO / "plugins" / "agent-pull-requests") + "[fleet]",
            str(REPO / "fleet-manager") + "[service-gateway]",
        ]
    extra = ("test" if smoke else "native,test") if component == "services/index" else "dev"
    if component == "worktree-manager":
        # The Manager distribution includes sibling bin/references payloads,
        # not just an importable wheel; exercise its real source layout.
        sources.append("--editable")
    sources.append(str(REPO / component) + f"[{extra}]")
    # Keep retained source/override topology below the Windows relative-path limit.
    with tempfile.TemporaryDirectory(prefix="standalone-dependencies-", dir=REPO) as temporary:
        overrides: list[str] = []
        if component == "services/index":
            # dev freezes source versions; test the local compatibility seam,
            # not an unrelated released wheel chosen to satisfy that floor.
            override = Path(temporary) / "overrides.txt"
            core = (REPO / "plugins" / "agent-index").resolve().as_uri()
            core_extra = "" if smoke else "[store,server]"
            procutil = local_dependency_source(
                REPO / "plugins" / "agent-index", "agent-procutil", source_root=REPO,
            ).as_uri()
            override.write_text(
                f"agent-index{core_extra} @ {core}\nagent-procutil @ {procutil}\n",
                encoding="utf-8",
            )
            overrides = ["--override", str(override)]
        elif component == "services/dispatch":
            # Same "dev freezes source versions" concern as services/index
            # above: force the agent-dispatch requirement to
            # resolve from this exact local checkout regardless of its
            # declared version, rather than a version-floor match that can
            # go stale.
            override = Path(temporary) / "overrides.txt"
            core = (REPO / "plugins" / "agent-dispatch").resolve().as_uri()
            override.write_text(f"agent-dispatch @ {core}\n", encoding="utf-8")
            overrides = ["--override", str(override)]
        elif component == "services/pr":
            override = Path(temporary) / "overrides.txt"
            core = (REPO / "plugins" / "agent-pull-requests").resolve().as_uri()
            fleet = (REPO / "fleet-manager").resolve().as_uri()
            override.write_text(
                f"agent-pull-requests[fleet] @ {core}\n"
                f"fleet-manager[service-gateway] @ {fleet}\n", encoding="utf-8",
            )
            overrides = ["--override", str(override)]
        run_uv_install(
            "uv", ["--python", str(python), *overrides, *sources],
            trusted_source_roots=(REPO,),
            retained_resource_roots=standalone_build_resources(component, source_root=REPO),
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
            **flags,
        )


def export_chat_membership(
    sandbox: Path, destination: Path | None, profile: str, outcome: int,
) -> int:
    """Validate every Chat outcome; optionally retain its membership report."""
    try:
        report = json.loads((sandbox / "pytest" / "chat-membership.json").read_text("utf-8"))
        if not isinstance(report, dict):
            raise ValueError("membership report must be an object")
    except (OSError, UnicodeError, ValueError) as error:
        print(f"standalone tests: Chat membership unavailable: {error}", file=sys.stderr)
        report = {
            "schema": "agent-chat.test-membership", "version": 1, "profile": profile,
            "membership_status": "unavailable",
            "reason": "collection-not-complete" if isinstance(error, FileNotFoundError)
            else "membership-report-unreadable",
        }
        result = outcome or 1
    else:
        report["membership_status"] = "available"
        result = outcome
        execution = report.get("execution")
        if outcome == 0 and (
            not isinstance(execution, dict) or execution.get("status") != "complete"
        ):
            print("standalone tests: Chat qualification execution is incomplete", file=sys.stderr)
            report["qualification_error"] = "execution-not-complete"
            result = 1
    report.update(test_exit_code=outcome, exit_code=result)
    if destination is None:
        return result
    try:
        with destination.open("x", encoding="utf-8") as output:
            json.dump(report, output, sort_keys=True)
    except OSError as error:
        print(f"standalone tests: Chat membership export failed: {error}", file=sys.stderr)
        return outcome or 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "component",
        type=lambda component: COMPONENT_ALIASES.get(component, component),
        choices=STANDALONE_CONSUMERS,
    )
    parser.add_argument("--python", type=Path, help="test interpreter (default: managed venv)")
    parser.add_argument(
        "--environment-root", type=Path,
        help="actual environment used by a nonstandard --python launcher (never probed)",
    )
    parser.add_argument("--prepare", action="store_true", help="provision declared test dependencies")
    parser.add_argument("--smoke", action="store_true", help="run controller contracts without real deployment cycles")
    parser.add_argument(
        "--chat-suite", choices=("acceptance", "full"),
        help="Chat qualification lane (default: acceptance; full is also required)",
    )
    parser.add_argument(
        "--chat-membership-report", type=Path,
        help="retain the Chat parameter-node map at a new file outside the disposable sandbox",
    )
    parser.add_argument("--admission-wait", type=float, default=0.0)
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args(argv)
    if (args.chat_suite or args.chat_membership_report) and args.component != "services/chat":
        parser.error("Chat qualification options are only defined for services/chat")
    if args.chat_membership_report and (
        args.chat_membership_report.exists() or not args.chat_membership_report.parent.is_dir()
    ):
        parser.error("--chat-membership-report requires a new file in an existing directory")
    chat_suite = (args.chat_suite or "acceptance") if args.component == "services/chat" else None
    if args.timeout is None:
        args.timeout = 180.0 if chat_suite == "acceptance" else 600.0
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if not math.isfinite(args.admission_wait) or args.admission_wait < 0:
        parser.error("--admission-wait must be a non-negative, finite number")
    if args.smoke and args.component != "services/index":
        parser.error("--smoke is only defined for services/index")
    if args.environment_root and not args.python:
        parser.error("--environment-root requires --python")
    if args.environment_root and args.prepare:
        parser.error("--environment-root cannot be used with --prepare")
    # Resolving bin/python itself follows a POSIX venv symlink to the base
    # interpreter and bypasses the venv. Resolve only its containing directory.
    requested_python = args.python or default_python(args.component)
    python = requested_python.parent.resolve() / requested_python.name
    root = REPO / args.component
    if args.prepare and not python.is_relative_to((REPO / ".test-venvs").resolve()):
        parser.error("--prepare may only provision an interpreter under repository .test-venvs")
    if not (root / "tests").is_dir():
        parser.error("component tests are required")
    limits = Limits(wall_seconds=args.timeout)
    try:
        limits.validate()
    except ValueError as exc:
        parser.error(str(exc))
    try:
        default = default_python(args.component)
        managed = python == default.parent.resolve() / default.name
        managed_prepare = args.prepare and python.parent.name.lower() in {"bin", "scripts"}
        environment_root = (
            args.environment_root.resolve() if args.environment_root
            else python.parent.parent.resolve() if managed or managed_prepare
            else interpreter_environment(python)
        )
        if args.environment_root:
            try:
                discovered_root = interpreter_environment(python)
            except ValueError:
                pass  # An opaque launcher requires the caller's explicit address.
            else:
                if discovered_root != environment_root:
                    parser.error("--environment-root disagrees with the interpreter's standard environment")
        if args.prepare and not environment_root.is_relative_to((REPO / ".test-venvs").resolve()):
            parser.error("--prepare may only provision an environment under repository .test-venvs")
        if args.prepare:
            if not managed_prepare or environment_root != python.parent.parent.resolve():
                parser.error("--prepare requires a standard managed venv launcher")
            environment_root.mkdir(parents=True, exist_ok=True)
        elif not environment_root.is_dir():
            parser.error("test environment root must already exist")
        lease = acquire(args.admission_wait, environment_root)
    except AlreadyRunningError as exc:
        print(
            f"[BUSY] Test environment {environment_root} is in use: {exc}. "
            "Use --admission-wait SECONDS to wait for it.",
            file=sys.stderr,
        )
        return 3
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"standalone tests: {exc}", file=sys.stderr)
        return 1
    try:
        # Availability is meaningful only after a competing rebuild releases
        # the environment. No candidate code runs before this exclusion.
        if args.prepare and python.is_file() and not (environment_root / "pyvenv.cfg").is_file():
            parser.error("--prepare requires a standard managed venv, not a copied host interpreter")
        if args.prepare:
            prepare(args.component, python, smoke=args.smoke)
        elif not python.is_file():
            parser.error("a prepared interpreter is required; use --prepare")
        # A component name may contain "/" (a nested consumer, e.g.
        # "services/dispatch") -- safe as a REAL path component (REPO /
        # component), but a tempfile prefix is a bare filename fragment, and
        # an embedded "/" there is misread as a path separator (WinError 3 on
        # Windows: it tries to create the temp dir under a nonexistent
        # subdirectory). Sanitize for this use only.
        safe_component = args.component.replace("/", "-")
        temporary = TestSandbox(prefix=f"{safe_component}-tests-")
        try:
            sandbox = Path(temporary.name)
            env = isolated_environment(os.environ, sandbox)
            env.pop("AGENT_CHAT_TEST_SUITE", None)
            if chat_suite:
                env["AGENT_CHAT_TEST_SUITE"] = chat_suite
            tests = (
                [
                    root / "tests" / "test_cli.py", root / "tests" / "test_config.py",
                    root / "tests" / "test_release.py", root / "tests" / "test_release_cli.py",
                    root / "tests" / "test_staging.py", root / "tests" / "test_staging_cli.py",
                    root / "tests" / "test_staging_cleanup_review.py",
                    root / "tests" / "test_staging_processes.py",
                    root / "tests" / "test_runtime_resource_distribution.py",
                    root / "tests" / "test_distribution.py",
                    root / "tests" / "test_release_distribution.py",
                    root / "tests" / "test_container.py",
                    root / "tests" / "test_fleet_image.py",
                ]
                if args.smoke else [root / "tests"]
            )
            outcome = run_contained(
                [str(python), "-I", "-m", "pytest", "-q", *map(str, tests),
                 "--basetemp", str(sandbox / "pytest")],
                cwd=root,
                env=env,
                sandbox=sandbox,
                limits=limits,
            )
            if chat_suite is not None:
                return export_chat_membership(
                    sandbox, args.chat_membership_report, chat_suite, outcome,
                )
            return outcome
        except UnreapedProcessError:
            temporary.retain()
            raise
        finally:
            if not temporary.retained:
                cleanup_sandbox(temporary)
    except (ContainmentError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"standalone tests: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            for output in (exc.stdout, exc.stderr):
                if output:
                    print(output, file=sys.stderr)
        return 1
    finally:
        lease.release()


if __name__ == "__main__":
    raise SystemExit(main())
