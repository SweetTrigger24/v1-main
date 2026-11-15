"""Sinh dữ liệu DHT22 tham khảo từ thời tiết từng giờ tại Hà Đông, không giả là đo thực."""
import argparse
import hashlib
import json
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from common import DATA_FILE, SAMPLE_SEC, now_local

SOURCE_DOC = "https://open-meteo.com/en/docs/historical-weather-api"


def download_weather(latitude, longitude, start, end, destination):
    params = {"latitude": latitude, "longitude": longitude, "start_date": start.date().isoformat(),
              "end_date": end.date().isoformat(), "hourly": "temperature_2m,relative_humidity_2m,precipitation",
              "models": "ecmwf_ifs", "timezone": "Asia/Ho_Chi_Minh"}
    url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "IoT-HaDong-weather-dataset/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = json.load(response)
    if raw.get("error"):
        raise ValueError(raw.get("reason", "API không trả về dữ liệu"))
    envelope = {"request_url": url, "downloaded_at": now_local().isoformat(), "response": raw}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return envelope


def synthesize(envelope, start, end, seed=20261110, sample_sec=SAMPLE_SEC):
    """Nội suy có kiểm tra độ phủ; không dùng mốc giờ tương lai để tạo phần cuối."""
    hourly = pd.DataFrame(envelope["response"]["hourly"])
    hourly["time"] = pd.to_datetime(hourly["time"])
    hourly = hourly.sort_values("time").drop_duplicates("time").set_index("time")
    hourly = hourly.rename(columns={"temperature_2m": "temp", "relative_humidity_2m": "hum"})
    hourly = hourly.loc[hourly.index <= end, ["temp", "hum"]].apply(pd.to_numeric, errors="coerce")
    if hourly.empty or hourly.isna().any().any():
        raise ValueError("Dữ liệu khí tượng bị thiếu; không tự bịa giá trị để lấp ngày trống")
    if not np.isfinite(hourly.to_numpy()).all() or not hourly.temp.between(-40, 80).all() or not hourly.hum.between(0, 100).all():
        raise ValueError("Số liệu khí tượng nằm ngoài dải hợp lệ")
    if len(hourly) < 2 or (hourly.index.to_series().diff().dropna() != pd.Timedelta(hours=1)).any():
        raise ValueError("Chuỗi khí tượng không liên tục theo giờ")
    if hourly.index[0] > start or end - hourly.index[-1] >= pd.Timedelta(hours=1):
        raise ValueError("Nguồn khí tượng không phủ đủ khoảng yêu cầu, nhất là ngày cuối")
    stamps = pd.date_range(start=start, end=end, freq=f"{sample_sec}s")
    joined = hourly.reindex(hourly.index.union(stamps)).sort_index().interpolate(method="time")
    base = joined.reindex(stamps).to_numpy()
    # Dao động ngắn hạn chỉ là giả lập. Giữ mốc từng giờ bằng cầu nhiễu về 0 ở hai đầu giờ.
    rng = np.random.default_rng(seed)
    innovation = rng.multivariate_normal([0, 0], [[.025 ** 2, -.003], [-.003, .25 ** 2]], len(stamps))
    noise = np.zeros_like(innovation)
    for i in range(1, len(stamps)):
        noise[i] = .92 * noise[i - 1] + innovation[i]
    phase = (stamps.minute.to_numpy() * 60 + stamps.second.to_numpy()) / 3600
    noise *= np.sin(np.pi * phase)[:, None]
    values = base + noise
    values[:, 0] = np.clip(values[:, 0], -40, 80)
    values[:, 1] = np.clip(values[:, 1], 0, 100)
    frame = pd.DataFrame({"time": stamps.strftime("%Y-%m-%dT%H:%M:%S"),
                          "temp": values[:, 0].round(1), "hum": values[:, 1].round(1)})
    return frame, hourly


