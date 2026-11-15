"""One shared CSV, full opted-in simulation and subsequently measured data."""
from datetime import datetime
import hashlib
import json
import importlib.util

import numpy as np
import pandas as pd
import pytest

import common as config
from provenance import prepare_sensor_csv, weather_training_ranges
from train_forecast import csv_training_frame, eligible_training_frame


def write_base(path):
    content=(b'time,temp,hum,label\n2026-11-14T23:00:00,20,80,sensor\n'
             b'2026-11-14T23:01:00,21,81,sensor\n')
    path.write_bytes(content)
    digest=hashlib.sha256(content).hexdigest()
    meta={'kind':'weather_derived_synthetic','csv_prefix_bytes':len(content),
          'csv_prefix_sha256':digest,'dataset_id':digest,'row_count':2,
          'start':'2026-11-14T23:00:00','end':'2026-11-14T23:01:00',
          'location':'QA','source_provider':'QA','source_url':'https://example.test',
          'row_metadata_file':'dataset.provenance.json'}
    path.with_suffix('.provenance.json').write_text(json.dumps(meta),encoding='utf-8')
    policy={'rows':2,'files_sha256':{path.name:digest},'training_time_ranges':[
        {'start':'2026-11-14T23:00:00','end':'2026-11-14T23:00:00','rows':1}]}
    (path.parent/'dataset.provenance.json').write_text(json.dumps(policy),encoding='utf-8')


def test_generation_forecast_is_included_as_simulation_without_changing_its_source(tmp_path):
    path=tmp_path/'sensor_data.csv'
    write_base(path)
    frame=csv_training_frame(path)
    assert frame.source.tolist()==['weather_sample','weather_sample']
    # The historical generation-time policy allowed only the first row.
    assert weather_training_ranges(path)==[['2026-11-14T23:00:00','2026-11-14T23:00:00']]
    eligible=eligible_training_frame(frame,True,datetime(2026,11,20))
    assert eligible.temp.tolist()==[20,21]
    assert eligible.source.eq('weather_sample').all()
    assert eligible_training_frame(frame,False,datetime(2026,11,20)).empty


def test_new_sensor_is_kept_even_in_a_previously_excluded_weather_minute(tmp_path):
    path=tmp_path/'sensor_data.csv'
    write_base(path)
    prepare_sensor_csv(path)
    with path.open('a',encoding='utf-8') as stream:
        stream.write('2026-11-14T23:01:35,25.1,61.2,sensor\n')
        stream.write('2026-11-14T23:01:35,25.2,61.3,sensor\n')
        stream.write('2026-11-21T05:00:00,26,62,sensor\n')
    frame=csv_training_frame(path)
    assert frame.source.tolist()==['weather_sample','sensor','sensor']
    eligible=eligible_training_frame(frame,True,datetime(2026,11,20))
    assert eligible.temp.tolist()==[20,25.2]
    real_only=eligible_training_frame(frame,False,datetime(2026,11,20))
    assert real_only.temp.tolist()==[25.2]


@pytest.mark.parametrize('change',['hash','missing','outside'])
def test_bad_training_policy_is_rejected(tmp_path,change):
    path=tmp_path/'sensor_data.csv'
    write_base(path)
    policy_path=tmp_path/'dataset.provenance.json'
    policy=json.loads(policy_path.read_text())
    if change=='hash':
        policy['files_sha256'][path.name]='wrong'
    elif change=='missing':
        policy.pop('training_time_ranges')
    else:
        policy['training_time_ranges'][0]['end']='2026-12-01T00:00:00'
    policy_path.write_text(json.dumps(policy),encoding='utf-8')
    with pytest.raises(ValueError):
        weather_training_ranges(path)


def test_current_csv_includes_all_87841_rows_after_the_endpoint():
    meta=json.loads(config.DATA_FILE.with_suffix('.provenance.json').read_text(encoding='utf-8'))
    # New real rows may be appended later; validate the immutable generated base.
    frame=csv_training_frame(config.DATA_FILE).iloc[:meta['row_count']]
    eligible=eligible_training_frame(frame,True,datetime(2026,11,16,6))
    assert len(frame)==87841 and len(eligible)==87841
    assert eligible.time.max()==pd.Timestamp('2026-11-16T05:00:00')
    assignment=meta.get('sensor_label_assignment')
    sensor_rows=assignment['rows'] if assignment else 0
    assert eligible.source.eq('sensor').sum()==sensor_rows
    assert eligible.source.eq('weather_sample').sum()==len(frame)-sensor_rows
    assert np.isfinite(eligible[['temp','hum']]).all().all()


