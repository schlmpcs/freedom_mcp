from freedom24_bot.state import load_seen, save_seen


def test_load_missing_file_returns_empty_set(tmp_path):
    assert load_seen(str(tmp_path / "nope.json")) == set()


def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    save_seen(path, {1, 2, 3})
    assert load_seen(path) == {1, 2, 3}


def test_save_overwrites_previous(tmp_path):
    path = str(tmp_path / "state.json")
    save_seen(path, {1, 2})
    save_seen(path, {9})
    assert load_seen(path) == {9}


def test_load_corrupt_file_returns_empty_set(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json{{{")
    assert load_seen(str(path)) == set()
