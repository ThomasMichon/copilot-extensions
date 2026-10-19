"""Doctor command arguments, independent of repair execution machinery."""

from __future__ import annotations


def add_parser(sub) -> None:
    sp = sub.add_parser(
        "doctor",
        help="Diagnose (and with --fix, repair) worktree/session health: "
        "corrupt tracking records, empty session registries, stale "
        "status, orphaned empty session shells, cwd/path misalignment, "
        "and drop-in registry hygiene.",
    )
    sp.add_argument(
        "--fix",
        action="store_true",
        help="Apply non-destructive repairs (YAML integrity, "
        "registry/title backfill, stale status, selected creation setup). Default: "
        "report only.",
    )
    sp.add_argument(
        "--gc-sessions",
        action="store_true",
        dest="gc_sessions",
        help="With --fix, also delete empty (0-user-message) "
        "session-state shells and purge their session-store "
        "rows (destructive; guarded by age/lock/current/"
        "registered).",
    )
    sp.add_argument(
        "--prune-pivots",
        action="store_true",
        dest="prune_pivots",
        help="With --fix, also delete stale Picker pivot manifest "
        "files (duplicate/identity-mismatch/missing-target/"
        "invalid-entry) once a live, correct manifest for the "
        "same plugin already exists elsewhere in the registry "
        "(destructive; never touches an operator-authored or "
        "indeterminate manifest -- explicit opt-in, off by "
        "default even with --fix).",
    )
    sp.add_argument(
        "--apply-daemon-health",
        action="store_true",
        dest="apply_daemon_health",
        help="Apply the resident status-monitor daemon-health repair path "
        "(identity-bound duplicate/stale generation reap). Default: report only.",
    )
    sp.add_argument("--json", action="store_true", help="Emit the health report as JSON.")
    sp.add_argument(
        "--guest-distribution", metavar="NAME",
        help="Inspect only the designated WSL guest/default. With --fix, register "
        "a verified named guest entry; never change the host default or repair records.",
    )
    from .tracking_yaml_repair import add_doctor_arguments
    add_doctor_arguments(sp)
    sp.add_argument(
        "--repair-machine-record", metavar="WORKTREE_ID",
        help="Inspect one exact native record's legacy local machine label; "
        "--fix normalizes only that label to the configured local identity.",
    )
    sp.add_argument(
        "--from-machine", metavar="NAME",
        help="Exact previous local machine label; requires --repair-machine-record.",
    )
    sp.add_argument(
        "--to-machine", metavar="KEY",
        help="With --repair-machine-record: assert the exact configured local target; "
        "never selects a different execution space.",
    )
    sp.add_argument(
        "--repair-creation", metavar="WORKTREE_ID",
        help="Inspect only a cold worktree's retained New setup; --fix completes it.",
    )
    sp.add_argument(
        "--seed-id", metavar="ID",
        help="With --repair-creation: exact retained New identity; required with --fix.",
    )
    sp.add_argument(
        "--projection-budget",
        type=int,
        default=256,
        help="Maximum exact session projection relations to inspect (default: 256)",
    )
