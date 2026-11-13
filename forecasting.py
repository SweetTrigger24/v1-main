"""Dự báo trực tiếp 72 mốc 10 phút, không lặp mô hình 10 phút cũ 72 lần."""
import hashlib
import json
import logging
from datetime import timedelta

import numpy as np
import pandas as pd

from alerts import classify
from common import (CONTEXT_STEPS, FORECAST_HOURS, FORECAST_META, FORECAST_MODEL,
                    FORECAST_STEP_MIN, FORECAST_STEPS)

LOG = logging.getLogger(__name__)


def regular_series(frame):
    """Giữ khoảng trống dài; chỉ điền tiến tối đa 20 phút, không nhìn tương lai."""
    if frame.empty:
        return pd.DataFrame(columns=["temp", "hum"], index=pd.DatetimeIndex([]))
    df = frame.copy().sort_values("time").drop_duplicates("time").set_index("time")
    last = df.index[-1]
    result = df[["temp", "hum"]].resample(f"{FORECAST_STEP_MIN}min", label="right", closed="right").mean()
    return result.loc[result.index <= last].ffill(limit=2)


def features(values, stamps, lo, hi):
    scaled = (values - lo) / np.maximum(hi - lo, 1e-6)
    phase = np.array([(s.hour * 60 + s.minute) / 1440 * 2 * np.pi for s in stamps])
    return np.column_stack([scaled, np.sin(phase), np.cos(phase)]).astype("float32")


def seasonal_values(history, times, current):
    """Trung vị cùng khung giờ các ngày đã có, hiệu chỉnh theo số đo hiện tại."""
    good = history.dropna()
    if good.empty:
        return np.tile(current, (len(times), 1)), np.array([2.0, 8.0]), False
    slots = good.index.hour * 6 + good.index.minute // FORECAST_STEP_MIN
    profile = good.groupby(slots).median().reindex(range(144))
    # Nội suy theo vòng ngày, chỉ dùng dữ liệu đã nằm trong lịch sử.
    cyclic = pd.concat([profile, profile, profile], ignore_index=True).interpolate(limit_direction="both")
    profile = cyclic.iloc[144:288].to_numpy()
    origin = pd.Timestamp(times[0]) - pd.Timedelta(minutes=FORECAST_STEP_MIN)
    origin_slot = origin.hour * 6 + origin.minute // FORECAST_STEP_MIN
    offset = np.asarray(current) - profile[origin_slot]
    pred = []
    for stamp in times:
        slot = stamp.hour * 6 + stamp.minute // FORECAST_STEP_MIN
        hours = (stamp - origin).total_seconds() / 3600
        pred.append(profile[slot] + offset * np.exp(-hours / 3))
    residual = history.diff(144).dropna().to_numpy()
    spread = np.sqrt(np.mean(residual ** 2, axis=0)) if len(residual) else np.array([2.0, 8.0])
    spread = np.maximum(spread, [0.5, 2.0])
    complete = len(good) >= 144 * 2 and profile.shape == (144, 2)
    return np.array(pred), spread, complete


