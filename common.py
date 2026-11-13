"""Cấu hình chung và hàm tiện ích cho dự án dự báo nhiệt độ, độ ẩm bằng LSTM.

Chân GPIO/cấu hình nền đặt ở đây. Ngưỡng và email nhận có thể ghi đè
bằng tab Cài đặt của dashboard, lưu chung trong SQLite.
"""
import json
import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")


def env_bool(name, default=False):
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} phải là true hoặc false")


def env_int(name, default, minimum=1, maximum=None):
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        raise ValueError(f"{name} phải là số nguyên") from None
    if value < minimum or (maximum is not None and value > maximum):
        limit = f"{minimum}–{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{name} phải trong khoảng {limit}")
    return value


def now_local():
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).replace(tzinfo=None)

# ---------- Chân GPIO (số BCM, KHÔNG phải số pin vật lý) ----------
PIN_DHT = 4          # DHT22 DATA   -> GPIO4  (pin 7)
PIN_LED_GREEN = 17   # LED xanh     -> GPIO17 (pin 11)
PIN_LED_YELLOW = 27  # LED vàng     -> GPIO27 (pin 13)
PIN_LED_RED = 22     # LED đỏ       -> GPIO22 (pin 15)
PIN_BUZZER = 18      # Còi          -> GPIO18 (pin 12)
PIN_BUTTON = 23      # Nút bấm      -> GPIO23 (pin 16)
GPIO_LOCK_PORT = 47931  # Khóa dùng chung giữa monitor và bài kiểm tra mạch

# Còi 3 chân dạng module dùng RPi.GPIO PWM (1000 Hz, duty 50%)
# Lưu ý: Thay thế cờ BUZZER_ACTIVE_HIGH cũ của gpiozero do phần cứng thực tế
# là module còi cần tín hiệu PWM liên tục để phát âm báo, tránh lỗi chỉ kêu 1 tiếng tách.
BUZZER_FREQ = 1000   # Hz – tần số PWM
BUZZER_DUTY = 50     # % – duty cycle khi còi đang kêu

# ---------- Dữ liệu; tên mô hình 10 phút chỉ giữ tương thích công cụ cũ ----------
SAMPLE_SEC  = 60             # chu kỳ đọc cảm biến (giây)
DATA_FILE   = BASE_DIR / "data/sensor_data.csv"
LOG_FILE    = BASE_DIR / "data/monitor_log.csv"
MODEL_FILE  = BASE_DIR / "model/lstm_model.keras"
TFLITE_FILE = BASE_DIR / "model/lstm_model.tflite"
META_FILE   = BASE_DIR / "model/meta.json"
LOOK_BACK   = 30             # số bước quá khứ đưa vào LSTM (30 phút)
HORIZON     = 10             # dự đoán trước bao nhiêu bước (10 phút)

# ---------- Ngưỡng cảnh báo ----------
TEMP_HIGH          = 32.0   # °C
HUM_HIGH           = 90.0   # %
RED_CONSECUTIVE    = 3      # số lần đo liên tiếp vượt ngưỡng → đỏ + còi
YELLOW_CONSECUTIVE = 2      # số lần dự báo liên tiếp vượt ngưỡng → vàng
MUTE_SEC           = 300    # bấm nút tắt còi trong 5 phút

# ---------- Email cảnh báo (dickreuter pattern) ----------
# Tài khoản gửi cấu hình ở .env; email nhận và bật/tắt chỉnh trên dashboard.
EMAIL_ENABLED  = env_bool("EMAIL_ENABLED")
EMAIL_SMTP     = os.getenv("EMAIL_SMTP", "smtp.gmail.com").strip()
EMAIL_PORT     = env_int("EMAIL_PORT", 465, maximum=65535)
EMAIL_FROM     = os.getenv("EMAIL_FROM", "").strip()
EMAIL_TO       = os.getenv("EMAIL_TO", "").strip()
EMAIL_PASS     = os.getenv("EMAIL_PASS", "")
EMAIL_COOLDOWN = env_int("EMAIL_COOLDOWN", 1800)

# ---------- MQTT (HumairaRizwan pattern) ----------
# Đặt MQTT_ENABLED = True nếu có MQTT broker (Mosquitto, Node-RED)
MQTT_ENABLED        = env_bool("MQTT_ENABLED")
MQTT_HOST           = os.getenv("MQTT_HOST", "localhost").strip()
MQTT_PORT           = env_int("MQTT_PORT", 1883, maximum=65535)
MQTT_TOPIC_SENSOR   = "pi/sensor"   # publish đọc cảm biến
MQTT_TOPIC_ALERT    = "pi/alert"    # publish cảnh báo

