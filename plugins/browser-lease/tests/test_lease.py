"""Lease primitive: mutual exclusion, wait-then-veto, crash release, idle release."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lease import BrowserLease  # noqa: E402

WORKER = Path(__file__).resolve().parent / "_worker.py"


def _run(*args: str, timeout: float = 60.0) -> dict:
    out = subprocess.run([sys.executable, str(WORKER), *args],
                         capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise AssertionError(f"worker failed rc={out.returncode}: {out.stderr[-800:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


@unittest.skipIf(os.name != "posix", "advisory flock is POSIX-only")
class LeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_mutual_exclusion_across_processes(self) -> None:
        """Three processes race: critical sections must never overlap."""
        lock, log = self.tmp / "mx.lock", self.tmp / "mx.log"
        procs = [subprocess.Popen(
            [sys.executable, str(WORKER), "critical", str(lock), str(log), f"p{i}", "0.4"])
            for i in range(3)]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=90), 0)
        events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        depth, holders = 0, set()
        for event in sorted(events, key=lambda e: e["t"]):
            if event["kind"] == "start":
                depth += 1
                holders.add(event["owner"])
                self.assertEqual(depth, 1, f"two sessions inside the browser at once: {event}")
            else:
                depth -= 1
                self.assertEqual(depth, 0, f"overlapping section exit: {event}")
        self.assertEqual(len(holders), 3, f"each contender should get a turn, got {holders}")

    def test_wait_then_veto_names_the_holder(self) -> None:
        """A holder outlasting wait_s makes the other session fail fast, holder named."""
        lock, marker = self.tmp / "veto.lock", self.tmp / "veto.marker"
        holder = subprocess.Popen(
            [sys.executable, str(WORKER), "hold", str(lock), str(marker), "holder-A", "6"])
        try:
            for _ in range(200):
                if marker.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "holder never took the lease")
            result = _run("try", str(lock), "", "waiter-B", "1.0")
        finally:
            holder.kill()
            holder.wait(timeout=10)
        self.assertFalse(result["acquired"], f"waiter should be refused, got {result}")
        self.assertIn("holder-A", result["holder"])
        self.assertGreaterEqual(result["waited"], 0.9, "waiter must use its grace window")

    def test_dead_holder_releases(self) -> None:
        """A holder killed mid-hold must not wedge the engine."""
        lock, marker = self.tmp / "crash.lock", self.tmp / "crash.marker"
        _run("crash", str(lock), str(marker), "crasher", "0")
        self.assertTrue(marker.exists())
        lease = BrowserLease("survivor", path=lock, wait_s=1.0)
        acquired, holder = lease.acquire()
        try:
            self.assertTrue(acquired, f"lease not released by the dead holder ({holder})")
        finally:
            lease.release()

    def test_idle_watchdog_releases(self) -> None:
        """The lease drops itself after sticky_s with no call, so a stalled turn cannot wedge it."""
        lease = BrowserLease("idler", path=self.tmp / "sticky.lock", sticky_s=1.0)
        self.assertTrue(lease.acquire()[0])
        self.assertTrue(lease.held_by_me())
        time.sleep(1.6)
        self.assertFalse(lease.held_by_me(), "lease never expired while idle")

    def test_touch_keeps_lease_past_idle_window(self) -> None:
        """An actively browsing turn holds the lease past the idle window."""
        lease = BrowserLease("busy", path=self.tmp / "renew.lock", sticky_s=1.0)
        self.assertTrue(lease.acquire()[0])
        for _ in range(3):
            time.sleep(0.6)
            lease.touch()
        self.assertTrue(lease.held_by_me(), "renewed lease must survive past sticky_s")
        lease.release()


if __name__ == "__main__":
    unittest.main()
