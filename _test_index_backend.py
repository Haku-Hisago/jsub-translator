"""Index-Translate backend 回归测试。

为什么要有这个文件：Index 走的是「本地 vLLM 服务」这条路，
而这台机器上没有 GPU / 没有 vLLM，**没法拿真模型验证**。
所以这里起一个**真实的 HTTP 服务**（实现 OpenAI 兼容的
/v1/models + /v1/chat/completions），让 openai SDK、prompt 组装、
校验、重试、缩批、fallback 全部走真实代码路径 —— 只有模型本身是假的。

这样能测到的：
  · instTrans prompt 结构对不对（官方格式）
  · 动态 glossary 是否只注入命中的词
  · 按 id 映射（而不是按下标）是否可靠
  · 校验能否拦住「少条 / 多余 / 空译文 / Markdown / 解释文字 / 术语未落实」
  · 校验失败会不会重试、会不会缩批
  · 过长译文会不会请求压缩（而不是截断）
  · fallback 开关是否真的默认关闭
  · 服务没起时是否给出人话错误

测不到的（已在 docs/index-translate.md 里明确标注）：
  · 真模型的翻译质量、显存占用、推理速度 —— 必须在你自己的 GPU 上跑。

Run:  .venv/Scripts/python.exe _test_index_backend.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


# ═══════════════════════════════════════════════════════════════
# 一个假的 vLLM 服务：只实现我们真正会打的两个端点
# ═══════════════════════════════════════════════════════════════

MODEL_ID = "IndexTeam/Index-Translate-2B"


class _Handler(BaseHTTPRequestHandler):
    #: 由测试在启动前设置：(request_body) -> 要返回的 content 字符串
    responder = None
    #: 记录收到的所有请求，供断言检查
    seen = []

    def log_message(self, *a):  # 静音，别把测试输出刷满
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json({"object": "list", "data": [
                {"id": MODEL_ID, "object": "model", "owned_by": "local"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        _Handler.seen.append(body)
        content = _Handler.responder(body)
        self._json({
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": body.get("model", MODEL_ID),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


def start_server(responder):
    _Handler.responder = responder
    _Handler.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/v1"


def segs(n, text="原文", speaker=None):
    out = []
    for i in range(n):
        s = {"start": float(i), "end": float(i + 1), "text": f"{text}{i}"}
        if speaker:
            s["speaker"] = speaker
        out.append(s)
    return out


# ═══════════════════════════════════════════════════════════════
print("=" * 62)
print("1. prompt 组装：必须是官方 instTrans 结构")
print("=" * 62)
from src import index_prompt as IP  # noqa: E402

batch = [{"text": "推しが尊すぎる。", "speaker": "SPEAKER_00"},
         {"text": "今日は楽しかった。", "speaker": "SPEAKER_01"}]
ids = [101, 102]
p = IP.build_instrans_prompt(batch, ids, glossary_pairs={"推し": "推", "尊い": "太尊了"})

check("开头是官方句式", p.startswith("请将以下日文字幕翻译成中文，并且严格遵循所有约束要求。"),
      p[:40])
check("含【源文】段", "【源文】" in p)
check("含【约束要求】段", "【约束要求】" in p)
check("约束项带编号", "1. 【硬性要求】" in p and "2. 【硬性要求】" in p)
check("术语用官方 A→B、C→D 形式", "推し→推、尊い→太尊了" in p, p[:400])
check("源文用 JSON 字典且 key 是全局 id", '"101"' in p and '"102"' in p)
check("speaker 随条目下发", '"speaker": "SPEAKER_00"' in p)
check("结尾是 JSON 输出指令", p.rstrip().endswith(
    '不要输出 Markdown、解释或任何额外文字。'), p[-60:])

# 上下文块
p2 = IP.build_instrans_prompt(
    batch, ids,
    context_before=[{"text": "前文", "translated": "前译", "speaker": ""}],
    context_after=[{"text": "后文"}])
check("上文块存在且标注不要翻译", "【上文（仅供理解语境，不要翻译）】" in p2)
check("下文块存在且标注不要翻译", "【下文（仅供理解语境，不要翻译）】" in p2)
check("上文带上已译内容（供一致性参考）", "前文  →  前译" in p2, p2[:500])

# 无术语时不应出现术语约束
p3 = IP.build_instrans_prompt(batch, ids)
check("无命中术语时不注入术语约束", "专名/术语对照" not in p3)

# 校对 prompt
pr = IP.build_instrans_review_prompt(
    [{"text": "原文", "translated": "初译", "speaker": "S"}], [7])
check("校对 prompt 要求条数一致", "条数必须一致" in pr)
check("校对 prompt 用 translation 输出", '"translation"' in pr)


# ═══════════════════════════════════════════════════════════════
print()
print("=" * 62)
print("2. 解析：必须认得多种模型输出形态")
print("=" * 62)
from src.translator import _parse_translation_response as parse  # noqa: E402

cases = [
    ("JSON 数组 + text", '[{"id":0,"text":"甲"}]', 1, "甲"),
    ("JSON 数组 + translation（Index 契约）",
     '[{"id":0,"translation":"甲"}]', 1, "甲"),
    ("JSON 字典 id→translation（Index 契约）",
     '{"101":{"translation":"甲"}}', 1, "甲"),
    ("JSON 字典 id→字符串", '{"101":"甲"}', 1, "甲"),
    ("带 Markdown 围栏", '```json\n[{"id":0,"text":"甲"}]\n```', 1, "甲"),
    ("一行一个对象", '{"id":0,"text":"甲"}\n{"id":1,"text":"乙"}', 2, "甲"),
    ("id 是字符串", '[{"id":"5","translation":"甲"}]', 1, "甲"),
]
for label, raw, n, first in cases:
    got = parse(raw, n)
    ok = len(got) == n and got and got[0]["text"] == first
    check(label, ok, f"得到 {got!r}")

check("纯文本（模型跑偏）→ 空列表，不假装有译文",
      parse("以下是翻译：你好", 1) == [], repr(parse("以下是翻译：你好", 1)))


# ═══════════════════════════════════════════════════════════════
print()
print("=" * 62)
print("3. 校验器：拦住不该流到字幕里的东西")
print("=" * 62)
from src.validator import validate_translations as V  # noqa: E402

S = {0: "今日は本当に楽しかった。", 1: "え、マジで？"}

check("正常译文通过", V([0, 1], {0: "今天真的很开心。", 1: "诶，真的假的？"}, S).ok)
check("少条 → fatal", not V([0, 1], {0: "今天真的很开心。"}, S).ok)
check("多条 → fatal", not V([0], {0: "今天真的很开心。", 1: "诶"}, S).ok)
check("空译文 → fatal", not V([0], {0: "   "}, S).ok)
check("Markdown → fatal", not V([0], {0: "```今天很开心```"}, S).ok)
check("粗体标记 → fatal", not V([0], {0: "**今天**很开心"}, S).ok)
check("解释性开头 → fatal", not V([0], {0: "以下是翻译：今天很开心"}, S).ok)
check("自我描述 → fatal", not V([0], {0: "作为一个AI，我无法翻译"}, S).ok)
check("英文道歉 → fatal", not V([0], {0: "I cannot translate this."}, S).ok)

r = V([0, 1], {0: "今天真的很开心。", 1: "诶，真的假的？"}, S,
      glossary_pairs={"推し": "推"})
check("未命中的术语不参与校验（不误报）", r.ok)

r = V([0], {0: "我推很棒"}, {0: "推しはすごい"},
      glossary_pairs={"推し": "推"})
check("术语落实 → 通过", r.ok)

r = V([0], {0: "我的本命很棒"}, {0: "推しメンはすごい"},
      glossary_pairs={"推しメン": "本命成员"})
check("术语未落实 → fatal（硬约束一票否决）", not r.ok, r.summary())

r = V([0], {0: "我的本命成员很棒"}, {0: "推しメンはすごい"},
      glossary_pairs={"推しメン": "本命成员"})
check("术语落实 → 通过", r.ok, r.summary())

r = V([0], {0: "我推很棒"}, {0: "推しはすごい"},
      glossary_pairs={"推し": "我推/主推"})
check("多选译法「我推/主推」任选其一都算合规", r.ok, r.summary())

# 已知局限：单字目标词用子串判断，「主推」含「推」→ 判不出违规。
# 这里**明确断言这个局限存在**，避免以后有人误以为它是可靠的。
r = V([0], {0: "我的主推很棒"}, {0: "推しはすごい"},
      glossary_pairs={"推し": "推"})
check("（已知局限）单字目标词的术语检查不可靠",
      r.ok, "如果这条开始 FAIL，说明检查变严了，请同步更新文档说明")

long_zh = "今天真的非常非常感谢大家能够来到这里观看我们的演出真的非常开心" * 2
r = V([0], {0: long_zh}, {0: "今日はありがとう"}, max_ratio=1.8)
check("过长 → 标记为待压缩（而非 fatal）", r.ok and r.too_long_ids == [0], str(r.too_long_ids))

r = V([0], {0: "今天很开心呢"}, {0: "今日は楽しかった"}, )
check("正常长度不触发压缩", r.too_long_ids == [])

r = V([0], {0: "ああ、そうですね"}, {0: "ああ、そうですね"})
check("日文残留 → 告警级（不阻断）",
      any(i.kind == "kana_residue" and not i.fatal for i in r.issues), r.summary())

r = V([0], {0: "今天\\N很开心"}, {0: "今日は楽しい"})
check("字面量 \\N → 告警（会被当成换行）",
      any(i.kind == "literal_break" for i in r.issues), r.summary())


# ═══════════════════════════════════════════════════════════════
print()
print("=" * 62)
print("4. 端到端：真的打 HTTP（假 vLLM 服务）")
print("=" * 62)

# 先把 Index 指向假服务，再导入 translator（config 在导入时读环境变量）
def fresh_translator(base_url, monkey=None):
    """在给定 base_url 下拿到一个干净的 IndexTranslator。

    ⚠️ 这里把 **所有** 后端地址都指向假服务，一个都不能漏：
    fallback 用的是 DeepSeekTranslator，它读 DEEPSEEK_BASE_URL ——
    只改 INDEX_BASE_URL 的话，fallback 会真的去调 api.deepseek.com，
    测试会**真的花钱**并且结果不可复现。
    """
    os.environ["INDEX_BASE_URL"] = base_url
    os.environ["DEEPSEEK_BASE_URL"] = base_url
    os.environ["DEEPSEEK_API_KEY"] = "sk-test-not-a-real-key-000000000000"
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-not-real-000000000000"
    for m in [m for m in list(sys.modules) if m.startswith("src")]:
        del sys.modules[m]
    import importlib
    import src.config as C
    import src.translator as T
    importlib.reload(C)
    importlib.reload(T)
    if monkey:
        monkey(T)
    return T


def reply_all(body):
    """按源文里出现的 id 逐条回译文 —— 模拟一个乖模型。"""
    payload = {}
    user = body["messages"][-1]["content"]
    block = user.split("【源文】", 1)[1].split("【", 1)[0]
    try:
        payload = json.loads(block)
    except Exception:
        payload = {}
    out = {k: {"translation": f"译{v['text']}"} for k, v in payload.items()}
    return json.dumps(out, ensure_ascii=False)


srv, base = start_server(reply_all)
try:
    T = fresh_translator(base)
    out = T.translate(segs(5), provider="index", context_window=0)

    check("HTTP 路径跑通，返回 5 条", len(out) == 5, str(len(out)))
    check("译文来自服务端", out[0]["translated"] == "译原文0", str(out[0]))
    check("未标记失败", all(not s["failed"] for s in out))
    check("id 用全局 id（源文 key 是 0..4）",
          all(k in _Handler.seen[-1]["messages"][-1]["content"] for k in
              ['"0"', '"4"']))
    check("system prompt 已下发", _Handler.seen[-1]["messages"][0]["role"] == "system")
    check("走了 instTrans prompt",
          "【约束要求】" in _Handler.seen[-1]["messages"][-1]["content"])
    check("按官方默认传 temperature=0", _Handler.seen[-1].get("temperature") == 0.0)
    check("max_tokens 用官方默认 1024",
          _Handler.seen[-1].get("max_tokens") == 1024)
    check("请求带 enable_thinking=False（SDK 会把 extra_body 平铺进请求体）",
          _Handler.seen[-1].get("chat_template_kwargs", {})
          .get("enable_thinking") is False,
          str(list(_Handler.seen[-1].keys())))
finally:
    srv.shutdown()


# ── 模型少给一条：必须只丢那一条，不能整体错位 ──
def reply_drop_one(body):
    """批较大时漏掉最后一条 —— 这是缩批能救的真实故障模式。

    如果模型**无论批多大**都漏最后一条（确定性缺陷），缩批是救不了的，
    那种情况就该整体失败 —— 下面另有一条断言覆盖。
    """
    user = body["messages"][-1]["content"]
    block = user.split("【源文】", 1)[1].split("【", 1)[0]
    payload = json.loads(block)
    keys = list(payload)
    if len(keys) > 2:
        keys = keys[:-1]
    return json.dumps({k: {"translation": f"译{payload[k]['text']}"} for k in keys},
                      ensure_ascii=False)


srv, base = start_server(reply_drop_one)
try:
    T = fresh_translator(base)
    # 关掉校验，单独观察「映射」行为（否则会触发重试/缩批）
    T.INDEX_VALIDATION_ENABLED = False
    out = T.translate(segs(4), provider="index", context_window=0)
    check("少一条时，只有那一条为空（其余不错位）",
          out[0]["translated"] == "译原文0" and out[2]["translated"] == "译原文2"
          and out[3]["translated"] == "",
          str([s["translated"] for s in out]))
    check("缺失的那条被标记 failed", out[3]["failed"] is True)
finally:
    srv.shutdown()


# ── 模型总是少一条：校验应拦住并缩批 ──
srv, base = start_server(reply_drop_one)
try:
    T = fresh_translator(base)
    out = T.translate(segs(4), provider="index", context_window=0, batch_size=4)
    check("校验拦住「少条」并缩批后补齐",
          all(s["translated"] for s in out), str([s["translated"] for s in out]))
    check("缩批确实发生了（请求数 > 1）", len(_Handler.seen) > 1, str(len(_Handler.seen)))
finally:
    srv.shutdown()


# ── 过长 → 请求压缩，而不是截断 ──
def reply_too_long(body):
    user = body["messages"][-1]["content"]
    if "压缩为更简洁" in user:                      # 压缩请求
        block = user.split("【源文】", 1)[1].split("\n\n只输出", 1)[0]
        payload = json.loads(block)
        return json.dumps({k: {"translation": "简短"} for k in payload}, ensure_ascii=False)
    block = user.split("【源文】", 1)[1].split("【", 1)[0]
    payload = json.loads(block)
    return json.dumps({k: {"translation": "非常非常冗长的译文" * 6} for k in payload},
                      ensure_ascii=False)


srv, base = start_server(reply_too_long)
try:
    T = fresh_translator(base)
    out = T.translate(segs(1), provider="index", context_window=0)
    check("过长译文被请求压缩（不是截断）", out[0]["translated"] == "简短",
          str(out[0]["translated"]))
    check("确实发了第二次请求", len(_Handler.seen) == 2, str(len(_Handler.seen)))
finally:
    srv.shutdown()


# ── 术语真的进了 prompt，且模型遵循后能通过硬约束 ──
def reply_glossary_ok(body):
    """一个「遵守术语表」的假模型：把硬约束里的目标词塞进译文。"""
    user = body["messages"][-1]["content"]
    block = user.split("【源文】", 1)[1].split("【", 1)[0]
    payload = json.loads(block)
    # 从【硬性要求】专名/术语对照: A→B、C→D 里抠出目标词
    targets = []
    if "专名/术语对照: " in user:
        seg = user.split("专名/术语对照: ", 1)[1].split("\n", 1)[0]
        targets = [p.split("→", 1)[1].strip() for p in seg.split("、") if "→" in p]
    text = "".join(targets) or "译文"
    return json.dumps({k: {"translation": text} for k in payload}, ensure_ascii=False)


srv, base = start_server(reply_glossary_ok)
try:
    T = fresh_translator(base)
    out = T.translate([{"start": 0.0, "end": 1.0, "text": "推しが尊い"}],
                      provider="index", context_window=0)
    sent = _Handler.seen[-1]["messages"][-1]["content"]
    check("命中的术语进了硬约束", "推し→推" in sent and "尊い→太尊了" in sent, sent[:400])
    check("未命中的词条没有被注入（省 token）", "エモい" not in sent)
    check("遵守术语的译文能通过硬约束校验",
          all(not s["failed"] for s in out), str(out[0]))

    # 反向：同样的输入，模型不遵守术语 → 必须失败
    srv2, base2 = start_server(reply_all)
    try:
        T2 = fresh_translator(base2)
        try:
            T2.translate([{"start": 0.0, "end": 1.0, "text": "推しが尊い"}],
                         provider="index", context_window=0)
            check("不遵守术语 → 必须报错", False, "居然通过了")
        except RuntimeError as e:
            check("不遵守术语 → 必须报错（硬约束一票否决）",
                  "未遵循术语" in str(e), str(e)[:160])
    finally:
        srv2.shutdown()
finally:
    srv.shutdown()


# ── 服务没起：必须给人话错误 ──
srv, base = start_server(reply_all)
port = srv.server_port
srv.shutdown()          # 立刻关掉，制造「没起服务」
try:
    T = fresh_translator(f"http://127.0.0.1:{port}/v1")
    from src.config import validate_config
    try:
        validate_config(provider="index")
        check("服务未启动时应报错", False, "居然通过了")
    except ValueError as e:
        msg = str(e)
        check("提示语明确说服务没起", "is not running" in msg, msg[:120])
        check("提示语给出预期地址", f"127.0.0.1:{port}/v1" in msg, msg[:200])
        check("提示语给出启动命令", "vllm serve" in msg, msg[:250])
        check("提示语给出退路（换 deepseek）", "TRANSLATION_PROVIDER=deepseek" in msg)
        check("不是裸 Connection refused", "Connection refused" not in msg)
except Exception as e:  # noqa: BLE001
    check("服务未启动路径未抛异常", False, f"{type(e).__name__}: {e}")


# ── fallback 默认关闭 ──
import src.config as _C  # noqa: E402
check("FALLBACK_ENABLED 默认关闭", _C.FALLBACK_ENABLED is False)
check("FALLBACK_PROVIDER 默认 deepseek", _C.FALLBACK_PROVIDER == "deepseek")
check("INDEX_API_KEY 默认 EMPTY（不是真 key）", _C.INDEX_API_KEY == "EMPTY")
check("VALID_PROVIDERS 含 index 且 index 在首位",
      _C.VALID_PROVIDERS[0] == "index", str(_C.VALID_PROVIDERS))


# ── fallback 打开后确实会兜底 ──
def reply_index_fails_fallback_ok(body):
    user = body["messages"][-1]["content"]
    if "【源文】" in user and '"translation"' in user:
        # Index 一律回垃圾（校验必失败）
        return "这不是 JSON"
    # fallback（deepseek 格式的 prompt）回正常 JSON 数组
    n = user.count('"id"')
    return json.dumps([{"id": i, "text": f"备{i}"} for i in range(n)], ensure_ascii=False)


srv, base = start_server(reply_index_fails_fallback_ok)
try:
    T = fresh_translator(base)
    T.FALLBACK_ENABLED = True
    T.FALLBACK_PROVIDER = "deepseek"
    out = T.translate(segs(2), provider="index", context_window=0)
    check("Index 全废时由 fallback 兜底",
          all(s["translated"].startswith("备") for s in out),
          str([s["translated"] for s in out]))
finally:
    srv.shutdown()


print()
print("=" * 62)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 62)
sys.exit(1 if FAIL else 0)
