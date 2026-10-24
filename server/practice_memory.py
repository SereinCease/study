"""Small, explicitly scoped projections of immutable runner facts for teaching memory."""
import hashlib
import json
from . import db


def relevant_runs(session, con=None, limit=6):
    """Never infer a run/session association from a title or a goal alone."""
    if not session.get('space_id') or not session.get('memory_enabled', True):
        return []
    if con is None:
        with db.connect() as c:
            return relevant_runs(session, c, limit)
    clauses = ["r.kind='run'", "json_extract(r.data,'$.space_id')=?",
               "json_extract(r.data,'$.goal_id') IS ?", 'e.active=1']
    args = [session['space_id'], session.get('goal_id')]
    links = []
    if session.get('id'):
        links.append("json_extract(r.data,'$.session_id')=?")
        args.append(session['id'])
    if session.get('concept_id'):
        links.append("json_extract(r.data,'$.concept_id')=?")
        args.append(session['concept_id'])
    if not links:
        return []
    clauses.append('(' + ' OR '.join(links) + ')')
    sql = 'SELECT r.data FROM entities r JOIN evidence e ON e.id=r.id WHERE ' + ' AND '.join(clauses) + ' ORDER BY r.created DESC,r.id DESC'
    if limit is not None:
        sql += ' LIMIT ?'
        args.append(limit)
    return [json.loads(row[0]) for row in con.execute(sql, args)]


def project(run, session):
    fields = ('id', 'revision', 'created', 'updated', 'exercise_id', 'space_id', 'goal_id',
              'session_id', 'concept_id', 'exit_code', 'counts', 'requirements_met',
              'rubric_checks', 'error_type', 'stop_reason', 'mutation_summary',
              'duration_ms', 'evaluation_version', 'report_origin', 'task_binding', 'grading_scope', 'linked_exercise_id')
    result = {key: run.get(key) for key in fields}
    result.update(
        provenance='actual_runner_report',
        practice_rules=['本报告只验证 exercise_id 对应固定练习的要求。session_context_only 只是会话上下文关联；即便显式关联 task_id，固定练习通过也不能证明内容不同的 AI 题目或变形题通过，不能据此完成或评分该 AI 题。'],
        rubric_scope='rubric_checks 主要是静态源码结构检查；出现 pytest.raises 或断言语法不证明该分支实际执行。Checkout 的固定缺陷检查单独以 mutation_summary 实际执行结果为准。失败后未到达的语句不得称为已通过。',
        association='explicit_session' if run.get('session_id') == session.get('id') and session.get('id') else 'same_goal_concept',
        attribution='真实运行器报告；摘要和引用不新增掌握证据，环境故障不证明代码已完成运行',
        assessment_voided=bool(run.get('assessment_voided')),
        assessment_void_id=run.get('assessment_void_id'),
        code_hash=hashlib.sha256(run.get('code', '').encode()).hexdigest(),
        code_excerpt=run.get('code', '')[:2000],
        code_truncated=len(run.get('code', '')) > 2000,
        stdout_excerpt=run.get('stdout', '')[:1000],
        stderr_excerpt=run.get('stderr', '')[:1000],
        logs_truncated=len(run.get('stdout', '')) > 1000 or len(run.get('stderr', '')) > 1000,
    )
    return result


def evidence(session, con=None, limit=6):
    return [project(run, session) for run in relevant_runs(session, con, limit)]


def pending(session, con=None):
    """Each run revision is summarized once; old prose is not sent again."""
    if con is None:
        with db.connect() as c:
            return pending(session, c)
    covered = {}
    for row in con.execute("SELECT data FROM entities WHERE kind='summary' AND json_extract(data,'$.session_id')=? AND json_extract(data,'$.status')='ready'", (session['id'],)):
        summary = json.loads(row[0])
        for rid in summary.get('practice_ids', []):
            covered[rid] = max(covered.get(rid, 0), summary.get('source_revisions', {}).get(rid, 0))
    runs = relevant_runs(session, con, limit=None)
    return [project(run, session) for run in reversed(runs)
            if covered.get(run['id']) != run.get('revision', 1)]
