# Resolve this payload's own runtime, never a newer mutable selection.
aw_manager_callback() {
    local payload="$1"
    shift
    local slot="${payload%/*}" python bootstrap manager_root
    if [[ "${payload##*/}" == payload && "${slot%/*}" == */versions ]]; then
        python="$slot/bin/python"
        [[ -f "$python" ]] || python="$slot/Scripts/python.exe"
        bootstrap="$slot/.runtime-bootstrap/runtime_launcher.py"
        [[ -f "$python" && -f "$bootstrap" && -f "$slot/.install-complete.json" ]] || return 1
        manager_root="${slot%/versions/*}"
        if [[ "$python" == *.exe ]]; then
            manager_root="$(cygpath -w "$manager_root")" || return 1
            bootstrap="$(cygpath -w "$bootstrap")" || return 1
        fi
        env -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV -u __PYVENV_LAUNCHER__ \
            WORKTREE_MANAGER_ROOT="$manager_root" \
            "$python" -I -S -B -X utf8 "$bootstrap" -m worktree_manager.mux_callback "$@"
    else
        command -v uv >/dev/null 2>&1 || return 1
        uv run --quiet --project "$payload" -m worktree_manager.mux_callback "$@"
    fi
}
