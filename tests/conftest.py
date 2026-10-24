import pytest
from server import db,ingest
from fastapi.testclient import TestClient
from server.app import app

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    db.init()
    def no_vector(text): raise RuntimeError('offline test')
    monkeypatch.setattr(ingest,'vector',no_vector)

@pytest.fixture
def client():
    with TestClient(app) as c:
        token=c.get('/api/session').json()['token']
        c.headers['x-learnloop-token']=token
        yield c
