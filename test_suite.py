"""Kiểm thử hành vi thực: cảnh báo, dự báo, lưu trữ, email và dữ liệu dashboard."""
import json
import importlib.util
import threading
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import common as config
from alerts import AlertMachine, classify, first_red
from dashboard_data import dashboard_payload, history_csv
from forecasting import Forecaster, regular_series
from monitor import Monitor, validate_demo_database
from notifications import EmailNotifier
from storage import Store
from train_forecast import make_examples
from provenance import generated_prefix, dataset_provenance
from generate_weather_data import synthesize, write_dataset
from pi_deploy import source_digest, validate_frontend, nginx_config, COMPILE_INPUTS, REFLEX_VERSION
from runtime_settings import load_settings, save_settings, SettingsConflict
import dashboard_auth

NOW = datetime(2026, 11, 10, 12)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.sqlite3")


def sample_frame(days=3, end=NOW):
    stamps = pd.date_range(end=end, periods=days * 144, freq="10min")
    phase = (stamps.hour * 60 + stamps.minute) / 1440 * np.pi * 2
    return pd.DataFrame({"time": stamps, "temp": 27 + np.sin(phase) * 3, "hum": 60 - np.sin(phase) * 10})


def forecast_point(t=28, h=60, minutes=30):
    level, reasons = classify(t, h)
    return {"time": (NOW + timedelta(minutes=minutes)).isoformat(), "temp": t, "hum": h,
            "state": level, "reasons": reasons}


@pytest.mark.parametrize("temp,hum,expected", [
    (26, 55, "XANH"), (15, 55, "VANG"), (30, 55, "VANG"), (10, 55, "DO"), (35, 55, "DO"),
    (26, 35, "VANG"), (26, 75, "VANG"), (26, 25, "DO"), (26, 85, "DO"), (10, 95, "DO"),
])
def test_classify_low_high_boundaries(temp, hum, expected):
    assert classify(temp, hum)[0] == expected


def test_confirmation_recovery_and_sensor_gap():
    machine = AlertMachine()
    assert machine.update(36, 60, [])[0] == "VANG"
    assert machine.update(36, 60, [])[0] == "VANG"
    assert machine.update(36, 60, [])[0] == "DO"
    assert machine.update(26, 60, [])[0] == "XANH"
    machine.update(36, 60, [])
    machine.update(36, 60, [])
    machine.sensor_failed()
    assert machine.update(36, 60, [])[0] == "VANG"


def test_future_low_humidity_yellow_and_red_priority():
    machine = AlertMachine()
    points = [forecast_point(), forecast_point(h=20, minutes=720)]
    assert first_red(points) == points[-1]
    assert machine.update(26, 50, points)[0] == "XANH"
    assert machine.update(26, 50, points)[0] == "VANG"
    for _ in range(3):
        result = machine.update(36, 50, points)
    assert result[0] == "DO"


def test_import_invalid_idempotent_and_never_current(store, tmp_path):
    path = tmp_path / "old.csv"
    path.write_text("time,temp,hum\n2026-11-01T10:00:00,25,60\nBAD,30,60\n2026-11-01T11:00:00,nan,50\n", encoding="utf-8")
    assert store.import_csv(path) == 1
    assert store.import_csv(path) == 0
    assert len(store.frame()) == 1
    assert store.latest_live() is None


def test_history_hour_day_filters_and_gaps(store):
    store.add_reading(NOW, 20, 40)
    store.add_reading(NOW + timedelta(minutes=30), 30, 60)
    store.add_reading(NOW + timedelta(hours=2), 25, 50)
    rows = store.history(NOW, NOW + timedelta(hours=3), "hour")
    assert rows[0]["temp"] == 25
    assert rows[0]["temp_min"] == 20 and rows[0]["temp_max"] == 30
    assert rows[1]["temp"] is None and rows[1]["count"] == 0
    daily = store.history(NOW, NOW + timedelta(hours=3), "day")
    assert len(daily) == 1 and daily[0]["count"] == 3
    filtered = store.history(NOW + timedelta(minutes=10), NOW + timedelta(hours=1))
    assert filtered[0]["temp"] == 30
    assert "temp_min" in history_csv(rows)


def test_history_invalid_range(store):
    with pytest.raises(ValueError):
        store.history(NOW, NOW - timedelta(hours=1))


def test_forecast_72_real_future_times_and_no_future_leak(tmp_path):
    forecaster = Forecaster(tmp_path / "absent.keras", tmp_path / "absent.json")
    frame = sample_frame()
    result = forecaster.predict(frame, NOW, [27, 60])
    assert len(result["points"]) == 72
    stamps = [datetime.fromisoformat(p["time"]) for p in result["points"]]
    assert stamps[0] == NOW + timedelta(minutes=10)
    assert stamps[-1] == NOW + timedelta(hours=12)
    assert all(0 <= p["hum_low"] <= p["hum_high"] <= 100 for p in result["points"])
    future = pd.DataFrame({"time": [NOW + timedelta(hours=1)], "temp": [80], "hum": [0]})
    assert forecaster.predict(pd.concat([frame, future]), NOW, [27, 60])["points"] == result["points"]


def test_long_gaps_not_filled():
    frame = sample_frame().drop(index=range(100, 150))
    series = regular_series(frame)
    assert series.isna().any().any()


def test_bad_model_metadata_falls_back(tmp_path):
    model, meta = tmp_path / "model.keras", tmp_path / "meta.json"
    model.write_bytes(b"not a keras model")
    meta.write_text('{"schema":1}')
    result = Forecaster(model, meta).predict(sample_frame(), NOW, [27, 60])
    assert len(result["points"]) == 72 and "dự phòng" in result["engine"]
    assert result["model_error"]


def test_model_inference_failure_falls_back(tmp_path):
    forecaster = Forecaster(tmp_path / "missing", tmp_path / "missing.json")
    def broken(*args, **kwargs):
        raise RuntimeError("model failure")
    forecaster._model = broken
    forecaster._meta = {"use_lstm": True, "lo": [10, 20], "hi": [40, 90]}
    result = forecaster.predict(sample_frame(), NOW, [27, 60])
    assert len(result["points"]) == 72 and "dự phòng" in result["engine"]


