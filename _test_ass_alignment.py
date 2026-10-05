"""Regression test for ASS style alignment (字幕垂直位置).

Guards the bug where per-speaker styles (S00/S01/...) were hardcoded to
Alignment 2 (bottom), so as soon as speaker diarization produced a speaker
style every subtitle jumped from the top of the frame to the bottom —
overriding the user's Alignment 8 (top-center) setting.

Run:  .venv/Scripts/python.exe _test_ass_alignment.py
"""
import re
import sys
from pathlib import Path

from src.ass_writer import (
    generate_ass,
    _speaker_style,
    STYLE_JAPANESE,
    STYLE_DEFAULT,
    STYLE_CHINESE,
    SPEAKER_ALIGNMENT,
)

PASS = FAIL = 0
TMP = Path("_tmp_align_test.ass")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def _raises(fn, exc=Exception):
    """返回值是否为「抛出了 exc」——用于断言非法输入不会静默返回错结果。"""
    try:
        fn()
    except exc:
        return True
    return False


def styles_of(text):
    """{style_name: {align, marginV, marginL, marginR, font, size}}"""
    out = {}
    for line in text.splitlines():
        if not line.startswith("Style:"):
            continue
        a = line.split(",")
        out[a[0].split(": ", 1)[1]] = {
            "align": a[18].strip(),
            "marginV": a[21].strip(),
            "marginL": a[19].strip(),
            "marginR": a[20].strip(),
            "font": a[1],
            "size": a[2],
        }
    return out


def dialogues_of(text):
    return [l.split(",")[3] for l in text.splitlines() if l.startswith("Dialogue:")]


def render(segs, mode):
    generate_ass(segs, TMP, title="t", mode=mode)
    try:
        return TMP.read_text(encoding="utf-8-sig")
    finally:
        if TMP.exists():
            TMP.unlink()


WITH_SPK = [
    {"start": 0.0, "end": 2.0, "text": "おはよう", "translated": "早上好",
     "speaker": "SPEAKER_00"},
    {"start": 2.0, "end": 4.0, "text": "はい", "translated": "是",
     "speaker": "SPEAKER_01"},
]


print("\n[常量]")
check(f"SPEAKER_ALIGNMENT == 8 (顶部居中)", SPEAKER_ALIGNMENT == 8,
      str(SPEAKER_ALIGNMENT))
check("日文样式本来就是 8", STYLE_JAPANESE.split(",")[18] == "8")
check("默认样式是 2（底部，不参与说话人）", STYLE_DEFAULT.split(",")[18] == "2")
check("中文样式是 2", STYLE_CHINESE.split(",")[18] == "2")

print("\n[说话人样式定位 —— 原 bug 核心]")
st = _speaker_style("S00", "&H00FFFFFF").split(",")
check("说话人样式 align=8", st[18] == "8", st[18])
check("说话人样式 marginV 与日文一致(=20)",
      st[21] == STYLE_JAPANESE.split(",")[21],
      f"{st[21]} vs {STYLE_JAPANESE.split(',')[21]}")
check("说话人样式 marginL/R 与日文一致",
      st[19] == STYLE_JAPANESE.split(",")[19] and st[20] == STYLE_JAPANESE.split(",")[20])
check("说话人样式 outline/shadow 与日文一致",
      st[16] == STYLE_JAPANESE.split(",")[16] and
      st[17] == STYLE_JAPANESE.split(",")[17])
check("共 23 个字段", len(st) == 23, f"got {len(st)}")

print("\n[bilingual + 说话人]")
txt = render(WITH_SPK, "bilingual")
ss = styles_of(txt)
check("生成了 S00", "S00" in ss)
check("S00 align=8", ss.get("S00", {}).get("align") == "8", str(ss.get("S00")))
check("S01 align=8", ss.get("S01", {}).get("align") == "8", str(ss.get("S01")))
check("所有 Dialogue 用的 style 都是 8",
      all(ss.get(s, {}).get("align") == "8" for s in set(dialogues_of(txt))),
      str(set(dialogues_of(txt))))

