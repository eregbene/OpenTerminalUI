"""Interactive, one-time cTrader Open API OAuth setup helper.

Run this from inside the backend container (it needs the `ctrader_open_api` package). Must be
run with `-m` (module mode), not as a direct file path -- a direct path only puts the script's own
directory on sys.path, not the /app repo root, so `from backend...` imports fail with
ModuleNotFoundError:

    docker exec -it openterminalui-backend-1 python -m backend.scripts.ctrader_oauth_setup

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
  2. You log into cTrader, select your DEMO account, and approve.
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


def _discover_ctid_trader_account_id(*, client_id: str, client_secret: str, access_token: str) -> int:
    """2026-08-27 real bug fix: this script used to print a hardcoded CTRADER_ACCOUNT_ID=10102160
    regardless of whose token was exchanged -- and that number is actually the account's
    traderLogin (the human-readable login shown in the cTrader UI), not the ctidTraderAccountId
    the API actually requires for CTRADER_ACCOUNT_ID (confirmed live: they are DIFFERENT numbers
    for the same account -- traderLogin=10102160, real ctidTraderAccountId=48382084). Queries
    ProtoOAGetAccountListByAccessTokenReq for real, so the printed value is always correct for
    whichever account was actually authorized, never a stale placeholder from someone else's
    setup run."""
    import asyncio

    from backend.brokers.ctrader.transport import CTraderTransport

    async def _query() -> int:
        from ctrader_open_api import EndPoints, Protobuf
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAApplicationAuthReq, ProtoOAGetAccountListByAccessTokenReq

        transport = CTraderTransport(host=EndPoints.PROTOBUF_DEMO_HOST, port=EndPoints.PROTOBUF_PORT)
        transport.start()
        deadline = asyncio.get_event_loop().time() + 15
        while not transport.is_connected:
            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError("cTrader transport did not connect within 15s")
            await asyncio.sleep(0.1)
        try:
            auth_req = ProtoOAApplicationAuthReq()
            auth_req.clientId = client_id
            auth_req.clientSecret = client_secret
            await transport.send(auth_req)

            list_req = ProtoOAGetAccountListByAccessTokenReq()
            list_req.accessToken = access_token
            response = Protobuf.extract(await transport.send(list_req))
            accounts = list(response.ctidTraderAccount)
            demo_accounts = [acc for acc in accounts if not acc.isLive]
            if not demo_accounts:
                raise RuntimeError(f"no DEMO account found on this token's account list ({len(accounts)} total, all live) -- refusing to guess")
            if len(demo_accounts) > 1:
                print(f"WARNING: {len(demo_accounts)} demo accounts on this token (traderLogins: {[a.traderLogin for a in demo_accounts]}) -- using the first one. Edit CTRADER_ACCOUNT_ID yourself if you meant a different one.", file=sys.stderr)
            chosen = demo_accounts[0]
            print(f"(discovered: traderLogin={chosen.traderLogin} -> ctidTraderAccountId={chosen.ctidTraderAccountId})")
            return chosen.ctidTraderAccountId
        finally:
            transport.stop()

    return asyncio.run(_query())


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

    account_id = _discover_ctid_trader_account_id(client_id=client_id, client_secret=client_secret, access_token=result.access_token)

    print("\nSuccess. Add these lines to your .env (never commit this file):\n")
    print(f"CTRADER_ENVIRONMENT=demo")
    print(f"CTRADER_CLIENT_ID={client_id}")
    print(f"CTRADER_CLIENT_SECRET={client_secret}")
    print(f"CTRADER_ACCESS_TOKEN={result.access_token}")
    print(f"CTRADER_REFRESH_TOKEN={result.refresh_token}")
    print(f"CTRADER_ACCOUNT_ID={account_id}")
    print(f"CTRADER_REDIRECT_URI={args.redirect_uri}")
    if result.expires_in:
        print(f"\n(access_token expires in {result.expires_in}s -- the adapter refreshes it automatically using the refresh_token; the refresh_token itself has no expiry but is single-use/rotating, so persist whatever new value a refresh returns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
