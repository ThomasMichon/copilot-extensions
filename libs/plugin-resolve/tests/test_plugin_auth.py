"""Plugin maintenance uses Git credentials, not an unrelated inference login."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from plugin_resolve import plugin_auth

GITHUB = {"source": {"source": "github", "repo": "example/catalog"}}


@pytest.mark.parametrize("key", ["GH_TOKEN", "GITHUB_TOKEN"])
def test_normal_github_token_overrides_only_the_management_child(
    key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = {key: "repository-value", "COPILOT_GITHUB_TOKEN": "inference-value"}
    before = dict(original)
    monkeypatch.setattr(plugin_auth, "_git_token", lambda *args, **kwargs: None)
    child = plugin_auth.plugin_management_environment(GITHUB, original, tmp_path)
    assert child["COPILOT_GITHUB_TOKEN"] == child["GH_TOKEN"] == "repository-value"
    assert original == before


@pytest.mark.parametrize(
    ("definition", "expected_url"),
    [
        pytest.param(GITHUB, "https://github.com/example/catalog", id="github-source"),
        pytest.param(
            {"source": {"source": "git", "url": "https://github.com/example/catalog.git"}},
            "https://github.com/example/catalog.git",
            id="git-source",
        ),
    ],
)
def test_git_owner_uses_the_execution_checkout_and_exact_source(
    definition: dict[str, Any],
    expected_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = {
        "PATH": "selected-tools",
        "GH_TOKEN": "host-wide-value",
        "COPILOT_GITHUB_TOKEN": "inference-value",
        "GIT_DIR": "another-checkout",
        "GIT_WORK_TREE": "another-worktree",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "source-router",
    }
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(plugin_auth, "execution_executable", lambda *args: "selected-git")

    def capture(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(
            arguments,
            0,
            "protocol=https\nhost=github.com\nusername=source-user\npassword=git-owner-value\n\n",
            "",
        )

    monkeypatch.setattr(plugin_auth, "_credential_run", capture)
    child = plugin_auth.plugin_management_environment(definition, original, tmp_path)
    assert child["COPILOT_GITHUB_TOKEN"] == child["GH_TOKEN"] == "git-owner-value"
    arguments, options = calls[0]
    assert arguments == [
        "selected-git",
        "-c",
        "credential.interactive=false",
        "-c",
        "core.askPass=",
        "credential",
        "fill",
    ]
    assert options["cwd"] == tmp_path
    assert options["input"] == f"url={expected_url}\n\n"
    assert options["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert options["env"]["GCM_INTERACTIVE"] == "never"
    assert "GIT_DIR" not in options["env"]
    assert "GIT_WORK_TREE" not in options["env"]
    assert options["env"]["GIT_CONFIG_COUNT"] == "1"
    assert options["env"]["GIT_CONFIG_VALUE_0"] == "source-router"
    assert original["GIT_DIR"] == "another-checkout"
    assert original["COPILOT_GITHUB_TOKEN"] == "inference-value"


@pytest.mark.parametrize(
    "definition",
    [
        {"source": {"source": "directory", "path": "./plugins"}},
        {"source": {"source": "git", "url": "https://example.com/catalog.git"}},
        {"source": {"source": "git", "url": "http://github.com/example/catalog.git"}},
    ],
)
def test_other_sources_do_not_receive_a_github_override(
    definition: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = {"GH_TOKEN": "repository-value", "COPILOT_GITHUB_TOKEN": "inference-value"}
    monkeypatch.setattr(
        plugin_auth,
        "_git_token",
        lambda *args, **kwargs: pytest.fail("Unexpected credential lookup"),
    )
    assert plugin_auth.plugin_management_environment(definition, original, tmp_path) == original


def test_missing_credentials_preserve_existing_anonymous_or_copilot_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = {"COPILOT_GITHUB_TOKEN": "inference-value"}
    monkeypatch.setattr(plugin_auth, "_git_token", lambda *args, **kwargs: None)
    assert plugin_auth.plugin_management_environment(GITHUB, original, tmp_path) == original


def test_explicit_git_owner_refusal_is_not_an_ambient_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_auth, "execution_executable", lambda *args: "selected-git")
    monkeypatch.setattr(
        plugin_auth,
        "_credential_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            128,
            "",
            "fatal: credential helper told us to quit",
        ),
    )
    with pytest.raises(RuntimeError, match="credential owner refused"):
        plugin_auth.plugin_management_environment(
            GITHUB,
            {"COPILOT_GITHUB_TOKEN": "inference-value"},
            tmp_path,
        )


def test_enterprise_environment_token_is_not_used_for_a_public_host_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = {"GH_HOST": "git.example.com", "GH_TOKEN": "enterprise-value"}
    monkeypatch.setattr(plugin_auth, "execution_executable", lambda *args: "selected-git")

    def capture(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "GH_TOKEN" not in kwargs["env"]
        return subprocess.CompletedProcess(arguments, 0, "password=public-host-value\n\n", "")

    monkeypatch.setattr(plugin_auth, "_credential_run", capture)
    child = plugin_auth.plugin_management_environment(GITHUB, original, tmp_path)
    assert child["COPILOT_GITHUB_TOKEN"] == "public-host-value"
    assert original["GH_TOKEN"] == "enterprise-value"


@pytest.mark.parametrize("locale", ["de_DE.UTF-8", "fr_FR.UTF-8"])
def test_localized_host_refusal_does_not_fall_back_to_another_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locale: str,
) -> None:
    original = {
        "LC_ALL": locale, "LANG": locale, "LANGUAGE": locale,
        "GH_TOKEN": "ambient-account-value",
    }
    before = dict(original)
    monkeypatch.setattr(plugin_auth, "execution_executable", lambda *args: "selected-git")

    def capture(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        pinned = all(kwargs["env"][key] == "C" for key in ("LC_ALL", "LANG", "LANGUAGE"))
        diagnostic = "fatal: credential helper told us to quit" if pinned else "fatal: Abbruch"
        return subprocess.CompletedProcess(arguments, 128, "", diagnostic)

    monkeypatch.setattr(plugin_auth, "_credential_run", capture)
    with pytest.raises(RuntimeError, match="credential owner refused"):
        plugin_auth.plugin_management_environment(GITHUB, original, tmp_path)
    assert original == before


def test_credential_from_another_authority_is_not_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_auth, "execution_executable", lambda *args: "selected-git")
    monkeypatch.setattr(
        plugin_auth,
        "_credential_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            "host=git.example.com\npassword=other-host-value\n\n",
            "",
        ),
    )
    with pytest.raises(RuntimeError, match="different authority"):
        plugin_auth.plugin_management_environment(GITHUB, {}, tmp_path)


def test_credential_lookup_respects_the_remaining_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_auth, "execution_executable", lambda *args: "selected-git")

    def capture(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 0.25
        raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])

    monkeypatch.setattr(plugin_auth, "_credential_run", capture)
    assert plugin_auth.plugin_management_environment(GITHUB, {}, tmp_path, timeout=0.25) == {}
    with pytest.raises(TimeoutError, match="deadline expired"):
        plugin_auth.plugin_management_environment(GITHUB, {}, tmp_path, timeout=0)


@pytest.mark.parametrize("binary", [False, True])
def test_plugin_diagnostics_redact_the_exact_opaque_child_credential(binary: bool) -> None:
    token = "opaque-value-without-a-recognizable-prefix"
    message = "plugin failure: " + token
    environment = {"COPILOT_GITHUB_TOKEN": token}
    diagnostic = (
        plugin_auth.redact_plugin_credentials(message.encode(), environment) if binary
        else plugin_auth.redact_plugin_credentials(message, environment)
    )
    assert diagnostic == (b"plugin failure: [redacted]" if binary else "plugin failure: [redacted]")
