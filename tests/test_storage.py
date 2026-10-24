from server import db

def test_legacy_migration_preserves_and_marks_incomplete():
    with db.connect() as c:
        c.execute('DELETE FROM schema_version')
        c.execute('CREATE TABLE sources(id TEXT PRIMARY KEY,title TEXT)')
        c.execute("INSERT INTO sources VALUES('old-source','旧教程')")
        c.execute('CREATE TABLE concepts(id TEXT PRIMARY KEY,label TEXT)')
        c.execute("INSERT INTO concepts VALUES('old-concept','断言')")
        c.execute('CREATE TABLE attempts(id TEXT PRIMARY KEY,result TEXT)')
        c.execute("INSERT INTO attempts VALUES('old-attempt','passed')")
    db.init();db.init()
    assert len(db.all('source'))==1 and db.all('source')[0]['status']=='legacy'
    assert db.all('concept')[0]['title']=='断言'
    assert db.all('legacy_attempt')[0]['evidence_incomplete']
    assert db.all('legacy_attempt')[0]['score'] is None
    assert not db.all('mastery')
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==1
        assert c.execute('PRAGMA foreign_keys').fetchone()[0]==1
