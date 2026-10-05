"""
Speech recognition using faster-whisper.
Transcribes Japanese audio to text with timestamps.
"""

import os
import sys
import time
import threading
from pathlib import Path
from typing import Optional, Callable, Iterator

from faster_whisper import WhisperModel

from .config import (
    WHISPER_MODEL, WHISPER_REFERENCE_MODELS, MODEL_DIR, ROOT_DIR,
    SOURCE_LANG, DEVICE, COMPUTE_TYPE,
)


VALID_MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]


def _cuda_dll_search_dirs() -> list[Path]:
    """Every directory that may hold the CUDA runtime DLLs CTranslate2 needs."""
    dirs: list[Path] = []

    # 1. Alongside ctranslate2.dll. Older ctranslate2 wheels shipped cuBLAS /
    #    cuDART right here; newer ones (e.g. 4.8.2) ship only cuDNN. This is
    #    also where a manual drop-in copy goes.
    try:
        import ctranslate2
        dirs.append(Path(ctranslate2.__file__).resolve().parent)
    except Exception:  # noqa: BLE001 - best-effort probing
        pass

    # 2. Next to the running program — the EXE's folder, and the PyInstaller
    #    unpack dir when frozen.
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass))
    else:
        dirs.append(Path(sys.argv[0]).resolve().parent)

    # 3. The nvidia-* wheels (nvidia-cublas-cu12 and friends).
    try:
        import nvidia  # namespace package
        for root in list(getattr(nvidia, "__path__", [])):
            dirs.extend(p for p in Path(root).glob("*/bin") if p.is_dir())
    except ImportError:
        pass

    seen: set[Path] = set()
    unique: list[Path] = []
    for d in dirs:
        if d.is_dir() and d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def _register_cuda_dll_dirs() -> None:
    """Make the CUDA runtime CTranslate2 needs discoverable.

    CTranslate2 loads cuBLAS **dynamically at runtime** via ``LoadLibrary``,
    which uses the standard Windows search path. Its wheel ships cuDNN but
    *never* cuBLAS, so without this a GPU run dies with::

        RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

    Registering the candidate directories makes a bare ``LoadLibrary`` resolve,
    regardless of which of them the DLLs actually ended up in.
    """
    if os.name != "nt":
        return
    for d in _cuda_dll_search_dirs():
        try:
            os.add_dll_directory(str(d))
        except OSError:
            pass


_register_cuda_dll_dirs()


def get_model_size(model: str) -> str:
    """Validate and return the model size string."""
    if model not in VALID_MODELS:
        raise ValueError(f"Invalid model size '{model}'. Choose from: {VALID_MODELS}")
    return model


