"""
Index-Translate 的 prompt 组装（instTrans 规范格式）。

为什么单独一个模块：Index-Translate 是**在 instTrans 基准格式上训练**的，
prompt 的骨架（`【源文】` / `【约束要求】` / 结尾指令）不是随便写的 ——
偏离训练格式会明显掉质量。所以这里严格照抄官方客户端
（`inference/llm/translate.py`）的拼装方式，并把「官方格式」这件事
集中在一处，方便日后对照上游更新。

官方两种格式（摘自 inference/llm/README_zh.md）：

  带约束：
    请将以下{源语言}{文体}翻译成{目标语言}，并且严格遵循所有约束要求。

    【源文】
    {source_text}

    【约束要求】
    1. 【硬性要求】...
    2. 【注意】...

    只输出译文，不要有任何额外说明。

  基础无约束：
    请将以下{源语言}文本翻译为{目标语言}，直接输出翻译结果，不要进行任何解释。

    {source_text}

本项目在其上加了一层：字幕需要 **JSON 进 / JSON 出**，才能可靠地
按 id 映射回原时间轴。官方也说明「当源文为 JSON 且带格式保留约束时，
结尾会自动替换为 JSON 输出指令」——本模块即按此扩展。
"""

import json

# 官方约束项前缀：硬约束（一票否决）与软约束（渐进评分）
HARD = "【硬性要求】"
NOTE = "【注意】"


def _format_pairs(pairs: dict) -> str:
    """术语对照表 → 官方要求的 `A→B、C→D` 形式。"""
    return "、".join(f"{k}→{v}" for k, v in pairs.items())


def _format_context(items: list[dict], label: str) -> str:
    """上下文块。只给模型看，明确标注「不要翻译」。"""
    lines = []
    for it in items:
        spk = f"[{it['speaker']}] " if it.get("speaker") else ""
        ja = it.get("text", "")
        zh = it.get("translated") or ""
        lines.append(f"{spk}{ja}" + (f"  →  {zh}" if zh else ""))
    return f"{label}\n" + "\n".join(lines)


def build_instrans_prompt(
    segments: list[dict],
    ids: list[int],
    glossary_pairs: dict | None = None,
    context_before: list[dict] | None = None,
    context_after: list[dict] | None = None,
    source_lang: str = "日文",
    target_lang: str = "中文",
    genre: str = "",
) -> str:
    """组装一批字幕的 instTrans prompt。

    Args:
        segments: 本批要翻译的字幕（含 text / 可选 speaker）。
        ids: 与 segments 一一对应的**全局 id**。用全局 id 而不是批内下标，
              模型回传时才能逐条核对，避免「少一条就整体错位」。
        glossary_pairs: 本批命中的术语 {日文: 中文}，组装为硬约束。
        context_before: 前文（已翻译），仅供理解语境。
        context_after: 后文（尚未翻译），仅供理解语境。
        source_lang / target_lang: 语言名（官方用完整语言名，不用代码）。
        genre: 文体声明（官方 -d / --genre），本项目固定为字幕场景。
    """
    genre_txt = f"{genre}" if genre else "字幕"

    # 源文用 JSON 字典：key 就是字幕 id，天然防止模型改动 id。
    payload = {}
    for seg, sid in zip(segments, ids):
        entry = {"text": seg["text"]}
        if seg.get("speaker"):
            entry["speaker"] = seg["speaker"]
        payload[str(sid)] = entry
    source_block = json.dumps(payload, ensure_ascii=False, indent=1)

    constraints: list[str] = []

    # 硬约束 1：术语强对照（官方 -g / --glossary 的格式）
    if glossary_pairs:
        constraints.append(
            f"{HARD}专名/术语对照: {_format_pairs(glossary_pairs)}"
        )

    # 硬约束 2：结构保护 —— 这是我们自己的 JSON 契约
    constraints.append(
        f"{HARD}保留源文的 JSON 结构不变：只翻译每条 \"text\" 的值；"
        "不得改动任何 key（字幕 id）、不得增删条目、不得改变条目顺序"
    )

    # 硬约束 3：社交元素保护（官方 social_preserve）—— 弹幕/评论里常见
    constraints.append(
        f"{HARD}保留源文中的社交元素原样不变：@用户名、#话题#、[表情代码]、URL"
    )

    # 软约束：文体与语体（官方 -S / --soft）
    constraints.append(
        f"{NOTE}译文用于视频字幕：简洁、自然、口语化，"
        "保留说话人语气，避免书面腔与逐字直译"
    )

    # 软约束：指代一致（官方 coref_resolution）
    if any(s.get("speaker") for s in segments):
        constraints.append(
            f"{NOTE}按 speaker 区分说话人：同一说话人的人称与敬语等级保持一致，"
            "不要把 A 的话译成 B 的口吻"
        )

    parts = [f"请将以下{source_lang}{genre_txt}翻译成{target_lang}，并且严格遵循所有约束要求。"]

    if context_before:
        parts.append(_format_context(context_before, "【上文（仅供理解语境，不要翻译）】"))

    parts.append("【源文】\n" + source_block)

    if context_after:
        parts.append(_format_context(context_after, "【下文（仅供理解语境，不要翻译）】"))

    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(constraints, 1))
    parts.append("【约束要求】\n" + numbered)

    # 结尾指令：官方在 JSON 场景下会换成 JSON 输出指令
    parts.append(
        "只输出 JSON，结构与【源文】完全一致：key 为字幕 id，value 为 "
        '{"translation": "译文"}。不要输出 Markdown、解释或任何额外文字。'
    )

    return "\n\n".join(parts)


