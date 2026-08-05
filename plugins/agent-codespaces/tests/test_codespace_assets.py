"""Tests for CodeSpace-side relay helper assets and provisioning."""

from __future__ import annotations

import base64
import gzip
import re

from agent_codespaces.codespace_assets import (
    AUTH_ERROR_POLICY_INSTRUCTIONS_ROOT,
    AUTH_ERROR_POLICY_REMOTE_PATH,
    asset_text,
    build_auth_error_policy_command,
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

    def test_auth_error_policy_contains_mandatory_rules(self) -> None:
        text = asset_text("auth-error-policy.instructions.md")
        assert "\r" not in text
        assert "Stop immediately" in text
        assert "Report the exact error" in text
        assert "Never run `git push --no-verify`" in text
        assert "Do not run `az login`" in text
        assert "device-code login" in text
        assert "`gh auth login`" in text
        assert "credential relay is" in text and "host-owned" in text
        assert "stdio/ACP channel" in text


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

    def test_command_falls_back_when_pinned_node_is_stale(self) -> None:
        """dotfiles #733: the backed-up shebang can point at a since-deleted
        /vscode/bin/<hash>/node after a VS Code server build rotation. The
        provision must validate the interpreter exists and fall back to
        env-node, rather than re-applying a dangling shebang that fails with
        `bad interpreter`."""
        cmd = build_provision_command()
        # Interpreter is extracted from the shebang...
        assert '_interp=' in cmd
        # ...and only kept when it is actually executable; otherwise env-node.
        assert '[ -x "$_interp" ] || _sb="#!/usr/bin/env node"' in cmd

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
        # Extract chunked gzip+base64 blobs and confirm they decode to the
        # original asset text. There are four: the relay client, the wrapper,
        # stale-npm-token scrub, and the #18 profile.d snippet.
        decoded = set(_decoded_chunked_payloads(cmd))
        assert len(decoded) == 4
        assert asset_text("ado-auth-helper-relay") in decoded
        assert asset_text("ado-auth-helper-wrapper") in decoded
        assert any(".npmrc" in d and "_authtoken" in d for d in decoded)
        # One blob is the login-shell git hardening export.
        assert any("GIT_TERMINAL_PROMPT=0" in d for d in decoded)

    def test_payload_transport_is_chunked_under_windows_argv_limits(self) -> None:
        """No generated line/token should reintroduce the WinError 206 connect
        failure caused by one oversized SSH command argument."""
        cmd = build_provision_command()
        assert len(cmd) < 30_000
        assert max(len(line) for line in cmd.splitlines()) < 8_000
        assert max(len(c) for c in re.findall(r"printf %s '([^']+)'", cmd)) < 8_000


class TestAuthErrorPolicyCommand:
    def test_command_deploys_policy_to_custom_instructions_root(self) -> None:
        cmd = build_auth_error_policy_command()
        assert AUTH_ERROR_POLICY_INSTRUCTIONS_ROOT in cmd
        assert AUTH_ERROR_POLICY_REMOTE_PATH in cmd
        assert 'mkdir -p "$(dirname "' in cmd
        assert "auth-error-policy.instructions.md" in cmd
        assert "COPILOT_CUSTOM_INSTRUCTIONS_DIRS" in cmd
        assert "|| true" in cmd  # best-effort: instruction write never blocks connect

    def test_command_embeds_policy_payload(self) -> None:
        cmd = build_auth_error_policy_command()
        decoded = _decoded_chunked_payloads(cmd)
        assert decoded == [asset_text("auth-error-policy.instructions.md")]

    def test_command_transport_stays_small_and_chunked(self) -> None:
        cmd = build_auth_error_policy_command()
        assert max(len(line) for line in cmd.splitlines()) < 8_000
        assert max(len(c) for c in re.findall(r"printf %s '([^']+)'", cmd)) < 8_000


def _decoded_chunked_payloads(cmd: str) -> list[str]:
    payloads = []
    block_re = re.compile(
        r'_f="\$HOME/[^"]+"; : > "\$_f";\n(?P<body>.*?)\nbase64 -d "\$_f"',
        re.S,
    )
    for block in block_re.finditer(cmd):
        chunks = re.findall(r"printf %s '([^']+)' >> \"\$_f\"", block.group("body"))
        raw = base64.b64decode("".join(chunks))
        payloads.append(gzip.decompress(raw).decode("utf-8"))
    return payloads
