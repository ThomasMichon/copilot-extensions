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

The bootstrap script's download branch is not the install version. After
refreshing registered harnesses, bootstrap resolves the configured suite
reference to a commit and installs Manager from that commit. That Manager
handles knowledge pairing, plugin hoisting and staging the union of required
services. Staging is not service activation. Re-running without arguments
must preserve existing registrations, not reset the machine to Manager alone.