print("\n[bilingual 分色版]")
generate_ass(WITH_SPK, TMP, title="t", mode="bilingual", speaker_colors=True)
txt = TMP.read_text(encoding="utf-8-sig")
TMP.unlink()
ss = styles_of(txt)
check("分色版 S00 align 仍为 8", ss.get("S00", {}).get("align") == "8")
check("分色版颜色不是纯白", ss.get("S00", {}) != {},
      "S00 missing")

print("\n[japanese 模式]")
txt = render([s for s in WITH_SPK], "japanese")
ss = styles_of(txt)
check("S00 align=8", ss.get("S00", {}).get("align") == "8")
check("Japanese align=8", ss.get("Japanese", {}).get("align") == "8")
check("日文行都用说话人样式（非 Japanese/Default）",
      set(dialogues_of(txt)) == {"S00", "S01"}, str(set(dialogues_of(txt))))

print("\n[bilingual_split 模式]")
txt = render(WITH_SPK, "bilingual_split")
ss = styles_of(txt)
check("S00 align=8", ss.get("S00", {}).get("align") == "8")
check("Japanese align=8", ss.get("Japanese", {}).get("align") == "8")
check("Chinese 仍是 2（中文在下方）", ss.get("Chinese", {}).get("align") == "2",
      str(ss.get("Chinese")))

print("\n[无说话人 —— 不应受影响]")
txt = render([{"start": 0.0, "end": 2.0, "text": "テスト",
               "translated": "测试"}], "bilingual")
ss = styles_of(txt)
check("不生成 S00", "S00" not in ss, str(list(ss)))
check("Dialogue 用 Default", set(dialogues_of(txt)) == {"Default"},
      str(set(dialogues_of(txt))))
check("Default align=2 保持不变", ss.get("Default", {}).get("align") == "2")

print("\n[字段结构完整]")
txt = render(WITH_SPK, "bilingual")
for line in txt.splitlines():
    if line.startswith("Style:"):
        check(f"Style 行 23 字段: {line.split(',')[0][7:]}",
              len(line.split(",")) == 23, f"got {len(line.split(','))}")
        break

print("\n[说话人颜色：网页预览必须与生成的 ASS 文件同源]")
# 曾经的 bug：前端自己算颜色（djb2 哈希 + 另一套调色板），后端用 md5 +
# 另一套调色板 —— 同一个说话人在预览里和在字幕文件里颜色不同。
# 更糟的是前端调色板把**纯白**排在首位，0 号说话人在预览里是白色，
# 正是 ass_writer 特意避开的「分到白色就看不出区别」。
from src.ass_writer import (  # noqa: E402
    SPEAKER_COLORS, SPEAKER_CSS_COLORS, get_speaker_css_color,
)

check("CSS 调色板不含纯白（否则 0 号说话人看不出颜色）",
      all(c.lower() != "#ffffff" for c in SPEAKER_CSS_COLORS),
      str(SPEAKER_CSS_COLORS))
check("ASS 调色板不含纯白",
      all(c.upper() != "&H00FFFFFF" for c in SPEAKER_COLORS), str(SPEAKER_COLORS))
check("CSS 调色板与 ASS 调色板数量一致（下标对齐）",
      len(SPEAKER_CSS_COLORS) == len(SPEAKER_COLORS),
      f"{len(SPEAKER_CSS_COLORS)} vs {len(SPEAKER_COLORS)}")

# 数量一致**不等于**颜色一致 —— 之前两份手写表就是数量相同、8 项里 5 项对不上。
# 真正的断言是：下标 i 的 CSS 色必须正好是下标 i 的 ASS 色反解出来的结果。
from src.ass_writer import abgr_to_css, get_speaker_color  # noqa: E402
_bad = [(i, a, c, abgr_to_css(a)) for i, (a, c)
        in enumerate(zip(SPEAKER_COLORS, SPEAKER_CSS_COLORS)) if abgr_to_css(a) != c]
