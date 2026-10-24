from server import db


def test_draft_saves_require_matching_revision_and_never_overwrite_accepted(client):
    draft=db.put('artifact',{'space_id':'automation','status':'draft','output':{'cards':[],'gaps':['original']}})
    endpoint='/api/artifacts/'+draft['id']
    saved=client.patch(endpoint,json={'revision':draft['revision'],'output':{'cards':[],'gaps':['first edit']}})
    assert saved.status_code==200
    assert client.patch(endpoint,json={'revision':draft['revision'],'output':{'cards':[],'gaps':['stale edit']}}).status_code==409
    assert client.patch(endpoint,json={'output':{'cards':[]}}).status_code==409
    assert db.get(draft['id'])['output']['gaps']==['first edit']
    accepted=db.update(draft['id'],{'status':'accepted'})
    assert client.patch(endpoint,json={'revision':accepted['revision'],'output':{'cards':[],'gaps':['cannot overwrite']}}).status_code==409
    assert db.get(draft['id'])['status']=='accepted'
    assert db.get(draft['id'])['output']['gaps']==['first edit']
