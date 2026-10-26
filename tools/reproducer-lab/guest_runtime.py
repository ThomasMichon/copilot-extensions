"""Explicit first preparation of image-built runtimes in owned guest state."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

from guest_operation import GuestOperation, LOCK, STATE
from process import run

SEED = Path("/opt/lab/runtime-seed")
RUNTIME = STATE / "runtime"


def _seed_descriptor() -> int:
    if SEED.resolve(strict=True) != SEED:
        raise ValueError("runtime seed must be the fixed immutable image directory")
    descriptor = os.open(SEED, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (
            (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022
            or not os.fstatvfs(descriptor).f_flag & os.ST_RDONLY
            or not os.path.samestat(info, SEED.lstat())
        ):
            raise ValueError("runtime seed must belong to the read-only image")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _runtime_identity() -> os.stat_result:
    info = RUNTIME.lstat()
    if (
        not stat.S_ISDIR(info.st_mode) or info.st_dev != STATE.lstat().st_dev
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (1000, 1000, 0o700)
        or RUNTIME.resolve(strict=True) != RUNTIME
    ):
        raise ValueError("runtime destination lost private guest-state posture")
    return info


def _require_initial_state(*, destination_created: bool) -> None:
    expected = {LOCK.name, RUNTIME.name} if destination_created else {LOCK.name}
    seen: set[str] = set()
    with os.scandir(STATE) as entries:
        for entry in entries:
            if entry.name not in expected or entry.name in seen:
                raise ValueError("retained guest state requires its original owner; no runtime preparation")
            seen.add(entry.name)
    if seen != expected:
        raise ValueError("initial guest state changed before runtime preparation")


def prepare_runtime(custody: GuestOperation) -> None:
    """Copy the fixed image seed once; never repair or resume a partial copy.

    Requires separately authorized and qualified guest resources, source/image,
    current storage monitoring and absence of competing writers. This does not
    install a service, create a task or authorize launching a copied runtime.
    No completion marker or installation receipt is fabricated.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("runtime preparation must run outside an event loop")
    custody.assert_owned()
    _require_initial_state(destination_created=False)
    descriptor = _seed_descriptor()
    try:
        # Exclusive creation retains even an incomplete attempt on any failure.
        RUNTIME.mkdir(mode=0o700)
        destination = _runtime_identity()
        custody.assert_owned()
        _require_initial_state(destination_created=True)
        if not os.path.samestat(os.fstat(descriptor), SEED.lstat()):
            raise ValueError("runtime seed changed before copying")
        result = run(
            ["/usr/bin/cp", "--recursive", "--no-dereference",
             "--preserve=timestamps", "--", str(SEED) + "/.", str(RUNTIME)],
            timeout=30, max_stdout_bytes=16 * 1024,
            env={"HOME": "/home/lab", "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        )
        if result.returncode:
            raise RuntimeError("guest runtime copy failed; retain the partial attempt")
        custody.assert_owned()
        _require_initial_state(destination_created=True)
        if (
            not os.path.samestat(destination, _runtime_identity())
            or not os.path.samestat(os.fstat(descriptor), SEED.lstat())
        ):
            raise ValueError("runtime source or destination changed during copying")
    finally:
        os.close(descriptor)
