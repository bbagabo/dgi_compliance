"""Local journal of the gateway (SQLite, WAL, synchronous=FULL).

Every operation is recorded BEFORE any command is sent to the MCF, and its result is written
BEFORE it is sent to ERPNext. After a crash, power cut or restart, the gateway reads the
journal first: an operation left in OPENING / FINALIZING / UNCERTAIN is reconciled with the MCF
(C1h) before any new operation is accepted.

States
  RECEIVED    lease received, nothing sent to the MCF yet
  OPENING     C3h/C0h/31h... sent, 38h NOT sent: the invoice is certainly not normalized
  FINALIZING  38h sent: the invoice may be normalized
  DONE        normalized, security elements stored in result_json
  REJECTED    certainly not normalized (error before 38h, cancelled, refused)
  UNCERTAIN   38h sent and the MCF state could not be read
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

OPEN_STATES = ("RECEIVED", "OPENING", "FINALIZING", "UNCERTAIN")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("""CREATE TABLE IF NOT EXISTS operations (
                op_id TEXT PRIMARY KEY,
                op_type TEXT NOT NULL,
                fn TEXT,
                state TEXT NOT NULL,
                lease_token TEXT,
                payload_json TEXT,
                result_json TEXT,
                opened_counters TEXT,
                delivered_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS op_fn ON operations (fn, state)")
            c.execute("CREATE INDEX IF NOT EXISTS op_state ON operations (state)")

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA synchronous=FULL")
        return c

    @staticmethod
    def _row(r):
        if not r:
            return None
        d = dict(r)
        for k in ("payload_json", "result_json", "opened_counters"):
            d[k[:-5] if k.endswith("_json") else k] = json.loads(d.pop(k)) if d.get(k) else None
        return d

    def get(self, op_id: str):
        with self._lock, self._connect() as c:
            return self._row(c.execute("SELECT * FROM operations WHERE op_id=?", (op_id,)).fetchone())

    def record_received(self, op: dict) -> dict:
        """Idempotent: a lease received twice keeps the existing state."""
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM operations WHERE op_id=?", (op["operation_id"],)).fetchone()
            if row:
                c.execute("UPDATE operations SET lease_token=?, updated_at=? WHERE op_id=?",
                          (op.get("lease_token"), _now(), op["operation_id"]))
                c.execute("COMMIT")
                return self._row(row)
            fn = (op.get("invoice") or {}).get("fn") or op.get("fn")
            c.execute("INSERT INTO operations (op_id, op_type, fn, state, lease_token, payload_json, created_at, updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (op["operation_id"], op.get("op_type") or "NORMALIZE", fn, "RECEIVED",
                       op.get("lease_token"), json.dumps(op, ensure_ascii=False), _now(), _now()))
            c.execute("COMMIT")
        return self.get(op["operation_id"])

    def set_state(self, op_id: str, state: str, *, result=None, opened_counters=None):
        with self._lock, self._connect() as c:
            sets, args = ["state=?", "updated_at=?"], [state, _now()]
            if result is not None:
                sets.append("result_json=?")
                args.append(json.dumps(result, ensure_ascii=False))
                sets.append("delivered_at=NULL")
            if opened_counters is not None:
                sets.append("opened_counters=?")
                args.append(json.dumps(opened_counters))
            args.append(op_id)
            c.execute(f"UPDATE operations SET {', '.join(sets)} WHERE op_id=?", args)

    def find_done_by_fn(self, fn: str):
        with self._lock, self._connect() as c:
            return self._row(c.execute(
                "SELECT * FROM operations WHERE fn=? AND state='DONE' ORDER BY updated_at DESC LIMIT 1",
                (fn,)).fetchone())

    def open_operations(self) -> list:
        with self._lock, self._connect() as c:
            q = "SELECT * FROM operations WHERE state IN (%s) ORDER BY created_at" % ",".join("?" * len(OPEN_STATES))
            return [self._row(r) for r in c.execute(q, OPEN_STATES).fetchall()]

    def undelivered(self) -> list:
        with self._lock, self._connect() as c:
            return [self._row(r) for r in c.execute(
                "SELECT * FROM operations WHERE result_json IS NOT NULL AND delivered_at IS NULL "
                "AND state IN ('DONE','REJECTED','UNCERTAIN') ORDER BY updated_at").fetchall()]

    def mark_delivered(self, op_id: str):
        with self._lock, self._connect() as c:
            c.execute("UPDATE operations SET delivered_at=? WHERE op_id=?", (_now(), op_id))