def test_default_training_uses_csv_without_creating_a_database(tmp_path,monkeypatch):
    import train_forecast
    path=tmp_path/'sensor_data.csv'
    write_base(path)
    def forbidden(*args,**kwargs):
        raise AssertionError('Default CSV training must not create/open a training DB')
    monkeypatch.setattr(train_forecast,'Store',forbidden)
    with pytest.raises(SystemExit,match='hiện có 0.0 ngày'):
        train_forecast.train(epochs=1,csv_path=path)
    assert not list(tmp_path.glob('*.sqlite3*'))


def test_train_reads_the_full_weather_csv_without_historical_range_exclusions(tmp_path,monkeypatch):
    import train_forecast
    path=tmp_path/'sensor_data.csv'
    write_base(path)
    monkeypatch.setattr(train_forecast,'now_local',lambda:datetime(2026,11,14,22))
    def capture(eligible):
        assert eligible.temp.tolist()==[20,21]
        assert eligible.source.eq('weather_sample').all()
        raise RuntimeError('full simulation selected')
    monkeypatch.setattr(train_forecast,'regular_series',capture)
    with pytest.raises(RuntimeError,match='full simulation selected'):
        train_forecast.train(epochs=1,csv_path=path,include_weather_samples=True)


def test_explicit_offline_cutoff_selects_assigned_sensor_horizon_without_changing_clock(tmp_path,monkeypatch):
    import train_forecast
    path=tmp_path/'sensor_data.csv'
    write_base(path)
    meta=json.loads(path.with_suffix('.provenance.json').read_text(encoding='utf-8'))
    meta.update(source_assignment='csv_label',sensor_label_assignment={
        'start_exclusive':'2026-11-14T23:00:00','end':'2026-11-14T23:01:00','label':'sensor','rows':1})
    path.with_suffix('.provenance.json').write_text(json.dumps(meta),encoding='utf-8')
    actual_now=datetime(2026,11,14,23)
    monkeypatch.setattr(train_forecast,'now_local',lambda:actual_now)
    selected=[]
    def capture(eligible):
        selected.append(eligible.source.tolist())
        raise RuntimeError('selected input')
    monkeypatch.setattr(train_forecast,'regular_series',capture)
    with pytest.raises(RuntimeError,match='selected input'):
        train_forecast.train(epochs=1,csv_path=path,include_weather_samples=True)
    with pytest.raises(RuntimeError,match='selected input'):
        train_forecast.train(epochs=1,csv_path=path,include_weather_samples=True,
                             data_cutoff='2026-11-14T23:01:00')
    assert selected==[['weather_sample'],['weather_sample','sensor']]
    assert train_forecast.now_local()==actual_now


@pytest.mark.skipif(importlib.util.find_spec('tensorflow') is None,reason='Install requirements-ml.txt for Keras QA')
def test_fit_from_shared_csv_uses_no_store_and_saves_a_valid_model(tmp_path,monkeypatch):
    import train_forecast
    path=tmp_path/'sensor_data.csv'
    prepare_sensor_csv(path)
    stamps=pd.date_range(end='2026-11-10T12:00:00',periods=16*144,freq='10min')
    phase=np.arange(len(stamps))*2*np.pi/144
    frame=pd.DataFrame({'time':stamps,'temp':25+np.sin(phase),'hum':60+5*np.cos(phase),'label':'sensor'})
    frame.to_csv(path,mode='a',header=False,index=False,lineterminator='\n')
    def forbidden(*args,**kwargs):
        raise AssertionError('CSV training opened a database')
    monkeypatch.setattr(train_forecast,'Store',forbidden)
    output=tmp_path/'model'
    train_forecast.train(epochs=1,csv_path=path,output_dir=output)
    meta=json.loads((output/'forecast_12h.json').read_text(encoding='utf-8'))
    assert meta['training_input']['eligible_rows']==len(frame)
    assert meta['source_counts']=={'sensor':len(frame)}
    assert meta['context']==144 and meta['steps']==72
    assert hashlib.sha256((output/'forecast_12h.keras').read_bytes()).hexdigest()==meta['model_sha256']
    assert not list(tmp_path.rglob('*.sqlite3*'))