def build_instrans_review_prompt(
    segments: list[dict],
    ids: list[int],
    context_before: list[dict] | None = None,
    source_lang: str = "日文",
    target_lang: str = "中文",
) -> str:
    """校对阶段的 instTrans prompt（同样 JSON 进 / JSON 出）。"""
    payload = {}
    for seg, sid in zip(segments, ids):
        entry = {"text": seg["text"], "translated": seg.get("translated", "")}
        if seg.get("speaker"):
            entry["speaker"] = seg["speaker"]
        payload[str(sid)] = entry
    source_block = json.dumps(payload, ensure_ascii=False, indent=1)

    constraints = [
        f"{HARD}保留源文的 JSON 结构不变：key（字幕 id）原样保留，不得增删条目",
        f"{HARD}不得增删信息：不改变原意与信息量，条数必须一致",
        f"{NOTE}让相邻句子的指代与人称与上文一致，去除翻译腔，改成自然口语",
        f"{NOTE}每条字幕保持简短，适合阅读；已足够好的条目原样保留",
    ]

    parts = [f"请校对以下{source_lang}→{target_lang}字幕译文，并且严格遵循所有约束要求。"]
    if context_before:
        parts.append(_format_context(context_before, "【上文（已校对，用于保持一致）】"))
    parts.append("【源文】\n" + source_block)
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(constraints, 1))
    parts.append("【约束要求】\n" + numbered)
    parts.append(
        "只输出 JSON，结构与【源文】一致：key 为字幕 id，value 为 "
        '{"translation": "修正后的译文"}。不要输出 Markdown、解释或额外文字。'
    )
    return "\n\n".join(parts)


# 官方要求「术语对照 + 结构化保护」时，instTrans 会换成 JSON 输出指令；
# 这里给出一份简短的基础格式（无约束）备用 —— 例如 glossary 关闭时。
def build_basic_prompt(segments: list[dict], ids: list[int],
                       source_lang: str = "日文", target_lang: str = "中文") -> str:
    payload = {str(sid): s["text"] for s, sid in zip(segments, ids)}
    return (
        f"请将以下{source_lang}文本翻译为{target_lang}，直接输出翻译结果，不要进行任何解释。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )
