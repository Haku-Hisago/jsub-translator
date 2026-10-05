"""Resilience tests for the translation stage (src/translator.py).

Background
----------
A wrong API key used to be the *slowest* possible failure. Every batch retried
`MAX_RETRIES` times with exponential backoff, and the loop never aborted — so a
500-segment video (≈34 batches) spent roughly 34 × 6 s ≈ **3 minutes** sleeping
before finally reporting "translation failed".

Two fixes, both tested here:

1. `_is_retryable()` — a 401/403/400/404/422 is deterministic; retrying only
   delays the error. Those now fail immediately.
2. `EARLY_ABORT_AFTER` — since each batch already retried internally, three
   consecutive failed batches means nine consecutive failed API calls. That is a
   systematic fault, not a blip, so the run stops instead of grinding on.

These tests are offline: the translator is replaced with a fake.

Run:  .venv/Scripts/python.exe _test_translate_resilience.py
"""
import json
import sys
import time
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


from src import translator as T  # noqa: E402


class FakeHTTPError(Exception):
    def __init__(self, status, msg="boom"):
        super().__init__(msg)
        self.status_code = status


class FakeTranslator:
    """Stands in for a real engine; `behaviour` decides what it does."""

    def __init__(self, behaviour="ok", error=None):
        self.behaviour = behaviour
        self.error = error
        self.calls = 0

    @property
    def name(self):
        return "FakeEngine"

    def translate_batch(self, prompt, system=None, temperature=0.0):
        self.calls += 1
        if self.behaviour == "fail":
            raise self.error
        # 真实引擎返回的是 JSON 数组文本
        n = prompt.count('"id"')
        return json.dumps([{"id": i, "text": f"译{i}"} for i in range(n)],
                          ensure_ascii=False)


def segs(n):
    return [{"start": i * 1.0, "end": i * 1.0 + 1.0, "text": f"原文{i}"} for i in range(n)]


print("\n[_is_retryable：确定性错误不该重试]")
for status in (400, 401, 403, 404, 422):
    check(f"HTTP {status} 不重试", T._is_retryable(FakeHTTPError(status)) is False)

print("\n[_is_retryable：可恢复错误应当重试]")
for status in (408, 429, 500, 502, 503):
    check(f"HTTP {status} 可重试", T._is_retryable(FakeHTTPError(status)) is True)
check("无 status_code 的异常按可重试处理（如超时/断网）",
      T._is_retryable(TimeoutError("timed out")) is True)
check("response.status_code 也能识别",
      T._is_retryable(type("E", (Exception,), {"response": FakeHTTPError(401)})()) is False)

print("\n[401 必须立刻失败，不能退避重试]")
fake = FakeTranslator("fail", FakeHTTPError(401, "invalid api key"))
t0 = time.time()
try:
    fake.translate_batch("x")
    check("应抛异常", False)
except Exception:
    elapsed = time.time() - t0
    check("抛出了异常", True)
    check(f"耗时 < 1s（实际 {elapsed:.2f}s）—— 没有退避等待", elapsed < 1.0, f"{elapsed:.2f}s")
    check("只调用了一次（未重试）", fake.calls == 1, f"calls={fake.calls}")

print("\n[真实 DeepSeekTranslator 的重试行为]")
# 注意：重试逻辑在真实的 translator 里，不在上面的 FakeTranslator 里。
# 所以这里必须驱动真实类，只把底层 client 换掉。

class FakeCompletions:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        raise self.exc


class FakeClient:
    def __init__(self, exc):
        self.completions = FakeCompletions(exc)
        self.chat = type("C", (), {"completions": self.completions})()


def real_translator_with(exc):
    tr = T.DeepSeekTranslator(api_key="sk-test")
    fc = FakeClient(exc)
    tr._client = fc          # 绕过懒加载，直接注入
    return tr, fc


T.RETRY_DELAY = 0.01

tr, fc = real_translator_with(FakeHTTPError(503, "server error"))
try:
    tr.translate_batch("x")
    check("可恢复错误应最终抛出", False, "居然没抛")
except Exception:
    check(f"503 重试到上限（{T.MAX_RETRIES} 次）", fc.completions.calls == T.MAX_RETRIES,
          f"calls={fc.completions.calls}")

tr, fc = real_translator_with(FakeHTTPError(401, "invalid api key"))
try:
    tr.translate_batch("x")
    check("401 应抛出", False, "居然没抛")
except Exception:
    check("401 只调用一次（不重试）", fc.completions.calls == 1, f"calls={fc.completions.calls}")

