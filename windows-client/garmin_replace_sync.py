"""
garmin_replace_sync.py

Purpose: after an ONLINE Zwift ride, find the activity Zwift's native
Garmin Connect integration auto-uploaded, delete it, then upload a
device-spoofed copy of the local .fit file in its place -- so the ride
counts toward Garmin Challenges/badges without leaving a duplicate.

Fires automatically via the AHK "Zwift Online" hotkey after Zwift
closes -- nobody is watching this run, so it has to fail safe.

SAFETY (v2 -- after the 2026-09-20 garmin_backfill_replace.py incident
where the same delete-before-fake ordering deleted 4 real activities
with no replacement uploaded):
  - fit-file-faker runs FIRST, on the local file, before Garmin is
    touched at all, and its output is verified to exist. If it fails
    for any reason (including fit-file-faker not being found -- it's
    now invoked by hardcoded full path, not PATH lookup), the script
    bails immediately. Nothing is deleted from Garmin.
  - Only after the fake is confirmed does it poll Garmin for the
    Zwift-synced activity and delete it.
  - Delete retries with backoff and treats a "not found" response as
    success (handles Garmin returning a timeout on a delete that
    actually went through server-side).
  - Upload retries with backoff too -- this is the one moment where
    failure is genuinely bad, since the original is already gone by
    then.
  - If upload still fails after retries, an ntfy alert fires with the
    local file path so it's a quick manual repair, not a mystery.

Credentials: password is pulled from Windows Credential Manager via
`keyring`, never stored in this file. Set it once (see
garmin_pipeline_test.py's docstring), and run that test script
interactively at least once before wiring this into the hotkey --
that's the run that handles MFA and caches the session token this
script reads silently on every future run.

Cutoff for "new" files: reads the mtime of a marker file the AHK
"Zwift Online" hotkey drops right before launching Zwift
(C:\\scripts\\.zwift_online_marker). Falls back to "last hour" if the
marker is missing so this still works when run manually.

Requires (pip install):
    garminconnect
    fitparse
    keyring
"""

import datetime
import glob
import os
import subprocess
import sys
import time

import fitparse
import keyring
from garminconnect import Garmin

# ---- config ----------------------------------------------------------
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "frankzotynia10@gmail.com")
GARMIN_TOKENSTORE = r"C:\scripts\.garmin_tokens"

MARKER_FILE = r"C:\scripts\.zwift_online_marker"
ZWIFT_ACTIVITIES_DIR = r"C:\Users\frank\AppData\Local\Zwift\Activities"
FIT_FILE_FAKER_PROFILE = "zwift"

# hardcoded full path -- do not rely on PATH. This is what broke on
# 2026-09-20: PATH lookup silently failed and every fake step died
# with WinError 2, but only after the delete had already gone through.
FIT_FILE_FAKER_EXE = r"C:\Users\frank\AppData\Local\Python\pythoncore-3.14-64\Scripts\fit-file-faker.exe"

POLL_INTERVAL_SEC = 20
POLL_MAX_ATTEMPTS = 15        # ~5 minutes total before giving up
MATCH_TOLERANCE_SEC = 180     # how close Garmin's synced start time must be to the local file's

DELETE_RETRY_ATTEMPTS = 3
RETRY_WAIT_SEC = 5

NTFY_URL = "https://ntfy.sh/YOUR_TOPIC"  # reuse whatever topic your calendar-sync workflow already alerts to


def get_garmin_password() -> str:
    pw = keyring.get_password("garmin", GARMIN_EMAIL)
    if not pw:
        raise RuntimeError(
            "Garmin password not found in Windows Credential Manager. Run:\n"
            f'  py -c "import keyring; keyring.set_password(\'garmin\', \'{GARMIN_EMAIL}\', \'YOUR_PASSWORD\')"'
        )
    return pw


def notify(message: str) -> None:
    try:
        import urllib.request
        req = urllib.request.Request(NTFY_URL, data=message.encode("utf-8"))
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[notify] failed to send ntfy alert: {e}")


def get_cutoff_timestamp() -> float:
    if os.path.exists(MARKER_FILE):
        return os.path.getmtime(MARKER_FILE)
    print("[warn] no marker file found, falling back to 'anything in the last hour'")
    return time.time() - 3600


def newest_fit_file(after_timestamp: float) -> str | None:
    candidates = glob.glob(os.path.join(ZWIFT_ACTIVITIES_DIR, "*.fit"))
    candidates = [f for f in candidates if os.path.getmtime(f) > after_timestamp]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def get_fit_start_time(fit_path: str) -> datetime.datetime | None:
    fitfile = fitparse.FitFile(fit_path)
    for record in fitfile.get_messages("session"):
        for field in record:
            if field.name == "start_time" and field.value is not None:
                return field.value
    return None


def fake_fit_file(fit_path: str) -> str:
    """Runs fit-file-faker and verifies the output actually exists.
    Raises on any failure -- caller must NOT touch Garmin unless this
    returns successfully."""
    if not os.path.exists(FIT_FILE_FAKER_EXE):
        raise RuntimeError(f"fit-file-faker.exe not found at {FIT_FILE_FAKER_EXE}")
    subprocess.run(
        [FIT_FILE_FAKER_EXE, "--profile", FIT_FILE_FAKER_PROFILE, fit_path],
        check=True,
    )
    base, ext = os.path.splitext(fit_path)
    modified = f"{base}_modified{ext}"
    if not os.path.exists(modified):
        raise RuntimeError(f"fit-file-faker ran but no output file appeared at {modified}")
    return modified


