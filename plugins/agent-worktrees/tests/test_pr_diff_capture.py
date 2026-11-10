"""Actual provider process capture retains exact text and selected GitHub scope."""

import sys

import pytest

from agent_worktrees.pr_diff_report import diff_report
from agent_worktrees.providers import base, gitea, github


@pytest.mark.parametrize("provider_name", ["github", "gitea"])
@pytest.mark.parametrize("explicit", [False, True])
def test_actual_cli_capture_keeps_explicit_bytes_and_legacy_normalization(monkeypatch, provider_name, explicit):
    module = github if provider_name == "github" else gitea
    provider = github.GitHubProvider() if provider_name == "github" else gitea.GiteaProvider()
    text = "diff --git a/x b/x\r\n+--untrusted λ\r-old\r\n"
    calls = []

    def run(argv, **kwargs):
        assert argv[0] == ("gh" if provider_name == "github" else "curl")
        calls.append(argv)
        suffix = b"\n200" if provider_name == "gitea" else b""
        script = f"import sys; sys.stdout.buffer.write(sys.stdin.buffer.read() + {suffix!r})"
        return base.run_cli([sys.executable, "-c", script], input_text=text, **kwargs)

    monkeypatch.setattr(module, "run_cli", run)
    if explicit:
        report = diff_report(provider, "example/repository", 7,
                             api_base="https://target.example", token="synthetic-selected-token")
        assert report["supported"] is True and not report["error"]
        actual = report["diff"]
    else:
        result = provider.get_diff("example/repository", 7,
                                   api_base="https://target.example", token="synthetic-selected-token")
        assert result.supported is True and not result.error
        actual = result.diff
    assert actual == (text if explicit else text.replace("\r\n", "\n").replace("\r", "\n"))
    assert len(calls) == 1


@pytest.mark.parametrize("provider_name", ["github", "gitea"])
def test_invalid_utf8_is_not_a_successful_lossless_diff(monkeypatch, provider_name):
    module = github if provider_name == "github" else gitea
    provider = github.GitHubProvider() if provider_name == "github" else gitea.GiteaProvider()

    def run(argv, **kwargs):
        return base.run_cli(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\n200')"],
            **kwargs,
        )

    monkeypatch.setattr(module, "run_cli", run)
    report = diff_report(provider, "example/repository", 7,
                         api_base="https://target.example", token="synthetic-selected-token")
    assert report["error"] and report["complete"] is False and report["diff"] == ""


@pytest.mark.parametrize("endpoint", ["https://", "https:///path", " \t", "https://host.example:bad"])
def test_configured_hostless_or_invalid_endpoint_never_uses_ambient_host(monkeypatch, endpoint):
    monkeypatch.setenv("GH_HOST", "unrelated.example")
    monkeypatch.setattr(github, "run_cli", lambda *a, **k: pytest.fail("invalid target invoked gh"))
    result = github.GitHubProvider().get_diff("example/repository", 7, api_base=endpoint)
    assert result.supported is False and result.error == "GitHub diff endpoint is invalid"


def test_selected_enterprise_token_overrides_all_ambient_aliases_in_actual_process(monkeypatch):
    names = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
    for name in names:
        monkeypatch.setenv(name, "unrelated-synthetic-token")
    monkeypatch.setenv("GH_HOST", "unrelated.example")

    def run(argv, **kwargs):
        script = (
            "import os,sys; "
            f"assert all(os.environ[name] == 'selected-synthetic-token' for name in {names!r}); "
            "assert os.environ['GH_HOST'] == 'target.example'; "
            "sys.stdout.write('selected scope')"
        )
        return base.run_cli([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(github, "run_cli", run)
    report = diff_report(github.GitHubProvider(), "example/repository", 7,
                         api_base="https://target.example/api/v3", token="selected-synthetic-token")
    assert not report["error"] and report["diff"] == "selected scope"
