"""Backup — snapshot LanceDB to backup target for fast recovery."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# zstd compression args for the snapshot tarball (#1950). ``-T0`` uses all
# available cores; nightly compression of the multi-GB index is otherwise
# single-threaded and slow (~9.5 min for 7.6 GB on the 2-core WSL box). Level
# stays at zstd's default (3) -- the bulk of the data is embedding vectors that
# compress poorly, so a higher level buys little size for a lot of CPU.
# Overridable for ops tuning (e.g. "-T2 -1" to cap threads / lower level).
_ZSTD_ARGS = os.environ.get("AGENT_INDEX_BACKUP_ZSTD_ARGS", "-T0")


def _write_status(
    config,
    *,
    ok: bool,
    detail: str,
    snapshot: str | None = None,
    size_bytes: int | None = None,
) -> None:
    """Record the outcome of the last backup attempt.

    Written to a local status file (always) and to the backup target (best effort). This
    is a machine-readable heartbeat for an external monitor (or a future
    ``/health`` probe) to consume: a stale ``checked_at`` or ``ok: false`` means
    backups have silently stopped -- the 9-day blind spot the 2026-07-06
    incident exposed. (The load-bearing failure signal today is the non-zero
    exit -> ``systemctl --failed`` path; this file is the richer supplement.)
    """
    status = {
        "ok": ok,
        "detail": detail,
        "checked_at": datetime.now(tz=UTC).isoformat(),
        "snapshot": snapshot,
        "size_bytes": size_bytes,
    }
    payload = json.dumps(status, indent=2)
    with contextlib.suppress(OSError):
        (config.data_dir / "backup-status.json").write_text(payload)
    if ok:
        with contextlib.suppress(OSError):
            config.backup_state_dir.mkdir(parents=True, exist_ok=True)
            (config.backup_state_dir / "backup-status.json").write_text(payload)


def run_backup() -> bool:
    """Create a zstd-compressed tarball of LanceDB data and copy to backup target.

    Returns ``True`` on success, ``False`` on failure. Failures are *loud*:
    they print an actionable message and the caller (CLI) exits non-zero so the
    ``agent-index`` systemd oneshot lands in ``failed`` -- visible to
    ``systemctl --failed`` and service monitoring -- instead of silently
    exiting 0 as the old "print and return" path did.
    """
    from agent_index.index_config import IndexConfig

    config = IndexConfig()
    lance_dir = config.lance_dir
    snapshots_dir = config.backup_snapshots_dir
    state_dir = config.backup_state_dir

    # Probe the actual CIFS mount point FIRST, before any status write. See
    # IndexConfig.backup_mount_root. The no-data path below reports ok=True and
    # mirrors the heartbeat to the backup target state dir; doing that before confirming
    # the mount would write to the local root *under* an unmounted mountpoint
    # and stamp it "ok" -- the exact write-to-local-and-claim-success failure
    # this backup is built to prevent.
    if not os.path.ismount(config.backup_mount_root):
        msg = f"backup target not mounted at {config.backup_mount_root}"
        print(msg)
        print(f"Mount with: sudo mount {config.backup_mount_root}")
        _write_status(config, ok=False, detail=msg)
        return False

    if not lance_dir.exists():
        print("No LanceDB data to back up.")
        _write_status(config, ok=True, detail="no-data")
        return True

    # Create backup target directories (the share is mounted; these are ours to make).
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    snapshot_name = f"agent-index-{today}.tar.zst"
    snapshot_path = snapshots_dir / snapshot_name

    # Build + verify on the *local* disk, then publish to the backup target atomically.
    # Writing the multi-GB tarball straight to CIFS is slow, and verifying it
    # there reads every byte back over the network -- doubling backup target I/O for a
    # nightly job. Local build keeps compression fast, local verify is cheap,
    # and a temp-name-then-rename publish means a partial transfer never appears
    # as a valid snapshot at the destination.
    local_tmp = config.data_dir / f".{snapshot_name}.building"
    backup_tmp = snapshots_dir / f".{snapshot_name}.partial"

    print(f"Creating snapshot: {snapshot_path}")
    start = time.monotonic()

    try:
        subprocess.run(
            [
                "tar",
                f"--use-compress-program=zstd {_ZSTD_ARGS}",
                "-cf",
                str(local_tmp),
                "-C",
                str(lance_dir.parent),
                lance_dir.name,
            ],
            check=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        msg = f"Snapshot tar failed: {exc}"
        print(msg)
        local_tmp.unlink(missing_ok=True)
        _write_status(config, ok=False, detail=msg)
        return False

    # Verify locally (cheap) before shipping: a truncated/corrupt snapshot is
    # worse than none because it looks like a valid recovery point until you
    # try to restore from it.
    if not _verify_snapshot(local_tmp):
        msg = f"Snapshot verification failed (unreadable/empty): {local_tmp}"
        print(msg)
        local_tmp.unlink(missing_ok=True)
        _write_status(config, ok=False, detail=msg)
        return False

    size_bytes = local_tmp.stat().st_size

    # Publish to backup target: copy to a temp name, then rename into place (atomic on the
    # same filesystem) so readers never see a half-written snapshot.
    try:
        backup_tmp.unlink(missing_ok=True)
        shutil.copyfile(local_tmp, backup_tmp)
        backup_tmp.replace(snapshot_path)
    except OSError as exc:
        msg = f"Publishing snapshot to backup target failed: {exc}"
        print(msg)
        backup_tmp.unlink(missing_ok=True)
        local_tmp.unlink(missing_ok=True)
        _write_status(config, ok=False, detail=msg)
        return False
    finally:
        local_tmp.unlink(missing_ok=True)

    elapsed = time.monotonic() - start
    print(f"Snapshot complete: {size_bytes / (1024 * 1024):.1f} MB in {elapsed:.1f}s")

    # Copy state file
    state_file = config.state_file
    if state_file.exists():
        (state_dir / "state.json").write_text(state_file.read_text())
        print("State file backed up to backup target.")

    # Prune old snapshots (keep 7)
    _prune_snapshots(snapshots_dir, keep=7)

    _write_status(
        config,
        ok=True,
        detail="ok",
        snapshot=snapshot_name,
        size_bytes=size_bytes,
    )
    return True


def _verify_snapshot(snapshot_path: Path) -> bool:
    """Return True if the snapshot exists, is non-empty, and lists cleanly."""
    try:
        if snapshot_path.stat().st_size == 0:
            return False
    except OSError:
        return False
    try:
        # `tar --list` decompresses and walks the archive index; a truncated or
        # corrupt zstd stream fails here rather than at restore time.
        subprocess.run(
            ["tar", "--zstd", "-tf", str(snapshot_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, OSError):
        return False
    return True



def _prune_snapshots(snapshots_dir: Path, *, keep: int) -> None:
    """Remove old snapshots, keeping the most recent `keep` files."""
    snapshots = sorted(snapshots_dir.glob("agent-index-*.tar.zst"), reverse=True)
    for old in snapshots[keep:]:
        print(f"Pruning old snapshot: {old.name}")
        old.unlink()