def _word_timestamps_enabled() -> bool:
    """Whether to ask Whisper for word-level timestamps.

    Needed by speaker diarization to split a subtitle line that straddles a
    speaker change. Costs a little extra time/memory, so it can be turned off
    with ``WHISPER_WORD_TIMESTAMPS=0`` — the rest of the pipeline works fine
    without it (diarization then assigns one speaker per whole segment).
    """
    return os.getenv("WHISPER_WORD_TIMESTAMPS", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _hf_cached_snapshots(model_size: str) -> list[Path]:
    """Local HuggingFace-cache snapshot dirs that hold a complete model.

    faster-whisper downloads into ``MODEL_DIR/models--<org>--faster-whisper-<size>/
    snapshots/<rev>/``. Those directories are directly loadable by CTranslate2.
    """
    found: list[Path] = []
    for repo in sorted(MODEL_DIR.glob(f"models--*--faster-whisper-{model_size}")):
        snap_dir = repo / "snapshots"
        if not snap_dir.is_dir():
            continue

        # Prefer the revision pinned in refs/main, then any other complete one.
        pinned = ""
        ref = repo / "refs" / "main"
        try:
            if ref.is_file():
                pinned = ref.read_text(encoding="utf-8").strip()
        except OSError:
            pinned = ""

        ordered: list[Path] = []
        if pinned and (snap_dir / pinned / "model.bin").is_file():
            ordered.append(snap_dir / pinned)
        ordered.extend(
            s for s in sorted(snap_dir.iterdir())
            if s not in ordered and (s / "model.bin").is_file()
        )
        found.extend(ordered)
    return found


def _resolve_model_path(model_size: str) -> str:
    """Return a local model directory if one exists, else the model name.

    Resolution order:
      1. ``MODEL_DIR/<size>-ct2/``           — explicit CTranslate2 export
      2. ``MODEL_DIR/models--*/snapshots/``  — HuggingFace Hub cache
      3. the bare model name                 — download from the Hub

    Returning a local *directory* makes faster-whisper load it in place and
    never contact the Hub, which is what enables fully offline operation.
    """
    local = MODEL_DIR / f"{model_size}-ct2"
    if (local / "model.bin").is_file():
        return str(local)

    cached = _hf_cached_snapshots(model_size)
    if cached:
        return str(cached[0])

    return model_size


def _local_model_sizes() -> list[str]:
    """Model sizes fully present in the local cache (CT2 export or Hub cache)."""
    return [
        size for size in VALID_MODELS
        if _hf_cached_snapshots(size) or (MODEL_DIR / f"{size}-ct2" / "model.bin").is_file()
    ]


def _candidate_model_dirs() -> list[Path]:
    """其它可能存放模型缓存的目录，用于诊断「路径配错」。

    典型场景：项目被移动/重命名后，.env 里写死的 MODEL_DIR 指向了不存在的
    旧路径，而模型其实还在磁盘上（只是换了地方）。这里扫几个常见位置，
    好把「正确的路径」直接告诉用户。
    """
    cands: list[Path] = []

    # 1) 项目根 / EXE 同目录下的 models/（正常默认位置）
    for base in (ROOT_DIR, Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT_DIR):
        cands.append(base / "models")

    # 2) 与当前（错误的）MODEL_DIR 同盘的兄弟目录 —— 覆盖「项目被改名/移动」
    try:
        parent = MODEL_DIR.parent
        if parent.is_dir():
            for sib in parent.iterdir():
                if sib.is_dir() and sib.name == "models":
                    cands.append(sib)
            # 项目目录被改名的情况：往上一层找
            grand = parent.parent
            if grand.is_dir():
                for sib in grand.iterdir():
                    if sib.is_dir():
                        cands.append(sib / "models")
    except OSError:
        pass

    # 去重，且排除当前 MODEL_DIR 本身
    out: list[Path] = []
    seen: set[str] = set()
    for c in cands:
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        if c.resolve() != MODEL_DIR.resolve():
            out.append(c)
    return out


def diagnose_model_dir() -> tuple[bool, str]:
    """启动时检查 MODEL_DIR 是否真的含有可用模型。

    移动/重命名项目后，`.env` 里写死的绝对路径会失效 —— 而这类错误是
    **静默**的：程序照常启动，直到识别阶段才发现「没有模型」，然后尝试
    联网下载，最后以连接超时告终（用户看到的是一大段看不懂的
    ConnectTimeout）。启动时就把问题指出来，能省掉一整轮无谓等待。

    Returns:
        (ok, message) —— `ok` 为 False 时 message 说明原因并尽量给出正确路径。
    """
    available = _local_model_sizes()
    needed = [WHISPER_MODEL] + WHISPER_REFERENCE_MODELS
    missing = [m for m in needed if m not in available]

    if not missing:
        return True, f"{MODEL_DIR}（可用：{', '.join(available)}）"

    if available:
        return False, (
            f"{MODEL_DIR} 缺少模型：{', '.join(missing)}"
            f"（已有：{', '.join(available)}）"
        )

    # 一个都没有 —— 极可能是路径配错，帮忙找找模型到底在哪
    msg = f"{MODEL_DIR} 下没有找到任何 Whisper 模型"
    if not MODEL_DIR.is_dir():
        msg += "（该目录不存在）"
    hints = [str(c) for c in _candidate_model_dirs() if _has_any_model(c)]
    if hints:
        msg += (
            "\n  -> 但在这些位置找到了模型，很可能是 .env 里的 MODEL_DIR 指错了：\n"
            + "\n".join(f"       {h}" for h in hints)
        )
    return False, msg


def _has_any_model(path: Path) -> bool:
    """该目录下是否有任何可用的 Whisper 模型（HF 缓存或 CT2 导出）。"""
    try:
        if any(path.glob("models--*--faster-whisper-*")):
            return True
        return any(path.glob("*-ct2/model.bin"))
    except OSError:
        return False


def _check_cuda_runtime() -> None:
    """Fail fast when the CUDA runtime is incomplete.

    CTranslate2 loads cuBLAS lazily, on the first ``encode()`` call — i.e. in
    the middle of transcription. A missing library therefore surfaces as a
    confusing mid-run error, or as a deadlock when several models are in
    flight. Probe it once up front instead, and explain how to fix it.
    """
    if DEVICE != "cuda":
        return

    import ctypes

    if os.name == "nt":
        libs, loader = ("cublas64_12.dll", "cublasLt64_12.dll"), ctypes.WinDLL
    else:
        libs, loader = ("libcublas.so.12", "libcublasLt.so.12"), ctypes.CDLL

    missing = []
    for lib in libs:
        try:
            loader(lib)
        except OSError:
            missing.append(lib)

    if missing:
        raise RuntimeError(
            f"CUDA 运行库不完整，缺少: {', '.join(missing)}。\n"
            f"  CTranslate2 只自带 cuDNN，不包含 cuBLAS，需要额外提供。\n"
            f"  解决办法（任选其一）：\n"
            f"    1. pip install nvidia-cublas-cu12 后重新打包（会自动打进 EXE）\n"
            f"    2. 手动把 cublas64_12.dll / cublasLt64_12.dll 放到本程序同目录\n"
            f"    3. 改用 CPU 识别：.env 中设 WHISPER_DEVICE=cpu、"
            f"WHISPER_COMPUTE_TYPE=int8（较慢，但一定能跑）"
        )


def transcribe(
    audio_path: Path,
    model_size: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cpu_threads: int = 4,
    num_workers: int = 2,
) -> list[dict]:
    """
    Transcribe Japanese audio to text with timestamps.

    Args:
        audio_path: Path to WAV audio file (16kHz mono recommended).
        model_size: Whisper model size (tiny, base, small, medium).
                    Defaults to config WHISPER_MODEL.
        progress_callback: Optional callback(progress: float 0-1, status: str).
        cpu_threads: CTranslate2 CPU thread count for this model.
        num_workers: Data-loading worker count for this model.

    Returns:
        List of segment dicts with keys: start, end, text.
    """
    if model_size is None:
        model_size = WHISPER_MODEL
    model_size = get_model_size(model_size)

    _check_cuda_runtime()

    if progress_callback:
        progress_callback(0.0, f"加载 Whisper {model_size} 模型...")

    # Configure model for GPU/CPU inference
    # DEVICE=("cuda"/"cpu") and COMPUTE_TYPE=("float16"/"int8") come from config (.env).
    model_path = _resolve_model_path(model_size)
    try:
        model = WhisperModel(
            model_path,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root=str(MODEL_DIR),
            cpu_threads=cpu_threads,  # Only used when DEVICE="cpu"
            num_workers=num_workers,  # Use some parallelism for data loading
        )
    except Exception as e:
        if model_path != model_size:
            raise
        # Nothing cached locally AND the Hub is unreachable — say what to do.
        available = _local_model_sizes()
        hint = (
            f"本地已缓存: {', '.join(available)}" if available
            else f"本地缓存为空（{MODEL_DIR}）"
        )
        # 一个模型都没有，最常见的原因不是「没下载」，而是 MODEL_DIR 指错了
        # （项目移动/改名后 .env 里写死的绝对路径失效）。
        misplaced = [str(c) for c in _candidate_model_dirs() if _has_any_model(c)]
        fix = ""
        if not available and misplaced:
            fix = (
                "\n  ⚠ 这些位置其实有模型，很可能是 .env 里的 MODEL_DIR 指错了：\n"
                + "\n".join(f"       {h}" for h in misplaced)
                + f"\n     当前 MODEL_DIR = {MODEL_DIR}"
            )
        raise RuntimeError(
            f"模型 '{model_size}' 本地不存在，且无法从 HuggingFace 下载"
            f"（{type(e).__name__}: {e}）。\n"
            f"  {hint}{fix}\n"
            f"  解决办法：修正 .env 中的 MODEL_DIR 指向真正存放模型的目录，"
            f"或把 WHISPER_MODEL / WHISPER_REFERENCE_MODELS "
            f"改为上面已缓存的模型名，或手动下载该模型到 {MODEL_DIR}"
        ) from e

    if progress_callback:
        progress_callback(0.05, "开始语音识别 (启用 VAD 语音检测)...")

    # Japanese-optimized transcription settings:
    # - VAD is CRITICAL for Japanese: prevents infinite loops and bad timings
    # - Lower threshold (0.4) to avoid cutting off soft Japanese speech
    # - Shorter min_silence (500ms) for Japanese sentence boundaries
    # - speech_pad_ms (800ms) prevents clipping at start of Japanese words
    # - Disable Whisper's internal thresholds — Silero VAD is the sole arbiter
    #   (compression_ratio/log_prob/no_speech can reject valid Japanese segments)
    segments, info = model.transcribe(
        str(audio_path),
        language=SOURCE_LANG,       # Japanese — explicit to skip auto-detection
        task="transcribe",          # Transcribe, not translate
        beam_size=3,                # 3 = good balance; 5 adds 30-50% CPU overhead
        # 0.0 优先（确定性、最准），但必须保留「升温回退链」：某段被
        # compression_ratio 判为复读时，Whisper 会升温重试。
        # 只给单个温度 = 无法回退 = 复读结果被原样保留。
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        vad_filter=True,            # Silero VAD — essential for Japanese
        vad_parameters=dict(
            threshold=0.4,              # Lower than default 0.5 for softer Japanese speech
            min_speech_duration_ms=250, # Capture short Japanese particles (は、が、を)
            min_silence_duration_ms=500,# Shorter than default 2000ms for JP sentence boundaries
            speech_pad_ms=800,          # CRITICAL: prevent clipping first syllables
            max_speech_duration_s=15,   # Shorter chunks help Japanese pacing
        ),
        # 保留复读守卫（Whisper 默认 2.4）。medium 在难段上会陷入
        # "テテテテ..." 死循环，关掉它就会把整段 39 秒的复读当正常字幕输出。
        # 实测：开启前 medium 输出 1 条 39s 的复读；开启后恢复正常分段。
        compression_ratio_threshold=2.4,
        # 下面两项对轻声日语过于苛刻，维持关闭 —— 静音判断已交给 Silero VAD，
        # 打开会误杀音量偏低的正常日语。
        log_prob_threshold=None,
        no_speech_threshold=None,
        # 词级时间戳：供说话人识别做「一句话横跨两个说话人」的切分。
        # 注意：large-v3-turbo 只有 4 层 decoder，DTW 推算的词级时间戳
        # 比完整版 large-v3 粗；diarizer 会先校验词序列能否还原原文，
        # 对不齐就自动跳过切分，不会污染字幕。
        # 可用 WHISPER_WORD_TIMESTAMPS=0 关闭（省一点耗时/显存）。
        word_timestamps=_word_timestamps_enabled(),
    )

    # Collect all segments
    results = []
    start_time = time.time()

    # Duration for progress estimation
    duration = info.duration

    for idx, segment in enumerate(segments):
        item = {
            "start": round(segment.start, 2),
            "end": round(segment.end, 2),
            "text": segment.text.strip(),
        }
        # 保留词级时间戳（仅在开启时存在），供 diarizer 做跨说话人切分
        if getattr(segment, "words", None):
            item["words"] = [
                {
                    "word": w.word,
                    "start": round(w.start, 2),
                    "end": round(w.end, 2),
                }
                for w in segment.words
            ]
        results.append(item)

        # Progress updates (throttled — every few segments)
        if progress_callback and idx % 10 == 0:
            elapsed = time.time() - start_time
            progress = min(segment.end / duration, 0.95) if duration > 0 else 0
            progress_callback(progress, f"语音识别中... {segment.end:.0f}s / {duration:.0f}s")

    if progress_callback:
        progress_callback(1.0, f"语音识别完成，共 {len(results)} 条字幕")

    return results


def transcribe_multi(
    audio_path: Path,
    model_sizes: list[str],
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """
    Transcribe the same audio with multiple Whisper models in parallel.

    Each model runs on its own thread with a dedicated WhisperModel instance
    (faster-whisper is not thread-safe within a single instance, but separate
    instances are fine). CPU threads are scaled down per model to avoid
    oversubscribing the machine.

    Args:
        audio_path: Path to WAV audio file.
        model_sizes: List of model size strings to run (e.g. ["large-v3-turbo", "medium"]).
        progress_callback: Optional callback(progress: float 0-1, status: str).

    Returns:
        (results, errors):
            results: {model_size: [segment dicts]} for every model that succeeded.
            errors:  {model_size: error message} for every model that failed.
    """
    model_sizes = list(dict.fromkeys(model_sizes))  # dedupe, preserve order
    n = max(1, len(model_sizes))
    cpu_threads = max(1, 4 // n)   # scale down so total threads stay ~constant
    num_workers = 1 if n > 1 else 2

    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    progress_state: dict[str, float] = {}
    lock = threading.RLock()  # reentrant: _report may be called while lock held

    def _report():
        if not progress_callback:
            return
        with lock:
            ok = len(results)
            failed = len(errors)
            done = ok + failed
            running = sum(
                progress_state.get(m, 0.0)
                for m in model_sizes
                if m not in results and m not in errors
            )
            overall = (done + running) / n
        # Report failures explicitly — a failed model used to be counted as
        # "完成", which hid the real problem behind a stuck-looking progress bar.
        detail = f"{ok}/{n} 成功"
        if failed:
            detail += f"，{failed} 个失败"
        progress_callback(min(overall, 1.0), f"语音识别中 ({detail})...")

    def worker(model_size: str):
        def on_progress(p: float, _msg: str):
            with lock:
                progress_state[model_size] = p
            _report()
        try:
            segs = transcribe(
                audio_path,
                model_size=model_size,
                cpu_threads=cpu_threads,
                num_workers=num_workers,
                progress_callback=on_progress,
            )
            with lock:
                results[model_size] = segs
        except Exception as e:
            with lock:
                errors[model_size] = str(e)
        _report()

    if DEVICE == "cuda":
        # GPU inference MUST be serialized. Two CTranslate2 models running
        # concurrently on one CUDA device make both threads lazily
        # ``LoadLibrary`` the same CUDA runtime DLLs; the loser blocks forever
        # inside the Windows loader. Symptom: one model reports back, the
        # process then sits at ~0% CPU / ~0% GPU holding VRAM indefinitely.
        # (Replicated 2026-09-16: hung >2h on a 39s clip.)
        # One GPU is the bottleneck anyway, so sequential costs little.
        for model_size in model_sizes:
            worker(model_size)
    else:
        # CPU inference is safe to parallelize — separate instances, no
        # shared CUDA runtime.
        threads = [threading.Thread(target=worker, args=(m,)) for m in model_sizes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    if progress_callback:
        msg = f"语音识别完成：{len(results)}/{n} 个模型成功"
        if errors:
            msg += "；失败 " + "；".join(
                f"{m}: {e.splitlines()[0][:120]}" for m, e in errors.items()
            )
        progress_callback(1.0, msg)

    return results, errors
