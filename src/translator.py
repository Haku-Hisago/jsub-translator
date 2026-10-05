"""
LLM-based translation supporting multiple engines.
Currently supports: DeepSeek (default) and Anthropic Claude.

DeepSeek uses the OpenAI-compatible SDK for low-cost, high-quality translation.
Anthropic uses the native Messages API.
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
)
from .glossary import build_glossary_hint

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
    """
    Parse the LLM response text to extract translation JSON.
    Handles markdown code blocks and plain JSON.
    """
    content = content.strip()

    # Remove markdown code blocks if present
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        translations = json.loads(content)
        if isinstance(translations, list):
            return translations
    except json.JSONDecodeError:
        pass

    # Fallback: try to parse line by line
    results = []
    for line in content.split("\n"):
        line = line.strip()
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and "text" in obj:
                results.append(obj)
        except json.JSONDecodeError:
            continue

    return results


# ═══════════════════════════════════════════════════════════════
# Abstract Base
# ═══════════════════════════════════════════════════════════════

class BaseTranslator(ABC):
    """Abstract base for translation engines."""

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
        provider: "deepseek" or "anthropic". Defaults to config TRANSLATION_PROVIDER.

    Returns:
        BaseTranslator instance.
    """
    # 空串也要回退到默认引擎 —— 与 resolve_api_key / validate_config 保持一致。
    # 之前只有这里用 `is None` 判断，结果空串能过校验、却在建翻译器时炸掉。
    provider = (provider or TRANSLATION_PROVIDER).lower().strip()
    if provider == "deepseek":
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
        provider: "deepseek" or "anthropic". Defaults to config.
        api_key: API key override.
        batch_size: Segments per API call. Defaults to TRANSLATION_BATCH_SIZE.
        progress_callback: Optional callback(progress: float 0-1, status: str).
        context_window: Number of previous segments carried forward as 上文语境.
                        Defaults to CONTEXT_WINDOW; 0 disables context threading.

    Returns:
        List of segment dicts with added 'translated' key (Chinese text).
    """
    if batch_size is None:
        batch_size = TRANSLATION_BATCH_SIZE
    if context_window is None:
        context_window = CONTEXT_WINDOW

    translator = create_translator(provider)

    # Allow per-call API key override
    if api_key:
        translator.api_key = api_key

    total = len(segments)
    translated_segments = []
    context: list[dict] = []  # rolling window of previous (source, translated, speaker)

    batch_count = 0
    failed_batches = 0
    consecutive_failures = 0
    first_error: Optional[str] = None

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = segments[batch_start:batch_end]
        batch_count += 1

        if progress_callback:
            progress = batch_start / total
            progress_callback(progress, f"翻译中 ({translator.name})... {batch_start}/{total}")

        prompt = _build_translation_prompt(batch, context if context_window > 0 else None)

        try:
            content = translator.translate_batch(prompt)
            translations = _parse_translation_response(content, len(batch))
            consecutive_failures = 0

            for i, seg in enumerate(batch):
                seg_copy = dict(seg)
                if i < len(translations):
                    seg_copy["translated"] = translations[i].get("text", "")
                else:
                    seg_copy["translated"] = ""
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

            # 连续多批失败 = 确定性问题（key 无效 / 余额不足 / 断网），
            # 再往下跑只是让用户干等。提前中止并说明原因。
            if consecutive_failures >= EARLY_ABORT_AFTER:
                raise RuntimeError(
                    f"翻译连续 {consecutive_failures} 批失败，已提前中止"
                    f"（共 {batch_count} 批，还有 "
                    f"{max(0, (total - batch_end + batch_size - 1) // batch_size)} 批未尝试）。\n"
                    f"  最后一个错误：{first_error}\n"
                    f"  最常见原因：API Key 无效 / 过期 / 余额不足，"
                    f"或网络无法访问 {translator.name}。\n"
                    f"  请检查网页「API Key」输入框，或 .env 中的配置后重试。"
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
        if failed_batches:
            progress_callback(
                1.0,
                f"翻译完成，共 {total} 条字幕（{failed_batches}/{batch_count} 批失败：{first_error}）",
            )
        else:
            progress_callback(1.0, f"翻译完成，共 {total} 条字幕")

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

    if api_key:
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
        batch_count += 1

        if progress_callback:
            progress = batch_start / total
            progress_callback(progress, f"连贯性校对中 ({translator.name})... {batch_start}/{total}")

        prompt = _build_review_prompt(batch, context if context_window > 0 else None)

        try:
            content = translator.translate_batch(
                prompt,
                system=REVIEW_SYSTEM_PROMPT,
                temperature=0.2,
            )
            corrections = _parse_translation_response(content, len(batch))

            for i, seg in enumerate(batch):
                seg_copy = dict(seg)
                if i < len(corrections):
                    new_text = (corrections[i].get("text") or "").strip()
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
