"""Huấn luyện LSTM dự báo trực tiếp 72 mốc; chọn bằng validation, test chỉ báo cáo."""
import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from common import (CONTEXT_STEPS, FORECAST_META, FORECAST_MODEL, FORECAST_STEP_MIN,
                    FORECAST_STEPS, MIN_TRAIN_DAYS, now_local)
from forecasting import features, regular_series, seasonal_values
from storage import Store


def make_examples(series, lo, hi, target_start, target_end, stride=3):
    """Toàn bộ nhãn của mỗi cửa sổ phải nằm trong đúng tập thời gian."""
    values = series.to_numpy(dtype="float32")
    feat = features(values, series.index, lo, hi)
    scaled = (values - lo) / np.maximum(hi - lo, 1e-6)
    xs, ys, origins = [], [], []
    for end in range(CONTEXT_STEPS, len(series) - FORECAST_STEPS + 1, stride):
        if end < target_start or end + FORECAST_STEPS > target_end:
            continue
        x, y = feat[end - CONTEXT_STEPS:end], scaled[end:end + FORECAST_STEPS]
        if np.isfinite(x).all() and np.isfinite(y).all():
            xs.append(x)
            ys.append(y)
            origins.append(end - 1)
    return (np.array(xs, dtype="float32").reshape(-1, CONTEXT_STEPS, 4),
            np.array(ys, dtype="float32").reshape(-1, FORECAST_STEPS, 2), origins)


def baseline_predictions(series, origins):
    result = []
    for origin in origins:
        stamps = series.index[origin + 1:origin + 1 + FORECAST_STEPS]
        pred, _, _ = seasonal_values(series.iloc[:origin + 1], stamps, series.iloc[origin].to_numpy())
        result.append(pred)
    return np.array(result)


def score(truth, pred):
    error = pred - truth
    return {"rmse": np.sqrt(np.mean(error ** 2, axis=(0, 1))).tolist(),
            "mae": np.mean(np.abs(error), axis=(0, 1)).tolist(),
            "rmse_12h": np.sqrt(np.mean(error[:, -1] ** 2, axis=0)).tolist()}


def eligible_training_frame(frame, include_weather_samples=False, cutoff=None):
    """Không dùng thời điểm chưa xảy ra hoặc ngầm coi mô phỏng là cảm biến."""
    excluded = ["demo", "sample", "forecast_sample"] + ([] if include_weather_samples else ["weather_sample"])
    return frame[(~frame["source"].isin(excluded)) & (frame["time"] <= pd.Timestamp(cutoff or now_local()))].copy()


