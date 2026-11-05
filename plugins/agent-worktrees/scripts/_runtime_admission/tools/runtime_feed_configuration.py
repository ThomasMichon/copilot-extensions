"""Installation profile policy composed with the stdlib native feed owner."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from build_toolchain_lock import _MARKER_ENV_QUERY_SCRIPT, sanitize_subprocess_env
from governed_feed_trust import _opaque_index_identity
from runtime_profile import ProfileError, ProfileUnavailable, validate_profile
from runtime_profile_policy import InstallationProfilePlan
from runtime_source_lock import SourceLockCandidate

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "libs" / "installer-engine"))
sys.path.insert(0, str(_ROOT / "libs" / "installation-context"))
import installer_dependency_policy as dependencies
from installer_native_feed import NativeFeedError, NativeFeedInputs, capture_native_feed_inputs  # noqa: F401
from installer_native_feed import _basic  # noqa: F401 -- compatibility for existing byte-construction controls
from installer_wheel_inputs import _check_deadline
from installation_context import resolve_runtime_profile_policy


def _capture(
    transaction, projection, original, profile, policy, uv, deadline, *,
    source_lock=None, source_resolution=False,
):
    if source_resolution:
        if profile is not None or source_lock is not None:
            raise ProfileError("source resolution preflight cannot carry an artifact or lock")
        wheels, profile_policy = None, "source-resolution"
    elif source_lock is None:
        wheels = profile.record()["wheels"]
        profile_policy = profile.record()["acquisition_policy"]
    else:
        if profile is not None:
            raise ProfileError("source feed capture must not carry an artifact profile")
        wheels = source_lock.record()["files"]
        profile_policy = "source-lock:" + source_lock.lock_id
    try:
        return capture_native_feed_inputs(
            transaction, projection, original, profile, policy, uv, deadline,
            wheels=wheels, profile_policy=profile_policy, opaque_identity=_opaque_index_identity,
        )
    except NativeFeedError as error:
        raise ProfileError(str(error)) from error


@contextmanager
def retain_native_feed_inputs(
    plan: InstallationProfilePlan, *, installation: dict, uv: str, python: Path,
    deadline: float, env: dict[str, str] | None = None,
):
    """Read actual owner policy and native settings without resolving/installing."""
    _check_deadline(deadline)
    current = resolve_runtime_profile_policy(**installation)["runtimeProfiles"]
    if current.get("status") != "ready" or current != plan.policy():
        raise ProfileError("native acquisition policy differs from its installation owner")
    if plan.release_selection is None:
        raise ProfileUnavailable("native artifact acquisition requires selected release mode")
    profile = validate_profile(plan.release_selection.selected.profile.record(), deadline=deadline)
    environment = dependencies._captured_environment(env)
    cwd = Path(installation["payload_root"])
    if not python.is_absolute() or not python.is_file():
        raise ProfileError("native feed capture needs the exact selected interpreter")
    try:
        observed = subprocess.run(
            [str(python), "-I", "-S", "-c", _MARKER_ENV_QUERY_SCRIPT],
            env=sanitize_subprocess_env(environment), capture_output=True, text=True,
            timeout=deadline - time.monotonic(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        _check_deadline(deadline)
        actual, expected = json.loads(observed.stdout), profile.record()["target"]["environment"]
        if observed.returncode or not isinstance(actual, dict):
            raise ProfileError("native feed target probe did not return marker facts")
        if actual != expected:
            fields = sorted(key for key in set(actual) | set(expected) if actual.get(key) != expected.get(key))
            raise ProfileError("native feed target differs from the selected interpreter: " + ", ".join(fields))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise ProfileError("native feed target probe failed or timed out") from None
    with dependencies._temporary_inputs(None) as temporary:
        root = dependencies._private_retained_root(Path(temporary)).resolve()
        requirements = root / "selected.in"
        requirements.write_text("".join(
            wheel["name"] + "==" + wheel["version"] + "\n" for wheel in profile.record()["wheels"]
        ), encoding="utf-8")
        projection = []
        with dependencies.retain_uv_compile(
            uv, [str(requirements), "--python", str(python), "--only-binary", ":all:",
                 "--no-python-downloads", "--python-version", profile.record()["target"]["environment"]["python_full_version"]],
            env=environment, cwd=cwd, trusted_source_roots=(root, cwd),
            deadline=deadline, native_target=True, _projection=projection,
        ) as transaction:
            if len(projection) != 2:
                raise ProfileError("native feed capture did not produce paired native configurations")
            yield _capture(transaction, projection[1], projection[0], profile, current, transaction.argv[0], deadline)


def _source_policy(plan: InstallationProfilePlan, installation: dict) -> dict:
    current = resolve_runtime_profile_policy(**installation)["runtimeProfiles"]
    if (
        current.get("status") != "ready" or current != plan.policy()
        or plan.release_selection is not None
        or plan.source_trigger not in (
            "release-index-unavailable", "artifact-unavailable", "approved-profile-unavailable",
        )
        or not current["selection"]["allowSourceFallback"]
    ):
        raise ProfileError("source feed capture lacks its actual owner")
    return current


def validate_source_resolution_inputs(
    plan: InstallationProfilePlan, *, installation: dict, transaction, projections: list[str],
    deadline: float,
) -> str:
    """Validate the held native settings before the compiler can request packages."""
    _check_deadline(deadline)
    current = _source_policy(plan, installation)
    if len(projections) != 2:
        raise ProfileError("source resolution preflight needs paired native configurations")
    return _capture(
        transaction, projections[1], projections[0], None, current,
        transaction.argv[0], deadline, source_resolution=True,
    ).policy_id


@contextmanager
def retain_source_feed_inputs(
    plan: InstallationProfilePlan, source_lock: SourceLockCandidate, *, installation: dict, deadline: float,
):
    """Reuse the source compiler's held native configuration, never re-resolve it."""
    _check_deadline(deadline)
    current = _source_policy(plan, installation)
    if (
        not isinstance(source_lock, SourceLockCandidate) or source_lock.native_lock is None
        or source_lock.transaction is None or len(source_lock.projections) != 2
    ):
        raise ProfileError("source feed capture lacks its native lock")
    record = source_lock.record()
    if (
        record["policy"] != current or record["trigger"] != plan.source_trigger or record["mode"] != "source"
        or record["native_lock_sha256"] != "sha256:" + hashlib.sha256(source_lock.native_lock).hexdigest()
        or record["requirements_sha256"] != "sha256:" + hashlib.sha256(source_lock.requirements).hexdigest()
    ):
        raise ProfileError("source feed capture differs from its retained lock and owner")
    yield _capture(
        source_lock.transaction, source_lock.projections[1], source_lock.projections[0],
        None, current, source_lock.transaction.argv[0], deadline, source_lock=source_lock,
    )
    _check_deadline(deadline)
    if resolve_runtime_profile_policy(**installation)["runtimeProfiles"] != current:
        raise ProfileError("source acquisition policy changed during consumption")
