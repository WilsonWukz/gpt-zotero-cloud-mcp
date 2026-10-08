"""Private, bounded SQLite change plans. This is NOT the Zotero database.

Plans are immutable, owner/client/credential-epoch bound, approved in the browser,
then claimed once before any upstream mutation. A running plan never auto-resumes.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import threading
from functools import wraps

from .zotero import DataError


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapped


class ChangeStore:
    def __init__(self, path, retention_days=7):
        self.lock = threading.RLock()
        self.retention = retention_days * 86400
        if path != ":memory:":
            p = Path(path)
            if not p.is_absolute() or p.is_symlink():
                raise ValueError("ZOTERO_STATE_DB must be an absolute, non-symlink private path")
            p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Resolve every component: an attacker-controlled symlink is not a private store.
            if p.parent.resolve() != p.parent.absolute():
                raise ValueError("State directory must not traverse symlinks")
            if p.parent.stat().st_uid != os.getuid() or p.parent.stat().st_mode & 0o077:
                raise ValueError("State directory must be owned by the service user and mode 0700")
            fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            os.chmod(p, 0o600)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("""CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, epoch TEXT NOT NULL,
            digest TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
            status TEXT NOT NULL, plan TEXT NOT NULL, receipts TEXT NOT NULL)""")

    @synchronized
    def close(self):
        self.db.close()

    @synchronized
    def cleanup(self):
        # Never silently drop an in-flight/uncertain write receipt.
        self.db.execute("DELETE FROM plans WHERE created<? AND status NOT IN ('applying','uncertain')",
                        (time.time() - self.retention,))

    @synchronized
    def create(self, owner, epoch, plan):
        self.cleanup()
        if self.db.execute("SELECT count(*) FROM plans").fetchone()[0] >= 1000:
            raise DataError("HISTORY_FULL", "Export/review the private change history before adding plans")
        raw = canonical(plan)
        if len(raw.encode()) > 2_000_000:
            raise DataError("PLAN_TOO_LARGE", "Split the changes into smaller plans")
        pid, now = secrets.token_urlsafe(24), time.time()
        digest = hashlib.sha256(raw.encode()).hexdigest()
        self.db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?,?,?)",
                        (pid, owner, epoch, digest, now, now + 1800, "pending", raw, "[]"))
        return self.get(pid, owner, epoch)

    @synchronized
    def get(self, pid, owner=None, epoch=None):
        r = self.db.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
        if not r or (owner is not None and r['owner'] != owner) or (epoch is not None and r['epoch'] != epoch):
            raise DataError("PLAN_NOT_FOUND", "No accessible change plan")
        r = dict(r)
        if hashlib.sha256(r['plan'].encode()).hexdigest() != r['digest']:
            raise DataError('PLAN_CORRUPT', 'Stored plan integrity check failed')
        r['plan'], r['receipts'] = json.loads(r['plan']), json.loads(r['receipts'])
        return r

    @synchronized
    def transition(self, pid, old, new):
        cur = self.db.execute("UPDATE plans SET status=? WHERE id=? AND status=? AND expires>?",
                              (new, pid, old, time.time()))
        if cur.rowcount != 1:
            raise DataError("PLAN_STATE_CONFLICT", "Plan expired, was already submitted, or needs a fresh review")

    @synchronized
    def claim(self, pid, owner, epoch, digest):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.get(pid, owner, epoch)
            if row['digest'] != digest:
                raise DataError("PLAN_DIGEST_MISMATCH", "Use the digest of the exact reviewed plan")
            if row['status'] in {'applied', 'partial', 'failed', 'uncertain'}:
                self.db.execute("COMMIT")
                return row, False
            if row['status'] != 'approved':
                raise DataError("OWNER_APPROVAL_REQUIRED", "The owner must approve the exact plan in its review page first")
            self.transition(pid, 'approved', 'applying')
            self.db.execute("COMMIT")
            return row, True
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    @synchronized
    def record(self, pid, receipts, status='applying'):
        self.db.execute("UPDATE plans SET receipts=?, status=? WHERE id=?",
                        (canonical(receipts), status, pid))

    @synchronized
    def history(self, owner, epoch, start=0, limit=20):
        rows = self.db.execute("SELECT id,digest,created,expires,status FROM plans WHERE owner=? AND epoch=? "
                               "ORDER BY created DESC,id LIMIT ? OFFSET ?", (owner, epoch, limit + 1, start)).fetchall()
        return {'plans': [dict(x) for x in rows[:limit]], 'next_start': start + limit if len(rows) > limit else None}
