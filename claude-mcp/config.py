import os
from dotenv import load_dotenv

load_dotenv()

# Read DB
DB_HOST     = os.environ["DB_HOST"]
DB_PORT     = os.environ.get("DB_PORT", "5432")
DB_NAME     = os.environ.get("DB_NAME", "postgres")
DB_USER     = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]

# Write DB
DB_WRITE_HOST     = os.environ.get("DB_WRITE_HOST", DB_HOST)
DB_WRITE_PORT     = os.environ.get("DB_WRITE_PORT", DB_PORT)
DB_WRITE_NAME     = os.environ.get("DB_WRITE_NAME", DB_NAME)
DB_WRITE_USER     = os.environ.get("DB_WRITE_USER")
DB_WRITE_PASSWORD = os.environ.get("DB_WRITE_PASSWORD")

# Hevy API
HEVY_API_KEY  = os.environ.get("HEVY_API_KEY")
HEVY_API_BASE = os.environ.get("HEVY_API_BASE", "https://api.hevyapp.com").rstrip("/")

# Auth
WORKOS_AUTHKIT_DOMAIN = os.environ.get("WORKOS_AUTHKIT_DOMAIN")
BASE_URL              = os.environ.get("BASE_URL")
