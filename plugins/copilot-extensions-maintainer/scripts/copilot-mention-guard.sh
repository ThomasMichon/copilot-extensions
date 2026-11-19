#!/usr/bin/env bash
# Native bash port of copilot-mention-guard.py for the preToolUse hook.
#
# The hook runs this file with the raw hook payload on stdin. It must keep the
# Python guard's decisions and output contract: print one compact deny JSON
# document (without a trailing newline), or nothing. Exit status 1 reports an
# internal error, which the hook turns into the "skipped" stderr line. Keep
# this file ASCII-only and compatible with bash 3.2. It must never start
# Python; the only child process it may start is `git remote --verbose`.
#
# Known deviations from the Python guard: JSON nesting deeper than 900 levels
# is treated as invalid input (Python's limit depends on its recursion limit),
# and very large inline commands (hundreds of KB) can exhaust the shared 1.5 s
# decision budget sooner than in Python, which ends in the same budget outcome.

shopt -u patsub_replacement 2>/dev/null
set -f
unset CDPATH
G_LCSET=${LC_ALL+1}
G_LCVAL=${LC_ALL-}
export LC_ALL=C
G_DQ='"'
G_SQ="'"
G_BSL='\'
if (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 1) )); then
    G_RN=1
else
    G_RN=0
fi

# Fast exit: a publication command needs a `gh` token and a write word, and
# JSON can hide them only with \u escapes, quoting, or backslashes.
if (( G_RN )); then
    IFS= read -r -N 67108864 G_RAW
else
    G_RAW=$(cat; printf x)
    G_RAW=${G_RAW%x}
fi
case $G_RAW in
    *'\u'*) ;;
    *)
        # Field splitting removes the quote characters in linear time.
        IFS="$G_DQ$G_SQ$G_BSL"
        G_PF=( $G_RAW )
        IFS=
        G_F="${G_PF[*]}"
        unset IFS G_PF
        case $G_F in *[gG][hH]*) ;; *) exit 0 ;; esac
        case $G_F in
            *[cC][oO][mM][mM][eE][nN][tT]*|*[rR][eE][vV][iI][eE][wW]*|*[cC][rR][eE][aA][tT][eE]*|*[eE][dD][iI][tT]*|*[aA][pP][iI]*) ;;
            *) exit 0 ;;
        esac
        unset G_F
        ;;
esac

G_NL=$'\n'
G_TAB=$'\t'
G_CR=$'\r'
G_WS=" $G_TAB$G_CR$G_NL"
P_BS=$'\034'
P_DQ=$'\035'
P_NUL=$'\036'
G_US=$'\037'
G_BOM=$'\xef\xbb\xbf'
G_SEPS=$'\034\035\036\037'
U_HIGH=$'[\x80-\xff]'
U_CONT=$'[\x80-\xbf]'
U_2=$'[\xc2-\xdf][\x80-\xbf]'
U_3A=$'\xe0[\xa0-\xbf][\x80-\xbf]'
U_3B=$'[\xe1-\xec\xee\xef][\x80-\xbf][\x80-\xbf]'
U_3C=$'\xed[\x80-\x9f][\x80-\xbf]'
U_4A=$'\xf0[\x90-\xbf][\x80-\xbf][\x80-\xbf]'
U_4B=$'[\xf1-\xf3][\x80-\xbf][\x80-\xbf][\x80-\xbf]'
U_4C=$'\xf4[\x80-\x8f][\x80-\xbf][\x80-\xbf]'
U_DOTLESS=$'\xc4\xb1'
U_DOTTED=$'\xc4\xb0'
U_NOHIGH=$'[!\x80-\xff]'
RE_NUM='^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?$'
RE_ASSIGN='^[A-Za-z_][A-Za-z0-9_]*='
RE_EPOCH='^[0-9]+[.][0-9]{6}$'
RE_PUNCT='^[();<>|&]+'
RE_WORD='^[^();<>|&]+'
RE_TO_NL=$'^[^\n]*\n'
RE_TO_HASH='^[^#]*'
RE_JWS=$'^[ \t\r\n]+'
RE_JNUM='^[-0-9eE.+]+'
G_SEPCH=( $'\002' $'\003' $'\004' $'\005' $'\006' $'\007' $'\010' $'\016' $'\017'
    $'\020' $'\021' $'\022' $'\023' $'\024' $'\025' $'\026' $'\027' $'\030' $'\031'
    $'\032' $'\033' $'\034' $'\035' $'\036' $'\037' )
RX_W="[[:space:]$G_SEPS]"
RX_NW="[^[:space:]$G_SEPS]"
RX_NWS="[^/[:space:]$G_SEPS]"
RE_REMOTE="^$RX_W*($RX_NW+)$RX_W+($RX_NW+)$RX_W+($RX_NW.*)\$"
RE_FETCH="^\\(fetch\\)($RX_W|\$)"
RE_URL="[gG]([iI]|$U_DOTLESS|$U_DOTTED)[tT][hH][uU][bB]\\.[cC][oO][mM][:/]($RX_NWS+)/($RX_NWS+)/?\$"
DENY_MENTION='{"permissionDecision":"deny","permissionDecisionReason":"DENIED: an @copilot mention in a GitHub PR/issue comment or review does not nudge the review bot -- it delegates to the separate Copilot cloud coding agent, which will push its own unreviewed commits to the PR branch. The review bot already re-runs on every push; just push a commit for a fresh verdict, or post the comment without the mention.","decision":"deny","message":"DENIED: do not @-mention copilot in a PR/issue comment or review."}'
DENY_UNSCANNABLE='{"permissionDecision":"deny","permissionDecisionReason":"DENIED: this publishes a PR/issue comment or review body this guard cannot fully inspect within its decision budget (including stdin, unresolved input, or a slow file). Write the body to a local file (--body-file <path>) or pass it inline (--body \"...\") so it can be scanned for an @copilot mention.","decision":"deny","message":"DENIED: PR/issue body text cannot be fully scanned."}'

# ------------------------------------------------------------------- helpers
if [[ ${EPOCHREALTIME-} =~ $RE_EPOCH ]]; then
    G_USEC=1
    G_BUDGET=1500000
    now() { G_NOW=${EPOCHREALTIME/./}; }
else
    G_USEC=0
    G_BUDGET=2
    now() { G_NOW=$SECONDS; }
fi
deadline_start() { now; G_DL=$((G_NOW + G_BUDGET)); }
expired() { now; (( G_NOW >= G_DL )); }
remaining_secs() {
    now
    local r=$((G_DL - G_NOW))
    (( r > 0 )) || return 1
    if (( G_USEC )); then
        printf -v G_SECS '%d.%06d' $((r / 1000000)) $((r % 1000000))
    else
        G_SECS=$r
    fi
}

if (( BASH_VERSINFO[0] >= 4 )); then
    eval 'lo() { G_LO=${1,,}; }'
