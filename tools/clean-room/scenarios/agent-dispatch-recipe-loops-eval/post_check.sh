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

# Real-forge-state helpers used by the effort-builder/effort-driver
# corroboration below. Plain functions (no cd, no subshell) so they can be
# passed straight to `capture` -- its label.log then holds ONLY the echoed
# command line (line 1) followed by real command output, never a mix.
_find_archive_path() {  # <owner/repo> <slug> -- locate the real dated archive dir
    gh api "repos/$1/git/trees/HEAD?recursive=1" --jq '.tree[] | select(.type=="tree") | .path' \
        | grep -E "^efforts/[0-9]{4}/[0-9]{2}/.*$2\$"
}

_fetch_readme() {  # <owner/repo> <dir-path> [ref] -- raw README.md content via contents API
    gh api -H "Accept: application/vnd.github.raw" "repos/$1/contents/$2/README.md?ref=${3:-HEAD}"
}

_list_issues_by_label() {  # <owner/repo> <label> -- sorted issue numbers carrying that label
    gh issue list --repo "$1" --state all --label "$2" --json number --jq '.[].number' | sort -n
}

_yaml_list_field() {  # <yaml-file> <top-level-key> -- first scalar in a
    # top-level YAML list declared EITHER inline ("key: [a, b]") or in block
    # style ("key:\n  - a\n  - b"). A declaration author may legitimately use
    # either serialization, so a single-shape grep/sed would silently miss
    # the other and skip real corroboration -- parse structurally instead.
    python3 - "$1" "$2" <<'PY' 2>/dev/null
import sys
path, key = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines()
for i, line in enumerate(lines):
    k, sep, v = line.partition(":")
    if not sep or k.strip() != key:
        continue
    val = v.split("#", 1)[0].strip()
    if val.startswith("["):
        inner = val.strip("[]")
        items = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
        if items:
            print(items[0])
        break
    if val:
        print(val.strip("\"'"))
        break
    for nxt in lines[i + 1:]:
        if nxt.strip() == "":
            continue
        if not nxt.startswith((" ", "\t")):
            break
        s = nxt.strip()
        if s.startswith("- "):
            print(s[2:].strip().strip("\"'"))
        break
    break
PY
}

