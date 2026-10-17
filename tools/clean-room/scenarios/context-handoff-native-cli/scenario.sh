#!/usr/bin/env bash
set -euo pipefail
source "${CR_LIB:?}"
source "$(dirname "$CR_LIB")/tmux-drive.sh"
export CR_SCENARIO_NAME=context-handoff-native-cli
cr_init
trap 'jam setup-command "setup command failed at line $LINENO" "inspect captured setup logs"; cr_finalize' ERR
cr_meta role starting-state-only
phase 0 "fresh isolated venue and supported CLI surface"
envdump
capture cli-help -- copilot --help
capture config-help -- copilot help config
capture commands-help -- copilot help commands
capture fixture-tests -- bash -lc \
    "cd /home/operator/scenario && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -p 'test_*.py'"
if [ -e "$HOME/.copilot/settings.json" ] || [ -e "$HOME/.agent-worktrees" ] || [ -e "$HOME/.agent-dispatch" ]; then
    jam repo-config "venue was not fresh" "use a new uniquely named clean-room container"
    cr_finalize
    exit 1
fi
phase 1 "fetch released payload"
capture release-fetch -- git clone --quiet --depth 1 --branch main \
    https://github.com/ThomasMichon/copilot-extensions.git "$HOME/released-marketplace"
PAYLOAD="$HOME/released-marketplace/plugins/context-handoff"
python3 /home/operator/scenario/controller.py fingerprint "$PAYLOAD"
if ! python3 /home/operator/scenario/controller.py verify-release; then
    jam plugin-load "released payload version or native-context fingerprint differs" \
        "record the release mismatch; do not substitute an unpublished source"
    cr_finalize
    exit 1
fi
pass "released payload version and fingerprint verified"
mkdir -p "$HOME/.copilot"
cat >"$HOME/.copilot/settings.json" <<JSON
{
  "experimental": true,
  "contextManagementTools": true,
  "extraKnownMarketplaces": {
    "copilot-extensions": {"source": {"source": "directory", "path": "$HOME/released-marketplace"}}
  },
  "enabledPlugins": {"context-handoff@copilot-extensions": true}
}
JSON
capture plugin-install -- copilot plugin install context-handoff@copilot-extensions
phase 2 "synthetic task and real CLI discovery"
if ! command -v setpriv >/dev/null 2>&1; then
    jam baseline-protection "setpriv is required to prevent driven sudo escalation" "use a supported base image"
    cr_finalize
    exit 1
fi
capture privilege-helper-help -- setpriv --help
if ! cr_tmux_ensure; then
    jam tmux-unavailable "could not provision the documented real CLI lane" "no ACP substitution"
    cr_finalize
    exit 1
fi
python3 /home/operator/scenario/controller.py seed
capture seed-baseline-ready -- python3 /home/operator/scenario/validation.py \
    verify-baseline /home/operator/native-cli-repo /opt/copilot-cleanroom/native-cli-baseline
pass "seed baseline established and disposable operator sudo privileges sealed before launch"
cr_meta seed_baseline "/opt/copilot-cleanroom/native-cli-baseline"
cr_meta transport interactive-cli-tmux
cr_meta verdict NOT-JUDGED
cr_finalize
