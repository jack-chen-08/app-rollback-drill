"""Minimal health watchdog: writes a JSON line to ALERT_LOG when /health fails."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

WATCH_URL = os.environ["WATCH_URL"]
INTERVAL = float(os.environ.get("WATCH_INTERVAL_S", "2"))
THRESHOLD = int(os.environ.get("WATCH_FAIL_THRESHOLD", "2"))
ALERT_LOG = os.environ["ALERT_LOG"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def probe() -> tuple[int, str]:
    try:
        with urllib.request.urlopen(WATCH_URL, timeout=2) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:200]
    except Exception as exc:  # noqa: BLE001 - any transport failure counts as down
        return 0, f"{exc.__class__.__name__}: {exc}"


def append(event: dict) -> None:
    os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)
    with open(ALERT_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        fh.flush()


def main() -> None:
    streak = 0
    fired = False
    while True:
        status, body = probe()
        if status != 200:
            streak += 1
            if streak >= THRESHOLD and not fired:
                append(
                    {
                        "event": "alert_fired",
                        "fired_at": now_iso(),
                        "url": WATCH_URL,
                        "status": status,
                        "consecutive_failures": streak,
                        "detail": body,
                    }
                )
                fired = True
        else:
            if fired:
                append({"event": "recovered", "fired_at": now_iso(), "url": WATCH_URL, "status": 200})
            streak = 0
            fired = False
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