_find_builder_pr_added_dir() {  # <owner/repo> [exclude-file] -- effort-builder's own contract accepts an
    # opened (unmerged) effort PR, not only a direct default-branch commit --
    # a new efforts/active/<dir>/README.md can therefore exist ONLY on an open
    # PR's head, where the default-branch contents diff above would never see
    # it. Scan real open PRs' real file-status (REST, which (unlike the
    # GraphQL files connection) reports true added/modified/removed) for the
    # first newly ADDED effort README, and print "<pr-number> <headRefName>
    # <dir>" for it. A PR number listed in <exclude-file> (PRs that already
    # added such a README BEFORE this run, against a deliberately-retained
    # fixture repo) is skipped so a stale prior run's PR is never mistaken
    # for this run's own evidence.
    local or="$1" exclude="${2:-}" num ref filename dir
    while read -r num ref; do
        [ -z "$num" ] && continue
        if [ -n "$exclude" ] && [ -f "$exclude" ] && grep -qx "$num" "$exclude" 2>/dev/null; then
            continue
        fi
        filename="$(gh api "repos/$or/pulls/$num/files" --jq '.[] | select(.status=="added") | .filename' 2>/dev/null \
            | grep -E '^efforts/active/[^/]+/README\.md$' | head -1)"
        if [ -n "$filename" ]; then
            dir="$(printf '%s' "$filename" | sed -E 's#^efforts/active/([^/]+)/README\.md$#\1#')"
            printf '%s %s %s\n' "$num" "$ref" "$dir"
            return 0
        fi
    done < <(gh pr list --repo "$or" --state open --json number,headRefName --jq '.[] | "\(.number) \(.headRefName)"' 2>/dev/null)
    return 1
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
    # The declaration's own "repo:" value is a bare "owner/name" forge
    # string -- the real queue key its emitted tasks carry (per
    # setup.sh:298-305's project_for_task() note). agent-dispatch's default
    # cwd-based repo resolution instead ALWAYS canonicalizes to a
    # host-qualified "github.com/owner/name" lane -- a DIFFERENT queue key,
    # so an unscoped `list` call here would always return empty. Pass the
    # declaration's own bare repo explicitly via --repo instead of relying
    # on the ambient cwd default.
    _decl_repo="$(grep -E '^repo:' "$_decl" | head -1 | sed -E 's/^repo:[[:space:]]*//' | tr -d '"'"'"'\r')"
    cr_meta "${_recipe}_declaration_name" "$_name"
    cr_meta "${_recipe}_task_label" "$_label"
    cr_meta "${_recipe}_declared_repo" "$_decl_repo"

    _list_out="$CR_LOGDIR/pc-list-${_recipe}.log"
    if [ -z "$_decl_repo" ]; then
        jam "dispatch-config" "declaration $_decl has no 'repo:' value -- cannot scope the list query to its real queue key" "every repository-issue-loop/effort-driver-loop declaration must declare repo:"
        continue
    fi
    if ! capture "pc-list-${_recipe}" -- bash -lc "cd '$FIXTURE_DIR' && agent-dispatch list --repo '$_decl_repo' --label '$_label' --status queued,proposed,claimed,started,suspended,submitted,completed,abandoned,dead_letter"; then
        jam "dispatch-config" "agent-dispatch list failed for $_recipe (see $_list_out)" "coordinator/CLI may be unreachable -- do not conflate this with a genuine empty list"
        continue
    fi
    _count="$(_json_field "$_list_out" "")"
    if [ -z "$_count" ]; then
        jam "dispatch-config" "could not parse agent-dispatch list output for $_recipe (see $_list_out)" "coordinator/CLI output was not valid JSON -- do not conflate this with a genuine empty list"
        continue
    fi
    cr_meta "${_recipe}_task_count" "$_count"

    if [ "$_count" != "0" ]; then
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
            if ! capture "pc-show-${_recipe}" -- bash -lc "cd '$FIXTURE_DIR' && agent-dispatch show '$_selected_id'"; then
                jam "dispatch-config" "agent-dispatch show failed for $_recipe task $_selected_id (see $_show_out)" "coordinator/CLI may be unreachable"
                continue
            fi
            _status="$(_json_field "$_show_out" status)"
            _result_ref="$(_json_field "$_show_out" result_ref)"
            cr_meta "${_recipe}_task_id" "$_selected_id"
            cr_meta "${_recipe}_final_status" "$_status"
            cr_meta "${_recipe}_result_ref" "$_result_ref"
            case "$_status" in
                completed)
                    pass "$_recipe: task $_selected_id reached terminal status '$_status'"
                    ;;
                submitted)
                    # This fixture's declarations all set
                    # require_verification: false specifically so a
                    # genuinely healthy run reaches 'completed' directly --
                    # 'submitted' here means the completion claim is still
                    # awaiting evaluator/manual verification
                    # (repository-issue-loop-adoption.md's diagnosis table),
                    # which is real evidence of a configuration/transition
                    # mismatch, not of recipe success. Do not PASS it.
                    info "$_recipe: task $_selected_id reached 'submitted', not 'completed' -- this fixture disables verification (require_verification: false), so 'submitted' indicates a real configuration/transition mismatch, not recipe success"
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

