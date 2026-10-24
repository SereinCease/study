"""Read-only structural audit of live30 acceptance data; no cloud model calls.

Run after the live driver/worker has stopped. Uses actual memory.search for FTS
and locally cached semantic retrieval; never rebuilds indexes or changes grades.
A passing structural audit cannot establish semantic correctness of AI prose.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.live30_acceptance import guarded_data_dir, sanitized, write_json


def stamp():
    return datetime.now(timezone.utc).isoformat()


def normalized(value):
    import unicodedata
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).split())


def active_attempt(a):
    return not (a.get('deleted') or a.get('assessment_voided') or a.get('memory_enabled') is False)


def observed(a):
    return a.get('corrected_score', a.get('score'))


def independent(a):
    score = observed(a)
    return (active_attempt(a) and score is not None and score >= .7 and not a.get('hint_level')
            and not a.get('answer_seen') and a.get('error_type') != 'environment')


def load_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', type=Path, default=ROOT / 'reports/remaining-acceptance/live30')
    parser.add_argument('--minimum-rounds', type=int, default=30)
    args = parser.parse_args()
    report = args.report_dir.resolve()
    data = guarded_data_dir()
    state = json.loads((report / 'state.json').read_text())
    if Path(state['data_directory']).resolve() != data:
        raise SystemExit('state.json 与 LEARNLOOP_DATA_DIR 不一致；未读取其他数据目录。')
    database = data / 'learnloop.sqlite3'
    if not database.is_file():
        raise SystemExit('独立验收数据库不存在；不会创建数据库。')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    # Import only storage/search modules, never the app lifespan, jobs or model adapter.
    from server import db, memory, ingest
    sid, space, goal, concept = (state[k] for k in ('session_id', 'space_id', 'goal_id', 'concept_id'))
    checks, notes = [], []

    def check(name, okay, details=None, pending=False):
        checks.append({'check': name, 'status': 'passed' if okay else 'pending' if pending else 'failed',
                       'details': details})

    with ExitStack() as stack:
        # The live harness holds exclusive locks on these existing files. Reading
        # with shared locks prevents the audit racing a new real teaching event.
        for name in ('live30-driver.lock', 'worker.lock'):
            path = data / name
            if not path.exists():
                raise SystemExit(f'缺少验收锁 {name}；请先初始化真实验收。')
            lock = stack.enter_context(path.open('r'))
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit('验收驱动或 worker 正在写入；请等待本轮完成后再审计。')
        con = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=30)
        stack.callback(con.close)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')

        @contextmanager
        def read_only_connection():
            # Actual memory.search/db.get algorithms are unchanged. Every access
            # shares this consistent, read-only transaction; writes are rejected.
            yield con

        original_connect = db.connect
        db.connect = read_only_connection
        stack.callback(setattr, db, 'connect', original_connect)
        all_records = [json.loads(r[0]) for r in con.execute('SELECT data FROM entities ORDER BY created,id')]
        records = {r['id']: r for r in all_records}
        kinds = defaultdict(list)
        for r in all_records:
            kinds[r['kind']].append(r)
        session = records[sid]
        events = [e for e in kinds['event'] if e.get('session_id') == sid]
        answers = [e for e in events if e.get('action') == 'answer']
        attempts = [a for a in kinds['attempt'] if a.get('session_id') == sid and a.get('event_id')]
        evidence = {r['id']: dict(r) for r in con.execute('SELECT * FROM evidence')}
        request_keys = {r['key']: r['entity'] for r in con.execute('SELECT * FROM request_keys')}
        operations = load_lines(report / 'operations.jsonl')
        submits = defaultdict(list)
        for op in operations:
            if op.get('operation') == 'submit':
                submits[op.get('job_id')].append(op)
        answer_attempts = defaultdict(list)
        for a in attempts:
            answer_attempts[a.get('event_id')].append(a)
        check('completed_real_answer_rounds', len(answers) >= args.minimum_rounds,
              {'actual': len(answers), 'minimum': args.minimum_rounds, 'excluded_actions': dict(Counter(e.get('action') for e in events if e not in answers))}, pending=True)
        check('unique_answer_task_ids', all(e.get('answered_task_id') for e in answers) and
              len({e.get('answered_task_id') for e in answers}) == len(answers),
              {'event_ids': [e['id'] for e in answers]})
        check('unique_event_jobs', len({e.get('job_id') for e in events}) == len(events))
        answer_requests = [submits[e.get('job_id')][0].get('request_id') for e in answers if len(submits[e.get('job_id')]) == 1]
        check('unique_answer_request_ids', len(answer_requests) == len(answers) and
              len(set(answer_requests)) == len(answer_requests) and all(answer_requests))
        event_results = []
        prior_question, prior_task, hint_level, answer_seen = '', None, 0, False
        for e in events:
            issues = []
            out = e.get('output') or {}
            snapshot = records.get(e.get('context_id'), {})
            call = records.get(e.get('model_call_id'), {})
            job = records.get(e.get('job_id'), {})
            linked = answer_attempts[e['id']]
            for field, wanted in [('space_id', space), ('goal_id', goal), ('concept_id', concept), ('session_id', sid)]:
                if e.get(field) != wanted:
                    issues.append(f'event {field} mismatch')
            if snapshot.get('kind') != 'context_snapshot' or snapshot.get('session_id') != sid or snapshot.get('space_id') != space or snapshot.get('goal_id') != goal:
                issues.append('context scope mismatch')
            if call.get('kind') != 'model_call' or call.get('status') != 'success' or not call.get('model'):
                issues.append('missing successful recorded model call')
            if job.get('status') != 'completed' or job.get('task') != 'session' or job.get('payload', {}).get('session_id') != sid:
                issues.append('job not a completed session request')
            if job.get('payload', {}).get('action') != e.get('action'):
                issues.append('job/event action mismatch')
            allowed_chunks = set(snapshot.get('source_ids', []))
            allowed_evidence = set(snapshot.get('evidence_ids', []) + snapshot.get('practice_ids', []))
            for cid in out.get('citations', []):
                chunk = records.get(cid, {})
                if cid not in allowed_chunks or chunk.get('kind') != 'chunk' or chunk.get('source_id') not in session.get('source_ids', []):
                    issues.append(f'invalid material citation {cid}')
            for eid in out.get('evidence_ids', []):
                record = records.get(eid, {})
                if eid not in allowed_evidence or record.get('kind') not in memory.EVIDENCE_KINDS or record.get('space_id') != space or record.get('goal_id') != goal:
                    issues.append(f'invalid historical citation {eid}')
            for eid in snapshot.get('evidence_ids', []) + snapshot.get('practice_ids', []) + snapshot.get('summary_ids', []):
                record = records.get(eid, {})
                if record.get('space_id') != space or record.get('goal_id') != goal:
                    issues.append(f'cross-goal context evidence {eid}')
                if record.get('created', '') > e.get('created', ''):
                    issues.append(f'context cites future evidence {eid}')
            budget, estimated = snapshot.get('budget', 12000), snapshot.get('estimated_tokens')
            if not isinstance(estimated, (int, float)) or estimated < 0 or estimated > budget:
                issues.append('estimated input exceeds budget or missing estimate')
            for field in ('source_revisions', 'summary_revisions', 'practice_revisions', 'note_revisions'):
                for eid, revision in snapshot.get(field, {}).items():
                    if eid not in records or revision > records[eid].get('revision', 1):
                        issues.append(f'unknown/future context version {eid}')
            # Historical snapshots may legitimately cite a revision invalidated
            # later. Only current ready summaries must match current revisions.
            if e.get('action') == 'answer':
                logged = submits[e.get('job_id')]
                if len(logged) != 1:
                    issues.append('answer does not have exactly one logged submit')
                else:
                    submit = logged[0]
                    key = f'event:{sid}:{submit.get("request_id")}'
                    if request_keys.get(key) != e.get('job_id'):
                        issues.append('request idempotency key does not resolve to event job')
                    if submit.get('task_id') != e.get('answered_task_id') or submit.get('answer') != e.get('answer'):
                        issues.append('submitted task/answer differs from stored event')
                if prior_task != e.get('answered_task_id'):
                    issues.append('answer task differs from preceding current task')
                if job.get('payload', {}).get('answer') != e.get('answer'):
                    issues.append('job answer differs from event answer')
                recovered = [a for a in linked if a.get('original_score_missing') and a.get('evaluator')=='reviewed_recovery']
                expected_attempts = 1 if (out.get('score') is not None or recovered) and session.get('mode') != 'explore' else 0
                if len(linked) != expected_attempts:
                    issues.append('answer assessment cardinality mismatch')
                for a in linked:
                    for field in ('space_id', 'goal_id', 'concept_id', 'session_id'):
                        if a.get(field) != e.get(field):
                            issues.append(f'attempt {field} mismatch')
                    if a.get('task_id') != e.get('answered_task_id') or normalized(a.get('question')) != normalized(prior_question):
                        issues.append('assessment bound to a different task/question')
                    if a.get('answer') != e.get('answer') or (not a.get('original_score_missing') and a.get('score') != out.get('score')):
                        issues.append('assessment original answer/score differs from event')
                    if a.get('original_score_missing') and (out.get('score') is not None or a.get('created')!=e.get('created') or not a.get('recovery_request_id') or not a.get('correction_reason')):
                        issues.append('recovered assessment lacks review provenance or original time')
                    if a.get('hint_level', 0) != hint_level or bool(a.get('answer_seen')) != answer_seen:
                        issues.append('assistance state lost before assessment')
                if expected_attempts:
                    hint_level, answer_seen = 0, False
            else:
                if linked or out.get('score') is not None:
                    issues.append('non-answer action created assessment/score')
                if e.get('action') == 'hint':
                    hint_level = min(3, hint_level + 1)
                if e.get('action') == 'reveal':
                    answer_seen = True
            next_question = normalized(out.get('question'))
            if next_question and next_question != normalized(prior_question):
                hint_level, answer_seen = 0, False
            prior_question, prior_task = out.get('question', ''), e.get('output_task_id')
            event_results.append({'event_id': e['id'], 'action': e.get('action'), 'task_id': e.get('answered_task_id'),
                'model_call_id': e.get('model_call_id'), 'context_id': e.get('context_id'),
                'estimated_tokens': estimated, 'budget': budget,
                'provider_prompt_tokens': (call.get('usage') or {}).get('prompt_tokens'), 'issues': issues})
        check('event_request_task_assessment_context_integrity', not any(x['issues'] for x in event_results), event_results)

        summaries = [s for s in kinds['summary'] if s.get('session_id') == sid]
        summary_results, covered, ready_versions = [], set(), Counter()
        for s in summaries:
            issues = []
            ready = s.get('status') == 'ready'
            inputs = s.get('evidence_ids', [])
            output_ids = s.get('output', {}).get('evidence_ids', [])
            if not output_ids or not set(output_ids) <= set(inputs):
                issues.append('summary output citation not in input whitelist')
            for eid in inputs:
                r = records.get(eid, {})
                if r.get('kind') not in ('event', 'attempt', 'run') or r.get('space_id') != space or r.get('goal_id') != goal:
                    issues.append(f'invalid summary source {eid}')
                if eid not in s.get('source_revisions', {}):
                    issues.append(f'missing source revision {eid}')
                if ready and (not evidence.get(eid, {}).get('active') or s['source_revisions'].get(eid) != r.get('revision', 1)):
                    issues.append(f'ready summary uses inactive/outdated source {eid}')
            event_ids = s.get('event_ids', [])
            run_ids = s.get('practice_ids', [])
            if not set(event_ids + run_ids) <= set(inputs):
                issues.append('summary coverage not in its evidence whitelist')
            if run_ids and (not s.get('output', {}).get('practice') or not set(run_ids) <= set(output_ids)):
                issues.append('real practice not separately summarized/cited')
            for eid in event_ids:
                if records.get(eid, {}).get('kind') != 'event' or records.get(eid, {}).get('session_id') != sid:
                    issues.append(f'invalid covered event {eid}')
                if ready:
                    covered.add(eid)
                    ready_versions[(eid, s.get('source_revisions', {}).get(eid))] += 1
            coverage_records = [records[i] for i in event_ids + run_ids if i in records]
            if coverage_records and s.get('covered_through') != max(r['created'] for r in coverage_records):
                issues.append('covered_through differs from actual covered records')
            if ready:
                if not evidence.get(s['id'], {}).get('active'):
                    issues.append('ready summary is not active in evidence index')
                for eid, version in s.get('material_versions', {}).items():
                    material = records.get(eid, {})
                    if material.get('kind') in ('source', 'chunk', 'entry', 'concept') and (material.get('revision', 1) != version or material.get('deleted') or material.get('memory_enabled') is False):
                        issues.append(f'ready summary references changed material {eid}')
            elif evidence.get(s['id'], {}).get('active'):
                issues.append('stale summary remains searchable')
            summary_results.append({'id': s['id'], 'status': s.get('status'), 'covered_event_count': len(event_ids),
                                    'covered_through': s.get('covered_through'), 'issues': issues})
        check('summary_ids_revisions_visibility_and_coverage', not any(s['issues'] for s in summary_results), summary_results)
        check('summary_no_duplicate_coverage', not any(n > 1 for n in ready_versions.values()),
              [{'event_id': key[0], 'revision': key[1], 'count': n} for key, n in ready_versions.items() if n > 1])
        uncovered = [e['id'] for e in events if evidence.get(e['id'], {}).get('active') and e['id'] not in covered]
        check('final_summary_covers_active_events', bool(summaries) and not uncovered,
              {'uncovered_event_ids': uncovered, 'ready_count': sum(s.get('status') == 'ready' for s in summaries)}, pending=True)
        summary_ops = [op for op in operations if op.get('operation') == 'summary']
        check('explicit_summary_did_not_create_assessments', all(op.get('created_no_teaching_evidence') is True for op in summary_ops),
              {'observed_operations': len(summary_ops), 'limitation': '仅显式摘要操作有前后计数；后台摘要另由事件/attempt一对一关联检查。'})

        grading = []
        for m in kinds['mastery']:
            if m.get('space_id') != space:
                continue
            relevant = [a for a in kinds['attempt'] if a.get('concept_id') == m['concept_id'] and a.get('space_id') == space and active_attempt(a)]
            score, eligible, assisted = 0.0, [], []
            for a in relevant:
                if a.get('hint_level') or a.get('answer_seen'):
                    assisted.append(a['id'])
                if a.get('score') is None or a.get('answer_seen') or a.get('error_type') == 'environment':
                    continue
                eligible.append(a)
                score = score * .7 + max(0, float(observed(a)) - .08 * int(a.get('hint_level', 0))) * .3
            successes = [a for a in relevant if independent(a)]
            dates = {datetime.fromisoformat(a['created']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() for a in successes}
            delayed = any((datetime.fromisoformat(b['created']) - datetime.fromisoformat(a['created'])).total_seconds() >= 86400
                          for a in successes for b in successes)
            success_times = [datetime.fromisoformat(a['created']) for a in successes]
            span = (max(success_times) - min(success_times)).total_seconds() if success_times else 0
            issues = []
            if abs(m.get('score', 0) - round(score, 3)) > .0005:
                issues.append('stored mastery differs from corrected eligible assessments')
            if m.get('evidence_count') != len(eligible) or m.get('assisted_count') != len(assisted):
                issues.append('eligible/assisted evidence count mismatch')
            if m.get('independent_dates') != len(dates):
                issues.append('independent success dates include unsupported evidence')
            if m.get('state') == 'strong' and not delayed:
                issues.append('strong state without any >=24h independent retest interval')
            if 'spaced_success_verified' in m and bool(m['spaced_success_verified']) != delayed:
                issues.append('stored spaced-success flag differs from independent timestamps')
            if 'independent_span_seconds' in m and abs(m['independent_span_seconds'] - span) > .001:
                issues.append('stored independent span differs from actual timestamps')
            grading.append({'mastery_id': m['id'], 'score': m.get('score'), 'replayed_score': round(score, 3),
                            'assisted_attempt_ids': assisted, 'independent_success_ids': [a['id'] for a in successes],
                            'independent_calendar_dates': sorted(dates), 'independent_span_seconds': span,
                            'has_24h_independent_interval': delayed, 'issues': issues})
        check('grading_replay_and_assistance_exclusion', not any(m['issues'] for m in grading), grading)

        def linked_attempts(hit):
            r = records.get(hit['id'], {})
            if r.get('kind') == 'attempt':
                return [r]
            if r.get('attempt_id') in records:
                return [records[r['attempt_id']]]
            return [a for a in attempts if a.get('event_id') == r['id']]

        early = next((e for e in answers if '折扣率范围误解' in e.get('answer', '')), None)
        later = next((e for e in answers if '订正我的早期误解' in e.get('answer', '')), None)
        retrieval_specs = [('early_error', '折扣率范围误解', early['id'] if early else None),
                           ('later_correction', '订正我的早期误解 折扣率不限定 不舍入', later['id'] if later else None)]
        corrected = [a for a in attempts if 'corrected_score' in a and active_attempt(a)]
        for a in corrected[:5]+[a for a in corrected[5:] if a.get('original_score_missing')]:
            retrieval_specs.append(('corrected_assessment', (a.get('correction_reason') or a.get('answer', ''))[:160], a.get('event_id')))
        retrieval = []
        for label, query, target in retrieval_specs:
            for semantic in (False, True):
                start = time.perf_counter()
                result = memory.search(query, concept_id=concept, space_id=space, goal_id=goal, limit=30, semantic=semantic)
                hits, issues, target_found = [], [], False
                for hit in result['items']:
                    r = records.get(hit['id'], {})
                    linked = linked_attempts(hit)
                    target_found |= r.get('id') == target or r.get('event_id') == target or any(a.get('event_id') == target for a in linked)
                    if r.get('space_id') != space or r.get('goal_id') != goal or not evidence.get(r.get('id'), {}).get('active'):
                        issues.append(f'out-of-scope/inactive search result {r.get("id")}')
                    corrections = []
                    indexed_text = evidence.get(hit['id'], {}).get('text', '')
                    for a in linked:
                        if 'corrected_score' in a:
                            corrections.append({'attempt_id': a['id'], 'original_score': a.get('score'),
                                'corrected_score': a['corrected_score'], 'correction_reason': a.get('correction_reason'),
                                'correction_stored_in_index': 'corrected_score' in indexed_text})
                            if 'corrected_score' not in indexed_text:
                                issues.append(f'current correction missing from indexed result {hit["id"]}')
                    hits.append({'id': hit['id'], 'kind': hit['kind'], 'revision': hit.get('revision'),
                        'linked_attempt_ids': [a['id'] for a in linked], 'answer': r.get('answer') or (linked[0].get('answer') if linked else None),
                        'excerpt': hit.get('excerpt'), 'corrections': corrections})
                retrieval.append({'case': label, 'query': query, 'semantic_requested': semantic,
                    'semantic_executed': '本地语义' in result.get('modes', []), 'modes': result.get('modes'),
                    'warning': result.get('warning'), 'target_event_id': target, 'target_found': target_found,
                    'elapsed_ms': round((time.perf_counter() - start) * 1000, 2), 'hits': hits, 'issues': issues})
        check('historical_retrieval_scope_and_current_corrections', not any(r['issues'] for r in retrieval),
              [{'case': r['case'], 'semantic_requested': r['semantic_requested'], 'issues': r['issues']} for r in retrieval])
        check('early_answer_and_later_correction_retrievable', bool(early and later) and all(r['target_found'] for r in retrieval),
              [{'case': r['case'], 'target_event_id': r['target_event_id'], 'target_found': r['target_found']} for r in retrieval])
        check('actual_local_semantic_retrieval_executed', all(r['semantic_executed'] for r in retrieval if r['semantic_requested']),
              [{'case': r['case'], 'modes': r['modes'], 'warning': r['warning']} for r in retrieval if r['semantic_requested']], pending=True)
        check('read_only_database_access', con.total_changes == 0, {'sqlite_query_only': con.execute('PRAGMA query_only').fetchone()[0], 'total_changes': con.total_changes})
        calls = kinds['model_call']
        manifest = json.loads((report / 'manifest.json').read_text()) if (report / 'manifest.json').exists() else {}
        check('cross_day_not_claimed_from_midnight_continuation',
              manifest.get('cross_day_retest', {}).get('status') in ('not_performed', 'pending') and
              manifest.get('cross_day_retest', {}).get('clock_simulation_used') is False,
              manifest.get('cross_day_retest'))
        reviewer = json.loads((report / 'reviewer-findings.json').read_text()) if (report / 'reviewer-findings.json').exists() else {}
        status_counts = Counter(c['status'] for c in checks)
        output = {'generated_at': stamp(), 'data_directory': str(data), 'session_id': sid, 'space_id': space, 'goal_id': goal,
                  'audit_scope': 'structural and retrieval integrity only; no semantic equivalence claimed',
                  'status': 'failed' if status_counts['failed'] else 'pending' if status_counts['pending'] else 'passed',
                  'check_counts': dict(status_counts), 'checks': checks, 'retrieval': retrieval,
                  'round_count': len(answers), 'event_count': len(events), 'attempt_count': len(attempts),
                  'recorded_model_calls': {'success': sum(c.get('status') == 'success' for c in calls),
                    'failed': sum(c.get('status') == 'failed' for c in calls),
                    'usage': {k: sum((c.get('usage') or {}).get(k, 0) or 0 for c in calls) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}},
                  'semantic_review': {'status': 'separate_manual_review_required',
                    'reviewed_answer_rounds': reviewer.get('reviewed_answer_rounds'), 'report': 'reviewer-findings.json',
                    'note': '引用ID和版本正确不代表AI解读、题目或评分正确；请独立核对原题、原答案、评分依据与摘要文义。'},
                  'cross_day_retest': {'status': 'pending', 'clock_simulation_used': False,
                    'note': '本次连续教学即使跨过午夜也不作为真实间隔复测。另行记录真实延迟复测，不根据事件日期数量自动认定。'},
                  'notes': notes}
        write_json(report / 'audit.json', output)
        print(json.dumps(sanitized({'status': output['status'], 'round_count': len(answers), 'checks': dict(status_counts),
              'report': str(report / 'audit.json'), 'failed_or_pending': [c for c in checks if c['status'] != 'passed']}), ensure_ascii=False, indent=2))
        return 1 if status_counts['failed'] else 2 if status_counts['pending'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
