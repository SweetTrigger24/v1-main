"""Nhận diện dữ liệu mô phỏng qua manifest đã kiểm tra hash hoặc bộ sinh cũ."""
import csv
import math
import json
import hashlib
import os
import uuid

SENSOR_CSV_FIELDS = ["time", "temp", "hum", "label"]


def prepare_sensor_csv(path):
    """Declare the monitor-owned append boundary; labels alone never prove origin.

    The original bytes stay intact, including synthetic rows labelled sensor.
    One monitor owns this file (the service holds the GPIO process lock).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        path.write_text(",".join(SENSOR_CSV_FIELDS) + "\n", encoding="utf-8")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        if next(reader, None) != SENSOR_CSV_FIELDS:
            raise ValueError("sensor_data.csv phải có đúng time,temp,hum,label; không nối vào CSV khác cấu trúc")
    with path.open("rb") as stream:
        stream.seek(-1, os.SEEK_END)
        if stream.read(1) != b"\n":
            raise ValueError("CSV có dòng cuối chưa hoàn chỉnh; không nối số đo mới")
    provenance = dataset_provenance(path)
    if "sensor_row_start" in provenance:
        return
    manifest = path.with_suffix(".provenance.json")
    meta = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {
        "schema": 1, "kind": "recorded_sensor_csv", "dataset_id": "sensor:" + uuid.uuid4().hex}
    content = path.read_bytes()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        start_row = sum(1 for _ in csv.DictReader(stream))
    meta["recorded_sensor_tail"] = {
        "writer": "monitor", "source": "sensor", "start_row": start_row,
        "prefix_bytes": len(content), "prefix_sha256": hashlib.sha256(content).hexdigest(),
        "columns": SENSOR_CSV_FIELDS,
        "semantics": "Only monitor appends labelled sensor after this verified prefix are sensor readings; prefix labels do not establish origin."}
    meta["simulation_summary_scope"] = "original_generated_prefix"
    temporary = manifest.with_name(manifest.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, manifest)
    finally:
        temporary.unlink(missing_ok=True)

import numpy as np


def generated_prefix(path):
    """Trả số dòng đầu khớp chính xác bộ sinh seed=42; ít nhất 60 dòng mới gắn nhãn."""
    rng = np.random.RandomState(42)
    noise_t = noise_h = 0.0
    matched = 0
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            for i, row in enumerate(csv.DictReader(f)):
                if i:
                    noise_t = .85 * noise_t + rng.normal(0, .15)
                    noise_h = .85 * noise_h + rng.normal(0, .5)
                phase = 2 * math.pi * (i - 540) / 1440
                t = np.clip(28 + 4.5 * math.sin(phase) + noise_t, 15, 42).round(1)
                h = np.clip(72 - 18 * math.sin(phase) + noise_h, 20, 98).round(1)
                if not (abs(float(row["temp"]) - t) < 1e-9 and abs(float(row["hum"]) - h) < 1e-9):
                    break
                matched += 1
    except (OSError, KeyError, ValueError, TypeError):
        pass
    return matched if matched >= 60 else 0


def dataset_provenance(path):
    """Kiểm tra prefix thay vì toàn tệp để cho phép append số đo thật phía sau."""
    manifest = path.with_suffix(".provenance.json")
    meta = {}
    result = None
    if manifest.exists():
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        if meta.get("kind") == "weather_derived_synthetic":
            size = int(meta["csv_prefix_bytes"])
            if size <= 0 or size > path.stat().st_size:
                raise ValueError("Manifest khí tượng không khớp kích thước CSV")
            with path.open("rb") as f:
                digest = hashlib.sha256(f.read(size)).hexdigest()
            if digest != meta["csv_prefix_sha256"]:
                raise ValueError("CSV đã thay đổi so với manifest; cần sinh lại hoặc kiểm tra nguồn dữ liệu")
            result = {"sample_rows": 0, "weather_rows": int(meta["row_count"]),
                    "dataset_id": meta["dataset_id"], "start": meta["start"], "end": meta["end"],
                    "location": meta["location"], "provider": meta["source_provider"],
                    "source_url": meta["source_url"], "source": "weather_sample"}
    if result is None:
        rows = generated_prefix(path)
        result = {"sample_rows": rows, "weather_rows": 0,
                  "dataset_id": meta.get("dataset_id", f"legacy:{rows}")}
    tail = meta.get("recorded_sensor_tail")
    if tail is not None:
        size, start_row = int(tail["prefix_bytes"]), int(tail["start_row"])
        if (tail.get("writer") != "monitor" or tail.get("source") != "sensor"
                or tail.get("columns") != SENSOR_CSV_FIELDS or start_row < max(result["sample_rows"], result["weather_rows"])
                or size <= 0 or size > path.stat().st_size):
            raise ValueError("Metadata phần cảm biến không hợp lệ")
        with path.open("rb") as stream:
            digest = hashlib.sha256(stream.read(size)).hexdigest()
        if digest != tail["prefix_sha256"]:
            raise ValueError("CSV đã thay đổi trước phần cảm biến; cần kiểm tra nguồn dữ liệu")
        result["sensor_row_start"] = start_row
    return result
