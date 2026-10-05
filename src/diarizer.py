"""
Speaker diarization — assign each transcription segment to a speaker.

Why this is a separate stage
----------------------------
Whisper (and every other ASR model we use) has **no speaker-awareness**: it is
trained to output text and timestamps, not "who said it". There is no open
Japanese model that produces speaker labels directly, so the standard
architecture is two passes followed by a time-axis alignment:

    audio ──┬─→ Whisper        ──→ text + timestamps   (what was said)
            └─→ speaker model  ──→ speaker turns       (who said it)
                        ↓
                  align on the time axis ──→ "SPEAKER_00: こんにちは"

Backends
--------
``sherpa``   (default) sherpa-onnx — pure ONNX Runtime, CPU only.
             Uses the *same* pyannote ``segmentation-3.0`` model (ONNX export)
             for segmentation plus a 3D-Speaker ERes2Net embedding model.
             ~20 MB of new deps + ~44 MB of weights, and crucially **no torch**
             (torch would add ~3 GB and a second cuBLAS 12 that conflicts with
             the hand-placed ``cublas64_12.dll`` in ``site-packages/ctranslate2``
             — the documented cause of this project's CUDA loader deadlock).

``pyannote`` pyannote.audio — optional, heavier, needs a HuggingFace token.
             Kept working for both the v3 (``use_auth_token`` + ``itertracks``)
             and v4 (``token`` + ``DiarizeOutput``) APIs.

``auto``     Try sherpa, then pyannote.

Failure is always reported through ``progress_callback`` — never silently
swallowed, so a run that degrades to "single speaker" says why.
"""

import os
from pathlib import Path
from typing import Optional, Callable

from .config import (
    MODEL_DIR,
    DIARIZE_BACKEND,
    DIARIZE_SEG_MODEL,
    DIARIZE_EMB_MODEL,
    DIARIZE_NUM_SPEAKERS,
    DIARIZE_CLUSTER_THRESHOLD,
    DIARIZE_SPLIT_ON_CHANGE,
)

# 说话人配色不在这里定义 —— 权威定义在 ass_writer.SPEAKER_COLORS /
# SPEAKER_CSS_COLORS（用 md5 取色，稳定可复现）。
#
# 这里原本有一份副本，且有两个问题，已删除：
#   1. 它是旧版配色，**首位是纯白** —— 而 ass_writer 特意避开纯白
#      （某个说话人分到白色就等于在分色版本里看不出区别）
#   2. 它用内置 hash() 取色，而 Python 对字符串的 hash 是**每进程随机化**的
#      （除非设了 PYTHONHASHSEED），同一个说话人每次运行颜色都不同 ——
#      尽管它的 docstring 写着 "consistent"。
# 当时没有任何代码引用它，属于死代码；留着只会被误用。
# 需要配色请从 ass_writer 导入。


def _get_hf_token() -> Optional[str]:
    """Get HuggingFace token from environment."""
    return os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")


# ---------------------------------------------------------------------------
# Audio loading
# ---------------------------------------------------------------------------

def _load_audio_16k(audio_path: Path):
    """Load an audio file as mono float32 at 16 kHz.

    Uses ``soundfile`` when available and falls back to PyAV (which the project
    already bundles for the transcription stage). Returns ``(samples, rate)``.
    """
    import numpy as np

    try:
        import soundfile as sf

        samples, rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
        samples = samples[:, 0]  # first channel only
        if rate != 16000:
            samples = _resample_linear(samples, rate, 16000)
            rate = 16000
        return np.ascontiguousarray(samples, dtype=np.float32), rate
    except ImportError:
        pass

    # PyAV fallback
    import av
    import numpy as np

    with av.open(str(audio_path)) as container:
        stream = container.streams.audio[0]
        chunks = []
        rate = stream.rate
        for frame in container.decode(stream):
            arr = frame.to_ndarray()
            # PyAV gives (channels, samples) for planar audio
            if arr.ndim > 1:
                arr = arr[0]
            chunks.append(arr)
    samples = np.concatenate(chunks).astype(np.float32)
    if rate != 16000:
        samples = _resample_linear(samples, rate, 16000)
        rate = 16000
    return np.ascontiguousarray(samples, dtype=np.float32), rate


def _resample_linear(samples, src_rate: int, dst_rate: int):
    """Cheap linear resampling — adequate for diarization embeddings."""
    import numpy as np

    if src_rate == dst_rate:
        return samples
    n_dst = int(round(len(samples) * dst_rate / src_rate))
    src_idx = np.arange(len(samples), dtype=np.float64)
    dst_idx = np.linspace(0, len(samples) - 1, n_dst, dtype=np.float64)
    return np.interp(dst_idx, src_idx, samples).astype(np.float32)


