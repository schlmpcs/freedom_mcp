"""Read-only smoke test against the live Freedom24/Tradernet API.

Loads credentials from .env (same as the server), then calls a few READ-ONLY
tools and prints the results. Places NO orders. Run this to confirm auth and
command names work before wiring the server into Claude Code.

Usage:
    .venv\\Scripts\\python.exe test_live.py            # default checks
    .venv\\Scripts\\python.exe test_live.py AAPL.US     # also quote a ticker
"""

import sys

import freedom24_mcp as srv


def show(label: str, result: str) -> None:
    print(f"\n===== {label} =====")
    print(result[:4000])


def main() -> None:
    if not srv.CONFIG.has_any_auth:
        print("No credentials found. Fill in .env (FREEDOM24_PUB_KEY/PRIV_KEY or "
              "FREEDOM24_LOGIN/PASSWORD) and try again.")
        sys.exit(1)

    print(f"Auth configured: {'API key' if srv.CONFIG.has_api_key else 'login/password'}")
    print(f"API URL: {srv.CONFIG.api_url}")

    # 1) Session / auth check
    show("get_session_info", srv.get_session_info())

    # 2) A quote (override ticker via CLI arg)
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL.US"
    show(f"get_quote({ticker})", srv.get_quote(ticker))

    # 3) Portfolio
    show("get_portfolio", srv.get_portfolio())

    print("\nDone. If any block shows an \"error\", read the message: an 'unknown "
          "command' means adjust that name in client.py COMMANDS; a signature/auth "
          "error means re-check your keys.")


if __name__ == "__main__":
    main()
