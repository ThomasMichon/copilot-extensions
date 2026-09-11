"""Tests for the feed-neutrality guard (aperture-labs feed-neutral-build-config
effort follow-through; see #2389 / tools/clean-room's --block-public-feeds).

The guard flags a hardcoded public package-feed URL (pypi.org,
files.pythonhosted.org, registry.npmjs.org, download.pytorch.org) unless it
sits behind a real override mechanism (Dockerfile ARG default, shell
${VAR:-<url>} expansion, PowerShell ?? '<url>' default) or an inline
'# feed-guard: allow <reason>' escape hatch, matching this repo's established
guard convention.

Run:  python -m pytest tools/test_check_feed_neutrality.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "check-feed-neutrality.py"
_spec = importlib.util.spec_from_file_location("check_feed_neutrality", _SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_bare_dockerfile_pin_is_flagged():
    text = "RUN pip install --index-url https://download.pytorch.org/whl/cu128 torch\n"
    problems = guard.scan_text(text, rel="Dockerfile")
    assert len(problems) == 1
    assert "download.pytorch.org" in problems[0]


def test_dockerfile_arg_default_is_exempt():
    text = (
        "ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128\n"
        'RUN pip install --index-url "${TORCH_INDEX_URL}" torch\n'
    )
    assert guard.scan_text(text, rel="Dockerfile") == []


def test_quoted_dockerfile_arg_default_is_exempt():
    text = 'ARG TORCH_INDEX_URL="https://download.pytorch.org/whl/cu128"\n'
    assert guard.scan_text(text, rel="Dockerfile") == []


def test_shell_default_expansion_is_exempt():
    text = 'TORCH_INDEX="${TORCH_INDEX_CU118:-https://download.pytorch.org/whl/cu118}"\n'
    assert guard.scan_text(text, rel="install.sh") == []


def test_bare_shell_assignment_is_flagged():
    text = 'TORCH_INDEX="https://download.pytorch.org/whl/cu118"\n'
    problems = guard.scan_text(text, rel="install.sh")
    assert len(problems) == 1


def test_powershell_coalesce_default_is_exempt():
    text = "$desired = $configuredIndexUrl ?? 'https://pypi.org/simple'\n"
    assert guard.scan_text(text, rel="install.ps1") == []


def test_comment_line_is_exempt():
    text = "# Matches download.pytorch.org/whl/cu128 for parity with the Dockerfile\n"
    assert guard.scan_text(text, rel="install.sh") == []


def test_inline_allow_comment_is_exempt():
    text = (
        'env["NPM_CONFIG_REGISTRY"] = "https://registry.npmjs.org/"  '
        "# feed-guard: allow deliberate public-npm repair path\n"
    )
    assert guard.scan_text(text, rel="foo.sh") == []


def test_inline_allow_comment_requires_a_reason():
    text = 'env["NPM_CONFIG_REGISTRY"] = "https://registry.npmjs.org/"  # feed-guard: allow\n'
    assert len(guard.scan_text(text, rel="foo.sh")) == 1


def test_allow_comment_not_shadowed_by_an_in_url_hash():
    # A pip VCS/egg-fragment URL contains its own '#' before the real
    # trailing comment marker; naively taking the FIRST '#' would miss the
    # allow-comment entirely.
    text = (
        'RUN pip install "https://pypi.org/simple#egg=x"  '
        "# feed-guard: allow legacy egg url\n"
    )
    assert guard.scan_text(text, rel="Dockerfile") == []


def test_non_public_host_is_not_flagged():
    text = "RUN pip install --index-url https://internal-feed.example.com/simple torch\n"
    assert guard.scan_text(text, rel="Dockerfile") == []


def test_bare_pin_not_masked_by_colocated_legit_override():
    text = (
        "RUN pip install --index-url ${A:-https://pypi.org/simple} "
        "--extra-index-url https://registry.npmjs.org/\n"
    )
    problems = guard.scan_text(text, rel="Dockerfile")
    assert len(problems) == 1
    assert "registry.npmjs.org" in problems[0]


def test_verify_scans_real_repo_tree_with_zero_findings():
    # Regression proof: run the guard against this repo's actual tracked
    # tree (the module's own REPO constant, not a fixture) and confirm no
    # hardcoded public-feed pin currently exists uncovered.
    problems = guard.verify()
    assert problems == [], "\n".join(problems)
