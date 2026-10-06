"""
LLM-based translation supporting multiple engines.

Engines:
  · index     — 本地 Index-Translate-2B（vLLM，OpenAI 兼容接口）**默认**
  · deepseek  — DeepSeek 云端 API（OpenAI 兼容 SDK）
  · anthropic — Anthropic Claude 原生 Messages API

三个后端共用同一个 `BaseTranslator.translate_batch()` 接口，所以
pipeline / fusion / review 都不需要知道自己在跟谁说话。
差异（prompt 格式、是否用全局 id）通过 `BaseTranslator` 上的钩子表达。
"""

import json
import time
from abc import ABC, abstractmethod
from typing import Optional, Callable

from openai import OpenAI
from anthropic import Anthropic

from .config import (
    TRANSLATION_PROVIDER, VALID_PROVIDERS,
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL,
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL,
    TRANSLATION_BATCH_SIZE,
    CONTEXT_WINDOW,
    REVIEW_BATCH_SIZE,
    INDEX_BASE_URL, INDEX_API_KEY, INDEX_MODEL,
    INDEX_MAX_TOKENS, INDEX_TEMPERATURE, INDEX_TIMEOUT,
    INDEX_ENABLE_THINKING,
    INDEX_BATCH_SIZE,
    INDEX_CONTEXT_BEFORE, INDEX_CONTEXT_AFTER,
    INDEX_GLOSSARY_ENABLED,
    INDEX_VALIDATION_ENABLED, INDEX_VALIDATION_RETRY,
    INDEX_MAX_LENGTH_RATIO,
    FALLBACK_ENABLED, FALLBACK_PROVIDER,
)
from .glossary import build_glossary_hint, build_glossary_pairs
from . import index_prompt
from .validator import validate_translations, build_shrink_prompt

# Maximum retries for API calls
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds base

# 连续这么多批失败就认为问题不是偶发的，直接中止。
# 注意每一批内部已经重试过 MAX_RETRIES 次，所以「连续 3 批失败」=
# 连续 9 次 API 调用失败 —— 这已经不是抖动，是确定性的故障。
# 不中止的话，一个 500 条字幕的视频要跑 34 批、每批白等 2+4 秒，
# 「填错 key」就变成几分钟的干等。
EARLY_ABORT_AFTER = 3

# 重试没有意义的状态码：重试只是让用户白等。
#   400 请求非法 / 401 key 无效 / 403 无权限 / 404 模型不存在 / 422 参数错误
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}


def _is_retryable(err: Exception) -> bool:
    """这个错误值得重试吗？

    「key 无效」「余额不足」这类是确定性的 —— 重试多少次都一样，
    只会把「立刻报错」拖成「等 2+4 秒再报错」。
    """
    status = getattr(err, "status_code", None)
    if status is None:
        resp = getattr(err, "response", None)
        status = getattr(resp, "status_code", None)
    if isinstance(status, int) and status in _NON_RETRYABLE_STATUS:
        return False
    return True


# System prompt optimized for Japanese → Chinese subtitle translation
SYSTEM_PROMPT = """你是一位专业的日文→中文字幕翻译专家。你的任务是翻译日语字幕为自然流畅的中文。

翻译原则：
1. 保持原文的语气、敬语和情感色彩
2. 口语化翻译，符合中文表达习惯，适合字幕阅读
3. 专有名词和人名保留原文或使用通用译名
4. 如果原文有文化特定表达（如谚语、梗），翻译为中文对应表达或加简洁说明
5. 每行字幕尽量简短，方便阅读
6. 上下文会提供前后几句，请根据语境翻译，避免逐字翻译
7. 若输入中包含「上文语境」，仅用于理解当前对话的语境（人物、指代、话题），不要重复翻译上文内容
8. 若输入中包含「术语参考」，优先采用其中给出的译法（尤其 JPOP/偶像/音乐行业术语）
9. 每个元素可能带 "speaker"（说话人标签），请结合说话人身份与语气翻译

「说话人一致性」是硬性要求（当输入带有 "speaker" 标签时）：
- 同一 speaker 的日文一人称必须译得一致：私→我、僕→我、俺→老子/我、わたくし→本人/我。
  同一角色前后不得一会儿「我」一会儿「人家」。
- 同一 speaker 对同一对象的敬语等级保持一致（对长辈用敬语、对平辈用口语），
  不得同一角色前一秒客气后一秒粗鲁。
- 日文省略主语是常态，请依据 speaker 判断谁在说话再补主语；
  不要把 A 说的话译成 B 的口吻。
- 不同 speaker 的语气要能被中文读者区分开（例如角色化的口癖、方言感、说话长短）。
- 角色称呼（お姉さん→姐姐、先輩→学长 等）按 speaker 关系保持全片统一。

输入格式：JSON 数组，每个元素包含 "id"、"text"（日文）、可选 "speaker"
输出格式：JSON 数组，每个元素包含 "id" 和 "text"（中文翻译），保持 id 对应关系，条数一致"""


