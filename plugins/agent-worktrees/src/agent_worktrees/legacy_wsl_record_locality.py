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
    ``-wsl`` and the detected platform is ``wsl`` -- the ACTUAL execution
    platform, never the overridable ``config.platform`` path-selection
    setting);
  * the ref's machine label resolves -- through the SAME topology sources
    used for identity qualification (key/alias/hostname/display_name), not
    a literal string compare -- to this guest's native counterpart (its
    ``-wsl`` suffix stripped);
  * a tracking record actually exists at the ref's resolved project path;
  * that record's own ``platform`` field is explicitly ``"wsl"`` and its
    ``machine`` field resolves (through the same topology sources) to the
    same native identity -- i.e. the record itself is unambiguous, trusted
    evidence that it was created by (and belongs to) this same WSL guest,
    just under an old unqualified label.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from . import config as cfg

if TYPE_CHECKING:
    from .tracking_claims import ClaimRef


def _native_counterpart_entry(config: cfg.Config) -> tuple[str, dict, object] | None:
    """This WSL guest's native-host registry entry, or ``None`` when this
    isn't (actually executing as, not merely configured as) a WSL guest.

    Returns ``(native_key, entries, native_entry)``; ``entries``/
    ``native_entry`` may be empty/``None`` when the registry can't load --
    callers then fall back to a literal ``native_key`` string compare.
    """
    machine = config.machine or ""
    if not machine.casefold().endswith("-wsl"):
        return None
    if cfg.detect_platform() != "wsl":
        return None
    native_key = machine[: -len("-wsl")]
    if not native_key:
        return None
    try:
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except Exception:
        entries = None
    native_entry = cfg.find_machine_entry(entries, native_key) if entries else None
    return native_key, entries, native_entry


def machine_label_is_legacy_local(label: str, config: cfg.Config) -> bool:
    """True when ``label`` names this WSL guest's legacy native-host
    identity (topology-equivalent to its ``-wsl``-stripped counterpart),
    independent of any specific record -- the pure machine-label half of
    :func:`resolve_legacy_wsl_owner_ref`'s identity test, for comparing two
    bare machine-ref strings rather than resolving one ref's on-disk
    record."""
    if not label:
        return False
    counterpart = _native_counterpart_entry(config)
    if counterpart is None:
        return False
    native_key, entries, native_entry = counterpart
    if entries:
        label_entry = cfg.find_machine_entry(entries, label)
        return label_entry is not None and label_entry is native_entry
    return label.casefold() == native_key.casefold()


def resolve_legacy_wsl_owner_ref(ref: "ClaimRef", config: cfg.Config) -> Path | None:
    """Resolve a legacy WSL owner-ref to its local record path, else ``None``.

    Never raises; never recognizes a non-WSL-platform record, a
    windows/linux/native target, or an unrelated machine.
    """
    from . import tracking

    if ref is None or not ref.machine or not ref.project:
        return None
    if not machine_label_is_legacy_local(ref.machine, config):
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
    # The record's own ``machine`` field is the trusted ground truth for
    # "which legacy label it was actually written under" -- but it may name
    # yet another topology label (hostname/alias/key) than the ref does, so
    # resolve it through the same topology check, not a literal string match.
    if not machine_label_is_legacy_local(record.machine or "", config):
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


def claim_refs_equivalent(left: "ClaimRef", right: "ClaimRef") -> bool:
    """True when two qualified owner-refs name the SAME local worktree
    record, directly (identical machine/project/worktree_id) or via one
    side's record-scoped legacy WSL spelling for the other's live identity.

    Never raises; loads the live config itself (fail-closed to ``False`` if
    unavailable). Used where an offer/accept flow must recognize a bundle
    actor recorded under a pre-split legacy label as the same worktree the
    current, newly-qualified actor now names.
    """
    if left is None or right is None:
        return False
    if (left.machine, left.project, left.worktree_id) == (
        right.machine, right.project, right.worktree_id,
    ):
        return True
    if left.project != right.project or left.worktree_id != right.worktree_id:
        return False
    try:
        config = cfg.load_config()
    except Exception:
        return False

    def _resolve(ref: "ClaimRef") -> Path | None:
        if ref.machine == config.machine:
            return cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
        return resolve_legacy_wsl_owner_ref(ref, config)

    left_path, right_path = _resolve(left), _resolve(right)
    return left_path is not None and left_path == right_path
