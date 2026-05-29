from garminconnect import Garmin
import os
import sys

GARMIN_EMAIL = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]

# Use the mounted token path if provided, otherwise default to ~/.garminconnect
TOKEN_DIR = os.environ.get("GARMINTOKENS", os.path.expanduser("~/.garminconnect"))

def get_mfa_code():
    return input("Enter Garmin MFA code: ").strip()

def main():
    os.makedirs(TOKEN_DIR, exist_ok=True)

    print("Starting Garmin login...")
    print(f"Using token directory: {TOKEN_DIR}")
    print("If Garmin requires MFA, you will be prompted for the code.")

    try:
        client = Garmin(
            GARMIN_EMAIL,
            GARMIN_PASSWORD,
            prompt_mfa=get_mfa_code
        )

        # THIS is the important part: tell login() where to persist tokens
        client.login(tokenstore=TOKEN_DIR)

        full_name = client.get_full_name()
        print(f"Login successful. Connected as: {full_name}")
        print("Garmin tokens should now be saved automatically.")
        print("Later runs can reuse those saved tokens.")

        print("Token directory contents:")
        print(os.listdir(TOKEN_DIR))

    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