# System prompt for the post-translation coherence review pass
REVIEW_SYSTEM_PROMPT = """你是日文→中文字幕的「连贯性校对员」。你会收到已翻译的字幕（日文原文 + 中文初译），请结合上下文逐条检查并修正，使整个片段语境连贯、读起来自然。

校对原则：
1. 保持原意、语气与信息量，不得增删内容或改变条数
2. 让相邻句子的指代（他/她/这个/那个/人称）与上文一致，必要时补全或替换主语
3. 人名、称谓、术语在全片保持一致（参考「上文」中已校对的译法）
4. 去除翻译腔，改成自然的中文口语（如避免生硬的「…的事情」「被…」「…来着」「正在…中」等）
5. 每条字幕保持简短，适合阅读
6. 若某条译文已足够好，则原样保留，不要为了改动而改动
7. 利用 "speaker" 标签做角色一致性校对：同一 speaker 的一人称与敬语等级必须统一，
   发现某条译文把 A 的口吻串到了 B 身上，请按该条自身的 speaker 改回对应口吻
8. 「上文」中的 speaker 与当前条目相同时，视为同一角色，称谓与语气需延续

输入：JSON 数组，每个元素 {"id", "text"(日文原文), "translated"(中文初译), "speaker"}
输出：JSON 数组，每个元素 {"id", "text"(修正后的中文)}，保持 id 与顺序一致，条数相同"""


def _format_context(context: list[dict], label: str) -> str:
    """Format a rolling window of already-translated/reviewed segments as a context block.

    Each context item is a segment dict with 'text' (Japanese) and 'translated' (Chinese).
    """
    lines = []
    for c in context:
        spk = f"[{c['speaker']}] " if c.get("speaker") else ""
        lines.append(f"{spk}{c['text']} → {c.get('translated', '')}")
    return f"{label}：\n" + "\n".join(lines)


def _build_translation_prompt(segments: list[dict], context: list[dict] = None) -> str:
    """Build the translation request prompt from segments.

    Args:
        segments: Segments in this batch.
        context: Optional rolling window of previous (source + translated) segments,
                 injected as 上文语境 so pronouns/topics/terms stay consistent.
    """
    items = []
    for i, seg in enumerate(segments):
        item = {"id": i, "text": seg["text"]}
        if seg.get("speaker"):
            item["speaker"] = seg["speaker"]
        items.append(item)
    prompt = json.dumps(items, ensure_ascii=False, indent=2)

    parts = []
    # 上文语境前置，帮助模型理解当前批次的指代/话题
    if context:
        parts.append(_format_context(context, "上文语境（已翻译，仅作语境参考，勿重复翻译）"))

    # 按本批次原文命中的术语，前置术语参考（命中才注入，未命中不占用 token）
    source_text = "\n".join(seg["text"] for seg in segments)
    hint = build_glossary_hint(source_text)
    if hint:
        parts.append(hint)

    parts.append(prompt)
    return "\n\n".join(parts)


def _build_review_prompt(segments: list[dict], context: list[dict] = None) -> str:
    """Build the prompt for the post-translation coherence review pass.

    Each segment carries its Japanese source and the draft Chinese translation,
    so the reviewer can check fidelity AND coherence at once.
    """
    items = []
    for i, seg in enumerate(segments):
        item = {"id": i, "text": seg["text"], "translated": seg.get("translated", "")}
        if seg.get("speaker"):
            item["speaker"] = seg["speaker"]
        items.append(item)
    prompt = json.dumps(items, ensure_ascii=False, indent=2)

    parts = []
    if context:
        parts.append(_format_context(context, "上文（已校对完成的译文，用于保持一致）"))
    parts.append(prompt)
    return "\n\n".join(parts)


