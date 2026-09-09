"""SQLite batch transactions, persistent alarm state and replay-safe receipts."""
import json
from contextlib import contextmanager
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
if __package__ and __package__.startswith("diagnosis."):
    from diagnosis.alarm.alarm_manager import DeviceAlarmState
else:
    from alarm.alarm_manager import DeviceAlarmState

class BatchConflict(ValueError):
    pass

class ServiceBusy(RuntimeError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)

class DatabaseManager:
    def __init__(self, db_path="diagnosis.db", stale_after_seconds=60):
        self.db_path = Path(db_path)
        if str(db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.closed = False
        self.stale_after_seconds = stale_after_seconds
        self.conn = sqlite3.connect(str(db_path), timeout=2, check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        try:
            self._create_tables()
        except BaseException:
            self.conn.close()
            raise

    def _create_tables(self):
        # Keep the original tables and columns so existing history remains readable.
        self.conn.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS diagnosis_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, batch_id TEXT,
            timestamp TEXT, rpm REAL, sampling_rate INTEGER, status TEXT,
            fault_type TEXT, confidence REAL, segment_consistency REAL,
            latency_ms REAL, probability TEXT, decision_reason TEXT);
        CREATE TABLE IF NOT EXISTS alarm_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, timestamp TEXT,
            alarm_level TEXT, fault_type TEXT, message TEXT, fault_count INTEGER);
        CREATE TABLE IF NOT EXISTS device_status (
            device_id TEXT PRIMARY KEY, last_time TEXT, status TEXT,
            alarm_level TEXT, fault_type TEXT);
        CREATE TABLE IF NOT EXISTS alarm_state (
            device_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batch_receipt (
            device_id TEXT NOT NULL, batch_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, result_json TEXT NOT NULL,
            PRIMARY KEY(device_id, batch_id));
        CREATE INDEX IF NOT EXISTS diagnosis_device_id ON diagnosis_result(device_id, id DESC);
        CREATE INDEX IF NOT EXISTS alarm_device_id ON alarm_history(device_id, id DESC);
        """)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(diagnosis_result)")}
        if "result_json" not in columns:
            self.conn.execute("ALTER TABLE diagnosis_result ADD COLUMN result_json TEXT")
        self.conn.commit()

    def _previous_state(self, device_id):
        row = self.conn.execute("SELECT payload FROM alarm_state WHERE device_id=?", (device_id,)).fetchone()
        if row:
            return json.loads(row["payload"])
        # Conservative upgrade of pre-migration alarms; never silently clear an active alarm.
        row = self.conn.execute("SELECT * FROM device_status WHERE device_id=?", (device_id,)).fetchone()
        if row:
            state = DeviceAlarmState(device_id).to_dict()
            state.update(alarm_level=row["alarm_level"] or "NORMAL",
                         fault_type=row["fault_type"] or "", message="迁移历史报警，等待新数据确认")
            return state
        return None

    def process_batch(self, device_id, batch_id, fingerprint, produce):
        if not self._lock.acquire(timeout=2):
            raise ServiceBusy("database is busy")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                receipt = self.conn.execute(
                    "SELECT * FROM batch_receipt WHERE device_id=? AND batch_id=?",
                    (device_id, batch_id)).fetchone()
                if receipt:
                    if receipt["fingerprint"] != fingerprint:
                        raise BatchConflict("batch_id already exists with different content")
                    result = json.loads(receipt["result_json"])
                else:
                    result, alarm = produce(self._previous_state(device_id))
                    self._save_result(result, alarm)
                    self.conn.execute("INSERT INTO batch_receipt VALUES (?,?,?,?)",
                                      (device_id, batch_id, fingerprint, encode(result)))
                self.conn.commit()
                return result
            except BaseException:
                self.conn.rollback()
                raise
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise ServiceBusy("database is busy") from exc
            raise
        finally:
            self._lock.release()

    def _save_result(self, result, alarm):
        keys = ("device_id", "batch_id", "timestamp", "rpm", "sampling_rate", "status",
                "fault_type", "confidence", "segment_consistency", "latency_ms")
        values = [result.get(k) for k in keys]
        values.extend([encode(result.get("probability", {})), result.get("decision_reason", ""), encode(result)])
        self.conn.execute("""INSERT INTO diagnosis_result
            (device_id,batch_id,timestamp,rpm,sampling_rate,status,fault_type,confidence,
             segment_consistency,latency_ms,probability,decision_reason,result_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
        self.conn.execute("""INSERT INTO alarm_history
            (device_id,timestamp,alarm_level,fault_type,message,fault_count) VALUES (?,?,?,?,?,?)""",
            (alarm["device_id"], alarm["last_update"], alarm["alarm_level"], alarm["fault_type"],
             alarm["message"], alarm["fault_count"]))
        self.conn.execute("""INSERT INTO device_status VALUES (?,?,?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET last_time=excluded.last_time,
            status=excluded.status, alarm_level=excluded.alarm_level, fault_type=excluded.fault_type""",
            (result["device_id"], result["timestamp"], result["status"], alarm["alarm_level"], alarm["fault_type"]))
        self.conn.execute("""INSERT INTO alarm_state VALUES (?,?)
            ON CONFLICT(device_id) DO UPDATE SET payload=excluded.payload""",
            (alarm["device_id"], encode(alarm)))

    @contextmanager
    def _read_lock(self):
        if not self._lock.acquire(timeout=2):
            raise ServiceBusy("database is busy")
        try:
            yield
        finally:
            self._lock.release()

    @staticmethod
    def _limit(limit):
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return limit

    def get_latest_results(self, device_id, limit=20):
        with self._read_lock():
            rows = self.conn.execute("SELECT * FROM diagnosis_result WHERE device_id=? ORDER BY id DESC LIMIT ?",
                                     (device_id, self._limit(limit))).fetchall()
            return [json.loads(row["result_json"]) if row["result_json"] else
                    {k: row[k] for k in row.keys() if k != "result_json"} for row in rows]

    def get_alarm_history(self, device_id, limit=50):
        with self._read_lock():
            return [dict(row) for row in self.conn.execute(
                "SELECT * FROM alarm_history WHERE device_id=? ORDER BY id DESC LIMIT ?",
                (device_id, self._limit(limit))).fetchall()]

    def get_device_status(self, device_id):
        with self._read_lock():
            row = self.conn.execute("SELECT * FROM device_status WHERE device_id=?", (device_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        last = datetime.fromisoformat(result["last_time"])
        if last.tzinfo is None:  # old records used local naive timestamps
            last = last.astimezone()
        age = max(0, (datetime.now(timezone.utc)-last).total_seconds())
        result["age_seconds"] = round(age, 2)
        result["stale"] = age > self.stale_after_seconds
        if result["stale"]:
            result["last_diagnosis_status"] = result["status"]
            result["status"] = "STALE"
        return result

    def is_ready(self):
        if not self._lock.acquire(timeout=0.05):
            return not self.closed  # a transaction is in progress
        try:
            return not self.closed and self.conn.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False
        finally:
            self._lock.release()

    def close(self):
        with self._lock:
            if not self.closed:
                self.conn.close()
                self.closed = True
