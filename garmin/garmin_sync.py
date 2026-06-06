"""Garmin sync entrypoint — orchestration only."""
from __future__ import annotations

import datetime
import os
import sys

from services.client import build_client, save_tokens
from services.db import get_connection, get_existing_columns
from services.daily import sync_date
from services.ftp import sync_ftp

LOOKBACK_DAYS = int(os.environ.get("GARMIN_LOOKBACK_DAYS", "3"))


def main() -> None:
    today = datetime.date.today()
    print(f"Syncing last {LOOKBACK_DAYS} days (today + {LOOKBACK_DAYS - 1} prior)...")

    try:
        client = build_client()

        conn = get_connection()
        print("Connected to Postgres.")

        with conn.cursor() as cur:
            existing_cols = get_existing_columns(cur, "garmin_daily", "public")

        dates_to_sync = [today - datetime.timedelta(days=i) for i in range(LOOKBACK_DAYS)]
        for target_date in dates_to_sync:
            try:
                sync_date(client, target_date, conn, existing_cols)
            except Exception as e:
                print(f"Failed to sync {target_date.isoformat()}: {e}", file=sys.stderr)

        sync_ftp(client, conn)
        save_tokens(client)

        conn.close()
        print("\nAll dates synced.")

    except Exception as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
    sys.exit(0)