def _parse_translation_response(content: str, expected_count: int) -> list[dict]:
    """把模型回复解析成 [{"id": int|None, "text": str}, ...]。

    容错点：
      · 去掉 Markdown 代码围栏
      · 整体是 JSON 数组 → 直接用
      · 整体是 JSON **对象**（Index 走 {"id": {"translation": ...}} 契约）→ 展开
      · 一行一个 JSON 对象 → 逐行收集
      · 字段名兼容 `text`（本项目原有）与 `translation`（Index 契约）

    解析不出来就返回空列表 —— 由调用方判定为「该批失败」，而不是把
    半截内容当成译文写进字幕。
    """
    content = (content or "").strip()

    # Remove markdown code blocks if present
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        content = content.strip()

    def _normalize(obj) -> Optional[dict]:
        """单个对象 → {"id":…, "text":…}；认不出来返回 None。"""
        if not isinstance(obj, dict):
            return None
        text = obj.get("text")
        if not isinstance(text, str):
            text = obj.get("translation")
        if not isinstance(text, str):
            return None
        sid = obj.get("id")
        if isinstance(sid, str) and sid.strip().lstrip("-").isdigit():
            sid = int(sid.strip())
        elif not isinstance(sid, int):
            sid = None
        return {"id": sid, "text": text}

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        out = [n for n in (_normalize(o) for o in parsed) if n]
        if out:
            return out

    if isinstance(parsed, dict):
        # {"101": {"translation": "…"}, "102": "…"} —— Index 的字典契约
        out = []
        for k, v in parsed.items():
            if isinstance(v, str):
                n = _normalize({"id": k, "text": v})
            else:
                n = _normalize({"id": k, **v} if isinstance(v, dict) else None)
            if n:
                out.append(n)
        if out:
            return out

    # Fallback: try to parse line by line
    results = []
    for line in content.split("\n"):
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        n = _normalize(obj)
        if n:
            results.append(n)
    return results


def _map_by_id(parsed: list[dict], batch_ids: list[int],
               expected_count: int) -> dict[int, str]:
    """把解析结果映射成 {批内下标: 译文}。

    优先**按 id 映射**（Index 用全局 id，其余后端用批内下标 0..n-1）——
    模型少给一条时，按位置映射会让后面所有字幕整体错位（错得无声无息），
    按 id 映射则只丢那一条。id 认不出来时才退回位置映射。
    """
    by_id: dict[int, str] = {}
    id_set = set(batch_ids)
    for item in parsed:
        sid = item.get("id")
        if sid in id_set and sid not in by_id:
            by_id[sid] = item["text"]

    if by_id:
        # 位置 → 该位置的全局 id → 译文
        return {pos: by_id[gid] for pos, gid in enumerate(batch_ids) if gid in by_id}

    # 模型没给 id：退回位置映射（保持与旧版一致）
    return {i: item["text"] for i, item in enumerate(parsed[:expected_count])}


# ═══════════════════════════════════════════════════════════════
# Abstract Base
# ═══════════════════════════════════════════════════════════════

class BaseTranslator(ABC):
    """Abstract base for translation engines."""

    #: 是否使用「全局 id + JSON 字典」的 prompt / 输出约定。
    #:
    #: False（deepseek / anthropic）：沿用批内下标 0..n-1 的 JSON 数组，
    #:                               与项目历史行为完全一致。
    #: True （index）：用字幕在整片里的全局 id，源文以 {id: {...}} 字典下发。
    #:                Index 在 instTrans 格式上训练，这种结构最稳，且模型
    #:                回传后能逐条核对 id —— 少一条只丢一条，不会整体错位。
    uses_global_ids = False

    @abstractmethod
    def translate_batch(self, prompt: str, system: Optional[str] = None, temperature: float = 0.0) -> str:
        """Send a batch translation request. Returns the raw text response.

        Args:
            prompt: The user message content.
            system: Optional override for the system prompt (defaults to SYSTEM_PROMPT).
            temperature: Sampling temperature.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable engine name."""
        ...

    # ── prompt 组装钩子 ──────────────────────────────────────
    # 默认实现 = 项目原有格式，保证 deepseek / anthropic 行为一字不变。

    def build_prompt(self, segments, ids, context_before, context_after, glossary_pairs) -> str:
        """翻译阶段的 user message。"""
        return _build_translation_prompt(segments, context_before)

    def build_review_prompt(self, segments, ids, context_before) -> str:
        """校对阶段的 user message。"""
        return _build_review_prompt(segments, context_before)


# ═══════════════════════════════════════════════════════════════
# Index-Translate Translator (本地 vLLM, OpenAI-compatible)
# ═══════════════════════════════════════════════════════════════

