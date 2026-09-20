"""
garmin_pipeline_test.py

One-off safe test: logs in (with MFA the first time, caching the
session afterward), uploads an already-edited FIT file as a throwaway
test activity, confirms it landed, then deletes it.

Also serves as the interactive re-auth helper: whenever
garmin_replace_sync.py reports the cached session expired, run this
by hand once to handle the MFA prompt and re-cache the token to
C:\\scripts\\.garmin_tokens -- after that, the unattended script reads
it silently again.

Credentials: password is pulled from Windows Credential Manager via
`keyring`, never stored in this file. One-time setup:

    setx GARMIN_EMAIL "you@example.com"
    py -m pip install keyring
    py -c "import keyring; keyring.set_password('garmin', 'you@example.com', 'YOUR_PASSWORD')"

(open a NEW terminal window after setx before running anything else)

Usage:
    py garmin_pipeline_test.py "C:\\path\\to\\some_modified.fit"
"""

import os
import sys

import keyring
from garminconnect import Garmin

GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
if not GARMIN_EMAIL:
    raise RuntimeError(
        'GARMIN_EMAIL environment variable not set. Run once: setx GARMIN_EMAIL "you@example.com" '
        "then open a new terminal window."
    )

GARMIN_TOKENSTORE = r"C:\scripts\.garmin_tokens"


def get_garmin_password() -> str:
    pw = keyring.get_password("garmin", GARMIN_EMAIL)
    if not pw:
        raise RuntimeError(
            "Garmin password not found in Windows Credential Manager. Run:\n"
            f'  py -c "import keyring; keyring.set_password(\'garmin\', \'{GARMIN_EMAIL}\', \'YOUR_PASSWORD\')"'
        )
    return pw


def main():
    if len(sys.argv) < 2:
        print("Usage: py garmin_pipeline_test.py <path_to_modified.fit>")
        return

    fit_path = sys.argv[1]

    print("[1/4] Logging in...")
    garmin = Garmin(
        GARMIN_EMAIL,
        get_garmin_password(),
        prompt_mfa=lambda: input("Enter Garmin MFA code: "),
    )
    garmin.login(tokenstore=GARMIN_TOKENSTORE)
    print("      OK (session cached to", GARMIN_TOKENSTORE, "for future runs)")

    print("[2/4] Confirming read access (last 3 activities)...")
    recent = garmin.get_activities(0, 3)
    for act in recent:
        print(f"      {act.get('activityId')}  {act.get('startTimeGMT')}  {act.get('activityName')}")

    print(f"[3/4] Uploading test file: {fit_path}")
    result = garmin.upload_activity(fit_path)  # this version wants a path string, not a file object
    print(f"      Upload result: {result}")

    try:
        new_id = result["detailedImportResult"]["successes"][0]["internalId"]
    except Exception:
        new_id = input(
            "      Couldn't auto-parse new activity ID -- paste it here "
            "(check Garmin Connect or the printed result above): "
        ).strip()

    print(f"[4/4] Deleting test activity {new_id}...")
    garmin.delete_activity(new_id)
    print("      OK -- pipeline confirmed working, no test data left behind.")


if __name__ == "__main__":
    main()
