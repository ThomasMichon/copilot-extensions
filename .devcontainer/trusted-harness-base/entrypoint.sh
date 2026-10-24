#!/bin/bash
set -euo pipefail

if [ "$(id -u)" != 0 ] || [ "$$" != 1 ] || [ "$#" != 1 ] || [ "$1" != /lib/systemd/systemd ]; then
    echo "Use root PID 1 with /lib/systemd/systemd; agent sessions use vscode." >&2
    exit 64
fi
if [ "$(findmnt -n -o PROPAGATION /)" != private ]; then
    echo "Initial mount propagation must be private before systemd starts." >&2
    exit 65
fi
if ! mountpoint -q /run || [ -L /run ] || [ -L /run/systemd ]; then
    echo "A private /run mount and an unlinked systemd runtime root are required." >&2
    exit 65
fi
install -d -m 755 /run/systemd
mount --bind /run/systemd /run/systemd
if ! mountpoint -q /run/systemd; then
    echo "The systemd runtime root must be pinned before namespace aliases exist." >&2
    exit 65
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
