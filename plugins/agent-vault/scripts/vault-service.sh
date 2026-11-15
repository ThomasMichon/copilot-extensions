#!/bin/sh
set -eu

python=$1
root=$2
shift 2
selected="$("$python" -I -B -X utf8 -c '
import sys
try:
    from agent_procutil.python_cache import POLICY_VERSION, policy_arguments
    assert POLICY_VERSION == 2
    arguments = policy_arguments(sys.argv[1], runtime=sys.executable)
    assert arguments == ["-B"] or (
        len(arguments) == 2 and arguments[0] == "-X"
        and arguments[1].startswith("pycache_prefix=") and len(arguments[1]) > 15
        and not any(c in arguments[1] for c in "\r\n\0"))
except Exception:
    arguments = ["-B"]
sys.stdout.buffer.write("\n".join(arguments).encode("utf-8"))
' "$root" 2>/dev/null)" || selected=""
case "$selected" in
    '-X
pycache_prefix='*)
        exec "$python" -X "${selected#*
}" -m agent_vault.service "$@" ;;
esac
exec "$python" -B -m agent_vault.service "$@"
