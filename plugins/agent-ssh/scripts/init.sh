#!/usr/bin/env bash
# Back-compat bootstrap entrypoint: delegate to the canonical install script.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$here/install.sh" install "$@"
