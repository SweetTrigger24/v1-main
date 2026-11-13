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
                    source TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'XANH',
                    reasons TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
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
            db.execute("INSERT OR REPLACE INTO readings VALUES (?, ?, ?, ?, ?, ?)",
                       (stamp.isoformat(timespec="seconds"), temp, hum, source, state,
                        json.dumps(reasons or [], ensure_ascii=False)))

    def import_csv(self, path=DATA_FILE):
        """Nhập CSV và cập nhật khi đổi bộ mẫu; luôn giữ số đo source=sensor."""
        marker = "import:" + str(path.resolve())
        if not path.exists():
            return 0
        provenance_key = "provenance:" + str(path.resolve())
        previous = self.get(provenance_key, {})
        stat = path.stat()
        fingerprint = [stat.st_size, stat.st_mtime_ns]
        if self.get(marker) == fingerprint:
            return 0
        provenance = dataset_provenance(path)
        sample_rows = provenance["sample_rows"]
        weather_rows = provenance.get("weather_rows", 0)
        count = 0
        with path.open(encoding="utf-8-sig", newline="") as f, self.connection() as db:
            if previous and previous.get("dataset_id") != provenance["dataset_id"]:
                if previous.get("sample_rows"):
                    db.execute("DELETE FROM readings WHERE source='sample'")
                if previous.get("weather_rows") and previous.get("start") and previous.get("end"):
                    db.execute("DELETE FROM readings WHERE source='weather_sample' AND time BETWEEN ? AND ?",
                               (previous["start"], previous["end"]))
            for i, row in enumerate(csv.DictReader(f)):
                try:
                    stamp = datetime.fromisoformat(row["time"]).isoformat(timespec="seconds")
                    t, h = float(row["temp"]), float(row["hum"])
                    if not (math.isfinite(t) and math.isfinite(h) and -40 <= t <= 80 and 0 <= h <= 100):
                        continue
                    source = "weather_sample" if i < weather_rows else "sample" if i < sample_rows else "import"
                    # Cho phép thay dữ liệu nhập/mẫu tại cùng timestamp, không đè số đo thật/demo.
                    cur = db.execute("""INSERT INTO readings VALUES (?, ?, ?, ?, 'XANH', '[]')
                        ON CONFLICT(time) DO UPDATE SET temp=excluded.temp, hum=excluded.hum,
                            source=excluded.source WHERE readings.source NOT IN ('sensor','demo')""",
                                     (stamp, t, h, source))
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
        query = "SELECT * FROM readings" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY time"
        with self.connection() as db:
            frame = pd.read_sql_query(query, db, params=params)
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
            row = db.execute("SELECT * FROM readings WHERE source IN ('sensor','demo') ORDER BY time DESC LIMIT 1").fetchone()
        return dict(row) if row else None
