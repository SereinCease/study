// Clock state contains a deadline, not a decrementing interval counter.
export const DEFAULT_DURATION = 25 * 60_000;
export function freshTimer(durationMs = DEFAULT_DURATION) {
  return { durationMs, remainingMs: durationMs, deadline: null, status: 'idle', task: '' };
}
export function remaining(timer, now) {
  return timer.status === 'running' ? Math.max(0, timer.deadline - now) : timer.remainingMs;
}
export function reconcile(timer, now) {
  if (timer.status === 'running' && remaining(timer, now) === 0) return {...timer, status: 'complete', remainingMs: 0, deadline: null};
  return timer;
}
export function toggleTimer(timer, now) {
  const wasRunning = timer.status === 'running';
  timer = reconcile(timer, now);
  if (wasRunning && timer.status === 'complete') return timer;
  if (timer.status === 'running') return {...timer, remainingMs: remaining(timer, now), deadline: null, status: 'paused'};
  if (timer.status === 'complete') return {...timer, remainingMs: timer.durationMs, deadline: now + timer.durationMs, status: 'running'};
  return {...timer, deadline: now + timer.remainingMs, status: 'running'};
}
export function restoreTimer(raw, now) {
  try {
    const t = JSON.parse(raw);
    if (!t || !Number.isFinite(t.durationMs) || t.durationMs < 60_000 || t.durationMs > 120 * 60_000 ||
      !Number.isFinite(t.remainingMs) || t.remainingMs < 0 || t.remainingMs > t.durationMs ||
      !['idle','running','paused','complete'].includes(t.status) ||
      (t.status === 'running' && (!Number.isFinite(t.deadline) || t.deadline > now + t.durationMs + 1000))) return freshTimer();
    return reconcile({...t, task: String(t.task || '').slice(0, 160)}, now);
  } catch { return freshTimer(); }
}
export function timeLabel(ms) {
  const seconds = Math.ceil(Math.max(0, ms) / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
