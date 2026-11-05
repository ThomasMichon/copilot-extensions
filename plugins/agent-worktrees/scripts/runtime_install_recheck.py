"""Recheck retained inputs across the native candidate publication handover."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from contextlib import ExitStack, contextmanager

PHASES = ("helpers", "selector")


class HandoverError(RuntimeError):
    """The construction owner did not authorize a publication boundary."""


def _associated_lock(lock, source):
    authentication = lock.get("source_authentication")
    policy, files = lock.get("policy"), lock.get("files")
    if (
        not isinstance(authentication, dict) or not isinstance(policy, dict)
        or not isinstance(policy.get("policyId"), str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", policy["policyId"]) is None
        or authentication.get("source") != source.document()
        or authentication.get("owner_policy") != policy["policyId"]
        or authentication.get("mode") != "source"
        or authentication.get("trigger") != lock.get("trigger")
        or not isinstance(files, list)
    ):
        return False
    expected = {}
    for item in files:
        if (
            not isinstance(item, dict) or not isinstance(item.get("filename"), str)
            or not isinstance(item.get("sha256"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"]) is None
            or item["filename"] in expected
        ):
            return False
        expected[item["filename"]] = item["sha256"]
    admission = lock.get("acquisition")
    if not expected:
        return admission is None
    if (
        not isinstance(admission, dict)
        or set(admission) != {"schema", "version", "policy_id", "files"}
        or admission["schema"] != "copilot-extensions.retained-wheel-feed-admission"
        or type(admission["version"]) is not int or admission["version"] != 1
        or not isinstance(admission["policy_id"], str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", admission["policy_id"]) is None
        or not isinstance(admission["files"], list)
    ):
        return False
    actual = {}
    for item in admission["files"]:
        if (
            not isinstance(item, dict) or not isinstance(item.get("filename"), str)
            or not isinstance(item.get("sha256"), str) or item["filename"] in actual
        ):
            return False
        actual[item["filename"]] = item["sha256"]
    return actual == expected


def wheel_profile(binding, source, verify_owner, selection, refused):
    nested = "profile" in binding
    if nested and (
        set(binding) not in (
            {"profile", "source_lock", "build_toolchain"},
            {"profile", "source_lock", "build_toolchain", "build_profile"},
        )
        or not isinstance(binding["source_lock"], dict)
        or binding["source_lock"].get("source") != source.document()
        or not isinstance(binding["build_toolchain"], dict)
        or (
            not isinstance(binding["build_profile"], dict) if "build_profile" in binding
            else not _associated_lock(binding["source_lock"], source)
        )
        or verify_owner is None or selection is None
    ):
        raise refused("Source-built wheels require their retained source owner")
    profile = binding["profile"] if nested else binding
    if not isinstance(profile, dict) or profile.get("component") != "agent-worktrees" or profile.get("release") != {
        "identity": "source:" + source.source_commit,
        "sha256": "sha256:" + source.assembly_hash,
    } or profile.get("mode") != "source":
        raise refused("Closed wheel profile differs from its Worktrees source")
    if nested:
        verify_owner()
    return profile


def wheel_marker_environment(python, refused) -> dict:
    from installer_marker_environment import MARKER_ENVIRONMENT_QUERY

    try:
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
        result = subprocess.run(
            [str(python), "-I", "-S", "-B", "-c", MARKER_ENVIRONMENT_QUERY],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            timeout=30, check=False, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode or len(result.stdout) > 16384:
            raise ValueError("invalid interpreter response")
        observed = json.loads(result.stdout)
        if not isinstance(observed, dict):
            raise ValueError("invalid interpreter response")
        return observed
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise refused("Wheel target interpreter probe failed") from error


@contextmanager
def candidate_channel():
    """Use private parent pipes; descendants inherit neither control endpoint."""
    sys.stdout.flush()
    with ExitStack() as stack:
        reader = stack.enter_context(os.fdopen(os.dup(0), "rb", buffering=0))
        writer = stack.enter_context(os.fdopen(os.dup(1), "wb", buffering=0))
        os.set_inheritable(reader.fileno(), False)
        os.set_inheritable(writer.fileno(), False)
        with open(os.devnull, "rb", buffering=0) as empty:
            os.dup2(empty.fileno(), 0)
        os.dup2(2, 1)

        def check(phase: str) -> None:
            if phase not in PHASES:
                raise HandoverError("Unknown publication boundary")
            writer.write(phase.encode("ascii") + b"\n")
            if reader.readline(16) != b"ok\n":
                raise HandoverError("The construction owner refused publication")

        yield check


def _wait(process: subprocess.Popen) -> tuple[int, BaseException | None]:
    interrupted = None
    while True:
        try:
            return process.wait(), interrupted
        except (KeyboardInterrupt, SystemExit) as error:
            interrupted = interrupted or error


def _close(stream) -> None:
    try:
        stream.close()
    except OSError:
        pass


def supervise(process: subprocess.Popen, verify) -> None:
    """Keep the caller's retained inputs until its candidate actually exits."""
    if process.stdin is None or process.stdout is None:
        raise HandoverError("Publication handover requires private parent pipes")
    seen = []
    failure = None
    try:
        while frame := process.stdout.readline(64):
            expected = PHASES[len(seen)] if len(seen) < len(PHASES) else None
            expected_frame = (expected + "\n").encode("ascii") if expected is not None else None
            if failure is not None or frame != expected_frame:
                failure = failure or HandoverError("Invalid publication handover")
                response = b"deny\n"
            else:
                try:
                    verify(expected)
                except BaseException as error:
                    failure = error
                    response = b"deny\n"
                else:
                    seen.append(expected)
                    response = b"ok\n"
            try:
                process.stdin.write(response)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                failure = failure or HandoverError("Candidate lost its publication handover")
                break
        _close(process.stdin)
        result, interrupted = _wait(process)
        failure = failure or interrupted
    except BaseException:
        _close(process.stdin)
        _wait(process)
        raise
    finally:
        _close(process.stdout)
    if failure is not None:
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise failure
        raise HandoverError("Retained publication verification refused") from failure
    if result:
        raise subprocess.CalledProcessError(result, process.args)
    if tuple(seen) != PHASES:
        raise HandoverError("Candidate omitted a publication boundary")


def run(argv: list[str], environment: dict[str, str], verify=None) -> None:
    process = subprocess.Popen(
        argv, env=environment,
        stdin=subprocess.PIPE if verify is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE if verify is not None else None,
        executable=argv[0] if os.name == "nt" else None,
        text=verify is None,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if verify is not None:
        supervise(process, verify)
        return
    try:
        stdout, stderr = process.communicate()
    except BaseException:
        process.communicate()
        raise
    if stdout:
        print(stdout, end="", flush=True)
    if stderr:
        print(stderr, end="", file=sys.stderr, flush=True)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, argv, stdout, stderr)


def verify_selected(snapshot, selected, wheels, expected) -> None:
    from install_source import _member, _read, native_selection, select_project

    current = select_project(snapshot, "agent-worktrees")
    if (
        current.identity != selected.identity
        or current.source_descriptor != selected.source_descriptor
        or current.project != selected.project
        or _read(_member(snapshot, current.project["profile"])) != wheels.receipt()["binding"]
    ):
        raise HandoverError("Selected wheel source changed before publication")
    if expected is not None and native_selection(current, wheels) != expected:
        raise HandoverError("Selected wheel inputs changed before publication")
