"""One worker: pending OAuth state is intentionally process-local."""
import os
import uvicorn

if __name__ == "__main__":
    local = os.getenv("LOCAL_DEV", "false").lower() == "true"
    uvicorn.run("zotero_cloud_mcp.app:create_app", factory=True,
                host="127.0.0.1" if local else "0.0.0.0", port=int(os.getenv("PORT", "8000")),
                workers=1, access_log=False, log_level="warning", proxy_headers=False)
