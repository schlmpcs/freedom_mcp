from freedom24_bot.security import is_allowed


def test_allowed_when_chat_matches():
    assert is_allowed(123, 123) is True


def test_denied_when_chat_differs():
    assert is_allowed(999, 123) is False


def test_denied_when_no_allowlist_configured():
    assert is_allowed(123, None) is False


def test_denied_when_chat_id_missing():
    assert is_allowed(None, 123) is False