def write_dataset(frame, hourly, envelope, output, source_file, seed, latitude, longitude):
    frame = frame.copy()
    if "label" not in frame:
        frame["label"] = "weather_simulated"
    frame = frame[["time","temp","hum","label"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if output.exists():
        folder = output.parent / "backups"
        folder.mkdir(exist_ok=True)
        stamp = now_local().strftime("%Y%m%d_%H%M%S_%f")
        backup = folder / f"{output.stem}_{stamp}.csv"
        shutil.copy2(output, backup)
        previous = output.with_suffix(".provenance.json")
        if previous.exists():
            shutil.copy2(previous, backup.with_suffix(".provenance.json"))
    temporary = output.with_suffix(".next.csv")
    frame.to_csv(temporary, index=False, lineterminator="\n")
    content = temporary.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    raw = envelope["response"]
    summary = {"temp_min": float(frame.temp.min()), "temp_max": float(frame.temp.max()),
               "temp_mean": float(frame.temp.mean()), "hum_min": float(frame.hum.min()),
               "hum_max": float(frame.hum.max()), "hum_mean": float(frame.hum.mean())}
    metadata = {"schema": 1, "kind": "weather_derived_synthetic", "source": "weather_sample",
                "dataset_id": digest, "generated_at": now_local().isoformat(), "seed": seed,
                "location": "Hà Đông, Hà Nội", "environment": "outdoor_weather_reference",
                "requested_coordinates": {"latitude": latitude, "longitude": longitude},
                "grid_coordinates": {"latitude": raw["latitude"], "longitude": raw["longitude"]},
                "timezone": "Asia/Ho_Chi_Minh", "source_provider": "Open-Meteo / ECMWF IFS",
                "source_url": envelope["request_url"], "documentation_url": SOURCE_DOC,
                "source_file": source_file.relative_to(output.parent).as_posix() if source_file.is_relative_to(output.parent) else source_file.as_posix(),
                "source_resolution": "hourly, approximately 9 km grid", "source_hours_available": len(hourly),
                "source_last_used": hourly.index[-1].isoformat(),
                "tail_hold_minutes": (pd.Timestamp(frame.time.iloc[-1]) - hourly.index[-1]).total_seconds() / 60,
                "start": frame.time.iloc[0], "end": frame.time.iloc[-1], "row_count": len(frame),
                "sample_sec": SAMPLE_SEC, "csv_prefix_bytes": len(content), "csv_prefix_sha256": digest,
                "method": "Hourly linear interpolation + small correlated AR(1) noise, zero noise at exact source hours; hold latest available hour for incomplete current hour; round 0.1 units.",
                "limitations": ["Not DHT22 observations and not exact Ha Dong station measurements.",
                                "Minute values are interpolated/simulated, not 43,200 independent observations.",
                                "Weather analysis grid does not reproduce indoor air conditioning or local microclimate.",
                                "Do not report validation on this data as real sensor forecast accuracy."],
                "summary": summary, "backup": backup.relative_to(output.parent).as_posix() if backup else None}
    temporary_meta = output.with_suffix(".provenance.next.json")
    temporary_meta.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output)
    temporary_meta.replace(output.with_suffix(".provenance.json"))
    daily = frame.assign(time=pd.to_datetime(frame.time)).set_index("time").resample("D").agg(
        temp_min=("temp", "min"), temp_mean=("temp", "mean"), temp_max=("temp", "max"),
        hum_min=("hum", "min"), hum_mean=("hum", "mean"), hum_max=("hum", "max"), samples=("temp", "count"))
    references = output.parent / "weather_sources" / "reference"
    references.mkdir(parents=True,exist_ok=True)
    daily.round(2).to_csv(references / "weather_daily_summary.csv", index_label="date")
    # Giữ nguyên số liệu nguồn từng giờ để kiểm tra độc lập với bộ dữ liệu phút.
    hourly.to_csv(references / "weather_hourly_reference.csv", index_label="time")
    project_dir = Path(__file__).resolve().parent
    source_command = source_file.relative_to(project_dir).as_posix() if source_file.is_relative_to(project_dir) else source_file.as_posix()
    report = f"""# Bộ dữ liệu thời tiết tham khảo · Hà Đông, Hà Nội

- Khoảng thời gian: **{metadata['start']} → {metadata['end']}**, giờ Việt Nam.
- **{len(frame):,} mẫu**, chu kỳ {SAMPLE_SEC} giây; đây là dữ liệu phút **nội suy/mô phỏng theo khí tượng**, không phải số đo DHT22 thực.
- Điểm yêu cầu: {latitude}°B, {longitude}°Đ; ô lưới trả về: {raw['latitude']}°B, {raw['longitude']}°Đ, cao độ {raw.get('elevation')} m.
- Nguồn: [Open-Meteo Historical Weather / ECMWF IFS]({SOURCE_DOC}), dữ liệu theo giờ, lưới khoảng 9 km. [URL yêu cầu cụ thể]({envelope['request_url']}).
- Nhiệt độ: **{summary['temp_min']:.1f}–{summary['temp_max']:.1f}°C**, trung bình {summary['temp_mean']:.2f}°C.
- Độ ẩm: **{summary['hum_min']:.1f}–{summary['hum_max']:.1f}%**, trung bình {summary['hum_mean']:.2f}%.

Giữ diễn biến nguồn từng giờ theo từng ngày, không lặp một đường sin ngày/đêm. Nội suy tuyến tính về từng phút; thêm nhiễu AR(1) nhỏ có tương quan nhiệt/ẩm, bằng 0 tại đúng mốc giờ nguồn; làm tròn 0,1 như giao diện DHT22. Phần cuối sau mốc nguồn {metadata['source_last_used']} giữ nền giờ cuối thêm {metadata['tail_hold_minutes']:.0f} phút, có nhiễu nhỏ. Không lấy mốc tương lai để hoàn tất phần đuôi.

Các mẫu phút không tạo thêm thông tin quan trắc độc lập so với nguồn giờ. Lưới khí tượng không tái hiện chính xác ban công, phòng kín hoặc điều hòa. Không dùng sai số kiểm thử trên bộ này để công bố độ chính xác dự báo cảm biến thật.

## Các tệp

- `sensor_data.csv`: định dạng tương thích `time,temp,hum,label`.
- `sensor_data.provenance.json`: tọa độ, URL, phương pháp, seed, SHA-256 và phạm vi mẫu.
- `weather_sources/reference/weather_hourly_reference.csv`: số liệu giờ gốc, trước khi thêm nhiễu.
- `weather_sources/reference/weather_daily_summary.csv`: thống kê từng ngày (ngày đầu/cuối là ngày không đủ 24 giờ).
- `weather_sources/`: JSON API gốc và bản tin METAR Nội Bài để đối chiếu vùng.
- `backups/`: bản sao dữ liệu trước khi thay thế; bản vừa sao lưu: `{metadata['backup']}`.

Nguồn ECMWF IFS là dữ liệu phân tích/mô hình khí tượng, không được gắn nhãn quan trắc trạm Hà Đông. Nguồn quan trắc Nội Bài qua [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/request/download.phtml?network=VN__ASOS) là đối chiếu độc lập ở nơi khác, không được nhập thành số đo Hà Đông. Kết quả đối chiếu lưu trong `weather_sources/regional_comparison.json` nếu có; không dùng để khẳng định sai số tại điểm đặt cảm biến.

Tải lại 30 ngày mới: `python generate_weather_data.py --days 30`. Tái lập bộ đã giao: `python generate_weather_data.py --days 30 --end {metadata['end']} --seed {seed} --source-file "{source_command}"`. Chương trình tự sao lưu CSV cũ. Sau khi đổi dữ liệu trên Pi, restart `iot-monitor` để nhập bộ mới.

Muốn thử huấn luyện trên dữ liệu này: `python train_forecast.py --include-weather-samples`. Metadata mô hình sẽ ghi rõ nguồn mô phỏng; mặc định huấn luyện vẫn loại dữ liệu mô phỏng.

Dữ liệu Open-Meteo cần ghi nguồn theo [CC BY 4.0](https://open-meteo.com/en/licence). Không thay nhãn nguồn khi xuất báo cáo.
"""
    (output.parent / "README_DATASET.md").write_text(report, encoding="utf-8")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--end", type=datetime.fromisoformat, help="Mặc định hiện tại theo giờ Việt Nam")
    parser.add_argument("--latitude", type=float, default=20.9714)
    parser.add_argument("--longitude", type=float, default=105.7788)
    parser.add_argument("--seed", type=int, default=20261110)
    parser.add_argument("--output", type=Path, default=DATA_FILE)
    parser.add_argument("--source-file", type=Path, help="Dùng JSON nguồn đã tải để tái lập offline")
    args = parser.parse_args()
    if args.days < 1 or args.days > 366:
        parser.error("Số ngày phải nằm trong 1..366")
    end = (args.end or now_local()).replace(second=0, microsecond=0)
    if end.tzinfo is not None or end > now_local():
        parser.error("Thời điểm cuối phải là giờ Việt Nam không kèm UTC offset và không ở tương lai")
    start = end - timedelta(days=args.days) + timedelta(seconds=SAMPLE_SEC)
    output = args.output.resolve()
    if args.source_file:
        source_file = args.source_file.resolve()
        envelope = json.loads(source_file.read_text(encoding="utf-8"))
    else:
        source_file = output.parent / "weather_sources" / f"ha_dong_ecmwf_ifs_{start:%Y%m%d}_{end:%Y%m%d}.json"
        envelope = download_weather(args.latitude, args.longitude, start, end, source_file)
    frame, hourly = synthesize(envelope, start, end, args.seed)
    metadata = write_dataset(frame, hourly, envelope, output, source_file, args.seed, args.latitude, args.longitude)
    print(json.dumps({key: metadata[key] for key in ("location", "start", "end", "row_count", "summary", "backup")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
