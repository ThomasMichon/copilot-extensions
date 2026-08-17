---
name: splitting-wsl-storage
description: >
  Split a WSL2 install across two backing stores on one Windows machine - keep
  the distro rootfs a small dynamic VHD on an NTFS partition, and move the bulk
  data (/home, /opt, heavy /var/lib/*) onto a dedicated NATIVE ext4 disk or
  partition that is bare-attached into WSL2 and bind-mounted back to the
  canonical paths. Use when WSL storage is large or I/O-heavy, when the rootfs
  VHD has grown huge, when you want native ext4 performance for container
  runtimes or datasets, or when you want the heavy data on a separate
  physical disk. Trigger phrases include:
  - 'split WSL storage'
  - 'move WSL data to a separate disk'
  - 'WSL on a native ext4 partition'
  - 'wsl --mount bare disk'
  - 'WSL rootfs is huge'
  - 'mount an ext4 disk into WSL'
  - 'WSL data disk'
  - 'bind mount /home /var/lib in WSL'
  - 'attach a disk to WSL at boot'
---

# Splitting WSL2 storage across an NTFS-hosted rootfs and a native ext4 disk

By default a WSL2 distro keeps **everything** — the OS and all of your data — in
a single dynamically-expanding `ext4.vhdx` living on an NTFS partition (usually
the Windows OS drive). That is fine until the data gets large or I/O-heavy: the
VHD balloons on the OS drive, every read/write pays the vhdx-over-NTFS
translation, and there is no fault/perf isolation between the OS and the data.

A robust alternative on a machine with a spare disk (or a free partition) is to
**split** the install:

- the distro **rootfs** (`/` — the OS and small system dirs) stays a small
  dynamic VHD on NTFS, and
- the **bulk data** (`/home`, `/opt`, and the heavy `/var/lib/*` such as
  container runtimes and datasets) lives on a **native ext4** disk/partition,
  attached into WSL2 with `wsl --mount ... --bare` and **bind-mounted** back to
  its canonical paths.

The service plane and your shells see a perfectly normal filesystem tree; the
bytes just live on native ext4 with no vhdx layer. This is the **environment**
side of setup — compose it with `setting-up-wsl` (install + networking) and, if
you also want a repo cloned in, `agent-worktrees:agent-worktrees-wsl-provision`.

> **Two hard identity rules that this whole procedure depends on** (skip them and
> a later reboot corrupts data): **mount by UUID inside WSL, attach by disk
> serial on Windows — never by `/dev/sdX` or by Windows disk number.** Both the
> Linux device node and the Windows disk number **renumber across reboots**; only
> the ext4 UUID and the physical serial are stable.

---

## 0. Decide: whole disk vs. a partition

- **A whole dedicated disk** is the simplest and is what the commands below
  assume: format the entire disk as ext4 (no partition table) and bare-attach
  the whole disk. Windows must not hold a filesystem on it.
- **A partition on a shared disk** works too: create a partition, format it
  ext4, and bare-attach the disk (WSL sees all partitions on it). Prefer a whole
  disk when you can — it removes a class of "Windows still owns a partition here"
  surprises.

Either way, the data disk must be **free of any filesystem Windows wants to
mount** while WSL owns it (see the RAW/Offline note in Gotchas).

## 1. Back up first

`wsl --shutdown` then copy the current `ext4.vhdx` somewhere safe (another disk
or a network share). This is a real data migration; a verified backup is your
undo. Find the vhdx path with:

```powershell
(Get-ChildItem "$env:LOCALAPPDATA\Packages" -Recurse -Filter ext4.vhdx -ErrorAction SilentlyContinue).FullName
# ...or wherever you imported the distro, e.g. a custom C:\...\WSL\<distro>\ext4.vhdx
```

## 2. Identify the data disk STRICTLY by serial (Windows)

Resolve the target disk by its **serial number**, not its disk number. This
matters most when two disks are the *same model* — the serial is the only safe
discriminator.

```powershell
Get-Disk | Select-Object Number, FriendlyName, SerialNumber, PartitionStyle, OperationalStatus
# Note the SerialNumber of the intended data disk. Everything below resolves the
# PHYSICALDRIVE number FROM that serial at the moment of use, because the number
# can change across reboots.
```

Take the disk offline to Windows so WSL can own it (whole-disk case):

```powershell
# Elevated. Confirm you have the RIGHT disk (by serial) before running this.
$serial = '<DATA_DISK_SERIAL>'.Trim().TrimEnd('.')   # normalize both sides
$disk   = @(Get-Disk | Where-Object { $_.SerialNumber -and $_.SerialNumber.Trim().TrimEnd('.') -eq $serial })
if ($disk.Count -ne 1) { throw "expected exactly 1 disk with serial $serial, found $($disk.Count)" }
Set-Disk -Number $disk[0].Number -IsOffline $true
```

## 3. Bare-attach the disk into WSL and format it ext4

WSL cannot attach its own bare disk — a **Windows-side** `wsl --mount --bare`
does it. Resolve the path from the serial each time:

```powershell
# Elevated. --bare attaches the raw block device to the WSL2 VM (no auto-mount).
$serial = '<DATA_DISK_SERIAL>'.Trim().TrimEnd('.')   # normalize both sides
$disk   = @(Get-Disk | Where-Object { $_.SerialNumber -and $_.SerialNumber.Trim().TrimEnd('.') -eq $serial })
if ($disk.Count -ne 1) { throw "expected exactly 1 disk with serial $serial, found $($disk.Count)" }
wsl.exe --mount "\\.\PHYSICALDRIVE$($disk[0].Number)" --bare
```

Inside WSL the disk appears as a `/dev/sdX` block device. **Format it once**
(whole-disk ext4 shown; for a partition, target the partition node instead):

```bash
# In WSL, as root. DESTRUCTIVE — triple-check this is the empty data disk.
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT        # find the new, empty device
mkfs.ext4 -L wsl-data /dev/sdX              # whole-disk ext4, labelled
blkid /dev/sdX                              # RECORD the UUID it prints
```

> **Save that UUID.** Everything persistent references the disk by UUID, never by
> `/dev/sdX` (which will not be `sdX` next boot).

## 4. Migrate the heavy directories onto the ext4

Measure before you move — the biggest consumers are often **not** what you
assume (container runtimes and caches frequently dwarf `/home`):

```bash
# In WSL, as root: what actually uses space
du -x -d1 -h / 2>/dev/null | sort -h
du -x -d1 -h /var/lib 2>/dev/null | sort -h
```

Mount the ext4 at a staging point and `rsync` each heavy directory into a
path-mirrored subdirectory, so the bind-mounts in step 5 are a clean 1:1 map:

```bash
# In WSL, as root
mkdir -p /mnt/wsl-data
mount UUID=<UUID> /mnt/wsl-data             # mount by UUID (not /dev/sdX — it renumbers)

# Quiesce writers first (stop docker/containerd and app services that write these
# paths) so the copy is consistent, THEN copy. Example set — adjust to your data:
rsync -aHAX --numeric-ids /home/     /mnt/wsl-data/home/
rsync -aHAX --numeric-ids /opt/      /mnt/wsl-data/opt/
rsync -aHAX --numeric-ids /var/lib/docker/ /mnt/wsl-data/varlib-docker/
# ...repeat for each heavy /var/lib/<svc> you are relocating.
```

Verify counts/sizes match before you trust the copy (`rsync` exit 0, spot-check
`du`/file counts). Keep the originals until step 6 proves the binds work.

## 5. Persist: mount by UUID + bind-mounts in /etc/fstab

Add the ext4 mount (by **UUID**) and one bind-mount per relocated directory.
`nofail` keeps a missing disk from wedging boot; `x-systemd.requires-mounts-for`
orders each bind after the ext4 is mounted:

```fstab
# /etc/fstab  — replace <UUID> with the value from `blkid`
UUID=<UUID>  /mnt/wsl-data  ext4  defaults,nofail,x-systemd.device-timeout=30  0 0

/mnt/wsl-data/home         /home         none  bind,nofail,x-systemd.requires-mounts-for=/mnt/wsl-data  0 0
/mnt/wsl-data/opt          /opt          none  bind,nofail,x-systemd.requires-mounts-for=/mnt/wsl-data  0 0
/mnt/wsl-data/varlib-docker /var/lib/docker none bind,nofail,x-systemd.requires-mounts-for=/mnt/wsl-data 0 0
# ...one line per relocated /var/lib/<svc>
```

Enable systemd if not already (`[boot] systemd=true` in `/etc/wsl.conf`) — the
systemd `fstab-generator` is what turns these lines into mount units in the init
namespace, so the binds are visible to every service.

## 6. The crux: attach the disk BEFORE the distro boots

This is the step that makes or breaks the setup. `/etc/fstab` runs **inside**
WSL, but WSL cannot attach its own bare disk — so the `wsl --mount --bare` must
happen on the **Windows side before the distro's service plane starts**.
Otherwise fstab has no ext4 to mount, the `nofail` binds are skipped, and
services come up on the **now-empty** rootfs directories.

**Enforce this invariant: never boot the service plane diskless.** The reliable
pattern is a Windows scheduled task (or your keepalive launcher) that, at system
startup:

1. resolves the disk **by serial** → `\\.\PHYSICALDRIVE<N>`,
2. runs `wsl.exe --mount \\.\PHYSICALDRIVE<N> --bare` (idempotent — an
   already-attached disk is success), retrying for the early-boot window where
   the disk is not enumerated yet, and
3. **only after a confirmed attach**, boots/keepalives the distro
   (`wsl -d <distro> sleep infinity`). If the attach fails, do **not** boot — a
   loud, retried failure is far better than silently starting on empty dirs.

The `setting-up-wsl` skill's keepalive helper pins the distro up; on a split-
storage machine you want the **attach to run first, in the same task**, so the
distro only ever boots with its data present. A minimal PowerShell shape:

```powershell
# Runs at startup, before login. Resolve by serial, attach, THEN keepalive.
# Fail CLOSED: track an explicit $attached flag so a disk that never enumerates
# (wsl.exe never invoked) can't be mistaken for success via a stale $LASTEXITCODE.
$serial   = '<DATA_DISK_SERIAL>'.Trim().TrimEnd('.')   # normalize both sides
$attached = $false
for ($i = 0; $i -lt 10 -and -not $attached; $i++) {
  $disk = @(Get-Disk | Where-Object { $_.SerialNumber -and $_.SerialNumber.Trim().TrimEnd('.') -eq $serial })
  if ($disk.Count -eq 1) {
    $out = & wsl.exe --mount "\\.\PHYSICALDRIVE$($disk[0].Number)" --bare 2>&1
    # wsl.exe emits UTF-16LE; strip NULs before matching its text.
    $txt = (($out | Out-String) -replace "`0", "")
    if ($LASTEXITCODE -eq 0 -or $txt -match 'already') { $attached = $true }
  }
  if (-not $attached) { Start-Sleep -Seconds 3 }
}
if (-not $attached) {
  Write-Error "data disk not attached; refusing to boot WSL diskless"; exit 1
}
& wsl.exe -d <distro> sleep infinity
```

Register it as a startup scheduled task the same way `setting-up-wsl` registers
the keepalive (via a windowless launcher, run whether or not a user is logged
on). A periodic watchdog task should re-run the same attach before any recovery
restart, and — belt-and-suspenders — a logon task can re-attach too.

## 7. Verify, then remove the originals

```bash
findmnt -o TARGET,SOURCE,FSTYPE | grep -E 'wsl-data|/home|/opt|/var/lib'
df -h | grep -E 'Filesystem|/dev/sd'
```

You want `/mnt/wsl-data` and each bind target showing the ext4 device, and
`df` showing the data on the ext4 (not the rootfs). **The device letter may
differ from step 3 — that is expected** (you mounted by UUID precisely so it
doesn't matter). Once the binds are confirmed healthy across a real
`wsl --shutdown` + reboot cycle, reclaim the space the old copies occupy on the
rootfs (they are shadowed by the binds; delete them from the underlying rootfs,
not through the bind). Keep the step-1 backup until you are fully satisfied.

---

## Gotchas

- **Windows shows the data disk as `RAW` / `Offline` — that is CORRECT** while it
  is bare-attached to WSL. Do **not** initialize, online, or format it in Disk
  Management; that destroys the live WSL data. It returns to normal only if you
  `wsl --unmount` it.
- **Mount by UUID, attach by serial — never `/dev/sdX` or disk number.** Both
  renumber across reboots. This is the single most important rule here.
- **Two identical-model disks?** The serial tail is the only safe discriminator —
  match the full serial, never "the <model>". Attaching the wrong same-model disk
  is a data-loss bug the serial gate exists to prevent.
- **`wsl --shutdown` stops ALL distros** (including Docker Desktop's backend,
  which auto-recovers) and every service in your distro. On a service host it is
  an outage — quiesce dependents and schedule it.
- **A benign `Processing /etc/fstab with mount -a failed` line** can appear on
  `wsl.exe` invocations if you also have network mounts (e.g. CIFS with
  `_netdev`) that aren't ready yet; the `nofail` entries still get mounted by
  systemd. It is not a data-disk failure.
- **Docker/containers:** if you relocate `/var/lib/docker` (and containerd), do
  it with the daemon stopped, and let the bind put it back at
  `/var/lib/docker` — you should not need to change `data-root` in
  `daemon.json` because the path is unchanged; only the backing bytes moved.
- **Cross-device surprises are gone once bind-mounted:** because each directory
  is bind-mounted back to its original path, apps that assume `/home` and
  `/var/lib/<svc>` are on the same filesystem still see them at the same paths —
  but they are now on the ext4, so anything that special-cases the *rootfs*
  device (rare) should be re-checked.

## See also

- `setting-up-wsl` — install, networking mode, systemd, and the keepalive helper
  this composes with (attach-then-keepalive).
- `troubleshooting-wsl-networking` — egress/loopback failures, unrelated to
  storage but part of the same environment setup.
