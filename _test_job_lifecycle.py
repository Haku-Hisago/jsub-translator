"""Job-lifecycle tests for app.py (src: the in-memory _jobs registry).

Background
----------
`_jobs` is keyed by job id and each entry keeps the job's **full result** — every
subtitle segment with both the Japanese text and the Chinese translation. Nothing
ever removed entries, so a long-running session accumulated one complete result
per processed video, forever.

The fix keeps only the most recent N *finished* jobs, evicting by **finish order**
(not creation order — a job created first may finish last and should not be the
first one dropped). Running jobs are never evicted.

Run:  .venv/Scripts/python.exe _test_job_lifecycle.py
"""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


import app as A  # noqa: E402


def reset():
    with A._lock:
        A._jobs.clear()
        A._finished_order.clear()


def add_job(job_id, status="running"):
    with A._lock:
        A._jobs[job_id] = {"queue": None, "status": status, "result": None}


print("\n[已结束任务按上限淘汰]")
reset()
limit = A._MAX_FINISHED_JOBS
for i in range(limit + 5):
    jid = f"job{i}"
    add_job(jid)
    A._mark_finished(jid)

check(f"已结束任务数不超过上限（{limit}）", len(A._jobs) == limit, f"实际 {len(A._jobs)}")
check("淘汰的是最早的", "job0" not in A._jobs and "job4" not in A._jobs,
      f"残留 {sorted(A._jobs)}")
check("保留的是最近的", f"job{limit + 4}" in A._jobs, f"残留 {sorted(A._jobs)}")
check("_finished_order 与 _jobs 一致", len(A._finished_order) == limit,
      str(len(A._finished_order)))

print("\n[运行中的任务永不淘汰]")
reset()
for i in range(limit + 5):
    add_job(f"fin{i}")
    A._mark_finished(f"fin{i}")
add_job("long_running")          # 仍在运行
A._mark_finished("fin0")         # 再来一个，触发淘汰
check("运行中的任务未被淘汰", "long_running" in A._jobs, f"残留 {sorted(A._jobs)}")
check("已结束任务仍受限", len(A._finished_order) <= limit, str(len(A._finished_order)))

print("\n[按结束顺序淘汰，而不是创建顺序]")
reset()
# 创建顺序 j0..j10，但结束顺序完全颠倒（j10 最先结束、j0 最后结束）
jobs = [f"j{i}" for i in range(limit + 1)]
for j in jobs:
    add_job(j)
for j in reversed(jobs):
    A._mark_finished(j)
# _finished_order = [j10, j9, ..., j0]，超出上限 1 个 → 应淘汰最先结束的 j10
check("最先结束的被淘汰（尽管它是最后创建的）", "j10" not in A._jobs,
      f"残留 {sorted(A._jobs)}")
check("最后结束的被保留（尽管它是最先创建的）", "j0" in A._jobs,
      f"残留 {sorted(A._jobs)}")

print("\n[重复标记同一任务不会重复计数]")
reset()
add_job("dup")
A._mark_finished("dup")
A._mark_finished("dup")
A._mark_finished("dup")
check("_finished_order 无重复项", len(A._finished_order) == 1, str(A._finished_order))

print("\n[接口行为：被淘汰的任务返回 404]")
reset()
add_job("gone")
A._mark_finished("gone")
client = A.app.test_client()
r = client.get("/api/result/gone")
check("已淘汰任务的 result 仍可取（未超限）", r.status_code == 200, str(r.status_code))

# 制造超限，确认最老的返回 404 而不是 500
for i in range(limit + 3):
    add_job(f"x{i}")
    A._mark_finished(f"x{i}")
r = client.get("/api/result/gone")
check("超限后被淘汰的任务返回 404", r.status_code == 404, str(r.status_code))
check("404 响应是 JSON（不是 HTML）", r.is_json, r.content_type)

r = client.get("/api/result/never_existed")
check("不存在的任务返回 404 JSON", r.status_code == 404 and r.is_json,
      f"{r.status_code} {r.content_type}")

print("\n[未超限时结果完整保留]")
reset()
add_job("keepme")
with A._lock:
    # 字段要齐全 —— /api/result 会读 segment 的 start/end
    A._jobs["keepme"]["result"] = {
        "title": "t",
        "video_path": "",
        "segments": [{"start": 0.0, "end": 1.0, "text": "あ", "translated": "啊",
                      "speaker": ""}],
        "files": {},
        "speakers": set(),
    }
    A._jobs["keepme"]["status"] = "done"