def test_temporal_training_targets_do_not_cross_split():
    series = regular_series(sample_frame(days=16))
    split = int(len(series) * .7)
    lo, hi = series.iloc[:split].min().to_numpy(), series.iloc[:split].max().to_numpy()
    x, y, origins = make_examples(series, lo, hi, 0, split)
    assert x.shape[1:] == (144, 4) and y.shape[1:] == (72, 2)
    assert all(i + 72 < split for i in origins)
    _, _, val_origins = make_examples(series, lo, hi, split, int(len(series) * .85))
    assert all(i + 1 >= split for i in val_origins)
    expected = (series.iloc[origins[0] + 1].to_numpy() - lo) / (hi - lo)
    np.testing.assert_allclose(y[0, 0], expected, atol=1e-5)


def test_dashboard_does_not_show_archived_or_stale_as_live(store):
    store.add_reading(NOW - timedelta(days=1), 28, 60, source="import")
    assert dashboard_payload(store, now=NOW)["temp"] == "—"
    store.add_reading(NOW - timedelta(minutes=10), 36, 60, state="DO")
    store.put("monitor", {"ok": True})
    store.put("forecast", {"origin": NOW.isoformat(), "points": [forecast_point(t=40)], "engine": "test"})
    payload = dashboard_payload(store, now=NOW)
    assert not payload["ok"] and not payload["forecast"] and not payload["red_expected"]


def test_dashboard_horizon_filter_and_alert_scans_all_12h(store):
    store.add_reading(NOW, 28, 60)
    store.put("monitor", {"ok": True})
    store.put("forecast", {"origin": NOW.isoformat(), "points": [forecast_point(), forecast_point(t=40, minutes=720)], "engine": "test"})
    payload = dashboard_payload(store, hours=1, now=NOW)
    assert len(payload["forecast"]) == 1 and payload["red_expected"]


@pytest.fixture
def email_enabled(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)


def test_email_current_red_durable_cooldown_and_recovery(store, email_enabled):
    sent = []
    sender = lambda subject, body: sent.append((subject, body))
    reading = {"temp": 36, "hum": 60, "source": "sensor"}
    EmailNotifier(store, sender).evaluate(reading, "DO", None, NOW)
    EmailNotifier(store, sender).evaluate(reading, "DO", None, NOW + timedelta(minutes=1))
    assert len(sent) == 1
    EmailNotifier(store, sender).evaluate(reading, "DO", None, NOW + timedelta(minutes=31))
    assert len(sent) == 2
    EmailNotifier(store, sender).evaluate({**reading, "temp": 26}, "XANH", None, NOW + timedelta(minutes=32))
    EmailNotifier(store, sender).evaluate(reading, "DO", None, NOW + timedelta(minutes=33))
    assert len(sent) == 3


def test_email_future_low_and_no_yellow_or_stale_predictions(store, email_enabled):
    sent = []
    notifier = EmailNotifier(store, lambda *args: sent.append(args))
    reading = {"temp": 27, "hum": 60, "source": "sensor"}
    forecast = {"origin": NOW.isoformat(), "points": [forecast_point(h=30)], "engine": "chu kỳ"}
    notifier.evaluate(reading, "VANG", forecast, NOW)
    assert not sent
    forecast["points"].append(forecast_point(h=20, minutes=720))
    notifier.evaluate(reading, "XANH", forecast, NOW)
    assert len(sent) == 1 and "12" in sent[0][0]
    notifier.evaluate(reading, "XANH", forecast, NOW + timedelta(hours=1))
    assert len(sent) == 1


def test_email_failure_retries_without_success_stamp(store, email_enabled):
    def fail(*args):
        raise OSError("offline")
    reading = {"temp": 36, "hum": 60, "source": "sensor"}
    EmailNotifier(store, fail).evaluate(reading, "DO", None, NOW)
    assert store.get("email_state")["current_red"]["sent"] == 0
    assert not store.get("email_status")["ok"]
    sent = []
    EmailNotifier(store, lambda *args: sent.append(args)).evaluate(reading, "DO", None, NOW + timedelta(minutes=1))
    assert len(sent) == 1


def test_demo_never_sends_email(store, email_enabled):
    sent = []
    EmailNotifier(store, lambda *args: sent.append(args)).evaluate({"temp": 40, "hum": 10, "source": "demo"}, "DO", None, NOW)
    assert not sent


def test_monitor_sensor_failure_does_not_create_fake_reading(store):
    class Broken:
        def read(self, stop): return None
        def set_outputs(self, state): self.state = state
    hw = Broken()
    monitor = Monitor(store, hw, write_csv=False)
    monitor.step(threading.Event(), NOW)
    assert store.frame().empty
    assert hw.state == "ERROR" and not store.get("monitor")["ok"]


def test_monitor_full_pipeline_and_low_alarm(store, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)
    class Sensor:
        def read(self, stop): return 12, 20
        def set_outputs(self, state): self.state = state
    hw = Sensor()
    monitor = Monitor(store, hw, write_csv=False)
    for minute in range(3):
        monitor.step(threading.Event(), NOW + timedelta(minutes=minute))
    assert hw.state == "DO" and len(store.frame()) == 3
    assert len(store.get("forecast")["points"]) == 72
    payload = dashboard_payload(store, now=NOW + timedelta(minutes=2))
    assert payload["ok"] and payload["state"] == "DO" and payload["red_expected"]


def test_existing_csv_generated_prefix_is_tagged(store):
    prefix = generated_prefix(config.DATA_FILE)
    if not prefix:
        provenance = dataset_provenance(config.DATA_FILE)
        if not provenance.get("weather_rows"):
            pytest.skip("CSV hiện tại không phải một bộ mô phỏng đã nhận diện")
        store.import_csv()
        assert len(store.frame().query("source == 'weather_sample'")) == provenance["weather_rows"]
        assert store.latest_live() is None
        return
    store.import_csv()
    assert len(store.frame().query("source == 'sample'")) == prefix
    assert store.latest_live() is None


def test_training_refuses_sample_data_without_loading_tensorflow(store):
    from train_forecast import train
    frame = sample_frame()
    with store.connection() as db:
        db.executemany("INSERT INTO readings VALUES (?, ?, ?, 'sample', 'XANH', '[]')",
                       [(row.time.isoformat(), row.temp, row.hum) for row in frame.itertuples()])
    with pytest.raises(SystemExit, match="hiện có 0.0 ngày"):
        train(epochs=1, db_path=store.path)


