"""Master-password prompt helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

PROMPT_TIMEOUT = 60
PROMPT_TITLE = "Agent Vault"


def prompt_password(message: str = "KeePass master password:") -> str | None:
    """Prompt for the KeePass master password via GUI dialog or terminal."""
    safe_msg = message.replace('"', "'")

    # Windows GUI
    try:
        ps_script = r'''
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = "Agent Vault"
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.TopMost = $true

$lbl = New-Object System.Windows.Forms.Label
$lbl.Text = "VAULT_PROMPT_MSG"
$lbl.Location = New-Object System.Drawing.Point(15, 20)
$lbl.AutoSize = $true
$form.Controls.Add($lbl)

$box = New-Object System.Windows.Forms.TextBox
$box.Location = New-Object System.Drawing.Point(15, 45)
$box.Size = New-Object System.Drawing.Size(320, 20)
$box.UseSystemPasswordChar = $true
$form.Controls.Add($box)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = "OK"
$ok.Location = New-Object System.Drawing.Point(255, 80)
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.AcceptButton = $ok
$form.Controls.Add($ok)

$form.Size = New-Object System.Drawing.Size(370, 150)
if ($form.ShowDialog() -ne "OK" -or -not $box.Text) { Write-Output "CANCELLED"; return }
Write-Output $box.Text
'''.replace("VAULT_PROMPT_MSG", safe_msg)
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=PROMPT_TIMEOUT,
        )
        pw = r.stdout.strip().replace("\r", "")
        if pw and pw != "CANCELLED":
            return pw
        return None
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return None

    # Linux GUI (zenity / kdialog)
    for gui in ("zenity", "kdialog"):
        if shutil.which(gui) and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            try:
                if gui == "zenity":
                    args = ["zenity", "--password", "--title=Agent Vault"]
                else:
                    args = ["kdialog", "--password", message, "--title", "Agent Vault"]
                r = subprocess.run(args, capture_output=True, text=True, timeout=PROMPT_TIMEOUT)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
                return None
            except FileNotFoundError:
                pass
            except subprocess.TimeoutExpired:
                return None
            break

    # Terminal fallback (only when no GUI was available and session is interactive)
    if sys.stdin.isatty():
        import getpass

        return getpass.getpass(f"{message} ")

    return None