class IndexTranslator(BaseTranslator):
    """本地 Index-Translate-2B（vLLM 起服务，OpenAI 兼容接口）。

    与云端后端的四点区别：
      1. 不需要真 key —— 本地 vLLM 不校验，按官方约定用 EMPTY
      2. prompt 走 instTrans 官方格式，且用全局 id（见 index_prompt）
      3. 超时给得很宽 —— 本地首次加载模型 / 长批可能要几分钟
      4. 解码参数按官方默认：temperature=0 贪心、enable_thinking=False
    """

    uses_global_ids = True

    def __init__(self, model: str = None, base_url: str = None, api_key: str = None):
        self.model = model or INDEX_MODEL
        self.base_url = base_url or INDEX_BASE_URL
        self.api_key = api_key or INDEX_API_KEY
        self._client = None
        # 有些 vLLM 版本不认识 chat_template_kwargs；被拒一次后就不再发，
        # 避免每条请求都白撞一次 400。
        self._send_thinking_kwarg = not INDEX_ENABLE_THINKING

    @property
    def client(self):
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=INDEX_TIMEOUT,
            )
        return self._client

    @property
    def name(self) -> str:
        return f"Index-Translate ({self.model})"

    # ── prompt 组装：交给 index_prompt（instTrans 官方格式）──

    def build_prompt(self, segments, ids, context_before, context_after, glossary_pairs) -> str:
        if INDEX_GLOSSARY_ENABLED and glossary_pairs:
            return index_prompt.build_instrans_prompt(
                segments, ids,
                glossary_pairs=glossary_pairs,
                context_before=context_before,
                context_after=context_after,
            )
        return index_prompt.build_instrans_prompt(
            segments, ids,
            context_before=context_before,
            context_after=context_after,
        )

    def build_review_prompt(self, segments, ids, context_before) -> str:
        return index_prompt.build_instrans_review_prompt(segments, ids, context_before)

    def _call(self, messages: list[dict], temperature: float) -> str:
        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=INDEX_MAX_TOKENS,
        )
        if self._send_thinking_kwarg:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": INDEX_ENABLE_THINKING}
            }
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            # 服务端不认识 chat_template_kwargs（老版本 vLLM / 非 Qwen 模板）→
            # 摘掉它重发一次。这个降级只做一次，之后不再附带。
            msg = str(e).lower()
            if "chat_template_kwargs" in msg or "enable_thinking" in msg:
                self._send_thinking_kwarg = False
                kwargs.pop("extra_body", None)
                response = self.client.chat.completions.create(**kwargs)
            else:
                raise
        return response.choices[0].message.content

    def translate_batch(self, prompt: str, system: Optional[str] = None,
                        temperature: float = None) -> str:
        temp = INDEX_TEMPERATURE if temperature is None else temperature
        # 没显式给 system 时用 Index 自己的（任务要求 system 明确列约束）。
        # review 阶段会显式传 REVIEW_SYSTEM_PROMPT，走的是上面那条分支。
        sys_prompt = system or INDEX_SYSTEM_PROMPT
        messages = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        messages.append({"role": "user", "content": prompt})

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return self._call(messages, temp)
            except Exception as e:
                last_error = e
                if not _is_retryable(e):
                    break
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (2 ** attempt))
        raise last_error


# ═══════════════════════════════════════════════════════════════
# DeepSeek Translator (OpenAI-compatible SDK)
# ═══════════════════════════════════════════════════════════════

class DeepSeekTranslator(BaseTranslator):
    """Translation via DeepSeek API (OpenAI-compatible)."""

    def __init__(self, api_key: str = None, model: str = None, base_url: str = None):
        self.api_key = api_key or DEEPSEEK_API_KEY
        self.model = model or DEEPSEEK_MODEL
        self.base_url = base_url or DEEPSEEK_BASE_URL
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    @property
    def name(self) -> str:
        return f"DeepSeek ({self.model})"

    def translate_batch(self, prompt: str, system: Optional[str] = None, temperature: float = 0.0) -> str:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system or SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=4096,
                )
                return response.choices[0].message.content
            except Exception as e:
                last_error = e
                # 确定性错误（key 无效等）不重试：重试只会让用户白等
                if not _is_retryable(e):
                    break
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (2 ** attempt))
        raise last_error


# ═══════════════════════════════════════════════════════════════
# Anthropic Translator
# ═══════════════════════════════════════════════════════════════

class AnthropicTranslator(BaseTranslator):
    """Translation via Anthropic Claude API."""

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or ANTHROPIC_API_KEY
        self.model = model or ANTHROPIC_MODEL
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = Anthropic(api_key=self.api_key)
        return self._client

    @property
    def name(self) -> str:
        return f"Anthropic ({self.model})"

    def translate_batch(self, prompt: str, system: Optional[str] = None, temperature: float = 0.0) -> str:
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system or SYSTEM_PROMPT,
                    temperature=temperature,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                )
                return response.content[0].text
            except Exception as e:
                last_error = e
                # 确定性错误（key 无效等）不重试：重试只会让用户白等
                if not _is_retryable(e):
                    break
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (2 ** attempt))
        raise last_error


# ═══════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════