r = client.get("/api/result/keepme")
check("已完成任务的结果可读取", r.status_code == 200 and r.get_json()["status"] == "done",
      f"{r.status_code} {r.data[:120]}")
if r.status_code == 200:
    d = r.get_json()
    check("结果内容完整（含译文）",
          d["count"] == 1 and d["segments"][0]["translated"] == "啊", str(d)[:160])

print("\n[失败的任务必须报 error，不能永远 running]")
# 触发场景：任务失败后用户刷新了页面，SSE 连接已断。
# 前端只能靠轮询 /api/result —— 如果这里永远回 "running"，
# 界面就会一直转圈，用户既看不到错误也没法重试。
reset()
add_job("boom")
with A._lock:
    A._jobs["boom"]["status"] = "error"
    A._jobs["boom"]["error"] = "下载失败：连接被拒绝"
r = client.get("/api/result/boom")
check("失败任务返回 status=error", r.status_code == 200 and r.get_json()["status"] == "error",
      str(r.get_json())[:120])
check("失败任务带回错误原因", "连接被拒绝" in (r.get_json().get("error") or ""),
      str(r.get_json())[:160])

# 没有记录原因时也不能崩，要给个兜底文案
reset()
add_job("boom2")
with A._lock:
    A._jobs["boom2"]["status"] = "error"
r = client.get("/api/result/boom2")
check("未记录原因时有兜底文案",
      r.status_code == 200 and bool(r.get_json().get("error")), str(r.get_json())[:120])

print("\n[SSE 重连：已结束的任务要补发终结事件]")
# 进度队列是一次性消费的。客户端断线重连时，done/error 已被上一个连接取走，
# 若不补发，重连后只会收到 ping，永远等不到结果。
reset()
add_job("donejob")
with A._lock:
    A._jobs["donejob"]["result"] = {"title": "T", "segments": [{}, {}], "files": {},
                                    "speakers": set()}
    A._jobs["donejob"]["status"] = "done"
body = client.get("/api/progress/donejob").get_data(as_text=True)
check("重连后能收到 done 事件", '"type": "done"' in body or '"type":"done"' in body,
      body[:160])
check("done 事件带回条数", '"count"' in body, body[:160])

reset()
add_job("errjob")
with A._lock:
    A._jobs["errjob"]["status"] = "error"
    A._jobs["errjob"]["error"] = "炸了"
body = client.get("/api/progress/errjob").get_data(as_text=True)
check("重连后能收到 error 事件", '"type": "error"' in body or '"type":"error"' in body,
      body[:160])
check("error 事件带回原因", "炸了" in body, body[:160])

print("\n[运行中的任务 SSE 仍走队列]")
reset()
import queue as _q  # noqa: E402
add_job("live")
live_q = _q.Queue()
with A._lock:
    A._jobs["live"]["queue"] = live_q
live_q.put({"type": "progress", "pct": 50, "stage": "translate", "message": "进行中"})
live_q.put({"type": "done", "title": "T", "count": 1})
body = client.get("/api/progress/live").get_data(as_text=True)
check("运行中任务的队列消息被转发", "进行中" in body, body[:200])
check("队列里的 done 会终止流", '"type": "done"' in body or '"type":"done"' in body,
      body[:200])

print("\n[被淘汰的任务：前端不能静默卡在转圈]")
# 后端对不存在的任务回 {"error": "任务不存在"} 且**不带 status**。
# 前端若只判断 status，就会落到 `status !== 'done'` 那条静默 return ——
# 界面永远停在转圈，用户既看不到原因也无法重试。
# （与「失败任务永远报 running」是同一类问题，只是入口不同。）
reset()
r = client.get("/api/result/never-existed")
_d = r.get_json()
check("不存在任务的响应带 error", r.status_code == 404 and "error" in _d, str(_d)[:100])
check("该响应确实没有 status 字段（前端不能只靠 status 判断）",
      "status" not in _d, str(_d)[:100])

_src = (Path(__file__).resolve().parent / "web" / "templates" / "index.html").read_text(
    encoding="utf-8")
_i_err = _src.find("if (data.error)")
_i_done = _src.find("if (data.status !== 'done')")
check("前端先处理 data.error", _i_err != -1, "未找到 data.error 分支")
check("data.error 分支在 status 判断之前",
      _i_err != -1 and _i_done != -1 and _i_err < _i_done,
      f"error@{_i_err} done@{_i_done}")

reset()  # 收尾，别把状态留给别的测试
print(f"\n{'=' * 46}\n  {PASS} passed, {FAIL} failed\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