def train(epochs=60, db_path=None, include_weather_samples=False, skip_csv_import=False, output_dir=None):
    store = Store() if db_path is None else Store(db_path)
    if not skip_csv_import:
        store.import_csv()
    frame = store.frame()
    eligible = eligible_training_frame(frame, include_weather_samples)
    future_excluded = int((frame["time"] > pd.Timestamp(now_local())).sum())
    series = regular_series(eligible)
    good_days = len(series.dropna()) / 144
    if good_days < MIN_TRAIN_DAYS:
        raise SystemExit(f"Cần ít nhất {MIN_TRAIN_DAYS} ngày số đo hợp lệ để huấn luyện 12 giờ; hiện có {good_days:.1f} ngày. "
                         "Giám sát vẫn chạy bằng dự báo chu kỳ. Nên thu 14–30 ngày dữ liệu thực.")
    # Không huấn luyện với các đoạn im lặng dài hoặc dữ liệu giả lập được đánh dấu.
    n = len(series)
    a, b = int(n * .70), int(n * .85)
    lo, hi = series.iloc[:a].min().to_numpy(), series.iloc[:a].max().to_numpy()
    parts = [make_examples(series, lo, hi, start, end) for start, end in ((0, a), (a, b), (b, n))]
    if any(len(x) < 8 for x, _, _ in parts):
        raise SystemExit("Không đủ cửa sổ liên tục trong train/validation/test. Hãy thu thêm dữ liệu.")
    from tensorflow import keras
    keras.utils.set_random_seed(42)
    model = keras.Sequential([
        keras.layers.Input((CONTEXT_STEPS, 4)),
        keras.layers.LSTM(48), keras.layers.Dropout(.15),
        keras.layers.Dense(FORECAST_STEPS * 2),
        keras.layers.Reshape((FORECAST_STEPS, 2)),
    ])
    model.compile(optimizer=keras.optimizers.Adam(1e-3), loss="mse")
    (xtr, ytr, _), (xva, yva, ova), (xte, yte, ote) = parts
    history = model.fit(xtr, ytr, validation_data=(xva, yva), epochs=epochs, batch_size=32,
                        shuffle=False, callbacks=[keras.callbacks.EarlyStopping(
                            patience=8, restore_best_weights=True)], verbose=2)
    scale = np.maximum(hi - lo, 1e-6)
    val_pred = model.predict(xva, verbose=0) * scale + lo
    val_truth = yva * scale + lo
    val_base = baseline_predictions(series, ova)
    val_rmse = np.sqrt(np.mean((val_pred - val_truth) ** 2, axis=(0, 1)))
    base_rmse = np.sqrt(np.mean((val_base - val_truth) ** 2, axis=(0, 1)))
    # So sánh trên từng biến, không cộng trực tiếp °C và %.
    use_lstm = bool(np.all(val_rmse < base_rmse))
    test_truth = yte * scale + lo
    test_pred = model.predict(xte, verbose=0) * scale + lo
    test_base = baseline_predictions(series, ote)
    meta = {"schema": 2, "steps": FORECAST_STEPS, "context": CONTEXT_STEPS,
            "training_data_kind": "weather_derived_simulation" if eligible["source"].eq("weather_sample").any() else "sensor_or_import",
            "source_counts": {str(k): int(v) for k,v in eligible["source"].value_counts().items()},
            "future_rows_excluded": future_excluded, "trained_at": now_local().isoformat(),
            "step_min": FORECAST_STEP_MIN, "lo": lo.tolist(), "hi": hi.tolist(),
            "train_end": series.index[a - 1].isoformat(), "data_end": series.index[-1].isoformat(),
            "use_lstm": use_lstm, "validation_rmse": val_rmse.tolist(),
            "baseline_validation_rmse": base_rmse.tolist(),
            "test_lstm": score(test_truth, test_pred), "test_baseline": score(test_truth, test_base),
            "windows": [len(part[0]) for part in parts],
            "loss": [float(v) for v in history.history["loss"]],
            "val_loss": [float(v) for v in history.history["val_loss"]]}
    model_path = Path(output_dir) / FORECAST_MODEL.name if output_dir else FORECAST_MODEL
    meta_path = Path(output_dir) / FORECAST_META.name if output_dir else FORECAST_META
    model_path.parent.mkdir(parents=True, exist_ok=True)
    # Metadata và model mang tên riêng, không đọc nhầm mô hình 10 phút cũ.
    temporary = model_path.with_name("forecast_12h.next.keras")
    model.save(temporary)
    meta["model_sha256"] = hashlib.sha256(temporary.read_bytes()).hexdigest()
    os.replace(temporary, model_path)
    temporary_meta = meta_path.with_suffix(".next.json")
    temporary_meta.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary_meta, meta_path)
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    print("Đã chọn LSTM." if use_lstm else "LSTM chưa thắng baseline trên cả hai biến; tiếp tục dùng chu kỳ ngày.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--skip-csv-import", action="store_true", help="Dùng nguyên DB đã chuẩn bị, không nhập CSV mặc định vào snapshot")
    parser.add_argument("--output-dir", type=Path, help="Lưu cặp mô hình vào thư mục riêng trước khi triển khai")
    parser.add_argument("--include-weather-samples", action="store_true",
                        help="Cho phép dữ liệu mô phỏng từ khí tượng để thử nghiệm; không coi sai số là kết quả cảm biến thật")
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("epochs phải >= 1")
    train(args.epochs, args.db, args.include_weather_samples, args.skip_csv_import, args.output_dir)
