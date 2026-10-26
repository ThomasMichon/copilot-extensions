# Embedded by install_hooks with the existing resolved PR/protected-branch policy.
# No runtime, provisioning, validation, registry or remote calls belong here.
aw_updates=
aw_hook_dir=${0%/*}
[ "$aw_hook_dir" != "$0" ] || aw_hook_dir=.
aw_local_hook=$aw_hook_dir/pre-push.local
case "${0##*/}" in pre-push.local) aw_local_hook= ;; esac
aw_newline='
'
aw_linked=0
if [ "$AGENT_WORKTREES_HOOKS" = 1 ] &&
   [ "$aw_pr_enabled" = 1 ] &&
   [ "$AGENT_WORKTREES_PR_PUSH" != 1 ]; then
    aw_dirs=$(git rev-parse --git-dir --git-common-dir) || exit 1
    aw_common=${aw_dirs#*"$aw_newline"}
    [ "${aw_dirs%%"$aw_newline"*}" = "$aw_common" ] || aw_linked=1
fi

aw_check_update() {
    # Git ref names cannot contain whitespace; suppress pathname expansion.
    set -f
    set -- $1
    if [ "$#" != 4 ]; then
        printf '%s\n' 'BLOCKED: malformed pre-push ref update.' >&2
        return 1
    fi
    case "$2$4" in
        *[!0-9a-fA-F]*) return 1 ;;
    esac
    case "${#2}:${#4}" in
        40:40|64:64) ;;
        *) return 1 ;;
    esac
    aw_deleted=0
    case "$2" in *[!0]*) ;; *) aw_deleted=1 ;; esac
    case "$3" in
        refs/heads/worktree/*)
            if [ "$aw_deleted" != 1 ]; then
                printf '%s\n' 'BLOCKED: publish a PR branch, not worktree/*.' >&2
                return 1
            fi ;;
    esac
    if [ "$aw_pr_enabled" = 1 ]; then
        for aw_protected in $aw_protected_refs; do
            if [ "$3" = "$aw_protected" ] &&
               { [ "$aw_deleted" = 1 ] || [ "$AGENT_WORKTREES_PR_PUSH" != 1 ]; }; then
                printf '%s\n' "BLOCKED: protected branch $3; use the managed PR flow." >&2
                return 1
            fi
        done
        case "$3" in
            refs/heads/*)
                if [ "$aw_linked" = 1 ] && [ "$aw_deleted" != 1 ]; then
                    printf '%s\n' 'BLOCKED: use agent-worktrees create-pr or push-changes.' >&2
                    return 1
                fi ;;
        esac
    fi
}

if [ "$AGENT_WORKTREES_HOOKS" = 1 ]; then
    while IFS= read -r aw_line || [ -n "$aw_line" ]; do
        aw_updates=$aw_updates$aw_line$aw_newline
        aw_check_update "$aw_line" || exit 1
    done
    if [ -n "$aw_local_hook" ] && [ -x "$aw_local_hook" ]; then
        printf '%s' "$aw_updates" | "$aw_local_hook" "$@"
        exit $?
    fi
elif [ -n "$aw_local_hook" ] && [ -x "$aw_local_hook" ]; then
    exec "$aw_local_hook" "$@"
fi
exit 0
