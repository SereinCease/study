import test from 'node:test';
import assert from 'node:assert/strict';
import {freshTimer,remaining,reconcile,toggleTimer,restoreTimer,timeLabel} from './timer-core.js';
test('background time and reload use the original deadline',()=>{
 const t=toggleTimer(freshTimer(),10000);
 assert.equal(remaining(t,310000),20*60000);
 const restored=restoreTimer(JSON.stringify(t),310000);
 assert.equal(restored.deadline,t.deadline);
 assert.equal(remaining(restored,310000),20*60000);
});
test('pause and resume preserve remaining time without counting paused time',()=>{
 let t=toggleTimer(freshTimer(60000),10000);t=toggleTimer(t,25000);
 assert.equal(remaining(t,200000),45000);
 t=toggleTimer(t,200000);assert.equal(t.deadline,245000);
});
test('expiry is idempotent and does not auto-start another round',()=>{
 const t=toggleTimer(freshTimer(60000),1000);
 const done=reconcile(t,61000);assert.equal(done.status,'complete');
 assert.deepEqual(reconcile(done,90000),done);
 assert.equal(restoreTimer(JSON.stringify(t),90000).status,'complete');
 assert.equal(toggleTimer(done,100000).deadline,160000);
});
test('corrupt, malformed and unbounded persisted values reset safely',()=>{
 for(const raw of ['{','null','{}',JSON.stringify({...freshTimer(),durationMs:-1}),JSON.stringify({...freshTimer(),status:'running',deadline:null})])assert.deepEqual(restoreTimer(raw,0),freshTimer());
});
test('display rounds upwards, never negative',()=>{
 assert.equal(timeLabel(1499999),'25:00');assert.equal(timeLabel(1),'00:01');assert.equal(timeLabel(0),'00:00');assert.equal(timeLabel(-1),'00:00');
});

test('a pause click at the exact deadline completes, rather than restarting',()=>{
 const t=toggleTimer(freshTimer(60000),1000);
 assert.equal(toggleTimer(t,61000).status,'complete');
});
