"""Invocation-local Worktrees admission under the retained tooling interpreter."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import runtime_input_verifier as verification
from runtime_install_governance import InstallationRefused


def _activate() -> None:
    import _runtime_admission

    _runtime_admission.activate()


def _producer(origin) -> tuple[Path, str]:
    acquired = origin.acquired
    if acquired is None or acquired.commit != origin.identity.source_commit:
        raise InstallationRefused("Admission requires the original held source acquisition")
    if acquired.selection.branch != "main":
        raise InstallationRefused("Ordinary admission requires the promoted source channel")
    locator = acquired.selection.repo
    match = re.fullmatch(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?", locator)
    if match is not None:
        repository = match[1]
    else:
        parsed = urlsplit(locator)
        if (
            parsed.scheme not in ("https", "ssh") or parsed.hostname != "github.com"
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.port is not None
        ):
            raise InstallationRefused("Ordinary release admission requires its GitHub producer")
        repository = parsed.path.strip("/").removesuffix(".git")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise InstallationRefused("Acquired source has an invalid GitHub producer")
    return acquired.checkout, repository


def run(
    *, installation: dict, uv: Path, base_python: Path, deadline: float,
    seed_limits, acquisition_limits, wheel_limits, max_subject_bytes: int,
    max_lock_bytes: int, max_snapshot_bytes: int,
    wait_timeout: float, child_timeout: float, frontend: dict | None = None,
    expected_source_identity: dict | None = None,
):
    """Keep acquisition and the restricted seed alive through native settlement.

    This qualification entry point is not enabled by the ordinary manifest.
    No receipt substitutes for either live verifier or managed publication custody.
    """
    if base_python.resolve(strict=True) != Path(sys.executable).resolve():
        raise InstallationRefused("Admission must start under the selected base interpreter")
    _activate()
    import installer_engine as engine
    import installer_dependency_policy as dependencies
    from runtime_admission_bootstrap import retain_origin_tooling
    from runtime_source_authentication import retain_installation_origin

    with retain_installation_origin(installation=installation, deadline=deadline) as origin:
        checkout, repository = _producer(origin)
        verification.require_source_contract(origin.payload)
        if expected_source_identity is not None and (
            engine.runtime_source.manifest_fields(origin.identity) != expected_source_identity
        ):
            raise InstallationRefused("Admission source differs from the selected frontend source")
        procutil = engine._procutil(engine.FrozenInputs(
            origin.payload, engine._package_roots(origin.payload), "",
        ))
        with retain_origin_tooling(
            origin, installation=installation, uv=uv, python=base_python,
            deadline=deadline, limits=seed_limits,
            acquisition_limits=acquisition_limits, wheel_limits=wheel_limits,
            procutil=procutil,
        ) as ready, verification.LiveInputVerifier(
            ready.verify, deadline=deadline,
        ) as guard, dependencies._temporary_inputs(None) as temporary:
            result_path = dependencies._private_retained_root(Path(temporary)) / "result.json"
            request = {
                "version": 1, "origin": origin.record(), "policy": origin.policy(),
                "payload": str(origin.payload), "checkout": str(checkout),
                "repository": repository, "installation": {
                    key: str(value) if isinstance(value, Path) else value
                    for key, value in installation.items()
                },
                "uv": str(uv), "deadline": deadline,
                "acquisition_limits": asdict(acquisition_limits),
                "wheel_limits": asdict(wheel_limits),
                "max_subject_bytes": max_subject_bytes, "max_lock_bytes": max_lock_bytes,
                "max_snapshot_bytes": max_snapshot_bytes,
                "wait_timeout": wait_timeout, "child_timeout": child_timeout,
                "frontend": frontend, "verifier": guard.identity, "result": str(result_path),
            }
            python = ready.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            executable = engine._native_retained_path(python)
            completed = verification.wait_for_settlement(
                [str(executable), "-I", "-B", str(Path(__file__).resolve()),
                 "--semantic-owner", json.dumps(request, allow_nan=False)],
                dict(os.environ), verifier=guard,
            )
            if completed.returncode:
                raise subprocess.CalledProcessError(completed.returncode, completed.args)
            guard.check()
            return json.loads(result_path.read_text(encoding="utf-8"))


def semantic_owner(request: dict):
    """Borrow the original acquisition; never reacquire its exclusive lease."""
    if not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 1:
        raise InstallationRefused("Unsupported native admission invocation")
    client = verification.current_client(request["verifier"])
    client.check()
    _activate()
    from packaging.markers import default_environment
    from packaging.tags import sys_tags
    from packaging.utils import canonicalize_name
    import installer_dependency_policy as dependencies
    from installer_wheel_acquisition import AcquisitionLimits
    from installer_wheel_inputs import WheelLimits, _check_deadline
    from runtime_profile_policy import plan_discovered_installation_profile
    from runtime_source_authentication import borrow_installation_origin
    from runtime_source_build import retain_source_runtime_inputs
    from runtime_artifact_inputs import retain_artifact_runtime_inputs
    import runtime_install

    deadline = request["deadline"]
    _check_deadline(deadline)
    path_arguments = {
        "legacy_root", "payload_root", "durable_home", "copilot_home", "project_root", "os_profile",
    }
    installation = {
        key: Path(value) if key in path_arguments and value is not None else value
        for key, value in request["installation"].items()
    }
    payload = Path(request["payload"])
    with borrow_installation_origin(
        payload, request["origin"], request["policy"], installation=installation,
        verify_owner=client.check, deadline=deadline,
    ) as origin, dependencies._temporary_inputs(None) as temporary:
        directory = dependencies._private_retained_root(Path(temporary))
        if request["policy"]["selection"]["backend"] != "cpu":
            raise InstallationRefused("Worktrees admission requires its native CPU capability")
        target = {
            "device": "cpu", "environment": default_environment(),
            "tags": [str(tag) for tag in sys_tags()],
        }
        name = canonicalize_name(runtime_install.engine._package_name(payload))
        if name != "agent-worktrees" or origin.identity.plugin != name:
            raise InstallationRefused("Native admission source is not Worktrees")
        roots = [name + "==" + origin.identity.package_version]
        plan = plan_discovered_installation_profile(
            directory, origin, installation=installation, repository=request["repository"],
            source_checkout=Path(request["checkout"]), main_commit=origin.identity.source_commit,
            target=target, component=name, roots=roots,
            max_subject_bytes=request["max_subject_bytes"], deadline=deadline,
        )
        common = dict(
            installation=installation, uv=request["uv"], python=Path(sys.executable),
            acquisition_limits=AcquisitionLimits(**request["acquisition_limits"]),
            wheel_limits=WheelLimits(**request["wheel_limits"]),
            max_snapshot_bytes=request["max_snapshot_bytes"], deadline=deadline,
        )
        if plan.release_selection is not None:
            inputs_owner = retain_artifact_runtime_inputs(
                plan, origin, directory, source_checkout=Path(request["checkout"]),
                procutil=runtime_install.engine._procutil(runtime_install.engine.FrozenInputs(
                    payload, runtime_install.engine._package_roots(payload), "",
                )), **common,
            )
        else:
            inputs_owner = retain_source_runtime_inputs(
                plan, target=target, component=name, max_lock_bytes=request["max_lock_bytes"],
                authenticated_source=origin, **common,
            )
        with inputs_owner as inputs, verification.LiveInputVerifier(
            inputs.verify, deadline=deadline,
        ) as guard:
            result = runtime_install.install(
                Path(request["policy"]["owner"]["runtimeRoot"]), inputs.payload,
                payload=Path(installation["payload_root"]), uv=Path(request["uv"]),
                base_python=Path(sys.executable), wheel_inputs=inputs.wheels,
                expected_source_identity=runtime_install.runtime_source.manifest_fields(inputs.identity),
                wait_timeout=min(request["wait_timeout"], deadline - time.monotonic()),
                child_timeout=min(request["child_timeout"], deadline - time.monotonic()),
                frontend=request["frontend"], input_verifier=guard,
                **({"source_descriptor": installation["source_descriptor"]}
                   if installation.get("source_descriptor") is not None else {}),
            )
            inputs.verify()
            return result


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3 or sys.argv[1] != "--semantic-owner":
            raise InstallationRefused("Native admission requires its existing invocation owner")
        request = json.loads(sys.argv[2])
        result = semantic_owner(request)
        path = Path(request["result"])
        with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
            json.dump(result, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        verification.close_clients()
