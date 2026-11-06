"""Runner-owned daemon leases and post-cleanup environment auditing."""

from __future__ import annotations


def run_contained(
    owner, command, *, cwd, env, sandbox, limits, daemon_purpose,
    daemon_lease, qualification, native_application,
):
    # Keep the original owner's native operations and instrumentation seams.
    if type(native_application) is not bool:
        raise ValueError("native application testing must be explicitly selected")
    if native_application:
        temporary = owner.os.environ.get("RUNNER_TEMP")
        if (
            qualification is not None
            or owner.os.environ.get("GITHUB_ACTIONS") != "true"
            or owner.os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or not temporary or not owner.Path(temporary).is_absolute()
            or not sandbox.resolve().is_relative_to(owner.Path(temporary).resolve())
            or owner.os.environ.get(owner.CONTAINED_ENV) == "1"
        ):
            raise owner.ContainmentError("Native application testing requires its disposable hosted CI owner.")
        env = dict(env)
        env.pop(owner.CONTAINED_ENV, None)
    lifecycle_results = []
    if qualification is not None:
        if owner.os.name != "nt":
            raise owner.QualificationError("Absolute native qualification requires the original Windows Job owner.")
        if limits != owner.Limits(wall_seconds=owner.WORK_SECONDS):
            raise owner.QualificationError(
                "Native qualification must preserve the original process, memory and poll limits."
            )
        qualification.validate(sandbox, owner.time.monotonic)
    environment_snapshot = owner._read_registry_environment()
    if qualification is not None:
        qualification.boundary.require(owner.time.monotonic, work=True)
    unreaped = None
    try:
        with owner.daemon_test_lifecycle(
            owner=f"contained-test-runner:{owner.os.getpid()}",
            registry_root=sandbox.resolve() / "daemon-instances",
            purpose=daemon_purpose,
            lease=daemon_lease,
            **(
                {"deadline": qualification.boundary.whole_deadline, "cleanup_results": lifecycle_results}
                if qualification is not None else (
                    {"cleanup_results": lifecycle_results} if native_application else {}
                )
            ),
        ) as daemon_environment:
            result = owner._run_contained_process(
                command, cwd=cwd, env={**env, **daemon_environment},
                sandbox=sandbox, limits=limits,
                **({"qualification": qualification} if qualification is not None else {}),
                **({"native_application": True} if native_application else {}),
            )
        if native_application and any(
            outcome.status not in ("settled", "missing") for outcome in lifecycle_results
        ):
            raise owner.UnreapedProcessError("Native application daemon cleanup remains unsettled.")
        if qualification is not None:
            qualification.boundary.require(owner.time.monotonic)
            qualification.lifecycle_settled = all(
                outcome.status in ("settled", "missing") for outcome in lifecycle_results
            )
    except owner.UnreapedProcessError as exc:
        unreaped = exc
        raise
    finally:
        try:
            if qualification is not None:
                qualification.boundary.require(owner.time.monotonic)
            changed = owner._registry_environment_drift(environment_snapshot)
            if changed:
                print(
                    "[HOST-STATE] contained test changed persistent Windows "
                    f"environment state; detected without rollback: {', '.join(changed)}",
                    file=owner.sys.stderr,
                )
        except BaseException as audit_error:
            if unreaped is None:
                raise
            raise owner.UnreapedProcessError(
                f"{unreaped}; environment audit also failed: "
                f"{type(audit_error).__name__}: {audit_error}"
            ) from unreaped
    if changed:
        return 125
    if qualification is not None:
        qualification.registry_unchanged = True
        qualification.require_settled(owner.time.monotonic)
    return result
