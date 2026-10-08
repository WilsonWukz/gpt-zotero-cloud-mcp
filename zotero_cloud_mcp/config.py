"""Configuration is separate from source; no account-specific defaults."""
from dataclasses import dataclass, field
import json
import os
from urllib.parse import urlsplit

VERSION = "0.2.0"
SCOPE = "zotero:read"
WRITE_SCOPE = "zotero:write"

@dataclass(frozen=True)
class Settings:
    base_url: str
    app_secret: str = field(default="", repr=False)
    login_password: str = field(default="", repr=False)
    zotero_key: str = field(default="", repr=False)
    collection_name: str = ""
    collection_key: str = ""
    library_type: str = "user"
    library_id: str = ""
    # Explicit operator opt-in; never adds write tools or non-GET upstream calls.
    allow_write_key: bool = False
    enable_writes: bool = False
    enable_content_reads: bool = False
    allow_permanent_delete: bool = False
    state_db: str = ""
    database_url: str = field(default="", repr=False)
    extra_redirects: tuple[str, ...] = ()
    local_dev: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        redirects = json.loads(os.getenv("OAUTH_REDIRECT_URIS", "[]"))
        if not isinstance(redirects, list) or not all(isinstance(x, str) for x in redirects):
            raise ValueError("OAUTH_REDIRECT_URIS must be a JSON list of exact URLs")
        return cls(
            base_url=os.getenv("PUBLIC_BASE_URL", os.getenv("RENDER_EXTERNAL_URL", "http://127.0.0.1:8000")).rstrip("/"),
            app_secret=os.getenv("APP_SECRET", ""),
            login_password=os.getenv("MCP_LOGIN_PASSWORD", ""),
            zotero_key=os.getenv("ZOTERO_API_KEY", ""),
            collection_name=os.getenv("ZOTERO_COLLECTION_NAME", ""),
            collection_key=os.getenv("ZOTERO_COLLECTION_KEY", ""),
            library_type=os.getenv("ZOTERO_LIBRARY_TYPE", "user"),
            library_id=os.getenv("ZOTERO_LIBRARY_ID", ""),
            allow_write_key=os.getenv("ZOTERO_ALLOW_WRITE_KEY", "false").strip().lower() == "true",
            enable_writes=os.getenv("ZOTERO_ENABLE_WRITES", "false").strip().lower() == "true",
            enable_content_reads=os.getenv("ZOTERO_ENABLE_CONTENT_READS", "false").strip().lower() == "true",
            allow_permanent_delete=os.getenv("ZOTERO_ALLOW_PERMANENT_DELETE", "false").strip().lower() == "true",
            state_db=os.getenv("ZOTERO_STATE_DB", ""),
            database_url=os.getenv("ZOTERO_DATABASE_URL", ""),
            extra_redirects=tuple(redirects),
            local_dev=os.getenv("LOCAL_DEV", "false").lower() == "true",
        )

    @property
    def resource(self) -> str:
        return self.base_url + "/mcp"

    @property
    def auth_ready(self) -> bool:
        u = urlsplit(self.base_url)
        secure = u.scheme == "https" or (self.local_dev and u.scheme == "http" and u.hostname in {"localhost", "127.0.0.1"})
        return bool(secure and u.hostname and not (u.username or u.password or u.query or u.fragment or u.path)
                    and 32 <= len(self.app_secret) <= 512 and 32 <= len(self.login_password) <= 512
                    and self.app_secret != self.login_password and self.zotero_key not in {self.app_secret, self.login_password})

    @property
    def data_ready(self) -> bool:
        return bool(self.zotero_key and (self.collection_name or self.collection_key)
                    and self.library_type in {"user", "group"}
                    and (not self.library_id or self.library_id.isascii() and self.library_id.isdecimal())
                    and (self.library_type != "group" or self.library_id))
