"""Update acquisition shared by automatic and explicitly blocking maintenance.

Healthy launches submit ``launch-update automatic --detach``. Its existing
execution-driver worker calls this stage under custody, then re-plans and
applies eligible work without joining the launching session. The historical
stage-then-join shell flow below remains only for explicit blocking recovery.

The launcher used to run the whole update/reconcile chain **serially before**
showing the Picker: ``copilot plugin update agent-worktrees`` (a ~1.3-1.9s
marketplace network call, even when already at latest) + ``pre-launch`` +
``reconcile-plugins`` x2. That fixed cost is paid on every boot, before the
Picker can paint.

This module implements the **stage** half of the Copilot-style
*stage-then-join* model: run the slow marketplace download **in the background
while the Picker is open**, so the operator's think-time hides it. The launcher
then **joined** (waited for) this stage and **applied** any pending update *after*
the Picker closes and *before* the psmux/Copilot handoff.

Critical safety constraint (why stage != apply): the Picker (``resolve``) runs
from the **installed runtime slot** ``~/.agent-worktrees/versions/<ver>`` (resolved
via the junction-free ``current-version`` marker; #1106). This stage
only touches the **marketplace payload dir**
(``~/.copilot/installed-plugins/copilot-extensions/agent-worktrees``) via
``copilot plugin update`` -- it never rewrites the running venv -- so it is safe
to run concurrently with the Picker. The *apply* (installer: payload->runtime +
pip) runs through the canonical execution owner, never inside this acquisition
function. Blocking launch wrappers may still wait on that owner.

Single-flight: an OS-held lease ensures two
near-simultaneous launches never both hit the marketplace / race the payload
write. PID/start-time records are diagnostics, never permission to reclaim a
live lease. A contending stage returns ``skipped: locked`` without replacing the
owner's shared status. The kernel releases the lease if its owner crashes.

The heavier, order-sensitive steps (the ``pre-launch`` self-update installers
and the two-pass ``reconcile-plugins``) stay in the shell apply step; this
stage only pre-computes the cheap ``pre-launch`` *plan* so the join can skip a
redundant spawn when nothing is stale.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from single_instance_lease import AlreadyRunningError, SingleInstance

from . import config as cfg, locks

# ── Well-known paths (under the runtime dir ~/.agent-worktrees) ────────────
_STATUS_NAME = "updater-status.json"
_LOCK_NAME = "updater.lock"

# The plugin's marketplace payload id and the files whose hashes decide whether
# a downloaded update actually changed anything worth applying.
_PLUGIN_ID = "agent-worktrees@copilot-extensions"
_FINGERPRINT_FILES = (
    "pyproject.toml",
    "plugin.json",
    "scripts/install.ps1",
    "scripts/install.sh",
)

# Upper bound on the marketplace download itself.
_COPILOT_UPDATE_TIMEOUT = 90
_HELD_LOCKS: dict[Path, tuple[SingleInstance, dict]] = {}
_LOCK_MUTEX = threading.Lock()
_LOCK_PROTOCOL = "kernel-lease-v1"


def status_path() -> Path:
    return cfg.install_dir() / _STATUS_NAME


def lock_path() -> Path:
    return cfg.install_dir() / _LOCK_NAME


def _pid_alive(pid: int) -> bool:
    return locks.pid_alive(pid)


def acquire_lock(lock: Path | None = None, *, pid: int | None = None) -> bool:
    """Acquire the single-flight update lock.

    False means contention only. Filesystem/metadata failures raise explicitly.
    Preserve a live legacy PID-only owner during migration, regardless of age.
    """
    lock = (lock or lock_path()).resolve()
    with _LOCK_MUTEX:
        if lock in _HELD_LOCKS:
            return False
        if pid is not None and pid != os.getpid():
            raise ValueError("A stage lease must be owned by the calling process")
        lease = SingleInstance(
            lock.parent, service="agent-worktrees-update-stage",
            lock_name=f"{lock.name}.guard",
        )
        try:
            lease.acquire()
        except AlreadyRunningError as error:
            cause = error.__cause__
            if isinstance(cause, OSError) and cause.errno in (errno.EACCES, errno.EAGAIN):
                return False
            if isinstance(cause, OSError):
                raise cause from None
            raise
        try:
            try:
                previous = json.loads(lock.read_text(encoding="utf-8"))
            except FileNotFoundError:
                previous = None
            if previous is not None:
                if not isinstance(previous, dict) or type(previous.get("pid")) is not int:
                    raise ValueError("Unrecognized update-stage lock metadata")
                if previous.get("protocol") not in (None, _LOCK_PROTOCOL):
                    raise ValueError("Unsupported update-stage lock protocol")
                if previous.get("protocol") != _LOCK_PROTOCOL and locks.lock_is_live(previous):
                    lease.release()
                    return False
            record = {
                "protocol": _LOCK_PROTOCOL,
                "pid": os.getpid(),
                "started": time.time(),
                "start_time": locks.process_start_time(os.getpid()),
                "token": uuid.uuid4().hex,
            }
            _write_status(lock, record)
        except BaseException:
            lease.release()
            raise
        _HELD_LOCKS[lock] = (lease, record)
        return True


def release_lock(lock: Path | None = None, *, pid: int | None = None) -> None:
    """Release only a lease acquired by this process; never unlink its guard."""
    lock = (lock or lock_path()).resolve()
    with _LOCK_MUTEX:
        held = _HELD_LOCKS.get(lock)
        if held is None or (pid is not None and pid != os.getpid()):
            return
        lease, record = held
        try:
            try:
                current = json.loads(lock.read_text(encoding="utf-8"))
            except FileNotFoundError:
                current = None
            if current is not None:
                if current != record:
                    raise ValueError("Update-stage lock metadata changed while its lease was held")
                lock.unlink()
        finally:
            del _HELD_LOCKS[lock]
            lease.release()


# ── Plugin payload discovery + fingerprint ─────────────────────────────────
def discover_plugin_dir(home: Path | None = None) -> tuple[Path | None, str]:
    """Locate the active agent-worktrees plugin payload dir and its layout.

    Mirrors the launcher's discovery: prefer the marketplace layout, fall back
    to a ``_direct`` install. Only the marketplace layout is updatable via
    ``copilot plugin update``.
    """
    home = home or Path.home()
    marketplace = (
        home / ".copilot" / "installed-plugins" / "copilot-extensions"
        / "agent-worktrees"
    )
    if marketplace.exists():
        return marketplace, "marketplace"
    direct_root = home / ".copilot" / "installed-plugins" / "_direct"
    if direct_root.exists():
        for child in sorted(direct_root.iterdir()):
            manifest = child / "plugin.json"
            if manifest.exists():
                try:
                    pj = json.loads(manifest.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if pj.get("name") == "agent-worktrees":
                    return child, "direct"
    return None, ""


def fingerprint(plugin_dir: Path) -> str:
    """Hash the version/launcher/installer files plus the actual application
    source (#2609) to detect a real change.

    The curated ``_FINGERPRINT_FILES`` list alone misses a plugin bug fix that
    lands purely in ``.py`` source under ``src/`` (or a vendored path-
    dependency's ``libs/*/src/``) without touching a version string or any of
    those specific meta-files -- exactly the gap that let a merged fix sit
    undetected on this machine: the marketplace payload had genuinely changed,
    but every staleness check available (this fingerprint, and the deployed-
    vs-payload version-drift check that reads ``pyproject.toml``'s version
    string) agreed nothing needed reinstalling. Hashing the source tree too
    closes that gap the same way :func:`install.ps1's Get-PayloadHash /
    install.sh's _payload_hash <#2609>` already were.

    Uses BLAKE2b (via ``hashlib``, no new dependency) rather than SHA-256:
    this is a pure local change-detector, never compared against an
    externally-supplied or attacker-controlled value, so SHA-256's
    collision-resistance guarantee is unused overhead here -- BLAKE2b is
    materially faster per byte on typical CPUs for the same "did this change"
    question. Deliberately NOT mirrored into ``install.ps1``'s
    ``Get-PayloadHash`` / ``install.sh``'s ``_payload_hash``: .NET's
    ``System.Security.Cryptography`` has no built-in BLAKE2b (only MD5, which
    risks tripping security scanners/policy for a change unrelated to any
    actual security need), and POSIX ``b2sum`` isn't reliably present on
    every platform ``sha256sum`` already is. Those two independently hash a
    different file set for a different purpose (a persisted, cross-run
    completion-marker comparison) and are unaffected by this choice.
    """
    import hashlib

    h = hashlib.blake2b()
    for rel in _FINGERPRINT_FILES:
        fp = plugin_dir / rel
        if fp.exists():
            try:
                h.update(fp.read_bytes())
            except Exception:
                h.update(b"<unreadable>")
        h.update(b"\x00")

    source_roots = [plugin_dir / "src"]
    libs_dir = plugin_dir / "libs"
    if libs_dir.is_dir():
        for lib in sorted(p for p in libs_dir.iterdir() if p.is_dir()):
            candidate = lib / "src"
            if candidate.is_dir():
                source_roots.append(candidate)
    for root in source_roots:
        if not root.is_dir():
            continue
        for fp in sorted(root.rglob("*")):
            if not fp.is_file() or fp.suffix in (".pyc", ".pyo") or "__pycache__" in fp.parts:
                continue
            h.update(str(fp.relative_to(plugin_dir)).replace("\\", "/").encode("utf-8"))
            try:
                h.update(fp.read_bytes())
            except Exception:
                h.update(b"<unreadable>")
            h.update(b"\x00")
    return h.hexdigest()


def _run_copilot_update() -> tuple[bool, str]:
    """Run bounded, caller-owned acquisition. Returns (succeeded, output)."""
    from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
    from shutil import which

    if which("copilot") is None:
        return False, "copilot not on PATH"
    try:
        proc = run_caller_owned(
            ["copilot", "plugin", "update", _PLUGIN_ID],
            timeout=_COPILOT_UPDATE_TIMEOUT,
            max_stdout_bytes=1024 * 1024,
            max_stderr_bytes=1024 * 1024,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out.strip()
    except OwnedCleanupError:
        raise
    except subprocess.TimeoutExpired:
        return False, f"copilot plugin update timed out ({_COPILOT_UPDATE_TIMEOUT}s)"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"copilot plugin update error: {e}"


def _resolve_before_fingerprint(prior: dict, plugin_dir: Path) -> tuple[str, str]:
    """Reuse the previous stage's recorded AFTER-fingerprint as this run's
    BEFORE-fingerprint when it is trustworthy, skipping one full-tree hash
    walk (roughly half the fingerprinting cost) in the common case where
    nothing changed between stage runs.

    Safe only because of this module's own docstring's "Critical safety
    constraint": the marketplace payload directory this hashes
    (``~/.copilot/installed-plugins/copilot-extensions/agent-worktrees``) is
    exclusively written by ``copilot plugin update`` -- nothing else in this
    stage-then-join flow mutates it between runs. The prior AFTER-fingerprint
    is trusted only when it was recorded for the SAME ``plugin_dir`` by a
    real, non-skipped, completed run; any other prior state (first run ever,
    a locked/no-plugin-dir skip, a different plugin_dir, or a non-marketplace
    layout that never computed one) falls back to a fresh full-tree hash so a
    mismatch never silently hides a real change. Returns
    ``(fingerprint, "cached" | "computed")`` -- the source tag is carried into
    the status file purely for diagnosability (tests/`doctor` can see which
    path a run took), never used to change behavior.
    """
    if (
        not prior.get("skipped")
        and prior.get("stage_done")
        and prior.get("plugin_dir") == str(plugin_dir)
        and isinstance(prior.get("fingerprint"), str)
        and prior["fingerprint"]
    ):
        return prior["fingerprint"], "cached"
    return fingerprint(plugin_dir), "computed"


def stage(
    *,
    status: Path | None = None,
    lock: Path | None = None,
    home: Path | None = None,
) -> dict:
    """Perform one background staging pass and write the status file.

    Steps (all safe w.r.t. the running Picker's venv):
      1. Single-flight: acquire the lock, else return ``skipped: locked``.
      2. Discover the marketplace payload dir (else ``skipped``).
      3. Fingerprint (reusing the prior run's AFTER-hash when trustworthy --
         see :func:`_resolve_before_fingerprint`) -> ``copilot plugin
         update`` -> fingerprint; the diff is ``plugin_changed`` (the shell
         apply runs the installer iff changed).
      4. Pre-compute the cheap ``pre-launch`` staleness plan so the join can
         skip a redundant spawn when nothing is stale.

    Returns the status dict (also written to ``status``).
    """
    status = status or status_path()
    lock = lock or lock_path()
    result: dict = {"stage_done": False, "ts": time.time()}

    try:
        acquired = acquire_lock(lock)
    except (OSError, ValueError) as error:
        result["stage_error"] = f"Update-stage lock unavailable: {error}"
        print(result["stage_error"], file=sys.stderr)
        return result
    if not acquired:
        result.update(stage_done=True, skipped="locked", plugin_changed=False)
        return result

    try:
        prior = read_status(status)
        _write_status(status, result)
        plugin_dir, layout = discover_plugin_dir(home)
        if plugin_dir is None:
            result.update(
                stage_done=True, skipped="no-plugin-dir", plugin_changed=False
            )
            _write_status(status, result)
            return result

        result["plugin_dir"] = str(plugin_dir)
        result["layout"] = layout

        plugin_changed = False
        copilot_output = "skipped (non-marketplace layout)"
        if layout == "marketplace":
            before, before_source = _resolve_before_fingerprint(prior, plugin_dir)
            ran, copilot_output = _run_copilot_update()
            result["acquisition_ok"] = ran
            after = fingerprint(plugin_dir) if ran else before
            result["fingerprint"] = after
            result["before_fingerprint_source"] = before_source
            plugin_changed = ran and (before != after)

        # Version-drift reconcile (#2826): the fingerprint diff above only
        # catches a payload change *within this run*. If the marketplace payload
        # already advanced on a prior run but the runtime venv was never
        # reinstalled -- an interrupted apply, or launches that went through the
        # binstub (which stages nothing) -- the venv is left behind
        # *permanently*: every later stage sees "already at latest", so
        # ``plugin_changed`` stays False and the stale venv is never reconciled.
        # Independently compare the deployed runtime version (deploy-manifest,
        # written by the installer) against the payload version and force an
        # apply when they diverge, so a lagging venv self-heals on the next boot.
        venv_drift = False
        deployment_diagnostic = None
        try:
            from . import reconcile

            payload_ver = reconcile.payload_version(plugin_dir)
            installer_environment, runtime_root = (
                reconcile.runtime_installer_environment(
                    "agent-worktrees",
                    plugin_dir,
                    base={},
                    home=home,
                )
            )
            deployed_ver = reconcile.runtime_deployed_version(
                "agent-worktrees", root=runtime_root
            )
            result["payload_version"] = payload_ver
            result["deployed_version"] = deployed_ver
            result["runtime_root"] = str(runtime_root)
            result["environment"] = installer_environment
            result["unset_environment"] = list(reconcile._RUNTIME_ENV_UNSET)
            venv_drift = bool(
                payload_ver and deployed_ver and payload_ver != deployed_ver
            )
            if plugin_changed or venv_drift:
                deployment_diagnostic = reconcile.publication_diagnostic(
                    "agent-worktrees", plugin_dir, runtime_root,
                    context=(
                        Path(installer_environment["COPILOT_EXTENSIONS_CONTEXT"])
                        if "COPILOT_EXTENSIONS_CONTEXT" in installer_environment else None
                    ),
                )
        except ValueError as error:
            result["venv_drift_error"] = str(error)
            result["runtime_apply_blocked"] = "installation-context-invalid"
            plugin_changed = False
        except Exception as error:
            result["venv_drift_error"] = str(error)
            result["runtime_apply_blocked"] = "venv-drift-check-failed"
            plugin_changed = False
        result["venv_drift"] = venv_drift
        if venv_drift and not plugin_changed:
            plugin_changed = True
            result["plugin_changed_reason"] = "venv-drift"
        if deployment_diagnostic is not None:
            result["runtime_apply_blocked"] = deployment_diagnostic["reason"]
            result["runtime_apply_diagnostic"] = deployment_diagnostic
        if result.get("runtime_apply_blocked"):
            plugin_changed = False

        result["plugin_changed"] = plugin_changed
        result["copilot_output"] = copilot_output

        # Cheap staleness plan for the join (best-effort; never fatal).
        try:
            from .__main__ import plan_pre_launch

            result["prelaunch"] = plan_pre_launch()
        except Exception as e:
            result["prelaunch"] = {"action": "continue", "reason": f"error: {e}"}

        result["stage_done"] = True
        _write_status(status, result)
        return result
    finally:
        release_lock(lock)


def _write_status(status: Path, data: dict) -> None:
    """Publish a complete record atomically; never hide publication failures."""
    status.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{status.name}.", suffix=".tmp", dir=status.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, status)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_status(status: Path | None = None) -> dict:
    """Read the last stage status (empty dict if missing/unreadable)."""
    status = status or status_path()
    try:
        return json.loads(status.read_text(encoding="utf-8"))
    except Exception:
        return {}


def indicator_state(
    *,
    status: Path | None = None,
    lock: Path | None = None,
) -> str:
    """Picker-facing update state for the version indicator (#1430).

    Returns one of:
      "paused"    -- this launch explicitly disabled updates;
      "checking"  -- a background stage has live owner metadata;
      "available" -- the stage finished and the marketplace payload changed
                     (an update is staged, ready to apply on launch/refresh);
      "current"   -- the stage finished and nothing changed (up to date);
      "idle"      -- no stage has run / it was skipped (no plugin dir, etc.).

    Read-only and cheap (two small files); safe to poll on the render tick.
    """
    if os.environ.get("WORKTREE_NO_UPDATE") == "1":
        return "paused"

    if locks.lock_is_live(locks.read_lock(lock or lock_path())):
        return "checking"
    st = read_status(status)
    if not st or not st.get("stage_done"):
        return "idle"
    if st.get("skipped"):
        return "idle"
    return "available" if st.get("plugin_changed") else "current"


def cmd_stage_update(args) -> int:
    """CLI: run one background staging pass (launcher backgrounds this)."""
    st = getattr(args, "status", None)
    if getattr(args, "indicator_state", False):
        payload = {
            "version": 1,
            "indicator_state": indicator_state(
                status=Path(st) if st else None,
            ),
        }
        if getattr(args, "json", False):
            print(json.dumps(payload))
        else:
            print(payload["indicator_state"])
        return 0
    result = stage(status=Path(st) if st else None)
    if getattr(args, "json", False):
        print(json.dumps(result))
    return 1 if result.get("stage_error") else 0
