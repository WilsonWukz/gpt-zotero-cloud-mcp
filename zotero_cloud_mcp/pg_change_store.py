"""Persistent PostgreSQL owner-approval/operation store for Neon.

A Zotero write and a database transaction cannot be atomic together. We keep
one-shot claims and receipts across application restarts and do not retry an
uncertain Zotero write.
"""
import hashlib
import json
import secrets
import threading
import time
from functools import wraps
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .change_store import canonical
from .zotero import DataError

TABLE = "zotero_mcp_change_plans"
FINAL = frozenset({"applied", "partial", "failed", "uncertain"})


def locked(func):
    @wraps(func)
    def call(self, *args, **kwargs):
        with self.lock:
            return func(self, *args, **kwargs)
    return call


class PostgresChangeStore:
    """Matches ChangeStore API; multiple hosts/processes may share the DB."""

    def __init__(self, dsn, retention_days=7, allow_insecure_local=False):
        u = urlsplit(dsn)
        if (u.scheme not in {"postgres", "postgresql"} or not u.hostname
                or not u.username or not u.password or u.fragment):
            raise ValueError("ZOTERO_DATABASE_URL requires a PostgreSQL URL with credentials")
        self.local = allow_insecure_local and u.hostname in {"localhost", "127.0.0.1"}
        if not self.local and not u.hostname.endswith(".neon.tech"):
            raise ValueError("Remote PostgreSQL host must be a Neon endpoint")
        try:
            config = conninfo_to_dict(dsn)
        except Exception:
            raise ValueError("Invalid PostgreSQL connection configuration") from None
        if not self.local and config.get("sslmode", "require") not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("TLS required for remote PostgreSQL")
        self.dsn = dsn  # Private: never send it to MCP, logs, or GitHub.
        self.lock = threading.RLock()
        self.retention = retention_days * 86400
        with self.connect() as db:
            db.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                epoch TEXT NOT NULL,
                digest TEXT NOT NULL,
                created DOUBLE PRECISION NOT NULL,
                expires DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL,
                plan TEXT NOT NULL,
                receipts TEXT NOT NULL
            )""")
            db.execute(f"""CREATE INDEX IF NOT EXISTS zotero_mcp_change_plans_history_idx
                ON {TABLE} (owner, epoch, created DESC, id)""")

    def connect(self):
        opts = {"autocommit": True, "row_factory": dict_row, "connect_timeout": 20,
                "prepare_threshold": None}
        if not self.local:
            opts["sslmode"] = "verify-full"
        return psycopg.connect(self.dsn, **opts)

    @locked
    def close(self):
        # Connections are short-lived; nothing to close across idle periods.
        pass

    @locked
    def cleanup(self):
        with self.connect() as db:
            db.execute(f"""DELETE FROM {TABLE} WHERE created < %s
                AND status NOT IN ('applying','uncertain')""",
                (time.time() - self.retention,))

    @staticmethod
    def unpack(row, owner=None, epoch=None):
        if not row or owner is not None and row["owner"] != owner or epoch is not None and row["epoch"] != epoch:
            raise DataError("PLAN_NOT_FOUND", "No accessible change plan")
        r = dict(row)
        if hashlib.sha256(r["plan"].encode()).hexdigest() != r["digest"]:
            raise DataError("PLAN_CORRUPT", "Stored plan integrity check failed")
        r["plan"] = json.loads(r["plan"])
        r["receipts"] = json.loads(r["receipts"])
        return r

    @locked
    def create(self, owner, epoch, plan):
        self.cleanup()
        raw = canonical(plan)
        if len(raw.encode()) > 2_000_000:
            raise DataError("PLAN_TOO_LARGE", "Split the changes into smaller plans")
        pid, now = secrets.token_urlsafe(24), time.time()
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.connect() as db:
            with db.transaction():
                # Serialize the bounded-capacity check across all workers.
                db.execute("SELECT pg_advisory_xact_lock(879164257)")
                n = db.execute(f"SELECT count(*) AS n FROM {TABLE}").fetchone()["n"]
                if n >= 1000:
                    raise DataError("HISTORY_FULL", "Export/review history before adding plans")
                db.execute(f"""INSERT INTO {TABLE}
                    (id,owner,epoch,digest,created,expires,status,plan,receipts)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid, owner, epoch, digest, now, now + 1800, "pending", raw, "[]"))
        return self.get(pid, owner, epoch)

    @locked
    def get(self, pid, owner=None, epoch=None):
        with self.connect() as db:
            row = db.execute(f"SELECT * FROM {TABLE} WHERE id=%s", (pid,)).fetchone()
        return self.unpack(row, owner, epoch)

    @locked
    def transition(self, pid, old, new):
        with self.connect() as db:
            row = db.execute(f"""UPDATE {TABLE} SET status=%s
                WHERE id=%s AND status=%s AND expires>%s RETURNING id""",
                (new, pid, old, time.time())).fetchone()
        if row is None:
            raise DataError("PLAN_STATE_CONFLICT", "Plan expired, was already submitted, or needs a fresh review")

    @locked
    def claim(self, pid, owner, epoch, digest):
        with self.connect() as db:
            with db.transaction():
                row = db.execute(f"SELECT * FROM {TABLE} WHERE id=%s FOR UPDATE", (pid,)).fetchone()
                r = self.unpack(row, owner, epoch)
                if r["digest"] != digest:
                    raise DataError("PLAN_DIGEST_MISMATCH", "Use the digest of the exact reviewed plan")
                if r["status"] in FINAL:
                    return r, False
                if r["status"] != "approved" or r["expires"] <= time.time():
                    raise DataError("OWNER_APPROVAL_REQUIRED", "Owner approval required for an unexpired plan")
                db.execute(f"UPDATE {TABLE} SET status='applying' WHERE id=%s", (pid,))
                return r, True

    @locked
    def record(self, pid, receipts, status="applying"):
        with self.connect() as db:
            db.execute(f"UPDATE {TABLE} SET receipts=%s,status=%s WHERE id=%s",
                       (canonical(receipts), status, pid))

    @locked
    def history(self, owner, epoch, start=0, limit=20):
        with self.connect() as db:
            rows = db.execute(f"""SELECT id,digest,created,expires,status FROM {TABLE}
                WHERE owner=%s AND epoch=%s ORDER BY created DESC,id LIMIT %s OFFSET %s""",
                (owner, epoch, limit + 1, start)).fetchall()
        return {"plans": [dict(r) for r in rows[:limit]],
                "next_start": start + limit if len(rows) > limit else None}
