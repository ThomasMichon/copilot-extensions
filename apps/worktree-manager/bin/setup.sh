#!/bin/sh
slot=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit
exec "$slot/install.sh" "$@"