class Forecaster:
    def __init__(self, model_path=FORECAST_MODEL, meta_path=FORECAST_META):
        self.model_path, self.meta_path = model_path, meta_path
        self._model = self._meta = None
        self._version = None
        self._load_attempted = False
        self.load_error = ""

    def _load(self, context_ready=True):
        if not self.model_path.exists() or not self.meta_path.exists():
            if self._version is not None:
                self._model = self._meta = self._version = None
                self._load_attempted = False
                self.load_error = ""
            return
        version = (self.model_path.stat().st_mtime_ns, self.meta_path.stat().st_mtime_ns)
        if version != self._version:
            self._version = version
            self._model = self._meta = None
            self._load_attempted = False
            self.load_error = ""
            try:
                meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
                if (meta.get("schema") != 2 or meta.get("steps") != FORECAST_STEPS
                        or meta.get("context") != CONTEXT_STEPS or meta.get("step_min") != FORECAST_STEP_MIN):
                    raise ValueError("Mô hình không đúng định dạng dự báo 12 giờ")
                expected_hash = meta.get("model_sha256")
                if expected_hash and hashlib.sha256(self.model_path.read_bytes()).hexdigest() != expected_hash:
                    raise ValueError("Tệp mô hình chưa khớp metadata; cần chép đủ cặp .keras và .json từ cùng lần huấn luyện")
                self._meta = meta
            except Exception as exc:
                self.load_error = str(exc)
                LOG.warning("Metadata LSTM không hợp lệ: %s", exc)
        if (not self._meta or not self._meta.get("use_lstm") or not context_ready
                or self._model is not None or self._load_attempted):
            return
        self._load_attempted = True
        try:
            from tensorflow import keras
            model = keras.models.load_model(self.model_path, compile=False)
            expected_hash = self._meta.get("model_sha256")
            if expected_hash and hashlib.sha256(self.model_path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError("Mô hình thay đổi trong lúc nạp; chờ cặp mô hình/metadata hoàn tất")
            self._model = model
            self.load_error = ""
        except Exception as exc:
            self._model = None
            self.load_error = str(exc)
            LOG.warning("Không nạp được LSTM, dùng dự báo chu kỳ: %s", exc)

    def predict(self, frame, origin, current, thresholds=None):
        # Chặn tuyệt đối dữ liệu tương lai, kể cả khi gọi để backtest.
        frame = frame[frame["time"] <= pd.Timestamp(origin)]
        series = regular_series(frame)
        base = pd.Timestamp(origin).floor(f"{FORECAST_STEP_MIN}min").to_pydatetime()
        times = [base + timedelta(minutes=FORECAST_STEP_MIN * i) for i in range(1, FORECAST_STEPS + 1)]
        history = series.loc[series.index >= base - timedelta(days=30)]
        pred, spread, complete = seasonal_values(history, times, current)
        engine = "Chu kỳ ngày (dự phòng)"
        note = "Dự báo tham khảo theo chu kỳ ngày; cần thêm dữ liệu thực và kiểm định cho mốc 12 giờ."
        context = series.reindex(pd.date_range(end=base, periods=CONTEXT_STEPS, freq=f"{FORECAST_STEP_MIN}min"))
        self._load(context_ready=not context.isna().any().any())
        if self._model is not None and self._meta.get("use_lstm") and not context.isna().any().any():
            try:
                lo, hi = np.array(self._meta["lo"]), np.array(self._meta["hi"])
                x = features(context.to_numpy(), context.index, lo, hi)[None]
                raw = np.asarray(self._model(x, training=False))[0].reshape(FORECAST_STEPS, 2)
                candidate = raw * np.maximum(hi - lo, 1e-6) + lo
                if not np.isfinite(candidate).all():
                    raise ValueError("LSTM trả về giá trị không hữu hạn")
                candidate_spread = np.asarray(self._meta["validation_rmse"], dtype=float)
                if (candidate_spread.shape != (2,) or not np.isfinite(candidate_spread).all()
                        or (candidate_spread < 0).any()):
                    raise ValueError("Sai số kiểm định LSTM phải gồm hai số hữu hạn không âm")
                pred = candidate
                spread = candidate_spread
                engine = "LSTM nhiều bước · 12 giờ"
                note = "Sai số tham khảo từ tập kiểm định; dự báo xa có thể lệch khi môi trường thay đổi."
                self.load_error = ""
            except Exception as exc:
                LOG.warning("Lỗi suy luận LSTM, dùng chu kỳ ngày: %s", exc)
                self.load_error = str(exc)
        if not complete:
            note += " Chưa đủ 2 ngày lịch sử hợp lệ gần đây."
        if "source" in frame and frame["source"].isin(["sample", "demo", "weather_sample"]).any():
            note += " Lịch sử có dữ liệu mô phỏng; phần dự báo chu kỳ chỉ có giá trị tham khảo/minh họa."
        if self._meta and self._meta.get("training_data_kind") == "weather_derived_simulation":
            note += " LSTM đã học dữ liệu mô phỏng theo khí tượng, chưa kiểm định bằng cảm biến thật."
        if series.empty or (pd.Timestamp(origin) - series.dropna().index.max()).total_seconds() > 1800:
            note += " Thiếu chuỗi số đo gần thời điểm hiện tại."
        pred[:, 0] = np.clip(pred[:, 0], -40, 80)
        pred[:, 1] = np.clip(pred[:, 1], 0, 100)
        points = []
        for i, (stamp, values) in enumerate(zip(times, pred)):
            # Đây là dải sai số tham khảo, không tuyên bố khoảng tin cậy đã hiệu chuẩn.
            margin = spread * (1 + (i + 1) / FORECAST_STEPS) * 1.5
            level, reasons = classify(*values, thresholds=thresholds)
            points.append({"time": stamp.isoformat(timespec="seconds"), "label": stamp.strftime("%d/%m %H:%M"),
                           "temp": round(float(values[0]), 2), "hum": round(float(values[1]), 2),
                           "temp_low": round(max(-40, float(values[0] - margin[0])), 2),
                           "temp_high": round(min(80, float(values[0] + margin[0])), 2),
                           "hum_low": round(max(0, float(values[1] - margin[1])), 2),
                           "hum_high": round(min(100, float(values[1] + margin[1])), 2),
                           "state": level, "reasons": reasons})
        return {"origin": origin.isoformat(timespec="seconds"), "hours": FORECAST_HOURS,
                "engine": engine, "note": note, "points": points,
                "model_error": self.load_error}
