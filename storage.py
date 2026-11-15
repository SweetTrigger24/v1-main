"""SQLite WAL: một tiến trình đọc cảm biến, nhiều trình duyệt đọc dashboard."""
import csv
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import pandas as pd

from common import DATA_FILE, DB_FILE
from provenance import dataset_provenance


def readings_with_origins_sql(db):
    """Read old Pi snapshots and the compact schema without mutating either."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(readings)")}
    if "source" in columns:
        return "SELECT time,temp,hum,state,reasons,source AS origin FROM readings"
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "reading_origins" in tables:
        return ("SELECT r.time,r.temp,r.hum,r.state,r.reasons,COALESCE(o.origin,'import') AS origin "
                "FROM readings r LEFT JOIN reading_origins o ON r.time=o.time")
    return "SELECT time,temp,hum,state,reasons,'import' AS origin FROM readings"


class Store:
    def __init__(self, path=DB_FILE):
        self.path = str(path)
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS readings (
                    time TEXT PRIMARY KEY, temp REAL NOT NULL, hum REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'XANH',
                    reasons TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reading_origins (
                    time TEXT PRIMARY KEY REFERENCES readings(time) ON DELETE CASCADE,
                    origin TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(readings)")}
            if "source" in columns:
                # Copy provenance before dropping the visible column. The DML
                # and ALTER are in the same transaction, so failure rolls back.
                db.execute("INSERT OR REPLACE INTO reading_origins SELECT time,source FROM readings")
                db.execute("DROP INDEX IF EXISTS readings_source_time")
                db.execute("ALTER TABLE readings DROP COLUMN source")
            db.execute("CREATE INDEX IF NOT EXISTS reading_origins_kind_time ON reading_origins(origin,time)")
            db.execute("INSERT OR IGNORE INTO reading_origins SELECT time,'import' FROM readings")
        self.remove_simulated_overlaps()

    def remove_simulated_overlaps(self):
        """Xóa mẫu mô phỏng cùng phút đã có sensor, giữ mọi lần đọc thật."""
        removed = 0
        with self.connection() as db:
            minutes = db.execute("SELECT DISTINCT substr(time,1,16) FROM reading_origins WHERE origin='sensor'").fetchall()
            for row in minutes:
                lower = datetime.fromisoformat(row[0]).replace(second=0)
                upper = lower + timedelta(minutes=1)
                removed += db.execute("DELETE FROM readings WHERE time IN (SELECT time FROM reading_origins WHERE origin IN ('sample','weather_sample') AND time>=? AND time<?)",
                                      (lower.isoformat(timespec='seconds'),upper.isoformat(timespec='seconds'))).rowcount
        return removed

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)",
                       (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def add_reading(self, stamp, temp, hum, source="sensor", state="XANH", reasons=None):
        if not (math.isfinite(temp) and math.isfinite(hum) and -40 <= temp <= 80 and 0 <= hum <= 100):
            raise ValueError("Số đo DHT22 không hợp lệ")
        with self.connection() as db:
            if source == "sensor":
                lower = stamp.replace(second=0,microsecond=0)
                upper = lower + timedelta(minutes=1)
                db.execute("DELETE FROM readings WHERE time IN (SELECT time FROM reading_origins WHERE origin IN ('sample','weather_sample') AND time>=? AND time<?)",
                           (lower.isoformat(timespec='seconds'),upper.isoformat(timespec='seconds')))
            db.execute("""INSERT INTO readings(time,temp,hum,state,reasons) VALUES (?, ?, ?, ?, ?)
                          ON CONFLICT(time) DO UPDATE SET temp=excluded.temp,hum=excluded.hum,
                          state=excluded.state,reasons=excluded.reasons""",
                       (stamp.isoformat(timespec="seconds"), temp, hum, state,
                        json.dumps(reasons or [], ensure_ascii=False)))
            db.execute("INSERT INTO reading_origins VALUES (?,?) ON CONFLICT(time) DO UPDATE SET origin=excluded.origin",
                       (stamp.isoformat(timespec="seconds"),source))

    def import_csv(self, path=DATA_FILE, include_sensor=True):
        """Nhập CSV và cập nhật khi đổi bộ mẫu; luôn giữ số đo source=sensor."""
        marker = "import:" + str(path.resolve())
        if not include_sensor:
            marker += ":without-sensor"
        if not path.exists():
            return 0
        provenance_key = "provenance:" + str(path.resolve())
        previous = self.get(provenance_key, {})
        stat = path.stat()
        provenance = dataset_provenance(path)
        fingerprint = [stat.st_size, stat.st_mtime_ns, provenance.get("sensor_label_assignment")]
        if self.get(marker) == fingerprint:
            return 0
        sample_rows = provenance["sample_rows"]
        weather_rows = provenance.get("weather_rows", 0)
        sensor_start = provenance.get("sensor_row_start")
        assignment = provenance.get("sensor_label_assignment")
        count = 0
        with path.open(encoding="utf-8-sig", newline="") as f, self.connection() as db:
            if previous and previous.get("dataset_id") != provenance["dataset_id"]:
                if previous.get("sample_rows"):
                    db.execute("DELETE FROM readings WHERE time IN (SELECT time FROM reading_origins WHERE origin='sample')")
                if previous.get("weather_rows") and previous.get("start") and previous.get("end"):
                    db.execute("DELETE FROM readings WHERE time IN (SELECT time FROM reading_origins WHERE origin='weather_sample' AND time BETWEEN ? AND ?)",
                               (previous["start"], previous["end"]))
            protected = dict(db.execute("SELECT time,origin FROM reading_origins WHERE origin IN ('sensor','demo')"))
            sensor_minutes = {stamp[:16] for stamp,origin in protected.items() if origin == "sensor"}
            for i, row in enumerate(csv.DictReader(f)):
                try:
                    stamp = datetime.fromisoformat(row["time"]).isoformat(timespec="seconds")
                    t, h = float(row["temp"]), float(row["hum"])
                    if not (math.isfinite(t) and math.isfinite(h) and -40 <= t <= 80 and 0 <= h <= 100):
                        continue
                    source = "weather_sample" if i < weather_rows else "sample" if i < sample_rows else "import"
                    if (assignment is not None and i < weather_rows and row.get("label") == "sensor"
                            and assignment["start_exclusive"] < stamp <= assignment["end"]):
                        source = "sensor"
                    if sensor_start is not None and i >= sensor_start and row.get("label") == "sensor":
                        source = "sensor"
                    if source == "sensor" and not include_sensor:
                        continue
                    if stamp in protected or (source in {"sample", "weather_sample"} and stamp[:16] in sensor_minutes):
                        continue
                    if source == "sensor":
                        lower = datetime.fromisoformat(stamp).replace(second=0)
                        upper = lower + timedelta(minutes=1)
                        db.execute("DELETE FROM readings WHERE time IN (SELECT time FROM reading_origins WHERE origin IN ('sample','weather_sample') AND time>=? AND time<?)",
                                   (lower.isoformat(timespec='seconds'),upper.isoformat(timespec='seconds')))
                        sensor_minutes.add(stamp[:16])
                    # Cho phép thay dữ liệu nhập/mẫu tại cùng timestamp, không đè số đo thật/demo.
                    cur = db.execute("""INSERT INTO readings(time,temp,hum,state,reasons) VALUES (?, ?, ?, 'XANH', '[]')
                        ON CONFLICT(time) DO UPDATE SET temp=excluded.temp, hum=excluded.hum,
                            state=excluded.state,reasons=excluded.reasons""",(stamp, t, h))
                    db.execute("INSERT INTO reading_origins VALUES (?,?) ON CONFLICT(time) DO UPDATE SET origin=excluded.origin",
                               (stamp,source))
                    count += cur.rowcount
                except (ValueError, KeyError, TypeError):
                    continue
        self.put(provenance_key, provenance)
        self.put(marker, fingerprint)
        return count

    def frame(self, start=None, end=None):
        clauses, params = [], []
        for op, val in ((">=", start), ("<=", end)):
            if val is not None:
                clauses.append(f"time {op} ?")
                params.append(val.isoformat(timespec="seconds"))
        with self.connection() as db:
            query = "SELECT * FROM (" + readings_with_origins_sql(db) + ")" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY time"
            frame = pd.read_sql_query(query, db, params=params)
        frame = frame.rename(columns={"origin":"source"})[["time","temp","hum","source","state","reasons"]]
        frame["time"] = pd.to_datetime(frame["time"])
        return frame

    def history(self, start, end, resolution="hour"):
        if start > end:
            raise ValueError("Thời gian bắt đầu phải trước thời gian kết thúc")
        rules = {"minute": "5min", "hour": "h", "day": "D"}
        if resolution not in rules:
            raise ValueError("Độ phân giải không hợp lệ")
        df = self.frame(start, end)
        if df.empty:
            return []
        minutes = {"minute": 5, "hour": 60, "day": 1440}[resolution]
        span_minutes = (df["time"].max() - df["time"].min()).total_seconds() / 60
        multiplier = max(1, math.ceil((span_minutes / minutes + 1) / 1500))
        # Khi khoảng dài, gộp tất cả số đo vào bin lớn hơn; không bỏ mất đỉnh/đáy.
        rule = rules[resolution] if multiplier == 1 else f"{minutes * multiplier}min"
        grouped = df.set_index("time")[["temp", "hum"]].resample(rule).agg(["mean", "min", "max", "count"])
        result = []
        for stamp, row in grouped.iterrows():
            item = {"time": stamp.isoformat(), "label": stamp.strftime("%d/%m %H:%M"),
                    "count": int(row[("temp", "count")])}
            for col in ("temp", "hum"):
                for stat in ("mean", "min", "max"):
                    val = row[(col, stat)]
                    item[col if stat == "mean" else f"{col}_{stat}"] = None if pd.isna(val) else round(float(val), 2)
            result.append(item)
        return result

    def latest_live(self):
        with self.connection() as db:
            row = db.execute("SELECT * FROM (" + readings_with_origins_sql(db) + ") WHERE origin IN ('sensor','demo') ORDER BY time DESC LIMIT 1").fetchone()
        if row:
            result = dict(row)
            result["source"] = result.pop("origin")
            return result
        return None