def find_zwift_activity(garmin: Garmin, ride_start: datetime.datetime) -> str | None:
    """Polls Garmin for the activity Zwift's own integration auto-uploaded.
    Read-only -- does NOT delete anything."""
    for attempt in range(POLL_MAX_ATTEMPTS):
        activities = garmin.get_activities(0, 5)
        for act in activities:
            act_start_str = act.get("startTimeGMT")
            if not act_start_str:
                continue
            act_start = datetime.datetime.strptime(act_start_str, "%Y-%m-%d %H:%M:%S")
            delta = abs((act_start - ride_start).total_seconds())
            if delta <= MATCH_TOLERANCE_SEC:
                type_key = (act.get("activityType") or {}).get("typeKey", "")
                if "cycling" in type_key or "virtual_ride" in type_key:
                    activity_id = str(act["activityId"])
                    print(f"[match] activityId={activity_id} start={act_start_str} type={type_key}")
                    return activity_id
        print(f"[poll] attempt {attempt + 1}/{POLL_MAX_ATTEMPTS}: no match yet, waiting {POLL_INTERVAL_SEC}s")
        time.sleep(POLL_INTERVAL_SEC)
    return None


def safe_delete(garmin: Garmin, activity_id: str) -> bool:
    """Deletes with retry. Treats a 'not found' style error as success,
    since Garmin sometimes times out (504) on a delete that actually
    went through server-side."""
    for attempt in range(1, DELETE_RETRY_ATTEMPTS + 1):
        try:
            garmin.delete_activity(activity_id)
            return True
        except Exception as e:
            msg = str(e).lower()
            if "404" in msg or "not found" in msg:
                return True  # already gone -- treat as success
            if attempt == DELETE_RETRY_ATTEMPTS:
                print(f"      delete failed after {DELETE_RETRY_ATTEMPTS} attempts: {e}")
                return False
            print(f"      delete attempt {attempt} failed ({e}), retrying in {RETRY_WAIT_SEC}s...")
            time.sleep(RETRY_WAIT_SEC)
    return False


def safe_upload(garmin: Garmin, fit_path: str) -> bool:
    """Retries the upload. This is the one moment where a failure is
    genuinely bad -- the original has already been deleted by now."""
    for attempt in range(1, DELETE_RETRY_ATTEMPTS + 1):
        try:
            garmin.upload_activity(fit_path)
            return True
        except Exception as e:
            if attempt == DELETE_RETRY_ATTEMPTS:
                print(f"      upload failed after {DELETE_RETRY_ATTEMPTS} attempts: {e}")
                return False
            print(f"      upload attempt {attempt} failed ({e}), retrying in {RETRY_WAIT_SEC}s...")
            time.sleep(RETRY_WAIT_SEC)
    return False


def _mfa_fallback():
    notify("Garmin replace-sync: session expired, MFA needed -- run garmin_pipeline_test.py by hand to re-auth.")
    raise RuntimeError("MFA required but running unattended")


def main() -> None:
    after_ts = get_cutoff_timestamp()

    fit_path = newest_fit_file(after_ts)
    if not fit_path:
        print("[error] no new .fit file found -- was this an online ride?")
        notify("Garmin replace-sync: no new Zwift .fit file found, nothing done.")
        return

    ride_start = get_fit_start_time(fit_path)
    if not ride_start:
        print("[error] could not read start_time from FIT file")
        notify(f"Garmin replace-sync: couldn't parse start time from {fit_path}")
        return

    # Step 1: fake FIRST, before touching Garmin at all. If this fails,
    # nothing on Garmin is touched -- the original Zwift-synced upload
    # (if it lands) just stays as-is, same as before this automation existed.
    try:
        print(f"[fake] processing {fit_path}...")
        modified_path = fake_fit_file(fit_path)
        print(f"[fake] OK -> {modified_path}")
    except Exception as e:
        print(f"[error] fake step failed, Garmin untouched: {e}")
        notify(f"Garmin replace-sync: fake step failed ({e}) -- ride NOT modified, still shows as Zwift-synced.")
        return

    garmin = Garmin(GARMIN_EMAIL, get_garmin_password(), prompt_mfa=_mfa_fallback)
    garmin.login(tokenstore=GARMIN_TOKENSTORE)

    # Step 2: find (read-only) the Zwift-synced activity on Garmin
    activity_id = find_zwift_activity(garmin, ride_start)
    if not activity_id:
        print("[error] never found the Zwift-synced activity on Garmin Connect")
        notify("Garmin replace-sync: gave up waiting for Zwift's auto-sync to appear -- check Garmin Connect manually.")
        return

    # Step 3: delete, with retry/504-tolerance
    print(f"[delete] removing activityId={activity_id}...")
    if not safe_delete(garmin, activity_id):
        print("[error] delete failed after retries -- original left in place, nothing uploaded")
        notify(f"Garmin replace-sync: delete failed for activityId={activity_id} -- ride still shows as Zwift-synced, nothing done.")
        return

    # Step 4: upload the replacement, with retry
    print(f"[upload] uploading {modified_path}...")
    if not safe_upload(garmin, modified_path):
        print("[error] upload failed after retries -- activity is ORPHANED, needs manual repair")
        notify(
            f"Garmin replace-sync: URGENT -- deleted activityId={activity_id} but upload failed after retries. "
            f"Manually upload {modified_path} to Garmin Connect."
        )
        return

    print(f"[done] replaced Zwift-synced activity with {modified_path}")
    notify("Garmin replace-sync: done -- ride now shows as a real device, should count toward Challenges.")


if __name__ == "__main__":
    main()
