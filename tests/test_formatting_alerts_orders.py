from freedom24_bot.formatting import (
    format_alert_fire, extract_alerts, format_alerts_list, extract_orders, format_orders,
)

# getAlertsList row. trigger_price is a JSON STRING.
ALERT = {
    "id": 76, "ticker": "AAPL.US", "trigger_type": "crossing",
    "trigger_price": '{"price":"250"}', "quote_type": "ltp", "triggered": "1",
}
ALERTS_PAYLOAD = {"alerts": [ALERT]}


def test_format_alert_fire_mentions_ticker_type_and_price():
    out = format_alert_fire(ALERT)
    assert "AAPL.US" in out
    assert "crossing" in out
    assert "250" in out


def test_format_alert_fire_survives_bad_trigger_price():
    out = format_alert_fire({"id": 1, "ticker": "X.US", "trigger_price": "garbage"})
    assert "X.US" in out


def test_extract_alerts_returns_rows():
    assert extract_alerts(ALERTS_PAYLOAD)[0]["id"] == 76


def test_format_alerts_list_renders_rows():
    out = format_alerts_list(ALERTS_PAYLOAD)
    assert "AAPL.US" in out


def test_format_alerts_list_empty():
    assert "No alerts" in format_alerts_list({"alerts": []})


def test_format_orders_empty():
    assert "No active orders" in format_orders({"orders": []})


# Real getNotifyOrderJson shape, confirmed by the live spike: result.orders.order[]
ACTIVE_ORDERS = {"result": {"orders": {"key": "x", "order": [
    {"instr": "AAPL.US", "oper": 1, "q": 100, "p": 250},
]}}}


def test_extract_orders_real_nesting():
    out = extract_orders(ACTIVE_ORDERS)
    assert len(out) == 1 and out[0]["instr"] == "AAPL.US"


def test_format_orders_renders_real_nesting():
    assert "AAPL.US" in format_orders(ACTIVE_ORDERS)
