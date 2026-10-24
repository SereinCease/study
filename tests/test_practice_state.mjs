import test from 'node:test';
import assert from 'node:assert/strict';
import { practiceState, practiceAssociation, restorePracticeDraft, practiceDraftPayload, localPracticeDraft, savedPracticeState, acknowledgePracticeLocal } from '../src/practice-state.js';

const scope = { space_id: 'automation', goal_id: 'checkout', exercise_id: 'assertions' };
const selection = { exerciseId: 'assertions', spaceId: 'automation', goalId: 'checkout' };

test('a corrected run replaces the old failure for newest-first and oldest-first API results', () => {
  const failed = { ...scope, id: 'failed', created: '2026-10-23T10:00:00Z', requirements_met: false };
  const corrected = { ...scope, id: 'corrected', created: '2026-10-23T10:01:00Z', requirements_met: true };
  for (const runs of [[corrected, failed], [failed, corrected]]) {
    assert.equal(practiceState({ ...selection, runs }).latestRun.id, 'corrected');
  }
});

test('other goals and spaces cannot replace this report or block a run', () => {
  const jobs = [
    { id: 'other-goal', task: 'run', status: 'running', payload: { ...scope, goal_id: 'other' } },
    { id: 'other-space', task: 'run', status: 'queued', payload: { ...scope, space_id: 'other' } },
    { id: 'own-finished', task: 'run', status: 'completed', payload: scope },
  ];
  const runs = [{ ...scope, goal_id: 'other', id: 'other-report', created: '2026-10-23T12:00:00Z' }];
  const state = practiceState({ ...selection, jobs, runs });
  assert.equal(state.latestRun, undefined);
  assert.equal(state.pending, false);
  assert.deepEqual(state.runJobs.map(x => x.id), ['own-finished']);
});

test('free practice restores its own draft instead of a goal draft from the same exercise', () => {
  const drafts = [{ ...scope, id: 'goal-draft', code: '# goal code' }, { ...scope, goal_id: null, id: 'free-draft', code: '# free code' }];
  assert.equal(practiceState({ ...selection, goalId: null, drafts }).draft.id, 'free-draft');
  assert.equal(practiceState({ ...selection, drafts }).draft.id, 'goal-draft');
});

test('only the selected exercise in the same goal contributes pending state', () => {
  const own = { task: 'run', status: 'running', payload: scope };
  assert.equal(practiceState({ ...selection, jobs: [own] }).pending, true);
  assert.equal(practiceState({ ...selection, exerciseId: 'fixtures', jobs: [own] }).pending, false);
});


const editor = { draftKey: 'automation-checkout-checkout-project', selected: 'checkout-project',
  spaceId: 'automation', goalId: 'checkout', sessionId: 'assert-session', conceptId: 'assert-concept',
  concept: 'page-object', code: '# Checkout Page Object', revision: 4, dirty: true };

test('the selected Page Object concept is saved instead of the teaching prop, with no mismatched session', () => {
  assert.deepEqual(practiceDraftPayload(editor), {
    code: '# Checkout Page Object', revision: 4, space_id: 'automation', goal_id: 'checkout',
    concept_id: 'page-object', session_id: null,
  });
  assert.deepEqual(practiceAssociation('', 'assert-session', 'assert-concept'), { concept_id: null, session_id: 'assert-session' });
  assert.deepEqual(practiceAssociation('assert-concept', 'assert-session', 'assert-concept'), { concept_id: 'assert-concept', session_id: 'assert-session' });
});

test('switching exercises or restarting restores each saved concept and explicit only-run choice', () => {
  const drafts = [
    { ...scope, revision: 3, code: '# assert', concept_id: null },
    { ...scope, exercise_id: 'checkout-project', revision: 4, code: editor.code, concept_id: 'page-object' },
    { ...scope, goal_id: 'other', exercise_id: 'checkout-project', revision: 20, code: '# other', concept_id: 'other' },
  ];
  const checkout = practiceState({ ...selection, drafts, exerciseId: 'checkout-project' }).draft;
  assert.equal(restorePracticeDraft({ draft: checkout }).concept, 'page-object');
  const assertions = practiceState({ ...selection, drafts }).draft;
  assert.equal(restorePracticeDraft({ draft: assertions }).concept, '');
  assert.deepEqual(restorePracticeDraft({ starter: '# first' }), { code: '# first', concept: '', revision: 0, dirty: false, conflict: null });
});

test('local recovery stores selection and does not borrow an association for an old-format local draft', () => {
  const local = JSON.parse(JSON.stringify(localPracticeDraft(editor)));
  assert.equal(local.concept_id, 'page-object');
  assert.equal(local.session_id, null);
  const draft = { code: '# old code', concept_id: 'assert-concept', revision: 4 };
  assert.equal(restorePracticeDraft({ draft, local }).concept, 'page-object');
  assert.equal(restorePracticeDraft({ draft, local: { code: '# legacy local', revision: 4 } }).concept, '');
  assert.equal(restorePracticeDraft({ draft, local: { code: '# only run', concept_id: null, revision: 4 } }).concept, '');
});

test('an in-flight save cannot acknowledge a later concept change even when code is identical', () => {
  const changed = { ...editor, concept: 'assert-concept' };
  const state = savedPracticeState(changed, editor, 5);
  assert.equal(state.dirty, true);
  assert.equal(state.revision, 5);
  assert.equal(state.concept, 'assert-concept');
  assert.equal(localPracticeDraft(state).session_id, 'assert-session');
  assert.equal(savedPracticeState(editor, editor, 5).dirty, false);
});

test('an old save cannot change a newly selected exercise, goal, or code snapshot', () => {
  assert.equal(savedPracticeState({ ...editor, draftKey: 'other-exercise' }, editor, 5), null);
  assert.equal(savedPracticeState({ ...editor, draftKey: 'other-goal' }, editor, 5), null);
  const newer = savedPracticeState({ ...editor, code: '# newer code' }, editor, 5);
  assert.equal(newer.dirty, true);
  assert.equal(newer.code, '# newer code');
  assert.equal(localPracticeDraft(newer).revision, 5);
  const frozen = practiceDraftPayload(editor);
  assert.equal(frozen.code, '# Checkout Page Object');
  assert.equal(frozen.concept_id, 'page-object');
});


test('saving after navigation reconciles that exercise local copy without discarding a newer selection', () => {
  assert.equal(acknowledgePracticeLocal(localPracticeDraft(editor), editor, 5), null);
  const changed = { ...localPracticeDraft(editor), concept_id: null, session_id: 'assert-session' };
  const retained = acknowledgePracticeLocal(changed, editor, 5);
  assert.equal(retained.concept_id, null);
  assert.equal(retained.revision, 5);
  assert.equal(acknowledgePracticeLocal({ ...changed, revision: 8 }, editor, 5), undefined);
});
