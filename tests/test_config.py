import os

import pytest

from freedom24_core.config import load_config


def test_mcp_transport_defaults_to_stdio(monkeypatch):
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    config = load_config()
    assert config.mcp_transport == "stdio"


def test_mcp_host_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("MCP_HOST", raising=False)
    config = load_config()
    assert config.mcp_host == "127.0.0.1"


def test_mcp_port_defaults_to_8000(monkeypatch):
    monkeypatch.delenv("MCP_PORT", raising=False)
    config = load_config()
    assert config.mcp_port == 8000


def test_mcp_bearer_token_none_by_default(monkeypatch):
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    config = load_config()
    assert config.mcp_bearer_token is None


def test_mcp_bearer_token_read_from_env(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "super-secret-token")
    config = load_config()
    assert config.mcp_bearer_token == "super-secret-token"


def test_mcp_transport_read_from_env(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    config = load_config()
    assert config.mcp_transport == "streamable-http"


def test_mcp_port_read_from_env(monkeypatch):
    monkeypatch.setenv("MCP_PORT", "9000")
    config = load_config()
    assert config.mcp_port == 9000


def test_mcp_port_bad_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MCP_PORT", "not-a-number")
    config = load_config()
    assert config.mcp_port == 8000
