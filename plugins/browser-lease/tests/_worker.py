"""Test worker: exercises the lease from a separate process, prints one JSON line."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lease import BrowserLease  # noqa: E402


def _log(path: str, **payload) -> None:
    payload["t"] = time.time()
    with open(path, "a") as fh:
        fh.write(json.dumps(payload) + "\n")


def main(argv: list[str]) -> int:
    mode, args = argv[1], argv[2:]

    if mode == "critical":
        lock, log, owner, hold_s = args
        lease = BrowserLease(owner, path=Path(lock), wait_s=60.0, sticky_s=30.0)
        acquired, holder = lease.acquire()
        if not acquired:
            print(json.dumps({"acquired": False, "holder": holder}))
            return 1
        _log(log, kind="start", owner=owner)
        time.sleep(float(hold_s))
        _log(log, kind="end", owner=owner)
        lease.release()
        print(json.dumps({"acquired": True}))
        return 0

    if mode == "hold":
        lock, marker, owner, hold_s = args
        lease = BrowserLease(owner, path=Path(lock), wait_s=10.0, sticky_s=30.0)
        acquired, holder = lease.acquire()
        if not acquired:
            print(json.dumps({"acquired": False, "holder": holder}))
            return 1
        Path(marker).write_text(owner)
        print(json.dumps({"acquired": True}))
        sys.stdout.flush()
        time.sleep(float(hold_s))
        return 0

    if mode == "crash":
        lock, marker, owner, _hold = args
        lease = BrowserLease(owner, path=Path(lock), wait_s=5.0, sticky_s=30.0)
        acquired, holder = lease.acquire()
        if not acquired:
            print(json.dumps({"acquired": False, "holder": holder}))
            return 1
        Path(marker).write_text(owner)
        print(json.dumps({"acquired": True}))
        sys.stdout.flush()
        os._exit(0)  # die holding the lease: no release, no cleanup
    if mode == "try":
        lock, _unused, owner, wait_s = args
        lease = BrowserLease(owner, path=Path(lock), wait_s=float(wait_s), sticky_s=30.0)
        started = time.time()
        acquired, holder = lease.acquire()
        waited = time.time() - started
        if acquired:
            lease.release()
        print(json.dumps({"acquired": acquired, "holder": holder, "waited": waited}))
        return 0

    print(json.dumps({"error": f"unknown mode {mode}"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
