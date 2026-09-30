import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from genesis_core import GenesisCore, NoCapacity, Task


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.core = GenesisCore()
        self.core.register("worker", "cpu", 100, slots=2)
        self.task = Task(60, ("cpu",))

    def test_execution_releases_capacity(self):
        self.assertEqual(self.core.execute(self.task, lambda n: n), "worker")
        self.assertEqual(self.core.snapshot()[0]["free_memory_mb"], 100)

    def test_failure_releases_capacity(self):
        def fail(node):
            raise RuntimeError("Callback failure")
        with self.assertRaises(RuntimeError):
            self.core.execute(self.task, fail)
        self.assertEqual(self.core.snapshot()[0]["active_tasks"], 0)
        self.assertEqual(self.core.snapshot()[0]["free_memory_mb"], 100)

    def test_backend_and_memory_limits(self):
        for task in (Task(101, ("cpu",)), Task(1, ("cuda",))):
            with self.assertRaises(NoCapacity):
                self.core.execute(task, lambda n: n)

    def test_draining_rejects_new_tasks(self):
        self.core.drain("worker")
        with self.assertRaises(NoCapacity):
            self.core.execute(self.task, lambda n: n)

    def test_concurrent_reservations(self):
        entered, release = Event(), Event()
        def hold(node):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test callback timed out")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.core.execute, self.task, hold)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(NoCapacity):
                    self.core.execute(self.task, lambda n: n)
                self.core.drain("worker")
            finally:
                release.set()
            future.result(timeout=5)
        self.assertEqual(self.core.snapshot()[0]["free_memory_mb"], 100)


if __name__ == "__main__":
    unittest.main()
