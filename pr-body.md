## Summary
- add picker regressions proving local Resume/Open and New Worktree keep `No Mux` and `AHP` as independent toggles, including the combined case
- add launch-path coverage proving the hosted AHP backend still honors `no_mux`, so backend and presentation stay composable through the relocation
- add the required worktree-manager changefile for the new regression coverage

## Testing
- `cd worktree-manager && uv run --extra dev ruff check --select F,E9 tests/production_picker/test_picker_tui.py tests/test_production_picker_transplant.py`
- `cd worktree-manager && uv run --extra dev pytest tests/production_picker/test_picker_tui.py -k "open_submenu_modifiers_can_be_combined or new_worktree_modifiers_can_be_combined or open_submenu_ahp_toggle_is_arrow_reachable or open_submenu_no_mux_toggle_on_resume or new_worktree_ahp_option_defaults_off_and_toggles or new_worktree_no_mux_option or new_worktree_decision_exits"`
- `cd worktree-manager && uv run --extra dev pytest tests/test_production_picker_transplant.py -k "manager_acts_on_production_picker_new_decision or manager_acts_on_production_picker_resume_decision or run_launch_selects_ahp_after_plan_resolution or run_launch_selects_ahp_without_mux_when_both_requested or run_launch_relocated_script_sets_no_mux_env or run_launch_default_never_calls_ahp or run_launch_delegates_to_relocated_script_on_windows"`
- `cd plugins/agent-worktrees && uv run --extra dev pytest tests/test_execution_leg.py -k legacy_session_backend_derives_generic_execution_leg`
- `cd plugins/agent-worktrees && uv run --extra dev pytest tests/test_execution_leg_cli.py -k "execution_leg_get_translates_and_clear_removes_legacy or worktree_json_projects_legacy_backend_as_execution_leg or execution_leg_get_rejects_opaque_legacy_backend"`
- `python tools/check-install-contract.py`
- `cd worktree-manager && uv run --extra dev pytest` *(known repo baseline still includes the 3 pre-existing `tests/production_picker/test_data_ssh_sources.py` failures; on this machine it also hits 10 unrelated Windows symlink-permission failures before any changed code fails)*
- `cd plugins/agent-worktrees && uv run --extra dev pytest`

## Documentation impact
- none yet beyond the effort log; the Phase 3b effort README will be updated in this PR once the PR number exists for the landed journal entry

Refs #2062
