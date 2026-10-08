"""Record-scoped locality for pre-guest-split legacy WSL owner-refs.

Before the native-host/WSL-guest identity split (guest-identity-and-
emitter-recovery), a WSL worktree's tracking record was written under the
bare native host's machine name (e.g. ``example-host``) rather than its own
qualified guest alias (``example-host-wsl``). Those historical records are
real and still local to the WSL guest -- but a plain
``ref.machine == config.machine`` comparison (and
:func:`agent_worktrees.machine_identity.is_local_machine`, which
deliberately treats the native host as foreign to its WSL guest -- no
global native-host alias) both read them as a different, cross-machine
owner.

:func:`resolve_legacy_wsl_owner_ref` is a narrow, **record-scoped**
exception, not a relaxation of the native/guest boundary: it recognizes a
ref as this legacy-local case only when EVERY one of these holds --

  * this machine is itself a WSL guest (``config.machine`` ends with
    ``-wsl`` and the detected platform is ``wsl``);
  * the ref's machine is exactly this guest's native counterpart (its
    ``-wsl`` suffix stripped);
  * a tracking record actually exists at the ref's resolved project path;
  * that record's own ``platform`` field is explicitly ``"wsl"`` and its
    ``machine`` field matches the ref's machine -- i.e. the record itself
    is unambiguous, trusted evidence that it was created by (and belongs
    to) this same WSL guest, just under the old unqualified name.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from . import config as cfg

if TYPE_CHECKING:
    from .tracking_claims import ClaimRef


def resolve_legacy_wsl_owner_ref(ref: "ClaimRef", config: cfg.Config) -> Path | None:
    """Resolve a legacy WSL owner-ref to its local record path, else ``None``.

    Never raises; never recognizes a non-WSL-platform record, a
    windows/linux/native target, or an unrelated machine.
    """
    from . import tracking

    if ref is None or not ref.machine or not ref.project:
        return None
    machine = config.machine or ""
    if not machine.casefold().endswith("-wsl"):
        return None
    if (config.platform or cfg.detect_platform()) != "wsl":
        return None
    native = machine[: -len("-wsl")]
    if not native or ref.machine.casefold() != native.casefold():
        return None
    path = cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
    if not path.exists():
        return None
    try:
        record = tracking.load_record(path)
    except Exception:
        return None
    if record.platform != "wsl":
        return None
    if (record.machine or "").casefold() != ref.machine.casefold():
        return None
    return path


def legacy_wsl_owner_ref_is_local(ref: "ClaimRef") -> bool:
    """Zero-config-arg convenience wrapper: loads the live config itself and
    swallows any resolution failure (fail-closed to "not local legacy")."""
    try:
        config = cfg.load_config()
    except Exception:
        return False
    return resolve_legacy_wsl_owner_ref(ref, config) is not None
