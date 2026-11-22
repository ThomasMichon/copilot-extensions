# New-footprint machine onboarding (in development)

This entry is separate from the current Worktree Manager bootstrap. The
commands below describe the intended interface; **no released installer is
available at these paths yet**.

```bash
url=https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/new-footprint/bootstrap.sh
curl -fsSL "$url" | bash -s -- --srcroot "$HOME/src" --repo example/harness
```

`bash -s` reads the script from standard input. To pass the harness by
environment instead, use `curl -fsSL "$url" | HARNESS_REPO=example/harness bash`.
An assignment on the `curl` side is not visible to Bash.

```powershell
$url = 'https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/new-footprint/bootstrap.ps1'
& ([scriptblock]::Create((Invoke-RestMethod $url))) -SrcRoot "$HOME\src" -Repo 'example/harness'
```

On a fresh machine, omitting the harness selects Manager alone from current
remote `main`. With a harness, bootstrap first discovers existing new-footprint
registrations and pairings. It reuses matching checkouts or clones under the
selected source root (default `~/src`); an existing local checkout is also
a valid input. It must not replace a checkout with a different origin.

**Authentication:** In a local interactive terminal, the PowerShell entry checks
both GitHub API access and HTTPS Git access to the selected remote repositories.
If needed, it opens GitHub CLI's browser sign-in and checks again. An
organization can still require the user to approve SSO or obtain repository
access; successful sign-in alone is not sufficient.

For SSH automation, obtain a short-lived, read-only credential locally for
the selected repositories. Supply it to the remote bootstrap process as
`GH_TOKEN` through the authenticated transport before invocation. Setting
`GH_TOKEN` in the local shell does not by itself forward it over SSH. Do not
put it in the SSH command line, repository URL, logs or a remote login store.
The bootstrap uses `gh auth git-credential` for GitHub HTTPS operations for
that invocation; it does not switch accounts or change global Git settings.
An unattended invocation with no usable supplied credential fails before
registration. Cleanup does not require repository sign-in.

The Windows SSH caller `install/new-footprint/invoke-ssh.ps1` accepts
`-Target`, an exact `-BootstrapRevision`, the bootstrap repository and path
arguments, and `-TokenProvider { ... }`. The provider runs **locally** and
returns one token on standard output. Use a provider scoped to the intended
repositories and the target. The caller sends it on SSH stdin, not in the
remote command, and the receiving process sets `GH_TOKEN` before invoking
the pinned bootstrap. No persistent `gh auth login --with-token` is needed.

The bootstrap script's download branch is not the install version. After
refreshing registered harnesses, bootstrap resolves the configured suite
reference to a commit and installs Manager from that commit. That Manager
handles knowledge pairing, plugin hoisting and staging the union of required
services. Staging is not service activation. Re-running without arguments
must preserve existing registrations, not reset the machine to Manager alone.