else
    lo() {
        local t=$1
        case $t in
            *[A-Z]*)
                t=${t//A/a}; t=${t//B/b}; t=${t//C/c}; t=${t//D/d}; t=${t//E/e}; t=${t//F/f}
                t=${t//G/g}; t=${t//H/h}; t=${t//I/i}; t=${t//J/j}; t=${t//K/k}; t=${t//L/l}
                t=${t//M/m}; t=${t//N/n}; t=${t//O/o}; t=${t//P/p}; t=${t//Q/q}; t=${t//R/r}
                t=${t//S/s}; t=${t//T/t}; t=${t//U/u}; t=${t//V/v}; t=${t//W/w}; t=${t//X/x}
                t=${t//Y/y}; t=${t//Z/z}
                ;;
        esac
        G_LO=$t
    }
fi

base_lo() {
    local t=${1//\\//}
    lo "${t##*/}"
}

utf8_valid() {
    local t=$1 c r carry= off=0 n k
    [[ $t == *$U_HIGH* ]] || return 0
    n=${#t}
    # Bash replaces patterns in time proportional to matches times length,
    # so validate bounded chunks and carry a split sequence to the next one.
    while (( off < n )); do
        c=$carry${t:off:4096}
        off=$((off + 4096))
        r=${c//$U_4A/x}; r=${r//$U_4B/x}; r=${r//$U_4C/x}
        r=${r//$U_3A/x}; r=${r//$U_3B/x}; r=${r//$U_3C/x}
        r=${r//$U_2/x}
        carry=
        [[ $r == *$U_HIGH* ]] || continue
        (( off < n )) || return 1
        for k in 1 2 3; do
            (( k <= ${#r} )) || break
            if [[ ${r:0:${#r}-k} != *$U_HIGH* && ${r: -k} != *$U_NOHIGH* ]]; then
                carry=${c: -k}
                break
            fi
        done
        [[ -n $carry ]] || return 1
    done
    return 0
}

# Large strings make bash pattern trimming and replacement quadratic, so the
# scanners below cut long text with field splitting, which is linear.

# split_on CH TEXT -> G_PARTS: TEXT split on every CH (empty fields kept).
split_on() {
    local IFS=$1 last n
    G_PARTS=( $2. )
    n=${#G_PARTS[@]}
    last=${G_PARTS[n-1]}
    G_PARTS[n-1]=${last:0:${#last}-1}
}

# split_keep CH TEXT -> G_PARTS: like split_on, but every part after the
# first starts with its CH.
split_keep() {
    local -a r
    split_on "$1" "$2"
    if (( ${#G_PARTS[@]} > 1 )); then
        r=( "${G_PARTS[@]:1}" )
        G_PARTS=( "${G_PARTS[0]}" "${r[@]/#/$1}" )
    fi
}

# seg_split TEXT -> SG: SG[0] is the text before the first quote or
# backslash; every other segment is one such character followed by text
# that has none.
seg_split() {
    local f g
    local -a l1 l2
    SG=()
    split_keep "$G_DQ" "$1"
    l1=( "${G_PARTS[@]}" )
    for f in "${l1[@]}"; do
        if [[ $f == *"$G_SQ"* ]]; then
            split_keep "$G_SQ" "$f"
            l2=( "${G_PARTS[@]}" )
        else
            l2=( "$f" )
        fi
        for g in "${l2[@]}"; do
            if [[ $g == *"$G_BSL"* ]]; then
                split_keep "$G_BSL" "$g"
                SG+=( "${G_PARTS[@]}" )
            else
                SG[${#SG[@]}]=$g
            fi
        done
    done
}

# pick_sep TEXT -> G_SEP, a control character that TEXT does not contain.
pick_sep() {
    local c
    for c in "${G_SEPCH[@]}"; do
        case $1 in *"$c"*) ;; *) G_SEP=$c; return 0 ;; esac
    done
    return 1
}

# read_file PATH LIMIT -> G_BUF (at most LIMIT bytes; NUL bytes are dropped).
read_file() {
    G_BUF=
    if (( G_RN )); then
        IFS= read -r -N "$2" G_BUF < "$1"
    else
        G_BUF=$(head -c "$2" -- "$1"; printf x)
        G_BUF=${G_BUF%x}
    fi
} 2>/dev/null

# crlf_norm: G_BUF with CRLF and lone CR changed to LF. Work in bounded
# chunks (carrying a final CR) so the replacement stays linear.
crlf_norm() {
    local c carry= off=0 n=${#G_BUF}
    local -a out=()
    while (( off < n )); do
        c=$carry${G_BUF:off:4096}
        off=$((off + 4096))
        carry=
        if (( off < n )) && [[ $c == *"$G_CR" ]]; then
            carry=$G_CR
            c=${c:0:${#c}-1}
        fi
        c=${c//"$G_CR$G_NL"/$G_NL}
        out[${#out[@]}]=${c//"$G_CR"/$G_NL}
    done
    local IFS=
    G_BUF="${out[*]}"
}

# read_bounded PATH STRICT CWD -> G_RB; returns 1 where Python returns None.
read_bounded() {
    local p=$1 t
    if [[ -n $3 && $p != /* ]]; then
        if [[ $3 == */ ]]; then p=$3$p; else p=$3/$p; fi
    fi
    [[ -n $p && -f $p && -r $p ]] || return 1
    expired && return 1
    read_file "$p" 800005
    (( ${#G_BUF} > 800004 )) && return 1
    if [[ $2 == 1 ]]; then utf8_valid "$G_BUF" || return 1; fi
    [[ $G_BUF == *$G_CR* ]] && crlf_norm
    if (( ${#G_BUF} > 200000 )); then
        t=${G_BUF//$U_CONT/}
        (( ${#t} > 200000 )) && return 1
    fi
    expired && return 1
    G_RB=$G_BUF
}

# --------------------------------------------------------------- JSON parser
# Nodes in document order: J_T type (o a s i f t F n), J_V scalar text,
# J_P parent index (-1 for the root), J_K member key, J_C child count.
# J_OK: 0 valid, 1 invalid under Python json.loads rules, 2 timed out.
u8() {
    local cp=$1 f
    if (( cp == 0 )); then G_U8=$P_NUL; return; fi
    if (( cp < 0x80 )); then
        printf -v f '\\x%02x' "$cp"
    elif (( cp < 0x800 )); then
        printf -v f '\\x%02x\\x%02x' $((0xC0 | (cp >> 6))) $((0x80 | (cp & 0x3F)))
    elif (( cp < 0x10000 )); then
        printf -v f '\\x%02x\\x%02x\\x%02x' $((0xE0 | (cp >> 12))) $((0x80 | ((cp >> 6) & 0x3F))) $((0x80 | (cp & 0x3F)))
    else
        printf -v f '\\x%02x\\x%02x\\x%02x\\x%02x' $((0xF0 | (cp >> 18))) $((0x80 | ((cp >> 12) & 0x3F))) $((0x80 | ((cp >> 6) & 0x3F))) $((0x80 | (cp & 0x3F)))
    fi
    printf -v G_U8 '%b' "$f"
}

json_restore() {
    G_JR=${1//$P_BS/$G_BSL}
    G_JR=${G_JR//$P_DQ/$G_DQ}
}

json_str() {
    local b=$1 p k n L cp low IFS
    local -a pc out
    case $b in *["$G_TAB$G_CR$G_NL"]*) return 1 ;; esac
    case $b in
        *"$G_BSL"*) ;;
        *) json_restore "$b"; G_JS=$G_JR; return 0 ;;
    esac
    split_on "$G_BSL" "$b"
    pc=( "${G_PARTS[@]}" )
    n=${#pc[@]}
    json_restore "${pc[0]}"
    out=( "$G_JR" )
    for (( k = 1; k < n; k++ )); do
        p=${pc[k]}
        L=1
        case $p in
            /*) out[${#out[@]}]=/ ;;
            b*) out[${#out[@]}]=$'\b' ;;
            f*) out[${#out[@]}]=$'\f' ;;
            n*) out[${#out[@]}]=$G_NL ;;
            r*) out[${#out[@]}]=$G_CR ;;
            t*) out[${#out[@]}]=$G_TAB ;;
            u[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]*)
                cp=$((16#${p:1:4}))
                L=5
                if (( cp >= 0xD800 && cp <= 0xDBFF && ${#p} == 5 && k + 1 < n )) &&
                    [[ ${pc[k+1]} == u[dD][c-fC-F][0-9a-fA-F][0-9a-fA-F]* ]]; then
                    k=$((k + 1))
                    p=${pc[k]}
                    low=$((16#${p:1:4}))
                    cp=$((0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00)))
                fi
                u8 "$cp"
                out[${#out[@]}]=$G_U8
                ;;
            *) return 1 ;;
        esac
        json_restore "${p:L}"
        out[${#out[@]}]=$G_JR
    done
    IFS=
    G_JS="${out[*]}"
}

json_add() {
    local p=-1
    (( depth )) && p=${stk[depth-1]}
    J_T[J_N]=$1
    J_V[J_N]=$2
    J_P[J_N]=$p
    J_C[J_N]=0
    if (( p >= 0 )); then
        J_C[p]=$((J_C[p] + 1))
        [[ ${J_T[p]} == o ]] && J_K[J_N]=$pk
    fi
    G_NODE=$J_N
    J_N=$((J_N + 1))
}

json_done() {
    if (( depth )); then expect=a; first=0; else root_done=1; fi
}

# json_parse TEXT CHECK_DEADLINE
json_parse() {
    local s=$1 chk=$2 t pre b c i np nq seg lead run digits
    local expect=v first=0 depth=0 pk= root_done=0 tok=0 IFS
    local -a stk parts
    J_T=(); J_V=(); J_P=(); J_K=(); J_C=(); J_N=0; J_OK=1
    [[ -n $s ]] || return 1
    if [[ $s == *[[:cntrl:]]* ]]; then
        IFS="$G_TAB$G_CR$G_NL"
        parts=( $s )
        IFS=
        t="${parts[*]}"
        if [[ $t == *[[:cntrl:]]* ]]; then
            t=${t//$'\177'/}
            [[ $t == *[[:cntrl:]]* ]] && return 1
        fi
    fi
    s=${s//"$G_BSL$G_BSL"/$P_BS}
    s=${s//"$G_BSL$G_DQ"/$P_DQ}
    split_on \" "$s"
    parts=( "${G_PARTS[@]}" )
    np=${#parts[@]}
    (( np % 2 )) || return 1
    IFS=$' \t\n'
    for (( i = 0; i < np; i++ )); do
        if (( chk )) && (( (tok += 1) % 256 == 0 )) && expired; then J_OK=2; return 2; fi
        if (( i % 2 )); then
            (( root_done )) && return 1
            json_str "${parts[i]}" || return 1
            if [[ $expect == k ]]; then
                pk=$G_JS
                expect=c
                continue
            fi
            [[ $expect == v ]] || return 1
            json_add s "$G_JS"
            json_done
            continue
        fi
        seg=${parts[i]}
        while :; do
            if [[ $seg =~ $RE_JWS ]]; then seg=${seg:${#BASH_REMATCH}}; fi
            [[ -n $seg ]] || break
            (( root_done )) && return 1
            if (( chk )) && (( (tok += 1) % 256 == 0 )) && expired; then J_OK=2; return 2; fi
            c=${seg:0:1}
            case $c in
                '{'|'[')
                    [[ $expect == v ]] || return 1
                    (( depth >= 900 )) && return 1
                    if [[ $c == '{' ]]; then json_add o ''; expect=k; else json_add a ''; expect=v; fi
                    stk[depth]=$G_NODE
                    depth=$((depth + 1))
                    first=1
                    seg=${seg:1}
                    ;;
                '}'|']')
                    (( depth )) || return 1
                    if [[ $c == '}' ]]; then
                        [[ ${J_T[stk[depth-1]]} == o ]] || return 1
                        [[ ( $expect == k && $first == 1 ) || $expect == a ]] || return 1
                    else
                        [[ ${J_T[stk[depth-1]]} == a ]] || return 1
                        [[ ( $expect == v && $first == 1 ) || $expect == a ]] || return 1
                    fi
                    depth=$((depth - 1))
                    seg=${seg:1}
                    json_done
                    ;;
                ':')
                    [[ $expect == c ]] || return 1
                    expect=v
                    first=0
                    seg=${seg:1}
                    ;;
                ',')
                    [[ $expect == a ]] && (( depth )) || return 1
                    if [[ ${J_T[stk[depth-1]]} == o ]]; then expect=k; else expect=v; fi
                    first=0
                    seg=${seg:1}
                    ;;
                *)
                    [[ $expect == v ]] || return 1
                    case $seg in
                        true*) json_add t ''; seg=${seg:4} ;;
                        false*) json_add F ''; seg=${seg:5} ;;
                        null*) json_add n ''; seg=${seg:4} ;;
                        NaN*) json_add f NaN; seg=${seg:3} ;;
                        Infinity*) json_add f Infinity; seg=${seg:8} ;;
                        -Infinity*) json_add f -Infinity; seg=${seg:9} ;;
                        [-0-9]*)
                            [[ $seg =~ $RE_JNUM ]]
                            run=$BASH_REMATCH
                            [[ $run =~ $RE_NUM ]] || return 1
                            if [[ $run == *[.eE]* ]]; then
                                json_add f "$run"
                            else
                                digits=${run#-}
                                (( ${#digits} > 4300 )) && return 1
                                json_add i "$run"
                            fi
                            seg=${seg:${#run}}
                            ;;
                        *) return 1 ;;
                    esac
                    json_done
                    ;;
            esac
        done
    done
    (( root_done )) || return 1
    J_OK=0
}

# jget PARENT KEY -> G_J, the last member with that key (last duplicate wins).
jget() {
    local i
    for (( i = J_N - 1; i > $1; i-- )); do
        if [[ ${J_P[i]} == "$1" && ${J_K[i]} == "$2" ]]; then G_J=$i; return 0; fi
    done
    return 1
}

# Python float() of a JSON number literal is nonzero (approximate underflow).
float_nonzero() {
    local v=${1#-} m e=0 es=1 ip fp d z sig lead
    case $v in NaN|Infinity) return 0 ;; esac
    m=${v%%[eE]*}
    [[ $v == *[eE]* ]] && e=${v#*[eE]}
    ip=${m%%.*}
    fp=
    [[ $m == *.* ]] && fp=${m#*.}
    d=$ip$fp
    z=${d%%[1-9]*}
    (( ${#z} == ${#d} )) && return 1
    [[ $e == -* ]] && es=-1
    e=${e#[-+]}
    e=${e#"${e%%[!0]*}"}
    [[ -n $e ]] || e=0
    if (( ${#e} > 15 )); then (( es > 0 )); return; fi
    lead=$(( ${#ip} - ${#z} - 1 + es * e ))
    (( lead > -324 )) && return 0
    (( lead < -324 )) && return 1
    sig=${d:${#z}:17}
    while (( ${#sig} < 17 )); do sig+=0; done
    [[ $sig > 24703282292062327 ]]
}

jtruthy() {
    case ${J_T[$1]} in
        s) [[ -n ${J_V[$1]} ]] ;;
        i) [[ ${J_V[$1]} != 0 && ${J_V[$1]} != -0 ]] ;;
        f) float_nonzero "${J_V[$1]}" ;;
        o|a) (( J_C[$1] > 0 )) ;;
        t) return 0 ;;
        *) return 1 ;;
    esac
}

# ---------------------------------------------------------------- killswitch
ks_active() {
    local ctx=${COPILOT_EXTENSIONS_CONTEXT-} p=${PRETOOL_KILLSWITCH_STATE_FILE-} h=${HOME-} a c
    ctx=${ctx//["$G_WS"$'\v\f']/}
    [[ -n $ctx ]] && return 1
    if [[ -n $p ]]; then
        case $p in
            '~'|'~/'*) [[ -n $h ]] || return 1; p=$h${p:1} ;;
        esac
        [[ $p == /* ]] || return 1
    else
        [[ -n $h ]] || return 1
        p=$h/.copilot-extensions/killSwitches/pretool-killswitch.json
    fi
    [[ -f $p && -r $p ]] || return 1
    read_file "$p" 16385
    (( ${#G_BUF} > 16384 )) && return 1
    G_BUF=${G_BUF#"$G_BOM"}
    utf8_valid "$G_BUF" || return 1
    json_parse "$G_BUF" 0
    (( J_OK == 0 )) && [[ ${J_T[0]} == o ]] || return 1
    jget 0 active || return 1
    a=${J_T[G_J]}
    [[ $a == t || $a == F ]] || return 1
    if jget 0 contract; then
        c=$G_J
        [[ ${J_T[c]} == o ]] || return 1
        jget "$c" id && [[ ${J_T[G_J]} == s && ${J_V[G_J]} == copilot-extensions.pretool-killswitch ]] || return 1
        jget "$c" version && [[ ${J_T[G_J]} == i && ${J_V[G_J]} == 1 ]] || return 1
    fi
    [[ $a == t ]]
}

# ----------------------------------------------------------------- tokenizer
# The scanners work on seg_split segments, so their cost is linear in the
# text; they stop the hook (allow) when the deadline expires, as Python does
# before the repository check.

# Port of _split_unquoted_newlines -> LINES. Empty lines are dropped because
# they hold no statements.
split_lines() {
    local q= e=0 d c i n k IFS=
    local -a cur W
    LINES=()
    seg_split "$1"
    n=${#SG[@]}
    for (( i = 0; i < n; i++ )); do
        if (( (i & 255) == 255 )) && expired; then exit 0; fi
        c=${SG[i]}
        if (( i )); then
            d=${c:0:1}
            c=${c:1}
            cur[${#cur[@]}]=$d
            if (( e )); then
                e=0
            elif [[ $d == "$G_BSL" && $q != "$G_SQ" ]]; then
                e=1
            elif [[ -n $q ]]; then
                if [[ $d == "$q" ]]; then q=; fi
            else
                q=$d
            fi
        fi
        [[ -n $c ]] || continue
        if (( e )); then
            cur[${#cur[@]}]=${c:0:1}
            c=${c:1}
            e=0
            [[ -n $c ]] || continue
        fi
        if [[ -n $q || $c != *"$G_NL"* ]]; then
            cur[${#cur[@]}]=$c
            continue
        fi
        if [[ ${c:0:1} == "$G_NL" ]]; then
            LINES[${#LINES[@]}]="${cur[*]}"
            cur=()
        fi
        IFS=$G_NL
        W=( $c )
        IFS=
        for (( k = 0; k < ${#W[@]}; k++ )); do
            if (( k )); then
                LINES[${#LINES[@]}]="${cur[*]}"
                cur=()
            fi
            cur[${#cur[@]}]=${W[k]}
        done
        if [[ ${c: -1} == "$G_NL" ]] && (( ${#W[@]} )); then
            LINES[${#LINES[@]}]="${cur[*]}"
            cur=()
        fi
    done
    LINES[${#LINES[@]}]="${cur[*]}"
}

# Port of shlex.shlex(line, posix=True, punctuation_chars="();<>|&") with
# whitespace_split=True -> TOKS; returns 1 where shlex raises ValueError.
# State st: ' ' between tokens, a word, c punctuation, e escape (back to
# esc), a quote character, or C inside a comment.
shlex_line() {
    local st=' ' esc=a quoted=0 tn=0 i n c IFS=
    local -a TP
    TOKS=()
    seg_split "$1"
    n=${#SG[@]}
    for (( i = 0; i < n; i++ )); do
        if (( (i & 255) == 255 )) && expired; then exit 0; fi
        c=${SG[i]}
        if (( i )); then
            sx_delim "${c:0:1}"
            c=${c:1}
        fi
        [[ -z $c ]] || sx_content "$c"
    done
    case $st in e|"$G_SQ"|"$G_DQ") return 1 ;; esac
    sx_emit
    return 0
}

sx_add() {
    TP[${#TP[@]}]=$1
    [[ -z $1 ]] || tn=1
}

sx_emit() {
    local IFS=
    if (( tn || quoted )); then TOKS[${#TOKS[@]}]="${TP[*]}"; fi
    TP=()
    tn=0
    quoted=0
}

sx_ws() {
    if [[ $st == a || $st == c ]]; then
        st=' '
        sx_emit
    fi
}

sx_delim() {
    local d=$1
    case $st in
        e)
            if [[ $esc != a && $d != "$G_BSL" && $d != "$esc" ]]; then sx_add "$G_BSL"; fi
            sx_add "$d"
            st=$esc
            ;;
        "$G_SQ")
            if [[ $d == "$G_SQ" ]]; then st=a; else sx_add "$d"; fi
            ;;
        "$G_DQ")
            if [[ $d == "$G_DQ" ]]; then
                st=a
            elif [[ $d == "$G_BSL" ]]; then
                esc=$G_DQ
                st=e
            else
                sx_add "$d"
            fi
            ;;
        C) ;;
        *)
            if [[ $st == c ]]; then
                st=' '
                sx_emit
            fi
            if [[ $d == "$G_BSL" ]]; then
                esc=a
                st=e
            else
                st=$d
                quoted=1
            fi
            ;;
    esac
}

sx_content() {
    local t=$1
    if [[ $st == e ]]; then
        if [[ $esc != a ]]; then sx_add "$G_BSL"; fi
        sx_add "${t:0:1}"
        st=$esc
        t=${t:1}
        [[ -n $t ]] || return 0
    fi
    case $st in
        "$G_SQ"|"$G_DQ") sx_add "$t" ;;
        *) sx_plain "$t" ;;
    esac
}

# Unquoted text: a comment runs from # through the next newline.
sx_plain() {
    local t=$1 pre
    while [[ -n $t ]]; do
        if [[ $st == C ]]; then
            [[ $t == *"$G_NL"* ]] || return 0
            [[ $t =~ $RE_TO_NL ]]
            t=${t:${#BASH_REMATCH}}
            st=' '
            continue
        fi
        case $t in
            *'#'*) ;;
            *) sx_words "$t"; return 0 ;;
        esac
        [[ $t =~ $RE_TO_HASH ]]
        pre=$BASH_REMATCH
        [[ -z $pre ]] || sx_words "$pre"
        sx_ws
        st=C
        t=${t:${#pre}+1}
    done
}

sx_words() {
    local t=$1 k n IFS
    local -a W
    case ${t:0:1} in ["$G_WS"]) sx_ws ;; esac
    IFS=$G_WS
    W=( $t )
    n=${#W[@]}
    for (( k = 0; k < n; k++ )); do
        (( k == 0 )) || sx_ws
        [[ -z ${W[k]} ]] || sx_word "${W[k]}"
    done
    case ${t: -1} in ["$G_WS"]) sx_ws ;; esac
}

sx_word() {
    local w=$1
    case $w in
        *['();<>|&']*) ;;
        *) sx_run a "$w"; return ;;
    esac
    while [[ -n $w ]]; do
        if [[ $w =~ $RE_PUNCT ]]; then
            sx_run c "$BASH_REMATCH"
        else
            [[ $w =~ $RE_WORD ]]
            sx_run a "$BASH_REMATCH"
        fi
        w=${w:${#BASH_REMATCH}}
    done
}

# sx_run KIND TEXT: a run of word (a) or punctuation (c) characters.
sx_run() {
    if [[ $st == "$1" ]]; then
        sx_add "$2"
        return
    fi
    if [[ $st == a || $st == c ]]; then sx_emit; fi
    sx_add "$2"
    st=$1
}

# ---------------------------------------------------------------- statements
# Statements are slices of ST_TOK: start ST_S[k], length ST_L[k].
split_statements() {
    local line tk cs cl
    ST_TOK=(); ST_S=(); ST_L=()
    split_lines "$1"
    for line in "${LINES[@]}"; do
        if expired; then exit 0; fi
        shlex_line "$line" || continue
        cs=${#ST_TOK[@]}
        cl=0
        for tk in "${TOKS[@]}"; do
            case $tk in
                ';'|'&'|'&&'|'||'|'|'|"$G_NL"|'('|')'|'<'|'>')
                    if (( cl )); then ST_S[${#ST_S[@]}]=$cs; ST_L[${#ST_L[@]}]=$cl; fi
                    cs=$(( ${#ST_TOK[@]} ))
                    cl=0
                    continue
                    ;;
            esac
            ST_TOK[${#ST_TOK[@]}]=$tk
            cl=$((cl + 1))
        done
        if (( cl )); then ST_S[${#ST_S[@]}]=$cs; ST_L[${#ST_L[@]}]=$cl; fi
    done
}

# Drops leading assignments and env/command wrappers from slice s/l.
strip_prefixes() {
    while (( l )); do
        while (( l )) && [[ ${ST_TOK[s]} =~ $RE_ASSIGN ]]; do s=$((s + 1)); l=$((l - 1)); done
        (( l )) || break
        base_lo "${ST_TOK[s]}"
        case $G_LO in
            env|env.exe)
                s=$((s + 1)); l=$((l - 1))
                while (( l )) && [[ ${ST_TOK[s]} == -* || ${ST_TOK[s]} =~ $RE_ASSIGN ]]; do s=$((s + 1)); l=$((l - 1)); done
                continue
                ;;
            command|command.exe)
                s=$((s + 1)); l=$((l - 1))
                while (( l )) && [[ ${ST_TOK[s]} == -* ]]; do s=$((s + 1)); l=$((l - 1)); done
                continue
                ;;
        esac
        break
    done
}

write_sub() {
    local s=$1 l=$2 i a
    for (( i = 1; i < l - 1; i++ )); do
        lo "${ST_TOK[s+i]}"
        a=$G_LO
        lo "${ST_TOK[s+i+1]}"
        case "$a $G_LO" in
            'pr comment'|'issue comment'|'pr review'|'pr create'|'pr edit'|'issue create'|'issue edit') return 0 ;;
        esac
    done
    return 1
}

pr_issue_create() {
    local s=$1 l=$2 i
    for (( i = 1; i < l - 1; i++ )); do
        lo "${ST_TOK[s+i]}"
        case $G_LO in
            pr|issue)
                lo "${ST_TOK[s+i+1]}"
                [[ $G_LO == create ]] && return 0
                ;;
        esac
    done
    return 1
}

api_write() {
    local s=$1 l=$2 i tok api=0 body=0 input=0
    for (( i = 1; i < l; i++ )); do
        lo "${ST_TOK[s+i]}"
        if [[ $G_LO == api ]]; then api=1; break; fi
    done
    (( api )) || return 1
    for (( i = 1; i < l; i++ )); do
        tok=${ST_TOK[s+i]}
        if [[ $tok == -f || $tok == -F || $tok == --field || $tok == --raw-field ]] && (( i + 1 < l )) && [[ ${ST_TOK[s+i+1]} == body=* ]]; then
            body=1
        elif [[ $tok == -f* || $tok == -F* || $tok == --field=* || $tok == --raw-field=* ]] && [[ $tok == *body=* ]]; then
            body=1
        fi
        [[ $tok == --input || $tok == --input=* ]] && input=1
    done
    (( body || input )) || return 1
    for (( i = 1; i < l; i++ )); do
        tok=${ST_TOK[s+i]}
        [[ $tok == -* ]] && continue
        case $tok in */comments*|*/reviews*|*/issues*|*/pulls*) return 0 ;; esac
    done
    return 1
}

pub_statements() {
    local k s l
    PUB_S=(); PUB_L=()
    split_statements "$1"
    for (( k = 0; k < ${#ST_S[@]}; k++ )); do
        s=${ST_S[k]}
        l=${ST_L[k]}
        strip_prefixes
        (( l )) || continue
        base_lo "${ST_TOK[s]}"
        case $G_LO in gh|gh.exe) ;; *) continue ;; esac
        write_sub "$s" "$l" || api_write "$s" "$l" || continue
        PUB_S[${#PUB_S[@]}]=$s
        PUB_L[${#PUB_L[@]}]=$l
    done
}

# -------------------------------------------------------------- body scanner
has_ref() {
    case $1 in *'${'*|*'$('*|*'$'[A-Za-z_]*|*'`'*) return 0 ;; esac
    return 1
}

input_body() {
    [[ $1 == - ]] && return 1
    read_bounded "$1" 1 "$2" || return 1
    json_parse "$G_RB" 1
    (( J_OK == 0 )) && [[ ${J_T[0]} == o ]] || return 1
    jget 0 body && [[ ${J_T[G_J]} == s ]] || return 1
    G_IB=${J_V[G_J]}
}

body_add() {
    if (( np )); then out+=$G_NL$1; else out=$1; fi
    np=$((np + 1))
}

# body_text START LEN CWD -> G_BODY; returns 1 when the body is unscannable.
body_text() {
    local s=$1 l=$2 cwd=$3 i=1 j tok nxt has v step field fset consumed flag prior out= np=0
    while (( i < l )); do
        expired && return 1
        tok=${ST_TOK[s+i]}
        has=0
        nxt=
        if (( i + 1 < l )); then has=1; nxt=${ST_TOK[s+i+1]}; fi
        if [[ $tok == --body || $tok == -b ]] && (( has )); then
            has_ref "$nxt" && return 1
            body_add "$nxt"
            i=$((i + 2))
            continue
        fi
        if [[ $tok == --body=* || $tok == -b=* ]]; then
            v=${tok#*=}
            has_ref "$v" && return 1
            body_add "$v"
            i=$((i + 1))
            continue
        fi
        if { [[ $tok == --body-file ]] && (( has )); } || [[ $tok == --body-file=* ]]; then
            if [[ $tok == --body-file ]]; then v=$nxt; step=2; else v=${tok:12}; step=1; fi
            [[ $v == - ]] && return 1
            read_bounded "$v" 0 "$cwd" || return 1
            body_add "$G_RB"
            i=$((i + step))
            continue
        fi
        if [[ $tok == --input ]] && (( has )); then
            input_body "$nxt" "$cwd" || return 1
            body_add "$G_IB"
            i=$((i + 2))
            continue
        fi
        if [[ $tok == --input=* ]]; then
            input_body "${tok:8}" "$cwd" || return 1
            body_add "$G_IB"
            i=$((i + 1))
            continue
        fi
        fset=0
        field=
        consumed=0
        if [[ $tok == -f || $tok == -F || $tok == --field || $tok == --raw-field ]] && (( has )); then
            field=$nxt; consumed=2; fset=1
        else
            for flag in -f -F --field --raw-field; do
                if [[ $tok == "$flag="* ]]; then field=${tok:${#flag}+1}; consumed=1; fset=1; break; fi
            done
        fi
        if (( fset )); then
            if [[ $field == body=* ]]; then
                v=${field:5}
                if [[ $v == '@-' ]]; then
                    return 1
                elif [[ $v == '@'* ]]; then
                    read_bounded "${v:1}" 0 "$cwd" || return 1
                    body_add "$G_RB"
                elif has_ref "$v"; then
                    return 1
                else
                    body_add "$v"
                fi
            fi
            i=$((i + consumed))
            continue
        fi
        if [[ $tok == body=* ]]; then
            prior=0
            for (( j = 0; j < i; j++ )); do
                if [[ ${ST_TOK[s+j]} == -f ]]; then prior=1; break; fi
            done
            if (( ! prior )); then
                v=${tok:5}
                has_ref "$v" && return 1
                body_add "$v"
            fi
        fi
        i=$((i + 1))
    done
    if (( np == 0 )); then
        pr_issue_create "$s" "$l" && return 1
        out=${ST_TOK[s]}
        for (( j = 1; j < l; j++ )); do out+=" ${ST_TOK[s+j]}"; done
    fi
    G_BODY=$out
}

# True when TEXT has an @copilot mention not followed by a word character.
mention_in() {
    local t=$1 k n
    local -a F
    t=${t//$U_DOTLESS/i}
    t=${t//$U_DOTTED/i}
    [[ $t == *@[cC][oO][pP][iI][lL][oO][tT]* ]] || return 1
    if pick_sep "$t"; then
        t=${t//@[cC][oO][pP][iI][lL][oO][tT]/$G_SEP}
        split_on "$G_SEP" "$t"
        F=( "${G_PARTS[@]}" )
        n=${#F[@]}
        for (( k = 1; k < n; k++ )); do
            mention_at "${F[k]:0:4}" && return 0
        done
        return 1
    fi
    while [[ $t == *@[cC][oO][pP][iI][lL][oO][tT]* ]]; do
        t=${t#*@[cC][oO][pP][iI][lL][oO][tT]}
        mention_at "${t:0:4}" && return 0
    done
    return 1
}

# mention_at NEXT: true when the text NEXT after a match is not a word
# character (end of text, an ASCII non-word byte, invalid UTF-8, or a code
# point outside the Unicode letter and number categories).
mention_at() {
    local s=$1 c b n cp k x seq
    [[ -n $s ]] || return 0
    c=${s:0:1}
    case $c in [-_a-zA-Z0-9]) return 1 ;; esac
    printf -v b '%d' "'$c"
    (( b < 128 )) && return 0
    if (( b >= 0xC2 && b <= 0xDF )); then n=2; cp=$((b & 0x1F))
    elif (( b >= 0xE0 && b <= 0xEF )); then n=3; cp=$((b & 0x0F))
    elif (( b >= 0xF0 && b <= 0xF4 )); then n=4; cp=$((b & 0x07))
    else return 0
    fi
    seq=${s:0:n}
    case $n in
        2) [[ $seq == $U_2 ]] || return 0 ;;
        3) [[ $seq == $U_3A || $seq == $U_3B || $seq == $U_3C ]] || return 0 ;;
        *) [[ $seq == $U_4A || $seq == $U_4B || $seq == $U_4C ]] || return 0 ;;
    esac
    for (( k = 1; k < n; k++ )); do
        printf -v x '%d' "'${seq:k:1}"
        cp=$(( (cp << 6) | (x & 0x3F) ))
    done
    wt_has "$cp" && return 1
    return 0
}

publish_reason() {
    local k
    G_REASON=
    for (( k = 0; k < ${#PUB_S[@]}; k++ )); do
        if expired; then G_REASON=unscannable; return; fi
        if ! body_text "${PUB_S[k]}" "${PUB_L[k]}" "$1"; then G_REASON=unscannable; return; fi
        if expired; then G_REASON=unscannable; return; fi
        if mention_in "$G_BODY"; then G_REASON=mention; return; fi
    done
}

# ----------------------------------------------------------- project scoping
# Best-effort Path.resolve(): physical path of the longest existing prefix.
resolve_dir() {
    local d=$1 tail= here=$PWD
    while [[ $d == */ && $d != / ]]; do d=${d%/}; done
    while ! cd -P -- "$d" 2>/dev/null; do
        if [[ $d == / || -z $d ]]; then G_REAL=$1; return; fi
        tail=/${d##*/}$tail
        d=${d%/*}
        [[ -n $d ]] || d=/
    done
    d=$PWD
    cd -- "$here" 2>/dev/null
    G_REAL=${d%/}$tail
    [[ -n $G_REAL ]] || G_REAL=/
}

adopting() {
    local d p name found i k v n m
    local -a ek ev
    [[ -n $1 && $1 == /* ]] || return 1
    resolve_dir "$1"
    d=$G_REAL
    while :; do
        expired && return 1
        found=0
        ek=(); ev=()
        for name in settings.json settings.local.json; do
            p=${d%/}/.github/copilot/$name
            [[ -f $p ]] || continue
            found=1
            read_bounded "$p" 1 "" || return 1
            json_parse "$G_RB" 0
            (( J_OK == 0 )) && [[ ${J_T[0]} == o ]] || return 1
            if jget 0 enabledPlugins; then
                p=$G_J
                [[ ${J_T[p]} == o ]] || return 1
                for (( i = p + 1; i < J_N; i++ )); do
                    [[ ${J_P[i]} == "$p" ]] || continue
                    k=${J_K[i]}
                    v=x
                    [[ ${J_T[i]} == t ]] && v=t
                    n=${#ek[@]}
                    for (( m = 0; m < ${#ek[@]}; m++ )); do
                        if [[ ${ek[m]} == "$k" ]]; then n=$m; break; fi
                    done
                    ek[n]=$k
                    ev[n]=$v
                done
            fi
        done
        if (( found )); then
            for (( m = 0; m < ${#ek[@]}; m++ )); do
                [[ ${ek[m]} == 'copilot-extensions-maintainer@'?* && ${ev[m]} == t ]] && return 0
            done
            return 1
        fi
        [[ -e ${d%/}/.git ]] && return 1
        [[ $d == / ]] && return 1
        d=${d%/*}
        [[ -n $d ]] || d=/
    done
}

gh_repo() {
    local u=${1%"$G_NL"} o r
    [[ $u =~ $RE_URL ]] || return 1
    o=${BASH_REMATCH[2]}
    r=${BASH_REMATCH[3]}
    case $r in *.[gG][iI][tT]) r=${r%????} ;; esac
    lo "$o/$r"
    G_REPO=$G_LO
}

# manifest_target ROOT -> G_TARGET; 1 when unresolved, 2 when not an object.
manifest_target() {
    local key
    read_bounded "$1/plugin.json" 1 "" || return 1
    json_parse "$G_RB" 0
    (( J_OK == 0 )) || return 1
    [[ ${J_T[0]} == o ]] || return 2
    for key in repository homepage; do
        if jget 0 "$key" && [[ ${J_T[G_J]} == s ]] && gh_repo "${J_V[G_J]}"; then
            G_TARGET=$G_REPO
            return 0
        fi
    done
    return 1
}

# repo_has CWD TARGET: `git remote --verbose` lists TARGET as a fetch remote.
repo_has() {
    local out st line name url rest seen=$G_US IFS
    remaining_secs || return 1
    IFS= read -r -d '' -t "$G_SECS" out < <(
        {
            cd -- "$1" || exit 1
            unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
            if [[ -n $G_LCSET ]]; then export LC_ALL=$G_LCVAL; else unset LC_ALL; fi
            git remote --verbose < /dev/null
            printf '\035%s' "$?"
        } 2>/dev/null
    )
    st=$?
    if (( st > 128 )); then
        kill "$!" 2>/dev/null
        return 1
    fi
    case $out in *"$P_DQ"*) ;; *) return 1 ;; esac
    st=${out##*"$P_DQ"}
    out=${out%"$P_DQ"*}
    [[ $st == 0 ]] || return 1
    out=${out//"$G_CR$G_NL"/$G_NL}
    out=${out//[$'\r\v\f\034\035\036']/$G_NL}
    out=${out//$'\xc2\x85'/$G_NL}
    out=${out//$'\xe2\x80\xa8'/$G_NL}
    out=${out//$'\xe2\x80\xa9'/$G_NL}
    IFS=$G_NL
    for line in $out; do
        [[ $line =~ $RE_REMOTE ]] || continue
        name=${BASH_REMATCH[1]}
        url=${BASH_REMATCH[2]}
        rest=${BASH_REMATCH[3]}
        [[ $rest =~ $RE_FETCH ]] || continue
        case $seen in *"$G_US$name$G_US"*) continue ;; esac
        seen+=$name$G_US
        if gh_repo "$url" && [[ $G_REPO == "$2" ]]; then return 0; fi
    done
    return 1
}

# ---------------------------------------------------------------------- main
main() {
    local hc=0 hc_t= hc_v= key args=-1 dict=1 cmd_t=s cmd_v= root rc
    ks_active && return 0
    deadline_start
    root=${COPILOT_PLUGIN_ROOT-}
    if [[ -z $root ]]; then
        root=${BASH_SOURCE[0]}
        if [[ $root == */* ]]; then root=${root%/*}/..; else root=..; fi
    fi
    json_parse "$G_RAW" 0
    (( J_OK == 0 )) && [[ ${J_T[0]} == o ]] || return 0
    jget 0 toolName && [[ ${J_T[G_J]} == s ]] || return 0
    [[ ${J_V[G_J]} == bash || ${J_V[G_J]} == powershell ]] || return 0
    for key in cwd workingDirectory; do
        if jget 0 "$key" && jtruthy "$G_J"; then hc=1; hc_t=${J_T[G_J]}; hc_v=${J_V[G_J]}; break; fi
    done
    if jget 0 toolArgs && jtruthy "$G_J"; then
        args=$G_J
        if [[ ${J_T[args]} == s ]]; then
            json_parse "${J_V[args]}" 0
            (( J_OK == 0 )) || return 0
            args=0
        fi
        [[ ${J_T[args]} == o ]] || dict=0
    fi
    if (( dict && args >= 0 )); then
        if jget "$args" command; then cmd_t=${J_T[G_J]}; cmd_v=${J_V[G_J]}; fi
        if (( ! hc )) && jget "$args" cwd && jtruthy "$G_J"; then hc=1; hc_t=${J_T[G_J]}; hc_v=${J_V[G_J]}; fi
    fi
    if (( ! hc )) && [[ -n ${COPILOT_PROJECT_DIR+x} ]]; then hc=1; hc_t=s; hc_v=$COPILOT_PROJECT_DIR; fi
    [[ $cmd_t == s ]] || return 0
    pub_statements "$cmd_v"
    (( ${#PUB_S[@]} )) || return 0
    (( hc )) && [[ $hc_t == s ]] || return 0
    adopting "$hc_v" || return 0
    manifest_target "$root"
    rc=$?
    if (( rc == 2 )); then
        utf8_valid "$G_RAW" || return 0
        return 1
    fi
    (( rc == 0 )) || return 0
    repo_has "$hc_v" "$G_TARGET" || return 0
    publish_reason "$hc_v"
    case $G_REASON in
        mention) utf8_valid "$G_RAW" && printf '%s' "$DENY_MENTION" ;;
        unscannable) utf8_valid "$G_RAW" && printf '%s' "$DENY_UNSCANNABLE" ;;
    esac
    return 0
}

# Code points of Unicode letters and numbers (categories L* and N*) above
# U+007F, as inclusive ranges; generated from the Python unicodedata tables.
wt_load() {
    WT_LO=( 170 178 181 185 188 192 216 248 710 736 748 750 880 886 890 895
        902 904 908 910 931 1015 1162 1329 1369 1376 1488 1519 1568 1632 1646
        1649 1749 1765 1774 1791 1808 1810 1869 1969 1984 2036 2042 2048 2074
        2084 2088 2112 2144 2160 2185 2208 2308 2365 2384 2392 2406 2417 2437
        2447 2451 2474 2482 2486 2493 2510 2524 2527 2534 2548 2556 2565 2575
        2579 2602 2610 2613 2616 2649 2654 2662 2674 2693 2703 2707 2730 2738
        2741 2749 2768 2784 2790 2809 2821 2831 2835 2858 2866 2869 2877 2908
        2911 2918 2929 2947 2949 2958 2962 2969 2972 2974 2979 2984 2990 3024
        3046 3077 3086 3090 3114 3133 3160 3165 3168 3174 3192 3200 3205 3214
        3218 3242 3253 3261 3293 3296 3302 3313 3332 3342 3346 3389 3406 3412
        3416 3430 3450 3461 3482 3507 3517 3520 3558 3585 3634 3648 3664 3713
        3716 3718 3724 3749 3751 3762 3773 3776 3782 3792 3804 3840 3872 3904
        3913 3976 4096 4159 4176 4186 4193 4197 4206 4213 4238 4240 4256 4295
        4301 4304 4348 4682 4688 4696 4698 4704 4746 4752 4786 4792 4800 4802
        4808 4824 4882 4888 4969 4992 5024 5112 5121 5743 5761 5792 5870 5888
        5919 5952 5984 5998 6016 6103 6108 6112 6128 6160 6176 6272 6279 6314
        6320 6400 6470 6512 6528 6576 6608 6656 6688 6784 6800 6823 6917 6981
        6992 7043 7086 7168 7232 7245 7296 7312 7357 7401 7406 7413 7418 7424
        7680 7960 7968 8008 8016 8025 8027 8029 8031 8064 8118 8126 8130 8134
        8144 8150 8160 8178 8182 8304 8308 8319 8336 8450 8455 8458 8469 8473
        8484 8486 8488 8490 8495 8508 8517 8526 8528 9312 9450 10102 11264
        11499 11506 11517 11520 11559 11565 11568 11631 11648 11680 11688
        11696 11704 11712 11720 11728 11736 11823 12293 12321 12337 12344
        12353 12445 12449 12540 12549 12593 12690 12704 12784 12832 12872
        12881 12928 12977 13312 19968 42192 42240 42512 42560 42623 42656
        42775 42786 42891 42960 42963 42965 42994 43011 43015 43020 43056
        43072 43138 43216 43250 43259 43261 43264 43312 43360 43396 43471
        43488 43494 43520 43584 43588 43600 43616 43642 43646 43697 43701
        43705 43712 43714 43739 43744 43762 43777 43785 43793 43808 43816
        43824 43868 43888 44016 44032 55216 55243 63744 64112 64256 64275
        64285 64287 64298 64312 64318 64320 64323 64326 64467 64848 64914
        65008 65136 65142 65296 65313 65345 65382 65474 65482 65490 65498
        65536 65549 65576 65596 65599 65616 65664 65799 65856 65930 66176
        66208 66273 66304 66349 66384 66432 66464 66504 66513 66560 66720
        66736 66776 66816 66864 66928 66940 66956 66964 66967 66979 66995
        67003 67072 67392 67424 67456 67463 67506 67584 67592 67594 67639
        67644 67647 67672 67705 67751 67808 67828 67835 67872 67968 68028
        68050 68112 68117 68121 68160 68192 68224 68288 68297 68331 68352
        68416 68440 68472 68521 68608 68736 68800 68858 68912 69216 69248
        69296 69376 69424 69457 69488 69552 69600 69635 69714 69745 69749
        69763 69840 69872 69891 69942 69956 69959 69968 70006 70019 70081
        70096 70108 70113 70144 70163 70207 70272 70280 70282 70287 70303
        70320 70384 70405 70415 70419 70442 70450 70453 70461 70480 70493
        70656 70727 70736 70751 70784 70852 70855 70864 71040 71128 71168
        71236 71248 71296 71352 71360 71424 71472 71488 71680 71840 71935
        71945 71948 71957 71960 71999 72001 72016 72096 72106 72161 72163
        72192 72203 72250 72272 72284 72349 72368 72704 72714 72768 72784
        72818 72960 72968 72971 73030 73040 73056 73063 73066 73112 73120
        73440 73474 73476 73490 73552 73648 73664 73728 74752 74880 77712
        77824 78913 82944 92160 92736 92768 92784 92864 92880 92928 92992
        93008 93019 93027 93053 93760 93952 94032 94099 94176 94179 94208
        100352 101632 110576 110581 110589 110592 110898 110928 110933 110948
        110960 113664 113776 113792 113808 119488 119520 119648 119808 119894
        119966 119970 119973 119977 119982 119995 119997 120005 120071 120077
        120086 120094 120123 120128 120134 120138 120146 120488 120514 120540
        120572 120598 120630 120656 120688 120714 120746 120772 120782 122624
        122661 122928 123136 123191 123200 123214 123536 123584 123632 124112
        124144 124896 124904 124909 124912 124928 125127 125184 125259 125264
        126065 126125 126129 126209 126255 126464 126469 126497 126500 126503
        126505 126516 126521 126523 126530 126535 126537 126539 126541 126545
        126548 126551 126553 126555 126557 126559 126561 126564 126567 126572
        126580 126585 126590 126592 126603 126625 126629 126635 127232 130032
        131072 173824 177984 178208 183984 194560 196608 201552 )
    WT_HI=( 170 179 181 186 190 214 246 705 721 740 748 750 884 887 893 895
        902 906 908 929 1013 1153 1327 1366 1369 1416 1514 1522 1610 1641 1647
        1747 1749 1766 1788 1791 1808 1839 1957 1969 2026 2037 2042 2069 2074
        2084 2088 2136 2154 2183 2190 2249 2361 2365 2384 2401 2415 2432 2444
        2448 2472 2480 2482 2489 2493 2510 2525 2529 2545 2553 2556 2570 2576
        2600 2608 2611 2614 2617 2652 2654 2671 2676 2701 2705 2728 2736 2739
        2745 2749 2768 2785 2799 2809 2828 2832 2856 2864 2867 2873 2877 2909
        2913 2927 2935 2947 2954 2960 2965 2970 2972 2975 2980 2986 3001 3024
        3058 3084 3088 3112 3129 3133 3162 3165 3169 3183 3198 3200 3212 3216
        3240 3251 3257 3261 3294 3297 3311 3314 3340 3344 3386 3389 3406 3414
        3425 3448 3455 3478 3505 3515 3517 3526 3567 3632 3635 3654 3673 3714
        3716 3722 3747 3749 3760 3763 3773 3780 3782 3801 3807 3840 3891 3911
        3948 3980 4138 4169 4181 4189 4193 4198 4208 4225 4238 4249 4293 4295
        4301 4346 4680 4685 4694 4696 4701 4744 4749 4784 4789 4798 4800 4805
        4822 4880 4885 4954 4988 5007 5109 5117 5740 5759 5786 5866 5880 5905
        5937 5969 5996 6000 6067 6103 6108 6121 6137 6169 6264 6276 6312 6314
        6389 6430 6509 6516 6571 6601 6618 6678 6740 6793 6809 6823 6963 6988
        7001 7072 7141 7203 7241 7293 7304 7354 7359 7404 7411 7414 7418 7615
        7957 7965 8005 8013 8023 8025 8027 8029 8061 8116 8124 8126 8132 8140
        8147 8155 8172 8180 8188 8305 8313 8329 8348 8450 8455 8467 8469 8477
        8484 8486 8488 8493 8505 8511 8521 8526 8585 9371 9471 10131 11492
        11502 11507 11517 11557 11559 11565 11623 11631 11670 11686 11694
        11702 11710 11718 11726 11734 11742 11823 12295 12329 12341 12348
        12438 12447 12538 12543 12591 12686 12693 12735 12799 12841 12879
        12895 12937 12991 19903 42124 42237 42508 42539 42606 42653 42735
        42783 42888 42954 42961 42963 42969 43009 43013 43018 43042 43061
        43123 43187 43225 43255 43259 43262 43301 43334 43388 43442 43481
        43492 43518 43560 43586 43595 43609 43638 43642 43695 43697 43702
        43709 43712 43714 43741 43754 43764 43782 43790 43798 43814 43822
        43866 43881 44002 44025 55203 55238 55291 64109 64217 64262 64279
        64285 64296 64310 64316 64318 64321 64324 64433 64829 64911 64967
        65019 65140 65276 65305 65338 65370 65470 65479 65487 65495 65500
        65547 65574 65594 65597 65613 65629 65786 65843 65912 65931 66204
        66256 66299 66339 66378 66421 66461 66499 66511 66517 66717 66729
        66771 66811 66855 66915 66938 66954 66962 66965 66977 66993 67001
        67004 67382 67413 67431 67461 67504 67514 67589 67592 67637 67640
        67644 67669 67702 67742 67759 67826 67829 67867 67897 68023 68047
        68096 68115 68119 68149 68168 68222 68255 68295 68324 68335 68405
        68437 68466 68497 68527 68680 68786 68850 68899 68921 69246 69289
        69297 69415 69445 69460 69505 69579 69622 69687 69743 69746 69749
        69807 69864 69881 69926 69951 69956 69959 70002 70006 70066 70084
        70106 70108 70132 70161 70187 70208 70278 70280 70285 70301 70312
        70366 70393 70412 70416 70440 70448 70451 70457 70461 70480 70497
        70708 70730 70745 70753 70831 70853 70855 70873 71086 71131 71215
        71236 71257 71338 71352 71369 71450 71483 71494 71723 71922 71942
        71945 71955 71958 71983 71999 72001 72025 72103 72144 72161 72163
        72192 72242 72250 72272 72329 72349 72440 72712 72750 72768 72812
        72847 72966 72969 73008 73030 73049 73061 73064 73097 73112 73129
        73458 73474 73488 73523 73561 73648 73684 74649 74862 75075 77808
        78895 78918 83526 92728 92766 92777 92862 92873 92909 92975 92995
        93017 93025 93047 93071 93846 94026 94032 94111 94177 94179 100343
        101589 101640 110579 110587 110590 110882 110898 110930 110933 110951
        111355 113770 113788 113800 113817 119507 119539 119672 119892 119964
        119967 119970 119974 119980 119993 119995 120003 120069 120074 120084
        120092 120121 120126 120132 120134 120144 120485 120512 120538 120570
        120596 120628 120654 120686 120712 120744 120770 120779 120831 122654
        122666 122989 123180 123197 123209 123214 123565 123627 123641 124139
        124153 124902 124907 124910 124926 125124 125135 125251 125259 125273
        126123 126127 126132 126253 126269 126467 126495 126498 126500 126503
        126514 126519 126521 126523 126530 126535 126537 126539 126543 126546
        126548 126551 126553 126555 126557 126559 126562 126564 126570 126578
        126583 126588 126590 126601 126619 126627 126633 126651 127244 130041
        173791 177977 178205 183969 191456 195101 201546 205743 )
}

wt_has() {
    local il=0 ih mid
    (( ${#WT_LO[@]} )) || wt_load
    ih=$(( ${#WT_LO[@]} - 1 ))
    while (( il <= ih )); do
        mid=$(( (il + ih) / 2 ))
        if (( $1 < WT_LO[mid] )); then
            ih=$((mid - 1))
        elif (( $1 > WT_HI[mid] )); then
            il=$((mid + 1))
        else
            return 0
        fi
    done
    return 1
}

main
exit $?
