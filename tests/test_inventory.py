import unittest
from datetime import datetime, timezone

from inventory import build_core, fetch_inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.document = {
            "updated_at": self.now.isoformat(),
            "servers": [{
                "name": "worker", "backend": "cuda",
                "allocatable_vram_mb": 1000,
                "available_task_slots": 1, "enabled": True,
            }],
        }

    def test_valid_snapshot(self):
        core = build_core(self.document, now=self.now)
        self.assertEqual(core.snapshot()[0]["free_memory_mb"], 1000)

    def test_stale_snapshot(self):
        self.document["updated_at"] = "2025-01-01T00:00:00Z"
        with self.assertRaises(ValueError):
            build_core(self.document, now=self.now)

    def test_disabled_placeholder(self):
        self.document["servers"][0].update(name="010101", enabled=False)
        self.assertEqual(build_core(self.document, now=self.now).snapshot(), [])

    def test_enabled_placeholder(self):
        self.document["servers"][0]["name"] = "010101"
        with self.assertRaises(ValueError):
            build_core(self.document, now=self.now)

    def test_duplicate_names(self):
        self.document["servers"].append(dict(self.document["servers"][0]))
        with self.assertRaises(ValueError):
            build_core(self.document, now=self.now)

    def test_invalid_endpoints_fail_before_network_access(self):
        for url in ("http://example.com", "https://010101/inventory.json",
                    "https://user:password@example.com"):
            with self.assertRaises(ValueError):
                fetch_inventory(url)


if __name__ == "__main__":
    unittest.main()