# ---------------------------------------------------------------------------
# Backend: sherpa-onnx
# ---------------------------------------------------------------------------

def _diarize_sherpa(
    audio_path: Path,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    num_speakers: Optional[int] = None,
    cluster_threshold: Optional[float] = None,
) -> list[tuple[float, float, str]]:
    """Run sherpa-onnx diarization. Returns [(start, end, speaker), ...].

    Raises on any failure so the caller can report it or fall through to
    another backend.
    """
    import sherpa_onnx

    seg_model = str(DIARIZE_SEG_MODEL)
    emb_model = str(DIARIZE_EMB_MODEL)

    for label, path in (("分割模型", seg_model), ("声纹模型", emb_model)):
        if not Path(path).exists():
            raise FileNotFoundError(f"{label}不存在: {path}")

    if progress_callback:
        progress_callback(0.05, "加载说话人识别模型...")

    if num_speakers is None:
        num_speakers = DIARIZE_NUM_SPEAKERS
    if cluster_threshold is None:
        cluster_threshold = DIARIZE_CLUSTER_THRESHOLD

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=seg_model, window_shift_ratio=0.1
            ),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=emb_model),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers, threshold=cluster_threshold
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError(
            "说话人识别配置校验失败，请确认分割模型与声纹模型文件完整"
        )

    engine = sherpa_onnx.OfflineSpeakerDiarization(config)

    if progress_callback:
        progress_callback(0.15, "正在分析说话人...")

    samples, rate = _load_audio_16k(audio_path)
    if rate != engine.sample_rate:
        samples = _resample_linear(samples, rate, engine.sample_rate)

    # sherpa reports progress as (processed_chunk, total_chunk); the callback
    # must return 0 to continue. Throttled: it fires per chunk (hundreds of
    # times per file) and each call crosses into the Flask SSE progress queue.
    _state = {"last": -1.0}

    def _on_progress(processed: int, total: int) -> int:
        if progress_callback and total > 0:
            frac = 0.15 + 0.75 * (processed / total)
            # Only emit when the reported percentage actually moves.
            pct = round(min(frac, 0.90) * 100)
            if pct != _state["last"]:
                _state["last"] = pct
                progress_callback(min(frac, 0.90), "正在分析说话人...")
        return 0

    result = engine.process(samples, callback=_on_progress).sort_by_start_time()

    turns = [(r.start, r.end, f"SPEAKER_{r.speaker:02d}") for r in result]
    if progress_callback:
        n = len(set(t[2] for t in turns))
        progress_callback(0.95, f"识别出 {n} 位说话人")
    return turns


# ---------------------------------------------------------------------------
# Backend: pyannote.audio (optional)
# ---------------------------------------------------------------------------

