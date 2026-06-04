#!/usr/bin/env python3
"""
Garmin token health check.
Decodes the di_token JWT to check expiry and validates the session.
Exits with code 1 and prints an error if token is expired or invalid.

Usage: python garmin_token_health.py
Run from the staleness watchdog or a dedicated n8n schedule.
"""

import os
import sys
import json
import base64
import datetime
from dotenv import load_dotenv

load_dotenv()

TOKEN_DIR  = os.getenv("GARMINTOKENS", "/root/.garminconnect")
TOKEN_FILE = os.path.join(TOKEN_DIR, "garmin_tokens.json")
# Warn if token expires within this many hours
WARN_HOURS = 12


def decode_jwt_exp(token):
    """Extract exp claim from JWT without verifying signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.b64decode(payload))
        return data.get("exp")
    except Exception as e:
        raise RuntimeError(f"Failed to decode JWT: {e}")


def main():
    print(f"Checking Garmin token health...")
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
