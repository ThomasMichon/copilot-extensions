#!/usr/bin/env bash
# agent-dispatch-recipe-loops-eval/post_check.sh -- programmatic ground-truth
# AFTER the orchestrator's turn.
#
# Generic by construction: it never hardcodes a fixture issue number, label
# string, or repo name -- it discovers each declaration's own `name`/`task_label`
# straight from the already-cloned registrar/*.yaml files on disk, then uses
# agent-dispatch's own real `list`/`show` CLI (scoped to the fixture repo's own
# git remote) plus `gh issue list`/`gh pr list` against that same remote for
# independent, real forge evidence. It NEVER substitutes for the literal-mode
# judgment -- a transcript that only *claims* completion in prose, with the
# coordinator/forge evidence disagreeing, is a FALSE-PASS regardless of what
# this script reports; its job is to make the ground truth VISIBLE. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

FIXTURE_DIR="$HOME/recipe-fixture"
REGISTRAR_DIR="$FIXTURE_DIR/.copilot-extensions/agent-dispatch/registrar"

: "${CR_SCENARIO_NAME:=agent-dispatch-recipe-loops-eval}"
export CR_SCENARIO_NAME
# Reuse the report the setup phase opened (append post-check evidence to it).
cr_init 2>/dev/null || true

_json_field() {  # <log-file> <field-name>
    python3 -c '
import json, sys
content = open(sys.argv[1], encoding="utf-8").read()
start = content.find("{")
start2 = content.find("[")
idx = min((i for i in (start, start2) if i >= 0), default=-1)
if idx < 0:
    print("")
    raise SystemExit
content = content[idx:]
data = json.loads(content)
if isinstance(data, list):
    print(len(data))
    raise SystemExit
value = data.get(sys.argv[2])
print(value if value is not None else "")
' "$1" "$2" 2>/dev/null
}

phase 9 "post-check: ground-truth after the orchestrator's turn"

if [ ! -d "$REGISTRAR_DIR" ]; then
    jam "dispatch-config" "no registrar dir at $REGISTRAR_DIR -- setup.sh did not clone the fixture repo" "re-run setup.sh; see cr-logs/clone-fixture.log"
    cr_finalize
fi

_remote="$(cd "$FIXTURE_DIR" && git remote get-url origin 2>/dev/null || true)"
cr_meta "fixture_remote" "$_remote"

for _decl in "$REGISTRAR_DIR"/*.yaml; do
    _recipe="$(basename "$_decl" .yaml)"
    _name="$(grep -E '^name:' "$_decl" | head -1 | sed -E 's/^name:[[:space:]]*//' | tr -d '"'"'"'\r')"
    _label="$(grep -E '^task_label:' "$_decl" | head -1 | sed -E 's/^task_label:[[:space:]]*//' | tr -d '"'"'"'\r')"
    cr_meta "${_recipe}_declaration_name" "$_name"
    cr_meta "${_recipe}_task_label" "$_label"

    _list_out="$CR_LOGDIR/pc-list-${_recipe}.log"
    capture "pc-list-${_recipe}" -- bash -lc "cd '$FIXTURE_DIR' && agent-dispatch list --label '$_label' --status queued,proposed,claimed,started,suspended,submitted,completed,abandoned,dead_letter" || true
    _count="$(_json_field "$_list_out" "")"
    cr_meta "${_recipe}_task_count" "${_count:-0}"

    if [ -n "${_count:-}" ] && [ "${_count:-0}" != "0" ]; then
        # Task lists are newest-first, and a loop's fast cadence can create
        # extra queued/abandoned occurrences after an earlier one already
        # reached a terminal state -- always picking items[0] would then
        # report the LATER, less-interesting task and hide the real
        # evidence. Prefer completed/submitted; fall back to a terminal
        # failure (abandoned/dead_letter, also real evidence worth
        # surfacing) before finally falling back to the newest non-terminal
        # task.
        _selected_id="$(python3 -c '
import json, sys
content = open(sys.argv[1], encoding="utf-8").read()
idx = content.find("[")
items = json.loads(content[idx:]) if idx >= 0 else []
if not items:
    print("")
    raise SystemExit
def pick(statuses):
    return next((t for t in items if t.get("status") in statuses), None)
chosen = pick(("completed", "submitted")) or pick(("abandoned", "dead_letter")) or items[0]
print(chosen.get("id", ""))
' "$_list_out" 2>/dev/null)"
        if [ -n "$_selected_id" ]; then
            _show_out="$CR_LOGDIR/pc-show-${_recipe}.log"
            capture "pc-show-${_recipe}" -- bash -lc "cd '$FIXTURE_DIR' && agent-dispatch show '$_selected_id'" || true
            _status="$(_json_field "$_show_out" status)"
            _result_ref="$(_json_field "$_show_out" result_ref)"
            cr_meta "${_recipe}_task_id" "$_selected_id"
            cr_meta "${_recipe}_final_status" "$_status"
            cr_meta "${_recipe}_result_ref" "$_result_ref"
            case "$_status" in
                submitted|completed)
                    pass "$_recipe: task $_selected_id reached terminal status '$_status'"
                    ;;
                abandoned|dead_letter)
                    info "$_recipe: task $_selected_id ended '$_status' -- check the transcript for the stated reason"
                    ;;
                "")
                    info "$_recipe: could not read a status for task $_selected_id (see $_show_out)"
                    ;;
                *)
                    info "$_recipe: task $_selected_id is still '$_status' (not yet terminal) -- real headless work may still be in flight"
                    ;;
            esac
        else
            info "$_recipe: a task list came back but no task id could be extracted (see $_list_out)"
        fi
    else
        info "$_recipe: no task found yet under label '$_label' -- check 'doctor' output in the transcript for why"
    fi

    _doctor_out="$CR_LOGDIR/pc-doctor-${_recipe}.log"
    if [ "$_recipe" != "effort-driver" ]; then
        capture "pc-doctor-${_recipe}" -- bash -lc "cd '$FIXTURE_DIR' && agent-dispatch repository-issue-loop doctor '$_decl'" || true
    fi
done

# Independent real-forge evidence, repo-derived (no hardcoded issue/PR numbers).
if [ -n "$_remote" ]; then
    capture "pc-gh-issues" -- bash -lc "cd '$FIXTURE_DIR' && gh issue list --state all --limit 50 --json number,title,labels,state,comments" || true
    capture "pc-gh-prs" -- bash -lc "cd '$FIXTURE_DIR' && gh pr list --state all --limit 50 --json number,title,state,headRefName" || true
    pass "captured independent gh issue/PR evidence for the fixture repo (cr-logs/pc-gh-issues.log, pc-gh-prs.log)"
else
    jam "dispatch-config" "could not resolve the fixture repo's own git remote for independent gh evidence" "verify $FIXTURE_DIR is a real git checkout"
fi

cr_finalize
