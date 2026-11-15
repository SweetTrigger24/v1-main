"""Truy vấn dashboard tách khỏi Reflex để kiểm thử lịch sử và dữ liệu cũ."""
import csv
import io
from datetime import datetime, timedelta

import common as config
from alerts import first_red
from storage import Store
from runtime_settings import load_settings


def dashboard_payload(store, hours=12, start=None, end=None, resolution="hour", now=None):
    now = now or config.now_local()
    start = datetime.fromisoformat(start) if start else now - timedelta(days=1)
    end = datetime.fromisoformat(end) if end else now
    if end - start > timedelta(days=366):
        raise ValueError("Chọn khoảng tối đa 366 ngày mỗi lần")
    hours = min(config.FORECAST_HOURS, max(1, int(hours)))
    settings = load_settings(store)
    current = store.latest_live()
    status = store.get("monitor", {})
    fresh = bool(current and 0 <= (now - datetime.fromisoformat(current["time"])).total_seconds() <= config.STALE_SEC)
    ok = fresh and status.get("ok", False)
    forecast = store.get("forecast", {})
    forecast_age = ((now - datetime.fromisoformat(forecast["origin"])).total_seconds()
                    if forecast else float("inf"))
    valid_forecast = bool(ok and 0 <= forecast_age <= config.FORECAST_REFRESH_SEC * 2)
    all_points = [p for p in forecast.get("points", []) if now < datetime.fromisoformat(p["time"]) <= now + timedelta(hours=12)] if valid_forecast else []
    selected = [p for p in all_points if datetime.fromisoformat(p["time"]) <= now + timedelta(hours=hours)]
    red = first_red(all_points, settings["thresholds"])
    history = store.history(start, end, resolution)
    provenance = store.get("provenance:" + str(config.DATA_FILE.resolve()), {})
    sender_ready = all((config.EMAIL_SMTP, config.EMAIL_FROM, config.EMAIL_PASS.strip()))
    email_status = store.get("email_status", {})
    if not settings["email_enabled"]:
        email = "Đã tắt"
    elif not sender_ready:
        email = "Chưa cấu hình tài khoản gửi"
    elif not settings["email_to"]:
        email = "Chưa cấu hình email nhận"
    elif not email_status:
        email = "Đã bật · chờ cảnh báo"
    elif email_status.get("ok"):
        email = "Lần gửi gần nhất thành công"
    else:
        email = "Gửi email lỗi: " + email_status.get("error", "")
    if not current:
        connection = "Chưa có số đo trực tiếp"
    elif not fresh:
        connection = "Mất cập nhật cảm biến"
    elif not status.get("ok"):
        connection = "Lỗi đọc cảm biến"
    elif current["source"] == "demo":
        connection = "MÔ PHỎNG · không phải số đo thật"
    else:
        connection = "Đang nhận dữ liệu cảm biến"
    return {"settings": settings, "sender_ready": sender_ready,
            "range_hours": str(hours), "temp": f"{current['temp']:.1f}" if current else "—",
            "hum": f"{current['hum']:.1f}" if current else "—",
            "state": current["state"] if ok else "CHƯA CÓ DỮ LIỆU MỚI",
            "ok": ok, "connection": connection,
            "updated": current["time"].replace("T", " ") if current else "Chưa có",
            "forecast": selected, "history": history,
            "engine": forecast.get("engine", "Chưa có dự báo") if valid_forecast else "Đang chờ số đo mới",
            "note": forecast.get("note", "") if valid_forecast else "Cần dịch vụ monitor và cảm biến đang hoạt động.",
            "red": (f"Có thể chạm mức đỏ lúc {red['time'].replace('T', ' ')} · {red['temp']:.1f}°C / {red['hum']:.1f}%"
                    if red else "Chưa thấy mốc dự báo chạm đỏ trong 12 giờ" if valid_forecast else "Chưa đủ dữ liệu đánh giá 12 giờ tới"),
            "red_expected": bool(red), "error": status.get("error", "") if not ok else "",
            "provenance": (f"{provenance['weather_source_rows']:,} mẫu nhãn mô phỏng; {provenance['sensor_label_assignment']['rows']:,} mẫu nhãn sensor."
                           if provenance.get("sensor_label_assignment") else
                           f"{provenance['weather_rows']:,} mẫu phút mô phỏng theo {provenance.get('provider', 'khí tượng')} tại {provenance.get('location', 'Hà Đông')}; không phải số đo DHT22 thật."
                           if provenance.get("weather_rows") else
                           f"CSV nhập có {provenance['sample_rows']:,} mẫu mô phỏng đã nhận diện; các mẫu này không dùng huấn luyện LSTM chính thức."
                           if provenance.get("sample_rows") else "Lịch sử nhập từ CSV; cần xác minh nguồn nếu dùng làm thực nghiệm."),
            "email": email,
            "temp_range": (f"{min(p['temp'] for p in selected):.1f} – {max(p['temp'] for p in selected):.1f} °C" if selected else "—"),
            "hum_range": (f"{min(p['hum'] for p in selected):.1f} – {max(p['hum'] for p in selected):.1f} %" if selected else "—")}


def history_csv(records):
    output = io.StringIO()
    fields = ["time", "temp", "temp_min", "temp_max", "hum", "hum_min", "hum_max", "count"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    return output.getvalue()
