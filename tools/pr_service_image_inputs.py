"""Disposable offline PR image inputs; no approval, acquisition or deployment."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
from pathlib import Path
import re
import time

from packaging.utils import canonicalize_name

from runtime_profile import ProfileError, RuntimeProfile, validate_profile
from runtime_profile_wheels import profile_wheel_inputs
from installer_dependency_policy import _private_retained_root, _temporary_inputs
from installer_wheel_inputs import WheelLimits, _check_deadline
from installer_wheel_snapshot import WheelSnapshot, _digest


_DOCKERFILE = (
    Path(__file__).resolve().parents[1] / "services" / "pr" / "container" / "Dockerfile"
)
_REQUIRED = frozenset({
    "agent-pull-requests-service", "agent-pull-requests", "fleet-manager", "agent-worktrees",
})


@dataclass(frozen=True)
class ImageInputs:
    path: Path
    python_base: str
    lock_id: str


@contextmanager
def prepare_image_inputs(
    profile: RuntimeProfile, snapshot: WheelSnapshot, *, python_base: str,
    limits: WheelLimits, deadline: float,
):
    """Retain inspected bytes through an offline builder's context lifetime.

    The trusted caller must separately authorize the profile, first-party
    release, target/base and feed acquisition before construction. Neither a
    profile record nor this result grants that authority. Pass python_base as
    the Dockerfile's PYTHON_BASE build argument and keep this context open until
    the builder has consumed every input. This function never runs a builder.
    """
    if (
        not isinstance(python_base, str)
        or re.fullmatch(r"[a-z0-9]+(?:[._:/-][a-z0-9]+)*@sha256:[0-9a-f]{64}", python_base) is None
    ):
        raise ProfileError("PR image requires an explicitly pinned base reference")
    if (
        type(deadline) not in (int, float) or not math.isfinite(deadline)
        or deadline <= time.monotonic()
    ):
        raise ProfileError("PR image requires a finite future deadline")
    if not isinstance(limits, WheelLimits):
        raise ProfileError("PR image requires explicit wheel limits")
    profile = validate_profile(profile.record(), max_tags=limits.max_tags, deadline=deadline)
    record = profile.record()
    environment = record["target"]["environment"]
    if (
        environment["sys_platform"] != "linux" or environment["os_name"] != "posix"
        or environment["platform_system"] != "Linux"
    ):
        raise ProfileError("PR image requires a Linux runtime profile")
    wheels = {canonicalize_name(wheel["name"]): wheel for wheel in record["wheels"]}
    if not _REQUIRED <= wheels.keys():
        raise ProfileError("PR image closure omits a required service or native distribution")
    inputs = profile_wheel_inputs(
        profile, snapshot, max_archive_bytes=limits.max_archive_bytes,
        max_unpacked_bytes=limits.max_expanded_bytes,
        max_metadata_bytes=limits.max_metadata_bytes, limits=limits, deadline=deadline,
    )
    with _temporary_inputs(None) as temporary:
        root = _private_retained_root(Path(temporary)).resolve()
        context = root / "context"
        wheelhouse = context / "wheels"
        wheelhouse.mkdir(parents=True)
        requirements = []
        for name, version, filename, digest in sorted(inputs.distributions):
            _check_deadline(deadline)
            stream = snapshot.archives[filename]
            with (wheelhouse / filename).open("xb") as destination:
                copied_digest, _ = _digest(stream, limits.max_archive_bytes, deadline, destination)
            if copied_digest != digest:
                raise ProfileError("PR image wheel changed during context construction")
            extras = wheels[name]["selected_extras"]
            suffix = "[" + ",".join(sorted(extras)) + "]" if extras else ""
            requirements.append(f"{name}{suffix}=={version} --hash={digest}")
        (context / "requirements.lock").write_bytes(("\n".join(requirements) + "\n").encode("ascii"))
        (context / "Dockerfile").write_bytes(_DOCKERFILE.read_bytes())
        inputs.verify()
        _check_deadline(deadline)
        yield ImageInputs(context, python_base, profile.lock_id)
        inputs.verify()
        _check_deadline(deadline)
