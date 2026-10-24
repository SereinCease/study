// API lists are newest-first; task and draft selection must also keep goal scope.
export function inPracticeScope(record, spaceId, goalId) {
  return record?.space_id === spaceId && (record.goal_id || null) === (goalId || null);
}

export function practiceState({ runs = [], jobs = [], drafts = [], exerciseId, spaceId, goalId }) {
  const matches = row => row.exercise_id === exerciseId && inPracticeScope(row, spaceId, goalId);
  const latestRun = runs.filter(matches).sort((a, b) =>
    String(b.created || '').localeCompare(String(a.created || '')) || String(b.id).localeCompare(String(a.id))
  )[0];
  const runJobs = jobs.filter(job => job.task === 'run' && matches(job.payload || {}));
  return {
    latestRun,
    runJobs,
    pending: runJobs.some(job => ['queued', 'running'].includes(job.status)),
    draft: drafts.find(matches),
  };
}

// The editor owns this choice; the teaching session's concept is not a default.
export function practiceAssociation(concept, sessionId, sessionConceptId) {
  const concept_id = concept || null;
  return {
    concept_id,
    session_id: !concept_id || concept_id === (sessionConceptId || null) ? sessionId || null : null,
  };
}

export function restorePracticeDraft({ draft, local, starter = '' }) {
  const chosen = local || draft;
  return {
    code: chosen?.code ?? starter,
    concept: chosen?.concept_id || '',
    revision: draft?.revision || 0,
    dirty: !!local,
    conflict: local && local.revision !== (draft?.revision || 0) ? draft || null : null,
  };
}

export function practiceDraftPayload(snapshot) {
  return {
    code: snapshot.code,
    revision: snapshot.revision,
    space_id: snapshot.spaceId,
    goal_id: snapshot.goalId || null,
    ...practiceAssociation(snapshot.concept, snapshot.sessionId, snapshot.conceptId),
  };
}

export function localPracticeDraft(snapshot) {
  const payload = practiceDraftPayload(snapshot);
  return { code: payload.code, revision: payload.revision, concept_id: payload.concept_id, session_id: payload.session_id };
}

export function samePracticeDraft(a, b) {
  if (!a || !b || a.draftKey !== b.draftKey) return false;
  const left = practiceDraftPayload(a), right = practiceDraftPayload(b);
  return ['code', 'space_id', 'goal_id', 'concept_id', 'session_id'].every(field => left[field] === right[field]);
}

export function savedPracticeState(current, snapshot, savedRevision) {
  if (!current || current.draftKey !== snapshot.draftKey) return null;
  // An in-flight request acknowledges only its original code + association.
  return { ...current, revision: savedRevision, dirty: !samePracticeDraft(current, snapshot) };
}

export function acknowledgePracticeLocal(local, snapshot, savedRevision) {
  if (!local || local.revision !== snapshot.revision) return undefined;
  const written = localPracticeDraft(snapshot);
  const same = ['code', 'concept_id', 'session_id'].every(field => (local[field] ?? null) === (written[field] ?? null));
  return same ? null : { ...local, revision: savedRevision };
}