def create_translator(provider: Optional[str] = None) -> BaseTranslator:
    """
    Create a translator instance for the given provider.

    Args:
        provider: "index" / "deepseek" / "anthropic". Defaults to config TRANSLATION_PROVIDER.

    Returns:
        BaseTranslator instance.
    """
    # 空串也要回退到默认引擎 —— 与 resolve_api_key / validate_config 保持一致。
    # 之前只有这里用 `is None` 判断，结果空串能过校验、却在建翻译器时炸掉。
    provider = (provider or TRANSLATION_PROVIDER).lower().strip()
    if provider == "index":
        return IndexTranslator()
    elif provider == "deepseek":
        return DeepSeekTranslator()
    elif provider == "anthropic":
        return AnthropicTranslator()
    else:
        raise ValueError(
            f"Unknown translation provider '{provider}'. "
            f"Valid options: {', '.join(VALID_PROVIDERS)}"
        )


# 翻译失败时写入译文位置的标记。前端**不要**靠匹配这个字符串来判断失败
# （那等于同一个约定写两遍，一边改了另一边就静默失效），
# 改用每段下发的 `failed` 布尔字段。
# ═══════════════════════════════════════════════════════════════
# Top-level translate()
# ═══════════════════════════════════════════════════════════════

# 翻译失败时写入译文位置的标记。前端**不要**靠匹配这个字符串来判断失败
# （那等于同一个约定写两遍，一边改了另一边就静默失效），
# 改用每段下发的 `failed` 布尔字段。
FAILED_MARKER = "[翻译失败: "


def _mark_failed(seg: dict, err: Exception) -> dict:
    """标记一段译文失败：既写入可读文本（会出现在字幕文件里），也打布尔标记。"""
    seg_copy = dict(seg)
    seg_copy["translated"] = f"{FAILED_MARKER}{err}]"
    seg_copy["failed"] = True
    return seg_copy


# Index 的 system prompt。
#
# 官方客户端把所有指令都放在 **user message**（instTrans 格式）里，
# 不发 system —— 因为模型是在那个格式上训练的。
# 但我们仍然显式给一份 system：任务要求 system prompt 明确列出约束，
# 而且实测中「system 里重申一遍」对 2B 这种小模型的指令遵循有正向作用。
#
# 若发现质量反而变差（偏离训练分布），在 .env 里设
# INDEX_USE_SYSTEM_PROMPT=false 即可退回纯官方用法。
INDEX_SYSTEM_PROMPT = """你是一名专业的日语→中文字幕翻译器。

任务：将日语视频字幕翻译成自然、准确、简洁的中文。

要求：
1. 保持原意，不添加原文没有的信息。
2. 优先使用提供的术语表。
3. 人名、团名、歌曲名等专有名词严格遵循术语表，不擅自汉化、不改变大小写。
4. 保持说话人的语气；同一说话人的人称与敬语等级前后一致。
5. 保留口语特点，但不要机械直译。
6. 中文应适合视频字幕阅读，避免过长句子。
7. 不要解释翻译过程。
8. 不要输出 Markdown。
9. 不要输出额外说明。
10. 只返回要求的 JSON。"""


def _uses_global_ids(translator) -> bool:
    """后端是否用「全局 id + JSON 字典」契约。

    用 getattr 而不是直接取属性：测试里的假引擎（以及任何只实现
    `translate_batch` + `name` 的鸭子类型替身）不该被迫实现新钩子。
    """
    return bool(getattr(translator, "uses_global_ids", False))


def _prompt_for(translator, batch, ids, ctx_before, ctx_after, glossary_pairs) -> str:
    """取翻译阶段的 user message（后端没实现钩子时退回项目原有格式）。"""
    fn = getattr(translator, "build_prompt", None)
    if fn is None:
        return _build_translation_prompt(batch, ctx_before)
    return fn(batch, ids, ctx_before, ctx_after, glossary_pairs)


def _review_prompt_for(translator, batch, ids, ctx_before) -> str:
    """取校对阶段的 user message。"""
    fn = getattr(translator, "build_review_prompt", None)
    if fn is None:
        return _build_review_prompt(batch, ctx_before)
    return fn(batch, ids, ctx_before)


def _render_batch(translator, batch, ids, ctx_before, ctx_after, glossary_pairs):
    """调一次模型 + 解析，返回 {prompt_id: 译文}。"""
    prompt = _prompt_for(translator, batch, ids, ctx_before, ctx_after, glossary_pairs)
    content = translator.translate_batch(prompt)
    parsed = _parse_translation_response(content, len(batch))
    return {p["id"]: p["text"] for p in parsed if p.get("id") is not None}


