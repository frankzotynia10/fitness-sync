from __future__ import annotations

import os
import sys

from garminconnect import Garmin

TOKEN_DIR = os.environ.get("GARMINTOKENS", os.path.expanduser("~/.garminconnect"))


def validate_token_dir() -> None:
    if not os.path.isdir(TOKEN_DIR):
        raise RuntimeError(
            f"GARMIN TOKEN ERROR: Token directory does not exist: {TOKEN_DIR}. "
            "Re-run login_once.py to re-authenticate."
        )
    token_files = [f for f in os.listdir(TOKEN_DIR) if not f.startswith('.')]
    if not token_files:
        raise RuntimeError(
            f"GARMIN TOKEN ERROR: Token directory is empty: {TOKEN_DIR}. "
            "Re-run login_once.py to re-authenticate."
        )
    print(f"Token directory OK: {TOKEN_DIR} ({len(token_files)} file(s): {token_files})")


def build_client() -> Garmin:
    """Initialise Garmin client and login from stored tokens."""
    validate_token_dir()
    client = Garmin()
    client.login(tokenstore=TOKEN_DIR)
    validate_session(client)
    return client


def validate_session(client: Garmin) -> None:
    try:
        name = client.get_full_name()
        print(f"Garmin session valid — authenticated as: {name}")
    except Exception as e:
        raise RuntimeError(
            f"GARMIN TOKEN ERROR: Login succeeded but session is invalid: {e}. "
            "Token may be expired. Re-run login_once.py to re-authenticate."
        )


def save_tokens(client: Garmin) -> None:
    """Persist refreshed tokens back to disk so they don't expire prematurely."""
    try:
        if hasattr(client, 'garth'):
            client.garth.dump(TOKEN_DIR)
        elif hasattr(client, 'client'):
            client.client.dump(TOKEN_DIR)
        else:
            print("WARNING: Could not find token store attribute to save tokens.", file=sys.stderr)
            return
        print(f"Tokens saved to {TOKEN_DIR}")
    except Exception as e:
        print(f"WARNING: Failed to save tokens: {e}", file=sys.stderr)
