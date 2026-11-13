"""Nhận diện dữ liệu mô phỏng qua manifest đã kiểm tra hash hoặc bộ sinh cũ."""
import csv
import math
import json
import hashlib

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
            return {"sample_rows": 0, "weather_rows": int(meta["row_count"]),
                    "dataset_id": meta["dataset_id"], "start": meta["start"], "end": meta["end"],
                    "location": meta["location"], "provider": meta["source_provider"],
                    "source_url": meta["source_url"], "source": "weather_sample"}
    rows = generated_prefix(path)
    return {"sample_rows": rows, "weather_rows": 0, "dataset_id": f"legacy:{rows}"}
