"""Interactive, one-time cTrader Open API OAuth setup helper.

Run this from inside the backend container (it needs the `ctrader_open_api` package):

    docker exec -it openterminalui-backend-1 python backend/scripts/ctrader_oauth_setup.py

Before running, you need a registered cTrader Open API application (free, takes a few minutes):
  1. Go to https://openapi.ctrader.com/apps and log in with your cTrader ID.
  2. Register a new application -- name/description are your choice.
  3. Set its Redirect URI to  http://localhost:5000/ctrader/oauth/callback
     (or your own value -- just pass the same one to this script via --redirect-uri).
  4. Copy the app's Client ID and Client Secret -- this script asks for them (never hardcode
     them anywhere in the repo; this script does not write them to any file itself).

This script:
  1. Builds the authorization URL (scope=accounts -- READ-ONLY, matching Phase 2's safety
     requirement) and asks you to open it in a browser and approve access.
  2. You log into cTrader, select your DEMO account (10102160), and approve.
  3. cTrader redirects your browser to the redirect URI with a `?code=...` query parameter --
     paste the FULL redirected URL (or just the code) back into this script.
  4. Exchanges the code for an access_token + refresh_token and prints the exact .env lines to
     add -- this script never writes your .env file for you; you copy/paste them yourself so you
     stay in control of what gets persisted where.
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import parse_qs, urlparse

from backend.brokers.ctrader.oauth import build_authorization_url, exchange_authorization_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--redirect-uri", default="http://localhost:5000/ctrader/oauth/callback")
    parser.add_argument("--scope", default="accounts", choices=["accounts", "trading"], help="accounts=read-only (default, required for this phase); trading=full access, do not use yet")
    args = parser.parse_args()

    if args.scope != "accounts":
        print("WARNING: you selected scope=trading. Phase 2 is read-only-only; scope=accounts is strongly recommended.", file=sys.stderr)

    client_id = input("cTrader app Client ID: ").strip()
    client_secret = input("cTrader app Client Secret: ").strip()
    if not client_id or not client_secret:
        print("Client ID and Client Secret are required.", file=sys.stderr)
        return 1

    url = build_authorization_url(client_id=client_id, client_secret=client_secret, redirect_uri=args.redirect_uri, scope=args.scope)
    print("\nOpen this URL in a browser, log in, select your DEMO account, and approve access:\n")
    print(url)
    print()

    pasted = input("Paste the FULL redirected URL (or just the `code` value) here: ").strip()
    auth_code = pasted
    if pasted.startswith("http"):
        query = parse_qs(urlparse(pasted).query)
        codes = query.get("code")
        if not codes:
            print("Could not find a `code` parameter in that URL.", file=sys.stderr)
            return 1
        auth_code = codes[0]

    result = exchange_authorization_code(client_id=client_id, client_secret=client_secret, redirect_uri=args.redirect_uri, auth_code=auth_code)

    print("\nSuccess. Add these lines to your .env (never commit this file):\n")
    print(f"CTRADER_ENVIRONMENT=demo")
    print(f"CTRADER_CLIENT_ID={client_id}")
    print(f"CTRADER_CLIENT_SECRET={client_secret}")
    print(f"CTRADER_ACCESS_TOKEN={result.access_token}")
    print(f"CTRADER_REFRESH_TOKEN={result.refresh_token}")
    print(f"CTRADER_ACCOUNT_ID=10102160")
    print(f"CTRADER_REDIRECT_URI={args.redirect_uri}")
    if result.expires_in:
        print(f"\n(access_token expires in {result.expires_in}s -- the adapter refreshes it automatically using the refresh_token; the refresh_token itself has no expiry but is single-use/rotating, so persist whatever new value a refresh returns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
