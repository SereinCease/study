import json,sqlite3
from concurrent.futures import ThreadPoolExecutor
from server import db


def test_upgrade_early_schema_indexes_evidence_after_column_added(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'old');db.DATA.mkdir()
    with sqlite3.connect(db.DATA/'learnloop.sqlite3') as c:
        c.executescript('CREATE TABLE entities(id TEXT PRIMARY KEY,kind TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL);CREATE TABLE schema_version(version INTEGER PRIMARY KEY,applied TEXT NOT NULL);INSERT INTO schema_version VALUES(1,"2025-01-01");')
        e={'id':'early-event','kind':'event','answer':'保留历史作答','created':'2025-01-01','domain_id':'automation-testing'}
        c.execute('INSERT INTO entities VALUES(?,?,?,?)',(e['id'],'event',json.dumps(e),'2025-01-01'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:db.init(),range(2)))
    assert db.get('early-event')['answer']=='保留历史作答'
    with db.connect() as c:
        assert c.execute('SELECT space_id FROM evidence WHERE id="early-event"').fetchone()[0]=='automation'
        assert len(c.execute('SELECT version FROM schema_version').fetchall())==5
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
