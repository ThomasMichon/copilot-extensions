---
name: budget-guidance-setup
description: |
  Installs or refreshes the budget-guidance runtime and configures its offline static budget reading.
  Trigger phrases include:
  - 'setting up budget-guidance'
  - 'installing budget-guidance'
  - 'manual budget posture configuration'
---

# Budget Guidance Setup

Use the plugin root supplied by the host. Never search installed marketplaces
or choose a same-named command from `PATH`.

Run the platform installer from that exact payload:

```bash
bash "$COPILOT_PLUGIN_ROOT/scripts/install.sh" install
```

```powershell
& (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts\install.ps1') -Action install
```

Create machine-local inert JSON at `~/.budget-guidance/config.json`, following
`docs/configuration.md`. Do not commit personal allowance or consumption data.
Verify with the exact `budget-guidance` argv from the session command catalog:

```text
<budget-guidance catalog argv[0]> status --json
```