check("每个下标上 CSS 色 == ASS 色反解（预览色必须等于字幕实际颜色）",
      not _bad, str(_bad[:3]))

# ASS 字节序是 AABBGGRR，手写极易漏掉换位（第 8 项就漏了，渲染成淡蓝而非土黄）。
# 逐个反解后必须仍是「能叫得出名字」的色，且不能有重复色。
check("反解后无重复色（8 个说话人必须能互相区分）",
      len(set(SPEAKER_CSS_COLORS)) == len(SPEAKER_CSS_COLORS), str(SPEAKER_CSS_COLORS))
check("ASS 值均为 8 位 &H00 开头（ABGR）",
      all(len(c) == 10 and c.startswith("&H00") for c in SPEAKER_COLORS),
      str([c for c in SPEAKER_COLORS if not (len(c) == 10 and c.startswith("&H00"))]))
check("abgr_to_css 对非法输入抛错（不静默返回错色）",
      _raises(lambda: abgr_to_css("&HFFFF")), "短值未抛错")

# 两个 getter 必须落在同一格 —— 之前各自算 md5 % len(自己的表)，
# 一旦两张表长度不同就会错位（且当时都已漂移）。
_lbl = ["SPEAKER_00", "SPEAKER_01", "SPEAKER_07", "说话人A", ""]
_mis = [(l, get_speaker_color(l), get_speaker_css_color(l)) for l in _lbl if l
        and abgr_to_css(get_speaker_color(l)) != get_speaker_css_color(l)]
check("get_speaker_color 与 get_speaker_css_color 必须落在同一格",
      not _mis, str(_mis[:3]))

# 收口：CSS 那份必须是推导出来的，不能再有人手写一份（否则还会漂）
_aw = (Path(__file__).resolve().parent / "src" / "ass_writer.py").read_text(encoding="utf-8")
check("SPEAKER_CSS_COLORS 由 SPEAKER_COLORS 推导（不再手写第二份）",
      "SPEAKER_CSS_COLORS = [abgr_to_css(" in _aw, "仍存在手写副本")

# 后端会产出哪些格式，前端就得有对应的下载按钮 —— 这份映射在两边各写了一份，
# 加了新格式却忘了加按钮时，用户是**看不到**那个格式的（按钮直接不显示）。
from src.ass_writer import generate_all_formats  # noqa: E402
_tmp = Path(__file__).resolve().parent / "output" / "_probe_fmt"
_tmp.mkdir(parents=True, exist_ok=True)
try:
    _res = generate_all_formats(
        [{"start": 0.0, "end": 1.0, "text": "あ", "translated": "啊", "speaker": "SPEAKER_00"}],
        _tmp, title="t")
    _keys = set(_res.keys())
finally:
    for _p in _tmp.glob("*"):
        try:
            _p.unlink()
        except OSError:
            pass
    try:
        _tmp.rmdir()
    except OSError:
        pass

_dl_html = (Path(__file__).resolve().parent / "web" / "templates"
            / "index.html").read_text(encoding="utf-8")
_m = re.search(r"const map = \{(.*?)\};", _dl_html, re.S)
_fe = set(re.findall(r":\s*'([a-z_]+)'", _m.group(1)))
check("后端产出的每个格式，前端都有下载按钮",
      _keys and _keys <= _fe, f"后端 {sorted(_keys)} / 前端 {sorted(_fe)}")
check("前端的每个下载按钮，后端都真的产出（否则点了没反应）",
      _fe <= _keys, f"前端 {sorted(_fe)} / 后端 {sorted(_keys)}")

# 前端不应再自带调色板/哈希
_html = (Path(__file__).resolve().parent / "web" / "templates" / "index.html").read_text(
    encoding="utf-8")
check("前端不再自带调色板常量", "SP_COLORS" not in _html)
check("前端改为使用服务端下发的 color", "seg.color" in _html or "s.color" in _html)

# 接口必须下发 color，且与 ass_writer 的算法一致
import json as _json  # noqa: E402
import app as _app  # noqa: E402

