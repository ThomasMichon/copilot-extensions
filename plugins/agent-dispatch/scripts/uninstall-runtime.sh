_finite_runtime_uninstall() {
    local mode="$1" python="" candidate helper="$SCRIPT_DIR/installer_engine_uninstall.py"
    [[ -f "$helper" && ! -L "$helper" ]] || helper="$SCRIPT_DIR/../../../libs/installer-engine/installer_engine_uninstall.py"
    for candidate in python3 python; do
        python="$(command -v "$candidate")" || continue
        case "$python" in "$INSTALL_DIR"/*) python=""; continue ;; esac
        break
    done
    [[ -n "$python" ]] || { _fail "Runtime uninstall requires an external metadata Python"; return 1; }
    "$python" -I -B "$helper" --payload-root "$PLUGIN_DIR" --plugin-id agent-dispatch \
        --root "$INSTALL_DIR" --shell "$BASH" "$mode"
}

_finite_owned_pids() {
    local inventory line pid parent command tail
    local pattern='^agent_dispatch (--local )?(serve|supervise( serve)?|(emitter|schedule|evaluator|lane|webhook) serve)( |$)'
    local owned=()
    inventory="$(ps -eo pid=,ppid=,args=)" || return 1
    while IFS= read -r line; do
        read -r pid parent command <<< "$line"
        case "$command" in
            "$INSTALL_DIR"/versions/*/bin/python\ *|"$INSTALL_DIR"/.venv/bin/python\ *|"$INSTALL_DIR"/venv/bin/python\ *)
                tail="${command#* -m }"
                [[ "$tail" =~ $pattern ]] || { _fail "Unclassified Dispatch runtime consumer (pid=$pid)" >&2; return 1; }
                owned+=("$pid") ;;
        esac
    done <<< "$inventory"
    while IFS= read -r line; do
        read -r pid parent command <<< "$line"
        if [[ " ${owned[*]} " == *" $parent "* && " ${owned[*]} " != *" $pid "* ]]; then
            _fail "Dispatch owns a live unclassified child/transport (pid=$pid)" >&2
            return 1
        fi
    done <<< "$inventory"
    [[ ${#owned[@]} -eq 0 ]] || printf '%s\n' "${owned[@]}"
    return 0
}

_finite_stop_owned() {
    local owned pid before now waited
    owned="$(_finite_owned_pids)" || return 1
    for pid in $owned; do
        before="$(ps -p "$pid" -o lstart=,args=)" || continue
        now="$(ps -p "$pid" -o lstart=,args=)" || continue
        [[ "$before" == "$now" ]] || { _fail "Dispatch identity changed before stop"; return 1; }
        kill "$pid" || return 1
        for waited in {1..15}; do
            now="$(ps -p "$pid" -o lstart=,args=)" || { now=""; break; }
            [[ "$now" == "$before" ]] || break
            sleep 1
        done
        [[ "$now" != "$before" ]] || { _fail "Owned Dispatch daemon did not stop"; return 1; }
    done
}
