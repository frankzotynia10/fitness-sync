#!/usr/bin/env python3
"""
Garmin token health check.
Decodes the di_token JWT to check expiry and validates the session.
Exits with code 1 and prints an error if token is expired or invalid.

Usage: python garmin_token_health.py
Run from the staleness watchdog or a dedicated n8n schedule.
"""

import os
import re
import sys
import json
import time
import base64
import datetime
from dotenv import load_dotenv

load_dotenv()

TOKEN_DIR  = os.getenv("GARMINTOKENS", "/root/.garminconnect")
TOKEN_FILE = os.path.join(TOKEN_DIR, "garmin_tokens.json")
# Warn if token expires within this many hours
WARN_HOURS = 12

# Personal API check: retry transient failures, fail hard only on a 4xx
PERSONAL_API_ATTEMPTS = 2
PERSONAL_API_RETRY_SLEEP = 10


def decode_jwt_exp(token):
    """Extract exp claim from JWT without verifying signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.b64decode(payload))
        return data.get("exp")
    except Exception as e:
        raise RuntimeError(f"Failed to decode JWT: {e}")


def check_personal_api():
    """In official mode, a rejected/revoked Personal API key would silently drop the
    sync back to garminconnect, so treat that as a health failure.

    Timeouts, 5xx and network errors are transient: retry, and if the API is still
    unreachable print a WARNING (no 'ERROR' in output) instead of failing, since the
    sync falls back to garminconnect on its own."""
    if os.getenv("GARMIN_SOURCE", "connect").strip().lower() != "official":
        return
    last = None
    for attempt in range(1, PERSONAL_API_ATTEMPTS + 1):
        try:
            from services.official import OfficialClient
            OfficialClient()
            print("Personal API key: OK")
            return
        except Exception as e:
            last = e
            msg = str(e)
            m = re.search(r"HTTP (4\d\d)", msg)
            key_rejected = ("is not set" in msg) or (m and m.group(1) != "429")
            if key_rejected:
                print(f"ERROR: Personal API key rejected: {msg}")
                print("Regenerate at connect.garmin.com > Settings > Personal API Key, update .env, recreate the container.")
                sys.exit(1)
            print(f"Personal API attempt {attempt}/{PERSONAL_API_ATTEMPTS} failed (transient): {msg}")
            if attempt < PERSONAL_API_ATTEMPTS:
                time.sleep(PERSONAL_API_RETRY_SLEEP)
    print(f"WARNING: Personal API unreachable after {PERSONAL_API_ATTEMPTS} attempts: {last}. "
          "Not treating as a key failure; sync falls back to garminconnect.")


def main():
    print(f"Checking Garmin token health...")
    check_personal_api()
    print(f"Token dir: {TOKEN_DIR}")

    # Check file exists
    if not os.path.exists(TOKEN_FILE):
        print(f"ERROR: Token file not found: {TOKEN_FILE}")
        print("Run login_once.py to re-authenticate.")
        sys.exit(1)

    with open(TOKEN_FILE) as f:
        tokens = json.load(f)

    di_token         = tokens.get("di_token")
    di_refresh_token = tokens.get("di_refresh_token")

    if not di_token:
        print("ERROR: di_token missing from token file.")
        sys.exit(1)

    if not di_refresh_token:
        print("ERROR: di_refresh_token missing from token file.")
        sys.exit(1)

    # Decode expiry
    try:
        exp = decode_jwt_exp(di_token)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    now        = datetime.datetime.now()
    exp_dt     = datetime.datetime.fromtimestamp(exp)
    hours_left = (exp_dt - now).total_seconds() / 3600

    print(f"Token expires: {exp_dt} ({hours_left:.1f} hours from now)")

    if hours_left <= 0:
        print("ERROR: Garmin di_token is EXPIRED. Run login_once.py to re-authenticate.")
        sys.exit(1)

    if hours_left <= WARN_HOURS:
        print(f"WARNING: Token expires in {hours_left:.1f}h — should auto-refresh on next sync.")
        # Don't exit 1 here — garminconnect will refresh it on next login() call

    # Validate session actually works
    try:
        from garminconnect import Garmin
        client = Garmin()
        client.login(tokenstore=TOKEN_DIR)
        name = client.get_full_name()
        print(f"Session valid — authenticated as: {name}")
        print("Garmin token health: OK")
    except Exception as e:
        print(f"ERROR: Session validation failed: {e}")
        print("Token may be expired or revoked. Run login_once.py to re-authenticate.")
        sys.exit(1)


if __name__ == "__main__":
    main()
