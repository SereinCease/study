"""Read the pre-change online backup, migrate a disposable copy and verify preservation."""
import json,sqlite3,tempfile,shutil
from pathlib import Path
from server import db
backup=db.ROOT/'.core/backup-v4-20261023-205622'
original=backup/'data/learnloop.sqlite3'
if not original.exists():raise RuntimeError('指定备份不存在')
with sqlite3.connect(original) as c:
 before={r[0]:json.loads(r[1]) for r in c.execute('SELECT id,data FROM entities')}
with tempfile.TemporaryDirectory(prefix='learnloop-restore-check-') as folder:
 shutil.copytree(backup/'data',Path(folder)/'data')
 old=db.DATA;db.DATA=Path(folder)/'data'
 try:
  db.init()
  with db.connect() as c:
   after={r[0]:json.loads(r[1]) for r in c.execute('SELECT id,data FROM entities')}
   integrity=c.execute('PRAGMA integrity_check').fetchone()[0];foreign=[list(r) for r in c.execute('PRAGMA foreign_key_check')]
  protected=['answer','code','text','citation_ids','score','corrected_score','junit']
  changed=[id for id,v in before.items() if id not in after or any(v.get(k)!=after[id].get(k) for k in protected)]
  report={'backup':str(backup),'records_before':len(before),'records_after':len(after),'missing_ids':list(set(before)-set(after)),'changed_evidence':changed,'integrity':integrity,'foreign_key_errors':foreign,'status':'passed' if not changed and integrity=='ok' and not foreign else 'failed'}
 finally:db.DATA=old
(db.ROOT/'reports/restore-v4.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
