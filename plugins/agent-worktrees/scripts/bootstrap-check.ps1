param([string]$BootstrapPython = '')

# Retain the legacy parameter for hook-client compatibility, without invoking it.
# Explicit setup/update owns runtime installation; the hook client owns guidance.
[Console]::Out.Write('{}')
exit 0
