from freedom24_bot.alerts import is_triggered, detect_new_fires


def _alert(id_, triggered):
    return {"id": id_, "ticker": "AAPL.US", "trigger_type": "crossing",
            "trigger_price": '{"price":"250"}', "triggered": triggered}


def test_is_triggered_truthy_values():
    assert is_triggered(_alert(1, "1")) is True
    assert is_triggered(_alert(1, 1)) is True


def test_is_triggered_falsey_values():
    assert is_triggered(_alert(1, "")) is False
    assert is_triggered(_alert(1, "0")) is False
    assert is_triggered(_alert(1, None)) is False


def test_new_fire_emits_message_and_records_id():
    msgs, seen = detect_new_fires([_alert(76, "1")], set())
    assert len(msgs) == 1 and "AAPL.US" in msgs[0]
    assert seen == {76}


def test_already_seen_fire_not_re_emitted():
    msgs, seen = detect_new_fires([_alert(76, "1")], {76})
    assert msgs == []
    assert seen == {76}


def test_reset_alert_drops_from_seen_enabling_refire():
    # Alert no longer triggered -> drops out of seen.
    msgs, seen = detect_new_fires([_alert(76, "0")], {76})
    assert msgs == []
    assert seen == set()
    # Next poll it fires again -> emitted.
    msgs2, seen2 = detect_new_fires([_alert(76, "1")], seen)
    assert len(msgs2) == 1
    assert seen2 == {76}


def test_ignores_error_rows():
    msgs, seen = detect_new_fires([{"error": "Instrument not found"}], set())
    assert msgs == [] and seen == set()
