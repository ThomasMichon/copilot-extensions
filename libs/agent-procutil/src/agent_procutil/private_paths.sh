#!/usr/bin/env bash
# Linux native binding of python_cache_permissions; sourcing is inert.
ce_private_path_check() {
    local path="$1" kind="$2" mode owner current mask
    [[ "$path" == /* && "$path" != *'/../'* && "$path" != *'/./'* && ! -L "$path" ]] || return 1
    read -r owner mode < <(stat -c '%u %a' -- "$path") || return 1
    [[ "$owner" =~ ^[0-9]{1,10}$ && "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
    current="${EUID}"
    [[ "$owner" == "$current" || ( "$kind" == ancestor && "$owner" == 0 ) ]] || return 1
    if [[ "$kind" == file ]]; then [[ -f "$path" ]] || return 1
    else [[ -d "$path" ]] || return 1; fi
    mask=$((8#$mode))
    if [[ "$kind" == ancestor ]]; then
        (( (mask & 0022) == 0 || (mask & 01000) != 0 )) || return 1
    else
        (( (mask & 0077) == 0 )) || return 1
    fi
}

ce_private_directory_prepare() {
    local path="$1" current=/ part count=0
    local -a parts
    [[ "$path" == /* && "$path" != / && "$path" != */ && "$path" != *//* &&
       "$path" != *[$'\n\r\t']* && ${#path} -le 8192 ]] || return 1
    IFS=/ read -r -a parts <<<"$path"
    ce_private_path_check / ancestor || return 1
    for part in "${parts[@]}"; do
        [[ -n "$part" ]] || continue
        (( count+=1 )); (( count <= 64 )) || return 1
        [[ "$part" != . && "$part" != .. ]] || return 1
        current="${current%/}/$part"
        if [[ ! -e "$current" && ! -L "$current" ]]; then
            (umask 077; mkdir -- "$current") 2>/dev/null || [[ -d "$current" ]] || return 1
        fi
        if [[ "$current" == "$path" ]]; then ce_private_path_check "$current" directory || return 1
        else ce_private_path_check "$current" ancestor || return 1; fi
    done
}
