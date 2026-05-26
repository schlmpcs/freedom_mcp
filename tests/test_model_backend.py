"""Tests for the pluggable model backend (offline — no network)."""

import asyncio

import pytest

from agent.model_backend import complete_text, resolve_backend


def test_resolve_backend_defaults_to_api(monkeypatch):
    monkeypatch.delenv("AGENT_BACKEND", raising=False)
    assert resolve_backend() == "api"


def test_resolve_backend_reads_env(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND", "claude_code")
    assert resolve_backend() == "claude_code"


def test_resolve_backend_explicit_arg_wins(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND", "api")
    assert resolve_backend("claude_code") == "claude_code"


def test_resolve_backend_aliases():
    assert resolve_backend("max") == "claude_code"
    assert resolve_backend("agent_sdk") == "claude_code"
    assert resolve_backend("anthropic") == "api"


def test_resolve_backend_unknown_raises():
    with pytest.raises(ValueError):
        resolve_backend("gpt")


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(self._text)


class _FakeAnthropic:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def test_complete_text_api_backend_uses_injected_client():
    fake = _FakeAnthropic("hello from the model")
    out = asyncio.run(
        complete_text(
            "system rules",
            "user question",
            model="claude-opus-4-7",
            max_tokens=123,
            backend="api",
            anthropic_client=fake,
        )
    )
    assert out == "hello from the model"
    # system prompt and params are forwarded to the SDK call
    assert fake.messages.last_kwargs["system"] == "system rules"
    assert fake.messages.last_kwargs["model"] == "claude-opus-4-7"
    assert fake.messages.last_kwargs["max_tokens"] == 123


def test_complete_text_api_backend_omits_empty_system():
    fake = _FakeAnthropic("ok")
    asyncio.run(complete_text("", "judge this", model="m", backend="api", anthropic_client=fake))
    assert "system" not in fake.messages.last_kwargs


def test_complete_text_unknown_backend_raises():
    with pytest.raises(ValueError):
        asyncio.run(complete_text("s", "u", model="m", backend="nope"))