# ---------- Giám sát 12 giờ: độc lập với mô hình 10 phút cũ ----------
_db_path = os.getenv("IOT_DB_FILE", "").strip()
DB_FILE = Path(_db_path).expanduser() if _db_path else BASE_DIR / "data/iot.sqlite3"
if not DB_FILE.is_absolute():
    DB_FILE = BASE_DIR / DB_FILE
FORECAST_MODEL = BASE_DIR / "model/forecast_12h.keras"
FORECAST_META = BASE_DIR / "model/forecast_12h.json"
FORECAST_STEP_MIN = 10
FORECAST_HOURS = 12
FORECAST_STEPS = FORECAST_HOURS * 60 // FORECAST_STEP_MIN
CONTEXT_STEPS = 24 * 60 // FORECAST_STEP_MIN
MIN_TRAIN_DAYS = 7
FORECAST_REFRESH_SEC = 600
STALE_SEC = SAMPLE_SEC * 3
DASHBOARD_REFRESH_SEC = 5

# Mặc định cho phòng sinh hoạt/phòng thiết bị thông thường; chỉnh theo nơi đặt.
TEMP_RED_LOW = float(os.getenv("TEMP_RED_LOW", "10"))
TEMP_YELLOW_LOW = float(os.getenv("TEMP_YELLOW_LOW", "15"))
TEMP_YELLOW_HIGH = float(os.getenv("TEMP_YELLOW_HIGH", "30"))
TEMP_RED_HIGH = float(os.getenv("TEMP_RED_HIGH", "35"))
HUM_RED_LOW = float(os.getenv("HUM_RED_LOW", "25"))
HUM_YELLOW_LOW = float(os.getenv("HUM_YELLOW_LOW", "35"))
HUM_YELLOW_HIGH = float(os.getenv("HUM_YELLOW_HIGH", "75"))
HUM_RED_HIGH = float(os.getenv("HUM_RED_HIGH", "85"))
for _bounds, _minimum, _maximum in (
    ((TEMP_RED_LOW, TEMP_YELLOW_LOW, TEMP_YELLOW_HIGH, TEMP_RED_HIGH), -40, 80),
    ((HUM_RED_LOW, HUM_YELLOW_LOW, HUM_YELLOW_HIGH, HUM_RED_HIGH), 0, 100),
):
    if not all(math.isfinite(value) and _minimum <= value <= _maximum for value in _bounds):
        raise ValueError("Ngưỡng nhiệt độ phải trong -40–80°C và độ ẩm trong 0–100%")
    if not all(a < b for a, b in zip(_bounds, _bounds[1:])):
        raise ValueError("Ngưỡng phải tăng dần: đỏ thấp < vàng thấp < vàng cao < đỏ cao")


# ---------- Hàm tiện ích ----------
def make_windows(arr, look_back, horizon):
    """Cắt chuỗi (n, 2) thành các cửa sổ trượt.

    X[i] = arr[i : i+look_back]
    y[i] = arr[i + look_back - 1 + horizon]   (giá trị 'horizon' bước sau cửa sổ)
    """
    n = len(arr) - look_back - horizon + 1
    if n <= 0:
        return (np.empty((0, look_back, arr.shape[1]), dtype="float32"),
                np.empty((0, arr.shape[1]), dtype="float32"))
    X = np.stack([arr[i:i + look_back] for i in range(n)]).astype("float32")
    y = np.stack([arr[i + look_back - 1 + horizon] for i in range(n)]).astype("float32")
    return X, y


def normalize(a, lo, hi):
    return (a - lo) / (hi - lo + 1e-8)


def denormalize(a, lo, hi):
    return a * (hi - lo + 1e-8) + lo


def rmse(y_true, y_pred):
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2, axis=0).mean()))


def rmse_per_column(y_true, y_pred):
    return np.sqrt(np.mean((y_true - y_pred) ** 2, axis=0))


def mae_per_column(y_true, y_pred):
    return np.mean(np.abs(y_true - y_pred), axis=0)


def save_meta(path, lo, hi):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"lo": [float(x) for x in lo], "hi": [float(x) for x in hi],
            "look_back": LOOK_BACK, "horizon": HORIZON, "sample_sec": SAMPLE_SEC}
    path.write_text(json.dumps(meta, indent=2))


def load_meta(path):
    meta = json.loads(Path(path).read_text())
    meta["lo"] = np.array(meta["lo"], dtype="float32")
    meta["hi"] = np.array(meta["hi"], dtype="float32")
    return meta
