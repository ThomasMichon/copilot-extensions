---
name: troubleshooting-wsl-networking
description: >
  Diagnose and fix WSL2 networking failures on locked-down Windows - no internet
  egress from WSL (apt "No route to host") behind a corporate host-vNIC filter,
  host<->WSL localhost failures under mirrored networking, and services that
  vanish when the distro idles. Provides the diagnosis order, the NAT vs mirrored
  fix, offline package sideloading, port-shadowing checks, and the windowless
  keepalive helper. Use when WSL can't reach the internet, apt fails, a WSL
  service is unreachable from Windows, or a WSL listener disappears. Trigger
  phrases include:
  - 'WSL no internet'
  - 'WSL apt No route to host'
  - 'WSL egress blocked'
  - 'cant reach WSL from Windows'
  - 'WSL localhost timed out'
  - 'WSL service unreachable'
  - 'WSL loopback broken'
  - 'WSL distro keeps stopping'
  - 'WSL port shadowed by Windows'
  - 'WSL sshd unreachable on 22'
---

# Troubleshooting WSL2 networking

**Diagnose before you change anything.** These failures look alike (a
timeout or a wrong/refused response) but have different causes. Identify which one
you have first.

| Symptom | Likely cause | Go to |
|---------|--------------|-------|
| `apt`/`curl` from WSL time out; host is fine | Corp host-vNIC filter blocks the WSL adapter's **egress** | § A |
| `Windows localhost:PORT` times out/refuses while the service works inside WSL | **mirrored** networking relay failure on a locked-down host, or the wrong mode for service hosting | § B |
| Service was reachable, now **refused**; distro shows `Stopped` | Distro **idled out** (service died) | § C |
| `Windows localhost:PORT` reaches the **wrong** service / unexpected auth failure (esp. `:22`) | A **Windows** process binds the port and shadows the WSL forward | § D |

Quick triage:

```powershell
# host vs WSL egress (WSL-specific if host is 200 and WSL times out)
Invoke-WebRequest https://archive.ubuntu.com -Method Head -TimeoutSec 8   # host
wsl -d <distro> -u root bash -c "curl -m8 -sSI https://archive.ubuntu.com >/dev/null && echo WSL-OK || echo WSL-BLOCKED"
# service reachability from Windows
Test-NetConnection localhost -Port <PORT>
# service reachability inside WSL (separates service config from host relay)
wsl -d <distro> -- bash -lc "ss -tlnp | grep ':<PORT>'"
wsl -d <distro> -- bash -lc "timeout 3 bash -c '</dev/tcp/127.0.0.1/<PORT>' && echo WSL-LOCAL-OK || echo WSL-LOCAL-FAIL"
# distro state
wsl -l -v
```

---

## § A. No egress from WSL (corp host-vNIC filter)

**Signature:** DNS resolves (dnsTunneling) but TCP times out — `apt` shows
`Could not connect ... No route to host`. The **Windows host reaches the same
endpoints fine**, and there is no proxy (`netsh winhttp show proxy` = Direct).

**Cause:** a corporate host network-filter adapter (e.g. an `FSE HostVnic` or
similar host-vNIC security filter) admits traffic from recognized host processes
but not the WSL virtual adapter. This is enforced below WSL and is **not
something to fight from inside WSL**.

**Confirm it's WSL-specific** (host reaches it, WSL doesn't) — then don't rabbit-hole.

**Fixes, in order of preference:**

1. **Sideload packages offline** (best when you only need to install something —
   inbound service hosting does NOT need WSL egress). See below.
2. Ask whether the corp filter can admit the WSL adapter/subnet (org-dependent).
3. Try `networkingMode=nat` — NAT masquerades WSL through the host vEthernet and
   sometimes passes the filter where mirrored doesn't (also fixes § B). Requires
   `wsl --shutdown`.

**File the environmental blocker** so it isn't rediscovered — it will bite any
future `apt`/`pip`/`npm` in WSL on that machine.

### Offline package install (sideload .deb)

WSL sshd (or any inbound service) needs **no** WSL egress — only the one-time
package install did. Download on Windows (which has connectivity), install with
`dpkg`:

```powershell
# 1. Resolve the EXACT release build (match the distro, not the newest pool version).
#    Jammy (22.04) example: openssh-server_8.9p1-3ubuntu0.NN. List candidates:
(Invoke-WebRequest 'http://security.ubuntu.com/ubuntu/pool/main/o/openssh/' -UseBasicParsing).Links.href |
  Where-Object { $_ -match 'openssh-server_8\.9p1-3ubuntu0\..*amd64\.deb$' }

# 2. Download the .deb(s) + strict deps to a temp dir, then dpkg -i in WSL:
$dl="$env:TEMP\wsl-debs"; New-Item -ItemType Directory -Force $dl | Out-Null
# Invoke-WebRequest <url> -OutFile "$dl\<file>.deb"   (server + sftp-server + libwrap0, etc.)
wsl -d <distro> -u root bash -c "dpkg -i /mnt/c/Users/<you>/AppData/Local/Temp/wsl-debs/*.deb"
```

Pick versions matching the distro's glibc (a newer-release build breaks). Runtime
libs (libssl3, libkrb5, ...) are usually already present; `dpkg` will name any
missing strict dep — fetch and add it the same way.

---

## § B. Host↔WSL localhost broken (mirrored networking on a locked-down host)

