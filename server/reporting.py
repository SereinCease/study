from . import db

def learning_report():
    clean=lambda s:str(s).replace('|','／').replace('\n',' ')
    concepts=[c for c in db.all('concept') if not c.get('merged_into')]
    mastery={m['concept_id']:m for m in db.all('mastery')}
    reviews={r['concept_id']:r for r in db.all('review')}
    lines=['# Learnloop 学习报告','',f'生成时间：{db.now()}','',
      '掌握度是根据作答证据计算的估计值，不代表能力认证。阅读、跳过、看完整答案和环境错误不增加掌握度。','',
      '## 学习目标','']
    lines += [f'- {clean(g["title"])}（每天 {g["minutes"]} 分钟）' for g in db.all('goal')] or ['尚未创建目标。']
    lines += ['','## 知识点与复习','', '| 知识点 | 估计掌握度 | 证据数 | 独立成功日期数 | 变形题验证 | 下次复习（UTC） |','|---|---:|---:|---:|---|---|']
    for c in concepts:
        m=mastery.get(c['id'],{});r=reviews.get(c['id'],{})
        lines.append(f'| {clean(c.get("title",c.get("label","")))} | {round(m.get("score",0)*100)}% | {m.get("evidence_count",0)} | {m.get("independent_dates",0)} | {"是" if m.get("transfer_verified") else "否"} | {r.get("due","未安排")} |')
    lines += ['','## 代码运行记录','']
    for run in db.all('run'):
        lines.append(f'- {run["exercise_id"]}：退出码 {run["exit_code"]}，测试 {run["counts"]["tests"]}，失败 {run["counts"]["failures"]}；任务要求{"通过" if run["requirements_met"] else "未通过"}。时间：{run["created"]}。')
    lines += ['','JUnit 报告由学习代码生成；任务检查是辅助证据。','', '## 错题与补练','']
    state={'pending_analysis':'待分析','pending_practice':'待补练','corrected_pending_review':'已纠正待复习','review_passed':'复习通过'}
    for m in db.all('mistake'):
        lines.append(f'- {clean(m.get("question",""))}：{state.get(m["status"],m["status"])}，补练 {len(m.get("practice_attempts",[]))} 次。')
    lines += ['','## 计算依据','','有效表现按 0.7 × 旧分数 + 0.3 × 本次表现更新，每级提示折减本次表现 0.08。分数至少 0.8、两个不同日期的独立成功及一次变形题成功，才显示掌握较好。复习通过后按 1、3、7、14 天推进，失败回到 1 天。','']
    return '\n'.join(lines)