def _diarize_pyannote(
    audio_path: Path,
    hf_token: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> list[tuple[float, float, str]]:
    """Run pyannote.audio diarization, compatible with both the v3 and v4 APIs.

    v4 changed three things we depend on:
      * ``use_auth_token=`` → ``token=``
      * returns a ``DiarizeOutput`` dataclass instead of a bare ``Annotation``
      * iteration yields ``(turn, speaker)`` instead of
        ``itertracks(yield_label=True)``

    Raises on any failure.
    """
    token = hf_token or _get_hf_token()
    if not token:
        raise RuntimeError("pyannote 后端需要 HuggingFace Token（HF_TOKEN）")

    from pyannote.audio import Pipeline

    model_name = os.getenv(
        "DIARIZE_PYANNOTE_MODEL", "pyannote/speaker-diarization-community-1"
    )

    if progress_callback:
        progress_callback(0.05, "加载 pyannote 说话人模型...")

    try:
        pipeline = Pipeline.from_pretrained(model_name, token=token)
    except TypeError:
        # pyannote <= 3.x used use_auth_token
        pipeline = Pipeline.from_pretrained(model_name, use_auth_token=token)

    try:
        import torch

        if torch.cuda.is_available():
            pipeline.to(torch.device("cuda"))
    except ImportError:
        pass  # torch is a pyannote dependency; if absent the pipeline is CPU-only

    if progress_callback:
        progress_callback(0.15, "正在分析说话人...")

    output = pipeline(str(audio_path))

    # v4 returns DiarizeOutput; v3 returns a bare Annotation.
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", None)
    if annotation is None:
        annotation = output  # v3 Annotation

    turns: list[tuple[float, float, str]] = []
    try:
        # v4: Annotation supports direct (turn, speaker) iteration.
        for turn, speaker in annotation:
            turns.append((turn.start, turn.end, str(speaker)))
    except (TypeError, ValueError):
        # v3 fallback
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            turns.append((turn.start, turn.end, str(speaker)))

    if progress_callback:
        n = len(set(t[2] for t in turns))
        progress_callback(0.95, f"识别出 {n} 位说话人")
    return turns


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diarize(
    audio_path: Path,
    segments: list[dict],
    hf_token: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    backend: Optional[str] = None,
    num_speakers: Optional[int] = None,
    cluster_threshold: Optional[float] = None,
) -> list[dict]:
    """
    Assign speaker labels to transcription segments.

    Args:
        audio_path: Path to the audio file.
        segments: Transcription segments from faster-whisper. Each has
                  'start', 'end', 'text', and optionally 'words' (word-level
                  timestamps, used for mid-segment speaker splitting).
        hf_token: HuggingFace token — only needed by the 'pyannote' backend.
        progress_callback: Optional progress callback(progress, message).
        backend: Override for DIARIZE_BACKEND ("sherpa"/"pyannote"/"auto"/"none").
        num_speakers: Known speaker count (-1 or None = auto clustering).
        cluster_threshold: Override for DIARIZE_CLUSTER_THRESHOLD.

    Returns:
        Segments with an added 'speaker' field (e.g. 'SPEAKER_00').
        When diarization is unavailable every segment gets 'speaker': ''
        — and the reason is reported via progress_callback, never hidden.
    """
    backend = (backend or DIARIZE_BACKEND or "sherpa").lower()

    if backend == "none":
        if progress_callback:
            progress_callback(0.5, "说话人识别已关闭")
        return _assign_single_speaker(segments)

    candidates = {
        "sherpa": ["sherpa"],
        "pyannote": ["pyannote"],
        "auto": ["sherpa", "pyannote"],
    }.get(backend, ["sherpa"])

    turns: Optional[list[tuple[float, float, str]]] = None
    failures: list[str] = []

    for name in candidates:
        try:
            if name == "sherpa":
                turns = _diarize_sherpa(
                    audio_path,
                    progress_callback=progress_callback,
                    num_speakers=num_speakers,
                    cluster_threshold=cluster_threshold,
                )
            else:
                turns = _diarize_pyannote(
                    audio_path, hf_token, progress_callback=progress_callback
                )
            break
        except Exception as e:  # noqa: BLE001 — report any backend failure
            failures.append(f"{name}: {type(e).__name__}: {e}")

    if turns is None:
        detail = "; ".join(failures) if failures else "没有可用后端"
        if progress_callback:
            progress_callback(0.5, f"说话人识别跳过（{detail}）")
        return _assign_single_speaker(segments)

    if not turns:
        if progress_callback:
            progress_callback(0.5, "未检测到任何说话人片段，按单说话人处理")
        return _assign_single_speaker(segments)

    # Assign each segment to the speaker with the most temporal overlap.
    # Segments that overlap no speaker turn (a pause, music, or a stretch the
    # diarizer treated as non-speech) fall back to the nearest turn so the
    # label stays continuous.
    result: list[dict] = []
    for seg in segments:
        speaker = _find_best_speaker(seg["start"], seg["end"], turns)
        if not speaker:
            speaker = _nearest_speaker(seg["start"], seg["end"], turns)
        result.append({**seg, "speaker": speaker})

    # Optionally split segments that straddle a speaker change.
    if DIARIZE_SPLIT_ON_CHANGE:
        reliable, reason = _word_timestamps_reliable(result)
        if reliable:
            before = len(result)
            result = split_segments_by_speaker(result, turns)
            if progress_callback and len(result) != before:
                progress_callback(
                    -1, f"按说话人切分字幕：{before} → {len(result)} 条"
                )
        elif progress_callback:
            progress_callback(-1, f"不切分字幕（{reason}）")

    speakers = set(s["speaker"] for s in result if s.get("speaker"))
    if progress_callback:
        progress_callback(1.0, f"识别出 {len(speakers)} 位说话人")
    return result


def _find_best_speaker(
    seg_start: float, seg_end: float, speaker_turns: list
) -> str:
    """Find the speaker with the most overlap with this segment."""
    best_speaker = ""
    best_overlap = 0.0
    for t_start, t_end, speaker in speaker_turns:
        overlap = max(0.0, min(seg_end, t_end) - max(seg_start, t_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = speaker
    return best_speaker


def _nearest_speaker(
    seg_start: float, seg_end: float, speaker_turns: list, max_gap: float = 8.0
) -> str:
    """Speaker of the temporally closest turn, for segments that overlap none.

    Happens when a segment lands in a pause, in background music, or on a
    section the diarizer treated as non-speech. Falling back to the nearest
    speaker keeps the label continuous instead of leaving a hole — but only
    within ``max_gap`` seconds, so a genuinely isolated segment (or a file with
    long instrumental stretches) is not mislabelled from far away.
    """
    if not speaker_turns:
        return ""
    best_speaker = ""
    best_gap = None
    for t_start, t_end, speaker in speaker_turns:
        if t_end < seg_start:
            gap = seg_start - t_end
        elif t_start > seg_end:
            gap = t_start - seg_end
        else:
            gap = 0.0
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best_speaker = speaker
    if best_gap is not None and best_gap <= max_gap:
        return best_speaker
    return ""


# Word-level timestamps from ``large-v3-turbo`` come from DTW over a 4-layer
# decoder, so they are coarser than full ``large-v3``. If fewer than this
# fraction of segments have words that concatenate back to the segment text,
# the alignment is not trustworthy and mid-segment splitting is skipped.
WORD_ALIGNMENT_MIN_RATIO = 0.95


def _word_timestamps_reliable(segments: list[dict]) -> tuple[bool, str]:
    """Decide whether word-level timestamps can be trusted for splitting.

    Returns ``(reliable, reason)``. A segment's words are considered aligned
    when joining them reproduces the segment text.
    """
    total = 0
    aligned = 0
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            continue
        total += 1
        text = (seg.get("text") or "").strip()
        if "".join(w.get("word", "") for w in words).strip() == text:
            aligned += 1

    if total == 0:
        return False, "没有词级时间戳"

    ratio = aligned / total
    if ratio < WORD_ALIGNMENT_MIN_RATIO:
        return False, (
            f"词级时间戳对不齐（{aligned}/{total} = {ratio:.0%}，"
            f"需 ≥ {WORD_ALIGNMENT_MIN_RATIO:.0%}）"
        )
    return True, f"{aligned}/{total}"


def split_segments_by_speaker(
    segments: list[dict], speaker_turns: list
) -> list[dict]:
    """Split a segment into pieces when its words change speaker.

    Only applies when word timestamps are reliable overall (see
    ``_word_timestamps_reliable``); otherwise the segments pass through
    unchanged and each keeps the speaker of its dominant overlap.
    """
    out: list[dict] = []
    for seg in segments:
        words = seg.get("words") or []
        text = (seg.get("text") or "").strip()
        joined = "".join(w.get("word", "") for w in words).strip()

        if not words or joined != text:
            out.append(seg)
            continue

        # Walk the words and cut wherever the speaker changes.
        groups: list[list[dict]] = []
        current: list[dict] = []
        current_speaker = None

        for w in words:
            w_start = w.get("start", seg["start"])
            w_end = w.get("end", seg["end"])
            spk = _find_best_speaker(w_start, w_end, speaker_turns)
            if current and spk != current_speaker:
                groups.append(current)
                current = []
            current_speaker = spk
            current.append(w)

        if current:
            groups.append(current)

        if len(groups) <= 1:
            out.append(seg)
            continue

        for g in groups:
            piece_text = "".join(w.get("word", "") for w in g).strip()
            if not piece_text:
                continue
            g_start = g[0].get("start", seg["start"])
            g_end = g[-1].get("end", seg["end"])
            piece_speaker = _find_best_speaker(g_start, g_end, speaker_turns)
            if not piece_speaker:
                piece_speaker = _nearest_speaker(g_start, g_end, speaker_turns)
            out.append({
                **seg,
                "start": round(g_start, 2),
                "end": round(g_end, 2),
                "text": piece_text,
                # 落到静音间隙里的碎片常常量不出重叠，此时依次退到
                # 「最近的说话人」→「母段的归属」，避免出现没有说话人的碎片。
                "speaker": piece_speaker or seg.get("speaker", ""),
                "words": g,
            })
    return out


def _assign_single_speaker(segments: list[dict]) -> list[dict]:
    """Assign empty speaker to all segments (fallback)."""
    return [{**s, "speaker": ""} for s in segments]


# 取色函数同样不在这里 —— 见文件上方说明。用 ass_writer.get_speaker_color /
# get_speaker_css_color。原副本用 hash() 取色，同一说话人每次运行颜色都不同。
