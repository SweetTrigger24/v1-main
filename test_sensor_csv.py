"""Portable mixed CSV: monitor appends, restart/import and simulated labels."""
import hashlib
import json
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

import common as config
from monitor import Monitor, validate_demo_database
from provenance import dataset_provenance, prepare_sensor_csv
from storage import Store
from train_forecast import csv_training_frame, eligible_training_frame

NOW = datetime(2026, 11, 16, 5, 1, 35)


@pytest.fixture
def sensor_csv(tmp_path, monkeypatch):
    path = tmp_path / 'sensor_data.csv'
    path.write_bytes(b'time,temp,hum,label\n2026-11-16T05:00:00,18,80,sensor\n2026-11-16T05:01:00,19,79,sensor\n')
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    path.with_suffix('.provenance.json').write_text(json.dumps({
        'kind':'weather_derived_synthetic', 'dataset_id':digest,
        'csv_prefix_bytes':len(content), 'csv_prefix_sha256':digest, 'row_count':2,
        'start':'2026-11-16T05:00:00', 'end':'2026-11-16T05:01:00',
        'location':'QA', 'source_provider':'QA', 'source_url':'https://example.test'}), encoding='utf-8')
    monkeypatch.setattr(config, 'DATA_FILE', path)
    monkeypatch.setattr(config, 'LOG_FILE', tmp_path/'monitor_log.csv')
    monkeypatch.setattr(config, 'MQTT_ENABLED', False)
    monkeypatch.setattr(config, 'EMAIL_ENABLED', False)
    return path


def collect(store, now=NOW, demo=False):
    sensor = SimpleNamespace(read=lambda stop:(25.1,61.2), set_outputs=lambda state:None)
    monitor = Monitor(store, sensor, demo=demo)
    monitor.step(threading.Event(), now)


@pytest.mark.parametrize('now',[NOW.replace(second=0),NOW])
def test_monitor_appends_same_csv_and_fresh_import_preserves_real_priority(sensor_csv, tmp_path, now):
    original = sensor_csv.read_bytes()
    live = Store(tmp_path/'live.sqlite3')
    collect(live,now)
    assert sensor_csv.read_bytes().startswith(original)
    frame = pd.read_csv(sensor_csv)
    assert frame.columns.tolist()==['time','temp','hum','label']
    assert len(frame)==3 and frame.label.eq('sensor').all()
    assert frame.iloc[-1].time==now.isoformat() and frame.iloc[-1].temp==25.1
    assert not (tmp_path/'live_sensor_data.csv').exists()
    assert dataset_provenance(sensor_csv)['sensor_row_start']==2
    restored = Store(tmp_path/'restored.sqlite3')
    restored.import_csv(sensor_csv)
    result = restored.frame()
    # The 05:01 simulated row is superseded by the real 05:01:35 reading.
    assert result.source.tolist()==['weather_sample','sensor']
    assert result.iloc[-1].temp==25.1 and result.iloc[-1].hum==61.2
    assert restored.latest_live()['time']==now.isoformat()
    assert restored.import_csv(sensor_csv)==0


def test_restart_appends_with_one_header_and_keeps_boundary(sensor_csv, tmp_path):
    collect(Store(tmp_path/'live.sqlite3'))
    boundary = dataset_provenance(sensor_csv)['sensor_row_start']
    collect(Store(tmp_path/'live.sqlite3'),NOW+timedelta(minutes=1))
    assert sensor_csv.read_text().count('time,temp,hum,label')==1
    assert dataset_provenance(sensor_csv)['sensor_row_start']==boundary
    restored = Store(tmp_path/'restored.sqlite3')
    restored.import_csv(sensor_csv)
    assert restored.frame().source.eq('sensor').sum()==2


def test_scenario_label_alone_never_becomes_sensor(sensor_csv, tmp_path):
    store = Store(tmp_path/'before.sqlite3')
    store.import_csv(sensor_csv)
    assert store.frame().source.eq('weather_sample').all()
    unverified = tmp_path/'arbitrary.csv'
    unverified.write_text('time,temp,hum,label\n2026-11-16T05:05:00,25,60,sensor\n',encoding='utf-8')
    other = Store(tmp_path/'unverified.sqlite3')
    other.import_csv(unverified)
    assert other.frame().source.tolist()==['import']


def test_explicit_assignment_matches_csv_and_sqlite_and_keeps_sensor_time_cutoff(sensor_csv,tmp_path):
    meta=json.loads(sensor_csv.with_suffix('.provenance.json').read_text(encoding='utf-8'))
    meta.update(kind='user_labeled_mixed_dataset',source_assignment='csv_label',
                sensor_label_assignment={'start_exclusive':'2026-11-16T05:00:00',
                                         'end':'2026-11-16T05:01:00','label':'sensor','rows':1})
    sensor_csv.with_suffix('.provenance.json').write_text(json.dumps(meta),encoding='utf-8')
    store=Store(tmp_path/'assigned.sqlite3')
    store.import_csv(sensor_csv)
    assert store.frame().source.tolist()==['weather_sample','sensor']
    frame=csv_training_frame(sensor_csv)
    assert frame.source.tolist()==['weather_sample','sensor']
    cutoff=datetime(2026,11,16,5)
    assert eligible_training_frame(frame,False,cutoff).empty
    assert eligible_training_frame(frame,True,cutoff).source.tolist()==['weather_sample']
    assert eligible_training_frame(frame,False,NOW).temp.tolist()==[19]
    # Explicitly assigned sensor rows are skipped for a demo database too.
    demo=Store(tmp_path/'demo_assigned.sqlite3')
    demo.import_csv(sensor_csv,include_sensor=False)
    assert demo.frame().source.tolist()==['weather_sample']
    # New monitor appends keep the configured historical labels on restore.
    collect(store,NOW+timedelta(minutes=1))
    restored=Store(tmp_path/'assigned_restored.sqlite3')
    restored.import_csv(sensor_csv)
    assert restored.frame().source.tolist()==['weather_sample','sensor','sensor']


