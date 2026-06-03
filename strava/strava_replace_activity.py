#!/usr/bin/env python3
"""
Download enhanced FIT from Garmin Connect and upload to Strava.
Does NOT delete the original — verify the new activity first, then delete manually.

Usage:
  python strava_replace_activity.py --garmin-id 579527833714 --strava-id 18755208377

Once verified:
  python strava_replace_activity.py --garmin-id 579527833714 --strava-id 18755208377 --delete-original
"""

import os
import sys
import zipfile
import io
import time
import json
import argparse
import requests
from dotenv import load_dotenv

# ── Env ───────────────────────────────────────────────────────────────────────
load_dotenv()

GARTH_HOME           = os.getenv("GARMINTOKENS", "/root/.garminconnect")
STRAVA_CLIENT_ID     = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_TOKENS_FILE   = os.environ["STRAVA_TOKENS_FILE"]


# ── Strava token helpers (shared pattern with strava_sync.py) ─────────────────
def load_tokens():
    with open(STRAVA_TOKENS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(token_data):
    tmp = STRAVA_TOKENS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    os.replace(tmp, STRAVA_TOKENS_FILE)


def get_access_token():
    tokens = load_tokens()
    if time.time() < tokens.get("expires_at", 0) - 60:
        return tokens["access_token"]
    print("  Strava token expired — refreshing...")
    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type":    "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }, timeout=30)
    resp.raise_for_status()
    new = resp.json()
    save_tokens({
        "access_token":  new["access_token"],
        "refresh_token": new["refresh_token"],
        "expires_at":    new["expires_at"],
    })
    print("  Token refreshed and saved")
    return new["access_token"]


# ── Step 1: Download FIT from Garmin ─────────────────────────────────────────
def download_fit(garmin_activity_id):
    print(f"\n[1/3] Downloading FIT for Garmin activity {garmin_activity_id}...")
    print(f"      Token dir: {GARTH_HOME}")

    try:
        import garth
        garth.resume(GARTH_HOME)
        print(f"      Authenticated as: {garth.client.username}")
    except Exception as e:
        print(f"      ERROR loading garth session: {e}")
        sys.exit(1)

    try:
        zip_bytes = garth.client.download(
            f"/download-service/files/activity/{garmin_activity_id}"
        )
        print(f"      Downloaded {len(zip_bytes)} bytes")
    except Exception as e:
        print(f"      ERROR downloading FIT: {e}")
        sys.exit(1)

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            fit_names = [n for n in z.namelist() if n.endswith(".fit")]
            if not fit_names:
                print(f"      ERROR: No .fit in zip. Contents: {z.namelist()}")
                sys.exit(1)
            fit_filename = fit_names[0]
            fit_data = z.read(fit_filename)
        print(f"      Extracted: {fit_filename} ({len(fit_data)} bytes)")
    except Exception as e:
        print(f"      ERROR extracting FIT: {e}")
        sys.exit(1)

    local_path = f"/tmp/{fit_filename}"
    with open(local_path, "wb") as f:
        f.write(fit_data)
    print(f"      Saved to: {local_path}")

    return fit_filename, fit_data


# ── Step 2: Upload FIT to Strava ──────────────────────────────────────────────
def upload_fit(fit_filename, fit_data, access_token):
    print(f"\n[2/3] Uploading FIT to Strava...")

    resp = requests.post(
        "https://www.strava.com/api/v3/uploads",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "data_type":  "fit",
            "name":       "Legs1",
            "sport_type": "WeightTraining",
            "trainer":    "1",
        },
        files={
            "file": (fit_filename, fit_data, "application/octet-stream"),
        },
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        print(f"      ERROR: Upload failed [{resp.status_code}]: {resp.text}")
        sys.exit(1)

    data = resp.json()
    upload_id = data.get("id")
    print(f"      Upload accepted. Upload ID: {upload_id} | Status: {data.get('status')}")
    return upload_id


# ── Step 3: Poll for completion ───────────────────────────────────────────────
def poll_upload(upload_id, access_token):
    print(f"\n[3/3] Polling upload status (up to 60s)...")

    for attempt in range(12):
        time.sleep(5)
        poll = requests.get(
            f"https://www.strava.com/api/v3/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        data            = poll.json()
        status          = data.get("status")
        error           = data.get("error")
        new_activity_id = data.get("activity_id")

        print(f"      [{attempt+1}] status={status} | error={error} | activity_id={new_activity_id}")

        if error:
            print(f"\n\u274c  Upload failed: {error}")
            sys.exit(1)

        if new_activity_id:
            return new_activity_id

    print("\n\u26a0\ufe0f  Timed out waiting for upload. Check Strava manually.")
    sys.exit(1)


# ── Step 4: Delete original (optional) ───────────────────────────────────────
def delete_activity(strava_activity_id, access_token):
    print(f"\nDeleting original Strava activity {strava_activity_id}...")
    resp = requests.delete(
        f"https://www.strava.com/api/v3/activities/{strava_activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if resp.status_code == 204:
        print(f"  \u2705 Deleted activity {strava_activity_id}")
    else:
        print(f"  ERROR: Delete failed [{resp.status_code}]: {resp.text}")
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Replace Strava activity with enhanced Garmin FIT")
    parser.add_argument("--garmin-id",       required=True, type=int, help="Garmin activity ID")
    parser.add_argument("--strava-id",       required=True, type=int, help="Strava activity ID to replace")
    parser.add_argument("--delete-original", action="store_true",     help="Delete original Strava activity after upload")
    args = parser.parse_args()

    access_token = get_access_token()

    fit_filename, fit_data = download_fit(args.garmin_id)
    upload_id              = upload_fit(fit_filename, fit_data, access_token)
    new_activity_id        = poll_upload(upload_id, access_token)

    print(f"\n\u2705  Upload complete!")
    print(f"    New Strava activity ID : {new_activity_id}")
    print(f"    View at               : https://www.strava.com/activities/{new_activity_id}")

    if args.delete_original:
        delete_activity(args.strava_id, access_token)
    else:
        print(f"\n    Original ({args.strava_id}) untouched.")
        print(f"    Re-run with --delete-original once you've verified the new activity looks right.")


if __name__ == "__main__":
    main()
