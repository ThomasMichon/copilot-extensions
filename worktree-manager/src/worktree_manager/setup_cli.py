"""Plan or apply prerequisites and the existing core installer."""

from __future__ import annotations

from .core_install import core_status, install_command, install_core
from .prereqs import detect_baseline, missing
from .provision import apply as provision_apply
from .provision import plan as provision_plan
from .provision import restart_needed


def cmd_setup(rest: list[str], *, banner: str) -> int:
    do_apply = "--apply" in rest
    gaps = missing(detect_baseline())
    actions = provision_plan(gaps)
    core = core_status()

    print()
    print(f"  {banner} — setup {'(APPLY)' if do_apply else '(plan / dry-run)'}")
    print()
    if not actions:
        print("  prerequisites: all satisfied — nothing to provision.")
    else:
        print("  prerequisites to provision (in order):")
        for action in actions:
            tag = "manual" if action.manual else action.method
            print(f"    - {action.name}  [{tag}]"
                  f"{'  (restart after)' if action.changes_path else ''}")
            if action.command:
                print(f"        $ {action.command}")
            if action.note:
                print(f"        {action.note}")
    results = provision_apply(actions, dry_run=not do_apply)
    if do_apply:
        failures = [
            result for result in results
            if result.action.auto and (not result.ran or result.returncode != 0)
        ]
        for result in failures:
            detail = (
                f"exit code {result.returncode}" if result.ran
                else result.skipped_reason or "installer did not run"
            )
            print(f"  ! prerequisite {result.action.name} failed: {detail}")
        gaps = missing(detect_baseline())
        if failures or gaps:
            if gaps:
                print(f"  ! prerequisites still needed: {', '.join(g.name for g in gaps)}")
                if restart_needed(results):
                    print("  ⟳ PATH changed — RESTART your shell (or open a new one)")
                    print("    before retrying so the newly-installed tools are found.")
            print("  ! core installation was not attempted.")
            return 1

    print()
    print("  agent-worktrees core:")
    command = install_command()
    if core.installed:
        print("    ✓ already installed (idempotent — nothing to do).")
    elif command is None:
        print("    ! the real installer needs a copilot-extensions checkout;")
        print("      run `worktree-manager setup` from (or after adopting) a checkout.")
    else:
        print(f"    state: {core.state} → drive the harness's own installer:")
        print(f"        $ {' '.join(command)}")
    result = install_core(dry_run=not do_apply)
    if do_apply and (
        (result.ran and result.returncode != 0)
        or (not result.ran and result.reason != "already-installed")
    ):
        detail = (
            f"exit code {result.returncode}" if result.ran
            else result.reason or "installer did not run"
        )
        print(f"    ! core install failed: {detail}")
        return 1
    if do_apply and result.ran:
        print(f"    installer exit code: {result.returncode}")
        core = core_status()
        if core.installed:
            print("    ✓ core install complete.")
        else:
            print(f"    ! core still {core.state} after install — see output above.")

    print()
    if not do_apply:
        print("  (plan only — nothing was changed. Re-run with --apply to execute.)")
    print()
    return 0 if not gaps and core.installed else 1
