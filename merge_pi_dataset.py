"""Ghép bản ghi source=sensor thật từ snapshot Pi vào bộ mô phỏng, giữ nguyên số đo."""
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from common import now_local
from storage import Store, readings_with_origins_sql


def sensor_records(snapshot, start_exclusive, end, as_of):
    """Đọc DB ở chế độ read-only; không suy nguồn sensor từ tên file hay CSV."""
    snapshot = Path(snapshot).resolve()
    if not snapshot.is_file():
        raise ValueError('Không tìm thấy snapshot Pi')
    with sqlite3.connect(snapshot.as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Snapshot SQLite không toàn vẹn')
        frame = pd.read_sql_query("SELECT time,temp,hum,origin AS source,state,reasons FROM (" + readings_with_origins_sql(db) + ") WHERE origin='sensor' ORDER BY time",db)
    if frame.empty:
        return frame
    stamps = pd.to_datetime(frame.time,errors='raise')
    if stamps.dt.tz is not None:
        stamps = stamps.dt.tz_convert('Asia/Ho_Chi_Minh').dt.tz_localize(None)
    frame = frame[(stamps>start_exclusive)&(stamps<=end)].copy()
    stamps = stamps.loc[frame.index]
    if (stamps>as_of).any():
        raise ValueError('DB có nhãn sensor ở tương lai; cần kiểm tra đồng hồ Pi')
    if not (np.isfinite(frame[['temp','hum']].to_numpy()).all() and
            frame.temp.between(-40,80).all() and frame.hum.between(0,100).all()):
        raise ValueError('Số đo sensor ngoài dải hợp lệ')
    frame['time'] = stamps.dt.strftime('%Y-%m-%dT%H:%M:%S')
    return frame.reset_index(drop=True)


def combine(evidence, sensor):
    """Thay mẫu mô phỏng cùng phút bằng số đo thật, giữ timestamp/giá trị gốc."""
    if sensor.empty:
        return evidence.copy()
    generated = evidence.copy()
    actual_minutes = pd.to_datetime(sensor.time).dt.floor('min')
    generated = generated[~pd.to_datetime(generated.time).dt.floor('min').isin(actual_minutes)]
    actual = sensor[['time','temp','hum','source']].copy()
    actual['weather_origin'] = 'dht22'
    actual['uses_forecast_model'] = False
    actual['future_at_generation'] = False
    actual['station_wmo'] = ''
    return pd.concat([generated,actual],ignore_index=True).sort_values('time').reset_index(drop=True)


def merge(folder, snapshot, expected_hash=None, source_description=None):
    folder,snapshot = Path(folder).resolve(),Path(snapshot).resolve()
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise ValueError('Hash snapshot khác giá trị đọc trên Pi')
    metadata = json.loads((folder/'sensor_data.provenance.json').read_text(encoding='utf-8'))
    end = datetime.fromisoformat(metadata['end'])
    cutoff = end-timedelta(days=10)
    imported_at = now_local()
    sensor = sensor_records(snapshot,cutoff,end,imported_at)
    evidence = pd.read_csv(folder/'sensor_data_sources.csv',keep_default_na=False)
    merged = combine(evidence,sensor)
    combined_path = folder/'combined_data.csv'
    merged.to_csv(combined_path,index=False,lineterminator='\n')
    sensor_path = folder/'pi_sensor_readings.csv'
    sensor.to_csv(sensor_path,index=False,lineterminator='\n')
    # DB đóng gói chỉ chứa số liệu và định danh bộ dữ liệu, không sao chép kv
    # cấu hình/mật khẩu/email riêng tư từ Pi.
    db_path = folder/'combined_data.sqlite3'
    output_store = Store(db_path)
    with output_store.connection() as db:
        db.execute('DELETE FROM readings')
        sensor_state = sensor.set_index('time')[['state','reasons']].to_dict('index') if len(sensor) else {}
        records = []
        for row in merged.itertuples(index=False):
            state = sensor_state.get(row.time,{'state':'XANH','reasons':'[]'})
            records.append((row.time,float(row.temp),float(row.hum),row.source,state['state'],state['reasons']))
        db.executemany('INSERT INTO readings VALUES (?,?,?,?,?)',[(r[0],r[1],r[2],r[4],r[5]) for r in records])
        db.executemany('INSERT INTO reading_origins VALUES (?,?)',[(r[0],r[3]) for r in records])
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('DB ghép không toàn vẹn')
    last_ten = merged[pd.to_datetime(merged.time)>cutoff]
    result = {'schema':1,'kind':'mixed_weather_simulation_and_pi_sensor',
              'start':metadata['start'],'end':metadata['end'],'timezone':'Asia/Ho_Chi_Minh',
              'imported_at':imported_at.isoformat(),'weather_generated_as_of':metadata['as_of'],
              'sensor_original':source_description,'snapshot_sha256':digest,
              'source_counts':{str(k):int(v) for k,v in merged.source.value_counts().items()},
              'row_count':len(merged),'sensor_start':None if sensor.empty else sensor.time.min(),
              'sensor_end':None if sensor.empty else sensor.time.max(),
              'last_10_days':{'start_exclusive':cutoff.isoformat(),'end':metadata['end'],
                              'source_counts':{str(k):int(v) for k,v in last_ten.source.value_counts().items()},
                              'continuous_sensor_10_days':False},
              'method':'Replace weather simulation in sensor minute buckets with original Pi readings, retaining raw sensor timestamps, values, state and reasons. Other slots remain weather_sample. No sensor interpolation.',
              'raw_sensor_file':sensor_path.name,'raw_sensor_sha256':hashlib.sha256(sensor_path.read_bytes()).hexdigest(),
              'combined_csv_sha256':hashlib.sha256(combined_path.read_bytes()).hexdigest(),
              'database_sha256':hashlib.sha256(db_path.read_bytes()).hexdigest(),
              'future_at_weather_generation_rows':int(merged.future_at_generation.sum()),
              'limitations':['The final 10 days are mixed, not 10 days of continuous DHT22 measurements.',
                             'Forecast timestamps that had not occurred when weather sources were downloaded are predictions, not observations.',
                             'Only source=sensor rows came from the provided Pi SQLite snapshot.',
                             'combined_data.csv includes source labels; default Store.import_csv does not restore sensor labels from an arbitrary CSV. Use the supplied SQLite or merge helper.']}
    (folder/'combined_data.provenance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    readme = folder/'WEATHER_DATA_README.md'
    text = readme.read_text(encoding='utf-8')
    marker = '\n## Ghép số đo thật từ Pi\n'
    text = text.split(marker)[0]
    text = text.replace('Chưa có tệp SQLite số đo thật từ Pi được cung cấp.',
                        'Đây là phần mô phỏng nền; kết quả ghép số đo Pi được ghi riêng bên dưới.')
    text = text.replace('Không có tệp số đo Pi được cung cấp; không đổi nhãn mô phỏng thành sensor.',
                        'Phần nền chỉ gồm weather_sample; xem combined_data.csv để có số đo Pi đã ghép.')
    title = '# Bộ dữ liệu hai tháng đã ghép số đo Pi\n'
    if not text.startswith(title):
        text = (title+f'\n**Dùng combined_data.csv hoặc combined_data.sqlite3** cho bộ đã ghép. '
                f'Có {len(merged):,} dòng, trong đó **{len(sensor)} bản ghi sensor thật** từ Pi; '
                f'các dòng còn lại là weather_sample. Chưa có đủ 10 ngày số đo thật. '
                f'Giữ nguyên timestamp và giá trị của sensor, kể cả nhiều lần đọc trong cùng phút.\n\n'
                +text.replace('# Dữ liệu mô phỏng hai tháng cho Hà Đông','## Dữ liệu mô phỏng nền cho Hà Đông',1))
    text += f'''
## Ghép số đo thật từ Pi

Đã đọc SQLite backup từ `{source_description}` lúc {imported_at.isoformat()}. Snapshot SHA-256: `{digest}`. Chỉ chọn source=sensor trong ({cutoff}, {end}]. Có **{len(sensor)} bản ghi thật**, từ **{result['sensor_start']}** đến **{result['sensor_end']}**; không có đủ 10 ngày quan trắc liên tục. Bản dữ liệu nền ở trên vẫn là mô phỏng; bộ ghép nằm trong các tệp sau:

- **combined_data.csv**: bộ ghép theo thời gian, có cột source, weather_origin, uses_forecast_model, future_at_generation. Số đo thật giữ nguyên giá trị và timestamp; bỏ mẫu mô phỏng cùng phút để tránh đếm hai lần. Khoảng thiếu vẫn weather_sample, không nội suy rồi gắn nhãn sensor.
- **combined_data.sqlite3**: DB dùng cấu trúc readings/kv của project, giữ nguồn sensor cho số đo thật. Không mang theo cấu hình/mật khẩu riêng tư trong kv của Pi.
- **pi_sensor_readings.csv**: số đo thật nguyên gốc đã lọc, kèm trạng thái và reasons.
- **combined_data.provenance.json**: số lượng theo nguồn, hash đầu vào/đầu ra và giới hạn sử dụng.

Bộ ghép có {len(merged):,} dòng; theo nguồn: {json.dumps(result['source_counts'],ensure_ascii=False)}. Trong 10 ngày cuối: {json.dumps(result['last_10_days']['source_counts'],ensure_ascii=False)}. Các khoảng trống thể hiện đúng việc Pi chưa ghi đủ dữ liệu, không phải 10 ngày chạy liên tục.

Không dùng lệnh import CSV mặc định để phục hồi nhãn sensor từ combined_data.csv vì importer hiện chỉ tin nguồn ghi trực tiếp của monitor và manifest prefix. Dùng DB ghép để giữ nhãn nguồn. Tệp CSV/DB đang hoạt động ở thư mục data gốc và mô hình hiện có chưa bị thay bởi bộ mới này.
'''
    readme.write_text(text,encoding='utf-8')
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--sensor-db',type=Path,required=True)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--source-description',default='Pi SQLite snapshot supplied by group')
    args=parser.parse_args()
    print(json.dumps(merge(args.folder,args.sensor_db,args.expected_sha256,args.source_description),ensure_ascii=False,indent=2))
