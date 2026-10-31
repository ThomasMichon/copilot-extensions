# Entry/shutdown extraction

Back to [the effort](README.md). **Implemented locally in `5a946fd6f`;
native qualification and source review/merge remain open.** The target is the existing owned
`plugins/agent-worktrees/src/agent_worktrees/current_roster_entry.py`;
no new module, framework, registry or exit policy is proposed.

## Exact movement

At source `f7a0576181099220aa5da3d334d6f5f812641e9c`:

1. Move the F1-added composition at `__main__.py:51–59` into
   `current_roster_entry.run_module_if_selected()`. It reads `sys.argv`,
   calls the existing pure `select_current_feed`, and calls the unchanged
   `_shutdown_exit.run_and_exit(selected)` only when selected.
   Keep this three-line root gate, plus its existing separating blank line:

   ```python
   if __name__ == "__main__":
       from .current_roster_entry import run_module_if_selected
       run_module_if_selected()
   ```

   A decline simply returns to normal module loading. It must not call,
   import or otherwise pre-run the legacy dispatcher.

2. The header move alone saves six lines but leaves the file above its
   current ceiling. Move only the existing exit-wrapper implementation at
   `__main__.py:6732–6742` into
   `current_roster_entry.run_legacy_console(main)`.
   Keep the public compatibility function **defined in the root**, rather
   than aliasing it to a differently named function:

   ```python
   def console_entry() -> None:
       """Compatibility entry retaining the root's late-bound main callable."""
       from .current_roster_entry import run_legacy_console
       run_legacy_console(main)
   ```

   The target receives the callable and forwards it to the same
   `_shutdown_exit.run_and_exit`; it does not import the root or call `main`
   before entering the exit policy. Move the fuller explanatory documentation
   with that implementation, not into an unrelated comment deletion.

The existing `current_roster_entry.console_entry()` selected/fallback behavior,
`__main__.__getattr__`, bottom module guard, process-module re-export, parser,
context, auth and all launch/lifecycle/monitor handlers stay unchanged.
`pyproject.toml` already points at the owned entry module and needs no change.
`_shutdown_exit.py` needs no edit.

## Import, monkeypatch and lifecycle contracts

- Importing `agent_worktrees.__main__` as a library never executes the early
  helper; the real `__name__ == "__main__"` condition remains in the root.
- The early helper imports no legacy root on decline, so it cannot create the
  former import back-edge. The existing module alias setup still runs in the
  same order when ordinary module loading continues.
- `__main__.console_entry` retains its import path, function name and signature.
  It reads the root's **current** `main` binding at call time, preserving
  `monkeypatch.setattr(m, "main", ...)`.
- The installed entry's existing decline path still calls the root's public
  `console_entry` exactly once; replacing that attribute remains observable.
- Exit-policy imports remain at call time. Patches to
  `_shutdown_exit.run_and_exit` remain observable; no copied policy or cached
  callback bypass is introduced.
- The shared shutdown helper remains solely responsible for return-code,
  SystemExit/interrupt/error behavior and the CPython-specific atexit/flush/
  `_exit` ordering. Both moved paths pass callables, not precomputed results.
- No generic executor, `core()` accessor, lifecycle framework or ownership
  mechanism is added.

## Source-qualified size projection

An in-memory AST-checked replacement, without writing production files,
projects **6,773 → 6,760 lines** (−13) on the stated source:
six lines saved at the header and seven at the compatibility wrapper.
The reported required-CI ceiling on that published head is 6,761.
The target module currently has 89 lines and stays far below the 1,000-line
cap with these two related helpers.

The admitted extraction's actual `check-module-size.py` passes at 6,760 lines;
the target entry module has 111 lines. Recheck against the actual tree after
any integration, rather than treating this snapshot as a pinned future ceiling.
**No baseline file edit, widening, suppression, unrelated comment trimming or
statement packing is part of this proposal.**

## Admission collision check

The complete open-PR file inventory was paginated for these exact source/test
paths. Besides this effort's own #8217, current main-file claims are:

- #6009, head `158f0c7aad11cbdb5e0a97ba49e72e8b3bdc9348`:
  import/anchor-ledger hunk at 93 and scoped-reap hunk at 4608.
- #7611, head `93d05c5c300a92daafbc5ec904813f46dbdf981b`:
  config-writer import at 116 and writer extraction at 5045.

Neither overlaps the proposed header or console-wrapper hunk. No other open
PR in that inventory touches `current_roster_entry.py`,
`test_current_roster_entry.py` or `test_shutdown_exit.py`.
This is hunk-scoped evidence, not a blanket claim on `__main__.py`.
#8031 and #7788 are merged; their native-custody/scheduling code is untouched.
#7449 remains open and owns `manager_launch_cli.py` and its diagnostics tests,
which are outside this proposal. Recheck heads before an admitted edit.

## Necessary affected validation after admission

- `tests/test_shutdown_exit.py`: retain the complete existing exit-policy
  matrix and `test_console_entry_wraps_main` unchanged in intent.
  Its packaging assertion currently expects the old
  `agent_worktrees.__main__:console_entry` declaration; update it to the
  already-authorized F1 declaration
  `agent_worktrees.current_roster_entry:console_entry`, retaining an exact
  mapping assertion and behavior coverage rather than weakening the check.
- `tests/test_current_roster_entry.py`: selected forwarding, decline without
  root import, library-import inertness, late-bound `m.main`, patched public
  `m.console_entry`, and exactly-once shutdown delegation.
- The existing affected module/console/guarded native entry controls retain
  their **same invocation, 1,100 rows, timer boundary, two-second assertion,
  output completeness and error/cleanup checks**. Run only necessary affected
  smoke after pure/static controls pass and retain original raw receipts.
- Ruff on touched files and the ordinary module-size guard, then required
  source CI/review. No tests or benchmarks were run for this proposal.