def _shrink_long(translator, chunk, ids, by_pos, too_long_pos):
    """把过长的译文请模型重写得更紧凑（不截断）。

    任务要求明确：**不要粗暴截断**。截断会直接吃掉句子成分，
    而且看起来「正常」，比啰嗦更难被发现。

    Args:
        by_pos: {块内下标: 译文}
        too_long_pos: 需要压缩的块内下标集合
    """
    items = [(ids[pos], chunk[pos]["text"], by_pos[pos])
             for pos in sorted(too_long_pos) if pos in by_pos]
    if not items:
        return by_pos
    try:
        prompt = build_shrink_prompt(items)
        content = translator.translate_batch(prompt)
        parsed = _parse_translation_response(content, len(items))
        fixed = {p["id"]: p["text"] for p in parsed if p.get("id") is not None}
        for pos, pid in enumerate(ids):
            new = (fixed.get(pid) or "").strip()
            if new and pos in by_pos:
                by_pos[pos] = new
    except Exception:
        # 压缩失败就保留原译文 —— 长一点总比丢内容好
        pass
    return by_pos


def _translate_chunk(translator, chunk, ids, sources, ctx_before, ctx_after,
                     glossary_pairs, allow_shrink, depth=0):
    """翻译一个块，带校验 / 重试 / 缩批。

    `ids` 是**该块在 prompt 里的 id**（Index = 全局 id；其余后端 = 块内下标）。
    返回 ({块内下标: 译文}, 最后一次的校验结果或 None)。译文可能部分缺失 ——
    缺失的条目由调用方决定是回退还是标记失败。
    """
    attempts = 1 + max(0, INDEX_VALIDATION_RETRY)
    last_res = None

    for _ in range(attempts):
        # 注意：**不在这里捕获异常**。传输层重试（超时/5xx/退避）已经由各
        # backend 的 translate_batch 内部做完了；再包一层会变成 3×3 次调用，
        # 让「key 填错」这种确定性故障白等 9 轮。异常直接向上抛，由主循环
        # 判定为「该批失败」并累计连续失败数。
        by_id = _render_batch(
            translator, chunk, ids, ctx_before, ctx_after, glossary_pairs)

        if not INDEX_VALIDATION_ENABLED:
            return ({pos: by_id[pid] for pos, pid in enumerate(ids) if pid in by_id},
                    None)

        res = validate_translations(
            ids, by_id, sources, glossary_pairs, INDEX_MAX_LENGTH_RATIO)
        last_res = res
        if res.ok:
            by_pos = {pos: by_id[pid] for pos, pid in enumerate(ids) if pid in by_id}
            # 过长 → 请模型压缩（不截断）
            if res.too_long_ids:
                too = {pos for pos, pid in enumerate(ids) if pid in set(res.too_long_ids)}
                by_pos = _shrink_long(translator, chunk, ids, by_pos, too)
            return by_pos, res
        # 校验失败 → 下一轮重试（同样的 prompt 再要一次，靠采样随机性改善）

    # 重试仍不过 → 缩批重来。批越小，模型越不容易漏条 / 跑偏。
    if allow_shrink and len(chunk) > 1 and depth < 2:
        mid = len(chunk) // 2
        left, _ = _translate_chunk(translator, chunk[:mid], ids[:mid],
                                   {i: sources[i] for i in ids[:mid]},
                                   ctx_before, None, glossary_pairs, True, depth + 1)
        right, _ = _translate_chunk(translator, chunk[mid:], ids[mid:],
                                    {i: sources[i] for i in ids[mid:]},
                                    None, ctx_after, glossary_pairs, True, depth + 1)
        merged = dict(left)
        for pos, text in right.items():
            merged[pos + mid] = text
        if merged:
            return merged, last_res

    return {}, last_res


