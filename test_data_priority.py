from datetime import datetime,timedelta
import json
import sqlite3

import pandas as pd

from storage import Store
from forecasting import regular_series
from train_forecast import eligible_training_frame
from generate_weather_data import synthesize,write_dataset

NOW=datetime(2026,11,10,12)


def test_sensor_replaces_simulated_minute_but_keeps_distinct_sensor_readings(tmp_path):
    store=Store(tmp_path/'db.sqlite3')
    store.add_reading(NOW,10,90,source='weather_sample')
    store.add_reading(NOW+timedelta(seconds=30),25,60)
    store.add_reading(NOW+timedelta(seconds=45),26,61)
    frame=store.frame()
    assert len(frame)==2 and frame.source.eq('sensor').all()
    assert frame.temp.tolist()==[25,26]
    store.add_reading(NOW+timedelta(seconds=45),27,62)
    assert len(store.frame())==2  # Cùng timestamp không nhân đôi.


def test_import_cannot_reintroduce_weather_in_sensor_minute(tmp_path):
    store=Store(tmp_path/'db.sqlite3')
    output=tmp_path/'weather.csv'
    raw={'request_url':'https://example.test','response':{'latitude':21.,'longitude':105.8,'hourly':{
        'time':[(NOW+timedelta(hours=i)).isoformat() for i in range(3)],
        'temperature_2m':[10,11,12],'relative_humidity_2m':[90,85,80]}}}
    source=tmp_path/'raw.json'
    source.write_text(json.dumps(raw),encoding='utf-8')
    frame,hourly=synthesize(raw,NOW,NOW+timedelta(hours=2))
    write_dataset(frame,hourly,raw,output,source,42,21.,105.8)
    store.add_reading(NOW+timedelta(seconds=35),25,60)
    store.import_csv(output)
    minute=store.frame(NOW,NOW+timedelta(seconds=59))
    assert len(minute)==1 and minute.source.iloc[0]=='sensor'


def test_old_overlaps_removed_on_open_without_deleting_real_values(tmp_path):
    path=tmp_path/'db.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE readings(time TEXT PRIMARY KEY,temp REAL,hum REAL,source TEXT,state TEXT,reasons TEXT)")
        db.executemany("INSERT INTO readings VALUES(?,?,? ,?,'XANH','[]')",[
            (NOW.isoformat(),10,90,'weather_sample'),
            ((NOW+timedelta(seconds=35)).isoformat(),25,60,'sensor')])
    frame=Store(path).frame()
    assert len(frame)==1 and frame.temp.iloc[0]==25


def test_legacy_migration_preserves_values_metadata_and_views(tmp_path):
    path=tmp_path/'legacy.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE readings(time TEXT PRIMARY KEY,temp REAL,hum REAL,source TEXT,state TEXT,reasons TEXT);
            CREATE INDEX readings_source_time ON readings(source,time);
            CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE VIEW annotated_readings AS SELECT r.* FROM readings r;
        """)
        db.execute("INSERT INTO readings VALUES(?,?,?,?,?,?)",(NOW.isoformat(),25.123,61.456,'weather_sample','VANG','["test"]'))
        db.execute("INSERT INTO kv VALUES('runtime_settings','{\"enabled\":true}')")
    store=Store(path)
    expected=store.frame()
    assert expected.source.tolist()==['weather_sample']
    assert expected.temp.tolist()==[25.123] and expected.hum.tolist()==[61.456]
    assert expected.state.tolist()==['VANG'] and expected.reasons.tolist()==['["test"]']
    assert store.get('runtime_settings')=={'enabled':True}
    with store.connection() as db:
        assert [r[1] for r in db.execute('PRAGMA table_info(readings)')]==['time','temp','hum','state','reasons']
        assert len(db.execute('SELECT * FROM annotated_readings').fetchone())==5
        assert db.execute('SELECT origin FROM reading_origins').fetchone()[0]=='weather_sample'
    pd.testing.assert_frame_equal(Store(path).frame(),expected)
    store.add_reading(NOW+timedelta(seconds=35),26,62)
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM reading_origins').fetchone()[0]==1
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert store.latest_live()['source']=='sensor'


def test_resampling_never_averages_sensor_with_simulation_same_minute():
    frame=pd.DataFrame({'time':[NOW,NOW+timedelta(seconds=35),NOW+timedelta(minutes=10)],
                        'temp':[10,25,26],'hum':[90,60,61],
                        'source':['weather_sample','sensor','sensor']})
    result=regular_series(frame)
    assert result.temp.dropna().iloc[0]==25.5
    assert result.hum.dropna().iloc[0]==60.5


def test_training_excludes_future_and_preserves_source_labels():
    frame=pd.DataFrame({'time':[NOW-timedelta(days=1),NOW,NOW+timedelta(minutes=1),NOW],
                        'temp':[20,25,26,30],'hum':[60,61,62,70],
                        'source':['weather_sample','sensor','sensor','forecast_sample']})
    actual=eligible_training_frame(frame,False,NOW)
    assert actual.source.tolist()==['sensor'] and actual.temp.iloc[0]==25
    simulated=eligible_training_frame(frame,True,NOW)
    assert simulated.source.tolist()==['weather_sample','sensor']