**Signature:** the service is listening and reachable inside WSL (`ss -tlnp |
grep :PORT`, then an app-level `127.0.0.1:PORT` check), but **Windows
`localhost:PORT` times out/refuses** or reaches the host side. `.wslconfig` has
`networkingMode=mirrored`.

**Cause:** mirrored mode is designed to support localhost between Windows and
WSL, and WSL documentation recommends it for VPN compatibility. On some
locked-down corporate host-vNIC/filter stacks, however, that localhost relay path
is filtered or lands on the host-side listener instead of the WSL service. Also,
`localhostForwarding=true` is ignored in mirrored mode; it only applies to NAT.

**Confirm:**
```powershell
wsl -d <distro> -- bash -lc "ss -tlnp | grep ':<PORT>'"
wsl -d <distro> -- bash -lc "timeout 3 bash -c '</dev/tcp/127.0.0.1/<PORT>' && echo WSL-LOCAL-OK || echo WSL-LOCAL-FAIL"
Test-NetConnection localhost -Port <PORT>
```

**Fix — switch to NAT + localhostForwarding** (the robust, well-understood path):

```ini
# %USERPROFILE%\.wslconfig
[wsl2]
networkingMode=nat
localhostForwarding=true
dnsTunneling=true
```
```powershell
wsl --shutdown   # bounces all distros incl. Docker (auto-recovers)
```
After WSL restarts, Windows `localhost:PORT` reaches the service through NAT's
host relay. **No Hyper-V/Windows firewall rule is needed** for this loopback
path.

> Tried-and-insufficient in mirrored mode (documented so you don't repeat them):
> `hostAddressLoopback=true` (an `[experimental]` mirrored-mode key) affects
> host-assigned IP addresses, not the NAT `localhostForwarding` relay; it may
> change *refused* into *timeout* without making the WSL service reachable. A
> Hyper-V VM firewall inbound allow
> (`New-NetFirewallHyperVRule -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'`)
> is for mirrored/LAN inbound exposure, not the NAT localhost path. For a
> Windows/tunnel-to-WSL service hop, switch to NAT instead of burning time on
> mirrored.

**Preserve intent:** if the user chose `mirrored`+`dnsTunneling` for corp VPN,
NAT keeps `dnsTunneling`; note the change is reversible if VPN DNS/routing regresses.

---

## § C. Distro won't stay up (service disappears)

**Signature:** a service that worked minutes ago is now **refused**; `wsl -l -v`
shows the distro `Stopped`. Even with systemd, WSL terminates an **idle** distro,
and Docker Desktop keeps only the *WSL VM* up, not your distro.

**Fix — keepalive:** a `sleep infinity` process pins the distro; a logon
Scheduled Task makes it survive reboots. Use the bundled windowless helper from
`setting-up-wsl` § "Keep the distro alive":

```powershell
# install/uninstall need elevation; status does not.
$ka = 'plugins\wsl-setup\skills\setting-up-wsl\references\wsl-keepalive.ps1'
pwsh -File $ka install -Distro <distro> -Service <svc> -TaskName WSL-Keepalive-<svc>
pwsh -File $ka status  -TaskName WSL-Keepalive-<svc> -Distro <distro> -Service <svc>
wsl -l -v                            # distro -> Running
1..3 | % { Start-Sleep 3; Test-NetConnection localhost -Port <PORT> | Select -Expand TcpTestSucceeded }
```

`-Service` starts the systemd service once before pinning (`systemctl start
<svc>; exec sleep infinity`); the helper is not a service monitor. Use
`systemctl enable <svc>` and the service unit's own restart policy for ongoing
service recovery.

---

## § D. A Windows listener shadows the WSL port (localhostForwarding no-op)

**Signature:** the WSL service is listening (`ss -tlnp` inside WSL shows
`0.0.0.0:PORT`) and the distro is up, yet `Windows localhost:PORT` reaches the
**wrong** service (or auth fails with keys that only the WSL service should
accept) — intermittently or after a Windows service starts. Classic case: **port
22**, where a Windows OpenSSH `sshd` binds `:22`.

**Cause:** `localhostForwarding` only forwards a Windows loopback port to WSL when
**nothing on Windows is already bound to it**. If a Windows process binds the same
port, the Windows binding wins and WSL is silently shadowed.

**Confirm:**
```powershell
Get-NetTCPConnection -State Listen -LocalPort <PORT> |
  ForEach-Object { $_.OwningProcess } | ForEach-Object { (Get-Process -Id $_).Path }
```
If a Windows process (not the WSL relay) owns it, that's the shadow.

**Fix — give the WSL service its own port.** Run the WSL listener on an unused
port (e.g. sshd on `2200` via `/etc/ssh/sshd_config.d/*.conf` → `Port 2200`) and
reference that port everywhere. Never co-locate a WSL service on a port a Windows
service also uses. Mirrored mode's `ignoredPorts` can let Linux bind a port that
Windows also uses, but it does not make Windows `localhost:PORT` unambiguously
reach the Linux service; a dedicated port is still the clean fix.

---

## General discipline

- **Host-reaches-it-but-WSL-doesn't ⇒ WSL-specific** — stop testing external IPs;
  focus on egress (§A) or loopback (§B).
- **Inbound service hosting never needs WSL egress** — don't block on §A to stand
  up a listener; sideload and move on.
- **`wsl --shutdown` is the only way `.wslconfig` applies** — and it bounces
  Docker; get the user's OK first.