def translate(
    segments: list[dict],
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    batch_size: Optional[int] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    context_window: Optional[int] = None,
) -> list[dict]:
    """
    Translate Japanese subtitle segments to Chinese.

    Args:
        segments: List of segment dicts with 'start', 'end', 'text' keys.
        provider: "index" / "deepseek" / "anthropic". Defaults to config.
        api_key: API key override.
        batch_size: Segments per API call. Defaults to INDEX_BATCH_SIZE for the
                    Index backend, TRANSLATION_BATCH_SIZE otherwise.
        progress_callback: Optional callback(progress: float 0-1, status: str).
        context_window: Number of previous segments carried forward as 上文语境.
                        Defaults to CONTEXT_WINDOW; 0 disables context threading.

    Returns:
        List of segment dicts with added 'translated' key (Chinese text).
    """
    translator = create_translator(provider)
    use_global_ids = _uses_global_ids(translator)

    if batch_size is None:
        batch_size = INDEX_BATCH_SIZE if use_global_ids else TRANSLATION_BATCH_SIZE
    if context_window is None:
        context_window = INDEX_CONTEXT_BEFORE if use_global_ids else CONTEXT_WINDOW

    # Allow per-call API key override（Index 不需要真 key，跳过）
    if api_key and not use_global_ids:
        translator.api_key = api_key

    # 备用引擎（默认关闭 —— 只有用户主动打开才会去调用云端 API）
    fallback = None
    if FALLBACK_ENABLED and FALLBACK_PROVIDER and FALLBACK_PROVIDER != (
            (provider or TRANSLATION_PROVIDER).lower().strip()):
        try:
            fallback = create_translator(FALLBACK_PROVIDER)
        except Exception:
            fallback = None

    total = len(segments)
    translated_segments = []
    context: list[dict] = []  # rolling window of previous (source, translated, speaker)

    batch_count = 0
    failed_batches = 0
    consecutive_failures = 0
    first_error: Optional[str] = None
    used_fallback = 0

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = segments[batch_start:batch_end]
        batch_ids = list(range(batch_start, batch_end))
        batch_count += 1

        if progress_callback:
            progress = batch_start / total
            progress_callback(progress, f"翻译中 ({translator.name})... {batch_start}/{total}")

        ctx_before = context if context_window > 0 else None
        # 后文只给模型看、不要求它翻（仅 Index 走这套 prompt）
        ctx_after = None
        if use_global_ids and INDEX_CONTEXT_AFTER > 0:
            ctx_after = segments[batch_end:batch_end + INDEX_CONTEXT_AFTER] or None

        # 本批真正命中的术语（未命中不注入）
        glossary_pairs = {}
        if use_global_ids and INDEX_GLOSSARY_ENABLED:
            glossary_pairs = build_glossary_pairs("\n".join(s["text"] for s in batch))

        # prompt 里的 id 空间：Index 用**全局 id**（便于逐条核对），
        # 其余后端沿用块内下标 0..n-1 —— 与它们原有的 prompt 格式一致。
        prompt_ids = batch_ids if use_global_ids else list(range(len(batch)))
        sources = {pid: seg["text"] for pid, seg in zip(prompt_ids, batch)}

        try:
            by_pos, res = _translate_chunk(
                translator, batch, prompt_ids, sources,
                ctx_before, ctx_after, glossary_pairs, allow_shrink=True)

            # 本批仍有缺口 → 尝试备用引擎（仅当用户显式开启）
            missing_pos = [p for p in range(len(batch)) if p not in by_pos]
            if missing_pos and fallback is not None:
                fb_chunk = [batch[p] for p in missing_pos]
                fb_sources = {i: seg["text"] for i, seg in enumerate(fb_chunk)}
                fb_map, _ = _translate_chunk(
                    fallback, fb_chunk, list(range(len(fb_chunk))), fb_sources,
                    None, None, {}, allow_shrink=False)
                if fb_map:
                    for i, text in fb_map.items():
                        by_pos[missing_pos[i]] = text
                    used_fallback += len(fb_map)

            if not by_pos:
                raise RuntimeError(
                    f"该批校验未通过且无可用译文：{res.summary() if res else '解析为空'}")

            consecutive_failures = 0
            for pos, seg in enumerate(batch):
                seg_copy = dict(seg)
                seg_copy["translated"] = by_pos.get(pos, "")
                # 显式标记「没失败」：前端据此判断，不再去匹配译文里的字符串
                seg_copy["failed"] = not bool(seg_copy["translated"])
                translated_segments.append(seg_copy)

        except Exception as e:
            failed_batches += 1
            consecutive_failures += 1
            if first_error is None:
                first_error = f"{type(e).__name__}: {e}"
            for seg in batch:
                translated_segments.append(_mark_failed(seg, e))

            # 连续多批失败 = 确定性问题（服务没起 / key 无效 / 断网），
            # 再往下跑只是让用户干等。提前中止并说明原因。
            if consecutive_failures >= EARLY_ABORT_AFTER:
                hint = (
                    f"  请确认本地 Index-Translate 服务已启动（{INDEX_BASE_URL}），"
                    f"或改用其它引擎。\n"
                    if use_global_ids else
                    f"  请检查网页「API Key」输入框，或 .env 中的配置后重试。\n"
                )
                raise RuntimeError(
                    f"翻译连续 {consecutive_failures} 批失败，已提前中止"
                    f"（共 {batch_count} 批，还有 "
                    f"{max(0, (total - batch_end + batch_size - 1) // batch_size)} 批未尝试）。\n"
                    f"  最后一个错误：{first_error}\n"
                    f"  最常见原因：API Key 无效 / 过期 / 余额不足，"
                    f"或网络无法访问 {translator.name}。\n"
                    + hint
                )

        # 把刚翻好的这批作为下一批的上文
        if context_window > 0:
            context.extend(translated_segments[-len(batch):])
            context = context[-context_window:]

    # 全部批次都失败 → 这一趟没有任何可用产出。必须让任务明确报错，
    # 否则会「成功」结束并输出一堆 [翻译失败: ...] 的字幕，用户很难察觉。
    if batch_count and failed_batches == batch_count:
        raise RuntimeError(
            f"翻译全部失败（{failed_batches}/{batch_count} 批）—— {first_error}\n"
            f"  最常见原因：API Key 无效 / 过期 / 余额不足，或网络无法访问 "
            f"{translator.name}。\n"
            f"  请检查网页「API Key」输入框，或 .env 中的配置后重试。"
        )

    if progress_callback:
        extra = f"，其中 {used_fallback} 条由备用引擎 {FALLBACK_PROVIDER} 完成" if used_fallback else ""
        if failed_batches:
            progress_callback(
                1.0,
                f"翻译完成，共 {total} 条字幕（{failed_batches}/{batch_count} 批失败：{first_error}）{extra}",
            )
        else:
            progress_callback(1.0, f"翻译完成，共 {total} 条字幕{extra}")

    return translated_segments


