"""Safety tests for the cache-cleanup path (src/cache.py).

This is the only code in the project that **deletes files**, so it gets its own
safety suite. Two things are being guarded:

1. **Path containment** — deletion must only ever touch files under OUTPUT_DIR or
   the system temp dir, and must never remove OUTPUT_DIR itself.
2. **Misconfigured OUTPUT_DIR** — `_categories()` finds videos via
   `OUTPUT_DIR.glob("*")`. If `.env` set OUTPUT_DIR to a *drive root* (`E:/`) or a
   system directory, that glob would sweep up unrelated files and "clean cache"
   would silently become "delete the user's data". `is_dangerous_root()` refuses
   to run in that case.

Run:  .venv/Scripts/python.exe _test_cache_safety.py
"""
import os
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent
PY = str(PROJ / ".venv" / "Scripts" / "python.exe")

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


sys.path.insert(0, str(PROJ))
from src import cache  # noqa: E402

print("\n[is_dangerous_root：该拦的拦住]")
for p, label in [
    (Path("E:/"), "盘根 E:"),
    (Path("C:/"), "盘根 C:"),
    (Path("C:/Windows"), "系统目录"),
    (Path("C:/Program Files"), "系统目录"),
    (Path("C:/Program Files (x86)"), "系统目录"),
    (Path("C:/Users"), "系统目录"),
    (Path("C:/ProgramData"), "系统目录"),
    (Path.home(), "用户主目录"),
]:
    check(f"判为危险: {label} ({p})", cache.is_dangerous_root(p) is True, str(p))

print("\n[is_dangerous_root：不该拦的别拦]")
for p, label in [
    (cache.OUTPUT_DIR, "本项目输出目录"),
    (PROJ, "项目根"),
    (PROJ.parent, "普通目录（项目上级）"),
    # 不要写死某台机器的路径：别人机器上不存在，用例就失去意义
    (Path.home() / "Documents", "普通用户目录"),
    (PROJ / "dist" / "output", "打包版输出目录"),
]:
    check(f"判为安全: {label}", cache.is_dangerous_root(p) is False, str(p))

print("\n[误配成盘根时必须拒绝清理]")
r = subprocess.run(
    [PY, "-c",
     "import sys; sys.path.insert(0, r'%s');"
     "from src import cache;"
     "cache.clean_cache(['videos'])" % PROJ],
    cwd=str(PROJ), capture_output=True, text=True,
    env={**os.environ, "OUTPUT_DIR": "E:/"})
check("盘根下 clean_cache 抛 ValueError",
      "ValueError" in r.stderr and "盘根" in r.stderr, r.stderr[-200:])
check("错误信息给出正确做法（OUTPUT_DIR 示例）",
      "OUTPUT_DIR" in r.stderr, r.stderr[-200:])

r = subprocess.run(
    [PY, "-c",
     "import sys, json; sys.path.insert(0, r'%s');"
     "from src import cache;"
     "print(json.dumps(cache.cache_stats(), ensure_ascii=False))" % PROJ],
    cwd=str(PROJ), capture_output=True, text=True,
    env={**os.environ, "OUTPUT_DIR": "E:/"})
check("误配时 cache_stats 带 warning", '"warning"' in r.stdout and "盘根" in r.stdout,
      r.stdout[:200])

print("\n[正常配置下不受影响]")
check("正常输出目录不危险", cache.is_dangerous_root(cache.OUTPUT_DIR) is False)
stats = cache.cache_stats()
check("正常配置 warning 为 None", stats.get("warning") is None, str(stats.get("warning"))[:80])

print("\n[参数校验]")
for bad, label in [
    ([], "空列表"),
    (["nope"], "未知类别"),
    (["videos", "bogus"], "含未知类别"),
]:
    try:
        cache.clean_cache(bad)
        check(f"拒绝 {label}", False, "未抛异常")
    except ValueError:
        check(f"拒绝 {label}", True)

print("\n[删除范围：绝不越过允许的根]")
# 造一个 OUTPUT_DIR 之外的文件，确认清理不会碰到它
outside = PROJ / "_tmp_must_survive.txt"
outside.write_text("do not delete me", encoding="utf-8")
try:
    for key in ("subtitles", "videos", "uploads", "temp"):
        cache.clean_cache([key])
    check("OUTPUT_DIR 之外的文件未被删除", outside.is_file())
finally:
    outside.unlink(missing_ok=True)

print("\n[绝不删除 OUTPUT_DIR 本身]")
check("清理后 OUTPUT_DIR 仍存在", cache.OUTPUT_DIR.is_dir(), str(cache.OUTPUT_DIR))

print("\n[临时目录只认 jsub_ 前缀]")
cats = cache._categories()
temp_paths = [Path(p) for p in cats["temp"]["paths"]]
check("temp 类别只包含 jsub_* 目录",
      all(p.name.startswith("jsub_") for p in temp_paths),
      str([p.name for p in temp_paths][:5]))

print("\n[上传可选项必须与「媒体文件」定义一致]")
# 曾经的 bug：cache.MEDIA_EXTS 有 17 个扩展名，而网页文件选择框的 accept
# 只有 16 个 —— 少了 .m4v。于是「缓存清理」认得 .m4v，用户却在选择框里
# 挑不到它（选择框会把 .m4v 过滤掉）。
# 两处列表各自维护就会漂移，这里断言它们必须一致。
import re as _re  # noqa: E402

_html = (PROJ / "web" / "templates" / "index.html").read_text(encoding="utf-8")
_m = _re.search(r'accept="([^"]+)"', _html)
_front = {e.strip().lower() for e in _m.group(1).split(",") if e.strip()} if _m else set()
_back = {e.lower() for e in cache.MEDIA_EXTS}

check("网页 accept 列表存在", bool(_front), str(_m)[:60] if _m else "未找到 accept")
check("accept 与 MEDIA_EXTS 完全一致",
      _front == _back,
      f"仅后端有 {sorted(_back - _front)}; 仅前端有 {sorted(_front - _back)}")
check("accept 含 .m4v（曾漏掉）", ".m4v" in _front, str(sorted(_front)))

print(f"\n{'=' * 46}\n  {PASS} passed, {FAIL} failed\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
