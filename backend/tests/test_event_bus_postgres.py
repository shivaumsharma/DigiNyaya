"""PostgreSQL LISTEN/NOTIFY wake-up for the shared event bus. Needs a real PostgreSQL: set DIGINYAYA_TEST_PG_URL to an empty
database (for example postgresql+psycopg://postgres@localhost:5544/dnbus); skipped otherwise. Runs in a subprocess so it
cannot disturb the SQLite database the rest of the suite uses."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

PG_URL = os.environ.get("DIGINYAYA_TEST_PG_URL")

SCRIPT = textwrap.dedent('''
    import os, sys, threading, time
    os.environ["DIGINYAYA_USE_LLM"] = "0"
    sys.path.insert(0, ".")
    from app import db
    from app.core.events import DbEventBus
    db.init_db()
    bus = DbEventBus()
    bus.NOTIFY_FALLBACK_SECONDS = 30.0   # polling alone would take this long: only NOTIFY can make it fast
    q = bus.subscribe("CASE-NOTIFY")
    time.sleep(1.0)                      # let the dispatcher reach its wait
    t0 = time.time()
    threading.Thread(target=lambda: bus.publish("CASE-NOTIFY", {"type": "agent", "n": 1})).start()
    ev = q.get(timeout=10)
    print("LATENCY", round(time.time() - t0, 3), ev["n"])
    print("LISTENER", bus._thread is not None)
''')


@unittest.skipUnless(PG_URL, "set DIGINYAYA_TEST_PG_URL to run the PostgreSQL bus test")
class PostgresNotify(unittest.TestCase):
    def test_a_published_event_reaches_the_dispatcher_without_waiting_for_a_poll(self):
        env = {**os.environ, "DIGINYAYA_DB": PG_URL}
        out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=Path(__file__).resolve().parents[1], env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        latency = float([line for line in out.stdout.splitlines() if line.startswith("LATENCY")][0].split()[1])
        self.assertLess(latency, 1.5, out.stdout)


if __name__ == "__main__":
    unittest.main()
