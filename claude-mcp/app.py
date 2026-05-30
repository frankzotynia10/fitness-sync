import threading
from fastmcp import FastMCP
from config import WORKOS_AUTHKIT_DOMAIN, BASE_URL

# External MCP — WorkOS auth required
if WORKOS_AUTHKIT_DOMAIN and BASE_URL:
    from fastmcp.server.auth.providers.workos import AuthKitProvider
    auth = AuthKitProvider(authkit_domain=WORKOS_AUTHKIT_DOMAIN, base_url=BASE_URL)
    mcp = FastMCP("Fitness Coach DB", auth=auth)
else:
    mcp = FastMCP("Fitness Coach DB")

# Internal MCP — no auth, internal network only
mcp_internal = FastMCP("Fitness Coach DB Internal")

# Register all tool modules on both instances
from tools import generic, garmin, strava, nutrition, hevy, proposals

for instance in [mcp, mcp_internal]:
    generic.register(instance)
    garmin.register(instance)
    strava.register(instance)
    nutrition.register(instance)
    hevy.register(instance)
    proposals.register(instance)

if __name__ == "__main__":
    # Start internal unauthenticated instance on port 8001 in background thread
    t = threading.Thread(
        target=lambda: mcp_internal.run(transport="http", host="0.0.0.0", port=8001),
        daemon=True
    )
    t.start()

    # Start external authenticated instance on port 8000 (main thread)
    mcp.run(transport="http", host="0.0.0.0", port=8000)