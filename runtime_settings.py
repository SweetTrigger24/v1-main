"""Cài đặt vận hành trong SQLite, dùng chung giữa dashboard và monitor."""
import math
import re

import common as config

THRESHOLD_FIELDS = (
    "temp_red_low", "temp_yellow_low", "temp_yellow_high", "temp_red_high",
    "hum_red_low", "hum_yellow_low", "hum_yellow_high", "hum_red_high",
)
SETTINGS_KEY = "runtime_settings"


class SettingsConflict(ValueError):
    pass


def default_thresholds():
    return {field: float(getattr(config, field.upper())) for field in THRESHOLD_FIELDS}


def load_settings(store):
    result = {"thresholds": default_thresholds(), "email_enabled": config.EMAIL_ENABLED,
              "email_to": config.EMAIL_TO, "revision": 0}
    saved = store.get(SETTINGS_KEY, {})
    result["thresholds"].update(saved.get("thresholds", {}))
    for field in ("email_enabled", "email_to", "revision", "updated_at"):
        if field in saved:
            result[field] = saved[field]
    return result


def validate_settings(values):
    thresholds = {}
    supplied = values.get("thresholds", {})
    for field in THRESHOLD_FIELDS:
        try:
            number = float(supplied[field])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Vui lòng nhập đủ 8 ngưỡng bằng số.") from None
        lower, upper = (-40, 80) if field.startswith("temp_") else (0, 100)
        if not math.isfinite(number) or not lower <= number <= upper:
            label = "Nhiệt độ phải trong -40–80°C." if field.startswith("temp_") else "Độ ẩm phải trong 0–100%."
            raise ValueError(label)
        thresholds[field] = number
    for prefix, label in (("temp", "Nhiệt độ"), ("hum", "Độ ẩm")):
        bounds = [thresholds[f"{prefix}_{suffix}"] for suffix in ("red_low", "yellow_low", "yellow_high", "red_high")]
        if not all(a < b for a, b in zip(bounds, bounds[1:])):
            raise ValueError(f"{label}: cần đỏ thấp < vàng thấp < vàng cao < đỏ cao.")
    enabled = values.get("email_enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("Trạng thái email không hợp lệ.")
    recipient = str(values.get("email_to", "")).strip()
    if recipient and (len(recipient) > 254 or not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", recipient)):
        raise ValueError("Địa chỉ email nhận không hợp lệ.")
    if enabled and not recipient:
        raise ValueError("Nhập email nhận trước khi bật cảnh báo email.")
    return {"thresholds": thresholds, "email_enabled": enabled, "email_to": recipient}


def save_settings(store, values, expected_revision):
    import json
    validated = validate_settings(values)
    with store.connection() as db:
        # Giao dịch ngăn hai trình duyệt ghi đè cấu hình của nhau.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT value FROM kv WHERE key=?", (SETTINGS_KEY,)).fetchone()
        old = json.loads(row[0]) if row else {}
        if old.get("revision", 0) != expected_revision:
            raise SettingsConflict("Cài đặt đã thay đổi ở phiên khác. Bấm Tải lại rồi chỉnh lại.")
        validated.update(revision=expected_revision + 1, updated_at=config.now_local().isoformat(timespec="seconds"))
        db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)",
                   (SETTINGS_KEY, json.dumps(validated, ensure_ascii=False, allow_nan=False)))
        old_email = (old.get("email_enabled", config.EMAIL_ENABLED), old.get("email_to", config.EMAIL_TO))
        if old_email != (validated["email_enabled"], validated["email_to"]):
            db.execute("DELETE FROM kv WHERE key IN ('email_state', 'email_status')")
    return validated
