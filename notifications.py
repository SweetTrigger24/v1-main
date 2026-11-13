"""Email đỏ hiện tại / đỏ trong 12 giờ, chống lặp và lưu trạng thái qua khởi động."""
import smtplib
import ssl
from datetime import datetime
from email.mime.text import MIMEText

import common as config
from alerts import classify, first_red
from runtime_settings import load_settings


class EmailNotifier:
    def __init__(self, store, sender=None):
        self.store = store
        self.sender = sender or self._send

    def _send(self, subject, body):
        settings = load_settings(self.store)
        recipient = settings["email_to"]
        if not all((config.EMAIL_FROM, recipient, config.EMAIL_PASS)):
            raise ValueError("Chưa cấu hình đầy đủ tài khoản gửi và email nhận")
        message = MIMEText(body, "plain", "utf-8")
        message["Subject"], message["From"], message["To"] = subject, config.EMAIL_FROM, recipient
        with smtplib.SMTP_SSL(config.EMAIL_SMTP, config.EMAIL_PORT, timeout=10,
                              context=ssl.create_default_context()) as smtp:
            smtp.login(config.EMAIL_FROM, config.EMAIL_PASS)
            smtp.send_message(message)

    def evaluate(self, reading, measured_state, forecast, now=None):
        settings = load_settings(self.store)
        if not settings["email_enabled"] or reading.get("source") != "sensor":
            return
        now = now or config.now_local()
        if reading.get("time") and not 0 <= (now - datetime.fromisoformat(reading["time"])).total_seconds() <= config.STALE_SEC:
            return
        # Không gửi dự báo đã cũ hoặc dự báo ngoài cửa sổ 12 giờ.
        points = []
        if forecast:
            age = (now - datetime.fromisoformat(forecast["origin"])).total_seconds()
            if 0 <= age <= config.FORECAST_REFRESH_SEC * 2:
                points = [p for p in forecast["points"]
                          if 0 < (datetime.fromisoformat(p["time"]) - now).total_seconds() <= 43200]
        future_red = first_red(points, settings["thresholds"])
        kind = "current_red" if measured_state == "DO" else ("future_red" if future_red else None)
        states = self.store.get("email_state", {})
        for key in ("current_red", "future_red"):
            state = states.setdefault(key, {"active": False, "sent": 0, "attempt": 0})
            if key != kind:
                state["active"] = False
        if kind is None:
            self.store.put("email_state", states)
            return
        state = states[kind]
        timestamp = now.timestamp()
        due = not state["active"] or timestamp - state["sent"] >= config.EMAIL_COOLDOWN
        if not due or timestamp - state["attempt"] < 60:
            return
        state["attempt"] = timestamp
        if kind == "current_red":
            subject = "[IoT] ĐỎ: môi trường hiện tại vượt ngưỡng"
            _, reasons = classify(reading["temp"], reading["hum"], settings["thresholds"])
            detail = "\n".join(reasons)
        else:
            subject = "[IoT] Dự báo chạm mức ĐỎ trong 12 giờ tới"
            detail = (f"Dự kiến lúc {future_red['time']}: {future_red['temp']:.1f}°C, "
                      f"{future_red['hum']:.1f}%\n" + "\n".join(future_red["reasons"]))
        body = (f"Thời điểm: {now:%d/%m/%Y %H:%M:%S} (Việt Nam)\n"
                f"Hiện tại: {reading['temp']:.1f}°C, {reading['hum']:.1f}%\n{detail}")
        try:
            self.sender(subject, body)
            state["sent"] = timestamp
            # Chỉ bắt đầu cooldown của đợt mới sau khi gửi thành công.
            # Nếu SMTP lỗi, giữ active=False để lần đo sau vẫn được thử lại.
            state["active"] = True
            self.store.put("email_status", {"ok": True, "time": now.isoformat(), "kind": kind})
        except Exception as exc:
            # Không đánh dấu đã gửi khi SMTP lỗi; lần đo tiếp theo sẽ thử lại.
            self.store.put("email_status", {"ok": False, "time": now.isoformat(), "error": str(exc)})
        finally:
            self.store.put("email_state", states)
