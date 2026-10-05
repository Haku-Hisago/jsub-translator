"""Regression tests for the URL-download module (yt-dlp) and its cookie support.

Background
----------
"Download by link" silently stopped working for YouTube. Root cause was NOT a
packaging problem (yt-dlp and all 972 extractors ARE bundled) and NOT an
outdated yt-dlp (it was already the latest release). YouTube started requiring
authentication for anonymous downloads, so the request came back as:

    ERROR: [youtube] <id>: Sign in to confirm you're not a bot
    ERROR: [youtube] <id>: No video formats found!

Bilibili and Niconico were unaffected — which is the tell that this is a
per-site auth issue, not a broken module.

The fix adds optional cookie support (cookies.txt file, or reading a browser's
cookie store) and turns the raw yt-dlp error into actionable guidance. Both are
strictly additive: with nothing configured, `_cookie_opts()` returns `{}` and
behaviour is byte-for-byte what it was before.

These tests are offline — no network calls.

Run:  .venv/Scripts/python.exe _test_downloader_cookies.py
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


def run_snippet(body: str, env_extra: dict | None = None):
    """Run a snippet in a fresh interpreter (config is read at import time)."""
    env = {**os.environ}
    for k in ("YTDLP_COOKIES_FILE", "YTDLP_COOKIES_FROM_BROWSER", "YTDLP_RETRIES"):
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
    code = f"import sys; sys.path.insert(0, r'{PROJ}'); " + body
    return subprocess.run([PY, "-c", code], cwd=str(PROJ), capture_output=True,
                          text=True, env=env)


sys.path.insert(0, str(PROJ))
from src import downloader as D  # noqa: E402
from src import config  # noqa: E402

print("\n[默认行为：未配置 cookies 时与改动前一致]")
r = run_snippet("from src import downloader as d; print(d._cookie_opts())")
check("未配置 -> _cookie_opts() 返回空 dict", r.stdout.strip() == "{}",
      f"stdout={r.stdout.strip()!r} stderr={r.stderr.strip()[:150]}")
check("未配置时不抛异常", r.returncode == 0, r.stderr.strip()[:200])

print("\n[方式 1：cookies.txt 文件]")
ck = PROJ / "_tmp_test_cookies.txt"
ck.write_text("# Netscape HTTP Cookie File\n"
              ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tdummy_value\n", encoding="utf-8")
try:
    r = run_snippet(
        "from src import downloader as d; print(d._cookie_opts().get('cookiefile',''))",
        {"YTDLP_COOKIES_FILE": str(ck)})
    check("配置 cookiefile 后被采用", r.stdout.strip() == str(ck), r.stdout.strip()[:200])

    # 相对路径必须锚定到项目目录，而不是进程 CWD
    r = run_snippet(
        "from src import config; print(config.YTDLP_COOKIES_FILE)",
        {"YTDLP_COOKIES_FILE": ck.name})
    check("相对路径锚定到项目根（不跟 CWD 走）",
          r.stdout.strip() == str(ck), r.stdout.strip()[:200])

    # 让 yt-dlp 真的读一遍，确认格式被接受
    r = run_snippet(
        "from yt_dlp import YoutubeDL; from src import downloader as d; "
        "y = YoutubeDL(d._cookie_opts()); "
        "print([(c.name, c.value) for c in y.cookiejar])",
        {"YTDLP_COOKIES_FILE": str(ck)})
    check("yt-dlp 能解析该 cookie 文件", "SID" in r.stdout, r.stdout.strip()[:200])

    print("\n[文件不存在时必须明确报错，不能静默忽略]")
    r = run_snippet(
        "from src import downloader as d\n"
        "try:\n"
        "    d._cookie_opts(); print('NO_RAISE')\n"
        "except FileNotFoundError as e:\n"
        "    print('RAISED', 'YTDLP_COOKIES_FILE' in str(e))",
        {"YTDLP_COOKIES_FILE": str(PROJ / "_definitely_missing.txt")})
    check("缺失的 cookies 文件会报错", r.stdout.strip().startswith("RAISED True"),
          r.stdout.strip()[:200])
finally:
    ck.unlink(missing_ok=True)

print("\n[方式 2：从浏览器读取]")
r = run_snippet(
    "from src import downloader as d; print(d._cookie_opts())",
    {"YTDLP_COOKIES_FROM_BROWSER": "edge"})
check("cookiesfrombrowser 被正确构造",
      "cookiesfrombrowser" in r.stdout and "edge" in r.stdout, r.stdout.strip()[:200])

r = run_snippet(
    "from src import downloader as d; print(d._cookie_opts())",
    {"YTDLP_COOKIES_FILE": "x.txt", "YTDLP_COOKIES_FROM_BROWSER": "edge"})
check("cookiefile 优先于 from_browser（文件缺失时报错而非回退）",
      "FileNotFoundError" in r.stderr, r.stderr.strip()[-200:])

print("\n[认证类错误的识别与提示]")
from yt_dlp.utils import DownloadError  # noqa: E402

AUTH = [
    "ERROR: [youtube] x: Sign in to confirm you're not a bot.",
    "ERROR: [youtube] x: No video formats found!",
    "ERROR: [youtube] x: This video is available to this channel's members",
    "ERROR: Login required",
]
NON_AUTH = [
    "ERROR: Unable to download webpage: timed out",
    "ERROR: Video unavailable",
    "ERROR: HTTP Error 404: Not Found",
]
for msg in AUTH:
    check(f"识别为认证问题: {msg[:44]}", D._looks_like_auth_error(DownloadError(msg)))
for msg in NON_AUTH:
    check(f"不误判: {msg[:44]}", not D._looks_like_auth_error(DownloadError(msg)))

print("\n[提示文案可操作]")
h = D._auth_help("https://www.youtube.com/watch?v=abc", configured=False)
check("提示里含站点名", "www.youtube.com" in h)
check("提示里给出 YTDLP_COOKIES_FILE 配置名", "YTDLP_COOKIES_FILE" in h)
check("提示里给出 YTDLP_COOKIES_FROM_BROWSER 配置名", "YTDLP_COOKIES_FROM_BROWSER" in h)
check("提示提醒需要重启", "重启" in h)
h2 = D._auth_help("https://www.youtube.com/watch?v=abc", configured=True)
check("已配置但仍失败 -> 提示 cookies 可能过期", "过期" in h2)

print("\n[cookie_status() 自检 —— 供 /api/health 用]")
r = run_snippet("import json; from src import downloader as d; "
                "print(json.dumps(d.cookie_status(), ensure_ascii=False))")
check("未配置时 configured=False", '"configured": false' in r.stdout, r.stdout[:200])
check("未配置时给出提示", "YouTube" in r.stdout, r.stdout[:250])

ck2 = PROJ / "_tmp_status_cookies.txt"
ck2.write_text("# Netscape HTTP Cookie File\n"
               ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\ta\n"
               ".youtube.com\tTRUE\t/\tTRUE\t0\tHSID\tb\n"
               ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tc\n", encoding="utf-8")
try:
    r = run_snippet("import json; from src import downloader as d; "
                    "print(json.dumps(d.cookie_status(), ensure_ascii=False))",
                    {"YTDLP_COOKIES_FILE": str(ck2)})
    check("配置文件存在时 exists=True", '"exists": true' in r.stdout, r.stdout[:250])
    check("能数出 cookie 条数（3 条）", '"cookie_count": 3' in r.stdout, r.stdout[:250])
    check("提示 cookies 可能过期", "过期" in r.stdout, r.stdout[:300])

    r = run_snippet("import json; from src import downloader as d; "
                    "print(json.dumps(d.cookie_status(), ensure_ascii=False))",
                    {"YTDLP_COOKIES_FILE": str(PROJ / "_missing_ck.txt")})
    check("文件缺失时 exists=False", '"exists": false' in r.stdout, r.stdout[:250])
    check("文件缺失时给出可读提示", "不存在" in r.stdout, r.stdout[:300])

    r = run_snippet("import json; from src import downloader as d; "
                    "print(json.dumps(d.cookie_status(), ensure_ascii=False))",
                    {"YTDLP_COOKIES_FROM_BROWSER": "edge"})
    check("浏览器方式 source=browser", '"source": "browser"' in r.stdout, r.stdout[:250])
    check("浏览器方式提醒可能失败", "App-Bound" in r.stdout or "占用" in r.stdout,
          r.stdout[:300])
finally:
    ck2.unlink(missing_ok=True)

print("\n[ffmpeg 查找：下载与音频提取必须看到同一个 ffmpeg]")
# 曾经的 bug：audio 与 downloader 各维护一份 ffmpeg 搜索顺序，
# downloader 少搜了 Program Files (x86) / Chocolatey / Scoop 三处。
# 于是用 Chocolatey 或 Scoop 装的 ffmpeg 会出现
# 「能提取音频，但 yt-dlp 合并不了」—— 两条路径看到的不是同一个 ffmpeg。
from src.audio import find_ffmpeg, _WIN_FFMPEG_DIRS  # noqa: E402

try:
    _exe = find_ffmpeg()
    _dir = D._find_ffmpeg_path()
    check("audio 找到 ffmpeg", bool(_exe), _exe)
    check("downloader 返回非空", bool(_dir), repr(_dir))
    check("两者指向同一个目录（同源）",
          Path(_exe).parent == Path(_dir), f"{Path(_exe).parent} vs {_dir}")
except RuntimeError:
    # 本机没装 ffmpeg：此时 downloader 应返回空串交由 yt-dlp 处理，而不是抛错
    check("找不到 ffmpeg 时 downloader 返回空串（不抛错）",
          D._find_ffmpeg_path() == "", repr(D._find_ffmpeg_path()))

check("audio 的候选目录覆盖常见安装方式（≥5 处）",
      len(_WIN_FFMPEG_DIRS) >= 5, str(len(_WIN_FFMPEG_DIRS)))
check("downloader 不再自带一份搜索顺序（改为复用 audio）",
      "shutil.which" not in (PROJ / "src" / "downloader.py").read_text(encoding="utf-8"))

print("\n[原有功能未受影响]")
check("is_url 仍能识别 http(s) 链接", D.is_url("https://example.com/a"))
check("is_url 不把普通路径当链接", not D.is_url(r"E:\videos\a.mp4"))

missing = PROJ / "_tmp_no_such_video.mp4"
try:
    D.resolve_video_input(str(missing))
    check("本地文件缺失时应报错", False)
except FileNotFoundError:
    check("本地文件缺失时仍抛 FileNotFoundError", True)

local = PROJ / "_tmp_local_video.mp4"
local.write_bytes(b"\x00" * 16)
try:
    got = D.resolve_video_input(str(local))
    check("本地文件路径原样返回（不走下载）", Path(got) == local, str(got))
finally:
    local.unlink(missing_ok=True)

print("\n[.env 三个文件都声明了新配置项]")
for name in (".env", ".env.example", "dist/.env"):
    p = PROJ / name
    if not p.exists():
        continue
    txt = p.read_text(encoding="utf-8", errors="replace")
    check(f"{name} 含 YTDLP_COOKIES_FILE", "YTDLP_COOKIES_FILE" in txt)
    check(f"{name} 含 YTDLP_COOKIES_FROM_BROWSER", "YTDLP_COOKIES_FROM_BROWSER" in txt)

print(f"\n{'=' * 46}\n  {PASS} passed, {FAIL} failed\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