print("\n[连续失败会提前中止，而不是跑完所有批次]")
T.RETRY_DELAY = 0.01
T.EARLY_ABORT_AFTER = 3
fake = FakeTranslator("fail", FakeHTTPError(503))
orig_create = T.create_translator
T.create_translator = lambda provider=None: fake
try:
    # 100 条字幕、每批 5 条 = 20 批；应在第 3 批就中止
    t0 = time.time()
    try:
        T.translate(segs(100), batch_size=5, context_window=0)
        check("应抛 RuntimeError", False, "居然正常返回了")
    except RuntimeError as e:
        msg = str(e)
        check("提前中止并报错", "提前中止" in msg, msg[:120])
        check("错误信息含未尝试批次数", "未尝试" in msg, msg[:160])
        check("错误信息给出排查方向（API Key）", "API Key" in msg, msg[:200])
        check(f"只尝试了 {T.EARLY_ABORT_AFTER} 批（不是全部 20 批）",
              fake.calls == T.EARLY_ABORT_AFTER, f"calls={fake.calls}")
        check(f"耗时很短（{time.time() - t0:.2f}s）", time.time() - t0 < 2.0)
finally:
    T.create_translator = orig_create

print("\n[中途恢复不会误判]")
class FlakyTranslator(FakeTranslator):
    """前两批失败，之后正常 —— 不应触发提前中止。"""
    def translate_batch(self, prompt, system=None, temperature=0.0):
        self.calls += 1
        if self.calls <= 2:
            raise FakeHTTPError(503)
        n = prompt.count('"id"')
        return json.dumps([{"id": i, "text": f"译{i}"} for i in range(n)],
                          ensure_ascii=False)

flaky = FlakyTranslator()
T.create_translator = lambda provider=None: flaky
try:
    out = T.translate(segs(20), batch_size=5, context_window=0)   # 4 批
    check("未提前中止（前两批失败后恢复）", len(out) == 20, f"得到 {len(out)} 条")
    check("恢复后的批次译文正常", out[-1]["translated"] == "译4", str(out[-1])[:80])
    check("失败批次仍被标记", "[翻译失败" in out[0]["translated"], str(out[0])[:80])
finally:
    T.create_translator = orig_create

print("\n[正常路径不受影响]")
ok = FakeTranslator("ok")
T.create_translator = lambda provider=None: ok
try:
    out = T.translate(segs(10), batch_size=5, context_window=0)
    check("全部翻译成功", len(out) == 10 and all("翻译失败" not in s["translated"] for s in out),
          str(out[0])[:80])
    check("调用了 2 批", ok.calls == 2, f"calls={ok.calls}")
    check("原文被保留", out[0]["text"] == "原文0")
finally:
    T.create_translator = orig_create

print("\n[失败标记：用布尔字段，不要靠匹配字符串]")
# 曾经：后端写 `[翻译失败: ...]`，前端再 `startsWith('[翻译失败')` 判断。
# 同一个约定两处硬编码 —— 后端一改文案，前端就**静默**失效，
# 失败的字幕会被当成正常译文显示出来。
from src.translator import FAILED_MARKER, _mark_failed  # noqa: E402

check("存在唯一的 FAILED_MARKER 常量", bool(FAILED_MARKER), FAILED_MARKER)
_marked = _mark_failed({"start": 0.0, "end": 1.0, "text": "あ"}, RuntimeError("boom"))
check("_mark_failed 写入可读文本", _marked["translated"].startswith(FAILED_MARKER),
      _marked["translated"])
check("_mark_failed 打布尔标记", _marked["failed"] is True, str(_marked))

# 后端除常量定义处，不应再有硬编码的 "[翻译失败"
# （注释里提到它是正常的，忽略以 # 开头的行）
_src_tr = (PROJ / "src" / "translator.py").read_text(encoding="utf-8")
_code_lines = [l for l in _src_tr.splitlines()
               if l.strip() and not l.lstrip().startswith("#")]
_code_hits = sum(l.count("[翻译失败") for l in _code_lines)
check("translator 代码里只出现一次（常量定义）",
      _code_hits == 1, f"代码里出现 {_code_hits} 次")

# 前端不得再字符串匹配
_html = (PROJ / "web" / "templates" / "index.html").read_text(encoding="utf-8")
check("前端不再 startsWith('[翻译失败')",
      "startsWith('[翻译失败'" not in _html)
check("前端改用 s.failed", "s.failed" in _html)

print("\n[真实调用：成功段落 failed 必须为 False]")
try:
    _real = T.translate([{"start": 0.0, "end": 2.0, "text": "こんにちは。"}])
    check("真实调用成功", bool(_real[0].get("translated")), str(_real[0])[:80])
    check("成功段落 failed=False", _real[0].get("failed") is False, str(_real[0])[:80])
except Exception as e:
    print(f"  （跳过：网络/API 不可用 -> {type(e).__name__}）")

print(f"\n{'=' * 46}\n  {PASS} passed, {FAIL} failed\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
