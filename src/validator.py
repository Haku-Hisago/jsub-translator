"""
译文校验（validator）。

为什么需要：本地小模型（2B）在长批、生僻输入下会偶发地
「少给一条」「多说一句话」「把 JSON 讲成散文」「把术语译成别的词」。
这些错误的共同点是**不会抛异常** —— 不校验的话它们会一路流到 ASS 文件里，
用户拿到手才发现某几条字幕错位或变成英文解释。

所以每次拿到模型回复后先过一遍这里：
  · fatal 级问题 → 该批作废，重试（必要时缩批）
  · warn  级问题 → 记录但不阻断（例如译文偏长，会另发一次压缩请求）

设计原则：**宁可漏报，不要误报**。误报会让正常译文被反复重试，
比漏报更糟（既慢又可能把好译文换坏）。所以每条规则都只匹配高置信度信号。
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Issue:
    kind: str      # 机器可读的问题类型
    detail: str    # 人类可读的说明
    fatal: bool    # True = 该批不可用


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    too_long_ids: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """没有任何 fatal 问题才叫通过。"""
        return not any(i.fatal for i in self.issues)

    @property
    def fatal_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.fatal]

    def summary(self) -> str:
        if not self.issues:
            return "校验通过"
        return "；".join(f"{i.kind}: {i.detail}" for i in self.issues[:3])


# ── Markdown / 解释性文字的特征 ──────────────────────────────
# 只匹配高置信度信号：代码围栏、标题行、粗体标记。
_MD_FENCE = re.compile(r"```|^\s{0,3}#{1,6}\s|\*\*[^*]+\*\*", re.M)

# 解释性开头。**只在字符串开头**匹配 —— 字幕正文里出现「注意」是正常的，
# 但一条字幕以「以下是」开头就基本可以断定模型在解释而非翻译。
_EXPLANATION_PREFIX = re.compile(
    r"^\s*(以下是|下面是|译文如下|翻译如下|这是翻译|注[:：]|说明[:：]|"
    r"Here is|Here's|Translation[:：]|Note[:：]|Sure[,，])",
    re.I,
)

# 模型跑偏时常见的自我描述
_EXPLANATION_CONTAINS = (
    "作为一个AI", "作为 AI", "我是一个AI", "我是一个 AI",
    "I cannot", "I'm sorry", "抱歉，我不能",
)

_KANA = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
# 全角/半角括号里的注音不算日文残留，这里不特殊处理 —— 只按比例判断。

# 字面量 \N：libass 会把它当成硬换行，模型输出它等于偷偷插了换行
_LITERAL_BREAK = re.compile(r"\\[Nn]")


def _kana_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(_KANA.findall(text)) / len(text)


def _glossary_violations(ja: str, zh: str, pairs: dict) -> list[str]:
    """检查硬约束术语是否落实。

    词典目标值可能写成「我推/主推」这种多选，所以按 / 拆开，
    只要**任一个**出现就算合规 —— 否则会把合法译文误判成违规。

    ⚠️ 已知局限：用子串判断，所以**单字目标词**不可靠 ——
    目标词是「推」时，「主推」也算命中（它确实含「推」）。
    对 1 个字的术语，这条检查基本等于不生效。
    这是有意接受的：换成更严格的词边界判断会误伤大量合法译文
    （中文没有天然的词边界），而误报比漏报更糟 —— 它会让好译文被反复重试。
    术语长度 ≥2 时判断是可靠的。
    """
    bad = []
    for src, tgt in pairs.items():
        if src not in ja:
            continue  # 这条术语没出现在这句里
        options = [o.strip() for o in str(tgt).split("/") if o.strip()]
        if options and not any(o in zh for o in options):
            bad.append(f"{src}→{tgt}")
    return bad


def validate_translations(
    expected_ids: list[int],
    translations: dict[int, str],
    sources: dict[int, str],
    glossary_pairs: dict | None = None,
    max_ratio: float = 1.8,
    kana_warn_ratio: float = 0.3,
) -> ValidationResult:
    """校验一批译文。

    Args:
        expected_ids: 本批应当返回的字幕 id（顺序无关）。
        translations: 模型返回的 {id: 译文}。
        sources: {id: 日文原文}。
        glossary_pairs: 本批注入的术语 {日文: 中文}（用于核对硬约束）。
        max_ratio: 译文长度上限倍率（相对日文原文），超过则要求压缩。
        kana_warn_ratio: 日文假名占比超过此值即告警。

    Returns:
        ValidationResult。`ok` 为 False 表示必须重试。
    """
    res = ValidationResult()
    expected = set(expected_ids)
    got = set(translations)

    # ── 条数与 id 对应（任务 #14 的前两条，也是最致命的）──
    missing = sorted(expected - got)
    if missing:
        res.issues.append(Issue(
            "missing_ids",
            f"缺少 {len(missing)} 条：{missing[:8]}{'…' if len(missing) > 8 else ''}",
            fatal=True,
        ))
    extra = sorted(got - expected)
    if extra:
        res.issues.append(Issue(
            "extra_ids",
            f"多出 {len(extra)} 条：{extra[:8]}{'…' if len(extra) > 8 else ''}",
            fatal=True,
        ))

    for sid in sorted(expected & got):
        zh = (translations.get(sid) or "").strip()
        ja = sources.get(sid, "")

        if not zh:
            res.issues.append(Issue("empty", f"id={sid} 译文为空", fatal=True))
            continue

        # ── 格式污染 ──
        if _MD_FENCE.search(zh):
            res.issues.append(Issue(
                "markdown", f"id={sid} 含 Markdown 标记", fatal=True))
            continue

        if _EXPLANATION_PREFIX.match(zh) or any(p in zh for p in _EXPLANATION_CONTAINS):
            res.issues.append(Issue(
                "explanation", f"id={sid} 疑似解释性文字：{zh[:30]}", fatal=True))
            continue

        # ── 硬约束：术语必须落实 ──
        if glossary_pairs:
            bad = _glossary_violations(ja, zh, glossary_pairs)
            if bad:
                res.issues.append(Issue(
                    "glossary", f"id={sid} 未遵循术语：{'、'.join(bad[:3])}", fatal=True))
                continue

        # ── 以下为告警级：记录但不作废 ──
        if ja and len(zh) > len(ja) * max_ratio and len(zh) > 12:
            res.too_long_ids.append(sid)

        if _kana_ratio(zh) > kana_warn_ratio:
            res.issues.append(Issue(
                "kana_residue", f"id={sid} 日文残留较多（{_kana_ratio(zh):.0%}）", fatal=False))

        if _LITERAL_BREAK.search(zh):
            res.issues.append(Issue(
                "literal_break", f"id={sid} 含字面量 \\N（会被当成换行）", fatal=False))

    return res


def build_shrink_prompt(items: list[tuple[int, str, str]]) -> str:
    """为「译文过长」的字幕生成压缩请求。

    任务 #15 明确要求：**不要粗暴截断**，而是请模型重写得更紧凑。
    截断会直接吃掉句子成分，比啰嗦更难被发现。

    Args:
        items: [(id, 日文原文, 当前过长的中文译文), ...]
    """
    import json

    payload = {
        str(sid): {"text": ja, "current": zh}
        for sid, ja, zh in items
    }
    return (
        "以下中文字幕译文偏长，不适合作为视频字幕阅读。\n\n"
        "请把每条 \"current\" 压缩为更简洁自然的中文，同时**保持原意不变**、"
        "不要遗漏信息、不要添加新信息。\n\n"
        "【源文】\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
        + "\n\n只输出 JSON，结构与【源文】一致：key 为字幕 id，value 为 "
          '{"translation": "压缩后的译文"}。不要输出解释或额外文字。'
    )
