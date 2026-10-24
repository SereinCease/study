"""Withdrawing a bad exercise assessment must also withdraw its correction claim."""
from datetime import datetime,timedelta,timezone
import pytest
from server import db,learning,memory


def setup_attempts():
    concept=db.put('concept',{'space_id':'automation','title':'断言','citation_ids':[],'source_ids':[]})
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    def attempt(day=0,**overrides):
        return db.put('attempt',{'space_id':'automation','concept_id':concept['id'],'answer':'原始回答保留','question':'明确的题目','score':1,'hint_level':0,'answer_seen':False,'is_transfer':True,'error_type':'unknown','created':(start+timedelta(days=day)).isoformat(),**overrides})
    def mistake(ids,**overrides):
        return db.put('mistake',{'space_id':'automation','concept_id':concept['id'],'answer':'原始错答','status':'pending_practice','practice_attempts':ids,**overrides})
    return concept,attempt,mistake


@pytest.mark.parametrize('delete',[False,True])
def test_withdraw_sole_practice_resets_state_without_zero_score_penalty(delete):
    concept,attempt,mistake=setup_attempts()
    valid=attempt(0,is_transfer=False)
    invalid=attempt(1)
    record=mistake([invalid['id']])
    learning.recompute(concept['id'])
    assert db.get(record['id'])['status']=='corrected_pending_review'
    memory.visibility(invalid['id'],False,delete=delete)
    revised=db.get(record['id']);mastery=db.get('mastery-'+concept['id'])
    assert revised['status']=='pending_practice' and revised['review_attempt'] is None
    assert revised['answer']==record['answer'] and revised['practice_attempts']==[invalid['id']]
    assert mastery['score']==.3 and mastery['evidence_count']==1 and mastery['last_attempt']==valid['id']
    assert db.get(invalid['id'])['score']==1 and db.get(invalid['id'])['answer']==invalid['answer']
    transition=revised['status_history'][-1]
    assert transition['from']=='corrected_pending_review' and transition['to']=='pending_practice'
    assert transition['reason']=='practice_evidence_unavailable' and transition['active_practice_attempt_ids']==[]
    # Repeated recomputation must not duplicate the same state transition.
    learning.recompute(concept['id'])
    assert db.get(record['id'])['status_history']==revised['status_history']


def test_other_valid_practice_survives_and_restoring_evidence_is_traceable():
    concept,attempt,mistake=setup_attempts()
    first=attempt(0);second=attempt(1)
    record=mistake([first['id'],second['id']])
    learning.recompute(concept['id'])
    memory.visibility(second['id'],False)
    assert db.get(record['id'])['status']=='corrected_pending_review'
    memory.visibility(first['id'],False)
    assert db.get(record['id'])['status']=='pending_practice'
    memory.visibility(first['id'],True)
    restored=db.get(record['id'])
    assert restored['status']=='corrected_pending_review'
    assert restored['status_history'][-1]['from']=='pending_practice'
    assert restored['status_history'][-1]['active_practice_attempt_ids']==[first['id']]


def test_linked_independent_review_survives_until_it_is_also_withdrawn():
    concept,attempt,mistake=setup_attempts()
    fix=attempt(0);review=attempt(2,is_transfer=False)
    record=mistake([fix['id']])
    learning.recompute(concept['id'])
    assert db.get(record['id'])['status']=='review_passed'
    memory.visibility(fix['id'],False)
    assert db.get(record['id'])['status']=='review_passed'
    assert db.get(record['id'])['review_attempt']==review['id']
    memory.visibility(review['id'],False)
    revised=db.get(record['id'])
    assert revised['status']=='pending_practice' and revised['review_attempt'] is None
    assert revised['status_history'][-1]['previous_review_attempt']==review['id']


def test_manual_record_and_another_space_are_not_rewritten():
    concept,attempt,mistake=setup_attempts()
    fix=attempt()
    manual=mistake([],status='pending_analysis')
    unrelated=mistake([fix['id']],space_id='inbox',status='corrected_pending_review')
    learning.recompute(concept['id'])
    assert db.get(manual['id'])==manual and db.get(unrelated['id'])==unrelated


@pytest.mark.parametrize('changes',[{'error_type':'environment'},{'answer_seen':True},{'score':None}])
def test_ineligible_practice_does_not_preserve_correction_claim(changes):
    concept,attempt,mistake=setup_attempts()
    fix=attempt(**changes)
    record=mistake([fix['id']],status='corrected_pending_review')
    learning.recompute(concept['id'])
    assert db.get(record['id'])['status']=='pending_practice'