def test_large_history_preserves_extrema(store):
    origin = NOW - timedelta(days=7)
    with store.connection() as db:
        db.executemany("INSERT INTO readings VALUES (?, ?, 50, 'sensor', 'XANH', '[]')",
                       [((origin + timedelta(minutes=i)).isoformat(timespec="seconds"), 70 if i == 5005 else 25)
                        for i in range(0, 7 * 24 * 60, 5)])
    result = store.history(origin, NOW, "minute")
    assert len(result) <= 1501
    assert max(row["temp_max"] for row in result if row["temp_max"] is not None) == 70


@pytest.mark.skipif(importlib.util.find_spec("tensorflow") is None, reason="Cài requirements-ml.txt để chạy thử Keras")
def test_keras_training_save_load_and_multistep_inference(store, tmp_path, monkeypatch):
    import train_forecast
    # Toàn bộ là dữ liệu QA trong DB tạm; không ghi vào thư mục model vận hành.
    frame = sample_frame(days=16)
    with store.connection() as db:
        db.executemany("INSERT INTO readings VALUES (?, ?, ?, 'sensor', 'XANH', '[]')",
                       [(row.time.isoformat(), row.temp, row.hum) for row in frame.itertuples()])
    model_path, meta_path = tmp_path / "qa.keras", tmp_path / "qa.json"
    monkeypatch.setattr(train_forecast, "FORECAST_MODEL", model_path)
    monkeypatch.setattr(train_forecast, "FORECAST_META", meta_path)
    train_forecast.train(epochs=1, db_path=store.path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["schema"] == 2 and len(meta["test_lstm"]["rmse_12h"]) == 2
    assert model_path.exists()
    # Ép chọn chỉ trong tệp QA để kiểm tra nhánh suy luận LSTM, không thay quyết định vận hành.
    meta["use_lstm"] = True
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    result = Forecaster(model_path, meta_path).predict(frame, NOW, [27, 60])
    assert "LSTM" in result["engine"] and len(result["points"]) == 72
    assert all(np.isfinite(p["temp"]) and np.isfinite(p["hum"]) for p in result["points"])


def weather_envelope():
    return {"request_url": "https://example.test/weather", "response": {
        "latitude": 20.98, "longitude": 105.79, "elevation": 13,
        "hourly": {"time": [(NOW + timedelta(hours=i)).isoformat() for i in range(4)],
                   "temperature_2m": [20., 22., 21., 80.],
                   "relative_humidity_2m": [80., 70., 75., 1.]}}}


def test_weather_generator_preserves_hourly_anchors_and_ignores_future():
    envelope = weather_envelope()
    end = NOW + timedelta(hours=2, minutes=30)
    frame, hourly = synthesize(envelope, NOW, end)
    for i, temp in enumerate([20, 22, 21]):
        row = frame[frame.time == (NOW + timedelta(hours=i)).isoformat()]
        assert row.temp.iloc[0] == temp
    assert len(hourly) == 3 and frame.temp.max() < 23
    assert frame.equals(synthesize(envelope, NOW, end)[0])


def test_weather_generator_rejects_gaps_and_nulls():
    envelope = weather_envelope()
    envelope["response"]["hourly"]["temperature_2m"][1] = None
    with pytest.raises(ValueError, match="bị thiếu"):
        synthesize(envelope, NOW, NOW + timedelta(hours=2))
    envelope = weather_envelope()
    for key in envelope["response"]["hourly"]:
        envelope["response"]["hourly"][key].pop(1)
    with pytest.raises(ValueError, match="không liên tục"):
        synthesize(envelope, NOW, NOW + timedelta(hours=2))


def test_weather_manifest_detects_modification_and_survives_append(store, tmp_path):
    output = tmp_path / "sensor.csv"
    source = tmp_path / "raw.json"
    envelope = weather_envelope()
    source.write_text(json.dumps(envelope), encoding="utf-8")
    frame, hourly = synthesize(envelope, NOW, NOW + timedelta(hours=2, minutes=30), seed=42)
    metadata = write_dataset(frame, hourly, envelope, output, source, 42, 20.9714, 105.7788)
    assert dataset_provenance(output)["weather_rows"] == len(frame)
    # Một số đo thực trùng timestamp không được bộ mẫu đè mất.
    store.add_reading(NOW, 25, 60)
    store.import_csv(output)
    actual = store.frame().query("source == 'sensor'")
    assert len(actual) == 1 and actual.temp.iloc[0] == 25
    with output.open("a", encoding="utf-8") as f:
        f.write(f"{(NOW + timedelta(hours=3)).isoformat()},23,65\n")
    assert dataset_provenance(output)["weather_rows"] == len(frame)
    store.import_csv(output)
    assert store.frame().iloc[-1]["source"] == "import"
    content = output.read_text(encoding="utf-8").replace(",20.0,80.0", ",19.0,80.0", 1)
    output.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="CSV đã thay đổi"):
        dataset_provenance(output)


def test_delivered_weather_dataset_contiguous_30_days():
    meta = json.loads(config.DATA_FILE.with_suffix(".provenance.json").read_text(encoding="utf-8"))
    df = pd.read_csv(config.DATA_FILE, parse_dates=["time"]).iloc[:meta["row_count"]]
    assert len(df) == 30 * 24 * 60
    assert df["time"].is_unique and df["time"].is_monotonic_increasing
    assert (df["time"].diff().dropna() == pd.Timedelta(minutes=1)).all()
    assert not df.isna().any().any()
    assert meta["source"] == "weather_sample"


