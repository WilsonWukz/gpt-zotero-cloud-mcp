"""PostgreSQL integration tests; CI uses disposable local PostgreSQL."""
import os
from concurrent.futures import ThreadPoolExecutor
import unittest
import uuid

from zotero_cloud_mcp.pg_change_store import PostgresChangeStore
from zotero_cloud_mcp.zotero import DataError


DSN = os.getenv("ZOTERO_TEST_POSTGRES_URL", "")


@unittest.skipUnless(DSN, "No disposable CI PostgreSQL")
class PostgresStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = PostgresChangeStore(DSN, allow_insecure_local=True)
        self.owner = "synthetic-" + uuid.uuid4().hex
        self.epoch = "synthetic-epoch"
        self.plan = {"preview": [{"action": "synthetic test"}],
                     "steps": [{"kind": "synthetic"}], "library_version": 3}

    def tearDown(self):
        self.store.close()

    def test_roundtrip_and_restart_and_client_separation(self):
        row = self.store.create(self.owner, self.epoch, self.plan)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["plan"], self.plan)
        with self.assertRaises(DataError) as ctx:
            self.store.get(row["id"], self.owner + "-other", self.epoch)
        self.assertEqual(ctx.exception.code, "PLAN_NOT_FOUND")
        self.store.close()
        again = PostgresChangeStore(DSN, allow_insecure_local=True)
        self.assertEqual(again.get(row["id"], self.owner, self.epoch)["plan"], self.plan)

    def test_approval_claim_idempotency_receipt_reconnection(self):
        row = self.store.create(self.owner, self.epoch, self.plan)
        with self.assertRaises(DataError):
            self.store.claim(row["id"], self.owner, self.epoch, row["digest"])
        self.store.transition(row["id"], "pending", "approved")
        with self.assertRaises(DataError) as ctx:
            self.store.claim(row["id"], self.owner, self.epoch, "0" * 64)
        self.assertEqual(ctx.exception.code, "PLAN_DIGEST_MISMATCH")
        r, new = self.store.claim(row["id"], self.owner, self.epoch, row["digest"])
        self.assertTrue(new)
        with self.assertRaises(DataError) as ctx:
            self.store.claim(row["id"], self.owner, self.epoch, row["digest"])
        self.assertEqual(ctx.exception.code, "OWNER_APPROVAL_REQUIRED")
        self.store.record(row["id"], [{"state": "done"}], "applied")
        again = PostgresChangeStore(DSN, allow_insecure_local=True)
        r, new = again.claim(row["id"], self.owner, self.epoch, row["digest"])
        self.assertFalse(new)
        self.assertEqual(r["receipts"], [{"state": "done"}])
        self.assertEqual(again.history(self.owner, self.epoch)["plans"][0]["status"], "applied")

    def test_two_instances_cannot_claim_same_plan(self):
        row = self.store.create(self.owner, self.epoch, self.plan)
        self.store.transition(row["id"], "pending", "approved")
        stores = [PostgresChangeStore(DSN, allow_insecure_local=True) for _ in range(2)]
        def do_claim(s):
            try:
                return s.claim(row["id"], self.owner, self.epoch, row["digest"])[1]
            except DataError as e:
                return e.code
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(do_claim, stores))
        self.assertIn(True, results)
        self.assertIn("OWNER_APPROVAL_REQUIRED", results)
        self.assertEqual(self.store.get(row["id"], self.owner, self.epoch)["status"], "applying")
        self.store.cleanup()
        self.assertEqual(self.store.get(row["id"], self.owner, self.epoch)["status"], "applying")

    def test_cannot_approve_cancelled_plan(self):
        row = self.store.create(self.owner, self.epoch, self.plan)
        self.store.transition(row["id"], "pending", "cancelled")
        with self.assertRaises(DataError):
            self.store.transition(row["id"], "pending", "approved")

    def test_bad_database_hosts_and_plaintext_rejected(self):
        with self.assertRaises(ValueError):
            PostgresChangeStore("postgresql://u:secret@other.example/db?sslmode=require")
        with self.assertRaises(ValueError):
            PostgresChangeStore("postgresql://u:secret@ep-myproject.neon.tech/db?sslmode=disable")
