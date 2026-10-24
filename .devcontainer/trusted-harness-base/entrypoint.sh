#!/bin/bash
set -euo pipefail

if [ "$(id -u)" != 0 ] || [ "$#" != 1 ] || [ "$1" != /lib/systemd/systemd ]; then
    echo "Use root PID 1 with /lib/systemd/systemd; agent sessions use vscode." >&2
    exit 64
fi
for path in /home/vscode /workspaces; do
    if ! mountpoint -q "$path"; then
        echo "Required per-container persistent mount is missing: $path" >&2
        exit 65
    fi
done
if [ "$(id -u vscode)" != 1000 ] || [ "$(id -g vscode)" != 1000 ]; then
    echo "Only vscode UID/GID 1000 is supported." >&2
    exit 65
fi
python3 /opt/trusted-harness/recipe/install.py --runtime
install -d -m 755 /run/sshd
/usr/sbin/sshd -t
# Same explicit cgroup requirement as agent-containers' trusted systemd profile.
mount -o remount,rw /sys/fs/cgroup
exec /lib/systemd/systemd