def test_replacing_weather_dataset_removes_old_generated_rows_only(store, tmp_path):
    output, source = tmp_path / "sensor.csv", tmp_path / "raw.json"
    envelope = weather_envelope()
    source.write_text(json.dumps(envelope), encoding="utf-8")
    frame, hourly = synthesize(envelope, NOW, NOW + timedelta(hours=2), seed=42)
    write_dataset(frame, hourly, envelope, output, source, 42, 20.9714, 105.7788)
    store.add_reading(NOW, 25, 60)
    store.import_csv(output)
    second = frame.copy()
    second["time"] = (pd.to_datetime(second.time) + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%dT%H:%M:%S")
    later_hourly = hourly.copy()
    later_hourly.index += pd.Timedelta(days=1)
    write_dataset(second, later_hourly, envelope, output, source, 42, 20.9714, 105.7788)
    store.import_csv(output)
    actual = store.frame()
    assert len(actual) == len(second) + 1
    assert actual.iloc[0]["source"] == "sensor" and actual.iloc[0].temp == 25
    assert set(actual[actual.source == "weather_sample"].time.dt.date) == {(NOW + timedelta(days=1)).date()}
    assert list((tmp_path / "backups").glob("*.csv"))


def test_demo_accepts_dashboard_environment_using_same_demo_db(store, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", __import__('pathlib').Path(store.path))
    store.add_reading(NOW, 25, 60, source="demo")
    validate_demo_database(store.path)
    assert store.latest_live()["source"] == "demo"


def test_demo_rejects_canonical_production_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    with pytest.raises(ValueError, match="iot.sqlite3"):
        validate_demo_database(tmp_path / "data/iot.sqlite3")


def test_demo_rejects_custom_db_with_real_sensor_readings(store):
    store.add_reading(NOW, 25, 60, source="sensor")
    with pytest.raises(ValueError, match="cảm biến thật"):
        validate_demo_database(store.path)
    assert store.latest_live()["source"] == "sensor"


def test_demo_rejects_production_role_even_without_sensor_data(store):
    store.put("database_role", "production")
    with pytest.raises(ValueError, match="vận hành thật"):
        validate_demo_database(store.path)


def test_settings_persist_across_store_instances_and_change_classification(store):
    values = load_settings(store)
    values["thresholds"].update(hum_red_low=15, hum_yellow_low=18)
    saved = save_settings(store, values, 0)
    restored = load_settings(Store(store.path))
    assert restored == saved and restored["revision"] == 1
    assert classify(26, 20)[0] == "DO"
    assert classify(26, 20, restored["thresholds"])[0] == "XANH"


@pytest.mark.parametrize("updates", [
    {"hum_red_high": 101}, {"temp_red_low": float("nan")},
    {"temp_yellow_low": 40}, {"hum_yellow_high": ""},
])
def test_invalid_settings_never_partially_saved(store, updates):
    values = load_settings(store)
    values["thresholds"].update(updates)
    with pytest.raises(ValueError):
        save_settings(store, values, 0)
    assert load_settings(store)["revision"] == 0


def test_settings_reject_invalid_or_missing_recipient(store):
    values = load_settings(store)
    values["email_enabled"] = True
    for recipient in ("", "not-an-email", "first@example.com\nBcc: other@example.com"):
        values["email_to"] = recipient
        with pytest.raises(ValueError):
            save_settings(store, values, 0)
    assert load_settings(store)["revision"] == 0


def test_settings_conflict_keeps_newer_browser_values(store):
    old = load_settings(store)
    first = load_settings(store)
    first["thresholds"]["temp_red_high"] = 40
    save_settings(store, first, 0)
    with pytest.raises(SettingsConflict):
        save_settings(store, old, 0)
    assert load_settings(store)["thresholds"]["temp_red_high"] == 40


def test_monitor_applies_threshold_change_without_restart_and_resets_count(store, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)
    class Sensor:
        def read(self, stop): return 36, 60
        def set_outputs(self, state): self.state = state
    hw = Sensor()
    monitor = Monitor(store, hw, write_csv=False)
    for minute in range(3):
        monitor.step(threading.Event(), NOW + timedelta(minutes=minute))
    assert hw.state == "DO"
    values = load_settings(store)
    values["thresholds"].update(temp_yellow_high=40, temp_red_high=45)
    save_settings(store, values, 0)
    monitor.step(threading.Event(), NOW + timedelta(minutes=3))
    assert hw.state == "XANH"
    assert not dashboard_payload(store, now=NOW + timedelta(minutes=3))["red_expected"]
    values["thresholds"].update(temp_yellow_high=30, temp_red_high=35)
    save_settings(store, values, 1)
    monitor.step(threading.Event(), NOW + timedelta(minutes=4))
    assert hw.state == "VANG"
    monitor.step(threading.Event(), NOW + timedelta(minutes=5))
    assert hw.state == "VANG"
    monitor.step(threading.Event(), NOW + timedelta(minutes=6))
    assert hw.state == "DO"


def test_email_switch_and_recipient_update_use_current_saved_settings(store, monkeypatch):
    import notifications
    import ssl
    monkeypatch.setattr(config, "EMAIL_FROM", "sender@example.com")
    monkeypatch.setattr(config, "EMAIL_PASS", "secret-never-in-ui")
    sent, contexts = [], []
    class SMTP:
        def __init__(self, *args, **kwargs): contexts.append(kwargs["context"])
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def login(self, *args): pass
        def send_message(self, msg): sent.append(msg["To"])
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", SMTP)
    values = load_settings(store)
    values.update(email_enabled=True, email_to="first@example.com")
    save_settings(store, values, 0)
    notifier = EmailNotifier(store)
    reading = {"temp": 36, "hum": 60, "source": "sensor"}
    notifier.evaluate(reading, "DO", None, NOW)
    values["email_to"] = "second@example.com"
    save_settings(store, values, 1)
    notifier.evaluate(reading, "DO", None, NOW + timedelta(minutes=1))
    assert sent == ["first@example.com", "second@example.com"]
    assert all(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED for context in contexts)
    values["email_enabled"] = False
    save_settings(store, values, 2)
    notifier.evaluate(reading, "DO", None, NOW + timedelta(minutes=2))
    assert len(sent) == 2
    assert "secret-never-in-ui" not in json.dumps(dashboard_payload(store, now=NOW + timedelta(minutes=2)))


def test_default_login_password_hashed_and_session_expires(store):
    session = dashboard_auth.login(store, "123456", now=1000)
    record = store.get(dashboard_auth.AUTH_KEY)
    assert len(record["digest"]) == 64 and record["digest"] != "123456"
    assert "password" not in record
    assert dashboard_auth.session_valid(store, session["version"], session["expires"], now=1001)
    assert not dashboard_auth.session_valid(store, session["version"], session["expires"], now=session["expires"])
    assert not dashboard_auth.session_valid(store, "forged", session["expires"], now=1001)


def test_wrong_password_and_login_throttle(store):
    for i in range(5):
        with pytest.raises(ValueError, match="không đúng"):
            dashboard_auth.login(store, "wrong", client_key="same-ip", now=1000 + i)
    with pytest.raises(ValueError, match="thử lại"):
        dashboard_auth.login(store, "123456", client_key="same-ip", now=1005)
    assert dashboard_auth.login(store, "123456", client_key="same-ip", now=1035)


def test_password_change_persists_and_invalidates_older_sessions(store):
    old = dashboard_auth.login(store, "123456")
    changed = dashboard_auth.change_password(store, "123456", "new-pass-123", "new-pass-123")
    assert not dashboard_auth.session_valid(store, old["version"], old["expires"])
    assert dashboard_auth.session_valid(store, changed["version"], changed["expires"])
    assert dashboard_auth.login(Store(store.path), "new-pass-123")
    with pytest.raises(ValueError, match="không đúng"):
        dashboard_auth.login(store, "123456")
    assert dashboard_auth.ensure_auth(Store(store.path))["version"] == changed["version"]


@pytest.mark.parametrize("current,new,confirmation", [
    ("wrong", "new-pass-123", "new-pass-123"),
    ("123456", "short", "short"),
    ("123456", "new-pass-123", "different"),
    ("123456", "123456", "123456"),
])
def test_password_change_validation_preserves_existing_login(store, current, new, confirmation):
    original = dashboard_auth.ensure_auth(store)
    with pytest.raises(ValueError):
        dashboard_auth.change_password(store, current, new, confirmation)
    assert store.get(dashboard_auth.AUTH_KEY) == original


def test_backend_guards_reject_client_auth_flag_and_clear_cached_data(store, monkeypatch):
    from iot_dashboard import iot_dashboard as ui
    monkeypatch.setattr(ui, "Store", lambda: store)
    state = ui.DashboardState(_reflex_internal_init=True)
    state.authenticated = True  # Chỉ sửa cờ UI không cấp phiên hợp lệ trên server.
    state.history = [{"time": "private-history", "temp": 25}]
    state.save_configuration()
    assert load_settings(store)["revision"] == 0
    assert not state.authenticated and state.history == []
    assert state.download() is None
    state.change_login_password({"current_password": "123456", "new_password": "unauthorized", "confirm_password": "unauthorized"})
    assert store.get(dashboard_auth.AUTH_KEY) is None


def test_authenticated_dashboard_handler_and_private_session_not_serialized(store, monkeypatch):
    from iot_dashboard import iot_dashboard as ui
    monkeypatch.setattr(ui, "Store", lambda: store)
    state = ui.DashboardState(_reflex_internal_init=True)
    state.login({"password": "123456"})
    assert state.authenticated
    state.save_configuration()
    assert load_settings(store)["revision"] == 1
    serialized = json.dumps(state.dict(), default=str)
    assert state._auth_version not in serialized
    assert store.get(dashboard_auth.AUTH_KEY)["digest"] not in serialized
    state.logout()
    assert not state.authenticated and state._auth_version == ""


def test_history_download_uses_filters_before_background_refresh(store, monkeypatch):
    from iot_dashboard import iot_dashboard as ui
    monkeypatch.setattr(ui, "Store", lambda: store)
    store.add_reading(NOW - timedelta(days=1), 20, 50)
    store.add_reading(NOW, 30, 70)
    state = ui.DashboardState(_reflex_internal_init=True)
    state.login({"password": "123456"})
    state.history = [{"time": (NOW - timedelta(days=1)).isoformat(), "temp": 20, "hum": 50}]
    state.choose_start(NOW.strftime("%Y-%m-%dT00:00"))
    state.choose_end(NOW.strftime("%Y-%m-%dT23:59"))
    state.choose_resolution("Theo ngày")
    assert state.download() is not None
    assert len(state.history) == 1 and state.history[0]["temp"] == 30
    assert state.history[0]["count"] == 1
    state.choose_start((NOW + timedelta(days=1)).isoformat())
    assert state.download() is None
    assert state.error


def test_tensorflow_not_imported_for_unused_model_or_incomplete_context(tmp_path, monkeypatch):
    import builtins
    model, meta = tmp_path / "model.keras", tmp_path / "meta.json"
    model.write_bytes(b"unused")
    values = {"schema": 2, "steps": 72, "context": 144, "step_min": 10, "use_lstm": False}
    meta.write_text(json.dumps(values), encoding="utf-8")
    original = builtins.__import__
    def reject_tensorflow(name, *args, **kwargs):
        if name == "tensorflow":
            raise AssertionError("Không được nạp TensorFlow trong nhánh dự phòng")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", reject_tensorflow)
    forecaster = Forecaster(model, meta)
    result = forecaster.predict(sample_frame(), NOW, [27, 60])
    assert "dự phòng" in result["engine"] and not result["model_error"]
    values["use_lstm"] = True
    meta.write_text(json.dumps(values), encoding="utf-8")
    result = forecaster.predict(sample_frame().tail(20), NOW, [27, 60])
    assert "dự phòng" in result["engine"] and not result["model_error"]


def test_lstm_loads_automatically_when_context_becomes_ready(tmp_path, monkeypatch):
    import builtins
    from types import SimpleNamespace
    model, meta = tmp_path / "model.keras", tmp_path / "meta.json"
    model.write_bytes(b"qa")
    meta.write_text(json.dumps({"schema": 2, "steps": 72, "context": 144, "step_min": 10, "use_lstm": True,
                               "lo": [0, 0], "hi": [40, 100], "validation_rmse": [1, 2]}), encoding="utf-8")
    calls = []
    def load_model(*args, **kwargs):
        calls.append(1)
        return lambda x, training=False: np.tile([.65, .6], (1, 72, 1))
    fake = SimpleNamespace(keras=SimpleNamespace(models=SimpleNamespace(load_model=load_model)))
    original = builtins.__import__
    def intercept(name, *args, **kwargs):
        return fake if name == "tensorflow" else original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", intercept)
    forecaster = Forecaster(model, meta)
    forecaster.predict(sample_frame().tail(20), NOW, [27, 60])
    assert not calls
    assert "LSTM" in forecaster.predict(sample_frame(), NOW, [27, 60])["engine"]
    forecaster.predict(sample_frame(), NOW, [27, 60])
    assert len(calls) == 1


def make_prebuilt_root(root):
    import hashlib
    for name in [*COMPILE_INPUTS, "iot_dashboard/iot_dashboard.py"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# QA source\n", encoding="utf-8")
    frontend = root / "deploy/frontend"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("<!doctype html>QA", encoding="utf-8")
    manifest = {"schema": 1, "reflex_version": REFLEX_VERSION, "source_sha256": source_digest(root),
                "api_url": "http://localhost:3000", "build_id": "1234567890abcdef",
                "files": {"index.html": hashlib.sha256((frontend / "index.html").read_bytes()).hexdigest()}}
    (root / "deploy/frontend_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return frontend


def test_prebuilt_manifest_rejects_stale_source_and_corrupt_frontend(tmp_path):
    folder = make_prebuilt_root(tmp_path)
    assert validate_frontend(tmp_path)["build_id"] == "1234567890abcdef"
    source = tmp_path / "iot_dashboard/iot_dashboard.py"
    original = source.read_text(encoding="utf-8")
    source.write_text("# changed", encoding="utf-8")
    with pytest.raises(ValueError, match="thay đổi"):
        validate_frontend(tmp_path)
    source.write_text(original, encoding="utf-8")
    (folder / "index.html").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        validate_frontend(tmp_path)


def test_missing_frontend_has_pc_build_instructions(tmp_path):
    with pytest.raises(ValueError, match="trên PC"):
        validate_frontend(tmp_path)


def test_pi_package_contains_static_build_but_no_runtime_secrets(tmp_path):
    import zipfile
    from prepare_pi import package
    make_prebuilt_root(tmp_path)
    for name in (".env", "data/iot.sqlite3", "venv/binary", ".web/node_modules/binary", "model/private.keras"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private", encoding="utf-8")
    archive_path = package(tmp_path, tmp_path / "release.zip")
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
    assert any(name.endswith("deploy/frontend/index.html") for name in names)
    assert not any("sqlite3" in name or "node_modules" in name or name.endswith(".env") or "/model/" in name or "/venv/" in name for name in names)


def test_nginx_serves_static_and_proxies_websockets():
    config_text = nginx_config("/var/www/iot-dashboard/1234567890abcdef")
    assert "worker_processes 1;" in config_text
    assert "proxy_set_header Upgrade $http_upgrade;" in config_text
    assert "proxy_pass http://127.0.0.1:8000;" in config_text
    assert "listen 3000;" in config_text
    assert "_event|ping|_upload|_health|_all_routes" in config_text


@pytest.mark.parametrize("name,value", [
    ("EMAIL_ENABLED", "treu"), ("MQTT_ENABLED", "enabled"),
    ("EMAIL_PORT", "0"), ("MQTT_PORT", "65536"),
    ("EMAIL_COOLDOWN", "-1"), ("EMAIL_PORT", "abc"),
    ("HUM_RED_HIGH", "105"), ("TEMP_RED_LOW", "-50"),
])
def test_invalid_environment_settings_rejected(name, value):
    import os
    import subprocess
    import sys
    env = dict(os.environ, **{name: value})
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", "import common"],
                            cwd=config.BASE_DIR, env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0
    assert "ValueError" in result.stderr


@pytest.mark.parametrize("path,expected", [("", "data/iot.sqlite3"), ("data/custom.sqlite3", "data/custom.sqlite3")])
def test_environment_database_path_resolves_from_project(path, expected):
    import os
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c", "import common; print(common.DB_FILE.as_posix())"],
                            cwd=config.BASE_DIR, env=dict(os.environ, IOT_DB_FILE=path),
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == (config.BASE_DIR / expected).as_posix()


def test_dotenv_with_windows_bom_is_loaded(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    shutil.copy2(config.BASE_DIR / "common.py", tmp_path / "common.py")
    (tmp_path / ".env").write_text('EMAIL_ENABLED=true\nEMAIL_PASS="qa secret with spaces"\n', encoding="utf-8-sig")
    env = {key: value for key, value in os.environ.items() if key not in ("EMAIL_ENABLED", "EMAIL_PASS")}
    result = subprocess.run([sys.executable, "-c", "import common; print(common.EMAIL_ENABLED, len(common.EMAIL_PASS))"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "True 21"


def test_configuration_check_rejects_missing_smtp_without_exposing_password(tmp_path, monkeypatch, capsys):
    from check_system import check_configuration
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "config.sqlite3")
    monkeypatch.setattr(config, "EMAIL_ENABLED", True)
    monkeypatch.setattr(config, "EMAIL_TO", "receiver@example.com")
    monkeypatch.setattr(config, "EMAIL_FROM", "")
    monkeypatch.setattr(config, "EMAIL_PASS", "private-qa-password")
    assert not check_configuration()
    output = capsys.readouterr().out
    assert "EMAIL_FROM" in output and "private-qa-password" not in output


def test_dashboard_email_status_distinguishes_enabled_and_sent(store, monkeypatch):
    values = load_settings(store)
    values.update(email_enabled=True, email_to="receiver@example.com")
    save_settings(store, values, 0)
    monkeypatch.setattr(config, "EMAIL_FROM", "")
    monkeypatch.setattr(config, "EMAIL_PASS", "")
    assert dashboard_payload(store, now=NOW)["email"] == "Chưa cấu hình tài khoản gửi"
    monkeypatch.setattr(config, "EMAIL_FROM", "sender@example.com")
    monkeypatch.setattr(config, "EMAIL_PASS", "qa-secret")
    assert dashboard_payload(store, now=NOW)["email"] == "Đã bật · chờ cảnh báo"
    store.put("email_status", {"ok": True, "time": NOW.isoformat()})
    assert dashboard_payload(store, now=NOW)["email"] == "Lần gửi gần nhất thành công"
    store.put("email_status", {"ok": False, "error": "SMTP unavailable"})
    assert "SMTP unavailable" in dashboard_payload(store, now=NOW)["email"]


def test_monitor_refreshes_forecast_after_clock_moves_back(store, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ENABLED", False)
    class Sensor:
        def read(self, stop): return 25, 60
        def set_outputs(self, state): pass
    monitor = Monitor(store, Sensor(), write_csv=False)
    monitor.step(threading.Event(), NOW + timedelta(minutes=10))
    monitor.step(threading.Event(), NOW)
    assert store.get("forecast")["origin"] == NOW.isoformat(timespec="seconds")


@pytest.mark.parametrize("failed_file", ["sensor.csv", "log.csv"])
def test_csv_failure_keeps_monitor_alerts_and_notifications(store, tmp_path, monkeypatch, failed_file):
    import monitor as service
    from types import SimpleNamespace
    monkeypatch.setattr(config, "MQTT_ENABLED", False)
    monkeypatch.setattr(config, "DATA_FILE", tmp_path / "sensor.csv")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "log.csv")
    original_append = service.append_csv

    def append(path, fields, values):
        if path.name == failed_file:
            raise PermissionError("CSV is read-only")
        original_append(path, fields, values)

    monkeypatch.setattr(service, "append_csv", append)
    states, published = [], []
    sensor = SimpleNamespace(read=lambda stop: (36, 60), set_outputs=states.append)
    monitor = Monitor(store, sensor)
    notified = []
    monitor.notifier = SimpleNamespace(evaluate=lambda *args: notified.append(args[1]))
    monitor.mqtt = SimpleNamespace(publish=lambda *args, **kwargs: published.append(args))
    for minute in range(3):
        monitor.step(threading.Event(), NOW + timedelta(minutes=minute))
    assert states == ["VANG", "VANG", "DO"]
    assert notified == states and len(published) == 6
    assert len(store.frame()) == 3 and store.get("monitor")["ok"]


def test_monitor_closes_mqtt_when_hardware_cleanup_fails(store, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(config, "MQTT_ENABLED", False)

    def fail():
        raise OSError("GPIO cleanup failed")

    calls = []
    monitor = Monitor(store, SimpleNamespace(close=fail))
    monitor.mqtt = SimpleNamespace(loop_stop=lambda: calls.append("stop"), disconnect=lambda: calls.append("disconnect"))
    with pytest.raises(OSError, match="GPIO cleanup"):
        monitor.close()
    assert calls == ["stop", "disconnect"]


@pytest.mark.parametrize("failure", ["led", "pwm", "none"])
def test_pi_hardware_releases_resources_after_partial_initialization(monkeypatch, failure):
    import sys
    import time
    from types import ModuleType, SimpleNamespace
    from hardware import PiHardware
    closed = []

    class LED:
        def __init__(self, pin):
            if failure == "led" and pin == config.PIN_LED_YELLOW:
                raise OSError("LED unavailable")
            self.pin = pin

        def close(self):
            closed.append(self.pin)

    class Button:
        def __init__(self, *args, **kwargs):
            pass

        @property
        def when_pressed(self):
            return self.callback

        @when_pressed.setter
        def when_pressed(self, callback):
            self.callback = callback
            callback()

        def close(self):
            closed.append("button")

    class PWM:
        def __init__(self, *args):
            pass

        def start(self, duty):
            if failure == "pwm":
                raise OSError("PWM unavailable")

        def ChangeDutyCycle(self, duty):
            pass

        def stop(self):
            closed.append("pwm")

    gpio = ModuleType("RPi.GPIO")
    gpio.BCM = gpio.OUT = 1
    gpio.setmode = gpio.setup = lambda *args: None
    gpio.cleanup = lambda pin: closed.append("gpio")
    gpio.PWM = PWM
    package = ModuleType("RPi")
    package.GPIO = gpio
    monkeypatch.setitem(sys.modules, "RPi", package)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio)
    monkeypatch.setitem(sys.modules, "adafruit_dht", SimpleNamespace(
        DHT22=lambda *args, **kwargs: SimpleNamespace(exit=lambda: closed.append("dht"))))
    monkeypatch.setitem(sys.modules, "board", SimpleNamespace(**{f"D{config.PIN_DHT}": config.PIN_DHT}))
    monkeypatch.setitem(sys.modules, "gpiozero", SimpleNamespace(LED=LED, Button=Button))

    if failure != "none":
        with pytest.raises(OSError, match="unavailable"):
            PiHardware()
    else:
        hardware = PiHardware()
        assert hardware.mute_until > time.monotonic()
        hardware.close()
        previous = list(closed)
        hardware.close()
        assert closed == previous and not hardware.thread.is_alive()
    assert "dht" in closed and config.PIN_LED_GREEN in closed
    if failure != "led":
        assert all(name in closed for name in ("button", "pwm", "gpio", config.PIN_LED_YELLOW, config.PIN_LED_RED))


def test_hardware_cli_refuses_to_compete_with_monitor(monkeypatch, capsys):
    import sys
    import check_system

    class BusySocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): raise OSError("GPIO lock occupied")

    monkeypatch.setattr(check_system.socket, "socket", BusySocket)
    monkeypatch.setattr(sys, "argv", ["check_system.py", "--hw-only"])
    monkeypatch.setattr(check_system, "check_hardware_interactive", lambda: pytest.fail("GPIO must stay unopened"))
    with pytest.raises(SystemExit) as exc:
        check_system.main()
    assert exc.value.code == 1
    assert "Khóa GPIO" in capsys.readouterr().out


@pytest.mark.parametrize("failed_service,expected", [("iot-dashboard", 1), ("", 0)])
def test_installer_checks_each_service(tmp_path, failed_service, expected):
    import shutil
    import subprocess
    from pathlib import Path
    bash = Path("C:/Program Files/Git/bin/bash.exe")
    command = str(bash) if bash.is_file() else shutil.which("bash")
    if command is None:
        pytest.skip("Cần bash để kiểm tra installer")
    script = (config.BASE_DIR / "install_pi.sh").read_text(encoding="utf-8")
    start = script.index("for service in iot-monitor iot-dashboard iot-web; do")
    end = script.index("\ndone", start) + len("\ndone")
    stub = 'sudo() { [[ "$4" != "' + failed_service + '" ]]; }\n'
    result = subprocess.run([command, "-c", stub + script[start:end]], cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == expected, result.stderr


@pytest.mark.parametrize("project_dir", ["/home/pi/Downloads/v1-main", "/home/pi/Downloads/IoT project"])
def test_installer_generated_working_directories_are_absolute(project_dir):
    import shlex
    import shutil
    import subprocess
    from pathlib import Path, PurePosixPath

    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if bash is None:
        pytest.skip("Cần bash để kiểm tra các unit được installer tạo")
    script = (config.BASE_DIR / "install_pi.sh").read_text(encoding="utf-8")
    start = script.index("sudo tee /etc/systemd/system/iot-monitor.service")
    end = script.index("sudo systemctl daemon-reload", start)
    # Thu nội dung heredoc trong bộ nhớ, không ghi unit hoặc gọi sudo thật.
    generation = script[start:end].replace(">/dev/null", "")
    setup = (f"PROJECT_DIR={shlex.quote(project_dir)}\n"
             "TARGET_USER=pi\nTARGET_GROUP=pi\n"
             'sudo() { [[ "$1" == tee ]] || return 1; cat; }\n')
    result = subprocess.run([bash, "-c", setup + generation], capture_output=True,
                            text=True, encoding="utf-8", check=True)
    directories = [line.partition("=")[2] for line in result.stdout.splitlines()
                   if line.startswith("WorkingDirectory=")]
    assert len(directories) == 2
    for directory in directories:
        # systemd đọc nguyên giá trị này trước khi kiểm tra đường dẫn tuyệt đối.
        assert PurePosixPath(directory).is_absolute(), directory
        assert directory == project_dir


def test_package_failure_preserves_previous_release(tmp_path, monkeypatch):
    import zipfile
    from prepare_pi import package
    make_prebuilt_root(tmp_path)
    destination = tmp_path / "release.zip"
    destination.write_bytes(b"previous release")

    def fail(*args, **kwargs):
        raise OSError("Archive write failed")

    monkeypatch.setattr(zipfile.ZipFile, "write", fail)
    with pytest.raises(OSError, match="Archive write"):
        package(tmp_path, destination)
    assert destination.read_bytes() == b"previous release"
    assert not list(tmp_path.glob("*.tmp"))


def test_mixed_model_and_metadata_fall_back_before_tensorflow(tmp_path, monkeypatch):
    import builtins
    import hashlib
    model, meta = tmp_path / "model.keras", tmp_path / "meta.json"
    model.write_bytes(b"incomplete new model")
    meta.write_text(json.dumps({"schema": 2, "steps": 72, "context": 144, "step_min": 10,
                               "use_lstm": True, "model_sha256": hashlib.sha256(b"previous model").hexdigest()}), encoding="utf-8")
    original = builtins.__import__

    def reject_tensorflow(name, *args, **kwargs):
        if name == "tensorflow":
            pytest.fail("TensorFlow must wait for the matching model pair")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_tensorflow)
    result = Forecaster(model, meta).predict(sample_frame(), NOW, [27, 60])
    assert len(result["points"]) == 72 and "dự phòng" in result["engine"]
    assert "chưa khớp" in result["model_error"]


def test_environment_cli_reports_failure_for_missing_core_library(monkeypatch):
    import builtins
    import sys
    from types import SimpleNamespace
    import check_system
    original = builtins.__import__
    diagnostic_libraries = {"gpiozero", "adafruit_dht", "board", "numpy", "pandas", "reflex", "dotenv",
                            "tensorflow", "matplotlib", "paho.mqtt.client"}

    def import_library(name, *args, **kwargs):
        if name == "RPi.GPIO":
            raise ImportError("missing GPIO driver")
        if name in diagnostic_libraries:
            return SimpleNamespace()
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_library)
    monkeypatch.setattr(check_system, "check_configuration", lambda: True)
    monkeypatch.setattr(sys, "argv", ["check_system.py", "--env-only"])
    with pytest.raises(SystemExit) as exc:
        check_system.main()
    assert exc.value.code == 1


def test_pi_requirements_accept_system_gpio_version():
    from packaging.requirements import Requirement
    lines = (config.BASE_DIR / "requirements-pi.txt").read_text(encoding="utf-8").splitlines()
    gpio = Requirement(next(line for line in lines if line.startswith("RPi.GPIO")))
    assert gpio.specifier.contains("0.7.1a4")
    assert gpio.specifier.contains("0.7.1")


def test_new_alert_smtp_failure_retries_before_previous_episode_cooldown(store, email_enabled):
    reading = {"temp": 36, "hum": 60, "source": "sensor"}
    sent = []
    notifier = EmailNotifier(store, lambda *args: sent.append(args))
    notifier.evaluate(reading, "DO", None, NOW)
    notifier.evaluate({**reading, "temp": 26}, "XANH", None, NOW + timedelta(minutes=1))

    def offline(*args):
        raise OSError("SMTP offline during a new alert")

    EmailNotifier(store, offline).evaluate(reading, "DO", None, NOW + timedelta(minutes=2))
    assert not store.get("email_status")["ok"]
    # Tạo notifier mới để kiểm tra cả tình huống service khởi động lại sau lỗi.
    EmailNotifier(store, lambda *args: sent.append(args)).evaluate(
        reading, "DO", None, NOW + timedelta(minutes=3))
    assert len(sent) == 2
    assert store.get("email_status")["ok"]
    notifier.evaluate(reading, "DO", None, NOW + timedelta(minutes=4))
    assert len(sent) == 2


@pytest.mark.parametrize("spread", [None, [1], [1, 2, 3], [float("nan"), 2], [1, -2], [1, float("inf")]])
def test_invalid_lstm_uncertainty_preserves_complete_baseline_forecast(tmp_path, spread):
    forecaster = Forecaster(tmp_path / "absent.keras", tmp_path / "absent.json")
    frame = sample_frame()
    baseline = forecaster.predict(frame, NOW, [27, 60])
    forecaster._model = lambda *args, **kwargs: np.zeros((1, 72, 2))
    forecaster._meta = {"use_lstm": True, "lo": [0, 0], "hi": [40, 100]}
    if spread is not None:
        forecaster._meta["validation_rmse"] = spread
    result = forecaster.predict(frame, NOW, [27, 60])
    assert result["engine"] == baseline["engine"]
    assert result["points"] == baseline["points"]
    assert result["model_error"]
    json.dumps(result, allow_nan=False)


def test_monitor_initialization_failure_closes_hardware_and_gpio_lock(store, monkeypatch):
    import sys
    from types import SimpleNamespace
    import monitor
    closed = []
    hardware = SimpleNamespace(close=lambda: closed.append("hardware"))
    lock = SimpleNamespace(bind=lambda *args: None, close=lambda: closed.append("lock"))

    def broken_monitor(*args, **kwargs):
        raise RuntimeError("Forecaster initialization failed")

    monkeypatch.setattr(monitor, "Store", lambda: store)
    monkeypatch.setattr(store, "import_csv", lambda: 0)
    monkeypatch.setattr(monitor, "PiHardware", lambda: hardware)
    monkeypatch.setattr(monitor, "Monitor", broken_monitor)
    monkeypatch.setattr(monitor.socket, "socket", lambda: lock)
    monkeypatch.setattr(monitor.signal, "signal", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["monitor.py", "--once"])
    with pytest.raises(RuntimeError, match="Forecaster initialization"):
        monitor.main()
    assert closed == ["hardware", "lock"]


@pytest.mark.parametrize("eventually_ready", [True, False])
def test_deployment_waits_for_real_http_readiness(eventually_ready):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pi_deploy import wait_for_backend

    class Handler(BaseHTTPRequestHandler):
        attempts = 0

        def do_GET(self):
            Handler.attempts += 1
            self.send_response(200 if eventually_ready and Handler.attempts >= 3 else 503)
            self.end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/ping"
            if eventually_ready:
                wait_for_backend(url, timeout=2, interval=.01)
                assert Handler.attempts >= 3
            else:
                with pytest.raises(RuntimeError, match="chưa sẵn sàng"):
                    wait_for_backend(url, timeout=.1, interval=.01)
        finally:
            server.shutdown()
            worker.join(timeout=2)
