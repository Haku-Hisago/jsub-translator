"""
Processing pipeline for jsub-translator.
Orchestrates video download, audio extraction, transcription,
translation, and ASS subtitle generation.
"""

import tempfile
import shutil
import uuid
from pathlib import Path
from typing import Optional, Callable, Generator

from .config import (
    OUTPUT_DIR, WHISPER_MODEL, WHISPER_REFERENCE_MODELS, TRANSLATION_PROVIDER,
    CONTEXT_WINDOW, ENABLE_REVIEW, REVIEW_PROVIDER, FUSE_PROVIDER,
    ensure_dirs, validate_config,
)
from .downloader import resolve_video_input, is_url
from .audio import extract_audio
from .transcriber import transcribe_multi
from .fusion import fuse_transcripts
from .translator import translate, review
from .diarizer import diarize
from .ass_writer import generate_all_formats


def process(
    input_source: str,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    enable_diarization: bool = True,
    num_speakers: Optional[int] = None,
    enable_context: bool = True,
    enable_review: Optional[bool] = None,
    progress_callback: Optional[Callable[[float, str, str], None]] = None,
    cleanup_temp: bool = True,
) -> dict:
    """
    Run the full subtitle translation pipeline.

    Args:
        input_source: Video URL or local file path.
        provider: Translation provider ("deepseek" / "anthropic").
                  Defaults to config TRANSLATION_PROVIDER.
        api_key: Runtime API key for `provider` (e.g. typed into the web UI).
                 Takes precedence over the provider's environment variable.
                 Only applied to the fusion/review passes when their engine
                 (FUSE_PROVIDER / REVIEW_PROVIDER) resolves to the same
                 provider — otherwise those fall back to their own env key.
        enable_diarization: Whether to attempt speaker diarization.
        num_speakers: Known speaker count for diarization (None = auto cluster).
        enable_context: Whether to carry cross-batch context (上文语境) during
                        fusion, translation, and review.
        enable_review: Whether to run the post-translation LLM coherence check.
                       Defaults to config ENABLE_REVIEW.
        progress_callback: Optional callback(progress: float 0-1,
                            stage: str, message: str).
        cleanup_temp: Whether to delete temporary files after processing.

    Returns:
        Dict with keys:
            - title: Video title
            - segments: List of translated subtitle segments
            - files: Dict of format → Path for generated ASS files
            - speakers: Set of detected speaker labels
    """
    if provider is None:
        provider = TRANSLATION_PROVIDER
    if enable_review is None:
        enable_review = ENABLE_REVIEW

    # Validate configuration (API keys, etc.)
    validate_config(provider=provider, api_key=api_key)
    ensure_dirs()

    # Unique run ID for temp files
    run_id = uuid.uuid4().hex[:8]
    temp_dir = Path(tempfile.gettempdir()) / f"jsub_{run_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    def report(stage: str, message: str, progress: float = -1):
        """Helper to call progress_callback if set."""
        if progress_callback:
            progress_callback(progress, stage, message)

    try:
        # --- Stage 1: Download / Locate Video ---
        report("download", "正在获取视频...", 0.0)
        # 直接下到 OUTPUT_DIR（而不是 OUTPUT_DIR/videos）：
        #   · 「原视频下载链接」和「缓存清理」都只看 OUTPUT_DIR 顶层，
        #     下到子目录还得再复制一份过去 —— 等于每个视频存两份，
        #     而且 videos/ 那份永远不在清理范围内（静默占空间）。
        #   · 本地文件路径不受影响（resolve_video_input 原样返回），
        #     仍由 app._run_job 复制进 OUTPUT_DIR 供下载。
        video_path = resolve_video_input(
            input_source,
            output_dir=OUTPUT_DIR,
            progress_callback=lambda p, msg: report("download", msg, 0.05 * p),
        )
        title = video_path.stem
        report("download", "视频就绪", 0.05)

        # --- Stage 2: Extract Audio ---
        report("audio", "正在提取音频...", 0.05)
        audio_path = extract_audio(
            video_path,
            output_dir=temp_dir / "audio",
            progress_callback=lambda p, msg: report("audio", msg, 0.05 + 0.10 * p),
        )
        report("audio", "音频提取完成", 0.15)

        # --- Stage 3: Transcribe (multi-ASR, parallel) ---
        model_sizes = [WHISPER_MODEL] + WHISPER_REFERENCE_MODELS
        report("transcribe", f"并行加载 {len(model_sizes)} 个 Whisper 模型：{', '.join(model_sizes)}...", 0.15)
        transcripts, trans_errors = transcribe_multi(
            audio_path,
            model_sizes,
            progress_callback=lambda p, msg: report("transcribe", msg, 0.15 + 0.25 * p),
        )
        if not transcripts:
            detail = "; ".join(f"{m}: {e}" for m, e in trans_errors.items())
            raise RuntimeError(f"所有 Whisper 模型识别失败。{detail}")

        primary = transcripts.get(WHISPER_MODEL)
        primary_model = WHISPER_MODEL
        if primary is None:
            # 主模型失败则回退到第一个成功的参考模型
            primary_model = next(iter(transcripts))
            primary = transcripts[primary_model]
            report("transcribe", f"主模型 {WHISPER_MODEL} 失败，回退到 {primary_model}", 0.40)

        reference_transcripts = [v for k, v in transcripts.items() if k != primary_model]
        report("transcribe", f"语音识别完成：{len(transcripts)}/{len(model_sizes)} 个模型成功", 0.40)

        # --- Stage 3.5: Multi-ASR fusion (LLM cross-check) ---
        fuse_provider = FUSE_PROVIDER or provider
        report("fuse", f"正在用大模型比对融合多路转写 (引擎: {fuse_provider})...", 0.40)
        segments = fuse_transcripts(
            primary,
            reference_transcripts,
            provider=fuse_provider,
            api_key=(api_key if fuse_provider == provider else None),
            progress_callback=lambda p, msg: report("fuse", msg, 0.40 + 0.05 * p),
            context_window=(CONTEXT_WINDOW if enable_context else 0),
        )
        # 注意：融合/翻译/校对三个阶段各自的收尾回调已经落在相同的总进度上，
        # 并且会区分「完成」与「部分失败」。这里不再补一条笼统的「完成」，
        # 否则会把刚刚发出的失败信息覆盖掉。

        # --- Stage 4: Speaker Diarization ---
        # 在翻译之前完成：说话人身份会随 segment 传给翻译/校对，
        # 让 LLM 按角色统一人称、敬语等级与称谓（见 translator.SYSTEM_PROMPT）。
        speakers = set()
        if enable_diarization:
            report("diarize", "正在识别说话人...", 0.45)
            segments = diarize(
                audio_path,
                segments,
                num_speakers=num_speakers,
                progress_callback=lambda p, msg: report("diarize", msg, 0.45 + 0.10 * max(p, 0.0)),
            )
            speakers = set(s.get("speaker", "") for s in segments if s.get("speaker"))
            report(
                "diarize",
                f"说话人识别完成：{len(speakers)} 位说话人" if speakers else "未识别出说话人信息",
                0.55,
            )
        else:
            # Ensure all segments have speaker field
            for s in segments:
                s["speaker"] = ""

        # --- Stage 5: Translate ---
        report("translate", f"正在翻译 {len(segments)} 条字幕 (引擎: {provider})...", 0.55)
        translated = translate(
            segments,
            provider=provider,
            api_key=api_key,
            progress_callback=lambda p, msg: report("translate", msg, 0.55 + 0.20 * p),
            context_window=(CONTEXT_WINDOW if enable_context else 0),
        )
        # Preserve speaker info in translated segments
        for i, seg in enumerate(translated):
            if i < len(segments):
                seg["speaker"] = segments[i].get("speaker", "")

        # --- Stage 5.5: Contextual coherence review (LLM check) ---
        if enable_review:
            review_provider = REVIEW_PROVIDER or provider
            report("review", f"正在进行上下文连贯性校对 (引擎: {review_provider})...", 0.75)
            translated = review(
                translated,
                provider=review_provider,
                api_key=(api_key if review_provider == provider else None),
                progress_callback=lambda p, msg: report("review", msg, 0.75 + 0.12 * p),
                context_window=(CONTEXT_WINDOW if enable_context else 0),
            )
        else:
            report("review", "已跳过连贯性校对", 0.87)

        # --- Stage 6: Generate ASS Files ---
        report("ass", "正在生成 ASS 字幕文件...", 0.87)
        ass_files = generate_all_formats(translated, title=title)
        report("ass", "字幕文件生成完成", 0.95)

        # --- Done ---
        report("done", "全部完成！", 1.0)

        return {
            "title": title,
            "video_path": video_path,
            "segments": translated,
            "files": ass_files,
            "speakers": speakers,
        }

    finally:
        # Cleanup temp files (audio only — video is saved to output dir)
        if cleanup_temp and temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass  # Best-effort cleanup
