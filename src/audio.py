"""
Audio extraction using FFmpeg.
Extracts audio from video files as 16kHz mono WAV for Whisper.
"""

import subprocess
import shutil
import sys
import os
import threading
from pathlib import Path
from typing import Optional, Callable

from .config import AUDIO_SAMPLE_RATE, OUTPUT_DIR


# Common Windows install directories for FFmpeg
_WIN_FFMPEG_DIRS = [
    # winget (Gyan.FFmpeg) — often here
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
    # Manual install
    Path("C:/ffmpeg/bin"),
    Path("C:/Program Files/ffmpeg/bin"),
    Path("C:/Program Files (x86)/ffmpeg/bin"),
    # Chocolatey
    Path("C:/ProgramData/chocolatey/bin"),
    # Scoop
    Path(os.environ.get("USERPROFILE", "")) / "scoop" / "shims",
]


def _find_in_windows_registry() -> list[str]:
    """Try to find FFmpeg paths from common install locations on disk."""
    found = []
    for base in _WIN_FFMPEG_DIRS:
        if not base.exists():
            continue
        # Check direct ffmpeg.exe
        ffmpeg_exe = base / "ffmpeg.exe"
        if ffmpeg_exe.exists():
            found.append(str(ffmpeg_exe))
        # Check for Gyan.FFmpeg winget package structure
        for subdir in base.iterdir():
            if subdir.is_dir() and "ffmpeg" in subdir.name.lower():
                # winget extracts to e.g., Gyan.FFmpeg_xxx/ffmpeg-xxx/bin/
                for root, dirs, files in os.walk(str(subdir)):
                    if "ffmpeg.exe" in files:
                        found.append(str(Path(root) / "ffmpeg.exe"))
    return found


def find_ffmpeg() -> str:
    """Find the ffmpeg executable (cross-platform)."""
    # 1. Check PATH via shutil.which
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    # 2. On Windows, search common install directories
    if sys.platform == "win32":
        # Also refresh PATH from registry
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                user_path = winreg.QueryValueEx(key, "PATH")[0]
                for p in user_path.split(";"):
                    ffmpeg_candidate = Path(p.strip()) / "ffmpeg.exe"
                    if ffmpeg_candidate.exists():
                        return str(ffmpeg_candidate)
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
                sys_path = winreg.QueryValueEx(key, "PATH")[0]
                for p in sys_path.split(";"):
                    ffmpeg_candidate = Path(p.strip()) / "ffmpeg.exe"
                    if ffmpeg_candidate.exists():
                        return str(ffmpeg_candidate)
        except Exception:
            pass

        # Search known directories
        for ffmpeg_path in _find_in_windows_registry():
            return ffmpeg_path

    raise RuntimeError(
        "FFmpeg not found. Please install FFmpeg:\n"
        "  - Windows: winget install Gyan.FFmpeg\n"
        "  或手动下载: https://www.gyan.dev/ffmpeg/builds/ → 下载 essentials 版 → 解压到 C:\\ffmpeg\n"
        "  - macOS:   brew install ffmpeg\n"
        "  - Linux:   sudo apt install ffmpeg"
    )


def find_ffprobe() -> str:
    """Find the ffprobe executable (cross-platform)."""
    # Try PATH first (handles .exe on Windows automatically)
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        return ffprobe_path

    # Fallback: derive from ffmpeg path
    ffmpeg_path = find_ffmpeg()
    parent = Path(ffmpeg_path).parent

    # On Windows, need .exe extension
    if sys.platform == "win32":
        candidates = [parent / "ffprobe.exe"]
    else:
        candidates = [parent / "ffprobe"]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise RuntimeError("ffprobe not found. Please install FFmpeg properly.")


def _condense_ffmpeg_stderr(lines) -> str:
    """把 ffmpeg 的 stderr 压成「用户能看懂的那几行」。

    ffmpeg 失败时会先打一大段版本横幅（ffmpeg version / configuration /
    libav* 版本号），真正的原因在**最后几行**。
    实测一个非视频文件会产出 14 行、约 2500 字符，前 10 行全是横幅 ——
    直接抛出去，用户在界面上看到的是一堵噪音墙，看不到
    「Invalid data found when processing input」这句关键信息。

    这里剔掉横幅，只留最后若干行有信息量的内容。
    """
    noise = ("ffmpeg version", "built with", "configuration:",
             "libav", "libsw", "libpostproc")
    kept = [l.rstrip() for l in lines
            if not any(l.strip().startswith(n) for n in noise)]
    kept = [l for l in kept if l.strip()]
    if not kept:  # 兜底：万一全是横幅，也别抛空
        kept = [l.rstrip() for l in lines if l.strip()][-8:]
    return "\n".join(kept[-8:])


def get_audio_duration(audio_path: Path) -> float:
    """
    Get the duration of an audio file in seconds using ffprobe.

    Args:
        audio_path: Path to audio file.

    Returns:
        Duration in seconds, or 0.0 if it cannot be determined.
    """
    ffprobe = find_ffprobe()

    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, Exception):
        return 0.0


def extract_audio(
    video_path: Path,
    output_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Path:
    """
    Extract audio from a video file as 16kHz mono WAV.

    Args:
        video_path: Path to the video file.
        output_dir: Directory for output audio. Defaults to OUTPUT_DIR/audio.
        progress_callback: Optional callback(progress: float 0-1, status: str).

    Returns:
        Path to the extracted WAV audio file.
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR / "audio"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_path = output_dir / f"{video_path.stem}.wav"

    # Get video duration for progress tracking
    try:
        duration = get_audio_duration(video_path)
    except Exception:
        duration = 0

    if progress_callback:
        progress_callback(0.0, "正在提取音频...")

    ffmpeg = find_ffmpeg()

    cmd = [
        ffmpeg,
        "-i", str(video_path),
        "-vn",                      # No video
        "-acodec", "pcm_s16le",     # PCM 16-bit
        "-ar", str(AUDIO_SAMPLE_RATE),  # 16kHz sample rate
        "-ac", "1",                 # Mono channel
        "-y",                       # Overwrite output
        "-progress", "pipe:1",      # Progress to stdout
        "-nostats",                 # No stats output
        str(audio_path),
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # Read stderr in a thread to prevent pipe buffer deadlock
    stderr_lines = []
    def read_stderr():
        for line in process.stderr:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    # Parse ffmpeg progress from stdout
    for line in process.stdout:
        if "out_time_us=" in line and duration > 0 and progress_callback:
            try:
                time_us = int(line.strip().split("=")[1])
                time_s = time_us / 1_000_000
                progress = min(time_s / duration, 1.0) if duration > 0 else 0
                progress_callback(progress, f"提取音频中... {time_s:.0f}s / {duration:.0f}s")
            except (ValueError, IndexError):
                pass

    process.wait()
    stderr_thread.join(timeout=5)

    if process.returncode != 0:
        raise RuntimeError(
            "FFmpeg 提取音频失败（多半是文件不是有效的视频/音频，或编码不受支持）：\n"
            f"{_condense_ffmpeg_stderr(stderr_lines)}"
        )

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio extraction failed, file not created: {audio_path}")

    if progress_callback:
        progress_callback(1.0, "音频提取完成")

    return audio_path
