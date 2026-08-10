#!/usr/bin/env bash
# bootstrap.sh — fetch and launch the copilot-extensions Configurator.
#
#   curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/configurator/bootstrap.sh | bash
#
# This is delivered OUTSIDE the plugin pipe: it does not require any Copilot
# plugin to be installed, and does not go through `copilot plugin`. It fetches
# the Configurator payload and runs it.
#
# Phase 0 assumes git + uv are already present; automatic prerequisite
# provisioning (installing Python/uv/etc. and prompting for restarts) lands in
# Phase 2 (issue #355). Override the fetched ref with $CONFIGURATOR_REF.

set -euo pipefail

REPO='https://github.com/ThomasMichon/copilot-extensions.git'
REF="${CONFIGURATOR_REF:-main}"
ROOT="$HOME/.copilot-extensions-configurator"
SRC="$ROOT/src"

echo 'copilot-extensions Configurator - bootstrap'

for tool in git uv; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: $tool is required for the Phase 0 bootstrap (automatic prerequisite install lands in Phase 2 / issue #355). Install $tool and re-run." >&2
        exit 1
    fi
done

mkdir -p "$ROOT"
if [ -d "$SRC/.git" ]; then
    git -C "$SRC" fetch --depth 1 origin "$REF" >/dev/null 2>&1
    git -C "$SRC" checkout -q FETCH_HEAD
else
    git clone --depth 1 --branch "$REF" "$REPO" "$SRC" >/dev/null 2>&1
fi

cd "$SRC/configurator"
exec uv run --quiet python -m configurator "$@"
