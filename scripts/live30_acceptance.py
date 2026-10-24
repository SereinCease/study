"""Human-steered, real-provider acceptance; never fabricates teaching responses.

Requires a dedicated LEARNLOOP_DATA_DIR whose path includes ``live30``.  Each
``answer`` reads one answer file and sends one real API event.  No command loops
through prewritten questions or manufactures 30 successful turns.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REPORT = ROOT / "reports/remaining-acceptance/live30"


def sanitized(value):
    """Reports contain teaching records, never configuration credentials."""
    if isinstance(value, dict):
        return {k: sanitized(v) for k, v in value.items()
                if k.lower() not in {"token", "api_key", "apikey", "authorization", "secret", "key"}}
    if isinstance(value, list):
        return [sanitized(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(sanitized(value), ensure_ascii=False, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


def progress(message):
    print(message, flush=True)


def guarded_data_dir():
    raw = os.environ.get("LEARNLOOP_DATA_DIR")
    if not raw:
        raise ValueError("必须显式设置独立 LEARNLOOP_DATA_DIR；不会使用个人数据目录。")
    data = Path(raw).expanduser().resolve()
    if "live30" not in str(data).lower() or data == (ROOT / "server/data").resolve():
        raise ValueError("验收数据目录必须包含 live30，且不能是 server/data。")
    os.environ["LEARNLOOP_DATA_DIR"] = str(data)
    return data


class Harness:
    def __init__(self, report):
        self.data = guarded_data_dir()
        self.report = report.resolve()
        self.report.mkdir(parents=True, exist_ok=True)
        self.state_path = self.report / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        if self.state.get("data_directory", str(self.data)) != str(self.data):
            raise ValueError("state.json 属于另一个数据目录；请使用对应目录或另选 --report-dir。")
        self.state.setdefault("data_directory", str(self.data))
        from server import db, jobs
        from server.app import app
        from fastapi.testclient import TestClient
        self.db, self.jobs = db, jobs
        self.client_context = TestClient(app, base_url="http://127.0.0.1:8000")
        self.client = None
        self.stack = contextlib.ExitStack()

    def __enter__(self):
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Prevent two drivers, or a separate worker, from claiming the same jobs.
        for name in ("live30-driver.lock", "worker.lock"):
            lock = self.stack.enter_context((self.data / name).open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stack.close()
                raise ValueError("此验收目录已有驱动或 worker；请勿并发运行。")
        self.client = self.stack.enter_context(self.client_context)
        self.client.headers["x-learnloop-token"] = self.client.get("/api/session").json()["token"]
        self.client.headers["origin"] = "http://127.0.0.1:8000"
        self.state.setdefault("started_at", self.db.now())
        self.save_state()
        return self

    def __exit__(self, *exc):
        try:
            self.snapshot()
        finally:
            self.stack.close()

    def save_state(self):
        write_json(self.state_path, self.state)

    def log(self, operation, **values):
        item = sanitized({"at": self.db.now(), "operation": operation, **values})
        with (self.report / "operations.jsonl").open("a") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    def api(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = {"detail": "非 JSON API 错误"}
            self.log("api_error", method=method, path=path, status=response.status_code, detail=detail)
            raise ValueError(f"HTTP {response.status_code}: {sanitized(detail)}")
        return response.json()

    def records(self, kind, session_id=None):
        values = self.db.all(kind)
        if session_id:
            values = [v for v in values if v.get("session_id") == session_id]
        return sorted(values, key=lambda v: (v.get("created", ""), v["id"]))

    def call_ids(self):
        return {v["id"] for v in self.records("model_call")}

    def tick(self):
        started = time.monotonic()
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(12):
                active = [j for j in self.records("job") if j.get("status") == "running"]
                label = ", ".join(f"{j['task']} {j.get('progress', 0)}%" for j in active)
                progress(f"处理中 {int(time.monotonic() - started)}s · {label or '等待任务'}")

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            return self.jobs.tick()
        finally:
            stop.set()
            thread.join(timeout=1)

    def finish(self, job_id):
        while True:
            job = self.db.get(job_id, "job")
            status = job["status"]
            if status == "completed":
                progress(f"任务完成 · {job['task']} · {job_id}")
                return job
            if status in ("failed", "cancelled"):
                self.log("job_failed", job=job)
                raise ValueError(f"任务 {job['task']} {status}: {job.get('error') or job.get('error_code')}")
            if status == "running":
                raise ValueError("发现中断后仍为 running 的任务；请显式运行 resume 恢复，不自动重发答案。")
            progress(f"执行任务 · {job['task']} · {job_id}")
            if not self.tick():
                raise ValueError("没有可执行任务，未伪造成功结果。")

    def drain(self):
        # Only this isolated data directory is ever processed.  Failed jobs are
        # retained; no retry loop and no fabricated successful event count.
        for _ in range(100):
            queued = [j for j in self.records("job") if j.get("status") == "queued"]
            if not queued:
                return
            self.finish(queued[0]["id"])
        raise ValueError("后台队列超过 100 个任务，停止并请人工核对。")

    def init(self, args):
        source_file = args.source.resolve()
        if not source_file.is_file():
            raise ValueError(f"找不到自有验收资料：{source_file}")
        before = self.call_ids()
        if not self.state.get("space_id"):
            space = self.api("POST", "/api/spaces", json={
                "title": "自动化测试 · 30 轮真实教学验收", "domain_id": "automation-testing",
                "mode": "learn", "description": "独立验收数据：逐轮人工作答，真实模型与原始证据。"})
            self.state.update(space_id=space["id"], source_file=str(source_file),
                              source_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest())
            self.save_state()
        if not self.state.get("source_id"):
            source = self.api("POST", "/api/sources/import", params={"space_id": self.state["space_id"]},
                              files={"file": (source_file.name, source_file.read_bytes(), "text/markdown")})
            self.state.update(source_id=source["id"], import_job_id=source["job_id"])
            self.save_state()
        self.finish(self.state["import_job_id"])
        if not self.state.get("decompose_session_id"):
            session = self.api("POST", "/api/skill-sessions", json={
                "skill_id": "decompose", "space_id": self.state["space_id"],
                "source_ids": [self.state["source_id"]], "goal": args.goal, "reasoning": args.reasoning})
            self.state.update(decompose_session_id=session["id"], decompose_job_id=session["job_id"])
            self.save_state()
        self.finish(self.state["decompose_job_id"])
        self.log("init_import_decompose", model_call_ids=sorted(self.call_ids() - before))
        accepted = self.accept_cards(args.decisions_file)
        if not accepted:
            return
        self.start_teaching(args.goal, args.reasoning, args.concept_id)
        self.drain()
        self.inspect()

    def accept_cards(self, decisions_file=None):
        sid = self.state.get("decompose_session_id")
        if not sid:
            raise ValueError("请先 init 导入和拆解资料。")
        artifacts = self.records("artifact", sid)
        reviews = [self.api("GET", f"/api/artifacts/{a['id']}/review") for a in artifacts]
        write_json(self.report / "card-review.json", reviews)
        pending = [r for r in reviews if r["status"] != "accepted"]
        template = {"artifacts": [{"artifact_id": r["artifact_id"], "review_revision": r["revision"],
                    "decisions": [{"item_id": i["item_id"],
                        "action": "REVIEW" if i["requires_decision"] else "create",
                        "reason": "核对原文后填写；重复候选须明确合并、忽略或另建依据" if i["requires_decision"] else "验收原文中的独立知识点"}
                                  for i in r["items"]]} for r in pending]}
        write_json(self.report / "decisions-template.json", template)
        new_count = sum(len(r["items"]) for r in pending)
        accepted_count = len(self.api("GET", "/api/concepts", params={"space_id": self.state["space_id"]}))
        if decisions_file:
            chosen = json.loads(decisions_file.read_text())
            choices = {v["artifact_id"]: v for v in chosen["artifacts"]}
        else:
            if pending and (any(i["requires_decision"] for r in pending for i in r["items"])
                            or not 1 <= accepted_count + new_count <= 10):
                self.state["stage"] = "card_review_required"
                self.save_state()
                progress("拆解已保存。请核对 card-review.json，编辑 decisions-template.json 的副本，再执行 accept --file。")
                progress("未自动猜测知识卡合并；学习会话尚未启动。")
                return False
            choices = {v["artifact_id"]: v for v in template["artifacts"]}
        for review in pending:
            decision = choices.get(review["artifact_id"])
            if not decision:
                raise ValueError(f"缺少草稿决定：{review['artifact_id']}")
            result = self.api("PATCH", f"/api/artifacts/{review['artifact_id']}", json={
                "accept": True, "review_revision": decision["review_revision"],
                "decisions": decision["decisions"]})
            self.log("accept_cards", artifact_id=result["id"], decisions=decision["decisions"],
                     concept_ids=result.get("concept_ids", []))
        concepts = self.api("GET", "/api/concepts", params={"space_id": self.state["space_id"]})
        self.state["concept_ids"] = [c["id"] for c in concepts]
        self.state["stage"] = "cards_accepted"
        self.save_state()
        write_json(self.report / "concepts.json", concepts)
        if not 1 <= len(concepts) <= 10:
            raise ValueError(f"实际知识卡 {len(concepts)} 张，未满足验收范围的 1–10 张；请核对资料与拆解结果，不补造知识卡。")
        return True

    def start_teaching(self, title, reasoning, concept_id=None):
        if not self.state.get("preference_id"):
            preference = self.api("POST", "/api/learner-profile/facts", json={
                "space_id": self.state["space_id"], "domain_id": "automation-testing", "category": "preference",
                "text": "每轮聚焦一题，反馈简洁；30轮覆盖不同边界和迁移，不反复只换数字。"})
            self.state["preference_id"] = preference["id"]
            self.save_state()
        if not self.state.get("goal_id"):
            goal = self.api("POST", "/api/goals", json={"space_id": self.state["space_id"],
                "title": title, "source_ids": [self.state["source_id"]], "minutes": 30})
            self.state["goal_id"] = goal["id"]
            self.save_state()
        concepts = self.api("GET", "/api/concepts", params={"space_id": self.state["space_id"]})
        if not self.state.get("session_id"):
            if concept_id:
                candidate = next((c for c in concepts if c["id"] == concept_id), None)
            else:
                candidates = [c for c in concepts if any(s in c["title"].lower() for s in ("断言", "assert"))]
                candidate = candidates[0] if len(candidates) == 1 else None
            if not candidate:
                progress(json.dumps([{"id": c["id"], "title": c["title"]} for c in concepts], ensure_ascii=False))
                raise ValueError("断言知识点不唯一或未生成，请通过 --concept-id 明确指定，不猜测关联。")
            session = self.api("POST", "/api/skill-sessions", json={"skill_id": "explain",
                "space_id": self.state["space_id"], "goal_id": self.state["goal_id"],
                "concept_id": candidate["id"], "source_ids": [self.state["source_id"]], "reasoning": reasoning})
            self.state.update(session_id=session["id"], concept_id=candidate["id"], start_job_id=session["job_id"], stage="teaching")
            self.save_state()
        self.finish(self.state["start_job_id"])

    def event(self, action, answer="", answer_file=None, reasoning=None):
        sid = self.require_session()
        previous = self.api("GET", f"/api/skill-sessions/{sid}")
        request_id = "live30-" + uuid.uuid4().hex
        before = self.call_ids()
        job = self.api("POST", f"/api/skill-sessions/{sid}/events", json={
            "action": action, "answer": answer, "request_id": request_id, "reasoning": reasoning})
        self.state["last_job_id"] = job["id"]
        self.save_state()
        self.log("submit", action=action, request_id=request_id, job_id=job["id"],
                 task_id=previous.get("current_task_id"), task=previous.get("current_task"),
                 answer=answer, answer_file=str(answer_file) if answer_file else None)
        try:
            result = self.finish(job["id"])
            self.log("event_completed", action=action, job_id=job["id"], result=result.get("result"),
                     model_call_ids=sorted(self.call_ids() - before))
            self.drain()
        finally:
            self.log("event_request_finished", action=action, job_id=job["id"], model_call_ids=sorted(self.call_ids() - before))
        self.inspect()

    def require_session(self):
        if not self.state.get("session_id"):
            raise ValueError("尚无精讲会话；请先完成 init/accept。")
        return self.state["session_id"]

    def summary(self):
        sid = self.require_session()
        before_counts = (len(self.records("attempt")), len(self.records("event")))
        before_calls = self.call_ids()
        job = self.api("POST", f"/api/skill-sessions/{sid}/summary")
        if job.get("id"):
            self.finish(job["id"])
        self.drain()
        after_counts = (len(self.records("attempt")), len(self.records("event")))
        self.log("summary", result=job, model_call_ids=sorted(self.call_ids() - before_calls),
                 attempts_events_before=before_counts, attempts_events_after=after_counts,
                 created_no_teaching_evidence=before_counts == after_counts)
        if before_counts != after_counts:
            raise ValueError("摘要操作改变了作答或教学事件数量，需排查。")
        self.inspect()

    def snapshot(self):
        sid = self.state.get("session_id")
        kinds = ("source", "chunk", "concept", "artifact", "goal", "session", "event", "attempt",
                 "mastery", "mistake", "review", "summary", "context_snapshot", "assessment_action", "job")
        records = {kind: self.records(kind) for kind in kinds}
        calls = [{k: c.get(k) for k in ("id", "created", "profile_id", "model", "reasoning", "max_output_tokens",
                    "status", "usage", "duration_ms", "error_code", "message")} for c in self.records("model_call")]
        write_json(self.report / "records.json", records)
        write_json(self.report / "model-calls.json", calls)
        events = self.records("event", sid) if sid else []
        attempts = self.records("attempt", sid) if sid else []
        summaries = self.records("summary", sid) if sid else []
        successful_answers = [e for e in events if e.get("action") == "answer"]
        manifest = {**self.state, "updated_at": self.db.now(), "transport": "FastAPI TestClient + local session token",
            "provider_responses": "real; no mocks or synthetic teaching turns",
            "teaching_events": len(events), "successful_answer_events": len(successful_answers),
            "successful_answer_event_ids": [e["id"] for e in successful_answers],
            "non_answer_events": len(events) - len(successful_answers), "attempt_count": len(attempts),
            "active_attempt_count": sum(not a.get("assessment_voided") and not a.get("deleted") for a in attempts),
            "model_success_count": sum(c["status"] == "success" for c in calls),
            "model_failed_count": sum(c["status"] == "failed" for c in calls),
            "usage": {k: sum((c.get("usage") or {}).get(k, 0) or 0 for c in calls)
                      for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
            "summaries": [{k: s.get(k) for k in ("id", "status", "event_ids", "evidence_ids", "covered_through", "source_revisions")} for s in summaries],
            "cross_day_retest": {"status": "not_performed", "clock_simulation_used": False,
                                 "note": "真实跨日复测独立记录；本驱动不修改时钟，也不据事件数量推断已完成。"},
            "semantic_review": "requires human review of question, rubric, feedback and cited raw evidence"}
        write_json(self.report / "manifest.json", manifest)
        write_json(self.report / "transcript.json", {"session_id": sid, "events": events, "attempts": attempts, "summaries": summaries})
        text = ["# 30 轮真实教学记录", "", "每条回答均从外部文件逐次提交；提示、开始和摘要不计入成功回答轮次。", ""]
        for index, e in enumerate(events, 1):
            linked = [a for a in attempts if a.get("event_id") == e["id"]]
            output = e.get("output", {})
            text.extend([f"## 事件 {index} · {e.get('action')} · {e['created']}", "",
                         f"事件 `{e['id']}` · 模型调用 `{e.get('model_call_id')}`", ""])
            for a in linked:
                text.extend(["### 本次回答的题目", a.get("question", ""), "", "### 评分依据", str(a.get("rubric", "")), "",
                    f"评价 `{a['id']}` · 原分数 {a.get('score')} · 纠正分数 {a.get('corrected_score')} · 作废 {bool(a.get('assessment_voided'))}", ""])
            for label, content in (("用户回答", e.get("answer")), ("反馈", output.get("feedback")),
                    ("讲解", output.get("explanation")), ("资料补充", output.get("supplement")),
                    ("当前新题", output.get("question")), ("当前题评分依据", output.get("rubric"))):
                if content:
                    text.extend(["### " + label, str(content), ""])
            text.extend(["引用：" + json.dumps({"citations": output.get("citations"), "evidence_ids": output.get("evidence_ids")}, ensure_ascii=False), ""])
        for s in summaries:
            text.extend([f"## 摘要 {s['id']} · {s.get('status')}", "", "```json", json.dumps(s.get("output"), ensure_ascii=False, indent=2), "```", ""])
        (self.report / "transcript.md").write_text(sanitized("\n".join(text)))
        if sid:
            memory = self.api("GET", f"/api/skill-sessions/{sid}/memory")
            write_json(self.report / "current-memory.json", memory)
            write_json(self.report / "current-session.json", self.api("GET", f"/api/skill-sessions/{sid}"))
        return manifest

    def inspect(self, query=None):
        sid = self.require_session()
        session = self.api("GET", f"/api/skill-sessions/{sid}")
        events = self.records("event", sid)
        latest = events[-1] if events else {}
        attempts = [a for a in self.records("attempt", sid) if a.get("event_id") == latest.get("id")]
        summary = self.snapshot()
        memory = self.api("GET", f"/api/skill-sessions/{sid}/memory")
        progress(json.dumps({"session_id": sid, "successful_answer_events": summary["successful_answer_events"],
            "teaching_events": len(events), "latest_event_id": latest.get("id"), "model_call_id": latest.get("model_call_id"),
            "attempts": [{k: a.get(k) for k in ("id", "question", "rubric", "score", "corrected_score", "assessment_voided", "hint_level", "answer_seen")} for a in attempts],
            "feedback": latest.get("output", {}).get("feedback"), "current_task": session.get("current_task"),
            "task_id": session.get("current_task_id"), "task_stale": session.get("current_task_stale"),
            "task_voided": session.get("current_task_voided"), "task_answered": session.get("current_task_answered"),
            "summaries": [{"id": s["id"], "status": s.get("status"), "event_count": len(s.get("event_ids", []))} for s in memory.get("summaries", [])[:3]],
            "reference_check": self.references(latest),
            "context": {k: (memory.get("context") or {}).get(k) for k in ("id", "event_ids", "evidence_ids", "summary_ids", "estimated_tokens")},
            "report_directory": str(self.report)}, ensure_ascii=False, indent=2))
        if query:
            result = self.api("GET", "/api/evidence/search", params={"q": query, "space_id": self.state["space_id"],
                                  "concept_id": self.state["concept_id"], "limit": 20})
            write_json(self.report / "last-evidence-search.json", result)
            progress(json.dumps({"query": query, "evidence_search": result}, ensure_ascii=False, indent=2))

    def references(self, event):
        result = {"citations": [], "evidence_ids": [], "all_exist": True, "scope_valid": True}
        for field in ("citations", "evidence_ids"):
            for identifier in event.get("output", {}).get(field, []):
                try:
                    record = self.db.get(identifier)
                    valid = record.get("kind") == "chunk" if field == "citations" else record.get("space_id") == self.state.get("space_id")
                    result[field].append({"id": identifier, "kind": record.get("kind"), "scope_valid": valid})
                    result["scope_valid"] &= valid
                except ValueError:
                    result[field].append({"id": identifier, "missing": True})
                    result["all_exist"] = False
        return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report-dir", type=Path, default=REPORT)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("init", "accept"):
        q = sub.add_parser(name)
        q.add_argument("--goal", default="契约驱动测试：30轮长会话复验")
        q.add_argument("--reasoning", choices=("fast", "standard", "deep", "max"), default="standard")
        q.add_argument("--concept-id")
        if name == "init":
            q.add_argument("--source", type=Path, default=ROOT / "samples/live30-automation.md")
            q.add_argument("--decisions-file", type=Path)
        else:
            q.add_argument("--file", type=Path, required=True)
    q = sub.add_parser("answer")
    q.add_argument("--file", type=Path, required=True)
    q.add_argument("--reasoning", choices=("fast", "standard", "deep", "max"))
    q = sub.add_parser("action")
    q.add_argument("action", choices=("continue", "hint", "answer-show", "reveal", "simpler", "deeper", "example", "skip"))
    q.add_argument("--reasoning", choices=("fast", "standard", "deep", "max"))
    sub.add_parser("summary")
    q = sub.add_parser("inspect")
    q.add_argument("--query")
    sub.add_parser("resume", help="独立目录中显式恢复中断任务；失败任务保留，不自动重试。")
    return p


def main():
    args = parser().parse_args()
    try:
        with Harness(args.report_dir) as h:
            if args.command == "init":
                h.init(args)
            elif args.command == "accept":
                if h.accept_cards(args.file):
                    h.start_teaching(args.goal, args.reasoning, args.concept_id)
                    h.drain()
                    h.inspect()
            elif args.command == "answer":
                text = args.file.read_text().strip()
                if not text:
                    raise ValueError("回答文件为空；不会提交或计为一轮。")
                h.event("answer", text, args.file.resolve(), args.reasoning)
            elif args.command == "action":
                h.event("reveal" if args.action == "answer-show" else args.action, reasoning=args.reasoning)
            elif args.command == "summary":
                h.summary()
            elif args.command == "inspect":
                h.inspect(args.query)
            elif args.command == "resume":
                h.jobs.recover()
                h.drain()
                h.inspect()
    except Exception as error:
        print("验收未完成：" + str(sanitized(str(error))), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
