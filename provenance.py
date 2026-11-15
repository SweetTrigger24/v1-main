"""Nhận diện dữ liệu mô phỏng qua manifest đã kiểm tra hash hoặc bộ sinh cũ."""
import csv
import math
import json
import hashlib
import os
import uuid
from pathlib import Path
from datetime import datetime

SENSOR_CSV_FIELDS = ["time", "temp", "hum", "label"]


def prepare_sensor_csv(path):
    """Declare the monitor-owned append boundary, separate from assigned labels.

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
        "semantics": "Monitor appends labelled sensor after this verified prefix; any user-assigned prefix labels are configured separately."}
    meta["simulation_summary_scope"] = "original_generated_prefix"
    temporary = manifest.with_name(manifest.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, manifest)
    finally:
        temporary.unlink(missing_ok=True)

import numpy as np


def weather_training_ranges(path):
    """Read the verified base CSV's eligibility policy, even after dates pass.

    None means the older manifest has no per-period policy; [] permits no
    generated rows. The policy never restricts subsequently measured sensors.
    """
    path = Path(path)
    dataset_provenance(path)
    manifest = path.with_suffix(".provenance.json")
    if not manifest.exists():
        return None
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    if "training_time_ranges" in meta:
        policy = meta
    else:
        reference = meta.get("row_metadata_file")
        if not reference:
            return None
        details = (path.parent / reference).resolve()
        if not details.is_relative_to(path.parent.resolve()) or not details.is_file():
            raise ValueError("Không tìm thấy metadata lọc dữ liệu train trong thư mục CSV")
        policy = json.loads(details.read_text(encoding="utf-8"))
        expected = policy.get("files_sha256", {}).get(path.name)
        if expected != meta.get("csv_prefix_sha256") or policy.get("rows") != meta.get("row_count"):
            raise ValueError("Metadata lọc train không khớp phần dữ liệu mô phỏng của CSV")
    if "training_time_ranges" not in policy:
        raise ValueError("Metadata thiếu quy tắc lọc mẫu dùng dự báo khi train")
    ranges = []
    for period in policy["training_time_ranges"]:
        start, end = period["start"], period["end"]
        if start > end or start < meta["start"] or end > meta["end"]:
            raise ValueError("Khoảng dữ liệu train nằm ngoài phần CSV đã xác minh")
        ranges.append([start,end])
    return ranges


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
        if meta.get("kind") in {"weather_derived_synthetic", "user_labeled_mixed_dataset"}:
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
            assignment = meta.get("sensor_label_assignment")
            if assignment is not None:
                start = datetime.fromisoformat(assignment["start_exclusive"])
                end = datetime.fromisoformat(assignment["end"])
                if (meta.get("source_assignment") != "csv_label"
                        or assignment.get("label") != "sensor"
                        or not 0 < int(assignment["rows"]) <= result["weather_rows"]
                        or not datetime.fromisoformat(meta["start"]) <= start < end <= datetime.fromisoformat(meta["end"])):
                    raise ValueError("Khoảng gán nhãn sensor không hợp lệ")
                result["sensor_label_assignment"] = assignment
                result["source_assignment"] = "csv_label"
                result["weather_source_rows"] = result["weather_rows"] - int(assignment["rows"])
                result["source"] = "mixed"
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
