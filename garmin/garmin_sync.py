"""Garmin sync entrypoint — orchestration only.

GARMIN_SOURCE=connect   (default) everything via garminconnect, as before.
GARMIN_SOURCE=official  Personal API first (GARMIN_PERSONAL_KEY); garminconnect fills
                        the gaps (training metrics, some intraday series, FTP, race
                        predictions). If garminconnect login fails, the official data
                        is still synced. If the official token fails, falls back to
                        connect mode.
GARMIN_TZ=Area/City     Timezone that defines "today" (e.g. America/New_York).
                        Default: the container clock (UTC).
GARMIN_DRY_RUN=1        Read-only: no DB writes or token saves. Recorded
                        writes go to GARMIN_DRY_RUN_OUT (JSON).
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

from services.client import build_client, save_tokens
from services.db import RecordingConnection, get_connection, get_existing_columns
from services.daily import sync_date
from services.ftp import sync_ftp
from services.official import OfficialClient

LOOKBACK_DAYS = int(os.environ.get("GARMIN_LOOKBACK_DAYS", "3"))
SOURCE = os.environ.get("GARMIN_SOURCE", "connect").strip().lower()
DRY_RUN = bool(os.environ.get("GARMIN_DRY_RUN"))
LOCAL_TZ = os.environ.get("GARMIN_TZ", "").strip()


def _clients():
    off = gc = None
    if SOURCE == "official":
        try:
            off = OfficialClient()
            print("Personal API token OK.")
        except Exception as e:
            print(f"WARNING: Personal API unavailable ({e}); falling back to garminconnect.", file=sys.stderr)
    try:
        gc = build_client()
    except Exception as e:
        if off is None:
            raise
        print(f"WARNING: garminconnect unavailable ({e}). Syncing official data only; "
              "training metrics, sleep/stress/steps/respiration intraday, FTP and race "
              "predictions are skipped this run.", file=sys.stderr)
    return gc, off


def main() -> None:
    today = datetime.datetime.now(ZoneInfo(LOCAL_TZ)).date() if LOCAL_TZ else datetime.date.today()
    print(f"Syncing last {LOOKBACK_DAYS} days (today + {LOOKBACK_DAYS - 1} prior), "
          f"source={SOURCE}{' [DRY RUN]' if DRY_RUN else ''}...")

    try:
        gc, off = _clients()

        conn = get_connection()
        print("Connected to Postgres.")
        if DRY_RUN:
            conn = RecordingConnection(conn)

        with conn.cursor() as cur:
            existing_cols = get_existing_columns(cur, "garmin_daily", "public")

        dates_to_sync = [today - datetime.timedelta(days=i) for i in range(LOOKBACK_DAYS)]
        for target_date in dates_to_sync:
            try:
                sync_date(gc, off, target_date, conn, existing_cols)
            except Exception as e:
                print(f"Failed to sync {target_date.isoformat()}: {e}", file=sys.stderr)

        if gc is not None:
            sync_ftp(gc, conn)
            if not DRY_RUN:
                save_tokens(gc)

        if DRY_RUN:
            out = os.environ.get("GARMIN_DRY_RUN_OUT", f"/tmp/garmin_dry_run_{SOURCE}.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(conn.writes, f, default=str, indent=1)
            print(f"\n[dry-run] {len(conn.writes)} writes recorded -> {out}")

        conn.close()
        print("\nAll dates synced.")

    except Exception as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
    sys.exit(0)
