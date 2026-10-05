"""
Multi-ASR transcript fusion via LLM.

Several Whisper models transcribe the same audio independently; their outputs
disagree on homophones, particles, segmentation, and omissions. This module uses
an LLM to cross-check those transcripts and merge them into a single, contextually
coherent Japanese transcript. The primary model's segmentation/timestamps are
preserved — only the Japanese text is corrected.
"""

import json
from typing import Optional, Callable

from .config import FUSE_BATCH_SIZE, CONTEXT_WINDOW
from .translator import create_translator, _parse_translation_response


FUSE_SYSTEM_PROMPT = """你是日语字幕的「多路语音识别校对员」。多个 Whisper 模型对同一段音频各自转写，结果互有出入（同音词、助词、断句、漏字等）。请以「主转录」为骨架，参考其它模型的转写，逐条输出校正后的日文字幕。

校对原则：
1. 保持主转录的条数与顺序不变，每条恰好对应一条输出
2. 每个元素的 "refs" 是其它模型在相同时段的转写，用于交叉验证：若主转录有同音/漏字/断句错误，参照 refs 修正；若 refs 本身明显错误则忽略
3. 让相邻句子的语境连贯（指代、省略、敬语、语气），补全口语中省略的主语/助词
4. 保持口语原貌，不要改写为书面语，不要增删语义
5. 若主转录某条已正确，则原样保留

输入：JSON 数组，每个元素 {"id","text"(主转录),"refs"(参考转写列表,可选)}
输出：JSON 数组，每个元素 {"id","text"(校正后日文)}，保持 id 与顺序一致，条数相同"""


def _overlap_refs(seg: dict, transcripts: list[list[dict]], margin: float = 0.3) -> list[str]:
    """Collect reference text from other transcripts that overlaps this segment's time span."""
    refs = []
    s0 = seg["start"] - margin
    e0 = seg["end"] + margin
    for transcript in transcripts:
        pieces = [x["text"] for x in transcript if x["end"] >= s0 and x["start"] <= e0]
        if pieces:
            refs.append(" ".join(pieces))
    return refs


def _build_fuse_prompt(
    batch: list[dict],
    transcripts: list[list[dict]],
    context: list[dict] = None,
) -> str:
    """Build the fusion prompt: primary segments + per-segment reference text."""
    items = []
    for i, seg in enumerate(batch):
        item = {"id": i, "text": seg["text"]}
        refs = _overlap_refs(seg, transcripts)
        if refs:
            item["refs"] = refs
        items.append(item)
    prompt = json.dumps(items, ensure_ascii=False, indent=2)

    parts = []
    if context:
        ctx_lines = "\n".join(c["text"] for c in context)
        parts.append(f"上文（已融合完成的日文字幕，用于保持语境连贯）：\n{ctx_lines}")
    parts.append(prompt)
    return "\n\n".join(parts)


def fuse_transcripts(
    primary: list[dict],
    reference_transcripts: list[list[dict]],
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    batch_size: Optional[int] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    context_window: Optional[int] = None,
) -> list[dict]:
    """
    Cross-check and merge multiple ASR transcripts into one coherent Japanese transcript.

    The primary transcript's segments (with start/end timestamps) are the skeleton;
    the LLM corrects each line's Japanese text using the other transcripts as reference,
    threading context across batches so the merged transcript reads fluently.

    Non-destructive: on any API/parse failure a batch keeps its primary text.

    Args:
        primary: Primary transcript segments (dicts with 'start', 'end', 'text').
        reference_transcripts: List of secondary transcripts, each a list of
                               segment dicts with 'start', 'end', 'text'.
        provider: LLM engine ("deepseek"/"anthropic"). Defaults to config.
        api_key: API key override.
        batch_size: Segments per fusion API call. Defaults to FUSE_BATCH_SIZE.
        progress_callback: Optional callback(progress: float 0-1, status: str).
        context_window: Rolling context size. Defaults to CONTEXT_WINDOW; 0 disables.

    Returns:
        Primary segments with 'text' replaced by the fused/corrected Japanese.
    """
    if batch_size is None:
        batch_size = FUSE_BATCH_SIZE
    if context_window is None:
        context_window = CONTEXT_WINDOW

    translator = create_translator(provider)
    if api_key:
        translator.api_key = api_key

    total = len(primary)
    fused: list[dict] = []
    context: list[dict] = []

    batch_count = 0
    failed_batches = 0
    first_error: Optional[str] = None

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = primary[batch_start:batch_end]
        batch_count += 1

        if progress_callback:
            progress = batch_start / total
            progress_callback(progress, f"多路转写融合中 ({translator.name})... {batch_start}/{total}")

        prompt = _build_fuse_prompt(
            batch,
            reference_transcripts,
            context if context_window > 0 else None,
        )

        try:
            content = translator.translate_batch(
                prompt,
                system=FUSE_SYSTEM_PROMPT,
                temperature=0.1,
            )
            corrections = _parse_translation_response(content, len(batch))

            for i, seg in enumerate(batch):
                seg_copy = dict(seg)
                if i < len(corrections):
                    new_text = (corrections[i].get("text") or "").strip()
                    if new_text:
                        seg_copy["text"] = new_text
                fused.append(seg_copy)

        except Exception as e:
            # 融合失败不致命：保留主转录原文，继续后续批次。但要记下来，
            # 否则「融合完成」会让用户以为多模型交叉验证真的生效了。
            failed_batches += 1
            if first_error is None:
                first_error = f"{type(e).__name__}: {e}"
            fused.extend(dict(seg) for seg in batch)

        if context_window > 0:
            context.extend(fused[-len(batch):])
            context = context[-context_window:]

    if progress_callback:
        if failed_batches:
            progress_callback(
                1.0,
                f"多路转写融合结束，共 {total} 条字幕"
                f"（{failed_batches}/{batch_count} 批失败，已保留主转录原文：{first_error}）",
            )
        else:
            progress_callback(1.0, f"多路转写融合完成，共 {total} 条字幕")

    return fused
