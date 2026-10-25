# Preserve home-installed plugin launchers without changing the root init PATH.
if [ "$(id -un)" = vscode ]; then
    export PATH="$HOME/.local/bin:/opt/trusted-harness/node/bin:/opt/trusted-harness/copilot:/opt/trusted-harness/uv:$PATH"
fi