def review(
    segments: list[dict],
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    batch_size: Optional[int] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    context_window: Optional[int] = None,
) -> list[dict]:
    """
    Post-translation LLM coherence check.

    Re-reads each batch with its Japanese source + draft Chinese, plus a rolling
    window of already-reviewed translations, and asks the LLM to fix contextual
    incoherence (pronoun/subject reference, term consistency) and translationese.

    This stage is non-destructive: on any API/parse failure it keeps the original
    draft translation for the affected batch rather than losing it.

    Args:
        segments: Segment dicts with 'text' (Japanese) and 'translated' (draft Chinese).
        provider: Review engine ("deepseek"/"anthropic"). Defaults to config.
        api_key: API key override.
        batch_size: Segments per review API call. Defaults to REVIEW_BATCH_SIZE.
        progress_callback: Optional callback(progress: float 0-1, status: str).
        context_window: Number of previous reviewed segments carried as context.
                        Defaults to CONTEXT_WINDOW; 0 disables.

    Returns:
        Segment dicts with 'translated' replaced by the reviewed Chinese.
    """
    if batch_size is None:
        batch_size = REVIEW_BATCH_SIZE
    if context_window is None:
        context_window = CONTEXT_WINDOW

    translator = create_translator(provider)
    use_global_ids = _uses_global_ids(translator)

    if api_key and not use_global_ids:
        translator.api_key = api_key

    total = len(segments)
    reviewed_segments: list[dict] = []
    context: list[dict] = []  # rolling window of already-reviewed segments

    batch_count = 0
    failed_batches = 0
    first_error: Optional[str] = None

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = segments[batch_start:batch_end]
        batch_ids = list(range(batch_start, batch_end))
        batch_count += 1

        if progress_callback:
            progress = batch_start / total
            progress_callback(progress, f"连贯性校对中 ({translator.name})... {batch_start}/{total}")

        ctx = context if context_window > 0 else None
        prompt = _review_prompt_for(translator, batch, batch_ids, ctx)

        try:
            # Index 按官方默认贪心解码（temperature=None → 用后端默认 0）；
            # 云端后端保持原来的 0.2，行为不变。
            content = translator.translate_batch(
                prompt,
                system=REVIEW_SYSTEM_PROMPT,
                temperature=(None if use_global_ids else 0.2),
            )
            parsed = _parse_translation_response(content, len(batch))
            mapped = _map_by_id(parsed, batch_ids, len(batch))

            for pos, seg in enumerate(batch):
                seg_copy = dict(seg)
                new_text = (mapped.get(pos) or "").strip()
                if new_text:
                    seg_copy["translated"] = new_text
                # 空校对结果 → 保留初译，避免丢失
                reviewed_segments.append(seg_copy)

        except Exception as e:
            # 校对失败不致命：保留初译，继续后续批次。但要记下来，
            # 否则「校对完成」会让用户以为这步真的生效了。
            failed_batches += 1
            if first_error is None:
                first_error = f"{type(e).__name__}: {e}"
            reviewed_segments.extend(dict(seg) for seg in batch)

        if context_window > 0:
            context.extend(reviewed_segments[-len(batch):])
            context = context[-context_window:]

    if progress_callback:
        if failed_batches:
            progress_callback(
                1.0,
                f"连贯性校对结束，共 {total} 条字幕"
                f"（{failed_batches}/{batch_count} 批失败，已保留初译：{first_error}）",
            )
        else:
            progress_callback(1.0, f"连贯性校对完成，共 {total} 条字幕")

    return reviewed_segments