with _app._lock:
    _app._jobs["_t_color"] = {
        "queue": None, "status": "done",
        "result": {"title": "T", "video_path": "", "files": {}, "speakers": set(),
                   "segments": [
                       {"start": 0.0, "end": 1.0, "text": "あ", "translated": "啊",
                        "speaker": "SPEAKER_00"},
                       {"start": 1.0, "end": 2.0, "text": "い", "translated": "咦",
                        "speaker": "SPEAKER_01"},
                       {"start": 2.0, "end": 3.0, "text": "う", "translated": "呜",
                        "speaker": ""},
                   ]},
    }
_c = _app.app.test_client()
_d = _json.loads(_c.get("/api/result/_t_color").data)
_segs = _d["segments"]
check("接口为每段下发 color 字段", all("color" in s for s in _segs), str(_segs[0])[:120])
check("下发的颜色与 ass_writer 一致",
      all(s["color"] == get_speaker_css_color(s["speaker"]) for s in _segs),
      str([(s["speaker"], s["color"]) for s in _segs]))
check("无说话人的段落不是白色系冲突色",
      _segs[2]["color"] == get_speaker_css_color(""), _segs[2]["color"])
with _app._lock:
    _app._jobs.pop("_t_color", None)

print("\n[花括号必须转义 —— 否则字幕内容会被渲染器吃掉]")
# ASS 里 `{...}` 是 override block。字幕文本中若含半角花括号，
# 渲染器会把括号**连同里面的内容**一起当作样式块丢掉。
# 用 libass 渲染成图片逐张比对实测过：
#   `A{これは} B` 与 `A B` 渲染结果逐像素相同（内容消失）
#   `A\{これは\} B` 同样消失（反斜杠转义在 libass 下无效）
#   `A｛これは｝ B` 正常显示
from src.ass_writer import _escape_ass  # noqa: E402

check("半角 { 换成全角", _escape_ass("a{b}c") == "a\uff5bb\uff5dc", _escape_ass("a{b}c"))
check("单独的 { 也处理", _escape_ass("{x") == "\uff5bx", _escape_ass("{x"))
check("单独的 } 也处理", _escape_ass("x}") == "x\uff5d", _escape_ass("x}"))
check("换行仍转成 \\N", _escape_ass("a\nb") == "a\\Nb", _escape_ass("a\nb"))
check("无括号文本不受影响", _escape_ass("普通文本") == "普通文本")

# 生成真实文件，确认 Dialogue 行里没有裸花括号
import tempfile as _tf  # noqa: E402
from src.ass_writer import generate_ass  # noqa: E402

_br_segs = [{"start": 0.0, "end": 2.0, "text": "A{これは} B", "translated": "A{这是} B"}]
with _tf.TemporaryDirectory() as _td:
    _p = Path(_td) / "br.ass"
    generate_ass(_br_segs, _p, mode="bilingual")
    _ass = _p.read_text(encoding="utf-8")
    _dial = [l for l in _ass.splitlines() if l.startswith("Dialogue:")]
    check("生成的 ASS 有 Dialogue 行", bool(_dial), str(len(_dial)))
    if _dial:
        # Dialogue 里合法存在 override block（如淡入淡出 {\fad(300,300)}）。
        # 先把这些**成对且合法**的块剥掉，剩下的正文里不应再有任何裸花括号
        # —— 若有，说明用户文本没被转义，渲染时会被吃掉。
        import re as _re2
        _body = _dial[0].split(",", 9)[-1]
        _stripped = _re2.sub(r"\{[^{}]*\}", "", _body)
        check("剥掉合法样式块后无残留 {", "{" not in _stripped, _stripped[:80])
        check("剥掉合法样式块后无残留 }", "}" not in _stripped, _stripped[:80])
        check("内容被保留（全角括号在位）",
              "\uff5bこれは\uff5d" in _stripped or "\uff5b这是\uff5d" in _stripped,
              _stripped[:80])

