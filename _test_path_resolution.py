"""Regression test for OUTPUT_DIR / MODEL_DIR path resolution.

Guards against the bug where a relative OUTPUT_DIR in .env was resolved
against the process CWD, so "open output folder" opened the wrong directory
(or failed) depending on how the program was launched.

Run:  .venv/Scripts/python.exe _test_path_resolution.py
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


SNIPPET = (
    "import sys; sys.path.insert(0, r'{}'); "
    "from src.config import OUTPUT_DIR, MODEL_DIR; "
    "print(str(OUTPUT_DIR)); print(str(MODEL_DIR))"
).format(PROJ)

print("\n[CWD 无关性] —— 这是原 bug 的核心")
cwd_results = {}
for cwd in [PROJ, PROJ / "dist", Path("C:/Windows"), Path("E:/")]:
    if not cwd.exists():
        continue
    r = subprocess.run([PY, "-c", SNIPPET], cwd=str(cwd),
                       capture_output=True, text=True)
    if r.returncode != 0:
        check(f"launch from {cwd}", False, r.stderr.strip()[:200])
        continue
    lines = r.stdout.strip().splitlines()
    out, mdl = Path(lines[0]), Path(lines[1])
    cwd_results[str(cwd)] = (out, mdl)
    check(f"从 {cwd} 启动 -> 解析稳定", out.is_absolute() and mdl.is_absolute())

vals = set(cwd_results.values())
check("不同 CWD 得到完全相同的路径", len(vals) == 1,
      f"got {len(vals)} distinct: {vals}")

print("\n[锚定位置正确]")
if cwd_results:
    out, mdl = next(iter(cwd_results.values()))
    check("OUTPUT_DIR 在项目根目录下", out.parent == PROJ or str(out).startswith(str(PROJ)),
          str(out))
    check("MODEL_DIR 在项目根目录下", str(mdl).startswith(str(PROJ)), str(mdl))
    check("OUTPUT_DIR 不以 C 盘为根", not str(out).upper().startswith("C:"), str(out))
    check("MODEL_DIR 不以 C 盘为根", not str(mdl).upper().startswith("C:"), str(mdl))

print("\n[目录真实存在]")
check("项目 output/ 存在且是目录", (PROJ / "output").is_dir())
check("项目 models/ 存在且是目录", (PROJ / "models").is_dir())

print("\n[helper 行为]")
sys.path.insert(0, str(PROJ))
from src.config import _anchor_dir, _resolve_dir

check("_anchor_dir 在源码模式下 = 项目根",
      _anchor_dir().resolve() == PROJ, str(_anchor_dir()))
check("空值 -> 锚定目录/默认名",
      _resolve_dir("", "output") == (PROJ / "output").resolve())
check("相对路径 -> 锚定目录/相对路径",
      _resolve_dir("../output", "x") == (PROJ.parent / "output").resolve(),
      str(_resolve_dir("../output", "x")))
abs_target = "E:/tmp/whatever"
check("绝对路径保持原样",
      _resolve_dir(abs_target, "x") == Path(abs_target).resolve(),
      str(_resolve_dir(abs_target, "x")))
check("空白值按空处理",
      _resolve_dir("   ", "output") == (PROJ / "output").resolve())

print("\n[.env 不含裸相对路径]")
for envf in [".env", ".env.example", "dist/.env"]:
    p = PROJ / envf
    if not p.exists():
        continue
    txt = p.read_text(encoding="utf-8", errors="replace")
    bad = [ln for ln in txt.splitlines()
           if ln.strip().startswith("OUTPUT_DIR=") and ln.strip() != "OUTPUT_DIR="]
    check(f"{envf} 的 OUTPUT_DIR 未使用裸相对路径", not bad, str(bad))

print("\n[MODEL_DIR 必须指向真实存在的模型目录] —— 这是静默故障的直接守卫")
# 项目被移动/改名后，.env 里写死的 MODEL_DIR 会指向不存在的旧路径。
# 症状：程序照常启动，直到识别阶段才发现没模型 -> 转去联网下载 -> ConnectTimeout。
# 实测踩过一次（E:\大学 -> E:\Study），3.5 GB 模型被无视。
for envf in [".env", "dist/.env"]:
    p = PROJ / envf
    if not p.exists():
        continue
    mdl = ""
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.strip().startswith("MODEL_DIR="):
            mdl = ln.split("=", 1)[1].strip()
    if not mdl:
        check(f"{envf} 的 MODEL_DIR 留空（自动锚定，安全）", True)
        continue
    mp = Path(mdl)
    check(f"{envf} 的 MODEL_DIR 目录存在", mp.is_dir(), f"不存在: {mdl}")
    check(f"{envf} 的 MODEL_DIR 下确有模型",
          bool(list(mp.glob("models--*--faster-whisper-*"))), f"空目录: {mdl}")

print("\n[diagnose_model_dir 行为]")
from src.transcriber import diagnose_model_dir, _local_model_sizes

ok, msg = diagnose_model_dir()
check("当前配置下自检通过", ok, msg)
cached = _local_model_sizes()
check("本地已缓存 medium", "medium" in cached, str(cached))
check("本地已缓存 large-v3-turbo", "large-v3-turbo" in cached, str(cached))

# 关键：把 MODEL_DIR 指向一个不存在的目录，自检必须失败
# 并且要能指出模型到底在哪（否则用户仍然不知道该怎么改）
SNIPPET_DIAG = (
    "import sys; sys.path.insert(0, r'{}'); "
    "from src.transcriber import diagnose_model_dir; "
    "ok, msg = diagnose_model_dir(); "
    "print('OK' if ok else 'BAD'); print(msg)"
).format(PROJ)
r = subprocess.run([PY, "-c", SNIPPET_DIAG], cwd=str(PROJ), capture_output=True,
                   text=True, env={**os.environ,
                                   "MODEL_DIR": "E:/不存在的路径/models"})
lines = r.stdout.strip().splitlines()
check("路径错误时自检失败", lines and lines[0] == "BAD", r.stdout[:150])
check("失败时指出正确路径（含 models）",
      any(str(PROJ / "models") in ln for ln in lines), r.stdout[:300])

print("\n[视频最终只应存在于 OUTPUT_DIR 一份]")
# 背景：URL 下载原本先落到 OUTPUT_DIR/videos，再由 app 复制一份到 OUTPUT_DIR ——
# 每个视频存两份，且 videos/ 那份不在「缓存清理」扫描范围内（静默占空间）。
# 修法是让下载直接落在 OUTPUT_DIR。
# 同时发现一个潜在崩溃：复制前的判断写的是 `Path != str`（恒为 True），
# 一旦源与目标同文件就会抛 SameFileError —— 改直落后必然会踩到。
import shutil as _sh  # noqa: E402
import app as _app  # noqa: E402

OUT = _app.OUTPUT_DIR

# 1) 已在 OUTPUT_DIR 的文件 —— 必须原地不动，不能报 SameFileError
inside = OUT / "_tmp_already_here.mp4"
inside.write_bytes(b"x" * 32)
try:
    got = _app._ensure_video_in_output(inside)
    check("已位于 OUTPUT_DIR 时不报错", Path(got).resolve() == inside.resolve(), got)
    check("已位于 OUTPUT_DIR 时内容未变", inside.read_bytes() == b"x" * 32)
finally:
    inside.unlink(missing_ok=True)

# 2) 位于别处的文件 —— 应复制进 OUTPUT_DIR，且原文件保留
outside = PROJ / "_tmp_elsewhere.mp4"
outside.write_bytes(b"y" * 48)
copied = OUT / outside.name
try:
    got = _app._ensure_video_in_output(outside)
    check("外部文件被复制进 OUTPUT_DIR",
          Path(got).resolve() == copied.resolve() and copied.is_file(), got)
    check("原文件仍保留（是复制不是移动）", outside.is_file())
finally:
    outside.unlink(missing_ok=True)
    copied.unlink(missing_ok=True)

# 3) 源码里不应再有「下载到 videos 子目录」的写法
_pipe = (PROJ / "src" / "pipeline.py").read_text(encoding="utf-8")
check("pipeline 不再把视频下到 OUTPUT_DIR/videos",
      'OUTPUT_DIR / "videos"' not in _pipe)

print("\n[下载端点：中文名 / 穿越防护 / MIME / 404 形态]")
# 这个端点是整条链路的终点（用户拿到的就是它），且直接吃用户可控的 URL 段。
sys.path.insert(0, str(PROJ))
import app as _app  # noqa: E402
from src.config import OUTPUT_DIR as _OUT  # noqa: E402
from urllib.parse import quote as _q  # noqa: E402

_OUT.mkdir(parents=True, exist_ok=True)
_zh = "测试视频字幕_双语.ass"
_f = _OUT / _zh
_f.write_text("[Script Info]\n", encoding="utf-8")
_c = _app.app.test_client()

_r = _c.get("/api/download/" + _zh)
check("中文文件名可下载", _r.status_code == 200, f"HTTP {_r.status_code}")
_r_q = _c.get("/api/download/" + _q(_zh))
check("URL 编码后的中文名也可下载", _r_q.status_code == 200)
check("附件头带正确文件名（RFC 5987）",
      "filename*=UTF-8''" in _r.headers.get("Content-Disposition", ""),
      _r.headers.get("Content-Disposition", "")[:80])

# Python 的 mimetypes 把 .ass 猜成 audio/aac —— 字幕被当音频，语义错且可能被安全软件误判
import mimetypes as _mt  # noqa: E402
check("（已知）Python 默认把 .ass 猜成 audio/aac",
      _mt.guess_type("x.ass")[0] == "audio/aac", str(_mt.guess_type("x.ass")))
check("端点已显式覆盖为 octet-stream",
      _r.headers.get("Content-Type", "").startswith("application/octet-stream"),
      _r.headers.get("Content-Type", ""))

# 穿越必须挡住
for _evil in ["../../etc/passwd", "..%2F..%2Fapp.py"]:
    _rr = _c.get("/api/download/" + _evil)
    check(f"穿越被挡: {_evil}",
          _rr.status_code == 404 and "application/json" in _rr.headers.get("Content-Type", ""),
          f"HTTP {_rr.status_code} {_rr.headers.get('Content-Type','')}")

# R4：/api/* 必须回 JSON，不能回纯文本
_r404 = _c.get("/api/download/definitely-missing.ass")
check("404 回 JSON 而非纯文本",
      "application/json" in _r404.headers.get("Content-Type", "")
      and "error" in _r404.get_json(),
      _r404.get_data(as_text=True)[:70])

# send_file 会打开探针文件；不读响应体的话句柄一直挂着，
# Windows 拒绝删除被占用的文件 -> unlink 抛 OSError。
# 这是测试写法问题，不是被测代码的问题：先 close 再删。
for _resp in (_r, _r_q):
    try:
        _resp.close()
    except Exception:
        pass

try:
    _f.unlink()
    check("测试探针文件已清理（不留垃圾）", not _f.exists())
except OSError as _e:
    print(f"  （跳过清理：{type(_e).__name__} -> {_e}）")
    check("清理失败不应影响结论", True)

print("\n[ffmpeg 错误信息：必须剔掉版本横幅]")
# 实测：给 ffmpeg 喂一个非视频文件，它先打 10 行版本横幅
# （ffmpeg version / configuration / libav* 版本号），真正的原因在最后几行，
# 整段约 2500 字符。直接抛给界面 = 一堵噪音墙。
sys.path.insert(0, str(PROJ))
from src.audio import _condense_ffmpeg_stderr  # noqa: E402

_banner = [
    "ffmpeg version 9.0-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers",
    "  built with gcc 16.1.0 (Rev2, Built by MSYS2 project)",
    "  configuration: --enable-gpl --enable-version3 --enable-static",
    "  libavutil      61.  1.100 / 61.  1.100",
    "  libavcodec     63.  1.100 / 63.  1.100",
    "  libavformat    63.  1.100 / 63.  1.100",
]
_real = [
    "[in#0 @ 000001f6] Error opening input: Invalid data found when processing input",
    "Error opening input file x.txt.",
]
_cond = _condense_ffmpeg_stderr(_banner + _real)
check("横幅被剔除", "ffmpeg version" not in _cond and "libavutil" not in _cond, _cond[:90])
check("真实原因被保留", "Invalid data found" in _cond, _cond[:120])
check("结果足够短（<400 字符）", len(_cond) < 400, f"{len(_cond)} 字符")
check("全是横幅时也有兜底（不返回空）",
      bool(_condense_ffmpeg_stderr(_banner).strip()), repr(_condense_ffmpeg_stderr(_banner)[:60]))
check("空输入不抛错", _condense_ffmpeg_stderr([]) == "", repr(_condense_ffmpeg_stderr([])))

print(f"\n{'=' * 46}\n  {PASS} passed, {FAIL} failed\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