# Independent real-forge evidence, repo-derived (no hardcoded issue/PR
# numbers or fixture-specific names). Plain PR number/title/state cannot by
# itself corroborate effort-builder's grouping or effort-driver's file
# change + archive move -- capture actual changed files/content and a
# before/after efforts/active/ diff too.
if [ -n "$_remote" ]; then
    _gh_issues_ok=0
    _gh_prs_ok=0
    capture "pc-gh-issues" -- bash -lc "cd '$FIXTURE_DIR' && gh issue list --state all --limit 50 --json number,title,labels,state,comments" && _gh_issues_ok=1
    capture "pc-gh-prs" -- bash -lc "cd '$FIXTURE_DIR' && gh pr list --state all --limit 50 --json number,title,state,headRefName,files,body" && _gh_prs_ok=1
    if [ "$_gh_issues_ok" = 1 ] && [ "$_gh_prs_ok" = 1 ]; then
        pass "captured independent gh issue/PR evidence (incl. changed files) for the fixture repo (cr-logs/pc-gh-issues.log, pc-gh-prs.log)"
    else
        jam "dispatch-config" "independent gh issue/PR evidence capture failed (see cr-logs/pc-gh-issues.log, cr-logs/pc-gh-prs.log)" "a failed capture must not be reported as captured evidence -- check gh auth/rate limits"
    fi

    # Owner/repo derived from the same resolved remote (no hardcoded name).
    _owner_repo="$(printf '%s' "$_remote" | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')"
    # capture()'s log always carries an echoed "$ <cmd>" first line ahead of
    # real output -- diff the RAW post-echo file (a clean, sorted dir
    # listing with no command text) instead of the human-readable capture
    # log, and track success explicitly rather than inferring it from the
    # log file merely existing.
    _efforts_active_after="$CR_LOGDIR/pc-efforts-active-after.log"
    _efforts_active_after_raw="$CR_LOGDIR/pc-efforts-active-after.raw.log"
    _efforts_fetch_ok=0
    printf '  $ gh api repos/%s/contents/efforts/active --jq (dir names) | sort\n' "$_owner_repo" > "$_efforts_active_after"
    if gh api "repos/$_owner_repo/contents/efforts/active" --jq '.[] | select(.type=="dir") | .name' 2>>"$_efforts_active_after" | sort > "$_efforts_active_after_raw"; then
        _efforts_fetch_ok=1
        cat "$_efforts_active_after_raw" >> "$_efforts_active_after"
    fi
    if [ "$_efforts_fetch_ok" = 1 ]; then
        _before_list="${_efforts_active_before:-$CR_LOGDIR/efforts-active-before.log}"
        if [ -f "$_before_list" ]; then
            _added="$(comm -13 "$_before_list" "$_efforts_active_after_raw" 2>/dev/null | tr '\n' ',' )"
            _removed="$(comm -23 "$_before_list" "$_efforts_active_after_raw" 2>/dev/null | tr '\n' ',' )"
            cr_meta "efforts_active_added" "$_added"
            cr_meta "efforts_active_removed" "$_removed"
            _builder_added_dir=""
            _builder_ref="HEAD"
            if [ -n "$_added" ]; then
                pass "efforts/active/ gained new effort dir(s) since setup: $_added (real effort-builder evidence)"
                _builder_added_dir="$(printf '%s' "$_added" | tr ',' '\n' | head -1)"
            else
                # effort-builder's own contract accepts an OPENED (unmerged)
                # effort PR too -- in that valid case the new directory
                # exists only on the PR head, so the default-branch diff
                # above stays empty. Before concluding nothing was built,
                # look for a newly-added efforts/active/<dir>/README.md
                # across the repo's real open PRs (true add/modify/remove
                # status, not just path/title) and corroborate against that
                # PR's own head ref instead.
                if capture "pc-builder-open-pr-search" -- _find_builder_pr_added_dir "$_owner_repo" "${_open_builder_prs_before:-$CR_LOGDIR/open-builder-prs-before.log}"; then
                    _pr_match="$(sed -n '2,$p' "$CR_LOGDIR/pc-builder-open-pr-search.log" | head -1)"
                    if [ -n "$_pr_match" ]; then
                        _builder_pr_num="$(printf '%s' "$_pr_match" | awk '{print $1}')"
                        _builder_ref="$(printf '%s' "$_pr_match" | awk '{print $2}')"
                        _builder_added_dir="$(printf '%s' "$_pr_match" | awk '{print $3}')"
                        cr_meta "effort_builder_open_pr" "$_builder_pr_num"
                        pass "effort-builder's new effort dir '$_builder_added_dir' found on open PR #$_builder_pr_num (ref '$_builder_ref'), not yet on the default branch (real effort-builder evidence)"
                    fi
                fi
                if [ -z "$_builder_added_dir" ]; then
                    info "efforts/active/ gained no new directory since setup, on the default branch or any open PR -- effort-builder may not have created one (or used an existing effort instead)"
                fi
            fi

            if [ -n "$_builder_added_dir" ]; then
                # Plain dir/PR creation doesn't corroborate GROUPING --
                # derive the expected issue set from effort-builder's own
                # declared include_labels (never hardcoded) and confirm the
                # new effort's real README content (read from the resolved
                # ref: default branch or the open PR's own head) actually
                # references them.
                _builder_decl="$REGISTRAR_DIR/effort-builder.yaml"
                _builder_label="$([ -f "$_builder_decl" ] && _yaml_list_field "$_builder_decl" "include_labels")"
                if [ -n "$_builder_label" ]; then
                    if capture "pc-builder-expected-issues" -- _list_issues_by_label "$_owner_repo" "$_builder_label"; then
                        _expected_issues_log="$CR_LOGDIR/pc-builder-expected-issues.log"
                        _expected_issues="$(sed -n '2,$p' "$_expected_issues_log")"
                        _expected_issue_count="$(printf '%s\n' "$_expected_issues" | grep -c . || true)"
                        if [ "${_expected_issue_count:-0}" -gt 0 ]; then
                            if capture "pc-builder-readme" -- _fetch_readme "$_owner_repo" "efforts/active/$_builder_added_dir" "$_builder_ref"; then
                                _builder_readme_log="$CR_LOGDIR/pc-builder-readme.log"
                                _missing_refs=""
                                while IFS= read -r _n; do
                                    [ -z "$_n" ] && continue
                                    grep -Eq "#${_n}([^0-9]|\$)" "$_builder_readme_log" || _missing_refs="$_missing_refs,$_n"
                                done <<<"$_expected_issues"
                                if [ -z "$_missing_refs" ]; then
                                    pass "effort-builder's new effort '$_builder_added_dir' (ref '$_builder_ref') README references all ${_expected_issue_count} expected issue(s) labeled '$_builder_label' (real grouping evidence)"
                                else
                                    info "effort-builder's new effort '$_builder_added_dir' (ref '$_builder_ref') README is missing a reference to issue(s) ${_missing_refs#,} expected from label '$_builder_label'"
                                fi
                            else
                                jam "dispatch-config" "could not read the new effort's README content via the contents API (see cr-logs/pc-builder-readme.log)" "check gh auth/rate limits"
                            fi
                        else
                            info "no issues found under effort-builder's declared label '$_builder_label' -- cannot corroborate grouping"
                        fi
                    else
                        jam "dispatch-config" "could not list issues for effort-builder's declared label '$_builder_label' (see cr-logs/pc-builder-expected-issues.log)" "check gh auth/rate limits"
                    fi
                fi
            fi
            if [ -n "$_removed" ]; then
                pass "efforts/active/ lost dir(s) since setup: $_removed (real effort-driver removal evidence, corroborate against a dated efforts/<year>/<month>/ archive below)"
            else
                info "efforts/active/ lost no directory since setup -- effort-driver may not have archived its effort yet"
            fi
        else
            info "no before-snapshot found (cr-logs/efforts-active-before.log) -- cannot diff efforts/active/"
        fi
    else
        jam "dispatch-config" "could not list efforts/active/ via the GitHub contents API (see cr-logs/pc-efforts-active-after.log)" "check gh auth/rate limits"
    fi

    # effort-driver's own declared effort_slugs (read straight from its
    # declaration, not hardcoded) -- confirm that exact slug is now ABSENT
    # from efforts/active/ (archived) rather than merely inferring it from
    # the generic added/removed diff above, then locate the real dated
    # archive directory via the repo's own git tree (never guessing the
    # date prefix) and capture its README content as corroborating evidence
    # of the actual archive move, not just the slug's disappearance.
    _driver_decl="$REGISTRAR_DIR/effort-driver.yaml"
    if [ -f "$_driver_decl" ]; then
        _driver_slug="$(_yaml_list_field "$_driver_decl" "effort_slugs")"
        if [ -n "$_driver_slug" ] && [ "$_efforts_fetch_ok" = 1 ]; then
            cr_meta "effort_driver_declared_slug" "$_driver_slug"
            _driver_slug_was_active_before=0
            if [ -n "${_before_list:-}" ] && [ -f "$_before_list" ] && grep -qx "$_driver_slug" "$_before_list" 2>/dev/null; then
                _driver_slug_was_active_before=1
            fi
            if grep -qx "$_driver_slug" "$_efforts_active_after_raw" 2>/dev/null; then
                info "effort-driver's declared slug '$_driver_slug' is STILL under efforts/active/ -- not yet archived"
            elif [ "$_driver_slug_was_active_before" != 1 ]; then
                # Absence alone doesn't prove THIS run archived it -- the
                # fixture repo is deliberately retained across reruns, so a
                # slug an EARLIER run already archived would otherwise emit
                # a stale PASS every time. Only attribute the archive move
                # to this run when the setup-time snapshot shows the slug
                # was genuinely active before this turn.
                info "effort-driver's declared slug '$_driver_slug' is not under efforts/active/, but it was already absent before this run started (per the setup-time snapshot) -- cannot attribute the archive move to this run"
            else
                pass "effort-driver's declared slug '$_driver_slug' is no longer under efforts/active/ (archived, per the real contents API read, and confirmed active before this run by the setup-time snapshot)"

                if capture "pc-archive-tree" -- _find_archive_path "$_owner_repo" "$_driver_slug"; then
                    _archive_path="$(sed -n '2,$p' "$CR_LOGDIR/pc-archive-tree.log" | head -1)"
                    if [ -n "$_archive_path" ]; then
                        cr_meta "effort_driver_archive_path" "$_archive_path"
                        if capture "pc-archive-readme" -- _fetch_readme "$_owner_repo" "$_archive_path" && [ -n "$(sed -n '2,$p' "$CR_LOGDIR/pc-archive-readme.log")" ]; then
                            pass "effort-driver's slug '$_driver_slug' is archived at real dated path '$_archive_path' with non-empty README content (real archive-move evidence)"
                        else
                            jam "dispatch-config" "found archive path '$_archive_path' but could not read its README content (see cr-logs/pc-archive-readme.log)" "check gh auth/rate limits"
                        fi
                    else
                        info "slug '$_driver_slug' is gone from efforts/active/ but no matching dated path was found under efforts/<year>/<month>/ -- cannot corroborate the archive move"
                    fi
                else
                    info "could not locate a dated archive path for slug '$_driver_slug' under efforts/<year>/<month>/ (see cr-logs/pc-archive-tree.log) -- cannot corroborate the archive move"
                fi
            fi
        fi
    fi
else
    jam "dispatch-config" "could not resolve the fixture repo's own git remote for independent gh evidence" "verify $FIXTURE_DIR is a real git checkout"
fi

cr_finalize
