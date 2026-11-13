"""Phân mức cảnh báo dùng chung cho phần cứng, dự báo và email."""
from dataclasses import dataclass

from common import RED_CONSECUTIVE, YELLOW_CONSECUTIVE
from runtime_settings import default_thresholds

RANK = {"XANH": 0, "VANG": 1, "DO": 2}


def classify(temp, hum, thresholds=None):
    thresholds = thresholds if thresholds is not None else default_thresholds()
    state, reasons = "XANH", []
    for name, value, bounds, unit in (
        ("Nhiệt độ", temp, tuple(thresholds[f"temp_{key}"] for key in ("red_low", "yellow_low", "yellow_high", "red_high")), "°C"),
        ("Độ ẩm", hum, tuple(thresholds[f"hum_{key}"] for key in ("red_low", "yellow_low", "yellow_high", "red_high")), "%"),
    ):
        red_lo, yellow_lo, yellow_hi, red_hi = bounds
        level = "DO" if value <= red_lo or value >= red_hi else (
            "VANG" if value <= yellow_lo or value >= yellow_hi else "XANH")
        if level != "XANH":
            direction = "thấp" if value <= yellow_lo else "cao"
            reasons.append(f"{name} {direction}: {value:.1f}{unit} ({level})")
        if RANK[level] > RANK[state]:
            state = level
    return state, reasons


def first_red(points, thresholds=None):
    """Tìm lần đỏ đầu tiên trên toàn bộ đường dự báo, kể cả ngưỡng thấp."""
    for point in points:
        state, reasons = classify(point["temp"], point["hum"], thresholds)
        if state == "DO":
            return {**point, "state": state, "reasons": reasons}
    return None


@dataclass
class AlertMachine:
    red_count: int = 0
    forecast_count: int = 0

    def update(self, temp, hum, forecast_points, thresholds=None):
        measured, reasons = classify(temp, hum, thresholds)
        red = first_red(forecast_points, thresholds)
        self.red_count = self.red_count + 1 if measured == "DO" else 0
        self.forecast_count = self.forecast_count + 1 if red else 0
        if self.red_count >= RED_CONSECUTIVE:
            return "DO", reasons
        if measured != "XANH" or self.forecast_count >= YELLOW_CONSECUTIVE:
            return "VANG", reasons or [f"Dự báo chạm mức đỏ lúc {red['time']}"]
        return "XANH", []

    def sensor_failed(self):
        # Không tính hai số đo cách nhau bởi một lần lỗi là liên tiếp.
        self.red_count = self.forecast_count = 0
