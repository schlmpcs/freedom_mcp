from freedom24_core import (
    COMMANDS,
    Config,
    TradernetClient,
    TradernetError,
    build_signed_request,
    load_config,
    setup_logging,
)


def test_commands_is_dict():
    assert isinstance(COMMANDS, dict)
    assert "portfolio" in COMMANDS


def test_config_defaults():
    # Config can be instantiated with no args
    c = Config()
    assert c.api_url == "https://freedom24.com/api"
    assert c.dry_run is False


def test_tradernet_client_requires_config():
    c = Config()
    client = TradernetClient(c)
    assert client.config is c


def test_freedom24_core_does_not_import_mcp():
    import importlib
    import sys

    # Remove any cached imports
    mods_before = set(sys.modules)
    import freedom24_core  # noqa: F401
    new_mods = set(sys.modules) - mods_before
    mcp_mods = [m for m in new_mods if "freedom24_mcp" in m]
    assert mcp_mods == [], f"freedom24_core pulled in MCP server: {mcp_mods}"
