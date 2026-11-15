"""Kiểm tra nguồn trạm, nhãn dữ liệu và thời gian của bộ mô phỏng mới."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from generate_station_dataset import decode_synop, simulate
from merge_pi_dataset import combine, sensor_records


def test_synop_section_one_station_time_and_humidity():
    raw = ('48825,2026,11,15,00,00,AAXX 15001 48825 31996 00000 '
           '10206 20198 30133 333 11000 21100=\n'
           '48825,2026,11,15,03,00,AAXX 15031 48825 32497 71402 '
           '10250 29080 30136=\n')
    frame, rejected = decode_synop(raw, datetime(2026,11,15,11))
    assert rejected == 0
    assert list(frame.index.hour) == [7,10]  # UTC -> Việt Nam
    assert frame.temp.tolist() == [20.6,25.0]  # Không lấy cực trị trong 333
    assert 94 < frame.hum.iloc[0] < 96
    assert frame.hum.iloc[1] == 80


def test_synop_rejects_wrong_station_future_and_mismatched_time():
    valid = '48825,2026,11,15,00,00,AAXX 15001 48825 31996 00000 10206 20198='
    raw = '\n'.join([valid, valid.replace('48825','48826'),
                     valid.replace('15,00,00','15,03,00').replace('15001','15031'),
                     valid.replace('15001','16001')])
    frame, rejected = decode_synop(raw, datetime(2026,11,15,8))
    assert len(frame) == 1 and rejected == 3


def fixtures():
    start = datetime(2026,11,15,0)
    stamps = pd.date_range(start,periods=25,freq='h')
    grid = pd.DataFrame({'temp':20.,'hum':70.,'origin':'archive'},index=stamps)
    grid.loc[stamps>=start+timedelta(hours=12),'origin'] = 'forecast'
    station = pd.DataFrame({'temp':[22.,24.],'hum':[80.,60.]},index=stamps[[0,6]])
    return start,grid,station


def test_station_anchors_preserved_and_future_never_sensor():
    start,grid,station = fixtures()
    as_of = start+timedelta(hours=12,minutes=30)
    frame,evidence = simulate(station,grid,start,start+timedelta(hours=18),as_of)
    assert len(frame) == 18*60+1
    assert frame.temp.iloc[0] == 22 and frame.temp.iloc[360] == 24
    assert frame.hum.iloc[0] == 80 and frame.hum.iloc[360] == 60
    assert evidence.source.eq('weather_sample').all()
    assert evidence.future_at_generation.sum() == 330
    assert evidence.loc[evidence.future_at_generation,'uses_forecast_model'].all()
    assert evidence.weather_origin.iloc[180] == 'station_adjusted_model'
    assert evidence.weather_origin.iloc[-1] == 'forecast_model'
    assert frame.equals(simulate(station,grid,start,start+timedelta(hours=18),as_of)[0])


def test_long_station_gap_uses_model_not_station_interpolation():
    start,grid,_ = fixtures()
    station = pd.DataFrame({'temp':[10.,10.],'hum':[90.,90.]},index=grid.index[[0,18]])
    frame,evidence = simulate(station,grid,start,start+timedelta(hours=24),start+timedelta(hours=24))
    assert frame.temp.iloc[360] == 20
    assert evidence.weather_origin.iloc[360] == 'archive_model'
    assert evidence.weather_origin.iloc[18*60] == 'station_anchor_rounded'


def test_missing_hour_is_error_not_fabricated_fill():
    start,grid,station = fixtures()
    grid = grid.drop(grid.index[4])
    with pytest.raises(ValueError,match='không phủ đủ'):
        simulate(station,grid,start,start+timedelta(hours=18),start+timedelta(hours=12))


def test_nonfinite_weather_is_rejected():
    start,grid,station = fixtures()
    grid.iloc[4,0] = np.inf
    with pytest.raises(ValueError,match='không phủ đủ'):
        simulate(station,grid,start,start+timedelta(hours=18),start+timedelta(hours=12))


def test_transition_to_model_tapers_instead_of_jumping():
    start,grid,_ = fixtures()
    station = pd.DataFrame({'temp':[10.,10.],'hum':[90.,90.]},index=grid.index[[0,18]])
    frame,evidence = simulate(station,grid,start,start+timedelta(hours=24),start+timedelta(hours=24))
    assert abs(frame.temp.iloc[1]-frame.temp.iloc[0]) <= .2
    assert evidence.weather_origin.iloc[1] == 'station_adjusted_model'


def test_combining_replaces_minute_without_changing_sensor_timestamp_or_values():
    start,grid,station = fixtures()
    _,evidence = simulate(station,grid,start,start+timedelta(hours=18),start+timedelta(hours=12))
    actual = pd.DataFrame([{'time':'2026-11-15T01:00:37','temp':23.456,'hum':61.234,
                           'source':'sensor','state':'XANH','reasons':'[]'}])
    result = combine(evidence,actual)
    sensor = result[result.source=='sensor']
    assert len(result) == len(evidence) and len(sensor)==1
    assert sensor.time.iloc[0] == actual.time.iloc[0]
    assert sensor.temp.iloc[0] == 23.456 and sensor.hum.iloc[0] == 61.234
    assert '2026-11-15T01:00:00' not in result.time.values
    assert not sensor.future_at_generation.any() and not sensor.uses_forecast_model.any()


@pytest.mark.parametrize('compact',[False,True])
def test_snapshot_uses_only_real_sensor_rows_and_rejects_future(tmp_path,compact):
    import sqlite3
    p = tmp_path/'pi.sqlite3'
    with sqlite3.connect(p) as db:
        db.execute('CREATE TABLE readings(time TEXT PRIMARY KEY,temp REAL,hum REAL,source TEXT,state TEXT,reasons TEXT)')
        db.executemany('INSERT INTO readings VALUES(?,?,?,?,?,?)',[
            ('2026-11-15T01:00:37',23.,61.,'sensor','XANH','[]'),
            ('2026-11-15T01:01:00',24.,62.,'weather_sample','XANH','[]')])
    if compact:
        from storage import Store
        Store(p)
    actual = sensor_records(p,datetime(2026,11,5),datetime(2026,11,16),datetime(2026,11,15,2))
    assert len(actual) == 1 and actual.source.iloc[0] == 'sensor'
    with pytest.raises(ValueError,match='tương lai'):
        sensor_records(p,datetime(2026,11,5),datetime(2026,11,16),datetime(2026,11,15))
