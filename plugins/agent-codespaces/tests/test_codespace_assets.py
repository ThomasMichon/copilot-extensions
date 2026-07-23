"""Tests for CodeSpace-side relay helper assets and provisioning."""

from __future__ import annotations

import base64
import re

from agent_codespaces.codespace_assets import (
    asset_text,
    build_provision_command,
)


class TestAssets:
    def test_relay_client_present_and_lf(self) -> None:
        text = asset_text("ado-auth-helper-relay")
        assert "get-access-token" in text
        assert "\r" not in text  # must be LF for Linux
        assert "DEFAULT_RELAY_PORT=9857" in text

    def test_wrapper_present_and_lf(self) -> None:
        text = asset_text("ado-auth-helper-wrapper")
        assert "ado-auth-helper-relay" in text
        assert "\r" not in text
        assert "9857" in text

    def test_wrapper_requires_real_helper(self) -> None:
        """The fallback must require() the real extension helper, not a static
        backup, so VS Code auth survives extension updates."""
        text = asset_text("ado-auth-helper-wrapper")
        assert "require(real)" in text
        assert "auth-helper.js" in text
        assert "ms-codespaces-tools.ado-codespaces-auth" in text

    def test_wrapper_waits_instead_of_hard_failing(self) -> None:
        """When neither relay nor VS Code helper is ready, the wrapper must
        BLOCK (bounded poll) instead of exiting immediately -- otherwise
        single-shot callers (setup-agency / external-git) fall through to an
        interactive git prompt that hangs postStart."""
        text = asset_text("ado-auth-helper-wrapper")
        assert "WAIT_DEADLINE_MS" in text
        assert "sleepMs" in text
        # Polls in a loop until the deadline rather than one-shot fail.
        assert "Date.now() >= deadline" in text

    def test_wrapper_fails_quietly_to_avoid_git_prompt(self) -> None:
        """On timeout, a git-credential `get` must emit quit=1 so git stops
        instead of prompting for a username/password (which hangs headless)."""
        text = asset_text("ado-auth-helper-wrapper")
        assert "quit=1" in text
        assert 'action === "get"' in text


class TestProvisionCommand:
    def test_command_installs_both_helpers(self) -> None:
        cmd = build_provision_command()
        assert "$HOME/.local/bin/ado-auth-helper-relay" in cmd
        assert "base64 -d" in cmd
        # Installed for both ado and azure auth helpers via the loop
        assert "ado-auth-helper azure-auth-helper" in cmd
        assert '"$HOME/$_n"' in cmd

    def test_command_preserves_node_shebang(self) -> None:
        cmd = build_provision_command()
        # Detect and reuse the extension's node shebang; fall back to env node
        assert "head -1" in cmd
        assert "#!/usr/bin/env node" in cmd

    def test_command_backs_up_native_helper_once(self) -> None:
        cmd = build_provision_command()
        # Only back up when the existing helper isn't already ours
        assert "grep -q ado-auth-helper-relay" in cmd
        assert '"$HOME/.$_n-vscode"' in cmd

    def test_pins_relay_credential_helper_for_ado_and_github(self) -> None:
        """#133/#112/#159: git's per-host credential.<host>.helper must be
        pinned to the relay-first ~/ado-auth-helper for the ADO hosts and
        github.com, with a leading empty reset so it overrides the native
        broker/codespace-token helpers, so headless `git push` works."""
        cmd = build_provision_command()
        for host in (
            "https://your-org.visualstudio.com",
            "https://dev.azure.com",
            "https://github.com",
        ):
            assert host in cmd
        # The pin points at the relay-first wrapper...
        assert 'git config --global --add "credential.${_h}.helper" "$HOME/ado-auth-helper"' in cmd
        # ...preceded by an empty reset so lower-priority helpers don't win.
        assert 'git config --global --add "credential.${_h}.helper" ""' in cmd

    def test_embedded_payload_roundtrips(self) -> None:
        cmd = build_provision_command()
        # Extract base64 blobs and confirm they decode to the asset text. There
        # are three: the relay client, the wrapper, and the #18 profile.d
        # snippet (piped via `sudo tee` rather than `> file`).
        blobs = re.findall(r"printf %s (\S+) \| base64 -d", cmd)
        assert len(blobs) == 3
        decoded = {base64.b64decode(b).decode("utf-8") for b in blobs}
        assert asset_text("ado-auth-helper-relay") in decoded
        assert asset_text("ado-auth-helper-wrapper") in decoded
        # The third blob is the login-shell git hardening export.
        assert any("GIT_TERMINAL_PROMPT=0" in d for d in decoded)
