"""
SQLite storage for Telegram Geo Inspector.
Data directory with 0700 permissions, files with 0600 permissions.
Supports geo_points, data_gaps, and cached_streams.
"""

import sqlite3
import json
import os
from typing import List, Dict, Optional, Any

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS geo_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    point_type TEXT NOT NULL, -- 'live' or 'static'
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    accuracy REAL,            -- horizontal_accuracy (0-1500m)
    heading INTEGER,          -- 0-360 degrees
    live_period INTEGER,      -- seconds or 0x7FFFFFFF
    edit_date INTEGER NOT NULL, -- Telegram message edit time (unix seconds)
    received_at INTEGER NOT NULL, -- local app arrival time (unix milliseconds)
    edit_number INTEGER NOT NULL,
    distance_from_prev REAL,
    speed_kmh REAL,
    bearing_deg REAL,
    anomaly_score INTEGER DEFAULT 0,
    anomaly_flags TEXT,       -- JSON array of flags
    is_after_gap INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS data_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id TEXT NOT NULL,
    gap_start_ts INTEGER NOT NULL,
    gap_end_ts INTEGER NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cached_streams (
    contact_id TEXT PRIMARY KEY,
    contact_name TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    point_type TEXT NOT NULL,
    is_live INTEGER NOT NULL,
    is_indefinite INTEGER NOT NULL,
    live_period INTEGER,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    accuracy REAL,
    heading INTEGER,
    last_edit_date INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contact_time ON geo_points(contact_id, edit_date);
CREATE INDEX IF NOT EXISTS idx_chat_message ON geo_points(chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_gaps_contact ON data_gaps(contact_id);
"""

class Database:
    def __init__(self, db_path: str = "data/tg_geo_history.sqlite3"):
        self.db_path = db_path
        self._ensure_secure_storage()
        self._init_db()

    def _ensure_secure_storage(self):
        # Create directory with 0700 permissions
        data_dir = os.path.dirname(self.db_path) or "data"
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, mode=0o700, exist_ok=True)
        elif os.name == "posix":
            try:
                os.chmod(data_dir, 0o700)
            except OSError:
                pass

    def _secure_file_perms(self):
        if os.name == "posix" and os.path.exists(self.db_path):
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass

    def _get_connection(self) -> sqlite3.Connection:
        self._secure_file_perms()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript(DB_SCHEMA)
            # Add is_after_gap column if upgrading older db
            try:
                conn.execute("ALTER TABLE geo_points ADD COLUMN is_after_gap INTEGER DEFAULT 0;")
            except Exception:
                pass
        self._secure_file_perms()

    def insert_point(self, point: Dict[str, Any], evaluation: Any) -> int:
        flags_json = json.dumps([
            {
                "rule_id": f.rule_id,
                "rule_name": f.rule_name,
                "weight": f.weight,
                "explanation": f.explanation,
                "observed_value": f.observed_value,
                "threshold": f.threshold
            }
            for f in evaluation.flags
        ], ensure_ascii=False)

        sql = """
        INSERT INTO geo_points (
            contact_id, contact_name, chat_id, message_id, point_type,
            lat, lon, accuracy, heading, live_period,
            edit_date, received_at, edit_number,
            distance_from_prev, speed_kmh, bearing_deg,
            anomaly_score, anomaly_flags, is_after_gap
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            str(point["contact_id"]),
            point.get("contact_name", "Unknown"),
            str(point["chat_id"]),
            point["message_id"],
            point.get("point_type", "live"),
            point["lat"],
            point["lon"],
            point.get("accuracy"),
            point.get("heading"),
            point.get("live_period"),
            point["edit_date"],
            point["received_at"],
            point.get("edit_number", 1),
            evaluation.distance_from_prev_m,
            evaluation.speed_kmh,
            evaluation.bearing_deg,
            evaluation.score,
            flags_json,
            1 if getattr(evaluation, "is_after_gap", False) or point.get("is_after_gap") else 0
        )

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return cur.lastrowid

    def insert_gap(self, contact_id: str, start_ts: int, end_ts: int, reason: str = "app_offline"):
        sql = """
        INSERT INTO data_gaps (contact_id, gap_start_ts, gap_end_ts, reason)
        VALUES (?, ?, ?, ?)
        """
        with self._get_connection() as conn:
            conn.execute(sql, (str(contact_id), int(start_ts), int(end_ts), reason))
            conn.commit()

    def get_gaps(self, contact_id: str) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM data_gaps WHERE contact_id = ? ORDER BY gap_start_ts ASC"
        with self._get_connection() as conn:
            rows = conn.execute(sql, (str(contact_id),)).fetchall()
            return [dict(r) for r in rows]

    def cache_streams(self, streams: List[Dict[str, Any]]):
        sql = """
        INSERT OR REPLACE INTO cached_streams (
            contact_id, contact_name, chat_id, message_id,
            point_type, is_live, is_indefinite, live_period,
            lat, lon, accuracy, heading, last_edit_date, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        import time
        now = int(time.time())
        with self._get_connection() as conn:
            for s in streams:
                conn.execute(sql, (
                    str(s["contact_id"]),
                    s["contact_name"],
                    str(s["chat_id"]),
                    s["message_id"],
                    s["point_type"],
                    1 if s.get("is_live") else 0,
                    1 if s.get("is_indefinite") else 0,
                    s.get("live_period"),
                    s["lat"],
                    s["lon"],
                    s.get("accuracy"),
                    s.get("heading"),
                    s["edit_date"],
                    now
                ))
            conn.commit()

    def get_cached_streams(self) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM cached_streams ORDER BY updated_at DESC"
        with self._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_contact_history(
        self,
        contact_id: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        only_anomalies: bool = False
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM geo_points WHERE contact_id = ?"
        params: List[Any] = [str(contact_id)]

        if start_ts is not None:
            query += " AND edit_date >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND edit_date <= ?"
            params.append(end_ts)
        if only_anomalies:
            query += " AND anomaly_score >= 20"

        query += " ORDER BY edit_date ASC, edit_number ASC"

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        results = []
        for r in rows:
            d = dict(r)
            if d.get("anomaly_flags"):
                try:
                    d["anomaly_flags"] = json.loads(d["anomaly_flags"])
                except Exception:
                    d["anomaly_flags"] = []
            else:
                d["anomaly_flags"] = []
            results.append(d)
        return results

    def get_recent_points_for_evaluation(self, contact_id: str, limit: int = 35) -> List[Dict[str, Any]]:
        sql = """
        SELECT * FROM geo_points
        WHERE contact_id = ?
        ORDER BY edit_date DESC, edit_number DESC
        LIMIT ?
        """
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (str(contact_id), limit))
            rows = cur.fetchall()
        rows = list(reversed(rows))
        results = []
        for r in rows:
            d = dict(r)
            if d.get("anomaly_flags"):
                try:
                    d["anomaly_flags"] = json.loads(d["anomaly_flags"])
                except Exception:
                    d["anomaly_flags"] = []
            else:
                d["anomaly_flags"] = []
            results.append(d)
        return results

    def delete_contact_history(self, contact_id: str) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM geo_points WHERE contact_id = ?", (str(contact_id),))
            deleted = cur.rowcount
            cur.execute("DELETE FROM data_gaps WHERE contact_id = ?", (str(contact_id),))
            cur.execute("DELETE FROM cached_streams WHERE contact_id = ?", (str(contact_id),))
            conn.commit()
            return deleted
