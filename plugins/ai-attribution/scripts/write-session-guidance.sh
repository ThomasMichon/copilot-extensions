#!/usr/bin/env bash
# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py,
# UNLESS a warm per-repository guidance cache lets this wrapper serve the
# session-scoped file directly without spawning python at all (process-count
# reduction; see copilot-agent-runtime#22031's sibling investigation).
# write_session_guidance.py remains the sole authority for the cache's format
# and every safety invariant (session-id shape, symlink/reparse defense,
# atomic replace, byte budget); this fast path mirrors those checks narrowly
# (via jq/sha256sum/git, all portable POSIX/coreutils tools) and falls
# through to the unchanged python path at the first sign of anything
# unexpected -- it never weakens what python would have done. Mirrors
# write-session-guidance.ps1's fast path; keep the two in sync.
set -uo pipefail

root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)}}}"
script="$root/scripts/write_session_guidance.py"

MAX_INPUT_BYTES=65536
GUIDANCE_MAX_BYTES=4096
CACHE_FORMAT_VERSION=1
GUIDANCE_HEADER=$'# AI attribution session guidance\n\n'
SESSION_ID_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'

run_python_fallback() {
    # $1, if provided, is the already-drained stdin text to re-supply.
    local python
    python="$(command -v python3 || command -v python || true)"
    if [[ -z "$python" || ! -f "$script" ]]; then
        printf '{}'
        return 0
    fi
    if [[ $# -ge 1 ]]; then
        PYTHONPATH="" "$python" "$script" <<<"$1" || printf '{}'
    else
        PYTHONPATH="" "$python" "$script" || printf '{}'
    fi
}

# A fast path needs jq (JSON) and sha256sum/shasum (cache-key hashing); on
# any host missing either, skip straight to the unchanged python path rather
# than risking a parse/hash mismatch against write_session_guidance.py.
have_jq="$(command -v jq || true)"
have_sha256="$(command -v sha256sum || true)"
sha256_uses_shasum=0
if [[ -z "$have_sha256" ]]; then
    have_sha256="$(command -v shasum || true)"
    sha256_uses_shasum=1
fi

stdin_text="$(cat)"
stdin_bytes=${#stdin_text}
if [[ -z "$have_jq" || -z "$have_sha256" || "$stdin_bytes" -gt "$MAX_INPUT_BYTES" ]]; then
    run_python_fallback "$stdin_text"
    exit 0
fi

fast_path_ok=0
session_id="$(printf '%s' "$stdin_text" | jq -er '.sessionId // empty' 2>/dev/null)"
cwd="$(printf '%s' "$stdin_text" | jq -er '.cwd // empty' 2>/dev/null)"

if [[ -n "$session_id" && "$session_id" =~ $SESSION_ID_PATTERN \
    && -n "$cwd" && -z "${AI_ATTRIBUTION_FORCE_REFRESH:-}" ]]; then
    resolved_home="${HOME:-}"
    if [[ -n "$resolved_home" ]]; then
        # Repo cache key: same derivation as write_session_guidance.py's
        # _repo_cache_key -- git toplevel when resolvable, else raw cwd,
        # SHA-256 of the UTF-8 bytes, first 16 hex chars.
        repo_root="$cwd"
        if top="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)" && [[ -n "$top" ]]; then
            repo_root="$top"
        fi
        if [[ "$sha256_uses_shasum" -eq 1 ]]; then
            key="$(printf '%s' "$repo_root" | shasum -a 256 | cut -c1-16)"
        else
            key="$(printf '%s' "$repo_root" | sha256sum | cut -c1-16)"
        fi

        cache_dir="$resolved_home/.copilot/ai-attribution-cache"
        cache_path="$cache_dir/$key.json"
        if [[ -f "$cache_path" && ! -L "$cache_path" ]]; then
            if data="$(cat "$cache_path" 2>/dev/null)" && [[ -n "$data" ]]; then
                version="$(printf '%s' "$data" | jq -er '.version // empty' 2>/dev/null)"
                computed_at="$(printf '%s' "$data" | jq -er '.computed_at // empty' 2>/dev/null)"
                guidance="$(printf '%s' "$data" | jq -er '.guidance // empty' 2>/dev/null)"
                ttl="${AI_ATTRIBUTION_CACHE_TTL_SECONDS:-3600}"
                if ! [[ "$ttl" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
                    ttl=3600
                fi
                now="$(date +%s)"
                if [[ "$version" == "$CACHE_FORMAT_VERSION" && -n "$computed_at" && -n "$guidance" ]] \
                    && age="$(awk -v now="$now" -v c="$computed_at" 'BEGIN { print (now - c) }')" \
                    && awk -v age="$age" -v ttl="$ttl" 'BEGIN { exit !(age <= ttl) }'; then
                    content="${GUIDANCE_HEADER}${guidance}"$'\n'
                    content_bytes=${#content}
                    if [[ "$content_bytes" -gt "$GUIDANCE_MAX_BYTES" && -n "$guidance" ]]; then
                        content="${GUIDANCE_HEADER}Current AI attribution guidance was omitted because it exceeded the bounded file budget. Treat guidance as unavailable for this session-start invocation."$'\n'
                    fi

                    # Session-scoped target: create each level and refuse
                    # (falling through to the unchanged python path) at the
                    # first sign of a symlink.
                    copilot_root="$resolved_home/.copilot"
                    state_root="$copilot_root/session-state"
                    session_root="$state_root/$session_id"
                    instructions_dir="$session_root/instructions"
                    target_dir="$instructions_dir/ai-attribution"
                    safe=1
                    for dir in "$copilot_root" "$state_root" "$session_root" "$instructions_dir" "$target_dir"; do
                        if ! mkdir -p -- "$dir" 2>/dev/null; then
                            safe=0
                            break
                        fi
                        if [[ -L "$dir" ]]; then
                            safe=0
                            break
                        fi
                    done
                    if [[ "$safe" -eq 1 ]]; then
                        target="$target_dir/session-guidance.instructions.md"
                        temp="$target_dir/.session-guidance.$$.$RANDOM.tmp"
                        if printf '%s' "$content" > "$temp" 2>/dev/null && mv -f -- "$temp" "$target" 2>/dev/null; then
                            fast_path_ok=1
                        else
                            rm -f -- "$temp" 2>/dev/null
                        fi
                    fi
                fi
            fi
        fi
    fi
fi

if [[ "$fast_path_ok" -eq 1 ]]; then
    printf '{}'
else
    run_python_fallback "$stdin_text"
fi
