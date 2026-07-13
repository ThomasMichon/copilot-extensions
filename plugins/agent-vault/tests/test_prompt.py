"""Tests for the master-password prompt title resolution."""

from __future__ import annotations

import pytest

from agent_vault import prompt


@pytest.fixture(autouse=True)
def clear_title_env(monkeypatch):
    monkeypatch.delenv(prompt.PROMPT_TITLE_ENV, raising=False)


def test_default_title():
    assert prompt._resolve_title(None) == prompt.PROMPT_TITLE


def test_explicit_title_wins(monkeypatch):
    monkeypatch.setenv(prompt.PROMPT_TITLE_ENV, "From Env")
    assert prompt._resolve_title("Explicit") == "Explicit"


def test_env_title_used_when_no_arg(monkeypatch):
    monkeypatch.setenv(prompt.PROMPT_TITLE_ENV, "Aperture Science Vault")
    assert prompt._resolve_title(None) == "Aperture Science Vault"


def test_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(prompt.PROMPT_TITLE_ENV, "")
    assert prompt._resolve_title(None) == prompt.PROMPT_TITLE
