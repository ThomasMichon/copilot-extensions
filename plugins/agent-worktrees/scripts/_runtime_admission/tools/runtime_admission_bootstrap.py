"""Compose the retained installation origin with the existing stdlib seed."""
from __future__ import annotations

from contextlib import contextmanager
import inspect
from pathlib import Path

from governed_feed_trust import _opaque_index_identity
from runtime_input_revocation import verify_digests_not_revoked
from runtime_profile_errors import ProfileError
from runtime_source_authentication import (
    AuthenticatedSource, dependencies, resolve_runtime_profile_policy,
)
import installer_packaging_seed as seed
from installer_wheel_inputs import _check_deadline


@contextmanager
def retain_origin_tooling(
    origin: AuthenticatedSource, *, installation: dict, uv: Path, python: Path,
    deadline: float, limits: seed.SeedLimits, acquisition_limits, wheel_limits,
    procutil, env: dict[str, str] | None = None, revoked_inputs=None,
):
    """Retain the real native compiler settings before importing Packaging.

    Acquisition has already selected this origin. Capturing its metadata and
    settings does not select source fallback or execute its build backend.
    """
    _check_deadline(deadline)
    if not isinstance(origin, AuthenticatedSource) or origin.record().get("mode") != "origin":
        raise ProfileError("tooling bootstrap needs its retained installation origin")
    policy = origin.policy()

    def verify_policy(wheels):
        current = resolve_runtime_profile_policy(
            **installation, backend=policy["selection"]["backend"],
        )["runtimeProfiles"]
        if current != policy or origin.record().get("owner_policy") != current.get("policyId"):
            raise ProfileError("tooling bootstrap installation policy changed")
        verify_digests_not_revoked(
            ["sha256:" + origin.identity.assembly_hash, *(wheel["sha256"] for wheel in wheels)],
            revoked_inputs,
        )
        return current

    def verify(wheels):
        origin.verify()
        return verify_policy(wheels)

    def verify_source(payload, source):
        if payload != origin.payload or source != origin.identity:
            raise ProfileError("tooling seed source verifier names another origin")
        origin.verify()

    verify(())
    owner_arguments = {"verify_owner": verify}
    if "source_verifier" in inspect.signature(seed.retain_packaging_seed).parameters:
        owner_arguments = {"verify_owner": verify_policy, "source_verifier": verify_source}
    environment = dependencies._captured_environment(env)
    target = seed._target(python, environment, deadline, procutil)
    with dependencies._temporary_inputs(None) as temporary:
        temporary = dependencies._private_retained_root(Path(temporary))
        projections = []
        with dependencies.retain_uv_source_compile(
            str(uv), [
                str(origin.payload / "pyproject.toml"), "--python", str(python),
                "--python-version", ".".join(str(value) for value in target["version"][:3]),
                "--only-binary", ":all:", "--no-python-downloads",
                "--output-file", str(temporary / "pylock.toml"), "--format", "pylock.toml",
            ],
            cwd=origin.payload, env=environment, trusted_source_roots=(origin.payload,),
            deadline=deadline, native_target=True, _projection=projections,
        ) as transaction, seed.retain_packaging_seed(
            payload=origin.payload, source=origin.identity, policy=policy,
            uv=uv, python=python, **owner_arguments,
            opaque_identity=_opaque_index_identity,
            limits=limits, acquisition_limits=acquisition_limits, wheel_limits=wheel_limits,
            deadline=deadline, procutil=procutil,
            transaction=transaction, projections=tuple(projections),
        ) as ready:
            yield ready
