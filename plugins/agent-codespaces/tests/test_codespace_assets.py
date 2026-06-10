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
        assert "DEFAULT_RELAY_PORT=9857" in text


class TestProvisionCommand:
    def test_command_installs_both_helpers(self) -> None:
        cmd = build_provision_command()
        assert "$HOME/.local/bin/ado-auth-helper-relay" in cmd
        assert "$HOME/ado-auth-helper" in cmd
        assert "base64 -d" in cmd

    def test_command_backs_up_native_helper_once(self) -> None:
        cmd = build_provision_command()
        # Only back up when the existing helper isn't already ours
        assert "grep -q ado-auth-helper-relay" in cmd
        assert "$HOME/.ado-auth-helper-vscode" in cmd

    def test_embedded_payload_roundtrips(self) -> None:
        cmd = build_provision_command()
        # Extract base64 blobs and confirm they decode to the asset text
        blobs = re.findall(r"printf %s (\S+) \| base64 -d", cmd)
        assert len(blobs) == 2
        decoded = {base64.b64decode(b).decode("utf-8") for b in blobs}
        assert asset_text("ado-auth-helper-relay") in decoded
        assert asset_text("ado-auth-helper-wrapper") in decoded
