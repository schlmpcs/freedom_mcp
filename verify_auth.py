"""Verify the patched auth.build_signed_request against the LIVE API.

Empty-params commands must still work (no regression); nested-params commands
must now pass the signature check (the fix).
"""
from __future__ import annotations

import os
import requests
from dotenv import load_dotenv

from auth import build_signed_request

load_dotenv(override=False)
PUB = os.environ["FREEDOM24_PUB_KEY"].strip()
PRIV = os.environ["FREEDOM24_PRIV_KEY"].strip()
BASE = (os.environ.get("FREEDOM24_API_URL") or "https://freedom24.com/api").strip().rstrip("/")


def call(cmd, params):
    body, headers = build_signed_request(PUB, PRIV, cmd, params)
    r = requests.post(f"{BASE}/v2/cmd/{cmd}", data=body, headers=headers, timeout=15)
    bad = "Invalid signature" in r.text
    print(f"[{'FAIL-SIG' if bad else 'sig-ok '}] {cmd}({params}) -> {r.text[:130]}")


print("== empty-params (regression check) ==")
call("getPositionJson", {})
call("getUserInfo", {})
print("== nested-params (the fix) ==")
call("getTradesHistory", {"date_from": "2026-04-01", "date_to": "2026-05-25"})
