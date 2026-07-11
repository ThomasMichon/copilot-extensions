---
name: troubleshooting-wsl-networking
description: >
  Diagnose and fix WSL2 networking failures on locked-down Windows - no internet
  egress from WSL (apt "No route to host") behind a corporate host-vNIC filter,
  host<->WSL loopback that times out under mirrored networking, and services that
  vanish when the distro idles. Provides the diagnosis order, the NAT vs mirrored
  fix, offline package sideloading, and the keepalive. Use when WSL can't reach
  the internet, apt fails, a WSL service is unreachable from Windows, or a WSL
  listener disappears. Trigger phrases include:
  - 'WSL no internet'
  - 'WSL apt No route to host'
  - 'WSL egress blocked'
  - 'cant reach WSL from Windows'
  - 'WSL localhost timed out'
  - 'WSL service unreachable'
  - 'WSL loopback broken'
  - 'WSL distro keeps stopping'
---

# Troubleshooting WSL2 networking

**Diagnose before you change anything.** These three failures look alike (a
timeout) but have different causes. Identify which one you have first.

| Symptom | Likely cause | Go to |
|---------|--------------|-------|
| `apt`/`curl` from WSL time out; host is fine | Corp host-vNIC filter blocks the WSL adapter's **egress** | § A |
| `Windows localhost:PORT` (or in-WSL `127.0.0.1:PORT`) times out, service is listening | **mirrored** networking loopback redirect | § B |
| Service was reachable, now **refused**; distro shows `Stopped` | Distro **idled out** (service died) | § C |

Quick triage:

```powershell
# host vs WSL egress (WSL-specific if host is 200 and WSL times out)
Invoke-WebRequest https://archive.ubuntu.com -Method Head -TimeoutSec 8   # host
wsl -d <distro> -u root bash -c "curl -m8 -sSI https://archive.ubuntu.com >/dev/null && echo WSL-OK || echo WSL-BLOCKED"
# service reachability from Windows
Test-NetConnection localhost -Port <PORT>
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

## § B. Host↔WSL loopback broken (mirrored networking)

**Signature:** the service is listening (`ss -tlnp | grep :PORT` inside WSL shows
`0.0.0.0:PORT`), yet **in-WSL `127.0.0.1:PORT` and Windows `localhost:PORT` both
time out**. `.wslconfig` has `networkingMode=mirrored`.

**Cause:** in mirrored mode WSL rewrites `127.0.0.1` routing through a special
loopback device to the host relay (shared-localhost), so a "loopback" connection
is forwarded to the **Windows** localhost (where nothing listens) instead of the
WSL service. Corp host-vNIC filters compound this by dropping the relay traffic.

**Confirm:**
```powershell
wsl -d <distro> -u root bash -c "ip rule; ip route show table 127"   # 127.0.0.1 via loopback0
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
After reboot, in-WSL `127.0.0.1:PORT` opens and Windows `localhost:PORT` reaches
the service via the host relay. **No Hyper-V/Windows firewall rule is needed** for
this loopback path.

> Tried-and-insufficient in mirrored mode (documented so you don't repeat them):
> `hostAddressLoopback=true` flips *refused* → *timeout* (forwards but the corp
> filter still drops), and a Hyper-V VM firewall inbound-allow for the port
> (`New-NetFirewallHyperVRule -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'`)
> did **not** restore reachability. NAT is the fix; don't burn time on mirrored.

**Preserve intent:** if the user chose `mirrored`+`dnsTunneling` for corp VPN,
NAT keeps `dnsTunneling`; note the change is reversible if VPN DNS/routing regresses.

---

## § C. Distro won't stay up (service disappears)

**Signature:** a service that worked minutes ago is now **refused**; `wsl -l -v`
shows the distro `Stopped`. Even with systemd, WSL terminates an **idle** distro,
and Docker Desktop keeps only the *WSL VM* up, not your distro.

**Fix — keepalive:** a `sleep infinity` process pins the distro; a logon
Scheduled Task makes it survive reboots. See `setting-up-wsl` § "Keep the distro
alive". Verify:

```powershell
Start-Process -WindowStyle Hidden wsl.exe -ArgumentList '-d','<distro>','-u','root','--exec','/bin/sh','-c','systemctl start <svc>; exec sleep infinity'
Start-Sleep 5; wsl -l -v            # distro -> Running
1..3 | % { Start-Sleep 3; Test-NetConnection localhost -Port <PORT> | Select -Expand TcpTestSucceeded }
```

---

## General discipline

- **Host-reaches-it-but-WSL-doesn't ⇒ WSL-specific** — stop testing external IPs;
  focus on egress (§A) or loopback (§B).
- **Inbound service hosting never needs WSL egress** — don't block on §A to stand
  up a listener; sideload and move on.
- **`wsl --shutdown` is the only way `.wslconfig` applies** — and it bounces
  Docker; get the user's OK first.
