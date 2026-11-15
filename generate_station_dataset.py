"""Mô phỏng theo SYNOP Hà Đông và mô hình khí tượng; không giả nhãn sensor.

CSV ba cột giữ tương thích monitor. CSV *_sources.csv giải thích từng dòng.
Dữ liệu gốc và thời điểm tải được lưu để có thể tái lập bằng --reuse-sources.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from common import DATA_FILE, SAMPLE_SEC, now_local
from generate_weather_data import write_dataset

STATION = '48825'
LATITUDE, LONGITUDE = 20.9714, 105.7788
TIMEZONE = 'Asia/Ho_Chi_Minh'


def fetch(url, target, json_response=False):
    """Lưu cả phản hồi gốc và URL; lỗi nguồn phải dừng, không sinh số thay thế."""
    request = urllib.request.Request(url, headers={'User-Agent': 'IoT-HaDong-study/2.0'})
    with urllib.request.urlopen(request, timeout=45) as response:
        content = response.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    if json_response:
        body = json.loads(content)
        if body.get('error'):
            raise ValueError(body.get('reason', 'Weather API error'))
        envelope = {'request_url': url, 'downloaded_at': now_local().isoformat(), 'response': body}
        target.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        if b'AAXX' not in content:
            raise ValueError('Nguồn SYNOP không trả về bản tin AAXX')
        target.write_bytes(content)
    return {'url': url, 'file': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'downloaded_at': now_local().isoformat()}


def download_sources(folder, start, end, as_of):
    entries = []
    # Tải thêm mốc trước thời điểm đầu, nhưng tuyệt đối không yêu cầu quan trắc tương lai.
    cursor = (start - timedelta(hours=18) - timedelta(hours=7)).replace(minute=0)
    last = min(end, as_of) - timedelta(hours=7)
    while cursor <= last:
        finish = min(cursor + timedelta(days=20), last)
        params = {'block': STATION, 'begin': cursor.strftime('%Y%m%d%H%M'),
                  'end': finish.strftime('%Y%m%d%H%M')}
        url = 'https://www.ogimet.com/cgi-bin/getsynop?' + urllib.parse.urlencode(params)
        path = folder / f'ha_dong_synop_{cursor:%Y%m%d%H%M}_{finish:%Y%m%d%H%M}.csv'
        entry = fetch(url, path)
        entry['kind'] = 'synop'
        entries.append(entry)
        cursor = finish + timedelta(minutes=1)
    common = {'latitude': LATITUDE, 'longitude': LONGITUDE,
              'hourly': 'temperature_2m,relative_humidity_2m', 'timezone': TIMEZONE}
    archive_end = min(end.date(), (as_of - timedelta(days=1)).date())
    if start.date() <= archive_end:
        params = dict(common, start_date=start.date().isoformat(), end_date=archive_end.isoformat(),
                      models='ecmwf_ifs')
        url = 'https://archive-api.open-meteo.com/v1/archive?' + urllib.parse.urlencode(params)
        entry = fetch(url, folder / 'ha_dong_archive.json', True)
        entry['kind'] = 'archive'
        entries.append(entry)
    if end.date() >= as_of.date():
        params = dict(common, start_date=(as_of - timedelta(days=2)).date().isoformat(),
                      end_date=end.date().isoformat(), models='ecmwf_ifs')
        url = 'https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(params)
        entry = fetch(url, folder / 'ha_dong_forecast.json', True)
        entry['kind'] = 'forecast'
        entries.append(entry)
    manifest = {'schema': 1, 'start': start.isoformat(), 'end': end.isoformat(),
                'as_of': as_of.isoformat(), 'timezone': TIMEZONE, 'station_wmo': STATION,
                'station_name': 'HA DONG', 'station_coordinates': [20.9667, 105.7667],
                'station_identity_reference': 'https://www.jma.go.jp/jma/jma-eng/jma-center/rwc/semester/Quality_Performance_Report_202406.pdf',
                'excluded_source': 'Meteostat 48825 mixes Ha Dong coordinates with Cat Bi name/ICAO; not used.',
                'sources': entries}
    path = folder / 'sources.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def decode_synop(text, as_of):
    """Giải mã phần 1 FM12; không nhầm nhóm nhiệt độ cực trị sau 333."""
    rows, rejected = [], 0
    for fields in csv.reader(io.StringIO(text)):
        try:
            if len(fields) < 7 or fields[0] != STATION:
                raise ValueError('station/header')
            stamp = datetime(*map(int, fields[1:6])) + timedelta(hours=7)
            if stamp > as_of:
                raise ValueError('future observation')
            report = ','.join(fields[6:]).strip()
            words = report.rstrip('=').split()
            if len(words) < 7 or words[0] != 'AAXX' or words[2] != STATION:
                raise ValueError('not station AAXX')
            if words[1][:4] != (stamp - timedelta(hours=7)).strftime('%d%H'):
                raise ValueError('report time mismatch')
            groups = []
            for word in words[5:]:
                if word in {'222', '333', '444', '555'} or word.startswith('222'):
                    break
                groups.append(word)
            tg = next(w for w in groups if re.fullmatch(r'1[01]\d{3}', w))
            hg = next(w for w in groups if re.fullmatch(r'2[019]\d{3}', w))
            temp = int(tg[2:]) / 10 * (-1 if tg[1] == '1' else 1)
            dewpoint = None
            if hg[1] == '9':
                hum = float(hg[2:])
                method = 'SYNOP 29UUU'
            else:
                dewpoint = int(hg[2:]) / 10 * (-1 if hg[1] == '1' else 1)
                if not -80 <= dewpoint <= temp + .3:
                    raise ValueError('dewpoint')
                hum = float(100 * np.exp(17.67 * dewpoint / (243.5 + dewpoint)
                                         - 17.67 * temp / (243.5 + temp)))
                hum = min(100., hum)
                method = 'RH from SYNOP T/Td, Magnus 17.67/243.5'
            if not (-40 <= temp <= 80 and 0 <= hum <= 100):
                raise ValueError('range')
            rows.append({'time': stamp, 'temp': temp, 'hum': hum, 'dewpoint': dewpoint,
                         'humidity_method': method, 'station_wmo': STATION, 'raw_synop': report})
        except (ValueError, StopIteration, TypeError):
            rejected += 1
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError('Không có bản tin SYNOP Hà Đông hợp lệ')
    return frame.sort_values('time').drop_duplicates('time', keep='last').set_index('time'), rejected


def load_sources(folder, manifest):
    station_text, grids = [], []
    as_of = datetime.fromisoformat(manifest['as_of'])
    for item in manifest['sources']:
        path = folder / item['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError(f'Hash nguồn không khớp: {path.name}')
        if item['kind'] == 'synop':
            station_text.append(path.read_text(encoding='utf-8'))
            continue
        payload = json.loads(path.read_text(encoding='utf-8'))['response']
        # Kiểm tra vị trí trả về của API, không chỉ tin tên/mã trạm.
        if abs(payload['latitude'] - LATITUDE) > .15 or abs(payload['longitude'] - LONGITUDE) > .15:
            raise ValueError('Ô lưới không thuộc khu vực Hà Đông')
        hourly = pd.DataFrame(payload['hourly']).rename(columns={
            'temperature_2m': 'temp', 'relative_humidity_2m': 'hum'})
        hourly['time'] = pd.to_datetime(hourly['time'])
        hourly['origin'] = item['kind']
        grids.append(hourly)
    station, rejected = decode_synop('\n'.join(station_text), as_of)
    # Archive được ưu tiên tại timestamp trùng với forecast.
    grid = pd.concat(grids).sort_values('origin').drop_duplicates('time', keep='first').set_index('time').sort_index()
    return station, grid, rejected


def simulate(station, grid, start, end, as_of, seed=20261116, max_station_gap_hours=12):
    """Giữ mốc SYNOP; nội suy có mô hình nền và nhiễu nhỏ đã gắn nguồn."""
    stamps = pd.date_range(start, end, freq=f'{SAMPLE_SEC}s')
    if start.tzinfo is not None or end.tzinfo is not None or as_of.tzinfo is not None:
        raise ValueError('Dùng giờ Việt Nam không kèm UTC offset')
    required = pd.date_range(stamps[0].floor('h'), stamps[-1].ceil('h'), freq='h')
    hours = grid.reindex(required)
    values = hours[['temp', 'hum']].apply(pd.to_numeric, errors='coerce')
    if values.isna().any().any() or not np.isfinite(values.to_numpy()).all():
        raise ValueError('Nguồn mô hình không phủ đủ các giờ; không tự lấp dữ liệu thiếu')
    if not values.temp.between(-40, 80).all() or not values.hum.between(0, 100).all():
        raise ValueError('Nguồn mô hình nằm ngoài dải hợp lệ')
    union = grid.index.union(stamps).union(station.index)
    modeled = grid[['temp', 'hum']].reindex(union).sort_index().interpolate('time', limit_area='inside')
    base = modeled.reindex(stamps).to_numpy()
    valid_station = station.loc[(station.index >= modeled.index[0]) & (station.index <= min(as_of, end))]
    valid_station = valid_station[modeled.reindex(valid_station.index).notna().all(axis=1)]
    if valid_station.empty:
        raise ValueError('Không có mốc trạm để điều chỉnh mô hình')
    residual = valid_station[['temp', 'hum']] - modeled.reindex(valid_station.index)
    offset = residual.reindex(residual.index.union(stamps)).sort_index().interpolate('time', limit_area='inside').reindex(stamps)
    station_ns = valid_station.index.asi8
    minute_ns = stamps.asi8
    right = np.searchsorted(station_ns, minute_ns, side='left')
    exact = (right < len(station_ns)) & (station_ns[np.minimum(right, len(station_ns)-1)] == minute_ns)
    left_idx = np.maximum(right - 1, 0)
    right_idx = np.minimum(right, len(station_ns)-1)
    bracketed = (right > 0) & (right < len(station_ns))
    gap = (station_ns[right_idx] - station_ns[left_idx]) / 3.6e12
    adjusted = (bracketed & (gap <= max_station_gap_hours)) | exact
    result = base.copy()
    result[adjusted] += offset.to_numpy()[adjusted]
    # Làm chênh lệch trạm giảm dần ở mép khoảng trống thay vì nhảy đột ngột
    # từ trạm sang mô hình tại phút kế tiếp. Không tạo thêm mốc quan trắc.
    left_distance = np.abs(minute_ns-station_ns[left_idx]) / 3.6e12
    right_distance = np.abs(minute_ns-station_ns[right_idx]) / 3.6e12
    nearest = np.where(left_distance<=right_distance,left_idx,right_idx)
    distance = np.minimum(left_distance,right_distance)
    taper = (~adjusted) & (distance<3)
    weight = np.maximum(0,1-distance[taper]/3)
    result[taper] += residual.to_numpy()[nearest[taper]] * weight[:,None]
    adjusted |= taper
    # Cầu nhiễu bằng 0 tại mỗi giờ, do đó giữ đúng tất cả mốc SYNOP (giờ tròn).
    rng = np.random.default_rng(seed)
    innovation = rng.multivariate_normal([0, 0], [[.025**2, -.003], [-.003, .25**2]], len(stamps))
    noise = np.zeros_like(innovation)
    for i in range(1, len(stamps)):
        noise[i] = .92 * noise[i-1] + innovation[i]
    phase = (stamps.minute.to_numpy()*60 + stamps.second.to_numpy()) / 3600
    result += noise * np.sin(np.pi * phase)[:, None]
    result[exact] = valid_station[['temp','hum']].reindex(stamps[exact]).to_numpy()
    result[:,0] = np.clip(result[:,0], -40, 80)
    result[:,1] = np.clip(result[:,1], 0, 100)
    # Nếu một trong hai mốc nội suy là forecast, bản ghi vẫn ghi là có dùng forecast.
    floor = stamps.floor('h')
    ceil = stamps.ceil('h')
    uses_forecast = ((grid.origin.reindex(floor).to_numpy() == 'forecast') |
                     (grid.origin.reindex(ceil).to_numpy() == 'forecast'))
    future = stamps > as_of
    origin = np.where(uses_forecast, 'forecast_model', 'archive_model').astype(object)
    origin[adjusted] = 'station_adjusted_model'
    origin[exact] = 'station_anchor_rounded'
    frame = pd.DataFrame({'time': stamps.strftime('%Y-%m-%dT%H:%M:%S'),
                          'temp': result[:,0].round(1), 'hum': result[:,1].round(1)})
    evidence = frame.copy()
    evidence['source'] = 'weather_sample'
    evidence['weather_origin'] = origin
    evidence['uses_forecast_model'] = uses_forecast
    evidence['future_at_generation'] = future
    evidence['station_wmo'] = np.where(adjusted, STATION, '')
    return frame, evidence


def create_dataset(output, source_dir, manifest, seed):
    start, end, as_of = (datetime.fromisoformat(manifest[k]) for k in ('start','end','as_of'))
    station, grid, rejected = load_sources(source_dir, manifest)
    frame, evidence = simulate(station, grid, start, end, as_of, seed)
    source_index = source_dir / 'sources.json'
    envelope = {'request_url': 'https://www.ogimet.com/cgi-bin/getsynop',
                'response': {'latitude': LATITUDE, 'longitude': LONGITUDE, 'elevation': 6}}
    # Dùng writer cũ để giữ sao lưu và manifest prefix tương thích với Store.import_csv.
    metadata = write_dataset(frame, grid[['temp','hum']], envelope, output, source_index,
                             seed, LATITUDE, LONGITUDE)
    references = output.parent/'weather_sources'/'reference'
    references.mkdir(parents=True,exist_ok=True)
    sources_csv = references/(output.stem + '_sources.csv')
    if sources_csv.exists() and metadata['backup']:
        import shutil
        backup = output.parent / metadata['backup']
        shutil.copy2(sources_csv, backup.with_name(backup.stem + '_sources.csv'))
    evidence.to_csv(sources_csv, index=False, lineterminator='\n')
    station.to_csv(references/'ha_dong_station_observations.csv', index_label='time', lineterminator='\n')
    grid.to_csv(references/'weather_hourly_reference.csv', index_label='time', lineterminator='\n')
    eligible = ~(evidence['uses_forecast_model'] | evidence['future_at_generation'])
    groups = eligible.ne(eligible.shift()).cumsum()
    training_ranges = [{'start':part.time.iloc[0],'end':part.time.iloc[-1],'rows':len(part)}
                       for _,part in evidence.loc[eligible].groupby(groups.loc[eligible])]
    last_ten = evidence[pd.to_datetime(evidence.time) > end-timedelta(days=10)]
    metadata.update({
        'as_of': as_of.isoformat(), 'source_provider': 'Ogimet SYNOP HA DONG WMO 48825 + Open-Meteo ECMWF IFS',
        'source_url': manifest['sources'][0]['url'],
        'source_urls': [item['url'] for item in manifest['sources']],
        'source_resolution': 'Irregular 3-hourly SYNOP anchors; hourly weather-model fallback',
        'source_last_used': grid.loc[grid.index<=end].index[-1].isoformat(), 'tail_hold_minutes': 0,
        'station_wmo': STATION, 'station_name': 'HA DONG',
        'station_observations': len(station), 'rejected_station_lines': rejected,
        'last_station_observation': station.index[-1].isoformat(),
        'future_at_generation_rows': int(evidence.future_at_generation.sum()),
        'forecast_model_rows': int(evidence.uses_forecast_model.sum()),
        'origin_counts': {str(k): int(v) for k,v in evidence.weather_origin.value_counts().items()},
        'row_sources_file': sources_csv.relative_to(output.parent).as_posix(),
        'training_time_ranges':training_ranges,
        'row_sources_sha256': hashlib.sha256(sources_csv.read_bytes()).hexdigest(),
        'last_10_days': {'start_exclusive': (end-timedelta(days=10)).isoformat(), 'end': end.isoformat(),
                         'rows': len(last_ten), 'sensor_rows': 0, 'weather_sample_rows': len(last_ten),
                         'note': 'Không có tệp số đo Pi được cung cấp; không đổi nhãn mô phỏng thành sensor.'},
        'method': 'Interpolate station-minus-model residuals between anchors <=12h apart; taper nearest residual to zero over 3h at gap edges; add to hourly interpolated model; small seeded correlated noise zero at full hours; round 0.1. Distant gaps use model. Future uses forecast, never sensor.',
        'limitations': ['Not DHT22 observations. Every generated row remains weather_sample.',
                       'RH derived from station temperature/dewpoint is approximate; raw SYNOP retained.',
                       'Station-adjusted values use retrospective interpolation, not independent minute observations.',
                       'Archive/forecast model fills and future values are not station observations.',
                       'Do not report forecast/simulation test scores as measured DHT22 accuracy.']})
    output.with_suffix('.provenance.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    text = f'''# Dữ liệu mô phỏng hai tháng cho Hà Đông

- Khoảng: **{start.isoformat()} → {end.isoformat()}**, giờ Việt Nam, gồm hai đầu mút.
- **{len(frame):,} dòng**, chu kỳ {SAMPLE_SEC} giây; toàn bộ có nhãn **weather_sample**.
- Thời điểm chốt nguồn: **{as_of.isoformat()}**. Có **{metadata['future_at_generation_rows']:,} dòng ở tương lai** tại thời điểm tạo; đây là mô phỏng theo dự báo.
- Trạm chính: **HA DONG, WMO 48825**, khoảng 20.9667°B, 105.7667°Đ. Đã đọc {len(station)} bản tin hợp lệ. Bản tin cuối: {station.index[-1].isoformat()}.
- Nhiệt độ đọc từ nhóm SYNOP 1snTTT; độ ẩm đọc từ 29UUU hoặc tính từ nhiệt độ và điểm sương 2snTdTdTd bằng công thức Magnus (17.67/243.5).
- Không dùng Meteostat 48825 vì metadata gắn lẫn tên/ICAO Cát Bi với tọa độ Hà Đông.

## Phương pháp và nguồn

Đọc bản tin SYNOP của trạm Hà Đông qua [Ogimet](https://www.ogimet.com/). Tính chênh lệch trạm so với mô hình nền ECMWF IFS; chỉ nội suy chênh lệch giữa hai mốc trạm cách nhau không quá 12 giờ. Cộng chênh lệch này vào mô hình nền. Ở mép khoảng thiếu, chênh lệch tại mốc trạm gần nhất giảm dần về 0 trong 3 giờ để chuyển tiếp liên tục sang mô hình. Khoảng xa mốc trạm dùng mô hình; thời gian chưa đến dùng dự báo. Thêm nhiễu AR(1) nhỏ bằng seed {seed}, bằng 0 ở giờ tròn, rồi làm tròn 0,1. Phương pháp dùng nội suy hồi cứu và không tạo ra quan trắc độc lập mỗi phút.

Mô hình nền: [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api); phần gần hiện tại/tương lai: [Open-Meteo ECMWF Forecast](https://open-meteo.com/en/docs/ecmwf-api). Ghi nguồn theo [CC BY 4.0](https://open-meteo.com/en/licence).

SYNOP chính thức tham khảo [WMO Manual on Codes](https://amc.namem.gov.mn/wp-content/uploads/WMO1/4.%20306_i1_2019_en.pdf). Định danh trạm được đối chiếu với [JMA RWC](https://www.jma.go.jp/jma/jma-eng/jma-center/rwc/semester/Quality_Performance_Report_202406.pdf).

## Các tệp

- `{output.name}`: bốn cột time,temp,hum,label tương thích monitor. Tên sensor_data không có nghĩa mọi dòng là số đo thật.
- `{sources_csv.relative_to(output.parent).as_posix()}`: nguồn tham chiếu từng dòng; có cờ dùng mô hình forecast và cờ tương lai tại thời điểm tạo.
- `{output.with_suffix('.provenance.json').name}`: manifest có SHA-256 kiểm tra prefix mô phỏng; vẫn cho phép monitor append số đo thật phía sau.
- `weather_sources/reference/ha_dong_station_observations.csv`: bản tin trạm đã giải mã, điểm sương và nguyên văn SYNOP; thời gian đổi UTC sang Việt Nam +7.
- `weather_sources/reference/weather_hourly_reference.csv`: mô hình nền theo giờ, phân biệt archive/forecast.
- `weather_daily_summary.csv`: thống kê dữ liệu mô phỏng theo ngày.
- `{source_index.relative_to(output.parent).as_posix()}`: URL, thời điểm tải, hash và danh sách tệp nguồn gốc.
- `backups/`: bản sao CSV, manifest và nguồn từng dòng trước khi thay.

## Mười ngày gần nhất

Khoảng ({end-timedelta(days=10)}, {end}] gồm {len(last_ten):,} dòng mô phỏng, **0 dòng sensor**. Chưa có tệp SQLite số đo thật từ Pi được cung cấp. Chỉ các bản ghi thực do monitor đọc DHT22 và lưu source=sensor mới được gọi là sensor. Quan trắc ở trạm khí tượng cũng không phải số đo của DHT22 nhóm.

Sau khi chạy Pi, đọc `data/iot.sqlite3` qua Store.frame() rồi lọc trường source='sensor' để lấy số đo thật. Bảng readings không chứa cột source; nguồn nằm trong reading_origins.origin. Không đổi nhãn lịch sử mô phỏng. Phần thời gian chưa xảy ra không thể có số đo thật.

## Tái lập và huấn luyện

Tái lập offline trong thư mục dự án:

```powershell
python generate_station_dataset.py --reuse-sources "{source_index.relative_to(Path(__file__).resolve().parent).as_posix()}" --seed {seed}
```

Mô hình hiện có chưa được huấn luyện lại trên bộ mới. Không dùng phần forecast làm giá trị quan trắc để tuyên bố độ chính xác. Mặc định train_forecast.py loại weather_sample; cờ --include-weather-samples chỉ dành cho thử nghiệm mô phỏng. Bản báo cáo cuối kỳ trước đó mô tả bộ dữ liệu/mô hình cũ, không tự cập nhật theo lần tạo này.
'''
    (output.parent/'README_DATASET.md').write_text(text,encoding='utf-8')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=datetime.fromisoformat)
    parser.add_argument('--end', type=datetime.fromisoformat)
    parser.add_argument('--seed', type=int, default=20261116)
    parser.add_argument('--output', type=Path, default=DATA_FILE)
    parser.add_argument('--reuse-sources', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.reuse_sources:
        source_index = args.reuse_sources.resolve()
        manifest = json.loads(source_index.read_text(encoding='utf-8'))
        source_dir = source_index.parent
        if args.start is not None or args.end is not None:
            parser.error('--reuse-sources giữ nguyên khoảng thời gian trong manifest')
    else:
        if args.start is None or args.end is None:
            parser.error('Cần --start và --end theo giờ Việt Nam')
        as_of = now_local().replace(second=0,microsecond=0)
        if any(t.tzinfo is not None for t in (args.start,args.end)):
            parser.error('Thời điểm phải là giờ Việt Nam không kèm UTC offset')
        if args.start >= args.end or args.start > as_of:
            parser.error('Cần start < end và start không ở tương lai')
        if (args.end-args.start).days > 366 or args.end > as_of+timedelta(days=7):
            parser.error('Tối đa 366 ngày lịch sử và 7 ngày dự báo')
        if any(t.second or t.microsecond for t in (args.start,args.end)):
            parser.error('Mốc đầu/cuối phải tròn phút')
        source_dir = output.parent/'weather_sources'/f'period_{args.start:%Y%m%d%H%M}_{args.end:%Y%m%d%H%M}_{as_of:%Y%m%d%H%M}'
        manifest = download_sources(source_dir,args.start,args.end,as_of)
    result = create_dataset(output,source_dir,manifest,args.seed)
    print(json.dumps({k:result[k] for k in ('start','end','row_count','station_observations',
                     'future_at_generation_rows','origin_counts','last_10_days','backup')},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
