def test_bot_package_imports():
    import freedom24_bot
    assert hasattr(freedom24_bot, "__version__")
