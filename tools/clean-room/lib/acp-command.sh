#!/usr/bin/env bash

clean_room_quote_bash() {
    printf '%q' "$1"
}

clean_room_build_acp_command() {
    local command='copilot --acp --stdio --allow-all-tools'
    local plugin_dir quoted_dir
    while IFS= read -r plugin_dir; do
        quoted_dir="$(clean_room_quote_bash "$plugin_dir")"
        command+=" --plugin-dir $quoted_dir"
    done
    printf '%s' "$command"
}
