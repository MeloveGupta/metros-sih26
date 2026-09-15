"""Tests for the Claude LLM client's credential handling.

The LLM path must authenticate with ANTHROPIC_API_KEY only -- no Claude Code
OAuth token, no keychain, no bare `anthropic.Anthropic()` fallback.
"""
from __future__ import annotations

import pytest

import backend.extract.llm as llm_mod
from backend.core.errors import ExtractionError


def test_llm_available_false_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert llm_mod.llm_available() is False


def test_llm_available_true_with_api_key(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    assert llm_mod.llm_available() is True


def test_client_raises_without_any_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(ExtractionError):
        llm_mod._client()


def test_client_ignores_oauth_token_env(monkeypatch):
    """A leftover ANTHROPIC_AUTH_TOKEN must be ignored entirely -- API key only."""
    pytest.importorskip("anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-oat01-should-be-ignored")
    assert llm_mod._has_credentials() is False
    with pytest.raises(ExtractionError):
        llm_mod._client()


def test_declarations_block_states_consumer_care_is_fully_mandatory():
    from backend.rules.catalog import load_catalog

    catalog = load_catalog()
    block = llm_mod._declarations_block(catalog, ["consumer_care"])
    assert "telephone" in block and "e-mail" in block
    assert "mandatory" in block


def test_no_oauth_token_path_in_backend_or_scripts():
    """Belt-and-suspenders: the OAuth/keychain path must not exist in code."""
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    banned = re.compile(r"ANTHROPIC_AUTH_TOKEN|anthropic-beta|keychain|ant auth|GEMINI",
                        re.IGNORECASE)
    hits = []
    for sub in ("backend", "scripts"):
        for path in (repo_root / sub).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if banned.search(text):
                hits.append(str(path.relative_to(repo_root)))
    assert not hits, f"stale OAuth/keychain/Gemini references found in: {hits}"
