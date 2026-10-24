"""Real adaptive acceptance student; never uses the teacher's hidden rubric.
Both sides use the configured provider. This is software evaluation, not a human study.
"""
import json
from scripts.live30_acceptance import Harness,REPORT,write_json
from server import models,db

with Harness(REPORT) as h:
    while True:
        events=[e for e in h.records('event',h.require_session()) if e['action']=='answer']
        number=len(events)+1
        if number>30:break
        if number==10 and not any(e['action']=='reveal' for e in h.records('event',h.require_session())):
            h.event('reveal')
        s=h.api('GET','/api/skill-sessions/'+h.require_session())
        question=s['current_task']['question']
        if not question.strip() or s.get('current_task_answered'):
            h.event('continue');s=h.api('GET','/api/skill-sessions/'+h.require_session());question=s['current_task']['question']
        previous=[{'question':a['question'],'answer':a['answer'],'feedback':a.get('correction_reason',a.get('feedback'))} for a in h.records('attempt',h.require_session())[-2:]]
        instruction=('本轮刻意提交不完整尝试：只完成第一项，明确其他项尚未完成，不伪装完整正确。' if number==9 else '认真独立解决当前题目，逐项回应，不查阅隐藏评分标准。若上轮有真实错误，先简短订正。')
        system='你是软件验收中的自动化测试学习者代理。你的答案不是人类学习效果证据。总长控制在1200汉字以内，代码精简完整，不重复题面，不展开长篇旁支。根据实际题目编写简洁作答；需要代码时给完整代码。不得宣称自己执行了未执行的代码、不得伪造报告或引用。题中契约仅用于静态推理。不要指挥教师评分，不要求满分。'+instruction
        with models.options(reasoning='fast'):
            answer=models.generate(system,json.dumps({'question':question,'recent_attempts':previous,'answer_was_shown':s.get('answer_seen',False)},ensure_ascii=False))
        call=models.last_call()
        if call:db.update(call,{'acceptance_role':'adaptive_student','acceptance_round':number})
        path=REPORT/'answers'/f'{number:02d}.md';path.write_text(answer);path.chmod(0o600)
        h.log('adaptive_student_generated',round=number,model_call_id=call,hidden_rubric_supplied=False)
        h.event('answer',answer,answer_file=path,reasoning='fast')
        print('COMPLETED_ROUND',number,flush=True)
    h.summary()
    job=h.api('POST','/api/evidence/reindex');h.finish(job['id']);h.drain()
    h.inspect()