def test_sensor_assignment_outside_verified_prefix_period_is_rejected(sensor_csv,tmp_path):
    meta=json.loads(sensor_csv.with_suffix('.provenance.json').read_text(encoding='utf-8'))
    meta.update(source_assignment='csv_label',sensor_label_assignment={
        'start_exclusive':'2026-11-06T05:00:00','end':'2026-11-16T05:01:00','label':'sensor','rows':1})
    sensor_csv.with_suffix('.provenance.json').write_text(json.dumps(meta),encoding='utf-8')
    with pytest.raises(ValueError,match='Khoảng gán nhãn sensor'):
        Store(tmp_path/'bad_assignment.sqlite3').import_csv(sensor_csv)


def test_changing_label_assignment_invalidates_import_marker_without_rewriting_csv(sensor_csv,tmp_path):
    store=Store(tmp_path/'changed_assignment.sqlite3')
    store.import_csv(sensor_csv)
    before=sensor_csv.read_bytes()
    meta=json.loads(sensor_csv.with_suffix('.provenance.json').read_text(encoding='utf-8'))
    meta.update(source_assignment='csv_label',sensor_label_assignment={
        'start_exclusive':'2026-11-16T05:00:00','end':'2026-11-16T05:01:00','label':'sensor','rows':1})
    sensor_csv.with_suffix('.provenance.json').write_text(json.dumps(meta),encoding='utf-8')
    assert store.import_csv(sensor_csv)==2
    assert store.frame().source.tolist()==['weather_sample','sensor']
    assert store.import_csv(sensor_csv)==0
    assert sensor_csv.read_bytes()==before


def test_demo_does_not_append_and_can_import_only_historical_prefix(sensor_csv, tmp_path):
    collect(Store(tmp_path/'live.sqlite3'))
    before = sensor_csv.read_bytes()
    demo = Store(tmp_path/'demo.sqlite3')
    demo.import_csv(sensor_csv,include_sensor=False)
    collect(demo,demo=True)
    assert sensor_csv.read_bytes()==before
    assert not demo.frame().source.eq('sensor').any()
    validate_demo_database(demo.path)


def test_new_csv_without_simulation_restores_sensor_rows(tmp_path, monkeypatch):
    path = tmp_path/'new.csv'
    monkeypatch.setattr(config,'DATA_FILE',path)
    monkeypatch.setattr(config,'LOG_FILE',tmp_path/'log.csv')
    monkeypatch.setattr(config,'MQTT_ENABLED',False)
    monkeypatch.setattr(config,'EMAIL_ENABLED',False)
    collect(Store(tmp_path/'live.sqlite3'))
    restored = Store(tmp_path/'restored.sqlite3')
    restored.import_csv(path)
    assert restored.frame().source.tolist()==['sensor']
    assert restored.frame().temp.tolist()==[25.1]


def test_changed_prefix_is_rejected_and_existing_bytes_are_not_rewritten(sensor_csv, tmp_path):
    prepare_sensor_csv(sensor_csv)
    content = sensor_csv.read_bytes().replace(b',18,80,',b',17,80,')
    sensor_csv.write_bytes(content)
    with pytest.raises(ValueError,match='CSV đã thay đổi'):
        Store(tmp_path/'restored.sqlite3').import_csv(sensor_csv)
    collect(Store(tmp_path/'live.sqlite3'))
    assert sensor_csv.read_bytes()==content


def test_wrong_csv_header_preserves_file_and_sqlite_reading(tmp_path, monkeypatch):
    path = tmp_path/'old.csv'
    content = b'time,temp,hum\n2026-11-16T05:00:00,18,80\n'
    path.write_bytes(content)
    monkeypatch.setattr(config,'DATA_FILE',path)
    monkeypatch.setattr(config,'MQTT_ENABLED',False)
    monkeypatch.setattr(config,'EMAIL_ENABLED',False)
    store = Store(tmp_path/'live.sqlite3')
    collect(store)
    assert path.read_bytes()==content
    assert store.latest_live()['temp']==25.1


def test_partial_tail_after_interruption_is_not_concatenated(sensor_csv, tmp_path):
    store=Store(tmp_path/'live.sqlite3')
    collect(store)
    content=sensor_csv.read_bytes()[:-3]
    sensor_csv.write_bytes(content)
    collect(store,NOW+timedelta(minutes=1))
    assert sensor_csv.read_bytes()==content
    assert store.latest_live()['time']==(NOW+timedelta(minutes=1)).isoformat()
