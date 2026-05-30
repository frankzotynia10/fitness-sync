from fastmcp import FastMCP
from config import WORKOS_AUTHKIT_DOMAIN, BASE_URL

# Auth
if WORKOS_AUTHKIT_DOMAIN and BASE_URL:
    from fastmcp.server.auth.providers.workos import AuthKitProvider
    auth = AuthKitProvider(authkit_domain=WORKOS_AUTHKIT_DOMAIN, base_url=BASE_URL)
    mcp = FastMCP("Fitness Coach DB", auth=auth)
else:
    mcp = FastMCP("Fitness Coach DB")

# Register all tool modules
from tools import generic, garmin, strava, nutrition, hevy, proposals

generic.register(mcp)
garmin.register(mcp)
strava.register(mcp)
nutrition.register(mcp)
hevy.register(mcp)
proposals.register(mcp)

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
