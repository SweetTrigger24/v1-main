"""Dịch vụ thu cảm biến, dự báo 12 giờ, điều khiển cảnh báo và gửi thông báo."""
import argparse
import csv
import json
import logging
import signal
import socket
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from contextlib import closing
from pathlib import Path

import pandas as pd

import common as config
from alerts import AlertMachine
from forecasting import Forecaster
from hardware import DemoHardware, PiHardware
from notifications import EmailNotifier
from storage import Store
from runtime_settings import load_settings

LOG = logging.getLogger("iot")


def validate_demo_database(path):
    """Xét dữ liệu và vai trò của DB, không nhầm IOT_DB_FILE của dashboard là DB thật."""
    target = Path(path).resolve()
    if target == (config.BASE_DIR / "data/iot.sqlite3").resolve():
        raise ValueError("Chế độ demo không được dùng data/iot.sqlite3; hãy chọn data/demo.sqlite3")
    if not target.exists():
        return
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "readings" in tables and db.execute("SELECT 1 FROM readings WHERE source='sensor' LIMIT 1").fetchone():
                raise ValueError("DB này có số đo cảm biến thật; hãy chọn DB demo khác")
            if "kv" in tables:
                role = db.execute("SELECT value FROM kv WHERE key='database_role'").fetchone()
                if role and json.loads(role[0]) == "production":
                    raise ValueError("DB này đã được dùng để vận hành thật; hãy chọn DB demo khác")
    except sqlite3.Error as exc:
        raise ValueError(f"Không kiểm tra được DB demo: {exc}") from exc


def append_csv(path, fields, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if header:
            writer.writerow(fields)
        writer.writerow(values)


class Monitor:
    def __init__(self, store, hardware, demo=False, write_csv=True):
        self.store, self.hardware = store, hardware
        self.demo, self.write_csv = demo, write_csv
        self.machine = AlertMachine()
        self.thresholds = None
        self.forecaster = Forecaster()
        self.notifier = EmailNotifier(store)
        self.forecast = None
        self.mqtt = None
        if config.MQTT_ENABLED and not demo:
            try:
                import paho.mqtt.client as mqtt
                self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-monitor-12h")
                self.mqtt.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
                self.mqtt.loop_start()
            except Exception:
                LOG.exception("MQTT chưa kết nối; vẫn tiếp tục thu cảm biến")

    def step(self, stop, now=None):
        settings = load_settings(self.store)
        thresholds = settings["thresholds"]
        if thresholds != self.thresholds:
            self.machine.sensor_failed()
            self.thresholds = thresholds
        reading = self.hardware.read(stop)
        now = now or config.now_local()
        if reading is None:
            self.machine.sensor_failed()
            self.hardware.set_outputs("ERROR")
            self.store.put("monitor", {"time": now.isoformat(), "ok": False, "mode": "demo" if self.demo else "sensor",
                                       "error": "Không đọc được DHT22 sau 5 lần thử"})
            LOG.warning("Đọc cảm biến lỗi; không tạo số đo giả")
            return
        temp, hum = reading
        source = "demo" if self.demo else "sensor"
        age = float("inf") if not self.forecast else (now - datetime.fromisoformat(self.forecast["origin"])).total_seconds()
        if not 0 <= age < config.FORECAST_REFRESH_SEC:
            try:
                frame = self.store.frame(start=now - timedelta(days=30), end=now)
                latest = pd.DataFrame([{"time": now, "temp": temp, "hum": hum, "source": source}])
                frame = latest if frame.empty else pd.concat([frame, latest], ignore_index=True)
                self.forecast = self.forecaster.predict(frame, now, [temp, hum], thresholds=thresholds)
                self.store.put("forecast", self.forecast)
            except Exception as exc:
                LOG.exception("Lỗi dự báo; tiếp tục cảnh báo theo số đo hiện tại")
                self.store.put("forecast_error", {"time": now.isoformat(), "error": str(exc)})
        forecast_age = ((now - datetime.fromisoformat(self.forecast["origin"])).total_seconds()
                        if self.forecast else float("inf"))
        points = ([p for p in self.forecast["points"] if datetime.fromisoformat(p["time"]) > now]
                  if 0 <= forecast_age <= 2 * config.FORECAST_REFRESH_SEC else [])
        state, reasons = self.machine.update(temp, hum, points, thresholds)
        self.hardware.set_outputs(state)
        self.store.add_reading(now, temp, hum, source, state, reasons)
        self.store.put("monitor", {"time": now.isoformat(), "ok": True, "mode": source})
        data = {"time": now.isoformat(timespec="seconds"), "temp": temp, "hum": hum, "state": state, "source": source}
        if self.write_csv and not self.demo:
            try:
                append_csv(config.DATA_FILE, ["time", "temp", "hum"], [data["time"], round(temp, 1), round(hum, 1)])
                first = points[0] if points else {}
                append_csv(config.LOG_FILE, ["time", "temp", "hum", "pred_temp", "pred_hum", "state"],
                           [data["time"], round(temp, 1), round(hum, 1), first.get("temp", ""), first.get("hum", ""), state])
            except OSError:
                LOG.exception("Không ghi được CSV; số đo đã lưu trong SQLite, tiếp tục xử lý cảnh báo")
        if self.mqtt:
            self.mqtt.publish(config.MQTT_TOPIC_SENSOR, json.dumps(data), qos=1)
            if state != "XANH":
                self.mqtt.publish(config.MQTT_TOPIC_ALERT, json.dumps(data), qos=1)
        self.notifier.evaluate(data, state, self.forecast, now)
        LOG.info("%.1f°C · %.1f%% · %s · %s", temp, hum, state, (self.forecast or {}).get("engine", "Chưa có dự báo"))

    def close(self):
        try:
            self.hardware.close()
        finally:
            if self.mqtt:
                self.mqtt.loop_stop()
                self.mqtt.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Số đo mô phỏng; bắt buộc DB riêng; không gửi email/ghi CSV thật")
    parser.add_argument("--db", type=str)
    parser.add_argument("--interval", type=int, default=config.SAMPLE_SEC)
    parser.add_argument("--once", action="store_true", help="Chạy một lần để kiểm tra")
    args = parser.parse_args()
    if args.interval < 2:
        parser.error("Chu kỳ phải >= 2 giây")
    if args.demo:
        if not args.db:
            parser.error("Chế độ demo cần --db, ví dụ --db data/demo.sqlite3")
        try:
            validate_demo_database(args.db)
        except ValueError as exc:
            parser.error(str(exc))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Khóa tiến trình bằng socket loopback; OS tự nhả khi crash hoặc mất nguồn.
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", config.GPIO_LOCK_PORT))
    except OSError:
        parser.error(f"Monitor hoặc bài kiểm tra mạch đang chạy (cổng khóa {config.GPIO_LOCK_PORT})")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    store = Store(args.db) if args.db else Store()
    store.put("database_role", "demo" if args.demo else "production")
    LOG.info("Đã nhập %d mẫu lịch sử", store.import_csv())
    hardware = DemoHardware() if args.demo else PiHardware()
    monitor = None
    try:
        monitor = Monitor(store, hardware, demo=args.demo)
        while not stop.is_set():
            started = time.monotonic()
            monitor.step(stop)
            if args.once:
                break
            stop.wait(max(0, args.interval - (time.monotonic() - started)))
    finally:
        try:
            if monitor is not None:
                monitor.close()
            else:
                hardware.close()
        finally:
            lock.close()


if __name__ == "__main__":
    main()
