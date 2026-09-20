# Windows client: Zwift -> Garmin device-spoofing pipeline

Runs on the Windows PC used for Zwift (not on the system running the main stack). 
Separate from the `garmin/` folder in this repo, which is the always-on Docker
Garmin<->Postgres sync service -- this is local automation that makes
Zwift rides count toward Garmin Connect Challenges/badges, which
Garmin doesn't do natively for Zwift-synced activities.

## How it works

Zwift's own Garmin Connect integration stays enabled and auto-uploads
every ride as normal. `garmin_replace_sync.py` then:

1. Finds the newest local `.fit` file in
   `C:\Users\frank\AppData\Local\Zwift\Activities` (using a marker
   file the AHK "Zwift Online" hotkey drops before launching Zwift, so
   it only picks up files from *this* ride).
2. Runs `fit-file-faker` (https://github.com/jat255/fit-file-faker) on
   it to spoof the FIT file's `device_info` to a real Garmin device
   (an Edge 850), using a profile configured once via
   `fit-file-faker --config-menu`.
3. Polls Garmin Connect for the activity Zwift's own integration just
   auto-uploaded, matching by start time + activity type.
4. Deletes that Zwift-attributed activity and uploads the
   device-spoofed replacement in its place.

Wired to run automatically: the AHK hotkey (`Ctrl+Alt+9`, "Zwift
 Online") launches Zwift, waits for `ZwiftApp.exe` to close (ride
finished), then runs this script in the background.

## Ordering matters

The fake step always runs *before* anything is deleted from Garmin,
and its output file is verified to exist before the delete happens.
This was learned the hard way on 2026-09-20, when an earlier version
of the one-time backfill script (same pattern, used to retroactively
fix historical activities) deleted first and faked/uploaded after --
a PATH resolution failure in `fit-file-faker` then caused 4 real
Garmin activities to be deleted with no replacement uploaded. Delete
and upload both retry with backoff; delete treats a "not found" error
as success (Garmin can return a 504 timeout on a delete that actually
succeeded server-side).

## One-time setup on a new machine

```
py -m pip install garminconnect fitparse keyring
py -c "import keyring; keyring.set_password('garmin', 'YOUR_EMAIL', 'YOUR_PASSWORD')"
fit-file-faker --config-menu   # create a profile named "zwift", device = Edge 850 (or your real device), using its numeric Unit ID (Settings -> About -> Copyright Info -> Unit ID on the device itself -- not the printed serial number)
py garmin_pipeline_test.py "<path to any local .fit file>"   # handles the first MFA prompt, caches the session token
```

After that, `garmin_replace_sync.py` reads the cached token from
`C:\scripts\.garmin_tokens` silently. If Garmin later forces a fresh
MFA challenge, the unattended script can't prompt for a code -- it
sends an ntfy push saying so and exits cleanly, and `garmin_pipeline_test.py`
is the one you run by hand to re-auth.

Password is never stored in plaintext -- always read from Windows
Credential Manager via `keyring`.