print("\n[配色必须唯一一份，且跨进程稳定]")
# diarizer.py 里曾有一份配色副本，两个问题：
#   1. 首位是纯白（ass_writer 特意避开的）
#   2. 用内置 hash() 取色 —— Python 对字符串的 hash **每进程随机化**，
#      同一说话人每次运行颜色都不同，docstring 却写着 "consistent"
# 该副本无人引用，已删除。这里锁死：不得复活，且取色必须跨进程稳定。
import subprocess as _sp  # noqa: E402

_src_diar = (Path(__file__).resolve().parent / "src" / "diarizer.py").read_text(
    encoding="utf-8")
check("diarizer 不再定义配色常量", "SPEAKER_COLORS = [" not in _src_diar)
check("diarizer 不再定义取色函数", "def get_speaker_color" not in _src_diar)
check("diarizer 留下说明指向权威位置", "ass_writer" in _src_diar)

# 跨进程稳定性：不同 PYTHONHASHSEED 必须得到同一颜色
# （这正是 hash() 取色的陷阱 —— 换成 hash() 会立刻失败）
_probe = (
    "import sys; sys.path.insert(0, r'%s');"
    "from src.ass_writer import get_speaker_color;"
    "print(get_speaker_color('SPEAKER_00'), get_speaker_color('SPEAKER_01'))"
    % (Path(__file__).resolve().parent)
)
_runs = []
for _seed in ("0", "1", "12345"):
    _r = _sp.run([sys.executable, "-c", _probe], capture_output=True, text=True,
                 env={**__import__("os").environ, "PYTHONHASHSEED": _seed})
    _runs.append(_r.stdout.strip())
check("取色跨进程稳定（换 hash() 会失败）",
      len(set(_runs)) == 1, str(_runs))
check("跨进程取色非空", bool(_runs[0]), str(_runs))

print("\n[说话人缩写名同样由服务端下发]")
# 与颜色同理：`SPEAKER_00 → S00` 的转换曾在前后端各写一遍。
# 两处目前一致，但同一转换写两遍迟早有一边先改 —— 改为服务端下发。
from src.ass_writer import _short_speaker  # noqa: E402

check("SPEAKER_00 → S00", _short_speaker("SPEAKER_00") == "S00", _short_speaker("SPEAKER_00"))
check("SPEAKER_12 → S12", _short_speaker("SPEAKER_12") == "S12", _short_speaker("SPEAKER_12"))
check("非 SPEAKER_ 前缀原样保留", _short_speaker("其他") == "其他", _short_speaker("其他"))
check("空值返回空", _short_speaker("") == "", repr(_short_speaker("")))

_html2 = (Path(__file__).resolve().parent / "web" / "templates" / "index.html").read_text(
    encoding="utf-8")
check("前端不再自带 SPEAKER_ 替换", "replace('SPEAKER_'" not in _html2)
check("前端改用服务端下发的 speaker_short", "speaker_short" in _html2)

with _app._lock:
    _app._jobs["_t_short"] = {
        "queue": None, "status": "done",
        "result": {"title": "T", "video_path": "", "files": {}, "speakers": set(),
                   "segments": [
                       {"start": 0.0, "end": 1.0, "text": "あ", "translated": "啊",
                        "speaker": "SPEAKER_07"},
                       {"start": 1.0, "end": 2.0, "text": "い", "translated": "咦",
                        "speaker": ""},
                   ]},
    }
_d2 = _json.loads(_c.get("/api/result/_t_short").data)
check("接口为每段下发 speaker_short",
      all("speaker_short" in s for s in _d2["segments"]), str(_d2["segments"][0])[:120])
check("下发的缩写名与 _short_speaker 一致",
      all(s["speaker_short"] == _short_speaker(s["speaker"]) for s in _d2["segments"]),
      str([(s["speaker"], s["speaker_short"]) for s in _d2["segments"]]))
with _app._lock:
    _app._jobs.pop("_t_short", None)

print(f"\n{'=' * 46}\n  {PASS} passed, {FAIL} failed\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
