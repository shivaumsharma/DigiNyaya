"""Reset cases stuck in 'processing' after a crashed pipeline run.

Why this is needed: before the jobs.py fix, an exception during the
pipeline (e.g. the Ollama/CUDA embeddings crash) logged an 'error' event
but never updated case.status. The case is left at status="processing"
forever, and POST /api/cases/{id}/run silently no-ops on it forever
(main.py's guard only allows starting from "awaiting_response" or "ready").

This script finds those orphaned cases and resets them to "ready" so they
can be re-run from scratch via the normal /run endpoint. It also appends
an audit event so the reset itself is visible in the case's event log.

Usage:
    python scripts/reset_stuck_cases.py                 # list stuck cases, ask before touching anything
    python scripts/reset_stuck_cases.py --yes            # reset ALL stuck cases without prompting
    python scripts/reset_stuck_cases.py DN-XXXX DN-YYYY  # reset only specific case IDs
    python scripts/reset_stuck_cases.py DN-XXXX --yes    # reset one specific case, no prompt

Run this from the `backend` folder (same folder as diginyaya.db), with
the venv active, so `import app.db` resolves correctly.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time

# Make sure we're importing the real app package, not a stray one.
sys.path.insert(0, ".")

from app import db  # noqa: E402

_DB_PATH = os.getenv("DIGINYAYA_DB", os.path.join(os.path.dirname(db.__file__), "..", "diginyaya.db"))
_DB_PATH = os.path.abspath(_DB_PATH)


def _check_integrity_and_backup() -> None:
    """Refuse to touch the DB unless it passes an integrity check, and always
    take a timestamped backup first. WAL-mode SQLite files (.db + .db-wal +
    .db-shm) are only consistent as a set -- copying just the .db file while
    the server is running, or mid-checkpoint, can leave you with a corrupted
    copy. This guards against writing on top of that."""
    if not os.path.exists(_DB_PATH):
        print(f"Could not find {_DB_PATH}. Run this from the `backend` folder, "
              f"or set DIGINYAYA_DB to the correct path.")
        sys.exit(1)

    try:
        con = sqlite3.connect(_DB_PATH)
        result = con.execute("PRAGMA integrity_check").fetchall()
        con.close()
    except sqlite3.DatabaseError as exc:
        print(f"!! Refusing to continue: {_DB_PATH} failed to open cleanly ({exc}).")
        print("   Make sure the backend server is stopped (so the .db-wal file is")
        print("   fully checkpointed) before running this script, and back up")
        print("   diginyaya.db, diginyaya.db-wal, and diginyaya.db-shm together.")
        sys.exit(1)

    if result != [("ok",)]:
        print("!! Refusing to continue: PRAGMA integrity_check reported problems:")
        for row in result:
            print(f"   {row}")
        sys.exit(1)

    backup_path = f"{_DB_PATH}.backup-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(_DB_PATH, backup_path)
    print(f"Backup written to {backup_path}")


def find_stuck_cases() -> list[str]:
    """Return case_ids whose last known status is 'processing'.

    Note: this can't check jobs.is_running() (that's an in-memory dict in
    the live server process, not visible from this standalone script).
    Only run this when the backend server is NOT actively processing those
    cases -- e.g. after confirming via the event log that the last event
    was an unhandled error, or the server has been restarted since.
    """
    stuck = []
    for case in db.all_cases():
        if case.get("status") == "processing":
            stuck.append(case["case_id"])
    return stuck


def last_event_summary(case_id: str) -> str:
    events = db.get_events(case_id, after_seq=0)
    if not events:
        return "(no events logged)"
    last = events[-1]
    return f"seq {last.get('seq')} · {last.get('type')}/{last.get('agent')} · {last.get('detail')}"


def reset_case(case_id: str) -> None:
    case = db.get_case(case_id)
    if case is None:
        print(f"  ! {case_id}: not found, skipping")
        return
    if case.get("status") != "processing":
        print(f"  ! {case_id}: status is '{case.get('status')}', not 'processing' -- skipping")
        return

    db.update_case(case_id, status="ready")
    db.append_event(
        case_id,
        {
            "type": "manual_reset",
            "agent": "orchestrator",
            "status": "",
            "title": "Manual reset",
            "detail": "Case manually reset from 'processing' to 'ready' after a crashed run "
            "(pre-dates the jobs.py error-status fix). Safe to re-run.",
            "payload": {},
            "ts": time.time(),
        },
    )
    print(f"  ✓ {case_id}: processing -> ready")


def main() -> None:
    _check_integrity_and_backup()

    args = sys.argv[1:]
    auto_yes = "--yes" in args
    explicit_ids = [a for a in args if not a.startswith("--")]

    if explicit_ids:
        targets = explicit_ids
    else:
        targets = find_stuck_cases()

    if not targets:
        print("No cases stuck in 'processing'. Nothing to do.")
        return

    print(f"Found {len(targets)} case(s) stuck in 'processing':\n")
    for cid in targets:
        print(f"  - {cid}")
        print(f"      last event: {last_event_summary(cid)}")

    if not auto_yes:
        answer = input("\nReset these to 'ready' so they can be re-run? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted, nothing changed.")
            return

    print()
    for cid in targets:
        reset_case(cid)

    print("\nDone. You can now POST /api/cases/{case_id}/run for each of these again.")


if __name__ == "__main__":
    main()
